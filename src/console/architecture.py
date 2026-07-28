"""Parse plan/09-deployment-map.md into the console's Architecture section.

The markdown file is the SOURCE, not a copy. The alternative — mermaid in
`config/diagrams.yaml` like the insight diagrams, with prose duplicated into the
plan doc — would put the same picture in two places, and the deployment map is
exactly the document that goes stale when there are two of it.

So the parse is deliberately dumb and the format is a convention the doc already
wants to follow: `## L<n> — <title>` starts a level, the first ```mermaid fence
inside it is that level's diagram, and everything else is its prose. Anything
before the first level is the intro; anything after the last `## L<n>` section
(the appendices — "Checking reality", "Why not") comes back as trailing
sections, so the console shows the whole document rather than only the pictures.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

DOC_PATH = Path("plan/09-deployment-map.md")

# The public repo. Every file the document names links here, so a reader can
# open the actual source of anything the diagrams claim.
REPO_URL = os.environ.get("A2ALAB_REPO_URL", "https://github.com/congmingwudi/a2a-lab")
REPO_BRANCH = os.environ.get("A2ALAB_REPO_BRANCH", "main")

# Speaker prep lives in the same file as the document — one source — but is
# lifted out of `sections` so the reader-facing render never contains it. The
# API only returns it to a signed-in reviewer.
PRESENTER_HEADING = "Presenter notes"

# "## L0 — The estate: five homes, one lab" (em dash or hyphen, either way).
# A level may carry a decimal — L5.5 (DNS) was inserted between the
# observability level and the code→deployment table, which is named as "L6" in
# CLAUDE.md and in the console's own copy, so renumbering to make room would
# have broken references outside this file. Without the decimal the section is
# silently demoted to an appendix: the doc still renders, the Architecture
# section just quietly loses a level.
_LEVEL_RE = re.compile(r"^##\s+(L\d+(?:\.\d+)?)\s*[—-]\s*(.+?)\s*$", re.M)
_SECTION_RE = re.compile(r"^##\s+(?!L\d+(?:\.\d+)?\s*[—-])(.+?)\s*$", re.M)
_MERMAID_RE = re.compile(r"```mermaid\n(.*?)```", re.S)

# A repo-relative path written in the prose, usually inside backticks:
# `scripts/identity_preflight.py`, `src/bridge/`, `deploy/obs/build_zips.sh`.
# Restricted to the repo's real top-level directories so ordinary prose
# containing a slash cannot be mistaken for a path.
_REPO_ROOTS = ("src", "scripts", "deploy", "config", "tests", "plan", "salesforce", "docs")
_FILE_REF_RE = re.compile(r"\b(?:" + "|".join(_REPO_ROOTS) + r")/[\w./-]*[\w/]")


def _published_paths(root: Path) -> set[str] | None:
    """Every path present on the branch the links point at.

    NOT the local filesystem. Linking on disk-existence produced links that
    404'd for a whole session's worth of new files: they existed here and had
    never been committed, so `github.com/.../blob/main/scripts/expiry_report.py`
    was a promise the remote could not keep. The question a link has to answer
    is "is this on the branch I am about to send someone to", and only git
    knows that.

    Returns None when git cannot answer, in which case the caller falls back to
    filesystem existence — better a possibly-dead link than no links at all in
    a tarball checkout.
    """
    for ref in (f"origin/{REPO_BRANCH}", REPO_BRANCH):
        try:
            out = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", ref],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if out.returncode == 0 and out.stdout.strip():
            return set(out.stdout.split("\n"))
    return None


def repo_files(markdown: str, root: Path) -> list[dict]:
    """Repo paths named in the doc that exist ON THE PUBLISHED BRANCH.

    Same rule the doc chips follow, for the same reason: a link that 404s in
    front of an audience is worse than plain text. A file that is real on this
    laptop but not yet pushed is exactly that kind of broken link, so it is
    rendered as plain code until it lands.
    """
    published = _published_paths(root)
    out: dict[str, dict] = {}
    for raw in _FILE_REF_RE.findall(markdown):
        path = raw.rstrip("/")
        if path in out:
            continue
        target = root / path
        if not target.exists():
            continue
        is_dir = target.is_dir()
        if published is not None:
            on_branch = (
                any(p.startswith(path + "/") for p in published) if is_dir else path in published
            )
            if not on_branch:
                continue
        kind = "tree" if is_dir else "blob"
        out[path] = {
            "path": path,
            "kind": "dir" if is_dir else "file",
            "url": f"{REPO_URL}/{kind}/{REPO_BRANCH}/{path}",
        }
    # Longest first: the UI matches these against rendered text, and `src/bridge`
    # must not win over `src/bridge/deploy.py` by being found first.
    return sorted(out.values(), key=lambda f: (-len(f["path"]), f["path"]))


def _split_diagram(body: str) -> tuple[str, str]:
    """(mermaid source, prose with the fence removed)."""
    match = _MERMAID_RE.search(body)
    if not match:
        return "", body.strip()
    prose = (body[: match.start()] + body[match.end() :]).strip()
    return match.group(1).strip(), prose


def parse(markdown: str) -> dict:
    """{intro, levels: [{id, title, mermaid, prose}], sections: [{title, body}]}."""
    heads = list(_LEVEL_RE.finditer(markdown))
    if not heads:
        return {"intro": markdown.strip(), "levels": [], "sections": []}

    intro = markdown[: heads[0].start()].strip()
    levels: list[dict] = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(markdown)
        body = markdown[head.end() : end]
        # A non-level "## " heading ends the last level and starts the
        # appendices — otherwise "Checking reality" would be swallowed into L6.
        if i + 1 == len(heads):
            tail = _SECTION_RE.search(body)
            if tail:
                body = body[: tail.start()]
        mermaid, prose = _split_diagram(body)
        levels.append(
            {
                "id": head.group(1),
                "title": head.group(2),
                "mermaid": mermaid,
                "prose": prose,
            }
        )

    after = markdown[heads[-1].end() :]
    sections: list[dict] = []
    presenter = ""
    marks = list(_SECTION_RE.finditer(after))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(after)
        body = after[mark.end() : end].strip()
        if mark.group(1).strip().lower() == PRESENTER_HEADING.lower():
            presenter = body
            continue
        sections.append({"title": mark.group(1), "body": body})
    return {
        "intro": intro,
        "levels": levels,
        "sections": sections,
        "presenter": presenter,
    }


def load(path: str | Path = DOC_PATH, root: Path | None = None) -> dict:
    doc = Path(path)
    if not doc.exists():
        return {
            "intro": "",
            "levels": [],
            "sections": [],
            "presenter": "",
            "files": [],
            "error": f"{doc} not found",
        }
    markdown = doc.read_text(encoding="utf-8")
    parsed = parse(markdown)
    parsed["path"] = str(doc)
    parsed["repo_url"] = REPO_URL
    parsed["files"] = repo_files(markdown, root or Path.cwd())
    return parsed
