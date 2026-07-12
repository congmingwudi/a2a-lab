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
import os
import re as _re
import time

import httpx

from interop.trace import Hop

API_VERSION = "v62.0"
SOURCE_LABEL = "Claude managed agent (A2A interop lab)"


class BriefWriter:
    def __init__(self, *, my_domain: str, client_id: str, client_secret: str):
        self.my_domain = my_domain.rstrip("/")
        if not self.my_domain.startswith("https://"):
            self.my_domain = f"https://{self.my_domain}"
        self.client_id = client_id
        self.client_secret = client_secret
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

    async def save_brief(
        self,
        *,
        account_name: str,
        headline: str,
        brief_markdown: str,
        research_session_id: str,
        trace_id: str,
    ) -> dict:
        """Insert the brief + Task + in-app alert. Returns the created ids."""
        safe_name = account_name.replace("'", r"\'")
        today = _dt.date.today().isoformat()
        # Web-search citation markers sometimes leak into the model's
        # markdown — scrub them so the stored brief is clean prose.
        brief_markdown = _re.sub(r"</?cite[^>]*>", "", brief_markdown)

        with Hop(
            trace_id,
            source="brief-worker",
            target="salesforce-org",
            protocol="rest",
            transport_detail=f"GET /query Account LIKE '{account_name}'",
            request_payload={"account_name": account_name},
        ) as hop:
            records = await self._query(
                f"SELECT Id, Name FROM Account WHERE Name LIKE '%{safe_name}%' LIMIT 1"
            )
            if not records:
                raise RuntimeError(f"no Account matched '{account_name}'")
            account = records[0]
            hop.response_payload = account

        body = {
            "Account__c": account["Id"],
            "Brief__c": brief_markdown,
            "Brief_Date__c": today,
            "Source__c": SOURCE_LABEL,
            "Research_Session_Id__c": research_session_id,
        }
        with Hop(
            trace_id,
            source="brief-worker",
            target="salesforce-org",
            protocol="rest",
            transport_detail="POST /sobjects/A2ALab_Account_Brief__c",
            request_payload={**body, "Brief__c": f"({len(brief_markdown)} chars) {headline}"},
        ) as hop:
            created = await self._request(
                "POST", "/sobjects/A2ALab_Account_Brief__c", json_body=body
            )
            brief_id = created["id"]
            hop.response_payload = created

        brief_url = (
            self.my_domain.replace(".my.salesforce.com", ".lightning.force.com")
            + f"/lightning/r/A2ALab_Account_Brief__c/{brief_id}/view"
        )
        task_body = {
            "Subject": f"Daily account brief available: {headline}"[:255],
            "WhatId": account["Id"],
            "Status": "Completed",
            "ActivityDate": today,
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
            transport_detail="POST /sobjects/Task (activity on the Account)",
            request_payload=task_body,
        ) as hop:
            task = await self._request("POST", "/sobjects/Task", json_body=task_body)
            hop.response_payload = task

        # In-app (bell) notification — best-effort: a missing notification
        # type or permission must not fail the brief delivery itself.
        notified = False
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
                notified = True
        except Exception as exc:  # error hop already recorded by Hop.__exit__
            print(f"[briefs] in-app notification failed (continuing): {exc}", flush=True)

        return {
            "account_id": account["Id"],
            "account_name": account["Name"],
            "brief_id": brief_id,
            "task_id": task.get("id"),
            "notified": notified,
        }

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
