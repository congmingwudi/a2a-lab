"""scripts/handoff.py — the baton between coding agents (D79 stations).

One JSON state file per batch under build-notes/handoffs/. The script owns the
transitions so no agent can take a turn it does not hold, and `status` tells
any agent — by its own name — whether it is its move and which files to read.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "handoff.py"


def run(*args, cwd, check=True):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"handoff {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
    return proc


@pytest.fixture
def repo(tmp_path):
    """A throwaway repo root: the script resolves build-notes/ from --root."""
    (tmp_path / "build-notes/claude/reviews").mkdir(parents=True)
    (tmp_path / "build-notes/codex/reviews").mkdir(parents=True)
    (tmp_path / "build-notes/claude/plan.md").write_text("# plan\n")
    return tmp_path


def state(repo, batch="ws99-b1"):
    return json.loads((repo / "build-notes/handoffs" / f"{batch}.json").read_text())


def test_propose_creates_the_batch_and_hands_to_the_other_agent(repo):
    run(
        "propose",
        "ws99-b1",
        "--agent",
        "claude",
        "--title",
        "t",
        "--file",
        "build-notes/claude/plan.md",
        "--root",
        str(repo),
        cwd=repo,
    )
    s = state(repo)
    assert s["station"] == "critique" and s["holder"] == "codex"
    assert s["files"]["plan"] == "build-notes/claude/plan.md"


def test_status_tells_an_agent_whether_it_is_its_move(repo):
    run(
        "propose",
        "ws99-b1",
        "--agent",
        "claude",
        "--title",
        "t",
        "--file",
        "build-notes/claude/plan.md",
        "--root",
        str(repo),
        cwd=repo,
    )
    mine = run("status", "ws99-b1", "--agent", "codex", "--root", str(repo), cwd=repo).stdout
    assert "YOUR MOVE" in mine and "build-notes/claude/plan.md" in mine
    theirs = run("status", "ws99-b1", "--agent", "claude", "--root", str(repo), cwd=repo).stdout
    assert "NOT YOUR MOVE" in theirs and "codex" in theirs


def test_an_agent_cannot_take_a_turn_it_does_not_hold(repo):
    run(
        "propose",
        "ws99-b1",
        "--agent",
        "claude",
        "--title",
        "t",
        "--file",
        "build-notes/claude/plan.md",
        "--root",
        str(repo),
        cwd=repo,
    )
    proc = run(
        "review",
        "ws99-b1",
        "--agent",
        "claude",
        "--verdict",
        "pass",
        "--file",
        "build-notes/claude/reviews/ws99-b1.md",
        "--root",
        str(repo),
        cwd=repo,
        check=False,
    )
    assert proc.returncode == 2 and "holds the baton" in proc.stderr
    assert state(repo)["holder"] == "codex"  # unchanged


def test_full_loop_propose_critique_settle_implement_review_respond_review_pass(repo):
    a = ["--root", str(repo)]
    run(
        "propose",
        "ws99-b1",
        "--agent",
        "claude",
        "--title",
        "t",
        "--file",
        "build-notes/claude/plan.md",
        *a,
        cwd=repo,
    )
    (repo / "build-notes/codex/reviews/ws99-b1.md").write_text("**Verdict: REQUEST CHANGES**\n")
    run(
        "review",
        "ws99-b1",
        "--agent",
        "codex",
        "--verdict",
        "request-changes",
        "--file",
        "build-notes/codex/reviews/ws99-b1.md",
        *a,
        cwd=repo,
    )
    assert state(repo)["station"] == "settle" and state(repo)["holder"] == "operator"
    run("settle", "ws99-b1", "--adr", "D80", "--implementer", "claude", *a, cwd=repo)
    assert state(repo)["station"] == "implement" and state(repo)["holder"] == "claude"
    (repo / "build-notes/claude/reviews/ws99-b1.md").write_text("# implementer notes\n")
    run(
        "built",
        "ws99-b1",
        "--agent",
        "claude",
        "--file",
        "build-notes/claude/reviews/ws99-b1.md",
        "--commit",
        "abc123",
        *a,
        cwd=repo,
    )
    s = state(repo)
    assert s["station"] == "review" and s["holder"] == "codex" and s["round"] == 1
    run(
        "review",
        "ws99-b1",
        "--agent",
        "codex",
        "--verdict",
        "request-changes",
        "--file",
        "build-notes/codex/reviews/ws99-b1.md",
        *a,
        cwd=repo,
    )
    assert state(repo)["station"] == "respond" and state(repo)["holder"] == "claude"
    run("respond", "ws99-b1", "--agent", "claude", "--commit", "def456", *a, cwd=repo)
    s = state(repo)
    assert s["station"] == "review" and s["holder"] == "codex" and s["round"] == 2
    run(
        "review",
        "ws99-b1",
        "--agent",
        "codex",
        "--verdict",
        "pass",
        "--file",
        "build-notes/codex/reviews/ws99-b1.md",
        *a,
        cwd=repo,
    )
    s = state(repo)
    assert s["station"] == "deploy" and s["holder"] == "operator" and s["verdict"] == "pass"
    run("deployed", "ws99-b1", "--note", "smoke green", *a, cwd=repo)
    assert state(repo)["station"] == "done"
    assert [h["action"] for h in state(repo)["history"]][:3] == ["propose", "review", "settle"]


def test_review_requires_the_verdict_line_in_the_file(repo):
    a = ["--root", str(repo)]
    run(
        "propose",
        "ws99-b1",
        "--agent",
        "claude",
        "--title",
        "t",
        "--file",
        "build-notes/claude/plan.md",
        *a,
        cwd=repo,
    )
    (repo / "build-notes/codex/reviews/ws99-b1.md").write_text("looks fine\n")
    proc = run(
        "review",
        "ws99-b1",
        "--agent",
        "codex",
        "--verdict",
        "pass",
        "--file",
        "build-notes/codex/reviews/ws99-b1.md",
        *a,
        cwd=repo,
        check=False,
    )
    assert proc.returncode == 2 and "Verdict:" in proc.stderr


def test_unknown_agent_is_refused_with_the_known_list(repo):
    proc = run("status", "--agent", "gemini", "--root", str(repo), cwd=repo, check=False)
    assert proc.returncode == 2 and "claude" in proc.stderr and "codex" in proc.stderr


def test_status_without_batch_lists_every_batch(repo):
    a = ["--root", str(repo)]
    for b in ("ws99-b1", "ws99-b2"):
        run(
            "propose",
            b,
            "--agent",
            "claude",
            "--title",
            b,
            "--file",
            "build-notes/claude/plan.md",
            *a,
            cwd=repo,
        )
    out = run("status", *a, cwd=repo).stdout
    assert "ws99-b1" in out and "ws99-b2" in out
