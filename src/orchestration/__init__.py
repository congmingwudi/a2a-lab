"""Fan-out orchestration (WS8) — one task, several platforms, in parallel.

Every other experiment in this lab is 1:1. This package is the missing shape:
an orchestrator dispatches one business task to several agents on several
platforms *at the same time* and synthesises one answer. It exists to stress
three things nothing else here touches — the D27 delegation guard under
parallel dispatch rather than chaining, observability correlation across four
platforms' logs, and partial failure, which at fan-out is the normal case
rather than the edge case.

Placed alongside `src/briefs/` rather than under `src/platforms/` for the same
reason that package is: this is host-side orchestration logic, not an agent we
serve. Both orchestrator variants (Anthropic Managed Agents and Google ADK)
call the same `dispatch()` so the comparison measures the hosting model, not
two different implementations.

The module is `runner.py`, not `dispatch.py`, so that re-exporting the
`dispatch()` function here does not shadow the module it came from —
`orchestration.dispatch` would otherwise resolve to the function and make the
module unpatchable in tests. Same name as `briefs/runner.py`, which is the
closest existing analogue.
"""

from orchestration.legs import LEGS, Leg, legs_for
from orchestration.runner import FanOutResult, LegResult, dispatch

__all__ = ["FanOutResult", "LegResult", "dispatch", "LEGS", "Leg", "legs_for"]
