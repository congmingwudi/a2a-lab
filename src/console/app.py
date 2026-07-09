"""Lab console: a web viewer for the wire traces.

    uv run python -m console --port 8200

- GET /            single-page UI (plain HTML/JS, no build step)
- GET /api/traces  traces grouped by trace_id, newest first
- GET /api/stream  SSE live tail of new TraceEvents (file-watcher)
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

TRACE_DIR = Path(os.environ.get("A2ALAB_TRACE_DIR", "traces"))
STATIC_DIR = Path(__file__).parent / "static"


def _read_events() -> list[dict]:
    events: list[dict] = []
    if not TRACE_DIR.exists():
        return events
    for path in sorted(TRACE_DIR.glob("*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


def create_console_app() -> FastAPI:
    app = FastAPI(title="A2A lab console")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

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
            # Track per-file offsets, starting at current EOF.
            offsets: dict[Path, int] = {}
            if TRACE_DIR.exists():
                for path in TRACE_DIR.glob("*.jsonl"):
                    offsets[path] = path.stat().st_size
            yield "event: hello\ndata: {}\n\n"
            while True:
                await asyncio.sleep(0.5)
                if not TRACE_DIR.exists():
                    continue
                for path in sorted(TRACE_DIR.glob("*.jsonl")):
                    prev = offsets.get(path, 0)
                    size = path.stat().st_size
                    if size > prev:
                        with path.open("r", encoding="utf-8") as f:
                            f.seek(prev)
                            chunk = f.read(size - prev)
                        offsets[path] = prev + len(chunk.encode("utf-8"))
                        for line in chunk.splitlines():
                            line = line.strip()
                            if line:
                                yield f"data: {line}\n\n"

        return StreamingResponse(
            gen(),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    return app


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
