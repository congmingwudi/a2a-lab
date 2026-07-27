"""The repo names cloud regions, never cloud accounts.

The account id and the SSO profile name identify whose cloud this is and who
pays for it. This is a public repo, so they are kept in `.env` and
`.a2alab/accounts.md` — both gitignored — and every AWS deploy proves its target
account at runtime instead of naming it in source.

These tests are the boundary check: they fail on the way IN, which is the only
point at which removing an identifier is cheap. Once it is pushed, it is in
history.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# Deliberately not written out in full anywhere else in the repo.
_ACCOUNT_ID = re.compile(r"\b\d{12}\b")
_PROFILE_NAME = re.compile(r"\bembark\b", re.I)

# Fixtures and docs legitimately contain 12-digit strings that are not accounts
# (timestamps, ids, example ARNs). Only flag digits that read as an account.
_ACCOUNT_CONTEXT = re.compile(
    r"(arn:aws[\w-]*:[\w-]*:[\w-]*:(\d{12})|account[^\n]{0,24}?(\d{12})|(\d{12})[^\n]{0,24}?account)",
    re.I,
)


def _tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True)
    return [REPO / line for line in out.stdout.split("\n") if line.strip()]


def _text_files():
    for path in _tracked_files():
        if path.suffix in {".png", ".jpg", ".gif", ".ico", ".zip", ".pdf"}:
            continue
        try:
            yield path, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue


def test_no_sso_profile_name_in_tracked_files():
    """`aws sso login` — never `--profile <name>`. The profile comes from .env."""
    hits = [
        f"{path.relative_to(REPO)}:{i}"
        for path, text in _text_files()
        for i, line in enumerate(text.splitlines(), 1)
        if _PROFILE_NAME.search(line)
    ]
    assert hits == [], f"SSO profile name committed: {hits}"


def test_no_aws_account_ids_in_tracked_files():
    hits = []
    for path, text in _text_files():
        if path.name == Path(__file__).name:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            match = _ACCOUNT_CONTEXT.search(line)
            if match and _ACCOUNT_ID.search(match.group(0)):
                hits.append(f"{path.relative_to(REPO)}:{i}")
    assert hits == [], f"AWS account id committed: {hits}"


def test_every_aws_deploy_script_sources_the_preflight():
    """A deploy that lands in the wrong account creates real, billable,
    wrongly-placed infrastructure and stays quiet about it. The guard only works
    if every script actually sources it — including the next one someone adds."""
    # The rule is behavioural, not positional: a script needs the guard exactly
    # when it calls the AWS CLI. deploy/bridge/gcp_federation.sh is pure gcloud
    # and correctly has none — and a new script that starts calling `aws` gets
    # caught here without anyone remembering to add it to a list.
    calls_aws = re.compile(r"^\s*(?:\w+=\$\()?aws\s+[a-z0-9-]+\s", re.M)
    scripts = sorted(p for p in (REPO / "deploy").rglob("*.sh") if p.name != "aws_preflight.sh")
    assert scripts, "no deploy scripts found — did the layout change?"
    missing = []
    for path in scripts:
        text = path.read_text(encoding="utf-8")
        if calls_aws.search(text) and "aws_preflight.sh" not in text:
            missing.append(str(path.relative_to(REPO)))
    assert missing == [], f"AWS deploy scripts with no account guard: {missing}"
