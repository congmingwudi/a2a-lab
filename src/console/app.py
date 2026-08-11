"""Lab console: a web viewer for the wire traces, and the cockpit for
launching experiments.

    uv run python -m console --port 8200

- GET  /              single-page UI (plain HTML/JS, no build step)
- GET  /api/traces    traces grouped by trace_id, newest first
- GET  /api/stream    SSE live tail of new TraceEvents (file-watcher)
- GET  /api/targets   runnable targets from config/targets.yaml
- GET  /api/scenarios primary demo scenarios + nav groups from config/scenarios.yaml
- GET  /api/insights  trusted-advisor findings from config/insights.yaml
- GET  /api/whats-next roadmap-horizon plans from config/whats_next.yaml
- GET  /api/config    active deployment mode (A2ALAB_MODE) + target remaps
- POST /api/run       run a scenario or one cell (custom prompt, live trace)
- POST /api/warmup/{name} pre-warm a hosted runtime; every duration recorded
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

import httpx
import yaml

from console import reviews
from console.insights import by_category, load_insights, to_markdown
from console.whats_next import load_plans
from interop import af_channel, delegation
from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry
from interop.trace import DEFAULT_TRACE_DIR, TRACE_DIR_ENV

STATIC_DIR = Path(__file__).parent / "static"

# Every hop between a browser and this app has an idle timeout, and an SSE
# stream that emits only when trace hops land looks idle for as long as the lab
# is quiet. The ALB the console moves behind (WS13) uses 120s; proxies and CDNs
# commonly default to 30s. 15s is inside all of them.
#
# The failure this prevents is silent, which is why it is a constant and not a
# preference: the browser's EventSource reconnects after a drop, but the new
# generator rebuilds its per-file offsets from current EOF — so hops that
# landed during the gap are never sent, and the live tail under-reports with no
# error anywhere.
SSE_KEEPALIVE_S = 15.0
SCENARIOS_PATH = Path("config/scenarios.yaml")
AGENTS_PATH = Path("config/agents.yaml")
DECISIONS_PATH = Path("plan/00-decisions.md")

_DECISION_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (D\d+)( \(revised\))?: (.+)$")


def load_decisions(path: str | Path = DECISIONS_PATH) -> dict[str, dict]:
    """Parse the ADR log into {"D28": {"id", "title", "date", "markdown"}}.
    Revised decisions (D9, D12) keep every entry in one markdown body,
    separated by a rule, with the latest entry's title/date on the chip."""
    p = Path(path)
    if not p.exists():
        return {}
    decisions: dict[str, dict] = {}
    current: dict | None = None
    body: list[str] = []

    def flush():
        if current is None:
            return
        section = f"### {current['date']} — {current['heading']}\n" + "\n".join(body).strip()
        entry = decisions.setdefault(
            current["id"], {"id": current["id"], "title": "", "date": "", "markdown": ""}
        )
        entry["title"], entry["date"] = current["title"], current["date"]
        sep = "\n\n---\n\n" if entry["markdown"] else ""
        entry["markdown"] = entry["markdown"] + sep + section

    for line in p.read_text(encoding="utf-8").splitlines():
        match = _DECISION_HEADING.match(line)
        if match:
            flush()
            date, did, revised, title = match.groups()
            current = {
                "id": did,
                "date": date,
                "title": title,
                "heading": f"{did}{revised or ''}: {title}",
            }
            body = []
        elif line.startswith("## "):
            flush()
            current = None  # non-decision section (M10 etc.)
        elif current is not None:
            body.append(line)
    flush()
    return decisions


def load_scenarios(path: str | Path = SCENARIOS_PATH) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("scenarios") or {}


def load_groups(path: str | Path = SCENARIOS_PATH) -> list[dict]:
    """Second-level nav groups ({id, title, upcoming?}, yaml order) — one per
    platform pair; `upcoming` groups are roadmap placeholders (WS2-WS5)."""
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("groups") or []


def load_agents(path: str | Path = AGENTS_PATH) -> dict[str, list[dict]]:
    """The Agent Registry source of truth (config/agents.yaml): one entry per
    distinct DEPLOYED agent, as opposed to targets.yaml's protocol faces. Returns
    {groups: [...], agents: [...]} in file order. See the Infrastructure → Agent
    Registry section (D57 canvas) and /api/agents."""
    p = Path(path)
    if not p.exists():
        return {"groups": [], "agents": []}
    raw = yaml.safe_load(p.read_text()) or {}
    return {"groups": raw.get("groups") or [], "agents": raw.get("agents") or []}


# Customer-shaped default for demos: the Agentforce agent's "Customer account
# status" topic answers this from real CRM records via the A2ALab: Get Account
# Summary action (accounts: Omega, Inc. / Acme Corp / Northwind Traders), and
# the Claude→Agentforce scenario's prompt_suffix makes Claude consult
# Agentforce for it. scripts/matrix.py keeps its own protocol-comparison
# utterance — that sweep needs a question every platform can answer unaided.
DEFAULT_QUESTION = (
    "Tell me what you know about account Omega, Inc. — a short summary of their current state."
)

# The single-hop protocol cells run without the two-sections prompt suffix,
# so their default question must be one each research agent answers alone
# (from its own knowledge — no Agentforce consult): the same
# protocol-comparison utterance the matrix sweep uses. Agentforce cells
# keep the CRM question — account status IS what those agents do alone.
CELL_RESEARCH_QUESTION = (
    "In two sentences: what is the difference between the MCP and A2A "
    "protocols for agent interoperability?"
)


# ---- Protocol-call cells: blurb + planned flow per target ------------------
# The Details tab shows what a single cell WILL execute: the entry hop plus
# the platform-interior legs behind it — untraced ones included honestly
# (they render as ghosts in the post-run call path too).

_TWIN_BY_TARGET = {
    "agentforce-rest": "Claude-paired",
    "agentforce-openai-rest": "OpenAI-paired",
    "agentforce-google-adk-rest": "Google ADK-paired",
    "agentforce-foundry-rest": "Foundry-paired",
}


def _lab_server_entry(t, agent_label: str) -> dict:
    transport = {
        "rest": (
            "POST /invoke on the lab's REST server — AgentRequest JSON in, "
            "AgentResponse out, trace id in the X-Trace-Id header."
        ),
        "mcp": (
            'tools/call "ask" on the lab\'s MCP server (streamable-http) — '
            "session_id and trace_id ride as tool arguments because MCP has "
            "no session semantics of its own."
        ),
        "a2a": (
            "A2A message/send on the lab's A2A server — the agent publishes "
            "its own AgentCard at /.well-known/agent-card.json; contextId "
            "carries the session, trace id rides message metadata."
        ),
    }[t.protocol]
    return {
        "source": "remote-caller",
        "target": t.name,
        "protocol": t.protocol,
        "detail": f"{transport} Behind it: {agent_label}.",
    }


def _claude_interior() -> dict:
    if os.environ.get("CLAUDE_BACKEND", "managed") == "managed":
        return {
            "source": "claude-researcher",
            "target": "anthropic-managed-agents",
            "protocol": "managed-agents-api",
            "detail": (
                "The adapter answers on Anthropic Managed Agents (the Claude "
                "API's hosted-agents beta, model claude-haiku-4-5): session "
                "create + turn — recorded as a real hop."
            ),
        }
    return {
        "source": "claude-researcher",
        "target": "claude-agent-sdk",
        "protocol": "internal",
        "detail": (
            "Self-hosted claude-agent-sdk turn in the lab process (model "
            "claude-haiku-4-5) — the calls to the Claude API are "
            "platform-interior."
        ),
    }


def cell_details(t) -> dict:
    """blurb (what this call actually is), flow (planned hops, untraced
    interior included), question (a default the agent answers alone)."""
    name, platform, proto = t.name, t.platform, t.protocol
    question = DEFAULT_QUESTION if platform == "agentforce" else CELL_RESEARCH_QUESTION

    if platform == "claude" and proto in ("rest", "mcp", "a2a"):
        via = {
            "rest": "over REST",
            "mcp": (
                "over MCP — Managed Agents has no MCP inbound surface of its "
                "own, so the lab serves the protocol in front of the agent "
                "it hosts (one adapter, three protocol servers)"
            ),
            "a2a": (
                "over the A2A protocol — Managed Agents has no A2A inbound "
                "surface of its own, so the lab serves the protocol (with a "
                "live AgentCard) in front of the agent it hosts"
            ),
        }[proto]
        return {
            "blurb": (
                f"The client calls the lab's Claude research agent {via}. "
                "Inside, the adapter answers on Anthropic Managed Agents — "
                "the Claude API's hosted-agents platform."
            ),
            "flow": [_lab_server_entry(t, "the Claude research agent"), _claude_interior()],
            "question": question,
        }
    if platform == "openai" and proto in ("rest", "mcp", "a2a"):
        return {
            "blurb": (
                f"The client calls the lab's OpenAI research agent "
                f"{'over ' + proto.upper() if proto != 'rest' else 'over REST'} "
                "(OpenAI Agents SDK, Responses API underneath). OpenAI hosts "
                "no inbound agent endpoint at all — the lab's servers are "
                "the only door to this agent."
            ),
            "flow": [
                _lab_server_entry(t, "the OpenAI research agent"),
                {
                    "source": "openai-researcher",
                    "target": "openai-platform",
                    "protocol": "internal",
                    "detail": (
                        "OpenAI Agents SDK turn against the Responses API (model "
                        "gpt-5-mini) — platform-interior, and OpenAI's trace "
                        "dashboard is write-only (no read API), so this leg "
                        "is dark."
                    ),
                },
            ],
            "question": question,
        }
    if proto == "agentcore-http":
        agent = "Claude (claude-agent-sdk)" if platform == "claude" else "OpenAI (Agents SDK)"
        inner = (
            {
                "source": "claude-researcher",
                "target": "claude-agent-sdk",
                "protocol": "internal",
                "detail": (
                    "claude-agent-sdk turn inside the container (the sdk "
                    "backend, model claude-haiku-4-5 — Managed Agents is "
                    "the laptop default; the container ships the "
                    "self-hosted fallback)."
                ),
            }
            if platform == "claude"
            else {
                "source": "openai-researcher",
                "target": "openai-platform",
                "protocol": "internal",
                "detail": "OpenAI Agents SDK turn (model gpt-5-mini) against the Responses API inside the container.",
            }
        )
        return {
            "blurb": (
                f"The client invokes the {agent} research agent self-hosted "
                "on Bedrock AgentCore Runtime. There is no public URL — the "
                "call is an IAM-signed invoke_agent_runtime that lands on "
                "the container's POST /invocations. The container writes its "
                "interior hops to the Aurora trace store; the console merges "
                "them into the call path."
            ),
            "flow": [
                {
                    "source": "remote-caller",
                    "target": name,
                    "protocol": "agentcore-http",
                    "detail": (
                        "boto3 invoke_agent_runtime (SigV4) — cloud IAM is "
                        "the only door; the JSON payload lands on the "
                        "container's POST /invocations."
                    ),
                },
                inner,
            ],
            "question": question,
        }
    if platform == "agentforce" and proto == "agentforce-api":
        twin = _TWIN_BY_TARGET.get(name, "lab")
        return {
            "blurb": (
                f"The client talks to the Agentforce service agent (the "
                f"{twin} twin) over Salesforce's GA Agent API: OAuth "
                "client-credentials, session create, then the message turn."
            ),
            "flow": [
                {
                    "source": "agentforce-client",
                    "target": "agentforce",
                    "protocol": "agentforce-api",
                    "detail": (
                        f"OAuth + session + message against the GA Agent API "
                        f"— the {twin} twin (closed two-platform pairing). How "
                        "the twin fulfills the request inside Salesforce is its "
                        "own business — the experiment measures the wire."
                    ),
                },
            ],
            "question": question,
        }
    if platform == "agentforce" and proto in ("mcp", "a2a"):
        return {
            "blurb": (
                f"The client calls Agentforce over {proto.upper()} — which "
                "Salesforce does not offer: the platform has no GA "
                f"{proto.upper()} inbound surface, so the lab's shim speaks "
                "the protocol and proxies each call to the Agent API. "
                "Honest status: via-shim, never native."
            ),
            "flow": [
                {
                    "source": "remote-caller",
                    "target": name,
                    "protocol": proto,
                    "detail": (
                        f"The lab's {proto.upper()} shim serves the protocol "
                        "surface Salesforce lacks."
                    ),
                },
                {
                    "source": "agentforce-client",
                    "target": "agentforce",
                    "protocol": "agentforce-api",
                    "detail": (
                        "The shim proxies to the GA Agent API — OAuth + "
                        "session + message, recorded as real hops."
                    ),
                },
            ],
            "question": question,
        }
    if platform == "foundry" and proto == "a2a":
        return {
            "blurb": (
                "The client calls the Foundry research agent through Foundry "
                "Agent Service's own incoming A2A endpoint — the lab's second "
                "platform-native A2A cell. Auth is Microsoft Entra only (no "
                "key option), the binding is JSONRPC, and the platform "
                "serves version-specific agent cards (v1.0 and v0.3) — the "
                "same version spectrum the lab bridges in its own servers."
            ),
            "flow": [
                {
                    "source": "remote-caller",
                    "target": name,
                    "protocol": "a2a",
                    "detail": (
                        "message/send against the agent's A2A endpoint — "
                        "Entra bearer (azure-ad ADC), v1.0 card fetched from "
                        "the version-specific path agentCard/v1.0."
                    ),
                },
                {
                    "source": "foundry-researcher",
                    "target": "gpt-5-mini",
                    "protocol": "internal",
                    "detail": (
                        "The prompt agent runs inside Foundry Agent Service "
                        "— platform-interior; the response id is the "
                        "retrievable join key."
                    ),
                },
            ],
            "question": question,
        }
    if proto == "foundry-api":
        return {
            "blurb": (
                "The client calls the Foundry research agent through the "
                "platform's own Responses surface (agent_reference) — the "
                "native front door, sibling of the Agent API cells. Entra "
                "ADC auth; the response id rides as platform_ref."
            ),
            "flow": [
                {
                    "source": "foundry-client",
                    "target": name,
                    "protocol": "foundry-api",
                    "detail": (
                        "responses.create with an agent_reference on the "
                        "project endpoint — Entra bearer, "
                        "previous_response_id chains the conversation."
                    ),
                },
                {
                    "source": "foundry-researcher",
                    "target": "gpt-5-mini",
                    "protocol": "internal",
                    "detail": (
                        "The prompt agent runs inside Foundry Agent Service "
                        "— platform-interior; tool calls (the Agentforce "
                        "A2A consult) happen platform-side."
                    ),
                },
            ],
            "question": question,
        }
    if platform == "adk":
        return {
            "blurb": (
                "The client calls the Google ADK research agent through "
                "Vertex AI Agent Engine's own A2A endpoint — the platform "
                "itself speaks the protocol; no lab server or shim in the "
                "path. Auth is Google IAM (ADC bearer), transport pinned to "
                "HTTP+JSON because the preview card route 404s."
            ),
            "flow": [
                {
                    "source": "remote-caller",
                    "target": name,
                    "protocol": "a2a",
                    "detail": (
                        "message:send against the Agent Engine A2A endpoint "
                        "— IAM bearer (google-adc), a2a-version 1.0, "
                        "minimal AgentCard built locally (preview gap)."
                    ),
                },
                {
                    "source": "adk-researcher",
                    "target": "gemini-2.5-flash-lite",
                    "protocol": "internal",
                    "detail": (
                        "ADK Runner + Gemini (model gemini-2.5-flash-lite) inside "
                        "the Agent Engine container — request-level Cloud "
                        "Logging/Monitoring only (Observability section); no "
                        "session/turn API."
                    ),
                },
            ],
            "question": question,
        }
    if platform == "guide":
        extra = (
            " This MCP server also publishes the guide's raw read tools "
            "(get_decision, get_trace, list_briefs, …) so the CALLING model "
            "can reason over lab data itself — two integration shapes on one "
            "endpoint."
            if proto == "mcp"
            else ""
        )
        return {
            "blurb": (
                "The meta exhibit: the Lab Guide — the console's own docent — "
                f"served as just another lab agent over {proto.upper()} "
                "through the same inbound seam every hosted agent uses. Ask "
                "it how the lab works, from any protocol client (Claude "
                f"Desktop included).{extra}"
            ),
            "flow": [
                _lab_server_entry(t, "the Lab Guide docent"),
                {
                    "source": "lab-guide",
                    "target": "anthropic-api",
                    "protocol": "internal",
                    "detail": (
                        "Direct Anthropic tool-use loop (Haiku-tier) grounded "
                        "in the lab's own docs, with read tools over the ADR "
                        "log, results, analyst briefs, and wire traces."
                    ),
                },
            ],
            "question": "In two sentences: what is this lab and what does it prove?",
        }
    return {
        "blurb": f"Single protocol call to {name} ({platform} over {proto}).",
        "flow": [{"source": "remote-caller", "target": name, "protocol": proto, "detail": ""}],
        "question": question,
    }


async def run_via_bridge(req: AgentRequest, target: str) -> dict:
    """Route a run through the bridge (Path A shape) so the trace shows the
    full loop: caller -> bridge -> target agent [-> Agentforce] -> back."""
    bridge_url = os.environ.get("A2ALAB_BRIDGE_URL", "http://localhost:8100")
    headers = {"x-trace-id": req.trace_id}
    if os.environ.get("BRIDGE_TOKEN"):
        headers["x-bridge-token"] = os.environ["BRIDGE_TOKEN"]
    async with httpx.AsyncClient(timeout=120.0) as http:
        r = await http.post(
            f"{bridge_url}/invoke/{target}",
            json={"message": req.message, "session_id": req.session_id},
            headers=headers,
        )
        r.raise_for_status()
        data = r.json()
    return {
        "ok": True,
        "trace_id": req.trace_id,
        "text": data.get("text", ""),
        "latency_ms": (data.get("bridge") or {}).get("total_ms"),
        "session_id": data.get("session_id"),
        "via_bridge": True,
    }


# ---- Component links: the real agent assets behind each experiment --------
# Deep links into the systems where each agent actually lives, shown in the
# console's Details tab. Computed server-side from env so org domains and
# agent ids never live in checked-in config.


def _lightning_domain() -> str | None:
    dom = os.environ.get("SF_MY_DOMAIN", "").replace("https://", "").rstrip("/")
    if not dom:
        return None
    return "https://" + dom.replace(".my.salesforce.com", ".lightning.force.com")


def _my_salesforce_domain() -> str | None:
    """The `*.my.salesforce.com` instance domain — the OAuth/token host, NOT
    the Lightning domain. `/services/oauth2/token` and `/singleaccess` live
    here (Single Access rejects login.salesforce.com / test.salesforce.com)."""
    dom = os.environ.get("SF_MY_DOMAIN", "").replace("https://", "").rstrip("/")
    if not dom:
        return None
    return "https://" + dom


# WS19/M10: the Tableau Next Embedding SDK is loaded from this pinned CDN build
# (Salesforce's own reference example pins the same 2.0.0). It is a bare ES
# module the browser imports directly — our console is static, no bundler — so
# the version lives here, not in a package.json. The dashboard's API name is
# fixed in the org (New_Dashboard); orgUrl must be the LIGHTNING domain.
TABLEAU_SDK_URL = (
    "https://cdn.jsdelivr.net/npm/@salesforce/analytics-embedding-sdk@2.0.0"
    "/dist/sdk-bundle.module.js"
)
TABLEAU_DASHBOARD_API_NAME = os.environ.get("TABLEAU_DASHBOARD_API_NAME", "New_Dashboard")
# The "open in Salesforce" deep link points at the in-org custom TAB
# (CustomTab A2A_Lab_Traffic) rather than the raw /tableau/dashboard view: the
# tab is an App Builder page that carries the licence/perm-set + asset-share
# context the dashboard needs to render, so it is the surface that actually
# works for a signed-in admin. Tab api-name from env so the org is never a
# literal here.
TABLEAU_APP_TAB = os.environ.get("TABLEAU_APP_TAB", "A2A_Lab_Traffic")


# The JWT `aud` for the token exchange is the login authorization server, NOT
# the My Domain host — the JWT-bearer assertion is validated against
# login.salesforce.com. Overridable for a sandbox (test.salesforce.com).
TAB_EMBED_JWT_AUD = os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com")
# Local fallback path for the signing key (hosted uses the env var below, shipped
# through Secrets Manager like the lab JWT key — never on the task definition).
TAB_EMBED_KEY_ENV = "A2ALAB_TAB_EMBED_JWT_KEY"
TAB_EMBED_KEY_FILE = Path(".a2alab/tab_embed_jwt_private.pem")


def _tab_embed_signing_key() -> str | None:
    """The RSA private key that signs the JWT-bearer assertion. Env wins (hosted,
    via Secrets Manager), else the gitignored local keypair — same precedence as
    interop.identity._private_key."""
    from_env = os.environ.get(TAB_EMBED_KEY_ENV)
    if from_env:
        return from_env.replace("\\n", "\n")
    if TAB_EMBED_KEY_FILE.exists():
        return TAB_EMBED_KEY_FILE.read_text()
    return None


def _tableau_embed_configured() -> bool:
    """True when the console can mint a frontdoor URL for the inline embed.

    The frontdoor exchange (/singleaccess) needs a token carrying the `web`
    scope, and Salesforce's client-credentials flow only ever issues `api` — so
    the embed uses the JWT-BEARER flow against the dedicated `a2a_lab_tab_embed`
    ECA, which runs in the `sub` user's context and so carries the ECA's `web`
    grant (proven 2026-08-09: token scope `web api`, /singleaccess → 200). That
    needs three things: the ECA consumer key (`iss`), the run-as username
    (`sub`), and the signing key whose public cert is on the ECA. Absent any of
    them the embed is not offered — the deep link and screenshot stand on their
    own (D37/F6 per-caller identity)."""
    have = bool(
        os.environ.get("SF_CLIENT_ID_TAB_EMBED")
        and os.environ.get("SF_TAB_EMBED_RUNAS_USER")
        and _tab_embed_signing_key()
    )
    return have and bool(_my_salesforce_domain())


async def _mint_frontdoor_url() -> str:
    """Mint a fresh, short-lived Salesforce frontdoor URL for the embed (WS19).

    Two hops, both server-side so no SF credential reaches the browser:
    a JWT-BEARER token (grant_type urn:ietf:params:oauth:grant-type:jwt-bearer),
    signed locally as the run-as user, exchanged at /services/oauth2/token →
    that token carries the ECA's `web` scope (the JWT flow runs in the `sub`
    user's context, unlike client-credentials which only ever gets `api`) →
    POST /services/oauth2/singleaccess → the `frontdoor_uri`. That URL logs the
    browser into the run-as user's session and is what the SDK takes as
    `authCredential`. Frontdoor URLs are short-lived and single-use, so this is
    called PER render — never cached.
    """
    import time

    import jwt as pyjwt

    domain = _my_salesforce_domain()
    if not domain:
        raise HTTPException(status_code=503, detail="SF_MY_DOMAIN not set")
    client_id = os.environ.get("SF_CLIENT_ID_TAB_EMBED")
    run_as = os.environ.get("SF_TAB_EMBED_RUNAS_USER")
    key = _tab_embed_signing_key()
    if not (client_id and run_as and key):
        raise HTTPException(status_code=503, detail="Tableau embed JWT credentials not set")
    now = int(time.time())
    assertion = pyjwt.encode(
        {"iss": client_id, "sub": run_as, "aud": TAB_EMBED_JWT_AUD, "exp": now + 180},
        key,
        algorithm="RS256",
    )
    async with httpx.AsyncClient(timeout=15.0) as http:
        tok = await http.post(
            f"{domain}/services/oauth2/token",
            data={
                "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                "assertion": assertion,
            },
        )
        if tok.status_code != 200:
            raise HTTPException(
                status_code=502, detail=f"SF token exchange failed: {tok.text[:200]}"
            )
        payload = tok.json()
        access = payload["access_token"]
        instance = payload.get("instance_url", domain).rstrip("/")
        fd = await http.post(
            f"{instance}/services/oauth2/singleaccess",
            headers={
                "Authorization": f"Bearer {access}",
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
            },
        )
        if fd.status_code != 200:
            raise HTTPException(
                status_code=502, detail=f"SF frontdoor exchange failed: {fd.text[:200]}"
            )
        uri = fd.json().get("frontdoor_uri")
        if not uri:
            raise HTTPException(
                status_code=502, detail="SF frontdoor response had no frontdoor_uri"
            )
        return uri


def _managed_agent_id() -> str | None:
    aid = os.environ.get("CLAUDE_MANAGED_AGENT_ID")
    if aid:
        return aid
    state = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "managed.json"
    try:
        return json.loads(state.read_text())["agent_id"]
    except Exception:
        return None


SHOTS_DIR = STATIC_DIR / "components"


def _shots(*slugs: str) -> dict:
    """Screenshot fields for a component: the UI shows every image whose
    file exists under static/components/, plus a drop-it-here hint for any
    missing ones. Public demo users see the screenshots instead of needing
    logins to each platform; the Open link stays for the operator."""
    return {
        "shots": [
            f"/static/components/{slug}.png"
            for slug in slugs
            if (SHOTS_DIR / f"{slug}.png").exists()
        ],
        "missing_shots": [slug for slug in slugs if not (SHOTS_DIR / f"{slug}.png").exists()],
    }


def _env_url(var: str, default: str) -> str:
    """A component's deep link: the env override, or the working default.

    `os.environ.get(var, default)` is wrong here, and was wrong in the running
    console for as long as `.env` carried `AGENTCORE_CONSOLE_URL=` and
    `AGENT_ENGINE_CONSOLE_URL=` with empty values: an empty string is a present
    key, so it BEAT the default and both rows rendered "not yet available" —
    the exact failure `test_every_component_has_a_console_url` exists to
    prevent. The test could not see it because tests do not load `.env` and
    `run_local.sh` does.

    An empty override means "I have nothing better", not "show no link".
    """
    return os.environ.get(var) or default


def public_components(comps: list[dict], signed_in: bool) -> list[dict]:
    """Component rows with the deep links removed for anonymous callers.

    `/api/scenarios` and `/api/targets` are on the PUBLIC surface — the landing
    exhibit renders from them with no credential. The component *titles* and
    notes are the exhibit ("this agent lives in Agentforce Studio"); the URLs
    are operator affordances that an anonymous visitor could not use anyway,
    since every one of them lands on a vendor login.

    They are also the one part of that payload that names accounts: the
    Salesforce org's my-domain, the GCP project id, an Azure tenant id. The
    repo stopped publishing those on 2026-07-27; an unauthenticated API that
    still hands them out would make that scrub cosmetic.
    """
    if signed_in:
        return comps
    return [{**c, "url": None, "url_requires_signin": bool(c.get("url"))} for c in comps]


def components_for(tags: set[str]) -> list[dict]:
    """Component rows for a scenario's tags (or a target's platform mapped to
    pseudo-tags). Each: {title, kind, note, url|None, shot|None, shot_slug}
    — url None renders as not-yet-available."""
    comps: list[dict] = []
    ld = _lightning_domain()
    # Keyed on managed-agents alone: the AgentCore-hosted Claude scenarios
    # carry `claude` too but run the sdk backend, not the managed platform.
    if "managed-agents" in tags:
        comps.append(
            {
                "title": "Claude research agent — Managed Agents (beta)",
                "kind": "claude",
                "note": "Agent + environment configuration (model, prompt, the "
                "ask_agentforce custom tool) in the Claude platform console.",
                "url": _env_url(
                    "CLAUDE_AGENT_CONSOLE_URL",
                    "https://platform.claude.com/workspaces/default/agents",
                ),
                **_shots(
                    "claude-managed-agents",
                    "claude-managed-agent-async"
                    if "daily-brief" in tags
                    else "claude-managed-agent-sync",
                ),
            }
        )
    if {"agentforce", "agent-api"} & tags:
        comps.append(
            {
                "title": "Agentforce agent — A2ALab Research Assistant (Claude-paired)",
                "kind": "agentforce",
                "note": "Open Agentforce Studio — topics, instructions, and the "
                "A2ALab: Get Account Summary action live here.",
                "url": f"{ld}/lightning/n/standard-AgentforceStudio?c__nav=agents" if ld else None,
                **_shots("agentforce-studio"),
            }
        )
    if "openai" in tags:
        comps.append(
            {
                "title": "OpenAI research agent — Agents SDK",
                "kind": "openai",
                "note": "M9: the openai-agents backend, answering locally and as the "
                "a2alab_openai AgentCore runtime. The agent is our container, so the "
                "platform-side asset is its Traces dashboard — the Agents SDK exports "
                "every run there by default.",
                # There is no OpenAI-hosted agent object to link to; the runs are
                # the asset. platform.openai.com/traces is the URL the Agents SDK
                # tracing docs publish (openai.github.io/openai-agents-python/tracing).
                "url": _env_url("OPENAI_CONSOLE_URL", "https://platform.openai.com/traces"),
                **_shots("openai-agentcore"),
            }
        )
    if {"foundry", "azure"} & tags:
        comps.append(
            {
                "title": "Microsoft Foundry agent — a2alab-foundry-researcher",
                "kind": "foundry",
                "note": "WS3: the prompt agent (gpt-5-mini) with the Agentforce A2A "
                "tool and inbound A2A enabled — instructions and connection pushed by "
                "deploy/foundry/provision_foundry.py.",
                # Microsoft documents the portal only as its root (ai.azure.com); the
                # per-project deep link carries ids the docs do not specify a format
                # for, so it is env-supplied — paste the URL the portal shows.
                "url": _env_url("FOUNDRY_CONSOLE_URL", "https://ai.azure.com"),
                **_shots("foundry-agent"),
            }
        )
    if "agent-engine" in tags:
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        comps.append(
            {
                "title": "Vertex AI Agent Engine — a2alab-adk-researcher",
                "kind": "adk",
                "note": "WS2: the ADK/Gemini agent deployed with native A2A serving "
                "(deploy/adk/deploy_adk.py); scale-to-zero on a personal GCP project.",
                "url": _env_url(
                    "AGENT_ENGINE_CONSOLE_URL",
                    "https://console.cloud.google.com/vertex-ai/agents/agent-engines"
                    + (f"?project={project}" if project else ""),
                ),
                **_shots("agent-engine"),
            }
        )
    if "agentcore" in tags:
        comps.append(
            {
                "title": "Bedrock AgentCore runtime — a2alab-claude / a2alab-openai",
                "kind": "aws",
                "note": "D26: the self-hosted Agent SDK containers deployed to Bedrock "
                "AgentCore Runtime (IAM-only data plane, no public HTTP endpoint) — "
                "deploy/agentcore/deploy.sh builds and pushes them.",
                "url": _env_url(
                    "AGENTCORE_CONSOLE_URL",
                    "https://us-east-1.console.aws.amazon.com/bedrock-agentcore/home"
                    "?region=us-east-1#/agent-runtimes",
                ),
                **_shots("agentcore-runtimes"),
            }
        )
    if "bridge" in tags:
        comps.append(
            {
                "title": "Bridge credential — A2ALab_Bridge",
                "kind": "bridge",
                "note": "Named/External Credential carrying X-Bridge-Token for the "
                "Apex callout; the bridge itself is src/bridge (:8100).",
                "url": f"{ld}/lightning/setup/NamedCredential/home" if ld else None,
                **_shots("bridge-credential"),
            }
        )
    if "daily-brief" in tags:
        comps.append(
            {
                "title": "Account Briefs — A2ALab_Account_Brief__c",
                "kind": "agentforce",
                "note": "Where the daily briefs land: long-text Brief__c on the "
                "Account, plus the activity and in-app alert. The Data 360 "
                "vector-search corpus for grounding the Agentforce agent (M10).",
                "url": f"{ld}/lightning/o/A2ALab_Account_Brief__c/list" if ld else None,
                **_shots("account-briefs"),
            }
        )
        brief_state = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "brief.json"
        deployment_note = "Provision with scripts/setup_brief_agent.py."
        if brief_state.exists():
            try:
                b = json.loads(brief_state.read_text())
                deployment_note = (
                    f"Deployment {b.get('deployment_id', '?')} — cron "
                    f"'{b.get('cron', '?')}' {b.get('timezone', '')} on "
                    f"{b.get('model', '?')} for: {b.get('accounts', '?')}."
                )
            except Exception:
                pass
        comps.append(
            {
                "title": "Scheduled deployment — A2ALab Daily Account Brief",
                "kind": "managed-agents",
                "note": deployment_note + " Sessions fired by the cron are serviced "
                "by `python -m briefs --watch` on the lab host.",
                "url": "https://platform.claude.com/workspaces/default/agents",
                **_shots("scheduled-deployment"),
            }
        )
    return comps


_PLATFORM_TAGS = {
    "claude": {"claude", "managed-agents"},
    "agentforce": {"agentforce"},
    "openai": {"openai"},
    "foundry": {"foundry", "azure"},
}


def _trace_dir() -> Path:
    # Resolved per call, not at import: main() loads .env after this module
    # is imported, and the recorders resolve the same env var lazily too —
    # reader and writers must agree on the directory.
    return Path(os.environ.get(TRACE_DIR_ENV, DEFAULT_TRACE_DIR))


def _parse_lines(data: bytes) -> list[dict]:
    """JSONL records split on \\n bytes only — str.splitlines() would also
    split on U+2028/U+2029 inside payloads and shred the record."""
    events: list[dict] = []
    for raw in data.split(b"\n"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            events.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return events


# Warm-up records live next to the traces (same isolated dir under tests):
# one JSON line per attempt, kept forever — the cold-start comparison data.
WARMUP_LOG = "warmups.jsonl"


def _read_warmups() -> list[dict]:
    path = _trace_dir() / WARMUP_LOG
    if not path.exists():
        return []
    return _parse_lines(path.read_bytes())


def _record_warmup(record: dict) -> None:
    trace_dir = _trace_dir()
    trace_dir.mkdir(parents=True, exist_ok=True)
    with (trace_dir / WARMUP_LOG).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _read_events() -> list[dict]:
    events: list[dict] = []
    trace_dir = _trace_dir()
    if not trace_dir.exists():
        return events
    for path in sorted(trace_dir.glob("*.jsonl")):
        events.extend(_parse_lines(path.read_bytes()))
    return events


# Hosted-runtime hops (AgentCore containers, the af-shim Lambda) write to the
# Aurora store, not local files (D23/D26/D28) — merge a recent window into
# the trace view so remote legs render as real recorded hops, not ghosts.
_REMOTE_WINDOW_S = 6 * 3600
_remote = {"ts": 0.0, "events": [], "client": None}


def _read_remote_events() -> list[dict]:
    """Soft-fail by design: no PG config, Aurora resuming, or missing creds
    just means local-only traces (and a retry on the next poll)."""
    now = time.time()
    if now - _remote["ts"] < 5:
        return _remote["events"]
    try:
        from observability.pg import SCHEMA, PgClient

        if not PgClient.configured():
            return []
        if _remote["client"] is None:
            _remote["client"] = PgClient.from_env()
        rows = _remote["client"].execute(
            f"""SELECT trace_id, hop_seq, ts, source, target, protocol,
                       transport_detail, status, latency_ms, platform_ref,
                       request_payload_raw::text AS request_payload_raw,
                       response_payload_raw::text AS response_payload_raw
                FROM {SCHEMA}.trace_events
                WHERE ts > :since ORDER BY ts LIMIT 2000""",
            {"since": now - _REMOTE_WINDOW_S},
        )
    except Exception:
        _remote["ts"] = now  # back off a poll interval, then retry
        return _remote["events"]
    for row in rows:
        for key in ("request_payload_raw", "response_payload_raw"):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except (json.JSONDecodeError, TypeError):
                    pass
    _remote.update(ts=now, events=rows)
    return rows


def _merged_events() -> list[dict]:
    """Local jsonl + remote Aurora hops, deduped: pg_backfill copies local
    hops into Aurora, so the same event can arrive from both stores."""
    events = _read_events()
    seen = {
        (
            e.get("trace_id"),
            e.get("hop_seq"),
            round(e.get("ts") or 0, 4),
            e.get("source"),
            e.get("target"),
        )
        for e in events
    }
    for ev in _read_remote_events():
        key = (
            ev.get("trace_id"),
            ev.get("hop_seq"),
            round(ev.get("ts") or 0, 4),
            ev.get("source"),
            ev.get("target"),
        )
        if key not in seen:
            events.append(ev)
    return events


def _read_remote_trace(trace_id: str) -> list[dict]:
    """Fetch ONE trace's hops from Aurora by id, with NO time window — the
    windowless companion to _read_remote_events(). /api/traces caps its Aurora
    read at a recent window (full jsonb payloads under the Data API's 1 MB
    result ceiling), so a trace linked from an OLD execution — Observations
    span days, that window is hours — is absent from the trace list and the UI
    renders "Trace not found". A single trace is small and its id is exact, so
    keying on trace_id alone stays well under the result cap regardless of age."""
    try:
        from observability.pg import SCHEMA, PgClient

        if not PgClient.configured():
            return []
        if _remote["client"] is None:
            _remote["client"] = PgClient.from_env()
        rows = _remote["client"].execute(
            f"""SELECT trace_id, hop_seq, ts, source, target, protocol,
                       transport_detail, status, latency_ms, platform_ref,
                       request_payload_raw::text AS request_payload_raw,
                       response_payload_raw::text AS response_payload_raw
                FROM {SCHEMA}.trace_events
                WHERE trace_id = :tid ORDER BY hop_seq, ts""",
            {"tid": trace_id},
        )
    except Exception:
        return []
    for row in rows:
        for key in ("request_payload_raw", "response_payload_raw"):
            if isinstance(row.get(key), str):
                try:
                    row[key] = json.loads(row[key])
                except (json.JSONDecodeError, TypeError):
                    pass
    return rows


def _trace_by_id(trace_id: str) -> dict | None:
    """One trace's hops, local jsonl + Aurora, deduped — same entry shape as
    /api/traces returns, but windowless (see _read_remote_trace). Returns None
    when neither store holds the id."""
    local = [e for e in _read_events() if e.get("trace_id") == trace_id]
    seen = {
        (e.get("hop_seq"), round(e.get("ts") or 0, 4), e.get("source"), e.get("target"))
        for e in local
    }
    evs = list(local)
    for ev in _read_remote_trace(trace_id):
        key = (ev.get("hop_seq"), round(ev.get("ts") or 0, 4), ev.get("source"), ev.get("target"))
        if key not in seen:
            evs.append(ev)
    if not evs:
        return None
    evs.sort(key=lambda e: (e.get("ts", 0), e.get("hop_seq", 0)))
    return {
        "trace_id": trace_id,
        "started": evs[0].get("ts"),
        "hops": evs,
        "protocols": sorted({e.get("protocol", "?") for e in evs}),
    }


# ---- role enforcement (WS6 U3, console half; role model per D36) -----------
# viewer: insights, the obs dashboard READ-ONLY, and the Lab Guide.
# operator (and the header-borne service token, which carries no user):
# everything — runs, warm-ups, harvest/analyze, raw traces. Enforced
# server-side; the UI hides what a role can't do, but the 403 is the guard.

# Traces stay viewer-visible on purpose: the org serves dummy demo data
# only, and the wire record IS the exhibit.
_OPERATOR_ONLY = {
    "run experiments": ("/api/run",),
    "warm up runtimes": ("/api/warmup",),
    "harvest platform logs": ("/api/obs/harvest",),
    "fire the analyst": ("/api/obs/analysis",),
}


def _viewer_forbidden(request: Request) -> None:
    user = request.scope.get("state", {}).get("lab_user") or {}
    if user.get("role") != "viewer":
        return
    path = request.url.path
    for action, prefixes in _OPERATOR_ONLY.items():
        if any(path.startswith(p) for p in prefixes):
            raise HTTPException(
                status_code=403,
                detail=f"viewer role — '{action}' is operator-only (D36 role model)",
            )


def create_console_app(registry: Registry | None = None):
    state = {"registry": registry}
    # One long-lived client per target (same rule as the bridge): they cache
    # OAuth tokens, sessions, and connections across runs.
    clients: dict[str, RemoteAgentClient] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for client in clients.values():
            await client.aclose()
        clients.clear()

    app = FastAPI(title="A2A lab console", lifespan=lifespan)

    @app.middleware("http")
    async def role_gate(request: Request, call_next):
        try:
            _viewer_forbidden(request)
        except HTTPException as exc:
            return Response(
                content=json.dumps({"detail": exc.detail}),
                status_code=exc.status_code,
                media_type="application/json",
            )
        return await call_next(request)

    # Component screenshots, the vendored mermaid bundle, the shell's own
    # assets — repo content, served on the public surface (see the middleware
    # exemptions below). It has to be: browsers cannot put a bearer header on
    # an <img src> or <script src>, and D36 removed query-param credentials,
    # so anything token-gated here simply 401s in the page.
    from fastapi.staticfiles import StaticFiles

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    # Async-scenario runs continue after /api/run returns; keep strong refs
    # so the tasks aren't garbage-collected mid-research.
    background_runs: set[asyncio.Task] = set()

    def get_registry() -> Registry:
        if state["registry"] is None:
            state["registry"] = Registry.load()
        return state["registry"]

    def get_client(name: str) -> RemoteAgentClient:
        if name not in clients:
            clients[name] = get_registry().client_for(name)
        return clients[name]

    # The SPA shell is ONE 430KB file that changes on every deploy, and the
    # whole app lives inside it — a stale cached shell hides a just-shipped
    # section (Monitoring, WS18, did exactly this: deployed and served at the
    # origin, invisible in the browser). With no cache directive a browser
    # heuristically caches HTML, so returning visitors run yesterday's page.
    # `no-cache` = revalidate before use; on a released exhibit correctness of
    # the shell beats saving a few KB per load. The /static mount (versioned
    # assets) keeps its own caching. Applies to both entry points.
    SHELL_HEADERS = {"cache-control": "no-cache"}

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return HTMLResponse(
            (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
            headers=SHELL_HEADERS,
        )

    @app.get("/guide", response_class=HTMLResponse)
    async def guide_page():
        # The Lab Guide popped into its own OS window. Same page, same origin:
        # it boots guide-only off location.pathname and shares the JWT + chat
        # history the console left in localStorage. No separate template to
        # keep in step.
        return HTMLResponse(
            (STATIC_DIR / "index.html").read_text(encoding="utf-8"),
            headers=SHELL_HEADERS,
        )

    @app.get("/api/targets")
    async def targets(request: Request):
        reg = get_registry()
        out = []
        for t in reg.targets.values():
            resolved_name = reg.resolve_name(t.name)
            details = cell_details(reg.get(resolved_name))
            if resolved_name != t.name:
                details["blurb"] += (
                    f" (A2ALAB_MODE={reg.mode} remaps this call to {resolved_name} — "
                    "the hosted runtime answers instead of the local server.)"
                )
            out.append(
                {
                    "name": t.name,
                    "platform": t.platform,
                    "protocol": t.protocol,
                    "status": t.status,
                    "components": public_components(
                        components_for(_PLATFORM_TAGS.get(t.platform, set())),
                        _signed_in(request),
                    ),
                    **details,
                }
            )
        return {"targets": out, "default_question": DEFAULT_QUESTION}

    # The delegating seam's identity per scenario — the rider exhibit shows
    # the RESOLVED block this experiment injects, not placeholders.
    def _scenario_rider(name: str) -> str | None:
        if name.startswith("agentforce-to-"):
            return delegation.rider_for("agentforce-twin-via-bridge", "agentforce")
        by_name = {
            "claude-to-agentforce": (
                "claude-managed-agent"
                if os.environ.get("CLAUDE_BACKEND", "managed") == "managed"
                else "claude-sdk-agent",
                "claude",
            ),
            "claude-aws-to-agentforce": ("claude-sdk-agent", "claude"),
            "strands-to-agentforce": ("strands-sdk-agent", "strands"),
            "chatgpt-to-agentforce": ("openai-agents-sdk-agent", "openai"),
            "adk-to-agentforce": ("adk-gemini-agent", "adk"),
            "foundry-to-agentforce": ("foundry-agent", "foundry"),
            # The fan-out scenarios delegate too — three times per run, to
            # three platforms. Showing no rider there implied the guard was
            # an Agentforce-only concern.
            "supplier-disruption-cma": ("a2alab-supply-orchestrator", "claude"),
            "supplier-disruption-adk": ("a2alab-supply-orchestrator-adk", "adk"),
            # Variant 3 (D61): the Agentforce orchestrator delegates the fan-out
            # through the bridge, which stamps THIS caller on each leg.
            "supplier-disruption-agentforce": (
                "agentforce-orchestrator-via-bridge",
                "agentforce",
            ),
        }
        pair = by_name.get(name)
        return delegation.rider_for(*pair) if pair else None

    @app.get("/api/scenarios")
    async def scenarios(request: Request):
        signed_in = _signed_in(request)
        return {
            "groups": load_groups(),
            "scenarios": [
                {
                    "name": name,
                    **spec,
                    "components": public_components(
                        components_for(set(spec.get("tags") or [])), signed_in
                    ),
                    **({"rider": r} if (r := _scenario_rider(name)) else {}),
                }
                for name, spec in load_scenarios().items()
            ],
        }

    @app.get("/api/agents")
    async def agents():
        """The Agent Registry (config/agents.yaml): one tile per distinct
        deployed agent, for Infrastructure → Agent Registry.

        This reads config/agents.yaml (the source of truth) and resolves each
        agent's `experiments` slugs to their scenario titles + live status from
        config/scenarios.yaml, so a tile can list clickable experiment rows and
        the two files cannot silently drift (an experiment renamed in scenarios
        but not here shows as "(unknown)" rather than a dead link). The live
        AgentCard is NOT fetched here — the tile lazy-loads it from
        /api/agent-card/{card_target} on expand, exactly as the experiment
        Details tab does, so this endpoint stays fast and never blocks on a cold
        platform."""
        reg = load_agents()
        scn = load_scenarios()

        def _exp(slug: str) -> dict:
            spec = scn.get(slug)
            return {
                "name": slug,
                "title": (spec or {}).get("title") or "(unknown experiment)",
                "group": (spec or {}).get("group"),
                "status": (spec or {}).get("status", "missing"),
            }

        out = []
        for a in reg["agents"]:
            out.append(
                {
                    **a,
                    "experiments": [_exp(s) for s in (a.get("experiments") or [])],
                }
            )
        return {"groups": reg["groups"], "agents": out}

    # ---- Insights + deployment mode ---------------------------------------
    # The trusted-advisor findings (config/insights.yaml via console.insights)
    # and which runtimes /api/run really hits under the active A2ALAB_MODE.

    @app.get("/api/architecture")
    async def architecture(request: Request):
        """The deployment map (plan/09-deployment-map.md), parsed into levels.

        Read from the plan file on every request rather than cached: the doc is
        the source of truth, and an operator editing it mid-readout should see
        the change on refresh, not after a restart.

        The doc's `## Presenter notes` section is stripped for everyone except a
        signed-in reviewer — the lab owner (config/users.yaml, `reviewer: true`).
        Stripped server-side rather than hidden in CSS: the page is served on a
        public hostname, and speaker prep that ships to the browser is published
        whether or not it is displayed.
        """
        from console.architecture import load as load_architecture

        doc = load_architecture()
        if not _is_reviewer(request):
            doc["presenter"] = ""
        return doc

    @app.get("/api/project")
    async def project():
        """How the lab is built and delivered (WS17, D60).

        Renders the delivery process from the PLAN and repo — never from Jira.
        The board counts here are computed by the SAME code jira_sync.py runs
        (scripts/jira_sync.parse_plan), so the console and the board cannot
        disagree: both read plan/07-workstreams.md and count the same way. D58
        made the board a one-way view generated from the plan; a panel that
        pulled live Jira would reintroduce exactly the drift D58 removed (a
        status true in Jira and nowhere the repo can see). The Jira link below
        is a launch point, not a data source — nothing here reads the board.
        """
        import sys as _sys

        scripts_dir = str(Path(__file__).resolve().parents[2] / "scripts")
        if scripts_dir not in _sys.path:
            _sys.path.insert(0, scripts_dir)
        try:
            import jira_sync
        except Exception as exc:  # noqa: BLE001
            return {"error": f"cannot load the plan parser: {type(exc).__name__}: {exc}"}

        plan = jira_sync.parse_plan()
        # Board arithmetic, computed the way jira_sync does (parse_items + the
        # epic-closes-on-its-stories rule). Epics = workstreams; stories = the
        # work items in each. A narrative-only workstream has an epic and no
        # stories, and stays OPEN even when it shipped — the same safe-direction
        # rule jira_sync applies, restated here so the counts match the board.
        epics = len(plan)
        epics_done = sum(1 for w in plan if w["done"])
        stories = sum(len(w["items"]) for w in plan)
        stories_done = sum(1 for w in plan for it in w["items"] if it["done"])
        narrative = sum(1 for w in plan if not w["items"] and w["status"])
        not_started = sum(1 for w in plan if not w["items"] and not w["status"])

        workstreams = [
            {
                "ws": w["ws"],
                "title": w["title"],
                "done": w["done"],
                "status": w["status"],
                "adrs": w["adrs"],
                "items_total": len(w["items"]),
                "items_done": sum(1 for it in w["items"] if it["done"]),
                "shape": (
                    "stories" if w["items"] else "narrative" if w["status"] else "not-started"
                ),
                "items": [
                    {"n": it["n"], "summary": it["summary"], "done": it["done"]}
                    for it in w["items"]
                ],
            }
            for w in plan
        ]

        site = os.environ.get("JIRA_SITE_URL", "").rstrip("/")
        project_key = os.environ.get("JIRA_PROJECT_KEY", "A2A")
        # Launch-out only. The board is browsed in Jira; the console never reads
        # it back (D60). Empty when JIRA_SITE_URL is unset — the UI then says the
        # board is generated but not linked, rather than showing a dead button.
        jira_url = f"{site}/jira/software/projects/{project_key}/boards" if site else ""

        return {
            "counts": {
                "epics": epics,
                "epics_done": epics_done,
                "stories": stories,
                "stories_done": stories_done,
                "narrative_epics": narrative,
                "not_started_epics": not_started,
            },
            "workstreams": workstreams,
            "jira": {"url": jira_url, "site": site, "project_key": project_key},
            # The delivery process, cited so the D-chips and doc-chips linkify.
            "process": (
                "The lab is built in **workstreams** (WS1…), each an epic. Scope for a "
                "workstream lives in `plan/07-workstreams.md` and the reasoning behind "
                "every choice is an ADR in `plan/00-decisions.md` — 58 of them and "
                "counting. Work items inside a workstream become stories; a workstream "
                "the plan records only as narrative becomes an epic with no stories, "
                "because turning prose into stories would invent a granularity the "
                "work never had (D58).\n\n"
                "The board is **generated one way** from the plan by "
                "`scripts/jira_sync.py` and read as a delivery view in Jira. Nothing "
                "reads it back: a status edited only in Jira is a status the plan and "
                "the console never see (D58, D60). The counts on this page are computed "
                "by the same parser `jira_sync.py` uses, so this page and the board "
                "cannot disagree — they are the same arithmetic over the same file."
            ),
            "docs": [
                {"path": "plan/07-workstreams.md", "label": "Workstreams — the scope, per WS"},
                {"path": "plan/00-decisions.md", "label": "Decisions (ADRs) — the reasoning"},
                {
                    "path": "plan/11-delivery.md",
                    "label": "Delivery record — how the board is generated",
                },
            ],
        }

    @app.get("/api/expiry")
    async def expiry(request: Request):
        """Credential countdown for the operator (scripts/expiry_report.py).

        Operator-only: expiry dates describe the lab's rotation posture, which
        is not part of the public exhibit. Served from a stored snapshot rather
        than by shelling out per request — the collectors call four cloud APIs
        and take seconds, which is a refresh action, not a page load.

        **Hosted store first, local file second (WS13).** The collector needs
        the operator's own AWS/az/gcloud sessions, so it cannot run in the
        container the console will move into; reading only `.a2alab/expiry.json`
        would leave this endpoint permanently empty once hosted. The file
        remains the fallback so a laptop run with no Aurora config still works.
        """
        import json as _json

        if not _is_operator(request):
            raise HTTPException(status_code=403, detail="operator-only")

        try:
            from observability.pg import STATE_EXPIRY, PgClient, PgObsStore

            if PgClient.configured():
                store = PgObsStore()
                try:
                    stored = store.get_state(STATE_EXPIRY)
                finally:
                    store.close()
                if stored:
                    return stored
        except Exception:  # noqa: BLE001 - fall through to the file
            pass

        path = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "expiry.json"
        if not path.exists():
            return {
                "credentials": [],
                "error": "no report yet — run: uv run python scripts/expiry_report.py --write",
            }
        try:
            report = _json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            return {"credentials": [], "error": f"unreadable report: {exc}"}
        return report

    @app.post("/api/credentials/analyze")
    async def credentials_analyze(request: Request):
        """Fire the credential analyst (a Claude Managed Agent) and return its
        briefing.

        Runs the COLLECTOR here rather than in the agent: gathering expiry dates
        needs the operator's own AWS/az/gcloud sessions, which a hosted agent
        does not have. The agent is handed measured numbers and asked for
        judgment — D22's split, applied to credentials.
        """
        if not _is_operator(request):
            raise HTTPException(status_code=403, detail="operator-only")

        def run():
            proc = subprocess.run(
                [sys.executable, "scripts/credential_analyst.py", "run"],
                capture_output=True,
                text=True,
                timeout=300,
                cwd=str(Path.cwd()),
                env={**os.environ, "PYTHONPATH": "src"},
            )
            return proc.returncode, proc.stdout, proc.stderr

        try:
            code, out, err = await asyncio.get_event_loop().run_in_executor(None, run)
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": "analyst timed out after 5 minutes"}
        brief_path = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "credential_brief.json"
        if code != 0 or not brief_path.exists():
            return {"ok": False, "error": (err or out or "analyst failed").strip()[-600:]}
        import json as _json

        return {"ok": True, **_json.loads(brief_path.read_text())}

    @app.get("/api/credentials/brief")
    async def credentials_brief(request: Request):
        if not _is_operator(request):
            raise HTTPException(status_code=403, detail="operator-only")
        import json as _json

        path = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "credential_brief.json"
        if not path.exists():
            return {"brief": None}
        return _json.loads(path.read_text())

    @app.get("/api/insights")
    async def insights(request: Request):
        data = reviews.attach_reviews(load_insights())
        return {
            "insights": data,
            "categories": by_category(data),
            # The UI hides the sign-off control for everyone else; the 403 on
            # POST is the actual guard (same rule as the role model above).
            "can_review": _is_reviewer(request),
        }

    def _signed_in(request: Request) -> bool:
        """Is this caller a known one — a verified persona or the service token?

        The credential is checked HERE rather than read off `scope["state"]`,
        because the endpoints this gates (`/api/scenarios`, `/api/targets`) are
        on the middleware's exempt list: it returns before verifying anything,
        so `lab_user` is never populated on exactly these paths. Trusting the
        state here would strip the links for signed-in operators too.
        """
        from interop import identity

        claims = request.scope.get("state", {}).get("lab_user")
        if claims:
            return True
        supplied = request.headers.get("x-lab-token")
        if not supplied:
            authz = request.headers.get("authorization", "")
            if authz.startswith("Bearer "):
                supplied = authz[len("Bearer ") :]
        if not supplied:
            return False
        expected = os.environ.get("A2ALAB_TOKEN")
        if expected and supplied == expected:
            return True
        return identity.verify_token(supplied) is not None

    def _is_operator(request: Request) -> bool:
        """A verified persona whose role is operator (config/users.yaml).
        Unlike _signed_in, the shared service token does not qualify: it
        identifies no one, and this is per-person operational data."""
        from interop import identity

        claims = request.scope.get("state", {}).get("lab_user") or {}
        sub = claims.get("sub")
        if not sub:
            return False
        role = identity.load_users().get(sub, {}).get("role")
        return identity.is_operator_role(role)

    def _is_owner(request: Request) -> bool:
        """The lab OWNER alone (role 'master of the universe') — narrower than
        _is_operator. Gates the in-org "A2A Lab" app deep link, which names the
        org my-domain and lands on a Salesforce login only the owner can pass;
        the operator (Ana) sees the dashboard screenshot but not the link."""
        from interop import identity

        claims = request.scope.get("state", {}).get("lab_user") or {}
        sub = claims.get("sub")
        if not sub:
            return False
        role = identity.load_users().get(sub, {}).get("role")
        return identity.is_owner_role(role)

    def _is_reviewer(request: Request) -> bool:
        """Sign-off is a named person's act: a verified lab user carrying
        `reviewer: true` in config/users.yaml. The shared service token
        identifies no one, so it can never approve a published claim."""
        from interop import identity

        claims = request.scope.get("state", {}).get("lab_user") or {}
        sub = claims.get("sub")
        if not sub:
            return False
        return bool(identity.load_users().get(sub, {}).get("reviewer"))

    @app.post("/api/insights/{insight_id}/review")
    async def review_insight(insight_id: str, request: Request):
        """Record the lab reviewer's decision on a published claim (approve
        or request changes, with an optional comment) into
        config/insight_reviews.yaml."""
        if not _is_reviewer(request):
            raise HTTPException(
                status_code=403,
                detail="insight sign-off is reserved to the lab's reviewer (config/users.yaml)",
            )
        body = await request.json()
        decision = str(body.get("decision") or "").strip()
        if decision not in reviews.STATES:
            raise HTTPException(
                status_code=400, detail=f"decision must be one of {list(reviews.STATES)}"
            )
        insight = next((i for i in load_insights() if i.get("id") == insight_id), None)
        if insight is None:
            raise HTTPException(status_code=404, detail=f"no insight '{insight_id}'")
        claims = request.scope.get("state", {}).get("lab_user") or {}
        reviews.record(insight, decision, user=claims, comment=str(body.get("comment") or ""))
        return {
            "id": insight_id,
            "review_state": reviews.review_state(insight, reviews.load_reviews()),
        }

    @app.get("/api/insights.md")
    async def insights_md():
        """The deck-ready markdown export — same renderer as
        scripts/export_insights.py, served with a filename so it downloads
        cleanly (this is what gets pulled into Claude Design)."""
        return Response(
            to_markdown(load_insights()),
            media_type="text/markdown; charset=utf-8",
            headers={"content-disposition": 'attachment; filename="a2a-lab-insights.md"'},
        )

    @app.get("/api/whats-next")
    async def whats_next():
        """The roadmap-horizon plans (config/whats_next.yaml) for the console's
        "What's Next" section — the answer to "where do you go from here?".

        Read from config on every request, like /api/architecture: the file is
        the source of truth, and an operator adding a plan should see it on
        refresh, not after a restart. Gated like the Insights feed (NOT on the
        middleware's exempt list): the Control Panel that reaches it is itself
        behind sign-in, so the section loads via SIGNED_IN_LOADERS, same as
        /api/insights."""
        return {"plans": load_plans()}

    @app.get("/healthz")
    async def healthz():
        """Liveness for the ALB target group (WS13).

        Deliberately answers from process state only — no store round trip. A
        health check that reaches Aurora turns a slow query into a rolling task
        replacement, and the console's job when the store is down is to say so,
        not to be replaced."""
        return {"status": "healthy", "app": "console"}

    @app.get("/api/decisions")
    async def decisions():
        """The ADR log parsed per decision id — the UI renders D-refs as
        chips whose popover shows the decision's markdown."""
        return {"decisions": load_decisions()}

    @app.get("/api/users")
    async def users():
        """The lab user directory (WS6 U1) — feeds the console's sign-in
        picker. Demo-scale IdP: no passwords, the experiment is identity
        PROPAGATION and authorization, not credential UX."""
        from interop import identity

        return {
            "users": [
                {"username": u, "name": e.get("name") or u, "role": e.get("role") or "viewer"}
                for u, e in identity.load_users().items()
            ]
        }

    @app.post("/api/login")
    async def login(request: Request):
        """Password-gated lab JWT issue (D36): persona + that ROLE's shared
        password from .env. This endpoint (with /api/users and /) is the
        public surface of the console — everything else needs the JWT the
        exchange returns, sent as Authorization: Bearer. The raw lab token
        never reaches a browser."""
        from interop import identity

        body = await request.json()
        username = (body.get("username") or "").strip()
        try:
            token = identity.authenticate(username, str(body.get("password") or ""))
        except ValueError:
            # One generic 401 — no probing which of user/password was wrong.
            raise HTTPException(status_code=401, detail="wrong user or password") from None
        claims = identity.verify_token(token) or {}
        return {"token": token, "user": identity.user_context(claims)}

    # WS18 — console usage analytics. The browser posts anonymous interaction
    # events (a visit before sign-in, a persona login, a top-level section nav)
    # to this same-origin route; the route stores a PII-free row in
    # lab.usage_events AND forwards to the operator's external AWS log sink
    # (Slack notify). Option B in the plan: the logger URL + key live in the
    # SERVER's environment (Secrets Manager), never in the public bundle —
    # unlike the mega-demo, which inlines them into the client. The "A2A Lab
    # Monitoring" section reads the aggregates back via /api/monitoring.

    KNOWN_USAGE_EVENTS = {"site_visit", "persona_login", "nav"}

    def _usage_writer_store():
        """A PgObsStore bound to the WRITER secret, or None off-cluster.

        record_usage INSERTs, and the default reader pair fails an INSERT with
        'cannot execute INSERT in a read-only transaction' (D46). Same writer
        construction the expiry report and the insight-review push use.
        """
        try:
            from observability.pg import PgClient, PgObsStore

            if not PgClient.configured():
                return None
            cluster = os.environ.get("A2ALAB_PG_CLUSTER_ARN")
            writer = os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
            client = (
                PgClient(cluster_arn=cluster, secret_arn=writer)
                if cluster and writer
                else PgClient.from_env()
            )
            return PgObsStore(client)
        except Exception:  # noqa: BLE001 - no cluster/boto3: analytics just no-op
            return None

    async def _forward_to_logger(message: str, detail: dict) -> None:
        """Fire-and-forget POST to the external AWS logger (mega-demo contract:
        {source, level, message, detail} + X-Api-Key). A logging failure must
        never surface — the console has already stored its own row, and the
        forward is the operator's Slack convenience, not the source of truth.
        """
        url = os.environ.get("A2ALAB_LOGGING_API_URL")
        key = os.environ.get("A2ALAB_LOGGING_API_KEY")
        if not url or not key:
            return
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                await client.post(
                    url,
                    headers={"Content-Type": "application/json", "X-Api-Key": key},
                    json={
                        "source": "a2a-console",
                        "level": "info",
                        "message": message,
                        "detail": detail,
                    },
                )
        except Exception:  # noqa: BLE001 - swallow exactly like the mega-demo's .catch(()=>{})
            pass

    @app.post("/api/track")
    async def track(request: Request):
        """Record one usage event. Exempt from auth so an UNAUTHENTICATED
        visit can be logged before any sign-in; write-only, returns 204 and
        discloses nothing. Persona/role are read from the verified JWT if the
        caller has one — never trusted from the request body."""
        try:
            body = await request.json()
        except Exception:  # noqa: BLE001 - a malformed beacon is not an error worth 400ing
            return Response(status_code=204)
        event = str(body.get("event") or "").strip()
        if event not in KNOWN_USAGE_EVENTS:
            # Unknown event names are dropped rather than stored: the schema is
            # a closed set, and an open sink is how analytics tables fill with
            # typos. 204 anyway — the client fires and forgets.
            return Response(status_code=204)

        # Identity comes from the JWT the middleware verified (exempt paths do
        # not populate scope state, so re-verify the bearer here, same as
        # _signed_in does for the other exempt routes).
        from interop import identity

        persona = role = None
        authz = request.headers.get("authorization", "")
        if authz.startswith("Bearer "):
            claims = identity.verify_token(authz[len("Bearer ") :]) or {}
            persona = claims.get("sub")
            role = claims.get("role") or (
                identity.load_users().get(persona, {}).get("role") if persona else None
            )

        # Country from Cloudflare's CF-IPCountry (2-letter code, not an IP);
        # locale from the first Accept-Language tag. Both may be absent (local
        # dev, or CF not forwarding) — stored as NULL, surfaced as "unknown".
        country = (request.headers.get("cf-ipcountry") or "").strip().upper()[:8] or None
        accept_lang = request.headers.get("accept-language", "")
        locale = (accept_lang.split(",")[0].split(";")[0].strip() or None) if accept_lang else None

        detail = body.get("detail") if isinstance(body.get("detail"), dict) else {}
        section = str(body.get("section") or "").strip()[:64] or None
        visitor_id = str(body.get("visitor_id") or "").strip()[:64] or None

        def write():
            store = _usage_writer_store()
            if store is None:
                return
            try:
                store.record_usage(
                    event,
                    visitor_id=visitor_id,
                    persona=persona,
                    role=role,
                    country=country,
                    locale=locale,
                    section=section,
                    detail=detail or None,
                )
            finally:
                store.close()

        # Store first (the durable record), then forward to Slack. Neither is
        # allowed to fail the request: the row write runs in a thread and its
        # exceptions are logged, the forward swallows its own.
        try:
            await asyncio.get_event_loop().run_in_executor(None, write)
        except Exception as exc:  # noqa: BLE001 - a full analytics table must not 500 a beacon
            print(f"/api/track store failed ({type(exc).__name__}: {str(exc)[:120]})")
        msg = {
            "site_visit": "A2A console visit",
            "persona_login": f"A2A console sign-in ({persona or 'unknown'})",
            "nav": f"A2A console section: {section or 'unknown'}",
        }.get(event, f"A2A console {event}")
        await _forward_to_logger(
            msg,
            {
                "event": event,
                "persona": persona,
                "role": role,
                "country": country,
                "locale": locale,
                "section": section,
                **(detail or {}),
            },
        )
        return Response(status_code=204)

    @app.get("/api/monitoring")
    async def monitoring(request: Request):
        """Usage aggregates for the A2A Lab Monitoring section, over a rolling
        window (day/week/month/year/all). Signed-in only — it is lab operating
        data, not part of the public exhibit."""
        if not _signed_in(request):
            raise HTTPException(status_code=401, detail="sign in to view lab monitoring")
        window = (request.query_params.get("window") or "week").lower()
        days = {"day": 1, "week": 7, "month": 30, "year": 365, "all": None}.get(window, 7)

        from observability.pg import PgClient, PgObsStore

        if not PgClient.configured():
            return {"window": window, "stats": None, "error": "hosted store not configured"}

        def run():
            store = PgObsStore()
            try:
                return store.usage_stats(days=days)
            finally:
                store.close()

        try:
            stats = await asyncio.get_event_loop().run_in_executor(None, run)
            return {"window": window, "stats": stats}
        except Exception as exc:  # noqa: BLE001 - surface, don't 500 the panel
            return {"window": window, "stats": None, "error": f"{type(exc).__name__}: {exc}"}

    @app.post("/api/guide")
    async def guide(request: Request):
        """The Lab Guide chat (plan/07, Lab Guide): stateless streaming turns
        — client holds history, the server streams SSE events {delta|tool|
        done}. Same interior the guide's REST/MCP/A2A servers use."""
        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise HTTPException(status_code=503, detail="guide unavailable — no ANTHROPIC_API_KEY")
        body = await request.json()
        message = (body.get("message") or "").strip()
        if not message:
            raise HTTPException(status_code=400, detail="empty message")
        history = body.get("history") or []
        view_ctx = body.get("view") or None

        from platforms.guide.core import make_adapter as make_guide

        if state.get("guide") is None:
            state["guide"] = make_guide()
        guide_adapter = state["guide"]

        async def gen():
            try:
                async for event in guide_adapter.answer_stream(
                    message, history=history, view=view_ctx
                ):
                    yield f"data: {json.dumps(event)}\n\n"
            except Exception as exc:  # surface as an SSE event, not a broken stream
                yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream")

    @app.get("/api/docs/{name:path}")
    async def doc(name: str):
        """Whitelisted lab docs + source files as raw text — the UI renders
        reference chips into popovers from these, same pattern as the decision
        chips. Markdown docs (plan/docs/build-notes/README) render through the
        client's markdown renderer; the two SOURCE trees (config/*.yaml and the
        Claude Code workflow scripts under .claude/workflows) render verbatim in
        a <pre>. All are public repo content — config YAMLs carry no secrets
        (the repo keeps those in .env / Secrets Manager) and the workflows are
        the WS20 exhibit. `.claude` is scoped to workflows/*.js so
        settings.local.json is never served."""
        repo = Path.cwd().resolve()
        candidate = (repo / name).resolve()
        allowed = (
            candidate == repo / "README.md"
            or (candidate.parent == repo / "plan" and candidate.suffix == ".md")
            or (candidate.parent == repo / "docs" and candidate.suffix == ".md")
            # build-notes/<tool>/*.md — the presentation source notes. Insight
            # refs already pointed at these and rendered as doc chips, so the
            # chips existed and 404'd; they are public repo content, same class
            # as plan/. `is_relative_to` rather than a parent check because the
            # tree is nested one level (build-notes/claude/…).
            or (candidate.is_relative_to(repo / "build-notes") and candidate.suffix == ".md")
            # config/*.yaml — the lab's own inputs (insights, targets, scenarios,
            # what's-next). Most are already served in PARSED form elsewhere; the
            # Insights Details view links the raw source so a reader sees the file
            # the insights-audit workflow actually reads.
            or (candidate.parent == repo / "config" and candidate.suffix in (".yaml", ".yml"))
            # .claude/workflows/*.js — the Claude Code orchestration scripts, the
            # WS20 exhibit (an actual actor-critic workflow). Scoped to this dir
            # and .js ONLY: settings.local.json alongside it must never be served.
            or (candidate.parent == repo / ".claude" / "workflows" and candidate.suffix == ".js")
        )
        if not allowed or not candidate.exists():
            raise HTTPException(status_code=404, detail=f"unknown doc: {name}")
        return {"name": name, "markdown": candidate.read_text(encoding="utf-8")}

    @app.get("/api/config")
    async def config(request: Request):
        reg = get_registry()
        # WS19/M10: the in-org "A2A Lab" Lightning app holds the live Tableau
        # Next dashboard over the Zero-Copy observability data. Everyone sees
        # the screenshot (static/components/tableau-next-traffic.png); only the
        # OWNER gets the deep link, because it names the org my-domain and lands
        # on a Salesforce login that only the owner can pass during a controlled
        # presentation. Scrubbed HERE (server-side), not just hidden in the UI —
        # same posture as public_components (2026-07-27) and the D36 role gates.
        ld = _lightning_domain()
        # Deep link to the in-org custom TAB (/lightning/n/<tab>) rather than the
        # raw /tableau/dashboard view: the tab is an App Builder page carrying the
        # licence + asset-share context the runtime needs, so it is the surface
        # that renders for a signed-in admin. Built from env so the org my-domain
        # is never a literal. This is the "open in Salesforce" target; the inline
        # embed below renders the same dashboard in-page.
        tableau_app_url = f"{ld}/lightning/n/{TABLEAU_APP_TAB}" if ld else None
        owner = _is_owner(request)
        # The inline embed: for the OWNER only, the console mints a frontdoor
        # URL server-side (see _mint_frontdoor_url) so the dashboard renders in
        # the tab with no login step. Everyone else gets the screenshot. `embed`
        # carries only non-secret rendering params — the SDK source, the org
        # LIGHTNING url (orgUrl), the dashboard api-name; the frontdoor URL is
        # NOT here, it is fetched per-render from /api/tableau/frontdoor, which
        # is itself owner-gated. `embed` is present only when both the owner is
        # asking AND the embed ECA is wired, so the UI never offers a control
        # that would 503.
        embed = None
        if owner and ld and _tableau_embed_configured():
            embed = {
                "sdk_url": TABLEAU_SDK_URL,
                "org_url": ld,
                "dashboard": TABLEAU_DASHBOARD_API_NAME,
                "frontdoor_endpoint": "/api/tableau/frontdoor",
            }
        tableau_next = {
            "app_url": tableau_app_url if owner else None,
            "app_url_owner_only": bool(tableau_app_url),
            "embed": embed,
            "embed_owner_only": bool(ld and _tableau_embed_configured()),
            "shot": (
                "/static/components/tableau-next-traffic.png"
                if (SHOTS_DIR / "tableau-next-traffic.png").exists()
                else None
            ),
        }
        return {
            "mode": reg.mode,
            "modes": reg.modes,
            "tableau_next": tableau_next,
            # Lab Guide chat: how many past chats the drawer keeps
            # (client-side localStorage; the server just sets the cap).
            "guide_history_limit": int(os.environ.get("GUIDE_CHAT_HISTORY", "10")),
            "remapped": {
                name: reg.resolve_name(name)
                for name in reg.targets
                if reg.resolve_name(name) != name
            },
            # D27: shown read-only in the run panel so the injected rider is
            # a visible design decision, not hidden plumbing.
            "delegation": {
                "max_depth": delegation.max_depth(),
                "rider": delegation.example_rider(),
                # Every seam that stamps the rider, not just the Agentforce
                # ones. The fan-out entries were missing, which made the
                # exhibit read as "this is about consulting Agentforce" on a
                # scenario that never touches Salesforce — the guard is
                # general, and the list has to show that.
                "seams": [
                    "ask_agentforce (sdk)",
                    "ask_agentforce (managed)",
                    "ask_agentforce (openai)",
                    "ask_agentforce (adk)",
                    "bridge",
                    "consult_business_units (fan-out, host-side)",
                    "consult_<unit> (fan-out, ADK ParallelAgent)",
                    "bridge fanout: route (Agentforce orchestrator, D61)",
                ],
                # The <placeholders> in the rider are display-only; real
                # injected blocks carry the delegating seam's identity:
                "callers": [
                    "claude-sdk-agent (claude)",
                    "claude-managed-agent (claude)",
                    "openai-agents-sdk-agent (openai)",
                    "adk-gemini-agent (adk)",
                    "agentforce-twin-via-bridge (agentforce)",
                    "a2alab-supply-orchestrator (claude)",
                    "a2alab-supply-orchestrator-adk (adk)",
                    "agentforce-orchestrator-via-bridge (agentforce)",
                ],
            },
            # D28 sibling exhibit: the per-run channel routing block the
            # console injects when the operator picks a2a-shim.
            "af_channel": {
                "tools": af_channel.CHANNEL_TOOLS,
                "routing_block": af_channel.routing_block("a2a-shim"),
            },
            # Sibling exhibit for the reverse direction: the twin's outbound
            # route (bridge = traced, direct = platform-native, untraced).
            "af_route": {
                "tools": af_channel.ROUTE_TOOLS,
                "routing_block": af_channel.route_block("direct"),
            },
            # WS8 variant 3 (D61): the Agentforce orchestrator's fan-out
            # topology (delegated = parallel off-platform via the bridge,
            # serial = three stacked Apex callouts that degrade by design).
            "af_topology": {
                "tools": af_channel.TOPOLOGY_TOOLS,
                "routing_block": af_channel.topology_block("serial"),
            },
        }

    @app.get("/api/tableau/frontdoor")
    async def tableau_frontdoor(request: Request):
        """Mint a fresh Salesforce frontdoor URL for the inline Tableau Next
        embed (WS19/M10). OWNER-ONLY — same gate as the app deep link: the URL
        opens a live session as the Tableau-Next-licensed admin, so it is never
        handed to the operator or a viewer. Called per-render (frontdoor URLs
        are short-lived/single-use), so it is not cached. Returns 403 for
        non-owners, 503 if the embed ECA is unwired, 502 if Salesforce rejects
        the exchange (e.g. the `web` scope is missing → Invalid_Scope)."""
        if not _is_owner(request):
            raise HTTPException(status_code=403, detail="owner only")
        uri = await _mint_frontdoor_url()
        return {"frontdoor_uri": uri}

    # ---- Runtime warm-up ---------------------------------------------------
    # AgentCore-hosted runtimes cold-start in ~30-60s (claude ~56s, openai
    # ~31s measured) — enough to blow a demo's timeout budget. The gear panel
    # pings each warmable target (options.warmup in config/targets.yaml)
    # before demonstrating, and every attempt's wall-clock duration lands in
    # <trace_dir>/warmups.jsonl for the cross-platform cold-start comparison.

    WARMUP_PING = "Reply with the single word: ready."
    warming: set[str] = set()

    @app.get("/api/warmup")
    async def warmup_status():
        by_target: dict[str, list[dict]] = {}
        for rec in _read_warmups():
            by_target.setdefault(rec.get("target", "?"), []).append(rec)
        out = []
        for t in get_registry().targets.values():
            if not t.options.get("warmup"):
                continue
            history = sorted(by_target.get(t.name, []), key=lambda r: r.get("ts", 0), reverse=True)
            history = history[:5]
            out.append(
                {
                    "name": t.name,
                    "platform": t.platform,
                    "protocol": t.protocol,
                    "last": history[0] if history else None,
                    "history": history,
                }
            )
        return {"targets": out}

    @app.post("/api/warmup/{name}")
    async def warmup(name: str):
        try:
            target = get_registry().get(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if not target.options.get("warmup"):
            raise HTTPException(status_code=404, detail=f"target '{name}' is not warmable")
        if name in warming:
            raise HTTPException(status_code=409, detail=f"warm-up for '{name}' already in flight")
        warming.add(name)
        started = time.time()
        t0 = time.monotonic()
        try:
            # A fresh, never-remapped client (exact=True): a warm-up must hit
            # the runtime it names, and must not disturb the cached clients'
            # sessions. A timeout is recorded, not raised — a >65s cold start
            # IS a data point.
            client = get_registry().client_for(name, exact=True)
            # warmup_delegated_platform: compose the ping as a delegated
            # request from that platform — the hosted shim keys its shared
            # Salesforce sessions by rider platform, so this pre-creates
            # the exact session the platform's next real call will ride
            # (and the rider guard keeps the twin's answer fast: no
            # external-research step on delegated turns).
            ping = WARMUP_PING
            ping_meta: dict = {}
            if target.options.get("warmup_delegated_platform"):
                ping, ping_meta = delegation.delegate(
                    WARMUP_PING,
                    caller="console-warmup",
                    platform=str(target.options["warmup_delegated_platform"]),
                    inbound_depth=0,
                )
            try:
                resp = await client.ask(
                    AgentRequest(message=ping, metadata=ping_meta, trace_id=new_trace_id())
                )
                ok, note = True, (resp.text or "").strip()
            finally:
                await client.aclose()
        except Exception as exc:  # noqa: BLE001 - the failure is the result
            ok, note = False, f"{type(exc).__name__}: {exc}"
        finally:
            warming.discard(name)
        record = {
            "target": name,
            "ts": round(started, 3),
            "duration_ms": int((time.monotonic() - t0) * 1000),
            "ok": ok,
            "note": note[:140],
        }
        _record_warmup(record)
        return record

    @app.get("/api/agent-card/{target_name}")
    async def agent_card(target_name: str):
        """Fetch a target's live AgentCard, server-side. The browser can't
        reach the A2A servers cross-origin, and the cards are generated at
        runtime — there is no file to serve. The well-known path is
        auth-exempt on our servers, so no token rides along."""
        try:
            reg = get_registry()
            # RESOLVE first (D55). Every other read path does — /api/targets,
            # /api/run — and this one did not, so in hosted mode the Details tab
            # asked localhost:8003 for a card that only exists on the faces
            # service. The browser saw "ConnectError: All connection attempts
            # failed" and the Components tab looked broken, while the same
            # target ran perfectly from the Run tab.
            target = reg.get(reg.resolve_name(target_name))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if target.protocol != "a2a" or not target.endpoint:
            raise HTTPException(
                status_code=409,
                detail=f"target '{target_name}' has no A2A endpoint to serve an agent card",
            )
        if target.options.get("transport"):
            # Pinned-transport target (Vertex AI Agent Engine preview): the
            # platform serves NO public card — the lab synthesizes the same
            # minimal card its client uses. Say so instead of 404ing.
            from google.protobuf.json_format import MessageToDict

            from a2a.client import minimal_agent_card
            from a2a.utils import TransportProtocol

            transport = target.options["transport"].upper().replace("-", "_")
            card = MessageToDict(
                minimal_agent_card(target.endpoint, [getattr(TransportProtocol, transport)])
            )
            return {
                "ok": True,
                "url": target.endpoint,
                "card": card,
                "synthesized": True,
                "note": (
                    "Synthesized locally — this platform's A2A serving is "
                    "preview and registers no public card route (the lab "
                    "client pins the transport instead; see the "
                    "native-a2a-young insight)."
                ),
            }
        url = target.endpoint.rstrip("/") + "/.well-known/agent-card.json"
        # Send the target's OWN configured auth header. The well-known path is
        # in EXEMPT_PATHS, which held while each face was its own server on its
        # own port — but the hosted faces are MOUNTED under a path prefix
        # (D51), and an exempt path does not survive the mount: the middleware
        # matches on the full request path, so /claude-a2a/.well-known/... is
        # not /.well-known/... and every card 401'd.
        #
        # Sending the credential is the better fix either way. These faces are
        # public internet now, and the lab gates them deliberately; relying on
        # an unauthenticated hole for the console's own convenience would be
        # the wrong thing to preserve.
        headers = {}
        auth = target.auth or {}
        if auth.get("header_name") and auth.get("header_value"):
            headers[auth["header_name"]] = auth["header_value"]
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                r = await http.get(url, headers=headers)
                r.raise_for_status()
                card = r.json()
        except Exception as exc:  # server down / not provisioned — a result, not a 500
            return {"ok": False, "url": url, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": True, "url": url, "card": card}

    @app.post("/api/run")
    async def run(request: Request):
        body = await request.json()
        message = (body.get("message") or "").strip() or DEFAULT_QUESTION
        via_bridge = bool(body.get("via_bridge"))
        # D28: which Agentforce tool the entry agent should use, echoed back
        # so the UI can badge the turn. Only meaningful on toggle scenarios.
        chosen_channel: str | None = None
        chosen_route: str | None = None
        chosen_topology: str | None = None

        scenario_name = body.get("scenario")
        if scenario_name:
            spec = load_scenarios().get(scenario_name)
            if not spec:
                raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario_name}'")
            if spec.get("status") != "live":
                raise HTTPException(
                    status_code=409, detail=f"scenario '{scenario_name}' is not live yet"
                )
            required_mode = spec.get("requires_mode")
            if required_mode and get_registry().mode != required_mode:
                # e.g. the AWS-hosted Agentforce→Claude variant: the bridge
                # only routes to the AgentCore runtime under A2ALAB_MODE=hosted.
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"scenario '{scenario_name}' needs A2ALAB_MODE={required_mode} "
                        f"(current: {get_registry().mode}) — set it in .env and restart "
                        "the stack"
                    ),
                )
            if spec.get("mode") == "fanout":
                # The orchestrator is not a target you can POST to. Its
                # `consult_business_units` tool is a CUSTOM tool, which on
                # Managed Agents means the HOST executes it — so running this
                # scenario means driving a managed session from here and
                # servicing the fan-out, exactly as scripts/run_fanout.py does.
                # Routing it through spec["target"] instead would send the
                # situation to the plain research agent, which has no such tool
                # and would answer the disruption itself: a brief that looks
                # right and consulted nobody.
                from orchestration.cma import (
                    CmaOrchestrator,
                    OrchestratorNotProvisioned,
                )

                # Which topology to run. "tool" keeps the host-side fan-out
                # (the control); "mcp" gives the model three remote MCP tools
                # and lets it schedule them (D41). The operator picks per run,
                # because the interesting comparison is same-agent-same-prompt.
                variant = "mcp" if body.get("variant") == "mcp" else "tool"
                trace_id = body.get("trace_id") or new_trace_id()
                try:
                    orch = CmaOrchestrator(variant=variant)
                except OrchestratorNotProvisioned as exc:
                    # Same class as the jira_sync gap: a console feature reaching
                    # for .a2alab/ state the hosted container doesn't carry. The
                    # orchestrator ids live in a file no container has; without
                    # the A2ALAB_FANOUT_ORCH_STATE env this host cannot run the
                    # fan-out. Answer with JSON, not a plaintext 500 the browser
                    # would fail to parse.
                    raise HTTPException(status_code=409, detail=str(exc))
                result = await orch.run(message, trace_id=trace_id)
                fan = result.get("fanout")
                if variant == "mcp":
                    # No host-side FanOutResult — the legs ran in the Lambda.
                    # What the MODEL did is the observable here, so report the
                    # call path instead of a coverage count we did not compute.
                    path = result["call_path"]
                    units = len({c.name for c in path.calls})
                    coverage = (
                        f"{units}/3 business units consulted · {path.render()}"
                        if path.calls
                        else "the orchestrator never consulted a business unit"
                    )
                elif fan is None:
                    # A real outcome, not an error: the model can decline to
                    # fan out, and hiding that would flatter the platform.
                    coverage = "the orchestrator never called consult_business_units"
                else:
                    coverage = f"{fan.ok_count}/{len(fan.results)} business units answered"
                return {
                    "ok": True,
                    "trace_id": result.get("trace_id", trace_id),
                    "text": f"{result.get('brief') or '(empty brief)'}\n\n---\n_{coverage}_",
                    "latency_ms": result.get("wall_ms"),
                    "variant": variant,
                }
            if spec.get("mode") == "async":
                # Fire-and-return: the research session runs for minutes in
                # the background; its hops stream into this turn's trace via
                # the client-minted trace_id. Any operator message beyond the
                # default question rides along as extra guidance.
                from briefs.runner import run_brief

                trace_id = body.get("trace_id") or new_trace_id()
                accounts = spec.get("account") or "Omega, Inc."
                extra = "" if message == DEFAULT_QUESTION else message

                async def _bg(trace_id=trace_id, accounts=accounts, extra=extra):
                    try:
                        result = await run_brief(accounts, trace_id, extra)
                        print(
                            f"[console] async brief done: {result['deliveries']} "
                            f"({result['elapsed_s']}s, trace {trace_id})",
                            flush=True,
                        )
                    except Exception as exc:
                        import traceback

                        traceback.print_exc()
                        from interop.trace import TraceEvent, get_recorder

                        rec = get_recorder()
                        rec.record(
                            TraceEvent(
                                trace_id=trace_id,
                                source="brief-worker",
                                target="brief-researcher",
                                protocol="managed-agents-api",
                                transport_detail="async brief run failed",
                                request_payload_raw={"accounts": accounts},
                                response_payload_raw={"error": f"{type(exc).__name__}: {exc}"},
                                status="error",
                                hop_seq=rec.next_hop_seq(trace_id),
                            )
                        )

                task = asyncio.create_task(_bg())
                background_runs.add(task)
                task.add_done_callback(background_runs.discard)
                return {
                    "ok": True,
                    "trace_id": trace_id,
                    "text": (
                        f"🛰️ **Async research started** for {accounts}.\n\n"
                        "This is the long-running pattern — the managed session is "
                        "researching news, competitors, government relations, and "
                        "geopolitics right now. Watch the call path below stream in "
                        "live (expect several minutes). When it finishes, the brief "
                        "lands in Salesforce: an A2ALab Account Brief record on the "
                        "account, a logged activity, and an in-app alert — all "
                        "credited to the Claude managed agent."
                    ),
                    "latency_ms": None,
                    "async": True,
                }
            name = spec["target"]
            via_bridge = bool(spec.get("via_bridge"))
            if spec.get("prompt_suffix"):
                message = f"{message}\n\n{spec['prompt_suffix']}"
            if spec.get("af_channel_toggle"):
                chosen_channel = body.get("af_channel") or "agent-api"
                if chosen_channel not in af_channel.CHANNEL_TOOLS:
                    chosen_channel = "agent-api"
                # The routing block rides AFTER the suffix; agent-api is the
                # tools' default bias, so only a2a-shim ever injects.
                if chosen_channel == "a2a-shim":
                    message += af_channel.routing_block("a2a-shim")
            if spec.get("af_route_toggle"):
                chosen_route = body.get("af_route") or "bridge"
                if chosen_route not in af_channel.ROUTE_TOOLS:
                    chosen_route = "bridge"
                # bridge is the twin script's default; only direct injects.
                if chosen_route == "direct":
                    message += af_channel.route_block("direct")
            if spec.get("af_topology_toggle"):
                # WS8 variant 3 (D61): the Agentforce orchestrator's fan-out
                # topology. delegated (default, works) vs serial (constraint
                # demo). delegated is the orchestrator script's default, so only
                # serial strictly needs the block — but injecting either makes
                # the chosen topology visible on the wire, and the UI badges it.
                chosen_topology = body.get("af_topology") or "delegated"
                if chosen_topology not in af_channel.TOPOLOGY_TOOLS:
                    chosen_topology = "delegated"
                message += af_channel.topology_block(chosen_topology)
        else:
            name = body.get("target")
        if not name:
            raise HTTPException(status_code=400, detail="missing 'target' or 'scenario'")
        try:
            get_registry().get(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        req = AgentRequest(
            message=message,
            # Client-minted trace_id so the UI can select the trace and watch
            # hops stream in while the run is still in flight.
            trace_id=body.get("trace_id") or new_trace_id(),
            session_id=body.get("session_id") or None,
        )
        # WS6 U1/U2: a signed-in operator's identity rides the origin
        # request on both channels — user_context (display/log, visible in
        # wire records) and user_token (the JWT, the verifiable channel the
        # seams forward; the F2 redactor keeps it out of the traces).
        lab_user = request.scope.get("state", {}).get("lab_user")
        if lab_user:
            from interop import identity

            req.metadata["user_context"] = identity.user_context(lab_user)
            authz = request.headers.get("authorization", "")
            if authz.startswith("Bearer "):
                req.metadata["user_token"] = authz[len("Bearer ") :]
        try:
            if via_bridge:
                result = await run_via_bridge(req, name)
                if chosen_channel:
                    result["af_channel"] = chosen_channel
                if chosen_route:
                    result["af_route"] = chosen_route
                if chosen_topology:
                    result["af_topology"] = chosen_topology
                return result
            client = get_client(name)
            resp = await client.ask(req)
            return {
                "ok": True,
                "trace_id": req.trace_id,
                "text": resp.text,
                "latency_ms": resp.latency_ms,
                "session_id": resp.session_id,
                "af_channel": chosen_channel,
                "af_route": chosen_route,
                "af_topology": chosen_topology,
            }
        except Exception as exc:  # surface the failure as a result, not a 500
            return {
                "ok": False,
                "trace_id": req.trace_id,
                "error": f"{type(exc).__name__}: {exc}",
            }

    @app.delete("/api/traces")
    async def clear_traces():
        """Cockpit cleanup: delete the trace JSONL files. Traces live on disk
        (traces/YYYY-MM-DD.jsonl, raw wire payloads per hop) — this removes
        the files; new runs start fresh ones."""
        trace_dir = _trace_dir()
        removed = 0
        if trace_dir.exists():
            for path in trace_dir.glob("*.jsonl"):
                path.unlink()
                removed += 1
        return {"ok": True, "removed": removed}

    @app.get("/api/traces")
    async def traces():
        # Thread: the Aurora read is blocking boto3 (and retries while a
        # scale-to-zero cluster resumes) — keep the event loop free for SSE.
        events = await asyncio.to_thread(_merged_events)
        grouped: dict[str, list[dict]] = {}
        for ev in events:
            grouped.setdefault(ev.get("trace_id", "unknown"), []).append(ev)
        out = []
        for trace_id, evs in grouped.items():
            evs.sort(key=lambda e: (e.get("ts", 0), e.get("hop_seq", 0)))
            out.append(
                {
                    "trace_id": trace_id,
                    "started": evs[0].get("ts"),
                    "hops": evs,
                    "protocols": sorted({e.get("protocol", "?") for e in evs}),
                }
            )
        out.sort(key=lambda t: t["started"] or 0, reverse=True)
        return {"traces": out}

    @app.get("/api/traces/{trace_id}")
    async def trace_by_id(trace_id: str):
        # Windowless single-trace lookup: the linked-trace click from an
        # Observations row (spanning days) must resolve even when the trace
        # predates /api/traces' recent Aurora window. Blocking store read off
        # the event loop, like /api/traces.
        trace = await asyncio.to_thread(_trace_by_id, trace_id)
        if trace is None:
            raise HTTPException(status_code=404, detail="trace not found")
        return {"trace": trace}

    @app.get("/api/stream")
    async def stream():
        """SSE live tail: watch the trace dir and push new lines as they land."""

        async def gen():
            # Track per-file byte offsets, starting at current EOF. All I/O
            # is binary so offsets stay byte-accurate with multibyte payloads,
            # and only complete lines (ending in \n) are consumed — a record
            # mid-append waits for the next poll instead of being emitted as
            # a truncated JSON fragment.
            offsets: dict[Path, int] = {}
            trace_dir = _trace_dir()
            if trace_dir.exists():
                for path in trace_dir.glob("*.jsonl"):
                    offsets[path] = path.stat().st_size
            yield "event: hello\ndata: {}\n\n"
            # A quiet lab emits nothing, and every intermediary reads silence as
            # a dead connection — the ALB the console moves behind (WS13) idles
            # at 120s, proxies commonly at 30s. The browser's EventSource
            # reconnects, but `offsets` is rebuilt from current EOF on the new
            # generator, so hops that landed during the gap are skipped and the
            # tail lies by omission. A comment line is the SSE no-op that keeps
            # the connection warm.
            last_emit = time.monotonic()
            while True:
                await asyncio.sleep(0.5)
                if time.monotonic() - last_emit >= SSE_KEEPALIVE_S:
                    last_emit = time.monotonic()
                    yield ": keepalive\n\n"
                trace_dir = _trace_dir()
                if not trace_dir.exists():
                    continue
                for path in sorted(trace_dir.glob("*.jsonl")):
                    prev = offsets.get(path, 0)
                    size = path.stat().st_size
                    if size < prev:
                        # File shrank — cleared via DELETE /api/traces and
                        # recreated. Restart from the top or new hops would
                        # be silently skipped until it regrew past the old
                        # offset.
                        prev = 0
                        offsets[path] = 0
                    if size <= prev:
                        continue
                    with path.open("rb") as f:
                        f.seek(prev)
                        chunk = f.read()
                    last_newline = chunk.rfind(b"\n")
                    if last_newline == -1:
                        continue  # partial line — pick it up next poll
                    offsets[path] = prev + last_newline + 1
                    for event in _parse_lines(chunk[: last_newline + 1]):
                        last_emit = time.monotonic()
                        yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    # ---- Observability (M11.3): each platform's interior view -------------
    # Reads only the local obs store (harvest-and-cache, D18) — the console
    # never proxies platform APIs live. POST /api/obs/harvest triggers the
    # same pull as scripts/obs_harvest.py.

    def _obs_store():
        """Aurora when it is configured; sqlite only as the local fallback (D49).

        This used to return `ObsStore()` unconditionally — sqlite, always. The
        hosted harvest Lambda writes Aurora and the local `obs_harvest.py`
        writes `traces/lab.db`, so the console rendered the local copy while
        the authoritative one filled up unseen. Nothing failed; the numbers
        were just quietly the wrong ones, and a container (no lab.db at all)
        showed an empty section.

        `A2ALAB_OBS_STORE=sqlite` still forces the local store, for working on
        a harvested snapshot offline.
        """
        from observability import make_obs_store

        return make_obs_store()

    # The honest capability matrix (plan/05-observability.md) — rendered
    # live in the coverage panel next to what was actually harvested.
    # WS9. The obs store's platform key for coding-agent telemetry — filtered
    # out of the Observability coverage panel and rendered in its own section.
    BUILD_TELEMETRY_PLATFORM = "coding"
    # WS16. The sibling behavioural-logs signal (edit-acceptance, tool mix,
    # latency, reliability, prompt cadence). Same section, same filtering: it
    # is not an agent platform, so it never appears as a coverage column.
    BUILD_LOGS_PLATFORM = "coding-logs"

    # The rolling window the briefs tab shows. Deliberately a window and not a
    # count: an empty week must LOOK empty. The analyst was paused for eleven
    # days and a "latest brief" view hid that behind a stale document (D56).
    BRIEF_WINDOW_DAYS = 7

    # Set by deploy/console/deploy_console.sh to the scheduled harvest Lambda.
    # Its presence is what makes the Harvest button asynchronous — unset on a
    # laptop, where the sweep runs in-process because nothing is timing it out.
    HARVEST_FUNCTION_ENV = "A2ALAB_HARVEST_FUNCTION"
    DEFAULT_HARVEST_FUNCTION = "a2alab-obs-harvest"

    # One string, two consumers (the telemetry payload and the cost sentinel's
    # brief panel). It is the caveat that has to travel with every rendering of
    # the number, so a second copy is a second thing that can drift out of it.
    BUILD_TELEMETRY_COST_NOTE = (
        "Modelled build cost at LIST PRICE — a client-side estimate the "
        "coding agent computes from token counts, not an invoice. On "
        "subscription or credit plans it is not money that changed hands."
    )

    # Per-tool metric coverage. The two coding agents do NOT emit the same
    # shapes, and a table that renders $0.00 for a tool with no cost metric is
    # a lie by omission — so the section states the coverage instead of
    # implying a zero. Observed live 2026-07-26 against CloudWatch.
    BUILD_TELEMETRY_TOOL_NOTES = {
        "claude-code": {
            "label": "Claude Code",
            "cost": True,
            "tokens": True,
            "detail": (
                "Eight documented metrics, all delta Sums: cost.usage in USD, "
                "token.usage by type, session.count, active_time.total, "
                "lines_of_code/commit/pull_request counts and "
                "code_edit_tool.decision. Attribution rides "
                "OTEL_RESOURCE_ATTRIBUTES from .claude/settings.local.json, so "
                "project and repo are set per checkout."
            ),
        },
        "codex": {
            "label": "Codex CLI",
            "cost": False,
            "tokens": False,
            "detail": (
                "Live since 2026-07-26 and correctly attributed "
                "(tool=codex, project, repo), but it does NOT mirror Claude "
                "Code's schema and the gaps are structural, not cosmetic. "
                "There is NO cost metric at all — cross-tool cost has to be "
                "modelled from tokens and a price table. And "
                "codex.turn.token_usage is a delta HISTOGRAM dimensioned by "
                "token_type, where Claude Code's token.usage is a delta SUM "
                "dimensioned by type; sum_over_time returns the series but no "
                "scalar for a histogram on this surface, so tokens are not "
                "wired up yet. What IS read today are the Sums: "
                "codex.thread.started (sessions) and "
                "codex.conversation.turn.count (turns). Attribution comes from "
                "scripts/codex_otel.sh at launch, because Codex ignores otel "
                "in project-local .codex/config.toml."
            ),
        },
        "cursor": {
            "label": "Cursor",
            "cost": False,
            "tokens": False,
            "detail": (
                "The only one of the three with NO native OTel exporter. Cursor "
                "exposes lifecycle hooks, not metrics; the lab's checked-in "
                ".cursor/hooks.json forwards them to the cursorscope ingestor, "
                "which exports to the same CloudWatch metrics endpoint (set up by "
                "scripts/cursor_otel.sh — build-notes/cursor/01). Two consequences "
                "for the numbers. Its metrics are CUMULATIVE counters "
                "(cursor_hook_events_total — the lowest common denominator, one "
                "point per lifecycle hook — plus cursor_session_total, "
                "cursor_prompt_total, cursor_tool_executions_total once a real "
                "Agent session emits them), not the delta Sums the two native "
                "exporters emit, so the harvest differences them with increase() "
                "rather than summing them. And there is NO cost metric and no "
                "consumable token Sum — cursorscope's token figures are gen_ai.* "
                "histograms this surface returns no scalar for, the same shape as "
                "Codex's token histogram — so Cursor is read for SESSIONS only. "
                "Its attribution is also NOT symmetrical (this bit on 2026-07-31): "
                "cursorscope's Node SDK carries NO @resource.tool / repo / project, "
                "only the service.* / deployment.environment keys it builds its "
                "resource block from, so the harvest resolves tool from "
                "@resource.service.name, repo from @resource.deployment.environment "
                "and project from @resource.service.namespace — fallbacks that fire "
                "only for Cursor, since both native exporters carry the primary "
                "labels."
            ),
        },
        "kiro": {
            "label": "Kiro",
            "cost": False,
            "tokens": False,
            "detail": (
                "Amazon Kiro — no native OTel exporter, same situation as Cursor. "
                "The lab's .kiro/hooks/otel-forward.json fires on 8 triggers "
                "(SessionStart, Stop, PostToolUse, PostFileSave/Create/Delete, "
                "UserPromptSubmit, PostTaskExec) and forward.sh emits OTLP JSON "
                "directly to CloudWatch via curl — no ingestor process needed "
                "(set up by scripts/kiro_otel.sh — build-notes/kiro/02). "
                "All metrics are CUMULATIVE monotonic Sums (kiro_session_total, "
                "kiro_prompt_total, kiro_tool_executions_total, kiro_file_saves_total, "
                "kiro_file_creates_total, kiro_file_deletes_total, "
                "kiro_task_executions_total, kiro_hook_events_total), queried with "
                "increase(). There is NO cost metric and NO token/model visibility — "
                "the hook contract exposes only the trigger name, not model or token "
                "counts (Probe 2 pending). Kiro's unique advantage is first-class "
                "file-operation triggers (PostFileSave/Create/Delete), which map "
                "directly onto the WS16 edit-acceptance signal that Cursor and "
                "Codex cannot provide."
            ),
        },
    }

    # Cost-tab Details pane: cross-tool comparison (D64, WS9). Rendered with
    # mdToHtml so D-chips and build-notes paths linkify. Kept here rather than
    # duplicated in index.html — same pattern as BUILD_TELEMETRY_COST_NOTE.
    BUILD_TELEMETRY_COMPARISON_MD = """\
### What each coding agent publishes

Three tools reach the same CloudWatch managed OTLP endpoint; they do **not**
publish the same metric shapes. The Run tab shows **n/a** (not `$0.00`) where a
tool has no cost or token metric — zero would read as "free"; n/a reads as "we
cannot see it." Full write-up: build-notes/cursor/02-cross-tool-cost-comparison.md.

| | Claude Code | Codex CLI | Cursor | Kiro |
|---|---|---|---|---|
| OTEL | Native exporter + `otelHeadersHelper` | Native exporter | **None** — hooks → cursorscope (D64) | **None** — hooks → direct OTLP curl |
| Cost metric | `claude_code.cost.usage` (USD estimate) | **None** | **None** | **None** |
| Token metric | `token.usage` — delta **Sum**, 4 buckets | `turn.token_usage` — delta **Histogram** | `gen_ai.*` — **Histogram** (hook estimate) | **None** — hook payload has no token data |
| Sessions | `session.count` (delta Sum) | `thread.started` (Sum) | `session_total` (cumulative → `increase()`) | `session_total` (cumulative → `increase()`) |
| File ops (WS16) | ❌ | ❌ | ❌ | ✅ `file_saves/creates/deletes_total` |
| Run tab | Cost + tokens + sessions | Sessions/turns only | Sessions/activity only | Sessions/activity + file ops |

### Why only Claude Code gets dollars

Claude Code publishes (1) a client-side USD estimate and (2) token counts as
delta **Sums** in four buckets (uncached input, cache read, cache creation,
output) that bill at different multiples. `coding_source.py` rolls those up with
`sum_over_time()`. The figure is **modelled build cost at list price** — not an
invoice (D44, build-notes/claude/10-consumption-and-list-price.md).

### Why Codex and Cursor show n/a for cost

**Codex** ships a native exporter but no cost metric. Its token data is a
**Histogram**; CloudWatch PromQL returns the series but no scalar this harvest
can sum — verified live 2026-07-26 (build-notes/claude/08-coding-agent-telemetry.md).
Only the Sums `codex.thread.started` and `codex.conversation.turn.count` are wired.

**Cursor** is one step further: no native exporter at all. cursorscope derives
**behaviour counters** from lifecycle hooks (build-notes/cursor/01) — sessions,
prompts, tool calls — not billing telemetry. Token estimates, when present, are
histograms with no cache-bucket split. Cursor Pro/Business subscription pricing
is also a different contract than list-price-per-token. D64 documents the
service.* attribution fallbacks and cumulative-counter handling.

### What would be needed to estimate Codex or Cursor cost

Not impossible — not implemented. Would require: token counts in a summable form
(new histogram query logic or Sum exporters), a list-price table, model labels on
every datapoint, and honest **modelled** labelling. Until then the cost sentinel
and console never present a combined dollar total across tools (WS12/D44).
"""

    # Shown in the Coding Agents Telemetry section when nothing has been collected yet,
    # which is the honest default: telemetry is NOT retroactive, so whatever
    # was built before the exporters were switched on is unmeasurable.
    BUILD_TELEMETRY_SETUP = [
        {
            "step": "Create the ingest identity and API key",
            "detail": (
                "An IAM user with the CloudWatchAPIKeyAccess managed policy, then "
                "`aws iam create-service-specific-credential --service-name "
                "cloudwatch.amazonaws.com --credential-age-days 90`. Done for this "
                "lab as a2alab-cw-metrics-otlp; the key lives in the Secrets Manager "
                "secret a2alab/telemetry/cw-metrics-api-key and expires 2026-10-24."
            ),
        },
        {
            "step": "Fetch the token at runtime, never store it",
            "detail": (
                "scripts/otel_headers.sh reads the secret with the developer's "
                "existing AWS session and prints the Authorization header; Claude "
                "Code calls it via otelHeadersHelper and refreshes every ~29 min. "
                "D39 applied to the laptop — a bearer token pasted into a config "
                "file is the long-lived credential that rule exists to remove."
            ),
        },
        {
            "step": "Pin the AWS profile in the helper — this one bit us",
            "detail": (
                "The helper runs in Claude Code's environment, not your shell, so "
                "AWS_PROFILE is usually unset and the CLI falls back to the DEFAULT "
                "profile — a different account, which cannot read the secret. The "
                "helper then returns {} because a missing token is designed to "
                "degrade to 'no telemetry' rather than break your session. Result: "
                "days of zero metrics with every config file correct and nothing "
                "logged. It now reads AWS_PROFILE from the repo .env and reports "
                "the resolved account on stderr when the fetch fails."
            ),
        },
        {
            "step": "Point Claude Code at the metrics endpoint",
            "detail": (
                "CLAUDE_CODE_ENABLE_TELEMETRY=1, OTEL_METRICS_EXPORTER=otlp, "
                "OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf, and "
                "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT="
                "https://monitoring.<region>.amazonaws.com/v1/metrics — note the "
                "/v1/metrics PATH and the protobuf protocol, both required. Add "
                "OTEL_RESOURCE_ATTRIBUTES with user.id/user.email/team.id, which is "
                "the shape Coding Agent Insights groups by."
            ),
        },
        {
            "step": "Metrics only — the token cannot carry logs or traces",
            "detail": (
                "A CloudWatch metrics bearer token works ONLY against the OTLP "
                "metrics endpoint. It cannot call the logs or traces endpoints, nor "
                "any query API. So OTEL_LOGS_EXPORTER=otlp against the same endpoint "
                "silently gets you nothing; per-event logs need the Logs endpoint "
                "and its own separate key."
            ),
        },
        {
            "step": "Attribute the work: project and repo are NOT built in",
            "detail": (
                "Neither tool emits any attribute naming the project, working "
                "directory or git repository — Claude Code's metrics carry session, "
                "app, user, terminal and per-metric labels and nothing about WHERE "
                "the work happened. OTEL_RESOURCE_ATTRIBUTES is the only hook, and "
                "it flattens to @resource.<key> as a queryable label. Because "
                ".claude/settings.local.json is per-repo, setting "
                "project=…,repo=owner/name there gives per-project cost for free; "
                "scripts/codex_otel.sh derives the same pair from git. Values "
                "cannot contain spaces."
            ),
        },
        {
            "step": "Point Codex at the same endpoint — NOT YET VERIFIED",
            "detail": (
                "The Codex CLI ships its own OpenTelemetry exporter, configured in "
                "~/.codex/config.toml (project-local .codex/config.toml explicitly "
                "IGNORES otel, so this cannot be made per-repo the way Claude Code "
                "can). Codex has THREE separate exporters, not one: otel.exporter "
                "is logs/events (default none), otel.trace_exporter is traces "
                "(default none), and otel.metrics_exporter is metrics — and it "
                "defaults to statsig, not OTLP. The wrapper still sets tool=codex "
                "in the resource attributes — that one label is the whole 'two "
                "team members, two coding tools' comparison, once anything "
                "arrives. Measured 2026-07-26: a real Codex "
                "session run through scripts/codex_otel.sh produced ZERO codex.* "
                "datapoints, because this lab's [otel] block set `exporter` (logs) "
                "to the CloudWatch METRICS endpoint with a metrics-only bearer "
                "token and never set metrics_exporter at all. Signal, endpoint and "
                "credential must agree; see the Codex config reference for the "
                "exact metrics_exporter syntax before retrying. Second asymmetry: "
                "Codex has no headers-helper hook, so its token is ${VAR} "
                "interpolation resolved ONCE at launch (hence the wrapper) while "
                "Claude Code re-fetches every ~29 min."
            ),
        },
        {
            "step": "Harvest",
            "detail": (
                "uv run python scripts/obs_harvest.py coding — or wait for the "
                "6-hourly harvest Lambda. Namespaces are discovered, not hardcoded."
            ),
        },
    ]

    # WS16 behavioural-logs empty state. Distinct from the metrics setup above
    # because the signal, endpoint, credential and read path all differ — and
    # because behavioural logs are OPT-IN (a launch wrapper), so "nothing here
    # yet" is usually "launched without the wrapper", not "nothing provisioned".
    BUILD_BEHAVIOUR_SETUP = [
        {
            "step": "Provision the logs ingest path (once)",
            "detail": (
                "uv run python scripts/setup_cw_logs_otlp.py --apply. Logs do NOT "
                "share the metrics credential: proven 2026-07-30, the metrics bearer "
                "token 403s against logs.<region>/v1/logs because a service-specific "
                "credential is scoped to one service (cloudwatch vs logs). The script "
                "mints a logs.amazonaws.com credential, creates the log group with "
                "bearer auth enabled, and stores the token in Secrets Manager (D39, "
                "never on disk)."
            ),
        },
        {
            "step": "Launch Claude Code with the wrapper",
            "detail": (
                "scripts/claude_otel.sh — behavioural logs are opt-in, so a plain "
                "`claude` exports metrics only. The wrapper fetches the logs token "
                "with your AWS session, sets OTEL_LOGS_EXPORTER=otlp and the "
                "signal-specific OTEL_EXPORTER_OTLP_LOGS_HEADERS (carrying the token "
                "AND the two undocumented x-aws-log-group / x-aws-log-stream headers "
                "the endpoint requires), and creates the log stream the provisioner "
                "leaves uncreated. Metrics keep their own token via otelHeadersHelper."
            ),
        },
        {
            "step": "Content flags stay OFF — deliberately",
            "detail": (
                "The wrapper sets no OTEL_LOG_USER_PROMPTS / OTEL_LOG_TOOL_* flag, so "
                "prompts, file contents and tool arguments are never emitted. Every "
                "insight is computed from metadata that ships regardless — decision, "
                "tool_name, duration_ms, status_code, prompt_length — so the "
                "dashboard is complete with nothing sensitive leaving the laptop "
                "(D59)."
            ),
        },
        {
            "step": "Harvest",
            "detail": (
                "uv run python scripts/obs_harvest.py coding-logs — or the Harvest "
                "button in this section, or wait for the harvest Lambda. Read-back is "
                "SigV4 FilterLogEvents over the log group (boto3 signs it natively), "
                "a different path from the metrics' PromQL. Telemetry is not "
                "retroactive: only turns run after the wrapper was first used appear."
            ),
        },
    ]

    OBS_CAPABILITIES = {
        "claude": {
            "label": "Claude Managed Agents",
            "can": [
                "list sessions (paginated)",
                "full per-session event history",
                "thinking + tool events",
                "token usage per model request",
            ],
            "cannot": [
                "time-range session filter",
                "org-wide usage/cost API",
                "events outlive session deletion",
            ],
        },
        "salesforce": {
            "label": "Salesforce Agentforce",
            "can": [
                "SQL over sessions/interactions/steps (STDM DMOs)",
                "Einstein GenAI gateway prompt/response logs",
                "OTel per-session export (72h, beta)",
            ],
            "cannot": [
                "anything until Data Cloud Session Tracing is enabled",
                "dashboards API (Agent Analytics is UI-only)",
            ],
        },
        "openai": {
            "label": "OpenAI",
            "can": [
                "org usage/cost metrics (admin key)",
                "fetch stored responses by known id (30-day TTL)",
            ],
            "cannot": [
                "read/list traces (dashboard is ingestion-only)",
                "list responses — ids must be captured at emit time",
            ],
        },
        "foundry": {
            "label": "Microsoft Foundry",
            "can": [
                "agent-semantic OTel spans via App Insights KQL (invoke_agent / chat / execute_tool)",
                "token usage per model call (gen_ai.usage.*) + full input/output messages",
                "the platform's OWN record of its A2A tool calls (execute_tool spans, durations)",
                "response id = the lab's platform_ref — turns join to wire traces out of the box",
            ],
            "cannot": [
                "raw wire bytes of the A2A tool call (span metadata only — the shim's wiretap has those)",
                "~2-4 min ingestion lag (App Insights pipeline)",
            ],
        },
        "adk": {
            "label": "Google ADK / Agent Engine",
            "can": [
                "Cloud Logging entries per engine (queryable, filterable)",
                "request + container app logs in near-real-time",
                "Cloud Monitoring: request counts/latencies per engine",
                "token counts per model (Vertex publisher metrics)",
                "the billing meters themselves (vCPU-s / GiB-s allocated) → est. cost",
                "Cloud Trace spans (OTel — not yet harvested)",
            ],
            "cannot": [
                "session/turn read API (preview A2A surface)",
                "A2A contextIds in default logs",
                "agent-semantic events (tool calls) without custom instrumentation",
                "token metrics per engine (project+model granularity only)",
            ],
        },
        "strands": {
            "label": "AWS Strands (Bedrock AgentCore)",
            "can": [
                "Bedrock model meters (AWS/Bedrock): input/output tokens, invocations, latency",
                "token counts → est. cost (Strands is this account's only Bedrock agent, so ModelId attributes cleanly)",
                "AgentCore runtime access log: invocation + error (5xx) counts",
            ],
            "cannot": [
                "session/turn read API (the Strands SDK exposes none)",
                "agent-semantic events (tool calls) without custom instrumentation",
                "token metrics per runtime (Bedrock meters are per-ModelId, account-wide)",
                "join to wire traces — platform_ref (Bedrock request-id) is null at this SDK version",
            ],
        },
    }

    @app.get("/api/obs/summary")
    async def obs_summary():
        store = _obs_store()
        try:
            data = store.summary()
        finally:
            store.close()
        # WS9: coding-agent telemetry shares the store but is NOT a platform
        # column. The coverage panel's honesty depends on its five columns
        # being the same kind of thing — each an agent platform whose interior
        # logs the lab harvests. Claude Code is the tool that BUILT the lab;
        # listing it beside Agentforce would quietly imply otherwise.
        data["platforms"].pop(BUILD_TELEMETRY_PLATFORM, None)
        data["platforms"].pop(BUILD_LOGS_PLATFORM, None)
        data["capabilities"] = OBS_CAPABILITIES
        return data

    @app.get("/api/build-telemetry")
    async def build_telemetry():
        """What the lab cost to build, per coding tool per day (WS9).

        Its own section rather than a sixth Observability column — see the
        note in obs_summary. Returns the setup steps too, because until the
        exporters are switched on there is nothing to show and the useful
        answer is "here is how to start collecting".
        """
        store = _obs_store()
        try:
            sessions = store.list_sessions(BUILD_TELEMETRY_PLATFORM, include_raw=True)
            summary = store.summary()
        finally:
            store.close()

        by_tool: dict[str, dict] = {}
        by_repo: dict[str, dict] = {}
        by_model: dict[tuple[str, str], dict] = {}
        days: list[dict] = []
        totals = {
            "cost_usd": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "cache_creation_tokens": 0,
            "sessions": 0,
        }
        for row in sessions:
            try:
                usage = json.loads(row.get("usage_json") or "{}")
                raw = json.loads(row.get("raw_json") or "{}")
            except (ValueError, TypeError):
                usage, raw = {}, {}
            tool = raw.get("tool") or str(row.get("native_id", "")).split(":")[0]
            cost = float(usage.get("cost_usd_estimated") or 0)
            # Four buckets, not two. `input_tokens` is the UNCACHED remainder —
            # reporting it as "input" understates a cache-heavy agent session by
            # an order of magnitude, and the three input buckets bill at
            # different multiples (~1x uncached, 1.25x/2x on write, ~0.1x on
            # read), so they can never be summed into one number and priced.
            # The harvest has stored all four since WS9; this endpoint dropped
            # two of them on the floor.
            tin = int(usage.get("input_tokens") or 0)
            tout = int(usage.get("output_tokens") or 0)
            tcr = int(usage.get("cache_read_input_tokens") or 0)
            tcc = int(usage.get("cache_creation_input_tokens") or 0)
            bucket = by_tool.setdefault(
                tool,
                {
                    "tool": tool,
                    "cost_usd": 0.0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "days": 0,
                    # Per-tool ACTIVITY, for the segmented tiles. Codex and Cursor
                    # publish no cost or token metric (both n/a), but they DO
                    # publish activity counters — sessions/turns for Codex,
                    # sessions/prompts/tool-calls for Cursor — which the harvest
                    # already stored in raw["metrics"] and raw["sessions"]. A
                    # tile group per tool needs those numbers here, summed across
                    # the tool's days, rather than only in the by-day table.
                    "sessions": 0,
                    "metrics": {},
                },
            )
            note = BUILD_TELEMETRY_TOOL_NOTES.get(tool) or {}
            bucket["cost_supported"] = bool(note.get("cost", True))
            bucket["tokens_supported"] = bool(note.get("tokens", True))
            bucket["cost_usd"] += cost
            bucket["input_tokens"] += tin
            bucket["output_tokens"] += tout
            bucket["cache_read_tokens"] += tcr
            bucket["cache_creation_tokens"] += tcc
            bucket["days"] += 1
            bucket["sessions"] += int(raw.get("sessions") or 0)
            # raw["metrics"] is {full_metric_name: summed_value} for this
            # tool-day — codex.conversation.turn.count, cursor_prompt_total, etc.
            # Sum them by name so a per-tool tile can pick the one it wants.
            for _mname, _mval in (raw.get("metrics") or {}).items():
                bucket["metrics"][_mname] = bucket["metrics"].get(_mname, 0) + float(_mval or 0)
            totals["cost_usd"] += cost
            totals["input_tokens"] += tin
            totals["output_tokens"] += tout
            totals["cache_read_tokens"] += tcr
            totals["cache_creation_tokens"] += tcc
            totals["sessions"] += int(raw.get("sessions") or 0)

            # Per-repository roll-up. This is the "what did each codebase
            # cost" view: the resource attributes were always on the wire, and
            # the harvest now keeps them instead of stripping them.
            for repo_name, rb in (raw.get("by_repo") or {}).items():
                rtok = rb.get("tokens") or {}
                agg = by_repo.setdefault(
                    repo_name,
                    {
                        "repo": repo_name,
                        "project": rb.get("project") or repo_name,
                        "cost_usd": 0.0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "cache_read_tokens": 0,
                        "cache_creation_tokens": 0,
                        "sessions": 0,
                        "active_time_s": 0.0,
                        "tools": {},
                        "days": set(),
                    },
                )
                if agg["project"] in (None, "", "unattributed"):
                    agg["project"] = rb.get("project") or repo_name
                rcost = float(rb.get("cost_usd") or 0)
                agg["cost_usd"] += rcost
                agg["input_tokens"] += int(rtok.get("input") or 0)
                agg["output_tokens"] += int(rtok.get("output") or 0)
                # `by_repo` keeps the raw OTel `type` label, so these are
                # cacheRead/cacheCreation here and snake_case on the session
                # usage above. Same numbers, two spellings, one API shape out.
                agg["cache_read_tokens"] += int(rtok.get("cacheRead") or 0)
                agg["cache_creation_tokens"] += int(rtok.get("cacheCreation") or 0)
                agg["sessions"] += int(rb.get("sessions") or 0)
                agg["active_time_s"] += float(rb.get("active_time_s") or 0)
                agg["days"].add(str(row.get("created_at") or "")[:10])
                tb = agg["tools"].setdefault(tool, {"tool": tool, "cost_usd": 0.0, "sessions": 0})
                tb["cost_usd"] += rcost
                tb["sessions"] += int(rb.get("sessions") or 0)

            # Which MODEL did the work. Both tools label every datapoint with
            # it (verified live 2026-07-27), so this is the one dimension that
            # is genuinely comparable across them — even though the units are
            # not: Claude Code can say what each model cost, Codex can only say
            # how many sessions and turns each model ran.
            for model_name, mb in (raw.get("by_model") or {}).items():
                mkey = (tool, model_name)
                magg = by_model.setdefault(
                    mkey,
                    {
                        "tool": tool,
                        "model": model_name,
                        "cost_usd": 0.0,
                        "tokens": 0,
                        "sessions": 0,
                        "days": 0,
                    },
                )
                magg["cost_usd"] += float(mb.get("cost_usd") or 0)
                magg["tokens"] += int(mb.get("tokens") or 0)
                magg["sessions"] += int(mb.get("sessions") or 0)
                magg["days"] += 1

            days.append(
                {
                    "id": row.get("native_id"),
                    "tool": tool,
                    "date": str(row.get("created_at") or "")[:10],
                    "cost_usd": round(cost, 4),
                    "input_tokens": tin,
                    "output_tokens": tout,
                    "cache_read_tokens": tcr,
                    "cache_creation_tokens": tcc,
                    "sessions": raw.get("sessions"),
                    "active_time_s": raw.get("active_time_s"),
                    "by_model": raw.get("by_model") or {},
                    "cost_supported": bool(
                        (BUILD_TELEMETRY_TOOL_NOTES.get(tool) or {}).get("cost", True)
                    ),
                    "tokens_supported": bool(
                        (BUILD_TELEMETRY_TOOL_NOTES.get(tool) or {}).get("tokens", True)
                    ),
                }
            )
        days.sort(key=lambda d: (d["date"], d["tool"]), reverse=True)
        totals["cost_usd"] = round(totals["cost_usd"], 4)
        for bucket in by_tool.values():
            bucket["cost_usd"] = round(bucket["cost_usd"], 4)
            # Friendly, named activity for the per-tool tiles, so the frontend
            # picks fields by meaning rather than hardcoding raw OTel names it
            # would then drift from. Sessions is universal; the rest are the
            # honest counters each tool actually publishes (Codex turns; Cursor
            # prompts and tool executions) and are absent → None when a tool
            # does not emit them, which the tiles render as an explained blank
            # rather than a misleading zero.
            m = bucket.get("metrics") or {}
            _mi = lambda name: int(m[name]) if name in m else None  # noqa: E731,B023
            bucket["activity"] = {
                "sessions": bucket.get("sessions") or 0,
                "turns": _mi("codex.conversation.turn.count"),
                "prompts": _mi("cursor_prompt_total"),
                "tool_calls": _mi("cursor_tool_executions_total"),
                "hook_events": _mi("cursor_hook_events_total"),
                # Kiro-specific: prompts, tool calls, and file operations (WS16).
                "kiro_prompts": _mi("kiro_prompt_total"),
                "kiro_tool_calls": _mi("kiro_tool_executions_total"),
                "kiro_file_saves": _mi("kiro_file_saves_total"),
                "kiro_file_creates": _mi("kiro_file_creates_total"),
                "kiro_file_deletes": _mi("kiro_file_deletes_total"),
                "kiro_tasks": _mi("kiro_task_executions_total"),
            }

        # The unattributed bucket earns its row only when it carries something
        # measurable. It exists so the per-repo view cannot silently disagree
        # with the total — an unattributed COST is still a cost — but a bar
        # reading $0.00 with no tokens satisfies nothing and reads as a broken
        # repo. Session counts alone (Codex publishes no cost or token metric)
        # go in the footnote below instead. Never dropped from `totals`.
        hidden = by_repo.get("unattributed")
        if (
            hidden
            and not hidden["cost_usd"]
            and not hidden["input_tokens"]
            and not hidden["output_tokens"]
            and not hidden["cache_read_tokens"]
            and not hidden["cache_creation_tokens"]
        ):
            by_repo.pop("unattributed")
        else:
            hidden = None

        repos = []
        for agg in by_repo.values():
            agg["cost_usd"] = round(agg["cost_usd"], 4)
            agg["days"] = len(agg["days"])
            agg["tools"] = sorted(
                ({**t, "cost_usd": round(t["cost_usd"], 4)} for t in agg["tools"].values()),
                key=lambda t: -t["cost_usd"],
            )
            # Share of the measured total, so "this project vs the ones
            # supporting it" is readable without doing the arithmetic.
            agg["cost_share"] = (
                round(agg["cost_usd"] / totals["cost_usd"], 4) if totals["cost_usd"] else 0.0
            )
            repos.append(agg)
        repos.sort(key=lambda r: (-r["cost_usd"], r["repo"]))

        harvest = (summary.get("platforms", {}).get(BUILD_TELEMETRY_PLATFORM) or {}).get("harvest")
        return {
            "enabled": bool(days),
            "harvest": harvest,
            "totals": totals,
            "by_tool": sorted(by_tool.values(), key=lambda b: -b["cost_usd"]),
            "by_model": sorted(
                ({**m, "cost_usd": round(m["cost_usd"], 4)} for m in by_model.values()),
                key=lambda m: (-m["cost_usd"], -m["sessions"], m["model"]),
            ),
            "model_note": (
                "`model` is a datapoint label on both tools' metrics — nothing "
                "had to be configured for it, unlike project and repo. The "
                "units differ, though: Claude Code labels its cost and token "
                "metrics, so cost per model is exact; Codex publishes no cost "
                "metric, so its models are counted in sessions and turns only."
            ),
            # Why the tiles show four numbers where every other cost dashboard
            # shows two. This is the section's transferable finding, so it is
            # stated in the UI rather than left in a build note.
            "token_note": (
                "Four buckets, not two. `input` is the UNCACHED remainder — the "
                "prompt actually sent is input + cache read + cache creation, so "
                "reporting `input` alone understates a long agent session by an "
                "order of magnitude. They also do not bill alike: a cache read "
                "costs roughly a tenth of uncached input and a cache write costs "
                "1.25x (5-minute TTL) or 2x (1-hour), which is why they cannot be "
                "summed into one 'tokens' figure and multiplied by a rate. Cost "
                "per unit of work is the engineering number; price per unit is a "
                "contract."
            ),
            "by_repo": repos,
            # What the per-repo table is NOT showing, so the omission is stated
            # rather than inferred from a table that looks complete.
            "repo_note": (
                f"{hidden['sessions']} unattributed session(s) are counted in the "
                "totals but not listed below: they carry no repo label and no "
                "measurable cost or tokens. Codex publishes neither metric, so its "
                "sessions land here whenever a checkout runs without "
                "OTEL_RESOURCE_ATTRIBUTES."
            )
            if hidden
            else None,
            "days": days,
            "cost_note": BUILD_TELEMETRY_COST_NOTE,
            "comparison_md": BUILD_TELEMETRY_COMPARISON_MD,
            "tool_notes": [{"tool": k, **v} for k, v in BUILD_TELEMETRY_TOOL_NOTES.items()],
            "scope_note": (
                "The totals above are account-wide: the harvest queries each "
                "metric name with no label selector, so every repository "
                "exporting to this CloudWatch endpoint is included. Use the "
                "By repository table to see one codebase on its own, or this "
                "project beside the repos that support it. Work whose "
                "exporter ran without resource attributes is counted as "
                "'unattributed' rather than being dropped. A repo label left "
                "on the docs placeholder (<owner>/name) is folded into the "
                "real repo of that name — the same codebase, mislabelled for "
                "a while, not two."
            ),
            "setup": BUILD_TELEMETRY_SETUP,
        }

    @app.get("/api/build-behaviour")
    async def build_behaviour():
        """What building the lab LOOKED like — behavioural telemetry (WS16, D59).

        Sibling to /api/build-telemetry (which answers what it COST). Reads the
        `coding-logs` aggregates the CodingLogsSource harvested from Claude
        Code's OTLP log events, merges them across the window, and returns the
        five insight families the tiles render: edit-acceptance, tool mix,
        per-request latency, reliability and prompt cadence.

        Content flags are off end to end, so there is no prompt, file or tool
        content in the store to return — only the metadata aggregates.
        """
        from observability.coding_logs_source import (
            DURATION_EDGES_MS,
            PROMPT_LEN_EDGES,
            percentile_from_hist,
        )

        store = _obs_store()
        try:
            sessions = store.list_sessions(BUILD_LOGS_PLATFORM, include_raw=True)
            summary = store.summary()
        finally:
            store.close()

        # Merge every day's aggregate into one window rollup. Histograms sum
        # elementwise (that is the whole reason they are histograms), so the
        # window percentiles below are exact on the bucket grid.
        accept = reject = 0
        by_source: dict[str, dict[str, int]] = {}
        tools: dict[str, dict] = {}
        models: dict[str, dict] = {}
        errors: dict[str, int] = {}
        refusals = 0
        retries: dict[str, int] = {}
        prompt_count = 0
        prompt_len_sum = 0
        prompt_len_hist = [0] * (len(PROMPT_LEN_EDGES) + 1)
        req_dur_hist = [0] * (len(DURATION_EDGES_MS) + 1)
        req_dur_sum = 0.0
        req_dur_n = 0
        days: list[dict] = []

        def _add_hist(dst: list[int], src) -> None:
            for i, v in enumerate(src or []):
                if i < len(dst):
                    dst[i] += int(v or 0)

        for row in sessions:
            try:
                raw = json.loads(row.get("raw_json") or "{}")
            except (ValueError, TypeError):
                raw = {}
            agg = raw.get("aggregates") or {}
            dec = agg.get("decisions") or {}
            accept += int(dec.get("accept") or 0)
            reject += int(dec.get("reject") or 0)
            for src_name, sv in (dec.get("by_source") or {}).items():
                b = by_source.setdefault(src_name, {"accept": 0, "reject": 0})
                b["accept"] += int(sv.get("accept") or 0)
                b["reject"] += int(sv.get("reject") or 0)
            for name, tv in (agg.get("tools") or {}).items():
                t = tools.setdefault(
                    name,
                    {
                        "tool": name,
                        "count": 0,
                        "success": 0,
                        "fail": 0,
                        "mcp": False,
                        "dur_hist": [0] * (len(DURATION_EDGES_MS) + 1),
                        "dur_sum": 0.0,
                        "dur_n": 0,
                    },
                )
                t["count"] += int(tv.get("count") or 0)
                t["success"] += int(tv.get("success") or 0)
                t["fail"] += int(tv.get("fail") or 0)
                t["mcp"] = t["mcp"] or bool(tv.get("mcp"))
                _add_hist(t["dur_hist"], tv.get("dur_hist"))
                t["dur_sum"] += float(tv.get("dur_sum") or 0)
                t["dur_n"] += int(tv.get("dur_n") or 0)
            for name, mv in (agg.get("models") or {}).items():
                m = models.setdefault(
                    name,
                    {
                        "model": name,
                        "count": 0,
                        "dur_hist": [0] * (len(DURATION_EDGES_MS) + 1),
                        "dur_sum": 0.0,
                        "dur_n": 0,
                        "input": 0,
                        "output": 0,
                        "cache_read": 0,
                        "cache_creation": 0,
                    },
                )
                m["count"] += int(mv.get("count") or 0)
                _add_hist(m["dur_hist"], mv.get("dur_hist"))
                m["dur_sum"] += float(mv.get("dur_sum") or 0)
                m["dur_n"] += int(mv.get("dur_n") or 0)
                for k in ("input", "output", "cache_read", "cache_creation"):
                    m[k] += int(mv.get(k) or 0)
                _add_hist(req_dur_hist, mv.get("dur_hist"))
                req_dur_sum += float(mv.get("dur_sum") or 0)
                req_dur_n += int(mv.get("dur_n") or 0)
            for code, n in (agg.get("errors") or {}).items():
                errors[code] = errors.get(code, 0) + int(n or 0)
            refusals += int(agg.get("refusals") or 0)
            for attempt, n in (agg.get("retries") or {}).items():
                retries[attempt] = retries.get(attempt, 0) + int(n or 0)
            pr = agg.get("prompts") or {}
            prompt_count += int(pr.get("count") or 0)
            prompt_len_sum += int(pr.get("len_sum") or 0)
            _add_hist(prompt_len_hist, pr.get("len_hist"))

            d_dec = agg.get("decisions") or {}
            d_decided = int(d_dec.get("accept") or 0) + int(d_dec.get("reject") or 0)
            days.append(
                {
                    "date": raw.get("date") or str(row.get("created_at") or "")[:10],
                    "edits_accepted": int(d_dec.get("accept") or 0),
                    "edits_decided": d_decided,
                    "tool_calls": sum(
                        int(t.get("count") or 0) for t in (agg.get("tools") or {}).values()
                    ),
                    "prompts": int((agg.get("prompts") or {}).get("count") or 0),
                    "api_errors": sum(int(v or 0) for v in (agg.get("errors") or {}).values())
                    + int(agg.get("refusals") or 0),
                }
            )
        days.sort(key=lambda d: d["date"], reverse=True)

        # ---- shape the five families for the tiles -------------------------
        decided = accept + reject
        edit_acceptance = {
            "accepted": accept,
            "decided": decided,
            "rate": round(accept / decided, 4) if decided else None,
            "by_source": [
                {
                    "source": s,
                    "accepted": v["accept"],
                    "decided": v["accept"] + v["reject"],
                    "rate": round(v["accept"] / (v["accept"] + v["reject"]), 4)
                    if (v["accept"] + v["reject"])
                    else None,
                }
                for s, v in sorted(
                    by_source.items(), key=lambda kv: -(kv[1]["accept"] + kv[1]["reject"])
                )
            ],
        }

        tool_rows = []
        for t in tools.values():
            n = t["dur_n"]
            tool_rows.append(
                {
                    "tool": t["tool"],
                    "count": t["count"],
                    "success": t["success"],
                    "fail": t["fail"],
                    "success_rate": round(t["success"] / (t["success"] + t["fail"]), 4)
                    if (t["success"] + t["fail"])
                    else None,
                    "mcp": t["mcp"],
                    "avg_ms": round(t["dur_sum"] / n) if n else None,
                    "p50_ms": percentile_from_hist(t["dur_hist"], DURATION_EDGES_MS, 0.5),
                    "p90_ms": percentile_from_hist(t["dur_hist"], DURATION_EDGES_MS, 0.9),
                }
            )
        tool_rows.sort(key=lambda r: -r["count"])
        tool_calls_total = sum(r["count"] for r in tool_rows)
        mcp_calls = sum(r["count"] for r in tool_rows if r["mcp"])

        model_rows = []
        for m in models.values():
            n = m["dur_n"]
            model_rows.append(
                {
                    "model": m["model"],
                    "requests": m["count"],
                    "avg_ms": round(m["dur_sum"] / n) if n else None,
                    "p50_ms": percentile_from_hist(m["dur_hist"], DURATION_EDGES_MS, 0.5),
                    "p90_ms": percentile_from_hist(m["dur_hist"], DURATION_EDGES_MS, 0.9),
                    "input_tokens": m["input"],
                    "output_tokens": m["output"],
                    "cache_read_tokens": m["cache_read"],
                    "cache_creation_tokens": m["cache_creation"],
                }
            )
        model_rows.sort(key=lambda r: -r["requests"])

        error_total = sum(errors.values()) + refusals
        request_total = sum(m["count"] for m in models.values())
        reliability = {
            "requests": request_total,
            "errors": sum(errors.values()),
            "refusals": refusals,
            "error_rate": round(error_total / request_total, 4) if request_total else None,
            "by_status": [
                {"status_code": c, "count": n}
                for c, n in sorted(errors.items(), key=lambda kv: -kv[1])
            ],
            # attempt>1 is a retry; attempt "1"/absent is the first try.
            "retries": sum(n for a, n in retries.items() if a not in ("0", "1", "", None)),
        }

        prompt_cadence = {
            "count": prompt_count,
            "avg_len": round(prompt_len_sum / prompt_count) if prompt_count else None,
            "p50_len": percentile_from_hist(prompt_len_hist, PROMPT_LEN_EDGES, 0.5),
            "p90_len": percentile_from_hist(prompt_len_hist, PROMPT_LEN_EDGES, 0.9),
        }

        request_latency = {
            "requests": req_dur_n,
            "avg_ms": round(req_dur_sum / req_dur_n) if req_dur_n else None,
            "p50_ms": percentile_from_hist(req_dur_hist, DURATION_EDGES_MS, 0.5),
            "p90_ms": percentile_from_hist(req_dur_hist, DURATION_EDGES_MS, 0.9),
        }

        harvest = (summary.get("platforms", {}).get(BUILD_LOGS_PLATFORM) or {}).get("harvest")
        return {
            "enabled": bool(sessions),
            "harvest": harvest,
            "edit_acceptance": edit_acceptance,
            "tools": tool_rows,
            "tool_calls_total": tool_calls_total,
            "mcp_calls": mcp_calls,
            "models": model_rows,
            "request_latency": request_latency,
            "reliability": reliability,
            "prompt_cadence": prompt_cadence,
            "days": days,
            "percentile_note": (
                "p50/p90 are read off a fixed-edge histogram, not raw samples — "
                "each is the upper edge of the bucket the quantile lands in, an "
                "honest over-estimate on the grid rather than a fabricated point. "
                "Histograms sum across days, so the window figure is exact where "
                "averaging per-day percentiles would not be."
            ),
            "content_note": (
                "Every number here is computed from event METADATA — decision, "
                "tool_name, duration_ms, status_code, prompt_length — that ships "
                "whether or not the content flags are set. Those flags are OFF, so "
                "no prompt text, file content or tool argument is emitted, stored "
                "or shown. The insights survive content-off because none of them "
                "needed content (D59)."
            ),
            "setup": BUILD_BEHAVIOUR_SETUP,
        }

    @app.get("/api/obs/sessions")
    async def obs_sessions(platform: str | None = None):
        store = _obs_store()
        try:
            sessions = store.list_sessions(platform)
            # D27 rider self-identification, as recorded by the platforms'
            # own logs — surfaced as a first-class column.
            callers = store.session_callers() if hasattr(store, "session_callers") else {}
            lab_traces = store.session_lab_traces() if hasattr(store, "session_lab_traces") else {}
            for s_row in sessions:
                key = f"{s_row.get('platform')}:{s_row.get('native_id')}"
                s_row["caller_agent"] = callers.get(key)
                # The lab-trace rider line (text-level join) beats platform_ref
                # counting: it survives into platforms the lab never traced.
                s_row["lab_trace_id"] = lab_traces.get(key)
                # Cross-platform common fields (survey: input/output tokens
                # exist for claude/openai/adk/foundry; model only where the
                # platform logs it — openai/foundry session raw).
                try:
                    usage = json.loads(s_row.get("usage_json") or "{}")
                    tin, tout = usage.get("input_tokens"), usage.get("output_tokens")
                    s_row["tokens_in"] = int(tin) if tin is not None else None
                    s_row["tokens_out"] = int(tout) if tout is not None else None
                except (ValueError, TypeError):
                    s_row["tokens_in"] = s_row["tokens_out"] = None
                try:
                    raw = json.loads(s_row.get("raw_json") or "{}")
                    s_row["model"] = (raw.get("model") or None) if isinstance(raw, dict) else None
                except (ValueError, TypeError):
                    s_row["model"] = None
            return {"sessions": sessions}
        finally:
            store.close()

    @app.get("/api/obs/events")
    async def obs_events(platform: str, session_id: str):
        store = _obs_store()
        try:
            return {
                "events": store.list_events(platform, session_id),
                "lab_traces": store.lab_traces_for(session_id),
            }
        finally:
            store.close()

    @app.post("/api/obs/harvest")
    async def obs_harvest(platform: str | None = None):
        from observability.adk_source import AdkSource
        from observability.anthropic_source import AnthropicSource
        from observability.coding_logs_source import CodingLogsSource
        from observability.coding_source import CodingSource
        from observability.openai_source import OpenAISource
        from observability.salesforce_source import SalesforceSource
        from observability.strands_source import StrandsSource

        sources = {
            "claude": AnthropicSource,
            "salesforce": SalesforceSource,
            "openai": OpenAISource,
            "adk": AdkSource,
            "strands": StrandsSource,
            # WS9/WS16. Reachable by name only: the Coding Agents Telemetry
            # section has its own Harvest button, and the sweep below stays the
            # five agent platforms so Observability's "harvested from all
            # platforms" keeps meaning what it says. `coding` is the metrics
            # (cost/tokens); `coding-logs` is the behavioural log signal.
            BUILD_TELEMETRY_PLATFORM: CodingSource,
            BUILD_LOGS_PLATFORM: CodingLogsSource,
        }
        wanted = (
            [platform]
            if platform
            else [n for n in sources if n not in (BUILD_TELEMETRY_PLATFORM, BUILD_LOGS_PLATFORM)]
        )
        if any(w not in sources for w in wanted):
            return {"ok": False, "error": f"unknown platform '{platform}'"}

        # Hosted: hand the work to the Lambda that already does it on a
        # schedule, and return immediately (D54).
        #
        # A full sweep takes over two minutes. The ALB's idle timeout is 120s
        # and Cloudflare's proxy limit is lower still, so a synchronous harvest
        # could not finish through the front door whatever we set: the browser
        # got the load balancer's HTML 504 and reported it as
        # `SyntaxError: Unexpected token '<'`. Raising a timeout would only
        # move the ceiling to one we do not control.
        #
        # Delegating also fixes two things the in-process sweep got wrong. The
        # Lambda harvests SIX platforms (this dict has four — foundry was never
        # in it, despite the comment above saying five), and it holds the
        # credentials the console container does not: the GCP service-account
        # key ADK needs, the Entra principal for Foundry, the CloudWatch grants
        # for coding. ADK failed here for exactly that reason.
        # Defaults to the real function rather than requiring the variable, so
        # the button behaves the same on a laptop as it does hosted. The
        # in-process sweep below is NOT a working fallback and never was: it
        # writes, and .env points A2ALAB_PG_SECRET_ARN at the READER secret
        # (right for console reads), so every source raised "cannot execute
        # INSERT in a read-only transaction" and the endpoint 500'd. It also
        # lacks the GCP key ADK needs and omits Foundry entirely. Set
        # A2ALAB_HARVEST_FUNCTION="" to force it anyway.
        function = os.environ.get(HARVEST_FUNCTION_ENV, DEFAULT_HARVEST_FUNCTION)
        if function:
            started_at = time.time()

            def fire():
                import boto3

                payload = {"platform": platform} if platform else {}
                boto3.client(
                    "lambda", region_name=os.environ.get("AWS_REGION", "us-east-1")
                ).invoke(
                    FunctionName=function,
                    InvocationType="Event",  # fire-and-forget; the result lands in Aurora
                    Payload=json.dumps(payload).encode(),
                )

            try:
                await asyncio.get_event_loop().run_in_executor(None, fire)
            except Exception as exc:  # noqa: BLE001 - surfaced, not swallowed
                return {"ok": False, "error": f"could not start the harvest: {exc}"}
            # `started_at` is the poll contract: the client watches
            # lab.obs_harvest for a last_harvest_at newer than this.
            return {
                "ok": True,
                "async": True,
                "started_at": started_at,
                "platforms": None if platform is None else [platform],
                "note": "harvest running in the background — this panel updates as each platform lands",
            }

        # Local: no Lambda configured, so run it here. Still the right
        # behaviour on a laptop, where there is no load balancer in the way.
        def run():
            store = _obs_store()
            out = []
            try:
                for name in wanted:
                    # Per source: one platform's credentials failing is a
                    # result about that platform, not a 500 for the request.
                    try:
                        out.append(sources[name]().harvest(store).__dict__)
                    except Exception as exc:  # noqa: BLE001
                        out.append(
                            {
                                "platform": name,
                                "status": "error",
                                "detail": f"{type(exc).__name__}: {exc}",
                            }
                        )
            finally:
                store.close()
            return out

        results = await asyncio.get_event_loop().run_in_executor(None, run)
        return {"ok": all(r.get("status") != "error" for r in results), "results": results}

    # ---- Hosted analyst (D23): briefs feed + ad-hoc analysis runs ---------
    # The analyst is a paused scheduled deployment on the Claude platform;
    # "Analyze" fires a manual deployment run (no local driver — the agent
    # reaches the store through the obs MCP server). Briefs land in
    # lab.obs_briefs on Aurora and are read back here.

    @app.get("/api/obs/briefs")
    async def obs_briefs():
        from observability.pg import PgClient, PgObsStore

        if not PgClient.configured():
            return {"briefs": [], "error": "hosted store not configured (A2ALAB_PG_*)"}

        def run():
            store = PgObsStore()
            try:
                # EVERY kind in the window, each row carrying its own `kind`
                # (D56). The console renders one sub-tab per kind, so a new
                # analysis agent appears as a new tab with no change here —
                # which is the point: the table was always designed to take
                # more authors, and the reader should not need editing each
                # time one arrives.
                #
                # What must never come back is the old behaviour: an unfiltered
                # list rendered under a single heading, where the cost
                # sentinel's brief appeared in the Observability section and
                # read as the analyst changing subject.
                return store.list_briefs(days=BRIEF_WINDOW_DAYS, limit=60)
            finally:
                store.close()

        try:
            briefs = await asyncio.get_event_loop().run_in_executor(None, run)
            return {"briefs": briefs}
        except Exception as exc:  # noqa: BLE001 - surface, don't 500 the panel
            return {"briefs": [], "error": f"{type(exc).__name__}: {exc}"}

    @app.post("/api/obs/analysis/run")
    async def obs_analysis_run():
        import time as _time

        # State override for hosts with no .a2alab/ (D48): the analyst ids live
        # in obs_analyst.json, a file no container has — same gap as the fan-out
        # orchestrator and jira_sync. Read the whole-JSON env first, then the
        # file. Every early return is a JSON body, never an uncaught raise: a
        # SystemExit or a json.loads error escaping here becomes a plaintext 500
        # that the browser fails to JSON.parse ("Unexpected token 'I'...").
        raw = os.environ.get("A2ALAB_OBS_ANALYST_STATE")
        if raw:
            try:
                state = json.loads(raw)
            except ValueError as exc:
                return {"ok": False, "error": f"analyst state env is not valid JSON: {exc}"}
        else:
            state_file = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "obs_analyst.json"
            if not state_file.exists():
                return {
                    "ok": False,
                    "error": "analyst not provisioned — scripts/setup_obs_analyst.py",
                }
            try:
                state = json.loads(state_file.read_text())
            except (ValueError, OSError) as exc:
                return {"ok": False, "error": f"cannot read analyst state: {exc}"}
        if state.get("mode") != "hosted" or not state.get("deployment_id"):
            return {"ok": False, "error": "analyst is not in hosted mode (D23)"}

        agent_name = state.get("agent_name") or "Observability Analyst"

        def run():
            from anthropic import Anthropic

            client = Anthropic()
            client.beta.deployments.run(state["deployment_id"])
            for _ in range(6):  # short poll for the session id; UI can check later
                _time.sleep(2)
                for dr in client.beta.deployment_runs.list(deployment_id=state["deployment_id"]):
                    if dr.session_id:
                        return {"ok": True, "session_id": dr.session_id, "agent_name": agent_name}
                    if getattr(dr, "error", None):
                        return {"ok": False, "error": f"{dr.error.type}: {dr.error.message}"}
            return {"ok": True, "session_id": None, "agent_name": agent_name}

        try:
            return await asyncio.get_event_loop().run_in_executor(None, run)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # ---- Cost sentinel (WS12): weekly build-cost brief --------------------
    # Same shape as the obs analyst above — a paused weekly deployment reading
    # the store through the obs MCP server — and deliberately so. What differs
    # is the read: briefs share lab.obs_briefs and are separated by `kind`.

    COST_BRIEF_KIND = "cost"
    COST_SENTINEL_STATE = "cost_sentinel.json"

    def _cost_sentinel_state() -> dict | None:
        # Env override for hosts with no .a2alab/ (D48), same as the obs analyst
        # and the fan-out orchestrator — the sentinel's ids live in
        # cost_sentinel.json, which no container has. deploy_console.sh injects
        # A2ALAB_COST_SENTINEL_STATE as whole JSON.
        raw = os.environ.get("A2ALAB_COST_SENTINEL_STATE")
        if raw:
            try:
                return json.loads(raw)
            except ValueError:
                return None
        path = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / COST_SENTINEL_STATE
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text())
        except (ValueError, OSError):
            return None

    @app.get("/api/cost-brief")
    async def cost_brief():
        """The newest weekly cost brief, plus enough state to render the panel
        before one exists. An un-provisioned sentinel is a normal state, not an
        error — the answer is the setup command."""
        state = _cost_sentinel_state()
        base = {
            "provisioned": bool(state),
            "agent_name": (state or {}).get("agent_name"),
            "agent_id": (state or {}).get("agent_id"),
            "deployment_id": (state or {}).get("deployment_id"),
            "model": (state or {}).get("model"),
            "cron": (state or {}).get("cron"),
            "timezone": (state or {}).get("timezone"),
            "setup_hint": "uv run python scripts/setup_cost_sentinel.py",
            # Travels with every response so no rendering of the brief can drop
            # it. Same rule as build-telemetry's cost_note.
            "cost_note": BUILD_TELEMETRY_COST_NOTE,
        }

        # Live schedule state, so Pause/Resume reflects the deployment rather
        # than the local file (the schedule can be changed from the Claude
        # console too). Best-effort: the store read below is the panel's real
        # payload, and an unreachable management API must not blank it.
        if state and state.get("deployment_id"):

            def _sched():
                from anthropic import Anthropic

                d = Anthropic().beta.deployments.retrieve(state["deployment_id"])
                sch = getattr(d, "schedule", None)
                nxt = getattr(sch, "upcoming_runs_at", None) or []
                return {
                    "sched_status": getattr(d, "status", None),
                    "cron": getattr(sch, "expression", None) or base["cron"],
                    "timezone": getattr(sch, "timezone", None) or base["timezone"],
                    "next_run_at": str(nxt[0]) if nxt else None,
                    "last_run_at": str(getattr(sch, "last_run_at", None) or "") or None,
                }

            try:
                live = await asyncio.get_event_loop().run_in_executor(None, _sched)
                base.update(live)
            except Exception as exc:  # noqa: BLE001 - schedule is a nicety, brief is the payload
                base["sched_error"] = f"{type(exc).__name__}: {exc}"

        from observability.pg import PgClient, PgObsStore

        if not PgClient.configured():
            return {**base, "briefs": [], "error": "hosted store not configured (A2ALAB_PG_*)"}

        def run():
            store = PgObsStore()
            try:
                return store.list_briefs(limit=8, kind=COST_BRIEF_KIND)
            finally:
                store.close()

        try:
            briefs = await asyncio.get_event_loop().run_in_executor(None, run)
            return {**base, "briefs": briefs}
        except Exception as exc:  # noqa: BLE001 - surface, don't 500 the panel
            return {**base, "briefs": [], "error": f"{type(exc).__name__}: {exc}"}

    @app.post("/api/cost-brief/run")
    async def cost_brief_run(request: Request):
        """Fire one manual deployment run. Operator-only: a firing bills a real
        session, which is not something a viewer should be able to spend."""
        import time as _time

        if not _is_operator(request):
            raise HTTPException(status_code=403, detail="operator-only")
        state = _cost_sentinel_state()
        if not state or not state.get("deployment_id"):
            return {
                "ok": False,
                "error": "sentinel not provisioned — scripts/setup_cost_sentinel.py",
            }

        def run():
            from anthropic import Anthropic

            client = Anthropic()
            # Manual runs work while the deployment is paused — which is how it
            # ships, so this is the normal path rather than an override.
            client.beta.deployments.run(state["deployment_id"])
            for _ in range(6):  # short poll; the UI can re-check later
                _time.sleep(2)
                for dr in client.beta.deployment_runs.list(deployment_id=state["deployment_id"]):
                    if dr.session_id:
                        return {"ok": True, "session_id": dr.session_id}
                    if getattr(dr, "error", None):
                        return {"ok": False, "error": f"{dr.error.type}: {dr.error.message}"}
            return {"ok": True, "session_id": None}

        try:
            return await asyncio.get_event_loop().run_in_executor(None, run)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    @app.post("/api/cost-brief/schedule")
    async def cost_brief_schedule(request: Request):
        """Pause or resume the sentinel's cron. Operator-only for the same
        reason as the manual run: resuming turns on recurring billed sessions,
        which is a spend decision, not a viewer's to make.

        The action is `pause` or `resume`. It talks to the live deployment via
        the SDK's pause/unpause, so the button reflects the actual schedule
        state — not the local state file, which only records what it was set to
        last (the schedule can be changed from the Claude console too)."""
        if not _is_operator(request):
            raise HTTPException(status_code=403, detail="operator-only")
        state = _cost_sentinel_state()
        if not state or not state.get("deployment_id"):
            return {"ok": False, "error": "sentinel not provisioned"}
        body = await request.json()
        action = (body or {}).get("action")
        if action not in ("pause", "resume"):
            return {"ok": False, "error": "action must be 'pause' or 'resume'"}

        def run():
            from anthropic import Anthropic

            client = Anthropic()
            did = state["deployment_id"]
            if action == "pause":
                client.beta.deployments.pause(did)
            else:
                client.beta.deployments.unpause(did)
            # Read the deployment back so the answer is the true state, not the
            # action we asked for — a pause that no-ops still returns 'paused'.
            d = client.beta.deployments.retrieve(did)
            sch = getattr(d, "schedule", None)
            return {
                "ok": True,
                "status": getattr(d, "status", None),
                "cron": getattr(sch, "expression", None),
                "timezone": getattr(sch, "timezone", None),
            }

        try:
            return await asyncio.get_event_loop().run_in_executor(None, run)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    # The console is tunnel-exposed and its API returns every raw wire
    # payload — including production-org responses. Public surface (D36):
    # the static index, the persona directory, and the password-gated
    # login. Everything else needs the JWT that login mints, sent as a
    # header — query-param credentials are gone (the live tail streams via
    # fetch, which can set headers; EventSource could not). No-op while
    # A2ALAB_TOKEN is unset.
    from interop.servers.auth import TokenAuthMiddleware

    # Public surface = the landing exhibit: the shell plus the
    # documentation-class GETs it renders (experiment tiles, protocol
    # lists, decision/doc chips) — all repo content, no wire or org data.
    # Live data (traces, obs, runs, guide) stays behind the persona JWT.
    return TokenAuthMiddleware(
        app,
        exempt_paths=(
            "/",
            # The popped-out Lab Guide window serves the SAME public SPA shell
            # as "/" (it boots guide-only off the path and signs in client-side
            # with the persona JWT, exactly as the main page does). Gating it
            # here 401s the window before its own sign-in can run — the shell
            # itself carries no data, so exempting it discloses nothing "/" does
            # not. All /api/guide calls it makes stay behind the JWT.
            "/guide",
            # WS13: the ALB health check has no credentials and never will —
            # a gated health path marks every task unhealthy and the service
            # never stabilises. It answers {"status":"healthy"} and nothing
            # else, so exempting it discloses only that the process is up.
            "/healthz",
            "/api/users",
            "/api/login",
            # WS18: an UNAUTHENTICATED visit must be logged before any sign-in,
            # so the usage beacon cannot sit behind the persona JWT. Write-only,
            # returns 204, stores a PII-free row — exempting it discloses
            # nothing. Any persona identity it records is read from the bearer
            # token when one is present, not from being on this list.
            "/api/track",
            "/api/scenarios",
            "/api/targets",
            "/api/decisions",
        ),
        # /static/: repo assets the public shell renders — component
        # screenshots and the vendored mermaid bundle. Not a policy softening
        # but the only workable rule: <img> and <script> tags cannot send the
        # bearer header, so gating these 401'd them in the browser (which is
        # exactly what happened to the screenshots between D36 and here).
        exempt_prefixes=("/api/docs/", "/static/"),
    )


def main() -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    from interop.secret_env import load_secret_env_and_log

    load_dotenv()
    # Hosted (WS13 item 1): credentials live in Secrets Manager, not on the task
    # definition, and are loaded before create_console_app() reads os.environ —
    # the registry expands ${VAR} at Registry.load(), so a late load produces
    # empty endpoints. A no-op locally, where the ARN is unset and .env holds
    # everything. The bridge has done this since WS7 item 7; the console was
    # containerized without it, which is what the guard below caught.
    load_secret_env_and_log("console")
    # Fail CLOSED. TokenAuthMiddleware treats "no A2ALAB_TOKEN" as "auth is off"
    # — correct on a laptop, catastrophic behind a public ALB. The first hosted
    # deploy (2026-07-28) wrote the runtime secret, passed its ARN, and never
    # loaded it: every /api surface answered 200 to an unauthenticated caller,
    # and a deliberately wrong bearer token was accepted too. A missing token in
    # a hosted container is a startup failure, not an open door.
    if os.environ.get("A2ALAB_RUNTIME_SECRET_ARN") and not os.environ.get("A2ALAB_TOKEN"):
        sys.exit(
            "console: A2ALAB_RUNTIME_SECRET_ARN is set but A2ALAB_TOKEN is not — "
            "refusing to start with authentication disabled. Check that the "
            "runtime secret carries A2ALAB_TOKEN."
        )
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    uvicorn.run(create_console_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
