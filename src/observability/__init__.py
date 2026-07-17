"""M11: cross-platform agent execution logs, harvested and cached locally.

Each platform gets a PlatformLogSource that pulls that platform's *interior*
view of the executions the lab drove (sessions, steps, LLM calls) into the
obs_* tables of traces/lab.db — the same SQLite file the sqlite TraceSink
writes — so lab-trace ⋈ platform-log joins are plain SQL. See
plan/05-observability.md for the honest per-platform capability matrix.
"""

from observability.base import HarvestResult, PlatformLogSource
from observability.store import ObsStore

__all__ = ["HarvestResult", "PlatformLogSource", "ObsStore"]
