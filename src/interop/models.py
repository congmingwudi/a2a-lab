"""Canonical message shapes shared by every protocol server and client.

Every protocol (REST, MCP, A2A) and every platform (Claude, Agentforce,
OpenAI, ...) maps to and from these two dataclasses. The mapping rules per
protocol are documented in plan/01-architecture.md.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any


def new_trace_id() -> str:
    return uuid.uuid4().hex


@dataclass
class AgentRequest:
    """A single question/instruction for an agent."""

    message: str
    session_id: str | None = None
    trace_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message": self.message,
            "session_id": self.session_id,
            "trace_id": self.trace_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentRequest":
        return cls(
            message=data["message"],
            session_id=data.get("session_id"),
            trace_id=data.get("trace_id"),
            metadata=data.get("metadata") or {},
        )


@dataclass
class AgentResponse:
    """An agent's answer."""

    text: str
    session_id: str | None = None
    latency_ms: int | None = None
    raw: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "session_id": self.session_id,
            "latency_ms": self.latency_ms,
            "raw": self.raw,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentResponse":
        return cls(
            text=data["text"],
            session_id=data.get("session_id"),
            latency_ms=data.get("latency_ms"),
            raw=data.get("raw"),
        )
