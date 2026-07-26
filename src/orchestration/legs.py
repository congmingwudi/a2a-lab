"""The legs of a fan-out scenario: who answers what, on which platform.

Scenario A — supplier disruption response. The platform assignment is the
business case, not an implementation detail: three business units, three
clouds, three vendors chosen at three different times, and a disruption that
does not care about any of that. See WS8 in plan/07-workstreams.md.

Each leg names its own **dedicated** agent rather than reusing the lab's
general-purpose research agents (the D25 twin rule, applied to a scenario
instead of a platform pair). Two reasons: the research agents carry prompts
that would drift the answers, and a dedicated agent means the observability
harvest can attribute a platform session to THIS experiment rather than to
"the ADK agent".
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Per-leg answer shaping. Kept identical across legs on purpose: the experiment
# compares platforms, so every leg must be asked for the same shape of answer,
# and the call path must be the same every run (the determinism requirement that
# makes runs comparable — same technique as the [A2A-LAB ROUTING] block).
ANSWER_SHAPE = (
    "Answer in at most 3 short bullets, 60 words total. Be specific and "
    "concrete. Do not ask clarifying questions — state your assumptions "
    "instead. Do not call any other agent or external tool."
)


@dataclass(frozen=True)
class Leg:
    """One parallel branch of a fan-out."""

    role: str  # stable id, used in trace hops and the synthesised answer
    business_unit: str  # the story: who owns this question in a real company
    target: str  # config/targets.yaml name
    platform: str  # for the delegation rider + display
    question: str  # role-specific instruction, appended to the shared task

    def prompt(self, task: str) -> str:
        return f"{self.question}\n\nSITUATION:\n{task}\n\n{ANSWER_SHAPE}"


# Per-leg target overrides, read LAZILY.
#
# These were originally baked into the LEGS tuple at import time, which was a
# silent bug: `scripts/run_fanout.py` imports this module at the top of the
# file and calls load_dotenv() inside main(), so the overrides in .env were
# always read as absent and every run quietly used the default targets. The
# console lied about nothing and the run looked perfect — the only way it
# surfaced was reading the recorded hops and finding the wrong target names.
# Anything env-dependent here has to be resolved when it is USED.
_TARGET_DEFAULTS = {
    "exposure": ("A2ALAB_LEG_EXPOSURE_TARGET", "google-adk-a2a"),
    "commercial": ("A2ALAB_LEG_COMMERCIAL_TARGET", "foundry-a2a"),
    "customer_comms": ("A2ALAB_LEG_COMMS_TARGET", "openai-agentcore"),
}


def target_for(role: str) -> str:
    var, default = _TARGET_DEFAULTS[role]
    return os.environ.get(var) or default


# Scenario A legs. Targets resolve through the registry, so A2ALAB_MODE and the
# hosted remaps apply exactly as they do everywhere else.
_LEG_SPECS: tuple[Leg, ...] = (
    Leg(
        role="exposure",
        business_unit="Logistics",
        target="",  # filled by legs_for() at call time
        platform="adk",
        question=(
            "You are the logistics operations agent. Assess which shipments, "
            "orders and routes are exposed by this disruption, and how badly."
        ),
    ),
    Leg(
        role="commercial",
        business_unit="Commercial / Legal",
        target="",
        platform="foundry",
        question=(
            "You are the commercial and contracts agent. Assess the "
            "contractual position: delay penalties, force majeure exposure, "
            "and which customer commitments are at risk."
        ),
    ),
    Leg(
        role="customer_comms",
        business_unit="Customer operations",
        target="",
        platform="openai",
        question=(
            "You are the customer communications agent. Draft the key points "
            "for notifying affected customers: what to say, what to promise, "
            "and what to avoid committing to."
        ),
    ),
)


def legs_for(scenario: str = "supplier-disruption") -> tuple[Leg, ...]:
    """Legs for a scenario, with targets resolved from the environment NOW.

    One scenario today; the signature is the seam a second one (vendor
    diligence, incident triage) would arrive through.
    """
    if scenario != "supplier-disruption":
        raise KeyError(f"unknown fan-out scenario '{scenario}' — known: supplier-disruption")
    return tuple(
        Leg(
            role=spec.role,
            business_unit=spec.business_unit,
            target=target_for(spec.role),
            platform=spec.platform,
            question=spec.question,
        )
        for spec in _LEG_SPECS
    )


# Back-compat for callers that imported the tuple directly. Evaluated at import
# like any module global, so prefer legs_for() anywhere env matters.
LEGS = legs_for()
