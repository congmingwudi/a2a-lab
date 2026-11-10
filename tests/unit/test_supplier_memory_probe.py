"""Regression tests for the D102 live provenance decision instrument."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import supplier_memory_probe as probe  # noqa: E402


class Memory:
    async def start_conversation(self, trace_id, **kwargs):
        return "sid"


@pytest.mark.asyncio
async def test_probe_uses_unique_case_and_waits_past_stale_context(monkeypatch, capsys):
    mem = Memory()
    monkeypatch.setattr(probe, "CaseMemory", lambda: SimpleNamespace(client=mem))
    monkeypatch.setattr(probe, "write_turn", _write_ok)
    seen = []

    async def read(mem, trace_id, **kwargs):
        seen.append(kwargs["conversation_id"])
        if len(seen) == 1:
            return None, "old context without this run", []
        observation_id = capsys.readouterr().out.split("observation    ", 1)[1].splitlines()[0]
        return None, f"{observation_id} Logistics provenance probe", []

    monkeypatch.setattr(probe, "open_and_read", read)
    rc = await probe.run(
        SimpleNamespace(owner="a2alab-review", case=None, apply=True, timeout=0.2, interval=0.001)
    )
    assert rc == 0
    assert len(seen) == 2
    assert len(set(seen)) == 1


@pytest.mark.asyncio
async def test_probe_reports_no_go_only_at_deadline(monkeypatch):
    monkeypatch.setattr(
        probe, "CaseMemory", lambda: SimpleNamespace(client=Memory())
    )
    monkeypatch.setattr(probe, "write_turn", _write_ok)
    calls = 0

    async def read(mem, trace_id, **kwargs):
        nonlocal calls
        calls += 1
        return None, "semantic content but no exact provenance", []

    monkeypatch.setattr(probe, "open_and_read", read)
    rc = await probe.run(
        SimpleNamespace(owner="a2alab-review", case="explicit", apply=True, timeout=0.01,
                        interval=0.001)
    )  # fmt: skip
    assert rc == 1
    assert calls > 1


async def _write_ok(*args, **kwargs):
    return [], False
