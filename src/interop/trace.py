"""Wire-visibility trace layer.

Every hop — inbound (a protocol server handling a request) and outbound
(a RemoteAgentClient calling a remote agent) — records one TraceEvent
carrying the *raw wire payloads* (JSON-RPC envelopes for MCP/A2A, HTTP
bodies for REST, Agent API JSON for Agentforce). Events append to a JSONL
file under traces/; the lab console tails that file over SSE.

Correlation: a trace_id is minted at the edge and propagated on every hop
(HTTP header X-Trace-Id for REST/bridge, message metadata for A2A,
tool-arg passthrough for MCP).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

TRACE_DIR_ENV = "A2ALAB_TRACE_DIR"
DEFAULT_TRACE_DIR = "traces"

_MAX_PAYLOAD_CHARS = 100_000


def _clip(payload: Any) -> Any:
    """Keep raw payloads raw, but bound pathological sizes."""
    if isinstance(payload, str) and len(payload) > _MAX_PAYLOAD_CHARS:
        return payload[:_MAX_PAYLOAD_CHARS] + f"...[clipped {len(payload)} chars total]"
    return payload


@dataclass
class TraceEvent:
    trace_id: str
    source: str
    target: str
    protocol: str  # rest | mcp | a2a | agentforce-api | internal
    transport_detail: str  # e.g. "POST /invoke", "tools/call ask", "message/send"
    request_payload_raw: Any
    response_payload_raw: Any = None
    status: str = "ok"  # ok | error | pending
    latency_ms: int | None = None
    hop_seq: int = 0
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["request_payload_raw"] = _clip(d["request_payload_raw"])
        d["response_payload_raw"] = _clip(d["response_payload_raw"])
        return d


class TraceRecorder:
    """Appends TraceEvents to a JSONL file, one file per day. File-based on
    purpose — no DB, easy to tail, easy to ship."""

    def __init__(self, trace_dir: str | Path | None = None):
        self.trace_dir = Path(trace_dir or os.environ.get(TRACE_DIR_ENV, DEFAULT_TRACE_DIR))
        self._lock = threading.Lock()
        self._seq_by_trace: dict[str, int] = {}

    def _current_file(self) -> Path:
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        return self.trace_dir / (time.strftime("%Y-%m-%d") + ".jsonl")

    def next_hop_seq(self, trace_id: str) -> int:
        with self._lock:
            seq = self._seq_by_trace.get(trace_id, 0)
            self._seq_by_trace[trace_id] = seq + 1
            return seq

    def record(self, event: TraceEvent) -> TraceEvent:
        line = json.dumps(event.to_dict(), default=str, ensure_ascii=False)
        with self._lock:
            with self._current_file().open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        return event


_recorder: TraceRecorder | None = None
_recorder_lock = threading.Lock()


def get_recorder() -> TraceRecorder:
    global _recorder
    with _recorder_lock:
        if _recorder is None:
            _recorder = TraceRecorder()
        return _recorder


def set_recorder(recorder: TraceRecorder) -> None:
    global _recorder
    with _recorder_lock:
        _recorder = recorder


class Hop:
    """Context manager that times a hop and records its TraceEvent.

    Usage:
        with Hop(trace_id, source="bridge", target="claude", protocol="mcp",
                 transport_detail="tools/call ask", request_payload=raw) as hop:
            ...
            hop.response_payload = raw_response
    """

    def __init__(
        self,
        trace_id: str,
        *,
        source: str,
        target: str,
        protocol: str,
        transport_detail: str,
        request_payload: Any,
        recorder: TraceRecorder | None = None,
    ):
        self.recorder = recorder or get_recorder()
        self.event = TraceEvent(
            trace_id=trace_id,
            source=source,
            target=target,
            protocol=protocol,
            transport_detail=transport_detail,
            request_payload_raw=request_payload,
            hop_seq=self.recorder.next_hop_seq(trace_id),
        )
        self._start = None
        self.response_payload: Any = None

    def __enter__(self) -> "Hop":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.event.latency_ms = int((time.perf_counter() - self._start) * 1000)
        self.event.response_payload_raw = self.response_payload
        if exc is not None:
            self.event.status = "error"
            if self.response_payload is None:
                self.event.response_payload_raw = f"{exc_type.__name__}: {exc}"
        self.recorder.record(self.event)
