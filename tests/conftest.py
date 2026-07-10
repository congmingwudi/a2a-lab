import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest

from interop import trace as trace_mod


@pytest.fixture(autouse=True)
def isolated_traces(tmp_path, monkeypatch):
    """Every test writes traces to its own temp dir."""
    trace_dir = tmp_path / "traces"
    monkeypatch.setenv(trace_mod.TRACE_DIR_ENV, str(trace_dir))
    recorder = trace_mod.TraceRecorder(trace_dir)
    trace_mod.set_recorder(recorder)
    yield trace_dir
    trace_mod.set_recorder(None)
