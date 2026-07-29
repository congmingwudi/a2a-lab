"""Lab console: a web viewer for the wire traces, and the cockpit for
launching experiments.

    uv run python -m console --port 8200

- GET  /              single-page UI (plain HTML/JS, no build step)
- GET  /api/traces    traces grouped by trace_id, newest first
- GET  /api/stream    SSE live tail of new TraceEvents (file-watcher)
- GET  /api/targets   runnable targets from config/targets.yaml
- GET  /api/scenarios primary demo scenarios + nav groups from config/scenarios.yaml
- GET  /api/insights  trusted-advisor findings from config/insights.yaml
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

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

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
            "chatgpt-to-agentforce": ("openai-agents-sdk-agent", "openai"),
            "adk-to-agentforce": ("adk-gemini-agent", "adk"),
            "foundry-to-agentforce": ("foundry-agent", "foundry"),
            # The fan-out scenarios delegate too — three times per run, to
            # three platforms. Showing no rider there implied the guard was
            # an Agentforce-only concern.
            "supplier-disruption-cma": ("a2alab-supply-orchestrator", "claude"),
            "supplier-disruption-adk": ("a2alab-supply-orchestrator-adk", "adk"),
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
        return identity.load_users().get(sub, {}).get("role") == "operator"

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
        """Whitelisted lab docs (plan/*.md + README.md) as raw markdown —
        the UI renders insight file-ref chips into popovers from these,
        same pattern as the decision chips."""
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
        )
        if not allowed or not candidate.exists():
            raise HTTPException(status_code=404, detail=f"unknown doc: {name}")
        return {"name": name, "markdown": candidate.read_text(encoding="utf-8")}

    @app.get("/api/config")
    async def config():
        reg = get_registry()
        return {
            "mode": reg.mode,
            "modes": reg.modes,
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
        }

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
            target = get_registry().get(target_name)
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
        try:
            async with httpx.AsyncClient(timeout=5.0) as http:
                r = await http.get(url)
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
                from orchestration.cma import CmaOrchestrator

                # Which topology to run. "tool" keeps the host-side fan-out
                # (the control); "mcp" gives the model three remote MCP tools
                # and lets it schedule them (D41). The operator picks per run,
                # because the interesting comparison is same-agent-same-prompt.
                variant = "mcp" if body.get("variant") == "mcp" else "tool"
                trace_id = body.get("trace_id") or new_trace_id()
                result = await CmaOrchestrator(variant=variant).run(message, trace_id=trace_id)
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

    # Set by deploy/console/deploy_console.sh to the scheduled harvest Lambda.
    # Its presence is what makes the Harvest button asynchronous — unset on a
    # laptop, where the sweep runs in-process because nothing is timing it out.
    HARVEST_FUNCTION_ENV = "A2ALAB_HARVEST_FUNCTION"

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
    }

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
            sessions = store.list_sessions(BUILD_TELEMETRY_PLATFORM)
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
        from observability.coding_source import CodingSource
        from observability.openai_source import OpenAISource
        from observability.salesforce_source import SalesforceSource

        sources = {
            "claude": AnthropicSource,
            "salesforce": SalesforceSource,
            "openai": OpenAISource,
            "adk": AdkSource,
            # WS9. Reachable by name only: the Coding Agents Telemetry section
            # has its own Harvest button, and the sweep below stays the five
            # agent platforms so Observability's "harvested from all platforms"
            # keeps meaning what it says.
            BUILD_TELEMETRY_PLATFORM: CodingSource,
        }
        wanted = [platform] if platform else [n for n in sources if n != BUILD_TELEMETRY_PLATFORM]
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
        function = os.environ.get(HARVEST_FUNCTION_ENV)
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
            try:
                return [sources[name]().harvest(store).__dict__ for name in wanted]
            finally:
                store.close()

        results = await asyncio.get_event_loop().run_in_executor(None, run)
        return {"ok": True, "results": results}

    # ---- Hosted analyst (D23): briefs feed + ad-hoc analysis runs ---------
    # The analyst is a paused scheduled deployment on the Claude platform;
    # "Analyze" fires a manual deployment run (no local driver — the agent
    # reaches the store through the obs MCP server). Briefs land in
    # lab.obs_briefs on Aurora and are read back here.

    @app.get("/api/obs/briefs")
    async def obs_briefs():
        from observability.pg import BRIEF_OBSERVABILITY, PgClient, PgObsStore

        if not PgClient.configured():
            return {"briefs": [], "error": "hosted store not configured (A2ALAB_PG_*)"}

        def run():
            store = PgObsStore()
            try:
                # Filtered by kind (D56). Two different agents write to
                # lab.obs_briefs — the observability analyst and the WS12 cost
                # sentinel — and this endpoint asked for neither, so it
                # returned whatever was newest. The Observability section
                # ended up rendering a build-COST brief and looking like the
                # analyst had suddenly started talking about coding telemetry.
                # Its sibling at /api/cost-brief always filtered; this one
                # never did.
                return store.list_briefs(kind=BRIEF_OBSERVABILITY)
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

        state_file = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab")) / "obs_analyst.json"
        if not state_file.exists():
            return {"ok": False, "error": "analyst not provisioned — scripts/setup_obs_analyst.py"}
        state = json.loads(state_file.read_text())
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
            # WS13: the ALB health check has no credentials and never will —
            # a gated health path marks every task unhealthy and the service
            # never stabilises. It answers {"status":"healthy"} and nothing
            # else, so exempting it discloses only that the process is up.
            "/healthz",
            "/api/users",
            "/api/login",
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
