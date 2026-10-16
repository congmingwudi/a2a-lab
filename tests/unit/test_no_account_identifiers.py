"""The repo names cloud regions, never cloud accounts — nor Salesforce orgs.

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


# A Salesforce org id: key prefix 00D, 15 or 18 chars. Retrieved ECA metadata
# carries it in orgScopedExternalApp / oauthLink, which is how it was committed
# until scripts/render_eca_metadata.py (2026-10-15). Case-insensitive, because
# Salesforce lowercases it inside generated usernames (`…@00d…ext`).
_SF_ORG_ID = re.compile(r"(?<![A-Za-z0-9])00D[A-Za-z0-9]{12}(?:[A-Za-z0-9]{3})?", re.I)
# KNOWN, not fixed (D88, WS27 item 24): the agent user Salesforce generates for
# every Agentforce agent embeds the org id, and each agent's deployable source
# names it — `default_agent_user` in an Agent Script bundle, `<botUser>` in a
# Bot. Exempt exactly those two fields so a leak anywhere else still fails.
_AGENT_USER_FIELD = re.compile(r"^\s*(default_agent_user:|<botUser>)")
_HEX = re.compile(r"[0-9a-f]+", re.I)  # a content hash (uv.lock URLs), not an id


def _org_id_in(line: str) -> bool:
    return any(not _HEX.fullmatch(m.group(0)) for m in _SF_ORG_ID.finditer(line))


def test_no_salesforce_org_ids_in_tracked_files():
    hits = [
        f"{path.relative_to(REPO)}:{i}"
        for path, text in _text_files()
        for i, line in enumerate(text.splitlines(), 1)
        if _org_id_in(line) and not _AGENT_USER_FIELD.match(line)
    ]
    assert hits == [], f"Salesforce org id committed: {hits}"


def _env_values(*keys: str) -> dict[str, str]:
    """Values from .env, which is gitignored: CI has none and the check skips there."""
    env = REPO / ".env"
    if not env.exists():
        return {}
    found = {}
    for line in env.read_text().splitlines():
        k, sep, v = line.partition("=")
        if sep and k.strip() in keys and v.strip().strip('"'):
            found[k.strip()] = v.strip().strip('"')
    return found


def test_no_salesforce_usernames_or_contact_emails_in_tracked_files():
    """The org's username DOMAIN (every user in the org shares it) and the ECA
    contact email, read from .env so the test itself names neither. They were
    committed in ECA metadata until 2026-10-15 and remain in history (D88)."""
    import pytest

    vals = _env_values(
        "SF_AGENT_CALLER_RUNAS_USER",
        "SF_BRIEFS_RUNAS_USER",
        "SF_OBS_RUNAS_USER",
        "SF_TAB_EMBED_RUNAS_USER",
        "SF_MEMORY_RUNAS_USER",
        "SF_ECA_CONTACT_EMAIL",
    )
    needles = {v.partition("@")[2] for k, v in vals.items() if k.endswith("_RUNAS_USER")}
    needles |= {v for k, v in vals.items() if k == "SF_ECA_CONTACT_EMAIL"}
    needles.discard("")
    if not needles:
        pytest.skip("no Salesforce identifiers in .env to check for (CI)")
    hits = [
        f"{path.relative_to(REPO)}:{i}"
        for path, text in _text_files()
        for i, line in enumerate(text.splitlines(), 1)
        if any(n in line for n in needles)
    ]
    assert hits == [], f"Salesforce username domain or contact email committed: {hits}"


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
