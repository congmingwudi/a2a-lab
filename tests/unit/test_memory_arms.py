"""WS27 memory arms driver (scripts/memory_arms.py, D84/D92): the pure parts."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import memory_arms as ma  # noqa: E402
from setup_agentic_memory import AGENTS  # noqa: E402


def test_every_arm_is_a_registered_agent():
    assert set(ma.ARMS.values()) <= set(AGENTS)
    assert AGENTS[ma.ARMS["native-flag"]].name == "A2ALab_Memory_Native_Flag"
    assert AGENTS[ma.ARMS["native-action"]].name == "A2ALab_Memory_Native_Action"
    assert AGENTS[ma.ARMS["employee-action"]].name == "A2ALab_Memory_Employee_Action"
    assert ma.EMPLOYEE_ARMS <= set(ma.ARMS)


def test_planted_fact_is_run_unique_and_deterministic():
    a, wa = ma.planted("0a1b2c3d")
    assert ma.planted("0a1b2c3d") == (a, wa)
    assert len(wa) == 2 and all(w in a.lower() for w in wa)
    names = {tuple(ma.planted(f"{n:08x}")[1]) for n in range(64)}
    assert len(names) == 64  # every adjective x noun pair is reachable


def test_recall_needs_the_whole_name():
    _, words = ma.planted("00000009")  # cobalt lantern
    assert ma.recalled("- Your dinghy is the Cobalt Lantern\n- you race Sundays", words)
    assert not ma.recalled("You have a dinghy called Lantern.", words)
    assert not ma.recalled("I have nothing remembered yet.", words)


def test_candidate_owners_include_the_caller_user(monkeypatch):
    monkeypatch.setenv("SF_AGENT_CALLER_RUNAS_USER", "caller@example.invalid")
    monkeypatch.setattr(ma, "soql", lambda q: [{"Id": "005CALLER"}])
    owners = ma.candidate_owners("a2alab-arms-1", ["005CALLER", "003EXTRA"])
    assert owners == ["a2alab-arms-1", "caller@example.invalid", "005CALLER", "003EXTRA"]


def test_native_memory_reads_manage_user_memory_outputs(tmp_path):
    # The shape of a `sf agent preview end` planner trace (2026-10-29, D95).
    (tmp_path / "traces").mkdir()
    (tmp_path / "traces" / "plan.json").write_text(
        '{"output": {"userMemoryEnabled": true,\n'
        '  "wasMemoryOperationPerformed": true,\n'
        '  "userMemories": "{\\"memories\\":[{\\"content\\":\\"canoe is Probe\\"}],\\"count\\":1}",\n'
        '  "resultMessage": "I updated the user memory"}}'
    )
    [m] = ma.native_memory(str(tmp_path))
    assert m["userMemoryEnabled"] is True
    assert m["resultMessage"] == "I updated the user memory"
    assert "canoe is Probe" in m["userMemories"]
