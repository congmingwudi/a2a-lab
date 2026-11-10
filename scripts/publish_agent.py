"""Publish an Agent Script bundle whose source names its agent user by placeholder.

An agent's Digital Agent User is a username, and a username carries the org's
username domain, which never goes in a tracked file (CLAUDE.md, D88). So a bundle
can name it as `{{env:SF_..._RUNAS_USER}}` in `default_agent_user` (and the
retrieved Bot's `<botUser>`), and this wrapper fills those in from .env for the
length of the publish, then puts the placeholders back: in the files it rendered
AND in whatever `sf agent publish` retrieved, which writes the real username
straight back into the project. This is WS27 item 24's mechanism, applied first
to A2ALab_Research_Assistant_Script (D106); the other bundles still carry their
shared generated user under the guard's exemption.

    uv run python scripts/publish_agent.py A2ALab_Research_Assistant_Script            # validate
    uv run python scripts/publish_agent.py A2ALab_Research_Assistant_Script --publish [--activate]

Every publish lands in the PRODUCTION org, so the default only validates.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

from setup_jwt_eca import load_env

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / "salesforce" / "force-app" / "main" / "default"
PLACEHOLDER = re.compile(r"\{\{env:([A-Z0-9_]+)\}\}")


def bundle_files(name: str) -> list[Path]:
    files = [DEFAULT / "aiAuthoringBundles" / name / f"{name}.agent"]
    files += sorted((DEFAULT / "bots" / name).glob("*.bot-meta.xml"))
    return [f for f in files if f.exists()]


def render(files: list[Path], env: dict[str, str]) -> dict[str, str]:
    """Fill placeholders in place; return {rendered value: placeholder}."""
    used: dict[str, str] = {}
    for f in files:
        text = f.read_text()

        def sub(m: re.Match) -> str:
            key = m.group(1)
            if not env.get(key):
                sys.exit(f"{f.relative_to(ROOT)} needs {key}; set it in .env")
            used[env[key]] = m.group(0)
            return env[key]

        f.write_text(PLACEHOLDER.sub(sub, text))
    return used


def holding(values) -> set[Path]:
    """Source files that already contain any of the values."""
    return {
        f
        for f in DEFAULT.rglob("*")
        if f.is_file()
        and f.suffix in {".agent", ".xml"}
        and any(v in f.read_text(errors="ignore") for v in values)
    }


def restore(used: dict[str, str], keep: set[Path] = frozenset()) -> list[Path]:
    """Put every placeholder back, wherever publish's retrieve wrote the value.

    `keep` is the files that held the value BEFORE the run: the rendered ECA
    policy names its run-as legitimately (gitignored, D88), so it is left alone.
    """
    touched = []
    for f in DEFAULT.rglob("*"):
        if not f.is_file() or f.suffix not in {".agent", ".xml"} or f in keep:
            continue
        text = f.read_text(errors="ignore")
        new = text
        for value, placeholder in used.items():
            new = new.replace(value, placeholder)
        if new != text:
            f.write_text(new)
            touched.append(f)
    return touched


def sf(*args: str) -> None:
    print("  sf " + " ".join(args), flush=True)
    rc = subprocess.run(["sf", *args], cwd=ROOT / "salesforce").returncode
    if rc:
        raise SystemExit(f"sf {args[0]} {args[1]} failed (exit {rc})")


def main(argv: list[str] | None = None) -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle")
    ap.add_argument("--publish", action="store_true", help="publish (default: validate only)")
    ap.add_argument("--activate", action="store_true", help="activate after publishing")
    ap.add_argument("-o", "--target-org", default=os.environ.get("SF_TARGET_ORG"))
    args = ap.parse_args(argv)
    if not args.target_org:
        sys.exit("set SF_TARGET_ORG in .env or pass --target-org")
    files = bundle_files(args.bundle)
    if not files:
        sys.exit(f"no bundle {args.bundle!r} under {DEFAULT.relative_to(ROOT)}")
    values = {os.environ[k] for f in files for k in PLACEHOLDER.findall(f.read_text())
              if os.environ.get(k)}  # fmt: skip
    keep = holding(values)
    used = render(files, dict(os.environ))
    try:
        org = ("-n", args.bundle, "-o", args.target_org)
        sf("agent", "validate", "authoring-bundle", *org)
        if args.publish:
            sf("agent", "publish", "authoring-bundle", *org)
            if args.activate:
                sf("agent", "activate", *org)
    finally:
        for f in restore(used, keep):
            print(f"  placeholder restored in {f.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
