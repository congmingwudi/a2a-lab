"""Salesforce delivery for the async account-brief pattern (ADR D16).

The Claude managed agent's `save_account_brief` custom tool is executed
host-side by the brief worker, which lands the research in the org via
plain Salesforce REST (same OAuth client-credentials app as the Agent API
client — credentials never enter the managed sandbox):

1. resolve the Account by name,
2. insert an A2ALab_Account_Brief__c record (long-text Brief__c),
3. log a completed Task on the Account crediting the Claude managed agent,
4. fire the A2ALab_Brief_Alert in-app notification (best-effort).

Every call records a TraceEvent so the console shows the delivery leg
hop by hop.
"""

from __future__ import annotations

import datetime as _dt
import json as _json
import os
import re as _re
import time
import urllib.parse as _url

import httpx

from interop.trace import Hop

API_VERSION = "v62.0"
SOURCE_LABEL = "Claude managed agent (A2A interop lab)"

# The org-enforced idempotency boundary (D81): both the brief and its Task carry
# this external-id/unique field, valued `<research_session_id>#<account_id>`, and
# are written by upsert so a re-run — or a concurrent losing writer — matches the
# existing record instead of creating a duplicate.
DELIVERY_KEY_FIELD = "A2ALab_Delivery_Key__c"

# Operator-authored, trusted name -> Account Id map (A5/F03). JSON env value (not
# a delimiter — account names contain commas). A model-provided name that matches
# a key here is resolved to the mapped Id directly, with a binding check.
ACCOUNT_MAP_ENV = "A2ALAB_BRIEF_ACCOUNT_MAP"

# The trusted-map value must be an ACCOUNT id, not merely a well-formed 15/18-char
# Salesforce id: the `001` key prefix is Account's, so a map entry pointing at a
# Contact/Opportunity/etc is rejected before it can mis-file a brief (A5/F03).
_ACCOUNT_ID_RE = _re.compile(r"^001[A-Za-z0-9]{12}([A-Za-z0-9]{3})?$")


def _fmt_candidates(rows: list[dict]) -> str:
    """Name (Id) list for an ambiguity error, so the operator can pick the one
    to add to the trusted map rather than guessing which the resolver saw."""
    return "; ".join(f"{r.get('Name')} ({r.get('Id')})" for r in rows)


def _soql_str(value: str) -> str:
    """Escape a value for a SOQL string literal: backslash then quote."""
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _soql_like(value: str) -> str:
    """Escape a value used inside a LIKE pattern: the string-literal escapes
    plus the LIKE wildcards `%` and `_`, so a crafted name cannot widen the
    match (no more `LIKE '%%'`) or inject."""
    return _soql_str(value).replace("%", "\\%").replace("_", "\\_")


def _load_account_map() -> dict[str, str]:
    raw = os.environ.get(ACCOUNT_MAP_ENV, "").strip()
    if not raw:
        return {}
    try:
        parsed = _json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"{ACCOUNT_MAP_ENV} is not valid JSON: {exc}") from None
    if not isinstance(parsed, dict):
        raise RuntimeError(f"{ACCOUNT_MAP_ENV} must be a JSON object of name -> Account Id")
    return {str(k): str(v) for k, v in parsed.items()}


class BriefWriter:
    def __init__(
        self,
        *,
        my_domain: str,
        client_id: str,
        client_secret: str,
        account_map: dict[str, str] | None = None,
    ):
        self.my_domain = my_domain.rstrip("/")
        if not self.my_domain.startswith("https://"):
            self.my_domain = f"https://{self.my_domain}"
        self.client_id = client_id
        self.client_secret = client_secret
        self.account_map = account_map or {}
        self._http = httpx.AsyncClient(timeout=30.0)
        self._token: str | None = None
        self._token_expiry: float = 0.0

    @classmethod
    def from_env(cls) -> "BriefWriter":
        try:
            return cls(
                my_domain=os.environ["SF_MY_DOMAIN"],
                client_id=os.environ["SF_CLIENT_ID"],
                client_secret=os.environ["SF_CLIENT_SECRET"],
                account_map=_load_account_map(),
            )
        except KeyError as missing:
            raise RuntimeError(
                f"Salesforce is not configured: missing env var {missing}."
            ) from None

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        r = await self._http.post(
            f"{self.my_domain}/services/oauth2/token",
            data={
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            },
        )
        r.raise_for_status()
        self._token = r.json()["access_token"]
        self._token_expiry = time.time() + 25 * 60
        return self._token

    async def _headers(self) -> dict[str, str]:
        return {
            "authorization": f"Bearer {await self._get_token()}",
            "content-type": "application/json",
        }

    async def _request(self, method: str, path: str, *, json_body=None, params=None):
        r = await self._http.request(
            method,
            f"{self.my_domain}/services/data/{API_VERSION}{path}",
            json=json_body,
            params=params,
            headers=await self._headers(),
        )
        if r.status_code >= 400:
            raise RuntimeError(f"Salesforce {method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.text else {}

    async def _query(self, soql: str) -> list[dict]:
        data = await self._request("GET", "/query", params={"q": soql})
        return data.get("records", [])

    async def _upsert_by_external_id(
        self, sobject: str, ext_field: str, key: str, body: dict
    ) -> dict:
        """Upsert on an external-id field. Race-safe at the org: a concurrent
        losing writer's PATCH matches the winning row instead of duplicating.
        Returns {"id", "created"}. Falls back to a re-query if the org answered
        204 (no body)."""
        seg = _url.quote(key, safe="")
        data = await self._request(
            "PATCH", f"/sobjects/{sobject}/{ext_field}/{seg}", json_body=body
        )
        rec_id = (data or {}).get("id")
        if not rec_id:  # some API paths answer 204 on update — recover the id
            rows = await self._query(
                f"SELECT Id FROM {sobject} WHERE {ext_field} = '{_soql_str(key)}' LIMIT 1"
            )
            rec_id = rows[0]["Id"] if rows else None
        if not rec_id:
            # Fail closed: a null id means we cannot honestly report the record,
            # and continuing would build a `/None/view` URL and claim success on
            # a delivery that may not exist. Raise so the call is recorded failed
            # and retried, not silently "delivered".
            raise RuntimeError(
                f"upsert {sobject} on {ext_field}='{key}' returned no id "
                f"(status {'204' if not data else 'ok'}) and the re-query found no row"
            )
        return {"id": rec_id, "created": bool((data or {}).get("created", False))}

    async def _resolve_account(self, account_name: str, trace_id: str) -> dict:
        """Deterministic Account resolution (A5/F03). Preference order:
        the trusted name->Id map (with a binding check), then an exact-name
        match, then a single escaped LIKE fallback. Anything ambiguous or empty
        raises and writes nothing."""
        name = (account_name or "").strip()
        if not name:
            raise RuntimeError("save_account_brief: empty account name — refusing to resolve")

        mapped_id = self.account_map.get(name)
        if mapped_id:
            if not _ACCOUNT_ID_RE.match(mapped_id):
                raise RuntimeError(f"{ACCOUNT_MAP_ENV} maps '{name}' to a malformed Account Id")
            with Hop(
                trace_id,
                source="brief-worker",
                target="salesforce-org",
                protocol="rest",
                transport_detail=f"GET /query Account Id = (trusted map: '{name}')",
                request_payload={"account_name": name, "mapped": True},
            ) as hop:
                rows = await self._query(
                    f"SELECT Id, Name FROM Account WHERE Id = '{_soql_str(mapped_id)}' LIMIT 1"
                )
                if not rows:
                    raise RuntimeError(
                        f"{ACCOUNT_MAP_ENV} maps '{name}' to {mapped_id}, but no such Account"
                    )
                account = rows[0]
                if account["Name"] != name:
                    raise RuntimeError(
                        f"account binding mismatch: map sends '{name}' to {mapped_id}, "
                        f"whose Name is '{account['Name']}' — refusing to mis-file the brief"
                    )
                hop.response_payload = account
            return account

        with Hop(
            trace_id,
            source="brief-worker",
            target="salesforce-org",
            protocol="rest",
            transport_detail=f"GET /query Account Name = '{name}'",
            request_payload={"account_name": name},
        ) as hop:
            # LIMIT 6, not 2: enough to LIST the colliding candidates in the
            # error (A5/F03 requires naming them), while still bounding the read.
            exact = await self._query(
                f"SELECT Id, Name FROM Account WHERE Name = '{_soql_str(name)}' LIMIT 6"
            )
            if len(exact) > 1:
                raise RuntimeError(
                    f"'{name}' is ambiguous — {len(exact)} Accounts share that exact name "
                    f"({_fmt_candidates(exact)}); refusing to guess (add it to {ACCOUNT_MAP_ENV})"
                )
            if len(exact) == 1:
                hop.response_payload = exact[0]
                return exact[0]
            like = await self._query(
                f"SELECT Id, Name FROM Account WHERE Name LIKE '%{_soql_like(name)}%' LIMIT 6"
            )
            if not like:
                raise RuntimeError(f"no Account matched '{name}'")
            if len(like) > 1:
                raise RuntimeError(
                    f"'{name}' is ambiguous — matched {len(like)} Accounts by LIKE "
                    f"({_fmt_candidates(like)}); refusing to guess (add it to {ACCOUNT_MAP_ENV})"
                )
            hop.response_payload = like[0]
            return like[0]

    async def save_brief(
        self,
        *,
        account_name: str,
        headline: str,
        brief_markdown: str,
        research_session_id: str,
        trace_id: str,
    ) -> dict:
        """Deliver the brief + Task + in-app alert idempotently. Resolves the
        Account deterministically (A5), then upserts both records on the
        composite delivery key (A4). Returns the resolved/created ids."""
        today = _dt.date.today().isoformat()
        # Web-search citation markers sometimes leak into the model's
        # markdown — scrub them so the stored brief is clean prose.
        brief_markdown = _re.sub(r"</?cite[^>]*>", "", brief_markdown)

        account = await self._resolve_account(account_name, trace_id)
        delivery_key = f"{research_session_id}#{account['Id']}"

        body = {
            "Account__c": account["Id"],
            "Brief__c": brief_markdown,
            "Brief_Date__c": today,
            "Source__c": SOURCE_LABEL,
            "Research_Session_Id__c": research_session_id,
            DELIVERY_KEY_FIELD: delivery_key,
        }
        with Hop(
            trace_id,
            source="brief-worker",
            target="salesforce-org",
            protocol="rest",
            transport_detail=f"PATCH upsert A2ALab_Account_Brief__c ({DELIVERY_KEY_FIELD})",
            request_payload={**body, "Brief__c": f"({len(brief_markdown)} chars) {headline}"},
        ) as hop:
            upserted = await self._upsert_by_external_id(
                "A2ALab_Account_Brief__c", DELIVERY_KEY_FIELD, delivery_key, body
            )
            brief_id = upserted["id"]
            hop.response_payload = upserted

        # In-app (bell) notification — idempotent AND best-effort. Gate it on the
        # BRIEF create, the pair's single atomic create point, and fire it here
        # (not after the Task): exactly one call/writer ever creates the brief, so
        # the alert fires exactly once — no duplicate on a re-delivery, a
        # concurrent losing writer, or a Task-only repair, and never missed if the
        # Task upsert later fails and retries. Best-effort: a missing notification
        # type or permission must not fail the delivery.
        notified = False
        if upserted.get("created"):
            notified = await self._notify(account, headline, brief_id, trace_id)

        brief_url = (
            self.my_domain.replace(".my.salesforce.com", ".lightning.force.com")
            + f"/lightning/r/A2ALab_Account_Brief__c/{brief_id}/view"
        )
        task_body = {
            "Subject": f"Daily account brief available: {headline}"[:255],
            "WhatId": account["Id"],
            "Status": "Completed",
            "ActivityDate": today,
            DELIVERY_KEY_FIELD: delivery_key,
            "Description": (
                f"The latest account intelligence brief ({today}) is available — "
                f"open it here:\n{brief_url}\n\n"
                f"Researched and written by the {SOURCE_LABEL} — news, competitor "
                "moves, government relations, and geopolitical signals aggregated "
                "from external sources. Also shown on this account's "
                '"Account Briefs" tab.'
            ),
        }
        with Hop(
            trace_id,
            source="brief-worker",
            target="salesforce-org",
            protocol="rest",
            transport_detail=f"PATCH upsert Task ({DELIVERY_KEY_FIELD}) — activity on the Account",
            request_payload=task_body,
        ) as hop:
            task = await self._upsert_by_external_id(
                "Task", DELIVERY_KEY_FIELD, delivery_key, task_body
            )
            hop.response_payload = task

        return {
            "account_id": account["Id"],
            "account_name": account["Name"],
            "brief_id": brief_id,
            "task_id": task.get("id"),
            "delivery_key": delivery_key,
            "notified": notified,
        }

    async def _notify(self, account: dict, headline: str, brief_id: str, trace_id: str) -> bool:
        """Fire the A2ALab_Brief_Alert in-app notification. Best-effort: returns
        False (never raises) if the type/permission/recipients are missing."""
        try:
            with Hop(
                trace_id,
                source="brief-worker",
                target="salesforce-org",
                protocol="rest",
                transport_detail="POST /actions/standard/customNotificationAction",
                request_payload={"type": "A2ALab_Brief_Alert", "target": brief_id},
            ) as hop:
                notif_types = await self._query(
                    "SELECT Id FROM CustomNotificationType "
                    "WHERE DeveloperName = 'A2ALab_Brief_Alert' LIMIT 1"
                )
                if not notif_types:
                    raise RuntimeError("CustomNotificationType A2ALab_Brief_Alert not deployed")
                recipients = await self._alert_recipients()
                if not recipients:
                    raise RuntimeError("no alert recipients resolved")
                result = await self._request(
                    "POST",
                    "/actions/standard/customNotificationAction",
                    json_body={
                        "inputs": [
                            {
                                "customNotifTypeId": notif_types[0]["Id"],
                                "recipientIds": recipients,
                                "title": f"New account brief: {account['Name']}"[:250],
                                "body": (
                                    f"{headline} — provided by a Claude managed agent "
                                    "(A2A interop lab)"
                                )[:750],
                                "targetId": brief_id,
                            }
                        ]
                    },
                )
                hop.response_payload = result
                return True
        except Exception as exc:  # error hop already recorded by Hop.__exit__
            print(f"[briefs] in-app notification failed (continuing): {exc}", flush=True)
            return False

    async def _alert_recipients(self) -> list[str]:
        """Users to notify: SF_ALERT_USERNAME if set, else active sysadmins."""
        username = os.environ.get("SF_ALERT_USERNAME")
        if username:
            safe = username.replace("'", r"\'")
            rows = await self._query(f"SELECT Id FROM User WHERE Username = '{safe}' LIMIT 1")
        else:
            rows = await self._query(
                "SELECT Id FROM User WHERE IsActive = true "
                "AND Profile.Name = 'System Administrator' LIMIT 5"
            )
        return [r["Id"] for r in rows]
