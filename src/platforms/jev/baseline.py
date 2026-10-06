"""The obvious alternative to Jev, asked the same questions (WS26, D82).

Both experiments compare Jev against "just ask a cheap LLM": a Haiku-tier model
given the same state and the same noul statements, told to answer with one
probability per statement as JSON. The comparison is the point — "is Jev
calibrated" only means something next to "is the alternative calibrated" — so
the frame is kept as close to Jev's as text allows: same statements, same 0–1
scale, no reasoning requested.

A probability an LLM writes is generated text, not a measurement. That is the
claim Jev is selling against, and this baseline is how the lab tests it rather
than repeating it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from interop.trace import Hop

MODEL_ENV = "A2ALAB_JEV_BASELINE_MODEL"
DEFAULT_MODEL = "claude-haiku-4-5"
TRACE_TARGET = "claude-haiku-baseline"

SYSTEM = (
    "You judge statements about a piece of text. For each named statement, give "
    "the probability (0 to 1) that it is true of the text. Reply with ONE JSON "
    "object mapping each statement name to a number, and nothing else."
)


class BaselineError(RuntimeError):
    pass


@dataclass
class BaselineResult:
    model: str
    probabilities: dict[str, float]
    usage: dict
    latency_ms: int = 0


def parse_probabilities(text: str, names: list[str]) -> dict[str, float]:
    """Pull `{name: p}` out of the model's reply. Tolerates a code fence or a
    sentence around the object; does NOT tolerate a missing name or a value
    outside 0–1 — those are errors, recorded as such, never coerced."""
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise BaselineError(f"no JSON object in reply: {text[:160]}")
    try:
        data = json.loads(match.group(0))
    except ValueError as exc:
        raise BaselineError(f"unparseable JSON in reply: {text[:160]}") from exc
    out: dict[str, float] = {}
    for name in names:
        value = data.get(name)
        if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
            raise BaselineError(f"no probability in 0–1 for '{name}': {text[:160]}")
        out[name] = float(value)
    return out


async def llm_noul(
    state: str,
    statements: dict[str, str],
    *,
    trace_id: str,
    source: str,
    client=None,
    model: str | None = None,
) -> BaselineResult:
    model = model or os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    if client is None:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic()
    listing = "\n".join(f"- {name}: {text}" for name, text in statements.items())
    user = f"TEXT:\n{state}\n\nSTATEMENTS:\n{listing}"
    request = {"model": model, "system": SYSTEM, "user": user}
    with Hop(
        trace_id,
        source=source,
        target=TRACE_TARGET,
        protocol="rest",
        transport_detail="POST /v1/messages (noul baseline)",
        request_payload=request,
    ) as hop:
        msg = await client.messages.create(
            model=model,
            max_tokens=200,
            temperature=0,
            system=SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(getattr(b, "text", "") for b in msg.content)
        hop.response_payload = text
        probabilities = parse_probabilities(text, list(statements))
    usage = getattr(msg, "usage", None)
    return BaselineResult(
        model=model,
        probabilities=probabilities,
        usage={
            "input_tokens": getattr(usage, "input_tokens", None),
            "output_tokens": getattr(usage, "output_tokens", None),
        },
        latency_ms=hop.event.latency_ms or 0,
    )
