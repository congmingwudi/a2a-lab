"""The Agentic Memory back end, read live from the org (D115, plan/03-results.md).

The memory API (plan/18, D91) is a black box from the caller's side: it takes
entries and returns context. Behind it, the agentic setup provisioned three kinds
of thing in Data 360, and this module reads all three headlessly so the console
can show them (the "Memory Back End" section):

* the ``AgenticConnector_*`` Ingestion API **data streams**, one per memory object,
  with the lake object (DLO) each lands in;
* the **DMOs** the memory objects are modelled as, with live row counts. A controlled
  Memory API fact was query-visible as an active row in both its source DLO and
  ``AiParticipantInsight_std__dlm``; API deletion removed the DMO row and retained
  an inactive DLO tombstone (measured 2026-11-11);
* the two HYBRID **search indexes** over messages and participant insights, with
  their embedding settings and how many chunks they actually hold.

Identity: the observability client-credentials app (``SalesforceOtelSource._token``,
``SF_CLIENT_ID_OBS``), the same one the console's OTel routes use. It reads
metadata and runs COUNT(*) queries only, and never touches a memory record.
"""

from __future__ import annotations

import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

API = "v66.0"
STREAM_PREFIX = "AgenticConnector_"

# Memory object -> the DMO it is modelled as. The stream list carries no mapping
# (its `mappings` is empty for these streams), so the target is stated here, and a
# DMO that does not exist on a tenant reports its query error instead of a count.
# Individual is deliberately uncounted: ssot__Individual__dlm holds every
# Individual in the org (CRM included), so its count says nothing about memory.
MEMORY_DMOS: dict[str, str | None] = {
    "Conversation": "ssot__Conversation__dlm",
    "AiAgentSession": "ssot__AiAgentSession__dlm",
    "AiAgentSessionParticipant": "ssot__AiAgentSessionParticipant__dlm",
    "AiAgentInteraction": "ssot__AiAgentInteraction__dlm",
    "AiAgentInteractionMessage": "ssot__AiAgentInteractionMessage__dlm",
    "AiAgentInteractionStep": "ssot__AiAgentInteractionStep__dlm",
    "AiAgentMessageAttachment": "AiAgentMessageAttachment_std__dlm",
    "AiParticipantInsight": "AiParticipantInsight_std__dlm",
    "Individual": None,
}

# A search index belongs to memory when its source DMO is a memory DMO.
_MEMORY_DMO_SET = {d for d in MEMORY_DMOS.values() if d}

_STREAM_RE = re.compile(r"^AgenticConnector_(?P<obj>.+?)_[0-9A-F]{8}$")

CACHE_S = 600
_cache: dict = {"at": 0.0, "body": None}
_lock = threading.Lock()


def memory_object(stream_name: str) -> str:
    """``AgenticConnector_AiAgentSession_7D8F75AF`` -> ``AiAgentSession``."""
    m = _STREAM_RE.match(stream_name or "")
    return m.group("obj") if m else (stream_name or "")


def dlo_object(dlo_name: str, field_names: list[str]) -> str:
    """Name the memory object behind an ``AgenticConnector_*__dll``.

    DLO names are truncated (``AgenticConnector_AiAgentInterac_2CC81C4C__dll`` is
    one of three objects), but the connector prefixes each field with the full
    object name (``AiAgentInteractionMessage_content__c``). So: the known objects
    the stem could be, narrowed to the longest one some field is prefixed with.
    """
    stem = memory_object(dlo_name.removesuffix("__dll"))
    cands = [o for o in MEMORY_DMOS if o.startswith(stem)]
    if len(cands) == 1:
        return cands[0]
    hits = [o for o in cands if any(f.startswith(o + "_") for f in field_names)]
    return max(hits, key=len) if hits else stem


def _uv(values: list | None, key: str) -> str | None:
    for v in values or []:
        if v.get("id") == key:
            return v.get("value")
    return None


def shape_stream(s: dict) -> dict:
    dlo = (s.get("dataLakeObjectInfo") or {}).get("name")
    obj = memory_object(s.get("name", ""))
    return {
        "name": s.get("name"),
        "object": obj,
        "type": s.get("dataStreamType"),
        "status": s.get("status"),
        "last_run_status": s.get("lastRunStatus"),
        "last_refresh": s.get("lastRefreshDate"),
        "total_records": s.get("totalRecords"),
        "dlo": dlo,
        "dmo": MEMORY_DMOS.get(obj),
    }


def shape_index(d: dict) -> dict:
    cfg = d.get("vectorEmbeddingConfiguration") or {}
    model = cfg.get("embeddingModel") or {}
    idx = cfg.get("index") or {}
    chunk = (d.get("chunkingConfiguration") or {}).get("fieldLevelConfigurations") or []
    return {
        "name": d.get("developerName"),
        "label": d.get("label"),
        "search_type": d.get("searchType"),
        "status": d.get("runtimeStatus"),
        "refreshed": d.get("indexRefreshedOn"),
        "source_dmo": d.get("sourceDmoDeveloperName"),
        "source_fields": [c.get("sourceDmoFieldDeveloperName") for c in chunk],
        "chunk_dmo": d.get("chunkDmoDeveloperName"),
        "vector_dmo": d.get("vectorDmoDeveloperName"),
        "embedding_model": model.get("id"),
        "dimension": _uv(model.get("userValues"), "dimension"),
        "chunk_tokens": _uv((chunk[0].get("config") or {}).get("userValues"), "max_tokens")
        if chunk
        else None,
        "algorithm": idx.get("id"),
        "hnsw_m": _uv(idx.get("userValues"), "M"),
        "hnsw_ef_construction": _uv(idx.get("userValues"), "hnswEfConstruction"),
        "similarity": cfg.get("similarityMetric"),
    }


class MemoryBackend:
    def __init__(self, http: httpx.Client | None = None, token=None):
        self._http = http or httpx.Client(timeout=30)
        self._token_fn = token

    def _auth(self) -> tuple[str, dict]:
        if self._token_fn is None:
            from observability.salesforce_otel_source import SalesforceOtelSource

            self._token_fn = SalesforceOtelSource(self._http)._token
        domain, tok = self._token_fn()
        return domain, {"authorization": f"Bearer {tok}"}

    def _count(self, domain: str, h: dict, table: str) -> dict:
        try:
            r = self._http.post(
                f"{domain}/services/data/{API}/ssot/query",
                headers=h,
                json={"sql": f"SELECT COUNT(*) AS n FROM {table}"},
            )
            if r.status_code >= 400:
                return {"rows": None, "error": f"HTTP {r.status_code}: {r.text[:160]}"}
            data = r.json().get("data") or [{}]
            return {"rows": int(data[0].get("n") or 0)}
        except Exception as exc:  # noqa: BLE001 - one table failing is a cell, not a 500
            return {"rows": None, "error": f"{type(exc).__name__}: {exc}"[:200]}

    def _streams_from_dlos(self, domain: str, h: dict) -> list[dict]:
        r = self._http.get(
            f"{domain}/services/data/{API}/ssot/metadata?entityType=DataLakeObject",
            headers=h,
            timeout=60,
        )
        r.raise_for_status()
        out = []
        for m in r.json().get("metadata") or []:
            name = m.get("name") or ""
            if not name.startswith(STREAM_PREFIX):
                continue  # PR_* problem-record DLOs and everything else
            obj = dlo_object(name, [f.get("name", "") for f in m.get("fields") or []])
            out.append(
                {
                    "name": None,
                    "object": obj,
                    "type": None,
                    "status": None,
                    "last_run_status": None,
                    "last_refresh": None,
                    "total_records": None,
                    "dlo": name,
                    "dmo": MEMORY_DMOS.get(obj),
                }
            )
        return out

    def snapshot(self) -> dict:
        domain, h = self._auth()
        r = self._http.get(f"{domain}/services/data/{API}/ssot/data-streams?limit=200", headers=h)
        r.raise_for_status()
        listing = r.json()
        streams = [
            shape_stream(s)
            for s in listing.get("dataStreams") or []
            if (s.get("name") or "").startswith(STREAM_PREFIX)
        ]
        # An identity without data-stream record access gets totalSize > 0 and an
        # empty page. The lake objects are still listed by /ssot/metadata, so the
        # tab falls back to those and says the stream records were hidden.
        hidden = 0
        if not listing.get("dataStreams") and listing.get("totalSize"):
            hidden = int(listing["totalSize"])
            streams = self._streams_from_dlos(domain, h)
        streams.sort(key=lambda s: s["object"])
        r = self._http.get(f"{domain}/services/data/{API}/ssot/search-index", headers=h)
        r.raise_for_status()
        indexes = [
            shape_index(d)
            for d in r.json().get("semanticSearchDefinitionDetails") or []
            if d.get("sourceDmoDeveloperName") in _MEMORY_DMO_SET
        ]
        indexes.sort(key=lambda i: i["name"] or "")

        tables = {s["dlo"] for s in streams if s["dlo"]}
        tables |= {d for d in MEMORY_DMOS.values() if d}
        tables |= {i["chunk_dmo"] for i in indexes if i["chunk_dmo"]}
        with ThreadPoolExecutor(max_workers=6) as pool:
            counts = dict(zip(tables, pool.map(lambda t: self._count(domain, h, t), tables)))

        for s in streams:
            s["dlo_count"] = counts.get(s["dlo"]) if s["dlo"] else None
            s["dmo_count"] = counts.get(s["dmo"]) if s["dmo"] else None
        for i in indexes:
            i["chunk_count"] = counts.get(i["chunk_dmo"]) if i["chunk_dmo"] else None
        return {
            "streams_hidden": hidden,
            "streams": streams,
            "indexes": indexes,
            "read_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }


def cached_snapshot(refresh: bool = False, backend: MemoryBackend | None = None) -> dict:
    """The snapshot, at most CACHE_S old. ``refresh`` forces a re-read."""
    with _lock:
        if not refresh and _cache["body"] and time.time() - _cache["at"] < CACHE_S:
            return {**_cache["body"], "cached": True}
        body = (backend or MemoryBackend()).snapshot()
        _cache.update(at=time.time(), body=body)
        return {**body, "cached": False}
