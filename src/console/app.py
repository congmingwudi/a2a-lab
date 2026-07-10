"""Lab console: a web viewer for the wire traces, and the cockpit for
launching experiments.

    uv run python -m console --port 8200

- GET  /            single-page UI (plain HTML/JS, no build step)
- GET  /api/traces  traces grouped by trace_id, newest first
- GET  /api/stream  SSE live tail of new TraceEvents (file-watcher)
- GET  /api/targets runnable targets from config/targets.yaml
- POST /api/run     run one experiment cell (custom prompt, live trace)
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

from interop.clients.base import RemoteAgentClient
from interop.models import AgentRequest, new_trace_id
from interop.registry import Registry
from interop.trace import DEFAULT_TRACE_DIR, TRACE_DIR_ENV

STATIC_DIR = Path(__file__).parent / "static"

# Same default utterance as scripts/matrix.py — a prompt every platform can
# attempt, so "Run all" doubles as the everything-is-up verification sweep.
DEFAULT_QUESTION = (
    "In two sentences: what is the difference between the MCP and A2A "
    "protocols for agent interoperability?"
)


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
                }
                for t in get_registry().targets.values()
            ],
            "default_question": DEFAULT_QUESTION,
        }

    @app.post("/api/run")
    async def run(request: Request):
        body = await request.json()
        name = body.get("target")
        if not name:
            raise HTTPException(status_code=400, detail="missing 'target'")
        try:
            get_registry().get(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        req = AgentRequest(
            message=(body.get("message") or "").strip() or DEFAULT_QUESTION,
            # Client-minted trace_id so the UI can select the trace and watch
            # hops stream in while the run is still in flight.
            trace_id=body.get("trace_id") or new_trace_id(),
            session_id=body.get("session_id") or None,
        )
        try:
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
                    if path.stat().st_size <= prev:
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
