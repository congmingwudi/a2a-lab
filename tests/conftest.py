import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from interop import trace as trace_mod


@pytest.fixture(autouse=True)
def isolated_traces(tmp_path, monkeypatch, request):
    """Every test writes traces to its own temp dir. Non-live tests also run
    with the lab's shared-secret env cleared, so a developer shell that has
    sourced .env (A2ALAB_TOKEN/BRIDGE_TOKEN exported) doesn't flip the auth
    middleware on under the unit suite.

    A2ALAB_MODE is cleared for the same reason and was the same bug: with
    `A2ALAB_MODE=hosted` exported, the registry remaps claude-rest ->
    claude-agentcore and two mode-sensitive tests fail with an assertion that
    says nothing about the cause. Sourcing .env before running pytest is a
    natural thing to do; the suite must describe the code, not the shell."""
    if "live" not in request.keywords:
        monkeypatch.delenv("A2ALAB_TOKEN", raising=False)
        monkeypatch.delenv("BRIDGE_TOKEN", raising=False)
        monkeypatch.delenv("A2ALAB_MODE", raising=False)
    trace_dir = tmp_path / "traces"
    monkeypatch.setenv(trace_mod.TRACE_DIR_ENV, str(trace_dir))
    recorder = trace_mod.TraceRecorder(trace_dir)
    trace_mod.set_recorder(recorder)
    yield trace_dir
    trace_mod.set_recorder(None)
