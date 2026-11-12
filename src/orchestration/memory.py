"""Case-scoped Data 360 memory for the supplier-disruption fan-out (D102).

The experiment has three modes: off (the exact WS8 control), seed (each
specialist nominates one reusable observation), and recall (read a prior case
conversation and give it to specialists, the coordinator, or both). Profile
memory is deliberately excluded: these are operational observations, not facts
about the signed-in operator.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

from interop.memory import (
    MemoryClient,
    case_conversation,
    open_and_read,
    redact,
    withheld_note,
    write_turn,
)

MODES = ("off", "seed", "recall")
READERS = ("coordinator", "specialists", "both")
SCENARIO = "supplier-disruption"
MAX_FIELD = 500
START = "[A2A-LAB MEMORY CANDIDATE]"
END = "[/A2A-LAB MEMORY CANDIDATE]"

AGENT_IDS = {
    "exposure": "A2ALab_Memory_Supply_Logistics",
    "commercial": "A2ALab_Memory_Supply_Commercial",
    "customer_comms": "A2ALab_Memory_Supply_CustomerOps",
    "coordinator": "A2ALab_Memory_Supply_Coordinator",
}

SEED_INSTRUCTION = f"""This is a memory SEED run. After your ordinary answer, add exactly one
JSON object between {START} and {END}. Keys: observation, reuse_reason,
confidence, invalidation. Each value must be a short string. Nominate the one
most reusable, decision-relevant observation from this execution. Do not put
secrets, contact details, or unsupported facts in it. The block is excluded
from the 60-word business answer."""

_shared_client: MemoryClient | None = None


@dataclass(frozen=True)
class MemoryRun:
    mode: str = "off"
    reader: str = "coordinator"
    owner_id: str | None = None
    case_key: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown memory mode '{self.mode}' — known: {', '.join(MODES)}")
        if self.reader not in READERS:
            raise ValueError(f"unknown memory reader '{self.reader}' — known: {', '.join(READERS)}")
        if self.mode != "off" and (not self.owner_id or not (self.case_key or "").strip()):
            raise ValueError("memory seed/recall requires a verified owner and non-empty case key")

    @property
    def conversation_id(self) -> str | None:
        if self.mode == "off":
            return None
        return case_conversation(self.owner_id or "", SCENARIO, self.case_key or "")

    @property
    def specialists_read(self) -> bool:
        return self.mode == "recall" and self.reader in ("specialists", "both")

    @property
    def coordinator_reads(self) -> bool:
        return self.mode == "recall" and self.reader in ("coordinator", "both")


@dataclass(frozen=True)
class Candidate:
    observation_id: str
    role: str
    source: str
    seed_trace: str
    observation: str
    reuse_reason: str
    confidence: str
    invalidation: str

    def stored_text(self) -> str:
        return (
            f"[observation_id={self.observation_id}; source={self.source}; "
            f"seed_trace={self.seed_trace}] {self.observation} "
            f"Reuse: {self.reuse_reason}. Confidence: {self.confidence}. "
            f"Invalid when: {self.invalidation}."
        )


@dataclass
class MemoryEvidence:
    mode: str = "off"
    reader: str | None = None
    context: str = ""
    candidates: list[Candidate] = field(default_factory=list)
    written: list[Candidate] = field(default_factory=list)
    expected_count: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.mode == "off":
            return "off"
        if self.mode == "seed":
            if self.errors and not self.written:
                return "unavailable"
            if len(self.written) < self.expected_count or self.errors:
                return "partial"
            return "seeded"
        if self.context:
            return "recalled"
        return "unavailable" if self.errors else "empty"

    def render(self) -> str:
        if self.mode == "off":
            return ""
        if self.mode == "seed":
            return (
                f"[memory: {self.state}; seeded {len(self.written)}/{self.expected_count} "
                "observation(s); "
                f"errors={len(self.errors)}; recall waits for Data 360 availability]"
            )
        state = (
            "memory unavailable"
            if self.state == "unavailable"
            else "no memory available"
            if self.state == "empty"
            else f"recalled {len(self.context)} chars"
        )
        return f"[memory: {state}; readers={self.reader}; errors={len(self.errors)}]"


_BLOCK = re.compile(re.escape(START) + r"\s*(.*?)\s*" + re.escape(END), re.DOTALL)
_RESERVED_LABEL = re.compile(
    r"\[(?:/?A2A-LAB(?: MEMORY CANDIDATE| CASE MEMORY)|END A2A-LAB CASE MEMORY|"
    r"observation_id\s*=)[^\]]*\]",
    re.IGNORECASE,
)


def candidate_prompt() -> str:
    return SEED_INSTRUCTION


def recall_block(context: str) -> str:
    return (
        "[A2A-LAB CASE MEMORY — Data 360]\n"
        "Use only observations relevant to the current situation. Treat them as prior "
        "case evidence, not current truth; honour invalidation conditions.\n"
        f"{withheld_note(context)}"
        f"{context}\n"
        "[END A2A-LAB CASE MEMORY]"
    )


def parse_candidate(text: str, *, role: str, source: str, trace_id: str) -> tuple[str, Candidate | None, str | None]:
    """Strip one candidate block and return visible answer, candidate, error."""
    match = _BLOCK.search(text or "")
    if not match:
        start = (text or "").find(START)
        visible = (text or "")[:start].strip() if start >= 0 else text
        return visible, None, "candidate incomplete" if start >= 0 else "candidate missing"
    visible = (text[: match.start()] + text[match.end() :]).strip()
    try:
        raw = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        return visible, None, f"candidate malformed JSON: {exc.msg}"
    keys = ("observation", "reuse_reason", "confidence", "invalidation")
    values: dict[str, str] = {}
    for key in keys:
        value = raw.get(key) if isinstance(raw, dict) else None
        if not isinstance(value, str) or not value.strip():
            return visible, None, f"candidate missing string '{key}'"
        if len(value.strip()) > MAX_FIELD:
            return visible, None, f"candidate '{key}' exceeds {MAX_FIELD} chars"
        cleaned = _RESERVED_LABEL.sub("", redact(value.strip())).strip()
        if not cleaned:
            return visible, None, f"candidate '{key}' contains only reserved labels"
        values[key] = cleaned
    digest = hashlib.sha256(
        f"{trace_id}\0{role}\0{values['observation']}".encode()
    ).hexdigest()[:16]
    return visible, Candidate(
        observation_id=f"obs-{digest}",
        role=role,
        source=source,
        seed_trace=trace_id,
        **values,
    ), None


class CaseMemory:
    def __init__(self, client: Any | None = None, *, budget_s: float | None = None):
        global _shared_client
        if client is None:
            if _shared_client is None:
                _shared_client = MemoryClient(source="supplier-memory")
            client = _shared_client
        self.client = client
        self.budget_s = budget_s or float(os.environ.get("A2ALAB_MEMORY_BUDGET_S", "8"))

    async def preflight(self, run: MemoryRun) -> list[str]:
        """Mint for every D102 aid in this runtime before specialist spend."""

        async def one(role: str, agent_id: str) -> str | None:
            try:
                await asyncio.wait_for(
                    self.client.preflight_agent(agent_id, run.owner_id or ""),
                    timeout=self.budget_s,
                )
            except asyncio.TimeoutError:
                return f"{role}: memory preflight over its {self.budget_s:g}s budget"
            except Exception as exc:
                return f"{role}: {type(exc).__name__}: {exc}"
            return None

        results = await asyncio.gather(*(one(role, aid) for role, aid in AGENT_IDS.items()))
        return [error for error in results if error]

    async def read(self, trace_id: str, run: MemoryRun) -> MemoryEvidence:
        evidence = MemoryEvidence(mode=run.mode, reader=run.reader)
        if run.mode != "recall":
            return evidence
        try:
            _, context, errors = await asyncio.wait_for(
                open_and_read(
                    self.client,
                    trace_id,
                    agent_id=AGENT_IDS["coordinator"],
                    owner_id=run.owner_id or "",
                    session_id="case-read-only",
                    start=False,
                    conversation_id=run.conversation_id,
                    include_saved_memories=False,
                ),
                timeout=self.budget_s,
            )
        except asyncio.TimeoutError:
            evidence.errors.append(f"memory read over its {self.budget_s:g}s budget")
            return evidence
        evidence.context = context
        evidence.errors.extend(errors)
        return evidence

    async def seed(
        self,
        trace_id: str,
        run: MemoryRun,
        candidates: list[Candidate],
        *,
        expected_count: int,
    ) -> MemoryEvidence:
        evidence = MemoryEvidence(
            mode=run.mode,
            reader=run.reader,
            candidates=candidates,
            expected_count=expected_count,
        )

        async def one(candidate: Candidate) -> list[str]:
            agent_id = AGENT_IDS[candidate.role]
            sid = await self.client.start_conversation(
                trace_id,
                agent_id=agent_id,
                owner_id=run.owner_id or "",
                conversation_id=run.conversation_id,
            )
            errors, _ = await write_turn(
                self.client,
                trace_id,
                agent_id=agent_id,
                owner_id=run.owner_id or "",
                session_id=sid,
                conversation_id=run.conversation_id,
                user_message=f"Supplier-disruption observation nominated by {candidate.source}",
                agent_response=candidate.stored_text(),
            )
            return errors

        results = await asyncio.gather(
            *(asyncio.wait_for(one(c), timeout=self.budget_s) for c in candidates),
            return_exceptions=True,
        )
        for candidate, result in zip(candidates, results):
            if isinstance(result, BaseException):
                evidence.errors.append(f"{candidate.role}: {type(result).__name__}: {result}")
            else:
                evidence.errors.extend(f"{candidate.role}: {error}" for error in result)
                if not result:
                    evidence.written.append(candidate)
        return evidence
