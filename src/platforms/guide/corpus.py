"""Grounding corpus for the Lab Guide (plan/07-workstreams.md, Lab Guide).

The lab documents itself — README, the ADR log, the plan docs — so the
guide has no separate knowledge base to maintain: CORE_DOCS are stuffed
into the system prompt (cheap on repeat turns via prompt caching), and
the long tail (the full ADR log, results, runbooks, workstreams) sits
behind read tools so the model pulls only what a question needs.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

# Stuffed into every turn's system prompt (~57KB ≈ 14k tokens, cached).
CORE_DOCS = [
    "README.md",
    "plan/01-architecture.md",
    "plan/02-matrix.md",
    "plan/08-insights.md",
]

# Readable on demand via the read_doc tool — same whitelist ethos as the
# console's /api/docs endpoint.
TOOL_DOCS = [
    "plan/00-decisions.md",
    "plan/03-results.md",
    "plan/04-runbooks.md",
    "plan/05-observability.md",
    "plan/07-workstreams.md",
    "config/targets.yaml",
    "config/scenarios.yaml",
    "docs/lab-guide-mcp.md",
]

# Same heading grammar the console's /api/decisions parser uses.
_DECISION_HEADING = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (D\d+)( \(revised\))?: (.+)$")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def read_doc(name: str) -> str:
    """One whitelisted doc, verbatim. Raises ValueError off-whitelist so the
    tool result tells the model what it may ask for instead."""
    if name not in TOOL_DOCS and name not in CORE_DOCS:
        raise ValueError(f"unknown doc '{name}' — readable docs: {', '.join(TOOL_DOCS)}")
    return (_repo_root() / name).read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _decision_sections() -> dict[str, dict]:
    """{"D28": {"id", "date", "title", "markdown"}} — revised decisions keep
    every entry in one body, latest heading wins the title."""
    path = _repo_root() / "plan" / "00-decisions.md"
    if not path.exists():
        return {}
    decisions: dict[str, dict] = {}
    current: dict | None = None
    body: list[str] = []

    def flush() -> None:
        if current is None:
            return
        section = f"### {current['date']} — {current['heading']}\n" + "\n".join(body).strip()
        entry = decisions.setdefault(
            current["id"], {"id": current["id"], "title": "", "date": "", "markdown": ""}
        )
        entry["title"], entry["date"] = current["title"], current["date"]
        sep = "\n\n---\n\n" if entry["markdown"] else ""
        entry["markdown"] += sep + section

    for line in path.read_text(encoding="utf-8").splitlines():
        match = _DECISION_HEADING.match(line)
        if match:
            flush()
            date, did, revised, title = match.groups()
            current = {
                "id": did,
                "date": date,
                "title": title,
                "heading": f"{did}{revised or ''}: {title}",
            }
            body = []
        elif line.startswith("## "):
            flush()
            current = None
        elif current is not None:
            body.append(line)
    flush()
    return decisions


def decisions_index() -> list[dict]:
    """[{id, date, title}] — small enough to stuff, so the model knows what
    exists before reaching for get_decision."""
    return [
        {"id": d["id"], "date": d["date"], "title": d["title"]}
        for d in _decision_sections().values()
    ]


def get_decision(decision_id: str) -> str:
    entry = _decision_sections().get(decision_id.strip().upper())
    if entry is None:
        known = ", ".join(sorted(_decision_sections(), key=lambda x: int(x[1:])))
        raise ValueError(f"unknown decision '{decision_id}' — known: {known}")
    return entry["markdown"]


PERSONA = """\
You are the Lab Guide — the docent for the A2A Interop Lab, a working \
cross-platform agent-to-agent laboratory spanning Salesforce Agentforce, \
Claude (Managed Agents + AgentCore), OpenAI (AgentCore), Google ADK (Vertex \
AI Agent Engine), and Microsoft Foundry, with every direction runnable over \
REST, MCP, and the A2A protocol and raw wire payloads recorded.

Visitors ask how the lab was built: call paths and protocol seams, the \
bridge/shim/direct routes, each platform's observability surface, how the \
agents are written and hosted, and what the findings mean. Answer from the \
grounding documents below and your read tools — never from general \
knowledge about these platforms when the lab's own record answers.

Rules of the house:
- The lab's core ethos is HONEST STATUS: native vs via-bridge vs via-shim \
vs blocked-beta distinctions matter. Preserve them; never round a shim up \
to "supports the protocol".
- Cite your sources inline the way the docs do — ADR ids (D27, D34), doc \
names (plan/02-matrix.md), trace ids — so a reader can verify.
- Use the tools when a question needs the ADR log, results tables, briefs, \
or an actual run's wire record; don't guess numbers. Measured figures come \
from plan/03-results.md or a dated ADR, nowhere else.
- Be concise and concrete: a visitor wants the mechanism and the finding, \
not a survey. Plain prose, short paragraphs.
- You explain the lab; you do not run experiments, change config, or give \
advice unrelated to the lab.
- You are yourself a lab exhibit: ADR D35 and the Lab Guide sections of \
plan/07-workstreams.md and plan/04-runbooks.md document how you are built \
(grounding corpus, read tools, prompt caching economics, the REST/MCP/A2A \
meta exhibit). When asked how you work, fetch them — get_decision('D35') \
and read_doc — and answer from the record like any other lab question.\
"""


@lru_cache(maxsize=1)
def system_prompt() -> str:
    """Persona + stuffed core docs + the ADR index. Cached per process; the
    Anthropic prompt cache makes repeat turns cheap on the wire too."""
    parts = [PERSONA]
    for name in CORE_DOCS:
        parts.append(f"\n\n===== {name} =====\n\n{read_doc(name)}")
    index_lines = "\n".join(f"- {d['id']} ({d['date']}): {d['title']}" for d in decisions_index())
    parts.append(
        "\n\n===== ADR index (plan/00-decisions.md — fetch bodies with "
        f"get_decision) =====\n\n{index_lines}"
    )
    return "".join(parts)
