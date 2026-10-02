"""WS26 / D82 — the Jev System-One client, the fan-out gate (Experiment B) and
the calibration study's extraction + metrics (Experiment C). No network: Jev is
an httpx MockTransport, the business-unit legs are fakes."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from interop.models import AgentResponse
from interop.registry import Registry, Target
from orchestration import gate as gate_mod
from orchestration.gate import GateDecision, GateResult, band, gate_legs, thresholds, unit_question
from orchestration.gate_eval import Situation, aggregate, load_situations, score_situation
from orchestration.legs import LEGS
from orchestration.runner import dispatch
from platforms.jev import JevError, SystemOneClient, question
from platforms.jev.baseline import BaselineError, parse_probabilities
from platforms.jev.calibration import Item, disagreements, extract, report, wire_label

ROOT = Path(__file__).resolve().parents[2]


def _jev(handler) -> SystemOneClient:
    return SystemOneClient(
        "test-key", base_url="https://jev.test", transport=httpx.MockTransport(handler)
    )


def _answers(ps: dict[str, float]):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {k: {"type": "noul", "noul": p} for k, p in ps.items()},
                "usage": {"requests": 1},
            },
        )

    return handler


# ------------------------------------------------------------------- client


async def test_client_posts_state_and_questions_with_bearer():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return _answers({"q": 0.8})(request)

    res = await _jev(handler).ask(
        "the state", {"q": question("noul", "is it so")}, trace_id="t1", source="test"
    )
    assert seen["url"] == "https://jev.test/v1/systemone"
    assert seen["auth"] == "Bearer test-key"
    assert seen["body"]["state"] == "the state"
    assert seen["body"]["questions"]["q"] == {"type": "noul", "instructions": "is it so"}
    assert res.model == "jev-1.13.0"
    assert res.answers["q"].noul == 0.8


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"model": "x"}),
        httpx.Response(200, json={"answers": {"q": {"type": "noul"}}}),
        httpx.Response(200, json={"answers": {"other": {"type": "noul", "noul": 0.5}}}),
    ],
    ids=["http-500", "non-json", "no-answers", "noul-without-p", "missing-answer"],
)
async def test_client_raises_jev_error_on_every_bad_response(response):
    with pytest.raises(JevError):
        await _jev(lambda r: response).ask(
            "s", {"q": question("noul", "x")}, trace_id="t", source="test"
        )


async def test_client_without_a_key_makes_no_call(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    client = SystemOneClient(transport=httpx.MockTransport(lambda r: pytest.fail("called")))
    assert not client.configured
    with pytest.raises(JevError, match="not set"):
        await client.ask("s", {"q": question("noul", "x")}, trace_id="t", source="test")


async def test_client_failure_is_still_a_recorded_hop(isolated_traces):
    with pytest.raises(JevError):
        await _jev(lambda r: httpx.Response(503, text="down")).ask(
            "s", {"q": question("noul", "x")}, trace_id="t-hop", source="test"
        )
    events = [
        json.loads(line)
        for f in isolated_traces.glob("*.jsonl")
        for line in f.read_text().splitlines()
    ]
    hops = [e for e in events if e["trace_id"] == "t-hop"]
    assert hops and hops[-1]["target"] == "typesafe-jev"
    assert hops[-1]["status"] == "error"


# --------------------------------------------------------------------- gate


def test_bands_and_threshold_validation(monkeypatch):
    assert band(0.95, 0.9, 0.1) == "dispatch"
    assert band(0.05, 0.9, 0.1) == "skip"
    assert band(0.5, 0.9, 0.1) == "uncertain"
    monkeypatch.setenv("A2ALAB_JEV_GATE_DISPATCH", "0.8")
    monkeypatch.setenv("A2ALAB_JEV_GATE_SKIP", "0.2")
    assert thresholds() == (0.8, 0.2)
    monkeypatch.setenv("A2ALAB_JEV_GATE_SKIP", "0.9")
    with pytest.raises(ValueError):
        thresholds()


def test_unit_question_is_built_from_the_legs_own_remit():
    for leg in LEGS:
        q = unit_question(leg)
        assert leg.business_unit in q and leg.question in q


async def test_gate_decides_per_unit_and_renders_skips_by_name():
    roles = [leg.role for leg in LEGS]
    ps = dict(zip(roles, [0.97, 0.03, 0.5]))
    result = await gate_legs("t", LEGS, trace_id="t", source="test", client=_jev(_answers(ps)))
    assert [d.decision for d in result.decisions] == ["dispatch", "skip", "uncertain"]
    assert result.dispatch_roles == {roles[0], roles[2]}
    text = result.render()
    assert f"[leg skipped by gate: {LEGS[1].business_unit}" in text
    assert "dispatched despite uncertainty" in text
    assert LEGS[1].business_unit in result.summary()


async def test_gate_fails_open_and_says_so():
    client = _jev(lambda r: httpx.Response(503, text="down"))
    result = await gate_legs("t", LEGS, trace_id="t", source="test", client=client)
    assert not result.available
    assert result.dispatch_roles == {leg.role for leg in LEGS}
    assert "failed open" in result.render()


async def test_gate_without_a_key_fails_open(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    result = await gate_legs("t", LEGS, trace_id="t", source="test")
    assert not result.available and len(result.dispatch_roles) == len(LEGS)


# --------------------------------------------------------------- runner/cma


class _Leg:
    def __init__(self, name, sink):
        self.name, self.sink = name, sink

    async def ask(self, req):
        self.sink.append(self.name)
        return AgentResponse(text=f"{self.name} answer", session_id=req.session_id)

    async def aclose(self):
        pass


def _registry(sink):
    reg = Registry(
        {leg.target: Target(name=leg.target, platform=leg.platform, protocol="a2a") for leg in LEGS}
    )
    clients = {leg.target: _Leg(leg.target, sink) for leg in LEGS}
    reg.client_for = lambda name, exact=False: clients[name]  # type: ignore[method-assign]
    return reg


async def test_gated_dispatch_runs_only_the_kept_legs_and_names_the_rest(monkeypatch):
    ps = {LEGS[0].role: 0.99, LEGS[1].role: 0.02, LEGS[2].role: 0.01}
    monkeypatch.setattr(gate_mod, "SystemOneClient", lambda: _jev(_answers(ps)))
    sink = []
    result = await dispatch(
        "task", caller="orc", caller_platform="claude", registry=_registry(sink), gate="jev"
    )
    assert sink == [LEGS[0].target]
    text = result.render()
    for skipped in LEGS[1:]:
        assert f"[leg skipped by gate: {skipped.business_unit}" in text
    assert "2/3 units skipped" in text


async def test_ungated_dispatch_never_touches_jev(monkeypatch):
    monkeypatch.setattr(gate_mod, "SystemOneClient", lambda: pytest.fail("gate ran"))
    sink = []
    result = await dispatch(
        "task", caller="orc", caller_platform="claude", registry=_registry(sink)
    )
    assert len(sink) == len(LEGS) and result.gate is None


async def test_unknown_gate_is_a_programming_error():
    with pytest.raises(ValueError):
        await dispatch("t", caller="o", caller_platform="c", registry=_registry([]), gate="x")


def test_runner_import_does_not_pull_in_platforms():
    """The fan-out Lambda bundles src/orchestration, not src/platforms
    (deploy/fanout/build_zip.sh); runner must import without it."""
    code = (
        "import sys; sys.path.insert(0, 'src'); import orchestration.runner; "
        "assert not any(m.startswith('platforms') for m in sys.modules), "
        "[m for m in sys.modules if m.startswith('platforms')]"
    )
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True)


def test_cma_refuses_a_gate_under_the_mcp_variant():
    from orchestration.cma import CmaOrchestrator

    with pytest.raises(ValueError, match="tool"):
        CmaOrchestrator(client=object(), state={}, variant="mcp", gate="jev")


def test_scenario_opts_into_the_gate_toggle():
    spec = yaml.safe_load((ROOT / "config" / "scenarios.yaml").read_text())
    assert spec["scenarios"]["supplier-disruption-cma"].get("gate_toggle") is True


# ---------------------------------------------------------------- gate eval


def test_situations_file_is_well_formed():
    data = yaml.safe_load((ROOT / "config" / "jev_situations.yaml").read_text())
    situations = load_situations(data)
    roles = {leg.role for leg in LEGS}
    assert len({s.id for s in situations}) == len(situations)
    assert {s.kind for s in situations} == {"all-three", "one-unit", "two-unit", "none"}
    for s in situations:
        assert set(s.expected) <= roles, s.id


def test_gate_eval_counts_wrong_skips_by_name():
    s = Situation("x", "one-unit", "t", expected=["commercial"])
    rows = score_situation(
        s, {"commercial": 0.05, "exposure": 0.02, "customer_comms": 0.5}, 0.9, 0.1
    )
    total = aggregate(rows)
    assert (total.tp, total.fn, total.tn, total.fp) == (0, 1, 1, 1)
    assert total.wrong_skips[0].role == "commercial"
    assert total.recall == 0.0 and total.uncertain == 1
    assert total.legs_saved == pytest.approx(2 / 3)


# -------------------------------------------------------------- calibration


def _row(req, resp, status="ok", seq=1, target="t", protocol="a2a"):
    return {
        "trace_id": "tr",
        "hop_seq": seq,
        "target": target,
        "protocol": protocol,
        "status": status,
        "request_payload_raw": json.dumps(req),
        "response_payload_raw": resp if isinstance(resp, str) else json.dumps(resp),
    }


LONG = "Rotterdam is closed; three orders are exposed and need rerouting."


@pytest.mark.parametrize(
    "req,resp",
    [
        ({"message": "What is exposed?"}, {"text": LONG}),
        (
            {"message": {"type": "Text", "text": "What is exposed?"}},
            {"messages": [{"message": LONG}]},
        ),
        (
            {"params": {"message": {"parts": [{"text": "What is exposed?"}]}}},
            {"result": {"artifacts": [{"parts": [{"text": LONG}]}]}},
        ),
        (
            {"params": {"arguments": {"question": "What is exposed?"}}},
            {"result": {"content": [{"type": "text", "text": LONG}]}},
        ),
        ({"situation": "What is exposed?"}, json.dumps(json.dumps({"brief": LONG}))),
    ],
    ids=["rest", "agent-api", "a2a-task", "mcp", "double-encoded"],
)
def test_extract_finds_question_and_answer_in_every_wire_shape(req, resp):
    items, excluded = extract([_row(req, resp)])
    assert not excluded
    assert items[0].question == "What is exposed?"
    assert items[0].answer == LONG
    assert items[0].label == 1


def test_wire_labels():
    assert wire_label("error", "Timeout", "")[0] == 0
    assert wire_label("ok", {"status": {"state": "TASK_STATE_FAILED"}}, LONG) == (0, "failed-task")
    assert wire_label("ok", {"error": {"code": -1}}, LONG) == (0, "error-body")
    assert wire_label("ok", {"state": "TASK_STATE_SUBMITTED"}, "") == (None, "open-task")
    assert wire_label("ok", {}, "[leg unavailable: x]  " + LONG) == (0, "leg-unavailable")
    assert wire_label("ok", {}, "") == (0, "empty-answer")
    assert wire_label("ok", {}, "ok") == (None, "too-short")
    assert wire_label("pending", {}, LONG) == (None, "pending")
    assert wire_label("ok", {}, LONG) == (1, "completed")


def test_extract_dedupes_and_counts_exclusions():
    rows = [
        _row({"message": "q?"}, {"text": LONG}, seq=1),
        _row({"message": "q?"}, {"text": LONG}, seq=2),
        _row({"unrelated": 1}, {"text": LONG}, seq=3),
    ]
    items, excluded = extract(rows)
    assert len(items) == 1 and items[0].count == 2
    assert excluded == {"no-question": 1}


def test_item_ids_are_unique_even_when_a_hop_is_recorded_twice():
    rows = [
        _row({"message": "q?"}, {"state": "TASK_STATE_WORKING"}, seq=4),
        _row({"message": "q?"}, {"text": LONG}, seq=4),
        _row({"message": "q?"}, {"text": LONG + " More."}, seq=4),
    ]
    items, _ = extract(rows)
    assert len(items) == 2 and len({it.id for it in items}) == 2


def test_state_shows_text_never_status():
    item = Item("i", "t", "a2a", "error", "q?", "", 0, "hop-error")
    assert "error" not in item.state.lower().replace("(empty)", "")
    assert item.state.startswith("REQUEST:")


def test_report_metrics():
    perfect = report([1.0, 0.0, 1.0, 0.0], [1, 0, 1, 0])
    assert perfect.brier == 0 and perfect.ece == 0 and perfect.accuracy == 1
    assert perfect.band_coverage == 1 and perfect.band_accuracy == 1
    hedged = report([0.5] * 4, [1, 0, 1, 0])
    assert hedged.brier == 0.25 and hedged.band_coverage == 0 and hedged.band_accuracy is None
    with pytest.raises(ValueError):
        report([0.5], [])


def test_disagreements_are_most_confident_first():
    items = [Item(str(i), "t", "a2a", "ok", "q", LONG, 1, "completed") for i in range(3)]
    rows = disagreements(items, [0.9, 0.05, 0.3])
    assert [it.id for it, _ in rows] == ["1", "2"]


# ----------------------------------------------------------------- baseline


def test_baseline_parses_fenced_json_and_rejects_bad_values():
    assert parse_probabilities('```json\n{"a": 0.7, "b": 1}\n```', ["a", "b"]) == {
        "a": 0.7,
        "b": 1.0,
    }
    for bad in ('{"a": 1.4}', '{"b": 0.2}', "no json here", '{"a": "high"}'):
        with pytest.raises(BaselineError):
            parse_probabilities(bad, ["a"])


def test_gate_result_rendering_is_present_whenever_a_gate_ran():
    result = GateResult(decisions=[GateDecision(LEGS[0], 0.95, "dispatch")], model="jev-x")
    assert "0/1 units skipped" in result.render()
    assert result.summary() == "Jev gate skipped none"
