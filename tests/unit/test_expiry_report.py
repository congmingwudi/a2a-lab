"""expiry_report: a credential the lab depends on must never vanish from the report.

The CloudWatch metrics API key (WS9) expired 2026-10-24 while the hosted report
read "0 expired" for nine days: the harvest Lambda was never given
A2ALAB_CW_METRICS_USER, and an unset user returned NO entry — indistinguishable
from "nothing to watch". An unconfigured watch is a finding, not an absence.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "expiry_report", Path(__file__).resolve().parents[2] / "scripts" / "expiry_report.py"
)
expiry_report = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(expiry_report)


def test_unset_metrics_user_is_reported_not_silently_dropped():
    entries = expiry_report.aws_service_credentials(None)
    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "error"
    assert "A2ALAB_CW_METRICS_USER" in entry["error"]
