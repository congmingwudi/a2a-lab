"""Thin async client for TypeSafe's System One API (WS26, D82).

One endpoint: `POST {base}/v1/systemone` with a bearer key, a `state` (the text
being judged) and named `questions`, each a `noul`, `choice` or `score`. The
response carries one typed answer per question plus token `usage`.

Plain httpx rather than the vendor SDK on purpose: the REST shape is the
documented contract (docs.typesafe.ai quickstart), the SDK's surface is not,
and an early-access SDK is one more thing to pin. The client is ~one function,
so owning it costs less than tracking someone else's.

Every call is a `Hop` under the caller's trace id (the trace-layer rule:
new code paths record trace events), so a gated fan-out shows its Jev decision
in the console beside the legs it chose to run. The bearer key is a header and
headers are never recorded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import httpx

from interop.trace import Hop

API_KEY_ENV = "TYPESAFE_API_KEY"
DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
# A System One call is meant to be fast. A gate that takes longer than this has
# stopped being cheaper than the legs it would skip, so the gate fails open.
DEFAULT_TIMEOUT_S = 15.0
TRACE_TARGET = "typesafe-jev"


class JevError(RuntimeError):
    """The call did not produce answers: missing key, HTTP error, bad body.

    One type on purpose — every caller in the lab has the same response to all
    of these (fail open and say so), so distinguishing them would only be
    branches that do the same thing.
    """


def question(kind: str, instructions: str, criteria: Any = None) -> dict[str, Any]:
    """Build one question in the wire shape. `criteria` is the option list for
    `choice` and the level list for `score`; `noul` takes none."""
    if kind not in ("noul", "choice", "score"):
        raise ValueError(f"unknown question type '{kind}' — known: noul, choice, score")
    q: dict[str, Any] = {"type": kind, "instructions": instructions}
    if criteria is not None:
        q["criteria"] = criteria
    return q


@dataclass
class Answer:
    """One typed answer. Exactly one of noul/choice/score is set, per `type`."""

    type: str
    noul: float | None = None
    choice: str | None = None
    score: float | None = None
    confidence: float | None = None
    probabilities: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> "Answer":
        if not isinstance(data, dict) or data.get("type") not in ("noul", "choice", "score"):
            raise JevError(f"malformed answer: {str(data)[:200]}")
        noul = data.get("noul")
        if data["type"] == "noul" and not isinstance(noul, (int, float)):
            raise JevError(f"noul answer without a probability: {str(data)[:200]}")
        return cls(
            type=data["type"],
            noul=float(noul) if isinstance(noul, (int, float)) else None,
            choice=data.get("choice"),
            score=data.get("score"),
            confidence=data.get("confidence"),
            probabilities=dict(data.get("probabilities") or {}),
        )


@dataclass
class Result:
    model: str
    answers: dict[str, Answer]
    usage: dict[str, Any]
    latency_ms: int = 0


class SystemOneClient:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        # Read at construction, not import — the lab's standing rule, because
        # scripts call load_dotenv() inside main() after importing (legs.py).
        self._api_key = api_key if api_key is not None else os.environ.get(API_KEY_ENV, "")
        self._base_url = (
            base_url or os.environ.get("TYPESAFE_BASE_URL") or DEFAULT_BASE_URL
        ).rstrip("/")
        self.model = model or os.environ.get("TYPESAFE_MODEL") or DEFAULT_MODEL
        self._timeout_s = timeout_s or DEFAULT_TIMEOUT_S
        self._transport = transport

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    async def ask(
        self,
        state: str,
        questions: dict[str, dict[str, Any]],
        *,
        trace_id: str,
        source: str,
        detail: str = "systemone",
    ) -> Result:
        if not self._api_key:
            raise JevError(f"{API_KEY_ENV} is not set — no Jev call made")
        body = {"state": state, "model": self.model, "questions": questions}
        with Hop(
            trace_id,
            source=source,
            target=TRACE_TARGET,
            protocol="rest",
            transport_detail=f"POST /v1/systemone ({detail})",
            request_payload=body,
        ) as hop:
            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout_s, transport=self._transport
                ) as http:
                    resp = await http.post(
                        f"{self._base_url}/v1/systemone",
                        json=body,
                        headers={"Authorization": f"Bearer {self._api_key}"},
                    )
            except httpx.HTTPError as exc:
                raise JevError(f"{type(exc).__name__}: {exc}"[:200]) from exc
            hop.response_payload = resp.text
            if resp.status_code >= 400:
                raise JevError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                data = resp.json()
            except ValueError as exc:
                raise JevError(f"non-JSON response: {resp.text[:200]}") from exc
            raw_answers = data.get("answers") if isinstance(data, dict) else None
            if not isinstance(raw_answers, dict):
                raise JevError(f"response has no answers: {resp.text[:200]}")
            answers = {name: Answer.from_wire(a) for name, a in raw_answers.items()}
            missing = set(questions) - set(answers)
            if missing:
                raise JevError(f"no answer for: {', '.join(sorted(missing))}")
        return Result(
            model=str(data.get("model") or self.model),
            answers=answers,
            usage=dict(data.get("usage") or {}),
            latency_ms=hop.event.latency_ms or 0,
        )
