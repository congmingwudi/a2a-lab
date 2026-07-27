"""Sync `.env` with AWS Secrets Manager — the last file that broke D39.

    uv run python scripts/env_sync.py pull          # secret  -> .env
    uv run python scripts/env_sync.py push          # .env    -> secret
    uv run python scripts/env_sync.py diff          # what would change
    uv run python scripts/env_sync.py pull --print  # to stdout, write nothing

D39 says every credential in this lab is a service identity fetched with the
one human AWS login. `.env` was the exception: a plaintext file that existed on
exactly one laptop, holding every platform's keys, and whose loss would not
lose the code but would lose the ability to run or deploy it. This closes that
gap using the mechanism the lab already trusts — no new vendor, no new
credential, no second human login.

**Onboarding a teammate becomes:** clone, `aws sso login`, `env_sync.py pull`.
`.env.example` stays the checked-in contract describing what the keys mean;
this moves the VALUES, and only to someone the secret's IAM policy already
admits. That is the difference between sharing secrets and sharing a Slack
message with secrets in it.

Safety rules this follows, learned the hard way in this repo:

* **`pull` never silently overwrites.** It writes `.env` only when the file is
  absent, identical, or `--force` is given, and it always keeps a timestamped
  `.env.bak-*` first. A tool that can erase an uncommitted `.env` is a tool
  that eventually will.
* **`push` refuses to shrink the secret** unless `--force`. Pushing a
  half-populated `.env` over a complete one is the realistic accident.
* **Values are never printed.** `diff` reports key names and which side is
  ahead — enough to decide, useless to a shoulder.
* **The account is verified first**, exactly as the deploy scripts do: pushing
  the lab's secrets into a personal account is the same class of mistake as
  deploying there.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

ENV_PATH = Path(".env")
DEFAULT_SECRET = "a2alab/env/dev"


def _client():
    import boto3

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return boto3.client("secretsmanager", region_name=region)


def _guard_account() -> str:
    """Same check deploy/aws_preflight.sh makes, for the same reason."""
    import boto3

    account = boto3.client("sts").get_caller_identity()["Account"]
    expected = os.environ.get("A2ALAB_AWS_ACCOUNT_ID")
    if expected and account != expected:
        raise SystemExit(
            f"wrong AWS account — refusing.\n"
            f"    expected : {expected}  (A2ALAB_AWS_ACCOUNT_ID)\n"
            f"    session  : {account}\n"
            "  Set AWS_PROFILE to the lab's profile and run 'aws sso login'."
        )
    if not expected:
        print(f"warning: A2ALAB_AWS_ACCOUNT_ID unset — using account {account}", file=sys.stderr)
    return account


def keys_of(text: str) -> set[str]:
    """Key names only. Used for reporting, so values never reach a terminal."""
    out = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        out.add(line.split("=", 1)[0].strip())
    return out


def read_secret(name: str) -> str | None:
    client = _client()
    try:
        return client.get_secret_value(SecretId=name)["SecretString"]
    except client.exceptions.ResourceNotFoundException:
        return None


def write_secret(name: str, body: str) -> str:
    client = _client()
    try:
        client.put_secret_value(SecretId=name, SecretString=body)
        return "updated"
    except client.exceptions.ResourceNotFoundException:
        client.create_secret(
            Name=name,
            SecretString=body,
            Description="a2a-lab .env — every platform credential the lab uses (D39)",
        )
        return "created"


def cmd_pull(secret: str, args) -> int:
    body = read_secret(secret)
    if body is None:
        print(f"no secret named {secret} — run 'push' first", file=sys.stderr)
        return 2
    if args.print:
        print(body, end="")
        return 0

    if ENV_PATH.exists():
        current = ENV_PATH.read_text(encoding="utf-8")
        if current == body:
            print(f".env already matches {secret} ({len(keys_of(body))} keys)")
            return 0
        if not args.force:
            only_local = sorted(keys_of(current) - keys_of(body))
            print(
                f"refusing to overwrite .env — it differs from {secret}.\n"
                f"  local-only keys : {', '.join(only_local) or 'none'}\n"
                f"  remote-only keys: {', '.join(sorted(keys_of(body) - keys_of(current))) or 'none'}\n"
                "  Run 'diff' to inspect, then 'pull --force' (a .bak is kept) "
                "or 'push' if local is ahead.",
                file=sys.stderr,
            )
            return 1
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = ENV_PATH.with_suffix(f".bak-{stamp}")
        backup.write_text(current, encoding="utf-8")
        print(f"kept {backup}")

    ENV_PATH.write_text(body, encoding="utf-8")
    ENV_PATH.chmod(0o600)
    print(f"pulled {secret} -> .env ({len(keys_of(body))} keys)")
    return 0


def cmd_push(secret: str, args) -> int:
    if not ENV_PATH.exists():
        print("no .env to push", file=sys.stderr)
        return 2
    body = ENV_PATH.read_text(encoding="utf-8")
    remote = read_secret(secret)
    if remote == body:
        print(f"{secret} already matches .env ({len(keys_of(body))} keys)")
        return 0
    if remote is not None and not args.force:
        lost = sorted(keys_of(remote) - keys_of(body))
        if lost:
            print(
                f"refusing to push — {len(lost)} key(s) in {secret} are missing from .env:\n"
                f"  {', '.join(lost)}\n"
                "  Pull first, or 'push --force' if the removal is intended.",
                file=sys.stderr,
            )
            return 1
    action = write_secret(secret, body)
    print(f"{action} {secret} from .env ({len(keys_of(body))} keys)")
    return 0


def cmd_diff(secret: str, _args) -> int:
    remote = read_secret(secret)
    if remote is None:
        print(f"no secret named {secret} yet")
        return 0
    if not ENV_PATH.exists():
        print(f"no local .env; {secret} has {len(keys_of(remote))} keys")
        return 0
    local_text = ENV_PATH.read_text(encoding="utf-8")
    local, rem = keys_of(local_text), keys_of(remote)
    if local_text == remote:
        print(f"identical ({len(local)} keys)")
        return 0
    print(f"local {len(local)} keys · {secret} {len(rem)} keys")
    print(f"  only local : {', '.join(sorted(local - rem)) or 'none'}")
    print(f"  only remote: {', '.join(sorted(rem - local)) or 'none'}")
    shared_differ = local & rem
    print(f"  shared keys: {len(shared_differ)} (values not compared in output)")
    return 0


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=["pull", "push", "diff"])
    parser.add_argument(
        "--secret",
        default=os.environ.get("A2ALAB_ENV_SECRET", DEFAULT_SECRET),
        help="Secrets Manager secret name (A2ALAB_ENV_SECRET)",
    )
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--print", action="store_true", help="pull: write to stdout only")
    args = parser.parse_args()

    _guard_account()
    handler = {"pull": cmd_pull, "push": cmd_push, "diff": cmd_diff}[args.command]
    return handler(args.secret, args)


if __name__ == "__main__":
    raise SystemExit(main())
