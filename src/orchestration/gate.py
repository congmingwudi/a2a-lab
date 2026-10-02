"""A System-One gate in front of the fan-out: which units does this situation need? (WS26, D82)

Experiment B in plan/17-jev-system-one.md. Without a gate, the host-side
`consult_business_units` tool runs every leg on every situation. With one, a
single Jev call asks one `noul` question per business unit — "this situation
requires the <unit> unit to act" — and the probability decides:

    p >= dispatch threshold   dispatch        (confident yes)
    p <= skip threshold       skip            (confident no)
    otherwise                 dispatch, marked uncertain

The middle band dispatches because of the runner's contract — a missing leg is
reported, never omitted (runner.py). An uncertain skip would be a silently
missing leg wearing a probability. And a skip is never silent either: the
hand-off to the orchestrating model names every skipped unit and its p, so the
brief can say who was not consulted and why.

The gate FAILS OPEN. No key, a timeout, an HTTP error or a malformed answer
means every leg dispatches and the hand-off says the gate was unavailable. A
gate outage may cost efficiency; it must never cost coverage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from orchestration.legs import Leg
from platforms.jev import JevError, SystemOneClient, question

GATES = ("none", "jev")

# The vendor's bands (docs.typesafe.ai/confidence): act automatically above 0.9,
# route elsewhere below 0.5. A noul is a probability rather than a Choice
# confidence, so "confident no" is the mirror image of "confident yes" — 0.1,
# not 0.5. Skipping a unit is the costly error (a missing section in a
# disruption brief), so the skip side is the strict one.
DISPATCH_THRESHOLD_DEFAULT = 0.9
SKIP_THRESHOLD_DEFAULT = 0.1


def thresholds() -> tuple[float, float]:
    """(dispatch, skip), read at call time — never import (see legs.py)."""
    dispatch = float(os.environ.get("A2ALAB_JEV_GATE_DISPATCH") or DISPATCH_THRESHOLD_DEFAULT)
    skip = float(os.environ.get("A2ALAB_JEV_GATE_SKIP") or SKIP_THRESHOLD_DEFAULT)
    if not 0.0 <= skip < dispatch <= 1.0:
        raise ValueError(f"gate thresholds need 0 <= skip < dispatch <= 1, got {skip}/{dispatch}")
    return dispatch, skip


def band(p: float, dispatch_at: float, skip_at: float) -> str:
    """The decision a probability earns. Shared by the live gate and the eval
    (scripts/jev_gate_eval.py), so the baseline is scored under the same bands."""
    if p >= dispatch_at:
        return "dispatch"
    if p <= skip_at:
        return "skip"
    return "uncertain"


def unit_question(leg: Leg) -> str:
    """The noul for one unit. Built from the leg's own remit text so the gate
    and the leg can never describe the unit differently."""
    return (
        f"Responding to this situation requires the {leg.business_unit} unit to act. "
        f"That unit's remit: {leg.question}"
    )


@dataclass
class GateDecision:
    leg: Leg
    p: float | None  # None when the gate was unavailable
    decision: str  # "dispatch" | "skip" | "uncertain" | "unavailable"

    @property
    def dispatches(self) -> bool:
        return self.decision != "skip"


@dataclass
class GateResult:
    decisions: list[GateDecision] = field(default_factory=list)
    available: bool = True
    error: str = ""
    model: str = ""
    latency_ms: int = 0
    usage: dict = field(default_factory=dict)

    @property
    def dispatch_roles(self) -> set[str]:
        return {d.leg.role for d in self.decisions if d.dispatches}

    @property
    def skipped(self) -> list[GateDecision]:
        return [d for d in self.decisions if d.decision == "skip"]

    def render(self) -> str:
        """The gate's lines in the hand-off. Always present when a gate ran, so
        a gated run can never read like an ungated one."""
        if not self.available:
            return f"[gate: Jev unavailable ({self.error}) — failed open, every unit consulted]"
        lines = []
        for d in self.skipped:
            lines.append(
                f"[leg skipped by gate: {d.leg.business_unit} ({d.leg.platform}) — "
                f"Jev p={d.p:.2f} that this unit must act]"
            )
        uncertain = [d for d in self.decisions if d.decision == "uncertain"]
        summary = (
            f"[gate: Jev {self.model} — {len(self.skipped)}/{len(self.decisions)} units skipped"
        )
        if uncertain:
            summary += ", dispatched despite uncertainty: " + ", ".join(
                f"{d.leg.business_unit} (p={d.p:.2f})" for d in uncertain
            )
        lines.append(summary + f"; {self.latency_ms} ms]")
        return "\n".join(lines)

    def summary(self) -> str:
        """One phrase for the console coverage line."""
        if not self.available:
            return "gate unavailable — failed open"
        if not self.skipped:
            return "Jev gate skipped none"
        return "Jev gate skipped " + ", ".join(d.leg.business_unit for d in self.skipped)


async def gate_legs(
    task: str,
    legs: tuple[Leg, ...],
    *,
    trace_id: str,
    source: str,
    client: SystemOneClient | None = None,
) -> GateResult:
    """Decide which legs to dispatch. Never raises for a Jev failure."""
    dispatch_at, skip_at = thresholds()
    client = client or SystemOneClient()
    questions = {leg.role: question("noul", unit_question(leg)) for leg in legs}
    try:
        result = await client.ask(
            task, questions, trace_id=trace_id, source=source, detail="fan-out gate"
        )
    except JevError as exc:
        return GateResult(
            decisions=[GateDecision(leg, None, "unavailable") for leg in legs],
            available=False,
            error=str(exc)[:160],
        )
    decisions = []
    for leg in legs:
        p = result.answers[leg.role].noul
        decisions.append(GateDecision(leg, p, band(p, dispatch_at, skip_at)))
    return GateResult(
        decisions=decisions,
        model=result.model,
        latency_ms=result.latency_ms,
        usage=result.usage,
    )
