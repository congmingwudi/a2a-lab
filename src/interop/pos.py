"""In-store POS orders → the Data 360 Ingestion API (WS32).

A store system "rings up" an order for a known customer; the row lands in the
`A2ALab_POS_Order__dlm` DMO, which the memory's real-time graph
(`A2ALab_Memory_CustomerPOS`) hangs under the customer's Individual. The question
the lab measures is how soon every agent reading GetContext sees it.

The row keys on `contact_id` = the CRM Contact id: the memory owner (D117) and the
AM01 identity link's SourceRecordId, so the order needs no identity-resolution run
to join the customer's graph.

Auth is the same JWT-bearer → `/services/a360/token` exchange as the memory client
(interop/memory.py), minus the agent binding: ingestion is not an agent call, so no
aid/xid. The caller identity is `SF_CLIENT_ID_POS` (its own ECA, per D37); until that
exists the memory ECA — which carries `cdp_ingest_api` — proves the path.
"""

from __future__ import annotations

import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from interop.trace import Hop

DMO = "A2ALab_POS_Order__dlm"
CONNECTION = "A2ALab_POS"
# `pos_purchase`, not the first `pos_order`: a stream's DLO columns are fixed at creation,
# and adding `unified_individual_id` to the connection schema never reached pos_order's
# DLO through any data-stream PATCH shape (2026-11-07). A second schema object + stream
# mapped onto the same DMO carries it.
OBJECT = "pos_purchase"
LINK_DMO = "UnifiedLinkssotIndividualAm01__dlm"
GRAPH = "A2ALab_Memory_CustomerPOS"
EVENT_TIME = "order_ts"
TARGET = "datacloud-ingest"

# (name, Data 360 type) — one list drives the ingest schema, the DMO, the mapping
# and the graph node, so they cannot drift apart.
FIELDS: list[tuple[str, str]] = [
    ("order_id", "Text"),
    ("contact_id", "Text"),
    # The AM01 Unified Individual id — the graph ROOT's primary key. A real-time graph
    # joins on it directly (1:N off the root, no identity-link → Individual hop) or
    # carries it as a Root Key indicator under Individual (WS32).
    ("unified_individual_id", "Text"),
    ("order_ts", "DateTime"),
    ("store_name", "Text"),
    ("store_city", "Text"),
    ("product_sku", "Text"),
    ("product_name", "Text"),
    ("category", "Text"),
    ("quantity", "Number"),
    ("amount", "Number"),
    ("currency_code", "Text"),
    ("channel", "Text"),
]


class PosError(RuntimeError):
    pass


_SFID = re.compile(r"^[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?$")


def new_order(contact_id: str, **fields: Any) -> dict[str, Any]:
    """One order row; caller fields override the defaults."""
    if not contact_id:
        raise PosError("a POS order needs the customer's CRM Contact id")
    row: dict[str, Any] = {
        "order_id": f"pos-{uuid.uuid4().hex[:12]}",
        "contact_id": contact_id,
        "unified_individual_id": "",  # resolved per row by PosClient.send
        "order_ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "store_name": "A2A Lab Flagship",
        "store_city": "Denver",
        "product_sku": "SKU-0001",
        "product_name": "Demo product",
        "category": "General",
        "quantity": 1,
        "amount": 0,
        "currency_code": "USD",
        "channel": "in-store",
    }
    row.update({k: v for k, v in fields.items() if v is not None})
    unknown = set(row) - {n for n, _ in FIELDS}
    if unknown:
        raise PosError(f"not in the pos_order schema: {sorted(unknown)}")
    return row


class PosClient:
    """Mints a tenant ingest token and posts orders to the streaming Ingestion API."""

    def __init__(self, http: httpx.AsyncClient | None = None, *, source: str = "console"):
        self._http = http or httpx.AsyncClient(timeout=30)
        self._tok: tuple[str, str, float] | None = None  # (token, base_url, expires_at)
        self.source = source

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _token(self) -> tuple[str, str]:
        if self._tok and self._tok[2] > time.time() + 60:
            return self._tok[0], self._tok[1]
        import jwt as pyjwt

        from interop.memory import _signing_key

        client_id = os.environ.get("SF_CLIENT_ID_POS") or os.environ.get("SF_CLIENT_ID_MEMORY")
        run_as = os.environ.get("SF_POS_RUNAS_USER") or os.environ.get("SF_MEMORY_RUNAS_USER")
        domain = os.environ.get("SF_MY_DOMAIN", "").rstrip("/")
        if not (client_id and run_as and domain):
            raise PosError("SF_CLIENT_ID_POS / SF_POS_RUNAS_USER / SF_MY_DOMAIN not set")
        domain = domain if domain.startswith("https://") else f"https://{domain}"
        assertion = pyjwt.encode(
            {"iss": client_id, "sub": run_as,
             "aud": os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com"),
             "exp": int(time.time()) + 180},
            _signing_key(), algorithm="RS256",
        )  # fmt: skip
        r = await self._http.post(
            f"{domain}/services/oauth2/token",
            data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                  "assertion": assertion},
        )  # fmt: skip
        if r.status_code != 200:
            raise PosError(f"JWT-bearer grant refused (HTTP {r.status_code}): {r.text[:200]}")
        core = r.json()
        r = await self._http.post(
            f"{core['instance_url']}/services/a360/token",
            data={"grant_type": "urn:salesforce:grant-type:external:cdp",
                  "subject_token": core["access_token"],
                  "subject_token_type": "urn:ietf:params:oauth:token-type:access_token"},
        )  # fmt: skip
        cdp = r.json() if r.status_code == 200 else {}
        if not cdp.get("access_token"):
            raise PosError(f"a360 CDP exchange refused (HTTP {r.status_code}): {r.text[:200]}")
        host = cdp["instance_url"]
        base = host if host.startswith("https://") else f"https://{host}"
        self._tok = (cdp["access_token"], base, time.time() + int(cdp.get("expires_in") or 3600))
        return self._tok[0], self._tok[1]

    async def unified_id(self, contact_id: str) -> str:
        """The customer's AM01 Unified Individual id, read from the identity link NOW.

        Deliberately uncached: an identity-resolution re-run can re-key the unified
        profile, and an order stamped with a stale id falls off the customer's graph.
        """
        if not _SFID.match(contact_id or ""):
            raise PosError(f"not a Salesforce id: {contact_id!r}")
        token, base = await self._token()
        sql = (f"SELECT UnifiedRecordId__c FROM {LINK_DMO} "
               f"WHERE SourceRecordId__c = '{contact_id}' LIMIT 1")  # fmt: skip
        r = await self._http.post(f"{base}/api/v2/query", json={"sql": sql},
                                  headers={"authorization": f"Bearer {token}"})  # fmt: skip
        if r.status_code != 200:
            raise PosError(f"unified-id lookup refused (HTTP {r.status_code}): {r.text[:200]}")
        rows = r.json().get("data") or []
        if not rows:
            raise PosError(f"{contact_id} has no AM01 unified profile yet (identity link empty)")
        row = rows[0]
        return row[0] if isinstance(row, list) else row.get("UnifiedRecordId__c")

    async def send(self, trace_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """POST rows to the stream; returns {status, body, accepted_s}. 202 = accepted.

        A row without `unified_individual_id` gets it resolved here, per row: the
        real-time graph joins orders on the root's primary key (WS32).
        """
        for row in rows:
            if not row.get("unified_individual_id"):
                row["unified_individual_id"] = await self.unified_id(row["contact_id"])
        token, base = await self._token()
        # The source is the name the connection was CREATED with, not its API name
        # (which carries an org-generated uuid suffix and 404s here, 2026-11-06).
        path = f"/api/v1/ingest/sources/{CONNECTION}/{OBJECT}"
        body = {"data": rows}
        with Hop(trace_id, source=self.source, target=TARGET, protocol="rest",
                 transport_detail=f"POST {path}", request_payload=body) as hop:  # fmt: skip
            t0 = time.monotonic()
            r = await self._http.post(
                f"{base}{path}", json=body, headers={"authorization": f"Bearer {token}"}
            )
            elapsed = time.monotonic() - t0
            parsed: Any = r.json() if "json" in r.headers.get("content-type", "") else r.text
            hop.response_payload = {"status": r.status_code, "body": parsed}
            hop.platform_ref = rows[0]["order_id"] if rows else None
        if r.status_code != 202:
            raise PosError(f"ingest refused (HTTP {r.status_code}): {str(parsed)[:300]}")
        return {"status": r.status_code, "body": parsed, "accepted_s": round(elapsed, 3)}
