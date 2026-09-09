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
    w = _writer(account_map={"Apple Inc.": "001000000000001AAA"})
    sf = FakeSF(by_id=[{"Id": "001000000000001AAA", "Name": "Apple Inc."}]).bind(w)
    out = await w.save_brief(
        account_name="Apple Inc.",
        headline="h",
        brief_markdown="b",
        research_session_id="s1",
        trace_id="t",
    )
    assert out["account_id"] == "001000000000001AAA"
    assert any("WHERE Id =" in q for q in sf.queries)
    assert not any("WHERE Name" in q for q in sf.queries)  # never resolved by name


async def test_map_binding_mismatch_rejects_the_write():
    """Map says name->id, but the org row's Name differs: attaching the brief
    would mis-file it under the wrong account. Reject, do not log-and-continue."""
    w = _writer(account_map={"Apple Inc.": "001000000000002AAA"})
    sf = FakeSF(by_id=[{"Id": "001000000000002AAA", "Name": "Banana Corp"}]).bind(w)
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
    w = _writer(account_map={"Apple Inc.": "001000000000003AAA"})
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
