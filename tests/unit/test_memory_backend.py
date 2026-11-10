"""The Memory Back End reader (D115): stream/DLO naming, the hidden-stream
fallback, index shaping, and the console route's never-500 contract."""

import json

import httpx
from fastapi.testclient import TestClient

from observability import memory_backend as mb

DOMAIN = "https://example.my.salesforce.com"


def test_memory_object_strips_prefix_and_hash():
    assert mb.memory_object("AgenticConnector_AiAgentSession_7D8F75AF") == "AiAgentSession"
    assert mb.memory_object("Something_else") == "Something_else"


def test_dlo_object_disambiguates_truncated_names_by_field_prefix():
    # Three objects share the stem "AiAgentInterac"; the fields carry the full name.
    assert (
        mb.dlo_object(
            "AgenticConnector_AiAgentInterac_2CC81C4C__dll",
            ["AiAgentInteractionMessage_content__c", "cdp_sys_UserAgent__c"],
        )
        == "AiAgentInteractionMessage"
    )
    assert (
        mb.dlo_object(
            "AgenticConnector_AiAgentInterac_6CDB9F70__dll",
            ["AiAgentInteraction_topicApiName__c"],
        )
        == "AiAgentInteraction"
    )
    # Session vs SessionParticipant: the longest prefix some field carries wins.
    assert (
        mb.dlo_object(
            "AgenticConnector_AiAgentSession_694B4F40__dll",
            ["AiAgentSessionParticipant_userId__c"],
        )
        == "AiAgentSessionParticipant"
    )
    assert (
        mb.dlo_object("AgenticConnector_AiAgentSession_7D8F75AF__dll", ["AiAgentSession_userId__c"])
        == "AiAgentSession"
    )
    # A unique stem needs no fields.
    assert mb.dlo_object("AgenticConnector_AiParticipantI_1232197F__dll", []) == (
        "AiParticipantInsight"
    )


INDEX = {
    "developerName": "AI_Agent_Interaction_Message",
    "label": "AI Agent Interaction Message index",
    "searchType": "HYBRID",
    "runtimeStatus": "READY",
    "indexRefreshedOn": "2026-10-13T03:52:28.000Z",
    "sourceDmoDeveloperName": "ssot__AiAgentInteractionMessage__dlm",
    "chunkDmoDeveloperName": "AI_Agent_Interaction_Message_chunk__dlm",
    "vectorDmoDeveloperName": "AI_Agent_Interaction_Message_index__dlm",
    "chunkingConfiguration": {
        "fieldLevelConfigurations": [
            {
                "config": {"userValues": [{"id": "max_tokens", "value": "512"}]},
                "sourceDmoFieldDeveloperName": "ssot__ContentText__c",
            }
        ]
    },
    "vectorEmbeddingConfiguration": {
        "embeddingModel": {
            "id": "multilingual-e5-large",
            "userValues": [{"id": "dimension", "value": "1024"}],
        },
        "index": {
            "id": "HNSW",
            "userValues": [
                {"id": "hnswEfConstruction", "value": "2000"},
                {"id": "M", "value": "64"},
            ],
        },
        "similarityMetric": "COSINE",
    },
}


def test_shape_index_reads_embedding_and_hnsw_settings():
    i = mb.shape_index(INDEX)
    assert i["embedding_model"] == "multilingual-e5-large"
    assert (i["dimension"], i["chunk_tokens"], i["hnsw_m"]) == ("1024", "512", "64")
    assert i["source_fields"] == ["ssot__ContentText__c"]
    assert i["chunk_dmo"] == "AI_Agent_Interaction_Message_chunk__dlm"


def _transport(counts: dict, *, hide_streams: bool):
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/ssot/data-streams"):
            if hide_streams:
                return httpx.Response(200, json={"dataStreams": [], "totalSize": 52})
            return httpx.Response(
                200,
                json={
                    "dataStreams": [
                        {
                            "name": "AgenticConnector_AiAgentSession_7D8F75AF",
                            "dataLakeObjectInfo": {
                                "name": "AgenticConnector_AiAgentSession_7D8F75AF__dll"
                            },
                            "lastRunStatus": "SUCCESS",
                        },
                        {"name": "Unrelated_Stream", "dataLakeObjectInfo": {"name": "x__dll"}},
                    ]
                },
            )
        if path.endswith("/ssot/metadata"):
            return httpx.Response(
                200,
                json={
                    "metadata": [
                        {
                            "name": "AgenticConnector_AiAgentSession_694B4F40__dll",
                            "fields": [{"name": "AiAgentSessionParticipant_userId__c"}],
                        },
                        {"name": "PR_AgenticConnector_AiAgentSession_7D8F7__dll", "fields": []},
                    ]
                },
            )
        if path.endswith("/ssot/search-index"):
            other = {**INDEX, "developerName": "catalog", "sourceDmoDeveloperName": "Other__dlm"}
            return httpx.Response(200, json={"semanticSearchDefinitionDetails": [INDEX, other]})
        if path.endswith("/ssot/query"):
            table = json.loads(req.content)["sql"].rsplit(" ", 1)[-1]
            if table not in counts:
                return httpx.Response(400, text="no such table")
            return httpx.Response(201, json={"data": [{"n": counts[table]}]})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


COUNTS = {
    "AgenticConnector_AiAgentSession_7D8F75AF__dll": 0,
    "AgenticConnector_AiAgentSession_694B4F40__dll": 232,
    "ssot__AiAgentSession__dlm": 543,
    "ssot__AiAgentSessionParticipant__dlm": 2221,
    "AI_Agent_Interaction_Message_chunk__dlm": 0,
}


def _backend(hide_streams: bool) -> mb.MemoryBackend:
    http = httpx.Client(transport=_transport(COUNTS, hide_streams=hide_streams))
    return mb.MemoryBackend(http=http, token=lambda: (DOMAIN, "tok"))


def test_snapshot_reads_streams_counts_and_memory_indexes_only():
    b = _backend(hide_streams=False).snapshot()
    assert b["streams_hidden"] == 0
    [s] = b["streams"]  # the unrelated stream is dropped
    assert s["object"] == "AiAgentSession" and s["last_run_status"] == "SUCCESS"
    assert s["dlo_count"] == {"rows": 0} and s["dmo_count"] == {"rows": 543}
    [i] = b["indexes"]  # the catalog index is not over a memory DMO
    assert i["chunk_count"] == {"rows": 0}


def test_snapshot_falls_back_to_lake_objects_when_streams_are_hidden():
    b = _backend(hide_streams=True).snapshot()
    assert b["streams_hidden"] == 52
    [s] = b["streams"]  # PR_* problem-record DLOs are skipped
    assert s["object"] == "AiAgentSessionParticipant"
    assert s["dlo_count"] == {"rows": 232} and s["dmo_count"] == {"rows": 2221}


def test_a_missing_dmo_is_a_cell_error_not_a_failure():
    b = _backend(hide_streams=False).snapshot()
    assert b["streams"][0]["dmo_count"]["rows"] == 543
    # AiParticipantInsight_std__dlm is not in COUNTS -> 400 -> an error cell
    http = httpx.Client(transport=_transport(COUNTS, hide_streams=False))
    c = mb.MemoryBackend(http=http, token=lambda: (DOMAIN, "t"))._count(
        DOMAIN, {}, "AiParticipantInsight_std__dlm"
    )
    assert c["rows"] is None and "400" in c["error"]


def test_route_reports_missing_identity_instead_of_500(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TRACE_DIR", str(tmp_path))
    monkeypatch.delenv("SF_MY_DOMAIN", raising=False)
    monkeypatch.delenv("SF_CLIENT_ID", raising=False)
    import importlib

    import console.app as console_app

    importlib.reload(console_app)
    r = TestClient(console_app.create_console_app(None)).get("/api/memory/backend")
    assert r.status_code == 200
    assert "obs identity unavailable" in r.json()["error"]
