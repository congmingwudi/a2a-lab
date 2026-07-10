"""Lab console: a web viewer for the wire traces, and the cockpit for
launching experiments.

    uv run python -m console --port 8200

- GET  /              single-page UI (plain HTML/JS, no build step)
- GET  /api/traces    traces grouped by trace_id, newest first
- GET  /api/stream    SSE live tail of new TraceEvents (file-watcher)
- GET  /api/targets   runnable targets from config/targets.yaml
- GET  /api/scenarios primary demo scenarios from config/scenarios.yaml
- POST /api/run       run a scenario or one cell (custom prompt, live trace)
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

import httpx
import yaml

from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry
from interop.trace import DEFAULT_TRACE_DIR, TRACE_DIR_ENV

STATIC_DIR = Path(__file__).parent / "static"
SCENARIOS_PATH = Path("config/scenarios.yaml")


def load_scenarios(path: str | Path = SCENARIOS_PATH) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("scenarios") or {}


# Customer-shaped default for demos: the Agentforce agent's "Customer account
# status" topic answers this from real CRM records via the A2ALab: Get Account
# Summary action (accounts: Omega, Inc. / Acme Corp / Northwind Traders), and
# the Claude→Agentforce scenario's prompt_suffix makes Claude consult
# Agentforce for it. scripts/matrix.py keeps its own protocol-comparison
# utterance — that sweep needs a question every platform can answer unaided.
DEFAULT_QUESTION = (
    "Tell me what you know about account Omega, Inc. — a short summary of their current state."
)


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


def components_for(tags: set[str]) -> list[dict]:
    """Component rows for a scenario's tags (or a target's platform mapped to
    pseudo-tags). Each: {title, kind, note, url|None} — url None renders as
    not-yet-available."""
    comps: list[dict] = []
    ld = _lightning_domain()
    if {"claude", "managed-agents"} & tags:
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
            }
        )
    if {"agentforce", "agent-api"} & tags:
        comps.append(
            {
                "title": "Agentforce agent — A2ALab Research Assistant",
                "kind": "agentforce",
                "note": "Open Agentforce Studio — topics, instructions, and the "
                "A2ALab: Get Account Summary action live here.",
                "url": f"{ld}/lightning/n/standard-AgentforceStudio?c__nav=agents"
                if ld
                else None,
            }
        )
    if "openai" in tags:
        comps.append(
            {
                "title": "OpenAI research agent — Bedrock AgentCore",
                "kind": "openai",
                "note": "Lands with M9 (platforms/openai + AgentCore deploy).",
                "url": None,
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
            }
        )
    return comps


_PLATFORM_TAGS = {
    "claude": {"claude", "managed-agents"},
    "agentforce": {"agentforce"},
    "openai": {"openai"},
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


def _read_events() -> list[dict]:
    events: list[dict] = []
    trace_dir = _trace_dir()
    if not trace_dir.exists():
        return events
    for path in sorted(trace_dir.glob("*.jsonl")):
        events.extend(_parse_lines(path.read_bytes()))
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
        return {
            "targets": [
                {
                    "name": t.name,
                    "platform": t.platform,
                    "protocol": t.protocol,
                    "status": t.status,
                    "components": components_for(_PLATFORM_TAGS.get(t.platform, set())),
                }
                for t in get_registry().targets.values()
            ],
            "default_question": DEFAULT_QUESTION,
        }

    @app.get("/api/scenarios")
    async def scenarios():
        return {
            "scenarios": [
                {"name": name, **spec, "components": components_for(set(spec.get("tags") or []))}
                for name, spec in load_scenarios().items()
            ]
        }

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

        scenario_name = body.get("scenario")
        if scenario_name:
            spec = load_scenarios().get(scenario_name)
            if not spec:
                raise HTTPException(status_code=404, detail=f"unknown scenario '{scenario_name}'")
            if spec.get("status") != "live":
                raise HTTPException(
                    status_code=409, detail=f"scenario '{scenario_name}' is not live yet"
                )
            name = spec["target"]
            via_bridge = bool(spec.get("via_bridge"))
            if spec.get("prompt_suffix"):
                message = f"{message}\n\n{spec['prompt_suffix']}"
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
                return await run_via_bridge(req, name)
            client = get_client(name)
            resp = await client.ask(req)
            return {
                "ok": True,
                "trace_id": req.trace_id,
                "text": resp.text,
                "latency_ms": resp.latency_ms,
                "session_id": resp.session_id,
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
        events = _read_events()
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
