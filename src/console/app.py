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
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

import httpx
import yaml

from console.insights import by_category, load_insights, to_markdown
from interop import af_channel, delegation
from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry
from interop.trace import DEFAULT_TRACE_DIR, TRACE_DIR_ENV

STATIC_DIR = Path(__file__).parent / "static"
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

_SF_INTERIOR = {
    "source": "agentforce",
    "target": "agentforce-apex",
    "protocol": "internal",
    "detail": (
        "The twin's topic planner and Apex actions run inside Salesforce — "
        "platform-interior; visible only through harvested execution logs "
        "(Observability section), never on the wire."
    ),
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
                "API's hosted-agents beta): session create + turn — recorded "
                "as a real hop."
            ),
        }
    return {
        "source": "claude-researcher",
        "target": "claude-agent-sdk",
        "protocol": "internal",
        "detail": (
            "Self-hosted claude-agent-sdk turn in the lab process — the "
            "model calls to the Claude API are platform-interior."
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
                        "OpenAI Agents SDK turn against the Responses API — "
                        "platform-interior, and OpenAI's trace dashboard is "
                        "write-only (no read API), so this leg is dark."
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
                    "backend — Managed Agents is the laptop default; the "
                    "container ships the self-hosted fallback)."
                ),
            }
            if platform == "claude"
            else {
                "source": "openai-researcher",
                "target": "openai-platform",
                "protocol": "internal",
                "detail": "OpenAI Agents SDK turn against the Responses API inside the container.",
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
                        f"— the {twin} twin (closed two-platform pairing)."
                    ),
                },
                _SF_INTERIOR,
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
                _SF_INTERIOR,
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
                    "target": "foundry",
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
                    "target": "gemini",
                    "protocol": "internal",
                    "detail": (
                        "ADK Runner + Gemini inside the Agent Engine "
                        "container — request-level Cloud Logging/Monitoring "
                        "only (Observability section); no session/turn API."
                    ),
                },
            ],
            "question": question,
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
                "url": os.environ.get(
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
                "title": "Agentforce agent — A2ALab Research Assistant",
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
                "a2alab_openai AgentCore runtime.",
                "url": os.environ.get("OPENAI_CONSOLE_URL") or None,
                **_shots("openai-agentcore"),
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
                "url": os.environ.get(
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
                "url": os.environ.get(
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
    # Component screenshots etc. — /static/* still requires the lab token
    # (the UI appends ?token=, same as the API calls).
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
    async def targets():
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
                    "components": components_for(_PLATFORM_TAGS.get(t.platform, set())),
                    **details,
                }
            )
        return {"targets": out, "default_question": DEFAULT_QUESTION}

    @app.get("/api/scenarios")
    async def scenarios():
        return {
            "groups": load_groups(),
            "scenarios": [
                {"name": name, **spec, "components": components_for(set(spec.get("tags") or []))}
                for name, spec in load_scenarios().items()
            ],
        }

    # ---- Insights + deployment mode ---------------------------------------
    # The trusted-advisor findings (config/insights.yaml via console.insights)
    # and which runtimes /api/run really hits under the active A2ALAB_MODE.

    @app.get("/api/insights")
    async def insights():
        data = load_insights()
        return {"insights": data, "categories": by_category(data)}

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

    @app.get("/api/decisions")
    async def decisions():
        """The ADR log parsed per decision id — the UI renders D-refs as
        chips whose popover shows the decision's markdown."""
        return {"decisions": load_decisions()}

    @app.get("/api/config")
    async def config():
        reg = get_registry()
        return {
            "mode": reg.mode,
            "modes": reg.modes,
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
                "seams": [
                    "ask_agentforce (sdk)",
                    "ask_agentforce (managed)",
                    "ask_agentforce (openai)",
                    "ask_agentforce (adk)",
                    "bridge",
                ],
                # The <placeholders> in the rider are display-only; real
                # injected blocks carry the delegating seam's identity:
                "callers": [
                    "claude-sdk-agent (claude)",
                    "claude-managed-agent (claude)",
                    "openai-agents-sdk-agent (openai)",
                    "adk-gemini-agent (adk)",
                    "agentforce-twin-via-bridge (agentforce)",
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
            try:
                resp = await client.ask(AgentRequest(message=WARMUP_PING, trace_id=new_trace_id()))
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
            while True:
                await asyncio.sleep(0.5)
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
        from observability import ObsStore

        return ObsStore()

    # The honest capability matrix (plan/05-observability.md) — rendered
    # live in the coverage panel next to what was actually harvested.
    OBS_CAPABILITIES = {
        "anthropic": {
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
                "response retrieval by id (Responses API — the join key the client captures)",
                "App Insights + KQL over agent runs (once attached — deliberately not provisioned yet)",
                "agent/version/session admin APIs (list, get, session log streams)",
            ],
            "cannot": [
                "anything harvested yet — foundry_source.py lands with App Insights (WS3 next)",
                "per-call A2A tool telemetry on the preview surface",
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
        data["capabilities"] = OBS_CAPABILITIES
        return data

    @app.get("/api/obs/sessions")
    async def obs_sessions(platform: str | None = None):
        store = _obs_store()
        try:
            return {"sessions": store.list_sessions(platform)}
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
        from observability.openai_source import OpenAISource
        from observability.salesforce_source import SalesforceSource

        sources = {
            "anthropic": AnthropicSource,
            "salesforce": SalesforceSource,
            "openai": OpenAISource,
            "adk": AdkSource,
        }
        wanted = [platform] if platform else list(sources)
        if any(w not in sources for w in wanted):
            return {"ok": False, "error": f"unknown platform '{platform}'"}

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
        from observability.pg import PgClient, PgObsStore

        if not PgClient.configured():
            return {"briefs": [], "error": "hosted store not configured (A2ALAB_PG_*)"}

        def run():
            store = PgObsStore()
            try:
                return store.list_briefs()
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

    # The console is tunnel-exposed and its API returns every raw wire
    # payload — including production-org responses. Only the static index
    # stays open; /api/* requires the lab token (query param allowed because
    # EventSource can't set headers). No-op while A2ALAB_TOKEN is unset.
    from interop.servers.auth import TokenAuthMiddleware

    return TokenAuthMiddleware(app, allow_query_param=True, exempt_paths=("/",))


def main() -> None:
    import argparse

    import uvicorn
    from dotenv import load_dotenv

    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8200)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()
    uvicorn.run(create_console_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
