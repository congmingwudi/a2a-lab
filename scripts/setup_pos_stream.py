"""In-store POS orders → Data 360 Ingestion API → the memory's real-time graph (WS32).

The question this answers: when a store system posts an order for a known
customer, how soon does every calling agent see it in Agentic Memory's
GetContext? The chain is headless end to end (recipe ported from the
operator's Data 360 retail demo; see plan/07 WS32):

  dmo     standalone DMO A2ALab_POS_Order (category OTHER)
  ingest  Ingestion API connection A2ALab_POS + `pos_order` schema + Engagement stream → DLO
  map     the stream's DLO → A2ALab_POS_Order__dlm
  rel     FieldSrcTrgtRelationship A2ALab_POS_Order.contact_id → ssot__Individual.ssot__Id
          (Metadata API — the REST relationship route is unreliable)
  graph   REALTIME graph A2ALab_Memory_CustPOS = config/agentic_memory_customer_pos_graph.json
          (the Customer360 shape + a POS node under the source Individual). No refresh: the
          create runs its own initial build. KNOWN BROKEN (2026-11-10): three creates from
          this body (A2ALab_Memory_CustomerPOS twice, A2ALab_Memory_CustPOS once) reached
          "ready" without a day-zero mapping and went ERROR at ~5 min with no message, while
          copy_graph_lab.py copies of an existing graph built. Name length is NOT the cause
          (21 characters failed too). The live registration carries A2ALab_Test_C360Copy
          until a create from this file succeeds (D118, WS32 item 6).

Graphs created 2026-11-07 got no real-time layer until 2026-11-10 (a platform-side fix);
since then an order reaches GetContext ~2 s after ingest (plan/03-results.md, WS32).

The order rows key on `contact_id` = the CRM Contact id, which is the memory owner (D117)
and the AM01 identity link's SourceRecordId, so no identity-resolution run is needed for a
row to reach the customer's graph. Nothing mutates without --apply.

  uv run python scripts/setup_pos_stream.py status
  uv run python scripts/setup_pos_stream.py build [--phase dmo|ingest|map|rel|graph] [--apply]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent), str(Path(__file__).parent.parent / "src")]
from setup_jwt_eca import load_env  # noqa: E402

import interop.pos as pos  # noqa: E402

V = "v66.0"  # REALTIME graphs must be POSTed at v66 (older versions reject the RT toggle)
BASE = f"/services/data/{V}/ssot"
INDIVIDUAL = "ssot__Individual__dlm"
POS_GRAPH = Path("config/agentic_memory_customer_pos_graph.json")
REL_DEV = "A2ALab_POS_Order_contact_id"


def target_org() -> str:
    import os

    return os.environ.get("SF_TARGET_ORG") or sys.exit("set SF_TARGET_ORG in .env")


def api(method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    """One /ssot call through the sf CLI's auth → (ok-ish status, parsed body)."""
    cmd = ["sf", "api", "request", "rest", f"{BASE}{path}", "-o", target_org(), "-X", method]
    if body is not None:
        cmd += ["-b", json.dumps(body)]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    out = proc.stdout.strip() or proc.stderr.strip()
    try:
        parsed = json.loads(out) if out else {}
    except json.JSONDecodeError:
        parsed = {"raw": out[:500]}
    failed = proc.returncode != 0 or (
        isinstance(parsed, list) and parsed and isinstance(parsed[0], dict)
        and parsed[0].get("errorCode")
    )  # fmt: skip
    return (500 if failed else 200), parsed


def log(*a) -> None:
    print(*a, flush=True)


# ------------------------------------------------------------------ lookups


def find_connection() -> dict | None:
    _, d = api("GET", "/connections?connectorType=IngestApi")
    for c in (d.get("connections") or []) if isinstance(d, dict) else []:
        if c.get("label") == pos.CONNECTION or str(c.get("name", "")).startswith(pos.CONNECTION):
            return c
    return None


def find_stream() -> tuple[str | None, str | None]:
    _, d = api("GET", "/data-streams?limit=200")
    for s in (d.get("dataStreams") or []) if isinstance(d, dict) else []:
        if str(s.get("name", "")).startswith(f"{pos.CONNECTION}_{pos.OBJECT}"):
            return s.get("name"), (s.get("dataLakeObjectInfo") or {}).get("name")
    return None, None


def dmo_exists() -> bool:
    st, d = api("GET", f"/data-model-objects/{pos.DMO}")
    return st == 200 and isinstance(d, dict) and bool(d.get("fields"))


def relationship_status() -> str | None:
    _, d = api("GET", f"/data-model-objects/{pos.DMO}/relationships")
    rows = d if isinstance(d, list) else (d.get("relationships") or d.get("data") or []) \
        if isinstance(d, dict) else []  # fmt: skip
    for r in rows:
        if (r.get("sourceField") or {}).get("name") == "contact_id__c":
            return str(r.get("status") or "").upper() or "UNKNOWN"
    return None


def graph_status() -> str | None:
    st, d = api("GET", f"/data-graphs/{pos.GRAPH}?dataspace=default")
    if st != 200 or not isinstance(d, dict) or not d.get("sourceObject"):
        return None
    return str(d.get("status") or "present")


# ------------------------------------------------------------------ phases


def phase_dmo(apply: bool) -> None:
    if dmo_exists():
        log(f"[dmo] {pos.DMO} exists")
        return
    fields = [
        {"name": n, "label": n.replace("_", " ").title(), "isPrimaryKey": n == "order_id",
         "isDynamicLookup": False, "dataType": t}
        for n, t in pos.FIELDS
    ]  # fmt: skip
    body = {"name": pos.DMO.removesuffix("__dlm"), "label": "A2A Lab POS Order",
            "category": "OTHER", "dataSpaceName": "default", "fields": fields}  # fmt: skip
    if not apply:
        log(f"[dmo] would POST /data-model-objects {pos.DMO} ({len(fields)} fields)")
        return
    st, d = api("POST", "/data-model-objects", body)
    log(f"[dmo] POST -> {st}: {json.dumps(d)[:300]}")


def phase_ingest(apply: bool) -> None:
    conn = find_connection()
    if not conn:
        if not apply:
            log(f"[ingest] would create IngestApi connection {pos.CONNECTION}, schema, stream")
            return
        st, d = api("POST", "/connections?connectorType=IngestApi",
                    {"connectorType": "IngestApi", "label": pos.CONNECTION,
                     "name": pos.CONNECTION})  # fmt: skip
        log(f"[ingest] connection POST -> {st}: {json.dumps(d)[:300]}")
        time.sleep(3)
        conn = find_connection() or sys.exit("connection did not appear")
    log(f"[ingest] connection {conn.get('name')} ({conn.get('id')})")
    schema = {"schemaType": "IngestApi", "name": pos.OBJECT, "label": pos.OBJECT,
              "fields": [{"name": n, "label": n, "dataType": t} for n, t in pos.FIELDS]}  # fmt: skip
    name, dlo = find_stream()
    if dlo:
        log(f"[ingest] stream {name} → {dlo}")
        return
    if not apply:
        log(f"[ingest] would PUT schema {pos.OBJECT} and POST the Engagement stream")
        return
    st, d = api("PUT", f"/connections/{conn['id']}/schema", {"schemas": [schema]})
    log(f"[ingest] schema PUT -> {st}: {json.dumps(d)[:300]}")
    time.sleep(3)
    body = {
        "name": f"{pos.CONNECTION}_{pos.OBJECT}",
        "datastreamType": "INGESTAPI",
        "connectorInfo": {"connectorType": "IngestApi",
                          "connectorDetails": {"name": conn["name"], "events": [pos.OBJECT]}},
        "dataLakeObjectInfo": {
            "label": pos.OBJECT, "category": "Engagement",
            "dataspaceInfo": [{"name": "default"}],
            "eventDateTimeFieldName": pos.EVENT_TIME,
            "dataLakeFieldInputRepresentations": [
                {"name": "order_id", "label": "order_id", "dataType": "Text",
                 "isPrimaryKey": True}],
        },
        "refreshConfig": {"refreshMode": "UPSERT"},
    }  # fmt: skip
    st, d = api("POST", "/data-streams", body)
    log(f"[ingest] stream POST -> {st}: {json.dumps(d)[:400]}")


def phase_map(apply: bool) -> None:
    _, dlo = find_stream()
    if not dlo:
        log("[map] stream DLO not found yet — run `ingest` (it can take a minute to provision)")
        return
    _, d = api("GET", f"/data-model-object-mappings?dmoDeveloperName={pos.DMO}&dataspace=default")
    maps = (d.get("objectSourceTargetMaps") or []) if isinstance(d, dict) else []
    if any(m.get("sourceEntityDeveloperName") == dlo for m in maps):
        log(f"[map] {dlo} → {pos.DMO} mapped")
        return
    body = {"sourceEntityDeveloperName": dlo, "targetEntityDeveloperName": pos.DMO,
            "fieldMapping": [{"sourceFieldDeveloperName": f"{n}__c",
                              "targetFieldDeveloperName": f"{n}__c"} for n, _ in pos.FIELDS]}  # fmt: skip
    if not apply:
        log(f"[map] would map {dlo} → {pos.DMO}")
        return
    st, d = api("POST", "/data-model-object-mappings?dataspace=default", body)
    log(f"[map] POST -> {st}: {json.dumps(d)[:400]}")


def _rel_xml() -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<FieldSrcTrgtRelationship xmlns="http://soap.sforce.com/2006/04/metadata" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        "  <definitionCreationType>Custom</definitionCreationType>\n"
        "  <masterLabel>A2ALab POS Order contact_id to Individual Id</masterLabel>\n"
        "  <owner>DataCloud</owner>\n"
        "  <relationshipCardinality>ManyToOne</relationshipCardinality>\n"
        f"  <sourceFieldName>{pos.DMO}.contact_id__c</sourceFieldName>\n"
        '  <targetEntity xsi:nil="true"/>\n'
        f"  <targetFieldName>{INDIVIDUAL}.ssot__Id__c</targetFieldName>\n"
        "</FieldSrcTrgtRelationship>\n"
    )


def phase_rel(apply: bool) -> None:
    status = relationship_status()
    if status:
        log(f"[rel] {pos.DMO}.contact_id → {INDIVIDUAL} ({status})")
        return
    if not apply:
        log(f"[rel] would deploy FieldSrcTrgtRelationship {REL_DEV}")
        return
    tmp = Path(tempfile.mkdtemp(prefix="a2alab-pos-rel-"))
    out = tmp / "force-app" / "main" / "default" / "fieldSrcTrgtRelationships"
    out.mkdir(parents=True)
    (tmp / "sfdx-project.json").write_text(json.dumps({
        "packageDirectories": [{"path": "force-app", "default": True}],
        "namespace": "", "sourceApiVersion": "66.0"}))  # fmt: skip
    (out / f"{REL_DEV}.fieldSrcTrgtRelationship-meta.xml").write_text(_rel_xml())
    p = subprocess.run(
        ["sf", "project", "deploy", "start", "--source-dir", "force-app",
         "--target-org", target_org(), "--wait", "10", "--json"],
        cwd=tmp, capture_output=True, text=True, check=False,
    )  # fmt: skip
    try:
        res = json.loads(p.stdout).get("result") or {}
    except json.JSONDecodeError:
        res = {}
    if res.get("success"):
        log(f"[rel] deployed {REL_DEV}")
    else:
        fails = ((res.get("details") or {}).get("componentFailures")) or []
        log(f"[rel] deploy failed: {[f.get('problem') for f in fails] or p.stderr[:300]}")


def build_graph() -> dict:
    """The tracked graph definition (POS_GRAPH): Customer360 + POS under the source Individual.

    It is a GET of a graph that built ACTIVE with its POS node made in the Data Graph
    builder UI (`A2ALab_Test_C360Copy`, 2026-11-07), renamed, not a generated node: every
    graph built from the earlier generated POS node errored a few minutes after the POST
    with no message, while the UI-shaped node built READY (plan/03-results.md, WS32).
    """
    g = json.loads(POS_GRAPH.read_text())
    if g.get("name") != pos.GRAPH:
        sys.exit(f"{POS_GRAPH} names {g.get('name')!r}, expected {pos.GRAPH!r}")
    return g


def phase_graph(apply: bool) -> None:
    status = graph_status()
    if status:
        log(f"[graph] {pos.GRAPH} exists (status={status})")
        return
    rel = relationship_status()
    if rel != "ACTIVE":
        log(f"[graph] relationship is {rel or 'missing'}; the graph POST needs it ACTIVE — run rel")
        return
    g = build_graph()
    if not apply:
        log(f"[graph] would POST {pos.GRAPH} from {POS_GRAPH} ({len(json.dumps(g))} bytes)")
        return
    st, d = api("POST", "/data-graphs?dataspace=default", g)
    log(f"[graph] POST -> {st}: {json.dumps(d)[:400]}")
    # No refresh here. The create runs its own initial build, which writes the
    # day-zero mapping + transform; the 2026-11-10 create that refreshed straight after
    # the POST ended with neither and went ERROR at ~5 min (plan/03-results.md).


PHASES = {"dmo": phase_dmo, "ingest": phase_ingest, "map": phase_map, "rel": phase_rel,
          "graph": phase_graph}  # fmt: skip


def cmd_status(_a) -> None:
    conn = find_connection()
    name, dlo = find_stream()
    log(f"dmo        {pos.DMO}: {'present' if dmo_exists() else 'missing'}")
    log(f"connection {pos.CONNECTION}: {conn.get('id') if conn else 'missing'}")
    log(f"stream     {name or 'missing'} → {dlo or '-'}")
    log(f"relation   {relationship_status() or 'missing'}")
    log(f"graph      {pos.GRAPH}: {graph_status() or 'missing'}")


def cmd_build(a) -> None:
    for phase in [a.phase] if a.phase else list(PHASES):
        PHASES[phase](a.apply)
    if not a.apply:
        log("(dry run — re-run with --apply to change the production org)")


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    sub.add_parser("status")
    sp = sub.add_parser("build")
    sp.add_argument("--phase", choices=list(PHASES))
    sp.add_argument("--apply", action="store_true", help="send it (default: dry run)")
    a = ap.parse_args()
    {"status": cmd_status, "build": cmd_build}[a.verb](a)


if __name__ == "__main__":
    main()
