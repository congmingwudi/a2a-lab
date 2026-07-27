"""env_sync's safety rules — the ones that protect an uncommitted `.env`.

The AWS calls are mocked; what is tested here is the refusal logic, because a
tool that can silently overwrite the only copy of every platform credential is
a tool that eventually will.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("env_sync", REPO / "scripts" / "env_sync.py")
env_sync = importlib.util.module_from_spec(spec)
sys.modules["env_sync"] = env_sync
spec.loader.exec_module(env_sync)

REMOTE = "A=1\nB=2\n# comment\nC=3\n"
LOCAL_SAME = REMOTE
LOCAL_AHEAD = "A=1\nB=2\nC=3\nD=4\n"
LOCAL_BEHIND = "A=1\nB=2\n"


def _args(**kw):
    return SimpleNamespace(**{"force": False, "print": False, **kw})


def _use(monkeypatch, tmp_path, local: str | None, remote: str | None):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(env_sync, "ENV_PATH", tmp_path / ".env")
    if local is not None:
        (tmp_path / ".env").write_text(local)
    monkeypatch.setattr(env_sync, "read_secret", lambda name: remote)
    written = {}
    monkeypatch.setattr(
        env_sync, "write_secret", lambda name, body: written.setdefault("body", body) and "updated"
    )
    return written


def test_keys_of_ignores_comments_and_blanks():
    assert env_sync.keys_of(REMOTE) == {"A", "B", "C"}


def test_pull_refuses_to_clobber_a_diverged_env(monkeypatch, tmp_path, capsys):
    _use(monkeypatch, tmp_path, LOCAL_AHEAD, REMOTE)
    assert env_sync.cmd_pull("s", _args()) == 1
    err = capsys.readouterr().err
    assert "refusing to overwrite" in err
    assert "D" in err  # names the local-only key so the operator can judge
    assert (tmp_path / ".env").read_text() == LOCAL_AHEAD, "local .env was modified"


def test_pull_force_keeps_a_backup_first(monkeypatch, tmp_path):
    _use(monkeypatch, tmp_path, LOCAL_AHEAD, REMOTE)
    assert env_sync.cmd_pull("s", _args(force=True)) == 0
    assert (tmp_path / ".env").read_text() == REMOTE
    backups = list(tmp_path.glob(".bak-*")) + list(tmp_path.glob(".env.bak-*"))
    assert backups, "no backup kept before overwriting"
    assert backups[0].read_text() == LOCAL_AHEAD


def test_pull_is_a_no_op_when_identical(monkeypatch, tmp_path, capsys):
    _use(monkeypatch, tmp_path, LOCAL_SAME, REMOTE)
    assert env_sync.cmd_pull("s", _args()) == 0
    assert "already matches" in capsys.readouterr().out
    assert not list(tmp_path.glob(".env.bak-*")), "wrote a backup for a no-op"


def test_push_refuses_to_drop_keys_that_only_exist_remotely(monkeypatch, tmp_path, capsys):
    written = _use(monkeypatch, tmp_path, LOCAL_BEHIND, REMOTE)
    assert env_sync.cmd_push("s", _args()) == 1
    assert "refusing to push" in capsys.readouterr().err
    assert "body" not in written, "pushed anyway"


def test_push_allows_adding_keys(monkeypatch, tmp_path):
    written = _use(monkeypatch, tmp_path, LOCAL_AHEAD, REMOTE)
    assert env_sync.cmd_push("s", _args()) == 0
    assert written["body"] == LOCAL_AHEAD


def test_diff_never_prints_values(monkeypatch, tmp_path, capsys):
    _use(monkeypatch, tmp_path, LOCAL_AHEAD, REMOTE)
    assert env_sync.cmd_diff("s", _args()) == 0
    out = capsys.readouterr().out
    assert "only local : D" in out
    for secret_value in ("=1", "=2", "=3", "=4"):
        assert secret_value not in out, f"leaked a value: {out}"
