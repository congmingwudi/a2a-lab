"""WS32: the POS order emitter and the graph builder that hangs orders off the customer."""

import json
import sys
from pathlib import Path

import httpx
import pytest

import interop.pos as pos

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))


def test_new_order_keys_on_contact_and_fits_schema():
    row = pos.new_order("003TEST0000000001", amount=12.5, product_sku="SKU-9")
    assert row["contact_id"] == "003TEST0000000001"
    assert row["order_id"].startswith("pos-")
    assert row["amount"] == 12.5 and row["product_sku"] == "SKU-9"
    assert set(row) == {n for n, _ in pos.FIELDS}


def test_new_order_refuses_unknown_fields_and_missing_owner():
    with pytest.raises(pos.PosError, match="not in the pos_order schema"):
        pos.new_order("003TEST0000000001", loyalty_tier="gold")
    with pytest.raises(pos.PosError, match="Contact id"):
        pos.new_order("")


async def test_send_posts_to_the_ingest_source_and_records_a_hop(monkeypatch):
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        seen["auth"] = req.headers["authorization"]
        return httpx.Response(202, json={"accepted": True})

    client = pos.PosClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def fake_token():
        return "tok", "https://tenant.example"

    monkeypatch.setattr(client, "_token", fake_token)
    row = pos.new_order("003TEST0000000001", unified_individual_id="u-1")
    res = await client.send("trace-1", [row])
    assert res["status"] == 202
    assert seen["url"] == "https://tenant.example/api/v1/ingest/sources/A2ALab_POS/pos_purchase"
    assert seen["body"] == {"data": [row]} and seen["auth"] == "Bearer tok"


async def test_send_resolves_the_unified_id_per_row(monkeypatch):
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path == "/api/v2/query":
            assert "SourceRecordId__c = '003TEST000000001AB'" in json.loads(req.content)["sql"]
            return httpx.Response(200, json={"data": [["unified-abc"]]})
        return httpx.Response(202, json={"accepted": True})

    client = pos.PosClient(httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    async def fake_token():
        return "tok", "https://tenant.example"

    monkeypatch.setattr(client, "_token", fake_token)
    rows = [pos.new_order("003TEST000000001AB"), pos.new_order("003TEST000000001AB")]
    await client.send("trace-3", rows)
    assert [r["unified_individual_id"] for r in rows] == ["unified-abc", "unified-abc"]
    assert calls.count("/api/v2/query") == 2  # per row, never cached


async def test_unified_id_refuses_a_non_id():
    client = pos.PosClient(httpx.AsyncClient(transport=httpx.MockTransport(lambda r: None)))
    with pytest.raises(pos.PosError, match="not a Salesforce id"):
        await client.unified_id("x' OR 1=1 --")


async def test_send_raises_on_refusal(monkeypatch):
    client = pos.PosClient(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(400, text="no")))
    )

    async def fake_token():
        return "tok", "https://tenant.example"

    monkeypatch.setattr(client, "_token", fake_token)
    with pytest.raises(pos.PosError, match="HTTP 400"):
        await client.send(
            "trace-2", [pos.new_order("003TEST0000000001", unified_individual_id="u")]
        )


def test_graph_hangs_pos_under_the_source_individual(monkeypatch):
    monkeypatch.chdir(Path(__file__).parents[2])
    import setup_pos_stream as s

    g = s.build_graph()
    assert g["name"] == pos.GRAPH

    def find(node, name):
        if node["name"] == name:
            return node
        for c in node.get("relatedObjects") or []:
            if hit := find(c, name):
                return hit
        return None

    ind = find(g["sourceObject"], s.INDIVIDUAL)
    node = find(ind, pos.DMO)
    assert node["path"] == [{"fieldName": "contact_id__c", "parentFieldName": "ssot__Id__c"}]
    projected = {f["sourceFieldName"] for f in node["fields"]}
    assert projected == {f"{n}__c" for n, _ in pos.FIELDS} | {"KQ_order_id__c", "KQ_contact_id__c"}
    join = next(f for f in node["fields"] if f["sourceFieldName"] == "contact_id__c")
    assert join["keyQualifierName"] == "KQ_contact_id__c"
    assert {r["valueType"] for r in node["recencyCriteria"]} == {"time", "record"}
