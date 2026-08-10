#!/usr/bin/env python
"""Mirror plan/07-workstreams.md into Jira as epics and stories (WS15).

    uv run python scripts/jira_sync.py            # DRY RUN — prints what it would do
    uv run python scripts/jira_sync.py --apply    # create/update in Jira

The plan stays the source of truth for scope and `plan/00-decisions.md` for
reasoning; Jira is the delivery view. So this runs one way — repo → Jira — and
is safe to re-run: it matches existing issues by summary and updates rather than
duplicating.

WHAT IT DOES NOT DO, on purpose:

- **It does not create an issue per ADR.** There are 58, and a decision is not a
  unit of work. They ride along as `adr-D<n>` labels, so a closed story still
  names the decision that justified it.
- **It does not invent sprints.** The board is Kanban (WS15): a sprint is a time
  box and a workstream is a scope box, and fourteen one-workstream sprints would
  describe a process that never happened.
- **It does not tidy the history.** A workstream that was raised, half-built and
  revised days later says so, because the plan says so.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

PLAN = Path("plan/07-workstreams.md")
# Jira's UI calls this container a SPACE now; the REST API and JQL still call it
# a `project` (/rest/api/3/project, `project = A2A`). The name here follows the
# API, so it is not stale — but a reader coming from the UI will be looking for
# the other word.
PROJECT = os.environ.get("JIRA_PROJECT_KEY", "A2A")

# "## WS13 — Full hosting: take the laptop off the runtime path (raised 2026-07-28)"
WS_RE = re.compile(r"^## (WS\d+) — (.+?)\s*$", re.M)

# The plan records work in TWO shapes, because it was written over two weeks and
# the later workstreams got tables. Both are imported; nothing else is.
#
# 1. A work-items table row — WS13/14/15:      | 3 | Grant iam:… | **done** |
# 2. A statused numbered line — WS1–WS12:      3. ✅ Matrix cells recorded → …
#
# Everything else in those sections is narrative: goals, credentials, cost notes,
# setup instructions the operator followed once. Turning prose into stories would
# invent a granularity the work never had, so a workstream with no items of
# either shape imports as an epic with no children, and its epic says so.
TABLE_ITEM_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.*?)\s*\|\s*$", re.M)
STATUS_ITEM_RE = re.compile(
    r"^(\d+)\.\s*([✅⏳❌])\s*(.+?)(?=\n\d+\.\s*[✅⏳❌]|\n\n|\Z)", re.M | re.S
)

DONE_RE = re.compile(r"\b(done|shipped|complete|provisioned|deployed|live)\b", re.I)
NOT_DONE_RE = re.compile(r"\bnot (started|done)\b|\boperator action\b|\bblocked\b", re.I)
ADR_RE = re.compile(r"\bD(\d{1,3})\b")

# A repo path named in the item text — the same shape console/architecture.py
# links, and restricted to real top-level directories for the same reason: so
# ordinary prose containing a slash is not mistaken for a file.
REPO_ROOTS = ("src", "scripts", "deploy", "config", "tests", "plan", "salesforce", "docs")
PATH_RE = re.compile(r"\b(?:" + "|".join(REPO_ROOTS) + r")/[\w./-]*[\w/]")
REPO_URL = os.environ.get("A2ALAB_REPO_URL", "https://github.com/congmingwudi/a2a-lab")
REPO_BRANCH = os.environ.get("A2ALAB_REPO_BRANCH", "main")


def repo_links(text: str, root: Path) -> list[str]:
    """Evidence links for the paths an item names.

    Existence-checked against the working tree, because a link that 404s in
    front of an audience is worse than no link — the same rule the console's
    architecture view already applies to the deployment map.
    """
    out: list[str] = []
    for raw in PATH_RE.findall(text):
        path = raw.rstrip("/.")
        target = root / path
        if not target.exists() or path in out:
            continue
        out.append(path)
    return [
        f"{REPO_URL}/{'tree' if (root / p).is_dir() else 'blob'}/{REPO_BRANCH}/{p}" for p in out
    ]


def _api(method: str, path: str, body: dict | None = None) -> dict:
    site = os.environ["JIRA_SITE_URL"].rstrip("/")
    cred = base64.b64encode(
        f"{os.environ['JIRA_EMAIL']}:{os.environ['JIRA_API_TOKEN']}".encode()
    ).decode()
    req = urllib.request.Request(
        f"{site}{path}",
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Basic {cred}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:400]
        raise RuntimeError(f"{method} {path} -> {exc.code}: {detail}") from None


def _adf(text: str) -> dict:
    """Jira Cloud wants Atlassian Document Format, not markdown."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p[:3000]}]} for p in paras
        ]
        or [{"type": "paragraph", "content": []}],
    }


def _clean(text: str) -> str:
    """One line of plain text from wrapped markdown."""
    return re.sub(r"\s+", " ", re.sub(r"[`*]", "", text)).strip()


def _status_line(body: str) -> str:
    """The workstream's own status paragraph, for the epic description.

    Stops at the first numbered item as well as at the blank line, because
    WS1–WS3 write the status as a heading immediately followed by the list —
    without that the "paragraph" swallows every item under it.
    """
    m = re.search(r"^\**Status\b.*?(?=\n\n|\n\d+\.\s|\Z)", body, re.M | re.S)
    return _clean(m.group(0))[:600] if m else ""


def parse_items(body: str) -> list[dict]:
    items: list[dict] = []
    for m in TABLE_ITEM_RE.finditer(body):
        n, summary, state = m.group(1), m.group(2), m.group(3)
        if set(summary) <= set("-| ") or summary.strip().lower() == "item":
            continue  # header / separator row
        items.append(
            {
                "n": int(n),
                "summary": _clean(summary)[:250],
                "state": _clean(state) or "—",
                "done": bool(DONE_RE.search(state)) and not NOT_DONE_RE.search(state),
            }
        )
    if items:
        return items
    for m in STATUS_ITEM_RE.finditer(body):
        n, mark, text = m.group(1), m.group(2), _clean(m.group(3))
        items.append(
            {
                "n": int(n),
                # The first clause is the item; the rest is the evidence, and it
                # belongs in the description, not in a 300-character summary.
                "summary": re.split(r"\s+[—:]\s+|\.\s+", text)[0][:250],
                "state": text[:600],
                "done": mark == "✅",
            }
        )
    return items


def parse_plan() -> list[dict]:
    """[{ws, title, adrs, done, status, items:[{n, summary, state, done}]}]."""
    text = PLAN.read_text(encoding="utf-8")
    heads = list(WS_RE.finditer(text))
    out = []
    for i, h in enumerate(heads):
        body = text[h.end() : heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        title = _clean(h.group(2))[:250]
        items = parse_items(body)
        status = _status_line(body)
        # An epic closes ONLY on the arithmetic of its stories: it has stories
        # and every one of them is done. Nothing here tries to read the status
        # prose for a verdict — "Everything that does not need AWS is done" and
        # "PROVISIONED … exit criteria are not met yet" both contain the word
        # "done" and neither means the workstream is finished.
        #
        # So a workstream the plan records only as narrative stays OPEN even
        # when it shipped. That is the safe direction to be wrong in: an open
        # epic carrying its verbatim status invites someone to read it, while a
        # wrongly-closed one buries the work that is still outstanding. Closing
        # those is a human call, made in Jira, not a guess made here.
        done = bool(items) and all(it["done"] for it in items)
        out.append(
            {
                "ws": h.group(1),
                "title": title,
                # Numeric sort, not lexical: `sorted(key=len)` quietly kept the
                # ten LOWEST-numbered ADRs, which for a workstream written last
                # week are the ten least relevant ones.
                "adrs": [
                    f"adr-D{d}"
                    for d in sorted({int(x) for x in ADR_RE.findall(body)}, reverse=True)[:12]
                ],
                "done": done,
                "status": status,
                "items": items,
            }
        )
    out.sort(key=lambda w: int(w["ws"][2:]))
    return out


def load_index() -> dict[str, str]:
    """{summary: key} for every issue in the project.

    Matching used to ask JQL `summary ~ "<text>"` per issue. That is a TEXT
    search, not an equality test: a summary containing `(`, `+`, `:` or `→` —
    which is most of them, since these come out of prose — silently matched
    nothing, so a re-run created 23 duplicates instead of updating in place.
    Fetching the project once and comparing strings in Python is both correct
    and one request rather than one per issue.
    """
    index: dict[str, str] = {}
    token = None
    while True:
        body = {
            "jql": f"project = {PROJECT} ORDER BY key",
            "maxResults": 100,
            "fields": ["summary"],
        }
        if token:
            body["nextPageToken"] = token
        res = _api("POST", "/rest/api/3/search/jql", body)
        for issue in res.get("issues") or []:
            index.setdefault(issue["fields"]["summary"].strip(), issue["key"])
        token = res.get("nextPageToken")
        if not token or res.get("isLast"):
            return index


INDEX: dict[str, str] = {}


def upsert(summary: str, issue_type: str, desc: str, labels: list[str], parent: str | None) -> str:
    existing = INDEX.get(summary.strip())
    fields = {
        "project": {"key": PROJECT},
        "summary": summary,
        "issuetype": {"name": issue_type},
        "description": _adf(desc),
        "labels": labels,
    }
    if parent:
        fields["parent"] = {"key": parent}
    if existing:
        _api("PUT", f"/rest/api/3/issue/{existing}", {"fields": fields})
        return existing
    key = _api("POST", "/rest/api/3/issue", {"fields": fields})["key"]
    INDEX[summary.strip()] = key  # so a re-entry in the same run updates, not duplicates
    return key


def transition_done(key: str) -> str:
    """Move an issue to a Done-category status, whatever this project calls it."""
    tr = _api("GET", f"/rest/api/3/issue/{key}/transitions").get("transitions") or []
    for t in tr:
        if (t.get("to") or {}).get("statusCategory", {}).get("key") == "done":
            _api("POST", f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": t["id"]}})
            return t["to"]["name"]
    return ""


def main() -> int:
    load_dotenv()
    apply = "--apply" in sys.argv
    for var in ("JIRA_SITE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN"):
        if not os.environ.get(var):
            sys.exit(f"{var} not set — see WS15 in plan/07-workstreams.md")

    root = Path.cwd()
    if apply:
        INDEX.update(load_index())
    plan = parse_plan()
    n_items = sum(len(w["items"]) for w in plan)
    print(f"{'APPLY' if apply else 'DRY RUN'} — {len(plan)} workstreams, {n_items} work items")
    print(f"space {PROJECT} on {os.environ['JIRA_SITE_URL']}\n")

    created = []
    for w in plan:
        summary = f"{w['ws']} — {w['title']}"
        # Three genuinely different situations, and calling them all "narrative"
        # would misreport the two that have not begun.
        if w["items"]:
            shape = f"{len(w['items'])} work item(s) tracked as stories under this epic."
        elif w["status"]:
            shape = (
                "The plan records this workstream as narrative, not as a work-item "
                "list, so it has no stories. That is the honest shape of how it was "
                "run — the detail is in the plan section, not lost."
            )
        else:
            shape = (
                "Not started: the plan describes the intent and has no status or work "
                "items for it yet. Stories appear here when the work does."
            )
        desc = (
            f"Workstream {w['ws']}, imported from plan/07-workstreams.md.\n\n"
            f"{w['status'] or 'No status recorded in the plan yet.'}\n\n"
            f"{shape}\n\nScope and reasoning stay in the plan and in the ADRs "
            "(plan/00-decisions.md); this epic is the delivery view, not a second "
            "source of truth."
        )
        labels = ["workstream", *w["adrs"], *(["not-started"] if shape.startswith("Not") else [])]
        mark = "done" if w["done"] else "open"
        if not apply:
            print(f"  EPIC  {summary[:74]:76} [{mark:4}] {len(w['items'])} items")
            for it in w["items"]:
                print(f"      {'done' if it['done'] else 'open':5} {it['summary'][:68]}")
            continue

        key = upsert(summary, "Epic", desc, labels, None)
        created.append(key)
        print(f"  {key:8} EPIC  {summary[:64]}")
        for it in w["items"]:
            # Jira caps a summary at 255 chars. The item text is already
            # truncated to 250, but the "WS20.10 — " prefix can push the
            # composed summary over, so bound the whole thing (the full text
            # lives in the description regardless).
            s = f"{w['ws']}.{it['n']} — {it['summary']}"[:255]
            links = repo_links(it["state"], root)
            d = (
                f"Work item {it['n']} of {w['ws']}, imported from "
                f"plan/07-workstreams.md.\n\nAs recorded there: {it['state']}"
            )
            if links:
                d += "\n\nSource: " + "  ".join(links)
            ik = upsert(s, "Story", d, ["workstream-item", *w["adrs"][:5]], key)
            if it["done"]:
                transition_done(ik)
            print(f"    {ik:8} {'done' if it['done'] else 'open':5} {it['summary'][:50]}")
        if w["done"]:
            transition_done(key)

    if not apply:
        print("\nnothing created — re-run with --apply")
    else:
        print(f"\n{len(created)} epics synced")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
