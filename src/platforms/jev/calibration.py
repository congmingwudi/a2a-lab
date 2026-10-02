"""Experiment C: is Jev calibrated on this lab's own wire data? (WS26, D82)

Method in plan/17-jev-system-one.md. Three parts, all pure functions so the
whole study is testable without a network:

1. **Extraction** — pull the question and the answer text out of a hop's raw
   wire bytes. The trace layer stores whatever crossed the wire, so one hop is
   a REST `{"text": ...}`, the next an A2A task with artifacts, the next an
   Agent API message list, some JSON-encoded twice, and errors are bare
   exception strings. `extract()` copes with all of them.
2. **Wire labels** — where the bytes themselves say whether an answer arrived,
   label the hop from them: `no` for a failed hop, a failed task, a JSON-RPC
   error, an empty answer or an unavailable-leg marker; `yes` for a completed
   hop with a non-trivial answer and none of those. Hops with nothing to judge
   (pending polls, submit acknowledgements, data calls with no question) are
   excluded and counted, never guessed.
3. **Metrics** — Brier score, expected calibration error, a reliability table,
   accuracy at 0.5, and coverage/accuracy at the vendor's act-or-abstain bands.

The `yes` label is the weak one: "completed with text" cannot see a polite
refusal or a placeholder, which is exactly where Jev might be right and the
wire wrong. So disagreements are returned for adjudication, not averaged away.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any

# The statement Jev (and the baseline) is asked about every hop. Worded so the
# lab's known false-200 shapes are inside it: an empty delegated section, a
# refusal, a stub, a "fetching that now" that never delivered.
STATEMENT = (
    "The RESPONSE contains a substantive answer to the REQUEST — not an error, "
    "a refusal, an empty or placeholder reply, or a promise to answer later."
)

# Below this many characters a "completed" answer is not labelled yes: a
# one-word reply to a real question is too ambiguous for the wire to call.
MIN_ANSWER_CHARS = 20
# What Jev sees per side. Long briefs are the norm; their head is enough to
# judge "did an answer arrive", and the cap bounds the cost of a large run.
MAX_SIDE_CHARS = 3000

_FAILED_STATES = {"TASK_STATE_FAILED", "TASK_STATE_REJECTED", "TASK_STATE_CANCELED"}
_OPEN_STATES = {"TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_INPUT_REQUIRED"}
_QUESTION_KEYS = ("message", "situation", "question", "prompt")
_ANSWER_KEYS = ("text", "brief", "answer", "output")


def _decode(raw: Any) -> Any:
    """JSON-decode up to twice — some hops recorded an already-encoded body."""
    for _ in range(2):
        if not isinstance(raw, str):
            break
        stripped = raw.strip()
        if not stripped or stripped[0] not in '{["':
            break
        try:
            raw = json.loads(stripped)
        except ValueError:
            break
    return raw


def _parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    out = []
    for p in parts:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and isinstance(p.get("text"), str):
            out.append(p["text"])
    return "\n".join(t for t in out if t)


def _find_question(obj: Any, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(obj, dict):
        for key in _QUESTION_KEYS:
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                # Agentforce Agent API: {"message": {"type": "Text", "text": ...}}
                if isinstance(value.get("text"), str) and value["text"].strip():
                    return value["text"]
                # A2A: params.message.parts[]
                text = _parts_text(value.get("parts"))
                if text:
                    return text
        for value in obj.values():
            found = _find_question(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_question(value, depth + 1)
            if found:
                return found
    return ""


def _find_state(obj: Any, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(obj, dict):
        state = obj.get("state")
        if isinstance(state, str) and state.startswith("TASK_STATE_"):
            return state
        for value in obj.values():
            found = _find_state(value, depth + 1)
            if found:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = _find_state(value, depth + 1)
            if found:
                return found
    return ""


def _find_answer(obj: Any, depth: int = 0) -> str:
    if depth > 6:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        return "\n".join(t for t in (_find_answer(v, depth + 1) for v in obj) if t)
    if not isinstance(obj, dict):
        return ""
    for key in _ANSWER_KEYS:
        value = obj.get(key)
        if isinstance(value, str) and value.strip():
            return value
    if "artifacts" in obj:  # A2A task: artifacts are strings or {parts: [...]}
        texts = []
        for art in obj.get("artifacts") or []:
            texts.append(art if isinstance(art, str) else _parts_text((art or {}).get("parts")))
        joined = "\n".join(t for t in texts if t)
        if joined:
            return joined
    if isinstance(obj.get("messages"), list):  # Agentforce Agent API
        joined = "\n".join(
            m.get("message", "") for m in obj["messages"] if isinstance(m, dict)
        ).strip()
        if joined:
            return joined
    if isinstance(obj.get("content"), list):  # MCP tool result
        joined = _parts_text(obj["content"])
        if joined:
            return joined
    for key in ("result", "task", "status", "message", "structuredContent"):
        if key in obj:
            found = _find_answer(obj[key], depth + 1)
            if found:
                return found
    if isinstance(obj.get("parts"), list):
        return _parts_text(obj["parts"])
    return ""


@dataclass
class Item:
    """One judged hop. `label` is 1 (answer arrived), 0 (it did not)."""

    # trace_id:hop_seq:content-hash of the first occurrence. The hash is part of
    # the id because (trace_id, hop_seq) is NOT unique in lab.db — one hop can be
    # recorded more than once with different bodies (a pending row and its
    # completion) — and the answer cache is keyed by id.
    id: str
    target: str
    protocol: str
    status: str
    question: str
    answer: str
    label: int
    reason: str  # why the wire says so: hop-error | failed-task | ... | completed
    count: int = 1  # identical (question, answer) pairs collapsed into one item

    @property
    def state(self) -> str:
        """What Jev sees: text only, never the status column."""
        return (
            f"REQUEST:\n{self.question[:MAX_SIDE_CHARS]}\n\n"
            f"RESPONSE:\n{self.answer[:MAX_SIDE_CHARS] or '(empty)'}"
        )

    def to_dict(self) -> dict:
        return asdict(self)


def wire_label(status: str, response: Any, answer: str) -> tuple[int | None, str]:
    """(label, reason). label None = nothing for the wire to judge: exclude."""
    if status == "pending":
        return None, "pending"
    state = _find_state(response)
    if status == "error":
        return 0, "hop-error"
    if state in _FAILED_STATES:
        return 0, "failed-task"
    if isinstance(response, dict) and response.get("error"):
        return 0, "error-body"
    if state in _OPEN_STATES:
        return None, "open-task"  # a submit ack has no answer by design
    if "[leg unavailable" in answer:
        return 0, "leg-unavailable"
    if not answer.strip():
        return 0, "empty-answer"
    if len(answer.strip()) < MIN_ANSWER_CHARS:
        return None, "too-short"
    return 1, "completed"


def extract(rows: list[dict]) -> tuple[list[Item], dict[str, int]]:
    """rows: trace_events dicts. Returns (deduplicated items, exclusion counts)."""
    items: dict[str, Item] = {}
    excluded: dict[str, int] = {}
    for row in rows:
        request = _decode(row.get("request_payload_raw"))
        response = _decode(row.get("response_payload_raw"))
        question = _find_question(request).strip()
        if not question:
            excluded["no-question"] = excluded.get("no-question", 0) + 1
            continue
        answer = _find_answer(response).strip()
        label, reason = wire_label(row.get("status") or "", response, answer)
        if label is None:
            excluded[reason] = excluded.get(reason, 0) + 1
            continue
        key = hashlib.sha256(f"{question}\x00{answer}".encode()).hexdigest()
        if key in items:
            items[key].count += 1
            continue
        items[key] = Item(
            id=f"{row.get('trace_id')}:{row.get('hop_seq')}:{key[:8]}",
            target=row.get("target") or "",
            protocol=row.get("protocol") or "",
            status=row.get("status") or "",
            question=question,
            answer=answer,
            label=label,
            reason=reason,
        )
    return list(items.values()), excluded


# --------------------------------------------------------------------- metrics


@dataclass
class Report:
    n: int
    positives: int
    brier: float
    ece: float
    accuracy: float  # at 0.5
    band_coverage: float  # share of items with p >= hi or p <= lo
    band_accuracy: float | None  # accuracy among those (None if none acted)
    reliability: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def report(ps: list[float], ys: list[int], *, bins: int = 10, hi=0.9, lo=0.1) -> Report:
    """Calibration of probabilities `ps` against 0/1 labels `ys`."""
    if len(ps) != len(ys):
        raise ValueError("ps and ys differ in length")
    n = len(ps)
    if n == 0:
        return Report(0, 0, math.nan, math.nan, math.nan, 0.0, None)
    brier = sum((p - y) ** 2 for p, y in zip(ps, ys)) / n
    accuracy = sum((p >= 0.5) == bool(y) for p, y in zip(ps, ys)) / n
    rows, ece = [], 0.0
    for b in range(bins):
        lo_edge, hi_edge = b / bins, (b + 1) / bins
        idx = [
            i for i, p in enumerate(ps) if lo_edge <= p < hi_edge or (b == bins - 1 and p == 1.0)
        ]
        if not idx:
            continue
        mean_p = sum(ps[i] for i in idx) / len(idx)
        rate = sum(ys[i] for i in idx) / len(idx)
        ece += abs(mean_p - rate) * len(idx) / n
        rows.append(
            {
                "bin": f"{lo_edge:.1f}-{hi_edge:.1f}",
                "n": len(idx),
                "mean_p": round(mean_p, 3),
                "observed": round(rate, 3),
            }
        )
    acted = [(p, y) for p, y in zip(ps, ys) if p >= hi or p <= lo]
    band_accuracy = sum((p >= hi) == bool(y) for p, y in acted) / len(acted) if acted else None
    return Report(
        n=n,
        positives=sum(ys),
        brier=round(brier, 4),
        ece=round(ece, 4),
        accuracy=round(accuracy, 4),
        band_coverage=round(len(acted) / n, 4),
        band_accuracy=None if band_accuracy is None else round(band_accuracy, 4),
        reliability=rows,
    )


def disagreements(items: list[Item], ps: list[float]) -> list[tuple[Item, float]]:
    """Items where the probability and the wire label point opposite ways,
    most confident first — the list the operator adjudicates."""
    out = [(it, p) for it, p in zip(items, ps) if (p >= 0.5) != bool(it.label)]
    return sorted(out, key=lambda pair: -abs(pair[1] - 0.5))
