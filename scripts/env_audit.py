"""Keep `.env`, `.env.example`, and the code that reads them in agreement.

    uv run python scripts/env_audit.py            # report
    uv run python scripts/env_audit.py --json     # machine-readable

The contract is simple and worth stating because it is easy to drift from:
**`.env.example` and `.env` carry the same key list.** The example holds the
documentation, `.env` holds the values. A key referenced by code and absent
from the example is undocumented; a key in the example and absent from `.env`
is unset; a key in neither is a typo waiting to happen.

Scanning notes, learned by getting them wrong first:

* **The working tree, not `git ls-files`.** A brand-new script is exactly the
  one whose variables are undocumented, and it is also the one git has not been
  told about yet. Scanning tracked files only reported `A2ALAB_ENV_SECRET` as
  unused while `env_sync.py` sat uncommitted next to it.
* **Config counts as code.** `config/targets.yaml` expands `${VAR}` at load, so
  a var used only there is genuinely used.
* **Some references cannot be seen statically** — `SF_CLIENT_ID_{seam}` is
  built at runtime, `.claude/settings.local.json` is read by the harness, and
  deploy manifests derive names. So "referenced nowhere" is reported as a
  QUESTION, never as an error, and this module never edits `.env` itself.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ENV = REPO / ".env"
EXAMPLE = REPO / ".env.example"

SCAN_SUFFIXES = {".py", ".sh", ".yaml", ".yml", ".json", ".toml"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    "traces",
    "tmp-docs",
    "dist",
    "_build",
    ".pytest_cache",
    ".ruff_cache",
    # Vendored agent skills. They carry their own env vars for their own
    # purposes; documenting them in this lab's `.env.example` would claim they
    # are lab configuration, which is the opposite of true.
    ".agents",
    # A build artefact: deploy/adk/_build is a copy of src/ made at deploy time,
    # so every var in it is already counted at its real home.
    "_build",
}

# os.environ["X"] / os.environ.get("X") / getenv("X") / ${X} / ${X:-d} / $X in shell
_PATTERNS = (
    re.compile(r'os\.environ(?:\.get)?[\[(]\s*["\']([A-Z][A-Z0-9_]{2,})["\']'),
    re.compile(r'getenv\(\s*["\']([A-Z][A-Z0-9_]{2,})["\']'),
    re.compile(r"\$\{([A-Z][A-Z0-9_]{2,})[:\}]"),
    re.compile(r"(?<![\w$])\$([A-Z][A-Z0-9_]{2,})\b"),
)

# Set by the shell, the cloud, or a vendor SDK — not ours to document.
AMBIENT = {
    "PATH",
    "HOME",
    "PWD",
    "SHELL",
    "USER",
    "LANG",
    "TERM",
    "TMPDIR",
    "EDITOR",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "CI",
    "DEBUG",
    "LOG_LEVEL",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION",
    "AWS_ACCOUNT",
    "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI",
    "AWS_LAMBDA_FUNCTION_NAME",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
    "OTEL_METRICS_EXPORTER",
    "OTEL_EXPORTER_OTLP_PROTOCOL",
    "OTEL_RESOURCE_ATTRIBUTES",
    "OTEL_LOGS_EXPORTER",
    "CLAUDE_CODE_ENABLE_TELEMETRY",
    # Placeholders in prose and examples, not variables anyone sets.
    "VAR",
    "NAME",
    "SEAM",
    # Passed inline to a heredoc for the length of one command
    # (`A2ALAB_CURRENT=… python3 - <<PY`) — a shell local wearing an env var's
    # clothes, and never something a checkout supplies.
    "A2ALAB_CURRENT",
    "A2ALAB_JSON",
    # Same shape: passed inline to deploy_datacloud_ingress.sh's heredocs
    # (`A2ALAB_RANGES_FILE=… A2ALAB_SRC=… python3 - <<PY`) so the embedded
    # Python can read them — locals, not configuration a checkout supplies.
    "A2ALAB_RANGES_FILE",
    "A2ALAB_SRC",
}

# This file's own regexes and docstrings mention variable names; scanning it
# would report them as lab configuration.
# .kiro/hooks/forward.sh uses internal variables (KIRO_OTLP_METRICS_*, KIRO_PROJECT,
# KIRO_REPO) sourced from .kiro/hooks/.env — not user-facing env config. They are
# written by scripts/kiro_otel.sh and consumed only inside the hook.
SKIP_FILES = {"scripts/env_audit.py", ".kiro/hooks/forward.sh"}


def keys_in(path: Path) -> list[str]:
    """Declared keys in a dotenv file, commented or not, in file order."""
    if not path.exists():
        return []
    out, seen = [], set()
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*#?\s*([A-Z][A-Z0-9_]{2,})=", line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def active_keys(path: Path) -> set[str]:
    """Keys that are actually SET — commented lines do not count."""
    if not path.exists():
        return set()
    return {
        m.group(1)
        for line in path.read_text(encoding="utf-8").splitlines()
        if (m := re.match(r"^\s*([A-Z][A-Z0-9_]{2,})=", line))
    }


# A shell script's own locals look identical to env references once expanded
# (`$ACCOUNT`, `$NAME`), and there are dozens of them. A var ASSIGNED in the
# same file is that file's variable, not lab configuration — unless the
# assignment is itself a default for an env var (`X="${X:-…}"`), which is the
# one shape that means "configurable, with a fallback".
_SH_ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+)?([A-Z][A-Z0-9_]{2,})=", re.M)
_SH_SELF_DEFAULT = re.compile(r'^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,})="?\$\{\1[:\-]')


def referenced(root: Path = REPO) -> dict[str, list[str]]:
    """{VAR: [files that reference it]} across the working tree.

    Excludes shell locals and test-only fixtures — neither is configuration a
    new checkout has to supply.
    """
    hits: dict[str, set[str]] = {}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(root))
        if rel in SKIP_FILES:
            continue
        local: set[str] = set()
        if path.suffix == ".sh":
            local = set(_SH_ASSIGN.findall(text)) - set(_SH_SELF_DEFAULT.findall(text))
        for pattern in _PATTERNS:
            for name in pattern.findall(text):
                if name in AMBIENT or name in local:
                    continue
                hits.setdefault(name, set()).add(rel)
    # A var seen only under tests/ is a fixture, not lab configuration.
    return {
        k: sorted(v) for k, v in sorted(hits.items()) if not all(f.startswith("tests/") for f in v)
    }


def mentioned_anywhere(names: set[str], root: Path = REPO) -> dict[str, list[str]]:
    """{VAR: files where the literal name appears at all}.

    Deliberately looser than `referenced`, and used ONLY for the "is this
    unused?" question — where a false "unused" is the expensive answer, because
    acting on it removes a working variable. It catches the indirection that
    defeats syntactic scanning: a constant (`TRACE_DIR_ENV = "A2ALAB_TRACE_DIR"`),
    a name built at runtime (`SF_CLIENT_ID_{seam}` — matched by its prefix
    below), a value read by a vendor SDK, or a mention in a runbook.
    """
    found: dict[str, set[str]] = {n: set() for n in names}
    words = {n: re.compile(rf"\b{re.escape(n)}\b") for n in names}
    # A runtime-constructed name never appears in full; its stem does.
    stems = {n: re.compile(rf"\b{re.escape(n.rsplit('_', 1)[0])}_\{{") for n in names}
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in SCAN_SUFFIXES | {".md"}:
            continue
        if any(part in SKIP_DIRS for part in path.parts) or path.name.startswith(".env"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        rel = str(path.relative_to(root))
        if rel in SKIP_FILES:
            continue
        for name in names:
            if words[name].search(text) or stems[name].search(text):
                found[name].add(rel)
    return {k: sorted(v) for k, v in found.items()}


def audit(root: Path = REPO) -> dict:
    refs = referenced(root)
    used = set(refs)
    example = keys_in(root / ".env.example")
    env_declared = keys_in(root / ".env")
    env_active = active_keys(root / ".env")
    return {
        "referenced": len(used),
        "example_keys": len(example),
        "env_keys": len(env_declared),
        # The contract gap: code reads it, nothing documents it.
        "undocumented": sorted(used - set(example)),
        # Documented but never set locally — fine for optional knobs, a
        # missing prerequisite for anything else.
        "unset": sorted(set(example) - env_active),
        # Set locally but not documented — the next person cannot know it exists.
        "undeclared_in_example": sorted(env_active - set(example)),
        # A QUESTION, not an error: static scanning cannot see every reference,
        # so this uses the looser whole-name search and still warrants a human
        # look before anything is removed.
        "possibly_unused": sorted(
            name for name, where in mentioned_anywhere(env_active, root).items() if not where
        ),
        "refs": refs,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = audit()
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"referenced in code/config : {report['referenced']}")
    print(f".env.example keys         : {report['example_keys']}")
    print(f".env keys                 : {report['env_keys']}")
    for label, key in (
        ("referenced but NOT documented in .env.example", "undocumented"),
        ("documented but not set in .env", "unset"),
        ("set in .env but not documented", "undeclared_in_example"),
        ("set in .env, referenced nowhere (verify before removing)", "possibly_unused"),
    ):
        items = report[key]
        print(f"\n{label}: {len(items)}")
        for name in items:
            where = report["refs"].get(name, [])
            print(f"  {name}" + (f"   [{where[0]}]" if where else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
