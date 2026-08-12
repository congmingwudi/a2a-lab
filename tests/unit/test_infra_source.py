"""Unit tests for the Track B cross-cloud infrastructure metrics harvester
(plan/explore-moirai-timeseries-forecasting.md).

No cloud calls: the pure normalize_* functions are tested against canned API
payloads, the store round-trip proves idempotent upsert, and the AWS ok-path
runs against an injected CloudWatch client. GCP/Azure ok-paths are exercised at
the normalize level (their harvest() wiring is a thin call around the same pure
function + an AuthorizedSession/MetricsQueryClient we do not stub here).
"""

from __future__ import annotations

import datetime as dt

from observability.infra_source import (
    AwsInfraSource,
    AzureInfraSource,
    GcpInfraSource,
    load_infra_config,
    normalize_azure_metrics,
    normalize_cloudwatch,
    normalize_gcp_timeseries,
)
from observability.store import ObsStore


def make_store(tmp_path):
    return ObsStore(tmp_path / "lab.db")


# --------------------------------------------------------------------------- #
# store round-trip
# --------------------------------------------------------------------------- #


def test_upsert_metrics_round_trip_and_idempotent(tmp_path):
    store = make_store(tmp_path)
    rows = [
        {
            "cloud": "aws",
            "resource": "obs-aurora",
            "metric": "ACUUtilization",
            "ts_at": "2026-08-11T00:00:00Z",
            "value": 12.5,
            "unit": "Percent",
            "labels": {"stat": "Average"},
        },
        {
            "cloud": "aws",
            "resource": "obs-aurora",
            "metric": "ACUUtilization",
            "ts_at": "2026-08-11T00:05:00Z",
            "value": 13.0,
            "unit": "Percent",
            "labels": {"stat": "Average"},
        },
    ]
    assert store.upsert_metrics(rows) == 2

    # Re-harvest of an overlapping window UPDATES in place, never duplicates.
    store.upsert_metrics([{**rows[0], "value": 99.0}])
    got = store._conn.execute(
        "SELECT value, labels_json FROM infra_metrics ORDER BY ts_at"
    ).fetchall()
    assert len(got) == 2
    assert got[0]["value"] == 99.0
    assert '"stat": "Average"' in got[0]["labels_json"]


def test_upsert_metrics_null_labels(tmp_path):
    store = make_store(tmp_path)
    store.upsert_metrics(
        [
            {
                "cloud": "gcp",
                "resource": "agent-engine",
                "metric": "x/cpu",
                "ts_at": "2026-08-11T00:00:00Z",
                "value": 1.0,
            }
        ]
    )
    row = store._conn.execute("SELECT unit, labels_json FROM infra_metrics").fetchone()
    assert row["unit"] is None and row["labels_json"] is None


# --------------------------------------------------------------------------- #
# pure normalizers
# --------------------------------------------------------------------------- #


def test_normalize_cloudwatch_zips_parallel_arrays():
    results = [
        {
            "Id": "m0",
            "Timestamps": [
                dt.datetime(2026, 8, 11, 0, 0, tzinfo=dt.timezone.utc),
                dt.datetime(2026, 8, 11, 0, 5, tzinfo=dt.timezone.utc),
            ],
            "Values": [10.0, 20.0],
        },
        {"Id": "unknown", "Timestamps": [dt.datetime.now(dt.timezone.utc)], "Values": [1.0]},
    ]
    meta = {
        "m0": {
            "resource": "obs-aurora",
            "metric": "ACUUtilization",
            "unit": "Percent",
            "label": "Aurora",
            "stat": "Average",
            "dims": {"DBClusterIdentifier": "a2alab-obs"},
        }
    }
    rows = normalize_cloudwatch(results, meta)
    # The result whose Id is not in meta is dropped, not guessed.
    assert len(rows) == 2
    assert rows[0]["cloud"] == "aws"
    assert rows[0]["ts_at"] == "2026-08-11T00:00:00Z"
    assert rows[1]["value"] == 20.0
    assert rows[0]["labels"]["dims"]["DBClusterIdentifier"] == "a2alab-obs"


def test_normalize_gcp_prefers_int64_then_double():
    ts = [
        {
            "metric": {"type": "aiplatform/cpu", "labels": {"engine": "e1"}},
            "points": [
                {"interval": {"endTime": "2026-08-11T00:00:00Z"}, "value": {"int64Value": "5"}},
                {"interval": {"endTime": "2026-08-11T00:05:00Z"}, "value": {"doubleValue": 2.5}},
                {"value": {"doubleValue": 9.0}},  # no interval -> skipped
            ],
        }
    ]
    rows = normalize_gcp_timeseries(
        ts, {"resource": "agent-engine", "metric": "x", "unit": "u", "label": "AE"}
    )
    assert [r["value"] for r in rows] == [5.0, 2.5]
    assert rows[0]["metric"] == "aiplatform/cpu"
    assert rows[0]["labels"]["metric_labels"]["engine"] == "e1"


def test_normalize_azure_picks_the_configured_aggregation():
    metrics = [
        {
            "name": {"value": "Latency"},
            "timeseries": [
                {
                    "data": [
                        {"timestamp": "2026-08-11T00:00:00Z", "average": 120.0},
                        {"timestamp": "2026-08-11T00:05:00Z", "average": None},  # skipped
                    ]
                }
            ],
        },
        {"name": {"value": "Unconfigured"}, "timeseries": []},  # no meta -> dropped
    ]
    meta = {
        "Latency": {
            "resource": "foundry-project",
            "unit": "ms",
            "label": "F",
            "aggregation": "Average",
        }
    }
    rows = normalize_azure_metrics(metrics, meta)
    assert len(rows) == 1
    assert rows[0]["metric"] == "Latency" and rows[0]["value"] == 120.0
    assert rows[0]["cloud"] == "azure"


def test_normalize_azure_accepts_rest_timestamp_spelling():
    # The live path is the Monitor REST API, whose point key is `timeStamp`
    # (capital S) and whose aggregation fields are lowercase — distinct from
    # the SDK object model's `timestamp`. The same normalizer must serve both.
    metrics = [
        {
            "name": {"value": "SuccessfulCalls"},
            "timeseries": [
                {
                    "data": [
                        {"timeStamp": "2026-08-11T00:00:00Z", "total": 3.0, "average": 1.5},
                        {"timeStamp": "2026-08-11T00:05:00Z"},  # no total -> skipped
                    ]
                }
            ],
        }
    ]
    meta = {
        "SuccessfulCalls": {
            "resource": "foundry-project",
            "unit": "Count",
            "label": "F",
            "aggregation": "Total",
        }
    }
    rows = normalize_azure_metrics(metrics, meta)
    assert len(rows) == 1
    assert rows[0]["value"] == 3.0 and rows[0]["ts_at"] == "2026-08-11T00:00:00Z"


# --------------------------------------------------------------------------- #
# honest degradation
# --------------------------------------------------------------------------- #


def test_aws_blocked_when_ids_unset(tmp_path, monkeypatch):
    for var in (
        "A2ALAB_ECS_CLUSTER",
        "A2ALAB_CONSOLE_SERVICE",
        "A2ALAB_BRIDGE_SERVICE",
        "A2ALAB_PG_CLUSTER_ID",
        "A2ALAB_HARVEST_FUNCTION",
    ):
        monkeypatch.delenv(var, raising=False)
    result = AwsInfraSource().harvest(make_store(tmp_path))
    assert result.status == "blocked"
    assert "unset resource ids" in result.detail


def test_gcp_blocked_without_project(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    result = GcpInfraSource().harvest(make_store(tmp_path))
    assert result.status == "blocked"
    assert "GOOGLE_CLOUD_PROJECT" in result.detail


def test_azure_blocked_without_resource_uri(tmp_path, monkeypatch):
    monkeypatch.delenv("AZURE_FOUNDRY_RESOURCE_ID", raising=False)
    result = AzureInfraSource().harvest(make_store(tmp_path))
    assert result.status == "blocked"
    assert "resource_uri" in result.detail or "AZURE_FOUNDRY_RESOURCE_ID" in result.detail


# --------------------------------------------------------------------------- #
# AWS ok-path against an injected CloudWatch client
# --------------------------------------------------------------------------- #


class _FakeCw:
    """Echoes one datapoint per queried series id, so the harvest exercises
    the full query-build → GetMetricData → normalize → store path with no AWS."""

    def get_metric_data(self, **kw):
        results = []
        for q in kw["MetricDataQueries"]:
            results.append(
                {
                    "Id": q["Id"],
                    "Label": q["MetricStat"]["Metric"]["MetricName"],
                    "Timestamps": [dt.datetime(2026, 8, 11, 0, 0, tzinfo=dt.timezone.utc)],
                    "Values": [42.0],
                }
            )
        return {"MetricDataResults": results}


def test_aws_ok_path_writes_points(tmp_path, monkeypatch):
    # Resolve every dimension so no series is skipped.
    monkeypatch.setenv("A2ALAB_ECS_CLUSTER", "a2alab")
    monkeypatch.setenv("A2ALAB_CONSOLE_SERVICE", "a2alab-console")
    monkeypatch.setenv("A2ALAB_BRIDGE_SERVICE", "a2alab-bridge")
    monkeypatch.setenv("A2ALAB_PG_CLUSTER_ID", "a2alab-obs")
    monkeypatch.setenv("A2ALAB_HARVEST_FUNCTION", "a2alab-obs-harvest")

    store = make_store(tmp_path)
    result = AwsInfraSource(cw_client=_FakeCw()).harvest(store)
    assert result.status == "ok"
    # config/infra_metrics.yaml defines 8 aws series, all resolvable now.
    assert result.events == 8
    n = store._conn.execute("SELECT COUNT(*) AS n FROM infra_metrics WHERE cloud='aws'").fetchone()[
        "n"
    ]
    assert n == 8


def test_config_loads_all_three_clouds():
    cfg = load_infra_config()
    assert {"aws", "gcp", "azure"} <= set(cfg)
    assert cfg["defaults"]["period_s"] == 300
