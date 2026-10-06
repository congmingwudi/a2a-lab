"""Scoring for Experiment B: did the gate consult the units a situation needed? (WS26, D82)

Pure functions over (situation, {role: p}) pairs, so the live script
(scripts/jev_gate_eval.py) and the tests score the same way. The labelled set is
config/jev_situations.yaml and it is AUTHOR-LABELLED — every number here is
agreement with the author, which the script prints beside the numbers.

What is counted, per unit and overall:

  needed & dispatched     TP — the gate kept a leg the brief needed
  needed & skipped        FN — a WRONG SKIP: a missing section in the brief.
                               The costly error; reported by name, not just rate.
  not needed & skipped    TN — a leg saved
  not needed & dispatched FP — a wasted leg (including the uncertain band, which
                               dispatches by design — see gate.py)

`legs saved` is every skip (right or wrong) over the legs an ungated run would
have sent (every unit on every situation) — the efficiency the gate bought, to
be read beside the wrong skips it cost. Brier is over the raw p
against needed/not-needed, so calibration is visible separately from the bands.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestration.gate import band


@dataclass
class Situation:
    id: str
    kind: str
    text: str
    expected: list[str]
    why: str = ""


@dataclass
class Scored:
    situation: str
    role: str
    p: float
    decision: str  # dispatch | skip | uncertain
    needed: bool

    @property
    def dispatched(self) -> bool:
        return self.decision != "skip"


@dataclass
class Score:
    n_legs: int = 0
    tp: int = 0
    fn: int = 0
    tn: int = 0
    fp: int = 0
    uncertain: int = 0
    brier: float = 0.0
    wrong_skips: list[Scored] = field(default_factory=list)

    @property
    def recall(self) -> float | None:
        """Of the legs the situation needed, the share the gate sent."""
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else None

    @property
    def precision(self) -> float | None:
        """Of the legs the gate sent, the share that were needed."""
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    @property
    def legs_saved(self) -> float:
        """Share of an ungated run's legs the gate did not send."""
        return (self.tn + self.fn) / self.n_legs if self.n_legs else 0.0


def load_situations(data: dict) -> list[Situation]:
    out = []
    for raw in data.get("situations") or []:
        out.append(
            Situation(
                id=raw["id"],
                kind=raw.get("kind", ""),
                text=" ".join(str(raw["text"]).split()),
                expected=list(raw.get("expected") or []),
                why=raw.get("why", ""),
            )
        )
    return out


def score_situation(
    situation: Situation, probs: dict[str, float], dispatch_at: float, skip_at: float
) -> list[Scored]:
    return [
        Scored(
            situation=situation.id,
            role=role,
            p=p,
            decision=band(p, dispatch_at, skip_at),
            needed=role in situation.expected,
        )
        for role, p in probs.items()
    ]


def aggregate(rows: list[Scored]) -> Score:
    s = Score(n_legs=len(rows))
    for r in rows:
        if r.needed and r.dispatched:
            s.tp += 1
        elif r.needed:
            s.fn += 1
            s.wrong_skips.append(r)
        elif r.dispatched:
            s.fp += 1
        else:
            s.tn += 1
        if r.decision == "uncertain":
            s.uncertain += 1
        s.brier += (r.p - (1.0 if r.needed else 0.0)) ** 2
    if rows:
        s.brier = round(s.brier / len(rows), 4)
    return s


def by(rows: list[Scored], key) -> dict[str, Score]:
    groups: dict[str, list[Scored]] = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    return {k: aggregate(v) for k, v in groups.items()}
