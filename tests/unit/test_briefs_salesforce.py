"""BriefWriter account resolution (A5/F03 write-half) and idempotent delivery
(A4/F07) — WS25 b3 / D81.

The writer lands a model-written brief in a *production* org, so two things must
hold: the Account it writes against is resolved deterministically (never a
partial/arbitrary `LIKE '%%'` match, and a trusted name->Id map wins when the
operator supplied one), and a re-run for the same (session, account) does not
duplicate the brief or its Task. Both are exercised here against a fake
Salesforce transport — no network, no org.
"""

from __future__ import annotations

import pytest

from briefs.salesforce import BriefWriter


def _writer(account_map=None) -> BriefWriter:
    w = BriefWriter(my_domain="example.my.salesforce.com", client_id="x", client_secret="y")
    w.account_map = account_map or {}
    return w


class FakeSF:
    """Routes `_query` by SOQL shape and records every `_request` (upsert)."""

    def __init__(self, *, exact=None, like=None, by_id=None):
        self.exact = exact if exact is not None else []
        self.like = like if like is not None else []
        self.by_id = by_id if by_id is not None else []
        self.queries: list[str] = []
        self.upserts: list[dict] = []
        # (sobject, key) -> id, so a repeat upsert returns the same id / created=False
        self._store: dict[tuple, str] = {}

    async def query(self, soql: str):
        self.queries.append(soql)
        if "CustomNotificationType" in soql or "FROM User" in soql:
            return []  # keep the best-effort in-app alert out of the way
        if "WHERE Id =" in soql:
            return list(self.by_id)
        if "Name LIKE" in soql:
            return list(self.like)
        if "Name =" in soql:
            return list(self.exact)
        return []

    async def request(self, method, path, *, json_body=None, params=None):
        if method == "PATCH":
            self.upserts.append({"path": path, "body": json_body})
            created = path not in self._store
            if created:
                self._store[path] = f"id_{len(self._store)}"
            return {"id": self._store[path], "success": True, "created": created}
        # POST reached only if the alert path runs; it should not in these tests
        return {"id": "unexpected_post"}

    def bind(self, w: BriefWriter):
        w._query = self.query  # type: ignore[assignment]
        w._request = self.request  # type: ignore[assignment]
        return self


# --- A5: deterministic resolution -------------------------------------------


async def test_empty_account_name_is_rejected_without_a_query():
    w = _writer()
    sf = FakeSF().bind(w)
    with pytest.raises(RuntimeError, match="(?i)account name"):
        await w.save_brief(
            account_name="   ",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )
    assert sf.queries == []  # never build `LIKE '%%'`
    assert sf.upserts == []


async def test_ambiguous_exact_name_writes_nothing():
    w = _writer()
    sf = FakeSF(exact=[{"Id": "001A", "Name": "Acme"}, {"Id": "001B", "Name": "Acme"}]).bind(w)
    with pytest.raises(RuntimeError, match="(?i)ambiguous|more than one|multiple"):
        await w.save_brief(
            account_name="Acme",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )
    assert sf.upserts == []


async def test_exact_single_match_is_used_without_a_like_fallback():
    w = _writer()
    sf = FakeSF(exact=[{"Id": "001A", "Name": "Apple Inc."}]).bind(w)
    out = await w.save_brief(
        account_name="Apple Inc.",
        headline="h",
        brief_markdown="b",
        research_session_id="s1",
        trace_id="t",
    )
    assert out["account_id"] == "001A"
    assert not any("LIKE" in q for q in sf.queries)


async def test_like_fallback_used_only_when_exact_is_empty():
    w = _writer()
    sf = FakeSF(exact=[], like=[{"Id": "001C", "Name": "Contoso Ltd"}]).bind(w)
    out = await w.save_brief(
        account_name="Contoso",
        headline="h",
        brief_markdown="b",
        research_session_id="s1",
        trace_id="t",
    )
    assert out["account_id"] == "001C"
    assert any("Name LIKE" in q for q in sf.queries)


async def test_ambiguous_like_writes_nothing():
    w = _writer()
    sf = FakeSF(exact=[], like=[{"Id": "1", "Name": "A"}, {"Id": "2", "Name": "B"}]).bind(w)
    with pytest.raises(RuntimeError, match="(?i)ambiguous|more than one|multiple"):
        await w.save_brief(
            account_name="Cont",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )
    assert sf.upserts == []


async def test_soql_wildcards_and_quotes_are_escaped():
    """A crafted name must not recreate `LIKE '%%'` or break the quoting."""
    w = _writer()
    sf = FakeSF(exact=[], like=[{"Id": "001D", "Name": "weird"}]).bind(w)
    await w.save_brief(
        account_name="a'b\\c%d_e",
        headline="h",
        brief_markdown="b",
        research_session_id="s1",
        trace_id="t",
    )
    like_q = next(q for q in sf.queries if "Name LIKE" in q)
    assert "%%" not in like_q  # the wildcard body is escaped, not bare
    assert r"\'" in like_q  # quote escaped
    assert r"\%" in like_q and r"\_" in like_q  # LIKE wildcards escaped
    assert r"\\" in like_q  # backslash escaped


async def test_no_match_raises():
    w = _writer()
    FakeSF(exact=[], like=[]).bind(w)
    with pytest.raises(RuntimeError, match="(?i)no account"):
        await w.save_brief(
            account_name="Nope",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )


# --- A5: trusted name->Id map ------------------------------------------------


async def test_map_hit_uses_the_id_and_never_resolves_by_name():
    w = _writer(account_map={"Apple Inc.": "001ACCTAPPLE000001"})
    sf = FakeSF(by_id=[{"Id": "001ACCTAPPLE000001", "Name": "Apple Inc."}]).bind(w)
    out = await w.save_brief(
        account_name="Apple Inc.",
        headline="h",
        brief_markdown="b",
        research_session_id="s1",
        trace_id="t",
    )
    assert out["account_id"] == "001ACCTAPPLE000001"
    assert any("WHERE Id =" in q for q in sf.queries)
    assert not any("WHERE Name" in q for q in sf.queries)  # never resolved by name


async def test_map_binding_mismatch_rejects_the_write():
    """Map says name->id, but the org row's Name differs: attaching the brief
    would mis-file it under the wrong account. Reject, do not log-and-continue."""
    w = _writer(account_map={"Apple Inc.": "001ACCTBANANA00002"})
    sf = FakeSF(by_id=[{"Id": "001ACCTBANANA00002", "Name": "Banana Corp"}]).bind(w)
    with pytest.raises(RuntimeError, match="(?i)mismatch|does not match|conflict"):
        await w.save_brief(
            account_name="Apple Inc.",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )
    assert sf.upserts == []


async def test_map_id_not_found_rejects():
    w = _writer(account_map={"Apple Inc.": "001ACCTNOSUCH00003"})
    FakeSF(by_id=[]).bind(w)
    with pytest.raises(RuntimeError, match="(?i)no such|not found|no account"):
        await w.save_brief(
            account_name="Apple Inc.",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )


# --- A4: idempotent, race-safe delivery -------------------------------------


async def test_brief_and_task_upserted_on_the_composite_delivery_key():
    w = _writer()
    sf = FakeSF(exact=[{"Id": "001A", "Name": "Apple Inc."}]).bind(w)
    out = await w.save_brief(
        account_name="Apple Inc.",
        headline="h",
        brief_markdown="b",
        research_session_id="sess9",
        trace_id="t",
    )
    assert out["delivery_key"] == "sess9#001A"
    enc = "sess9%23001A"  # '#' is percent-encoded in the URL path segment
    paths = [u["path"] for u in sf.upserts]
    assert any(
        f"/sobjects/A2ALab_Account_Brief__c/A2ALab_Delivery_Key__c/{enc}" in p for p in paths
    )
    assert any(f"/sobjects/Task/A2ALab_Delivery_Key__c/{enc}" in p for p in paths)
    assert out["brief_id"] and out["task_id"]


async def test_second_call_for_the_same_key_does_not_duplicate():
    w = _writer()
    sf = FakeSF(exact=[{"Id": "001A", "Name": "Apple Inc."}]).bind(w)
    kw = dict(
        account_name="Apple Inc.",
        headline="h",
        brief_markdown="b",
        research_session_id="sess9",
        trace_id="t",
    )
    first = await w.save_brief(**kw)
    second = await w.save_brief(**kw)
    assert first["brief_id"] == second["brief_id"]  # same record, upsert matched
    assert first["task_id"] == second["task_id"]
    # two upsert calls per save (brief + Task), but only two distinct records
    brief_ids = {sf._store[p] for p in sf._store if "A2ALab_Account_Brief__c" in p}
    assert len(brief_ids) == 1


# --- A5: adversarial resolution (F03 write-half) -----------------------------


async def test_map_rejects_a_well_formed_non_account_id():
    """A 15/18-char id is not enough: a Contact/Opportunity id (003/006 prefix)
    in the map would mis-file the brief under a non-Account. Only the Account
    key prefix (001) is accepted, and the write never happens."""
    w = _writer(account_map={"Apple Inc.": "003CONTACTXYZ00001"})  # 003 = Contact
    sf = FakeSF(by_id=[{"Id": "003CONTACTXYZ00001", "Name": "Apple Inc."}]).bind(w)
    with pytest.raises(RuntimeError, match="(?i)malformed|account id"):
        await w.save_brief(
            account_name="Apple Inc.",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )
    assert sf.upserts == []
    assert not any("WHERE Id =" in q for q in sf.queries)  # rejected before the query


async def test_ambiguous_exact_error_lists_the_candidates():
    """The operator must be told WHICH accounts collided, so they can add the
    right one to the trusted map — a bare count is not actionable."""
    w = _writer()
    sf = FakeSF(exact=[{"Id": "001AAA", "Name": "Acme"}, {"Id": "001BBB", "Name": "Acme"}]).bind(w)
    with pytest.raises(RuntimeError) as ei:
        await w.save_brief(
            account_name="Acme",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )
    msg = str(ei.value)
    assert "001AAA" in msg and "001BBB" in msg  # both ids named
    assert sf.upserts == []


async def test_ambiguous_like_error_lists_the_candidates():
    w = _writer()
    sf = FakeSF(
        exact=[],
        like=[{"Id": "001CCC", "Name": "Contoso Ltd"}, {"Id": "001DDD", "Name": "Contoso LLC"}],
    ).bind(w)
    with pytest.raises(RuntimeError) as ei:
        await w.save_brief(
            account_name="Contoso",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )
    msg = str(ei.value)
    assert "Contoso Ltd (001CCC)" in msg and "Contoso LLC (001DDD)" in msg
    assert sf.upserts == []


# --- A4: fail-closed on an unrecoverable upsert id --------------------------


async def test_upsert_with_no_id_and_empty_requery_fails_closed():
    """A 204 upsert whose re-query finds no row must RAISE, not return id=None
    and build a `/None/view` URL while reporting success."""

    class NoIdSF(FakeSF):
        async def request(self, method, path, *, json_body=None, params=None):
            if method == "PATCH":
                self.upserts.append({"path": path, "body": json_body})
                return {}  # 204-shaped: no id in the body

    w = _writer()
    NoIdSF(exact=[{"Id": "001A", "Name": "Apple Inc."}]).bind(w)
    with pytest.raises(RuntimeError, match="(?i)no id|no row|returned no"):
        await w.save_brief(
            account_name="Apple Inc.",
            headline="h",
            brief_markdown="b",
            research_session_id="s1",
            trace_id="t",
        )


# --- A4: idempotent in-app alert, concurrency, partial-pair repair ----------


class NotifySF(FakeSF):
    """A fake whose in-app alert path actually runs: it returns a notification
    type and a recipient, and records every customNotificationAction POST. The
    PATCH upsert is atomic (check+store share no await), modelling Salesforce's
    external-id upsert, with an `await` at the top so concurrent callers still
    interleave everywhere else."""

    def __init__(self, *, prefill=None, **kw):
        super().__init__(**kw)
        self.posts: list[dict] = []
        for p in prefill or []:  # pre-existing rows (partial-pair setup)
            self._store[p] = f"pre_{len(self._store)}"

    async def query(self, soql: str):
        self.queries.append(soql)
        if "CustomNotificationType" in soql:
            return [{"Id": "0ML000000000001"}]
        if "FROM User" in soql:
            return [{"Id": "005000000000001"}]
        if "WHERE Id =" in soql:
            return list(self.by_id)
        if "Name LIKE" in soql:
            return list(self.like)
        if "Name =" in soql:
            return list(self.exact)
        return []

    async def request(self, method, path, *, json_body=None, params=None):
        import asyncio

        await asyncio.sleep(0)  # yield so gathered callers interleave up to here
        if method == "PATCH":
            self.upserts.append({"path": path, "body": json_body})
            created = path not in self._store  # atomic: no await between here...
            if created:
                self._store[path] = f"id_{len(self._store)}"  # ...and here
            return {"id": self._store[path], "success": True, "created": created}
        if method == "POST" and "customNotificationAction" in path:
            self.posts.append({"body": json_body})
            return {"id": "notif"}
        return {"id": "unexpected_post"}


def _kw(**over):
    base = dict(
        account_name="Apple Inc.",
        headline="h",
        brief_markdown="b",
        research_session_id="sess9",
        trace_id="t",
    )
    base.update(over)
    return base


async def test_first_delivery_alerts_once_redelivery_is_silent():
    w = _writer()
    sf = NotifySF(exact=[{"Id": "001A", "Name": "Apple Inc."}]).bind(w)
    first = await w.save_brief(**_kw())
    assert first["notified"] is True
    assert len(sf.posts) == 1  # the brief was created -> one alert
    second = await w.save_brief(**_kw())
    assert second["notified"] is False
    assert len(sf.posts) == 1  # re-delivery created nothing -> no second alert


async def test_concurrent_writers_make_one_pair_and_one_alert():
    """The settled race: two writers deliver the SAME (session, account) at once.
    The org's external-id upsert is atomic, so exactly one creates the brief and
    one the Task; there must be exactly one brief, one Task, and one alert."""
    import asyncio

    w = _writer()
    sf = NotifySF(exact=[{"Id": "001A", "Name": "Apple Inc."}]).bind(w)
    a, b = await asyncio.gather(w.save_brief(**_kw()), w.save_brief(**_kw()))
    assert a["brief_id"] == b["brief_id"]
    assert a["task_id"] == b["task_id"]
    brief_rows = {v for p, v in sf._store.items() if "A2ALab_Account_Brief__c" in p}
    task_rows = {v for p, v in sf._store.items() if "/Task/" in p}
    assert len(brief_rows) == 1 and len(task_rows) == 1
    assert len(sf.posts) == 1  # exactly one alert despite two concurrent writers


async def test_partial_pair_repair_brief_exists_task_missing():
    """Tick 1 created the brief and alerted, then the Task upsert failed. The
    retry must create the Task WITHOUT a second alert (the brief already exists)
    and return both ids."""
    brief_path = "/sobjects/A2ALab_Account_Brief__c/A2ALab_Delivery_Key__c/sess9%23001A"
    w = _writer()
    sf = NotifySF(exact=[{"Id": "001A", "Name": "Apple Inc."}], prefill=[brief_path]).bind(w)
    out = await w.save_brief(**_kw())
    assert out["brief_id"] and out["task_id"]
    assert out["notified"] is False  # brief pre-existed -> no new alert
    assert len(sf.posts) == 0


async def test_partial_pair_repair_task_exists_brief_missing():
    """The reverse: the Task already exists but the brief does not. Creating the
    brief fires exactly one alert; the Task upsert matches the existing row."""
    task_path = "/sobjects/Task/A2ALab_Delivery_Key__c/sess9%23001A"
    w = _writer()
    sf = NotifySF(exact=[{"Id": "001A", "Name": "Apple Inc."}], prefill=[task_path]).bind(w)
    out = await w.save_brief(**_kw())
    assert out["notified"] is True  # the brief was created now -> one alert
    assert len(sf.posts) == 1
    task_rows = {v for p, v in sf._store.items() if "/Task/" in p}
    assert len(task_rows) == 1  # the pre-existing Task was matched, not duplicated
