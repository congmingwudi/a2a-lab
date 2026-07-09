"""Protocol-agnostic interop core: canonical message shapes, the inbound
AgentAdapter seam, the outbound RemoteAgentClient seam, trace recording,
and the target registry."""

from interop.models import AgentRequest, AgentResponse
from interop.adapter import AgentAdapter, serve
from interop.trace import TraceEvent, TraceRecorder, get_recorder

__all__ = [
    "AgentRequest",
    "AgentResponse",
    "AgentAdapter",
    "serve",
    "TraceEvent",
    "TraceRecorder",
    "get_recorder",
]
