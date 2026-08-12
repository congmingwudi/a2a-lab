"""Keep the demo warm — one pass, meant to be looped.

    uv run python scripts/demo_watch.py              # one warm pass, table out
    uv run python scripts/demo_watch.py --json       # machine-readable
    uv run python scripts/demo_watch.py --targets claude-agentcore,openai-agentcore

Then, inside a live Claude Code session, wrap it in a bounded watch:

    /loop 4m uv run python scripts/demo_watch.py

`/loop` re-runs this every 4 minutes *in the current session*, so it dies when
you close the terminal — which is exactly right for a pre-demo/attended watch
and exactly WRONG for standing monitoring (that is what the scheduled obs
analyst D23 and cost sentinel WS12/D44 are for: EventBridge-fired Lambdas that
run with no human present). See build-notes/claude/13-recurring-tasks.md.

What it does, and why it is a THIN wrapper: the hosted twins cold-start ~31–56s
(config/targets.yaml), which blows the Path-A action budget mid-demo. The
console already knows how to warm them — POST /api/warmup/{name} composes the
correct (delegated) ping and records the duration to warmups.jsonl for the
cross-platform cold-start comparison. This script just drives those existing
endpoints on the console's behalf: it discovers the warmable targets from
GET /api/warmup, fires each, and checks /healthz. No warm-up logic is
reimplemented here — reimplementing it would fork the very numbers the console
publishes. If a warm-up fails, the process exits non-zero, so a Stop-hook wired
to Slack (build-notes/claude/05) turns each loop pass into a walk-away signal.

Auth: when the console has A2ALAB_TOKEN set, /api/warmup is gated (it is not on
the middleware's exempt list, unlike /healthz). This script sends that token as
X-Lab-Token — the service credential TokenAuthMiddleware documents. It loads
.env, so a value there is picked up automatically; export A2ALAB_TOKEN to
override. Unset = local dev with auth off, and no header is sent.

Read-only against the lab except for the warm-up pings themselves, which are
the point.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

# Read .env like the other scripts (run_console.sh loads it for the console; a
# bare `uv run python scripts/...` does not), so A2ALAB_TOKEN and any
# A2ALAB_CONSOLE_URL are picked up without the caller exporting them. Optional:
# if python-dotenv isn't present, the process env still wins.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# localhost:8200 is scripts/run_console.sh's default port; not an environment
# identifier, so a default is allowed here (CLAUDE.md's no-fallback rule is
# about account/project ids). Point at the hosted console with A2ALAB_CONSOLE_URL.
CONSOLE_URL = os.environ.get("A2ALAB_CONSOLE_URL", "http://localhost:8200").rstrip("/")
WARMUP_TIMEOUT_S = 120  # a cold start can be ~56s; leave headroom over that

# When the console has A2ALAB_TOKEN set, auth is on and /api/warmup is gated
# (it is NOT on the middleware's exempt list, unlike /healthz). The shared token
# is exactly the "service credential" TokenAuthMiddleware documents, sent as
# X-Lab-Token — the right credential for a headless driver like this. Unset =
# local dev with auth off, and the header is simply omitted.
LAB_TOKEN = os.environ.get("A2ALAB_TOKEN")


def _auth_headers() -> dict[str, str]:
    return {"x-lab-token": LAB_TOKEN} if LAB_TOKEN else {}


def _get(path: str) -> dict:
    req = urllib.request.Request(f"{CONSOLE_URL}{path}", headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def _post(path: str, timeout: int) -> dict:
    req = urllib.request.Request(f"{CONSOLE_URL}{path}", method="POST", headers=_auth_headers())
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _healthz() -> tuple[bool, str]:
    try:
        body = _get("/healthz")
        ok = body.get("status") == "healthy"
        return ok, body.get("status", "?")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _warmable_targets() -> list[str]:
    """Ask the console which targets are flagged warmup:true — one source of
    truth (config/targets.yaml, read by the console), never a copy here."""
    data = _get("/api/warmup")
    return [t["name"] for t in data.get("targets", [])]


def watch(targets: list[str]) -> dict:
    health_ok, health_note = _healthz()
    results = []
    for name in targets:
        t0 = time.monotonic()
        try:
            rec = _post(f"/api/warmup/{name}", timeout=WARMUP_TIMEOUT_S)
            results.append(
                {
                    "target": name,
                    "ok": bool(rec.get("ok")),
                    "duration_ms": rec.get("duration_ms"),
                    "note": (rec.get("note") or "")[:120],
                }
            )
        except urllib.error.HTTPError as exc:
            # 409 = a warm-up for this target is already in flight (a previous
            # loop pass has not finished). Not a failure — report and move on.
            already = exc.code == 409
            results.append(
                {
                    "target": name,
                    "ok": already,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "note": ("already warming" if already else f"HTTP {exc.code}"),
                }
            )
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            results.append(
                {
                    "target": name,
                    "ok": False,
                    "duration_ms": int((time.monotonic() - t0) * 1000),
                    "note": f"{type(exc).__name__}: {exc}",
                }
            )
    return {
        "console": CONSOLE_URL,
        "healthz": {"ok": health_ok, "note": health_note},
        "targets": results,
        "all_ok": health_ok and all(r["ok"] for r in results),
    }


def _print_table(report: dict) -> None:
    h = report["healthz"]
    print(f"console  {report['console']}")
    print(f"healthz  {'ok' if h['ok'] else 'DOWN'}  ({h['note']})")
    print(f"{'target':28} {'ok':4} {'ms':>7}  note")
    print("-" * 72)
    for r in report["targets"]:
        ms = r["duration_ms"] if r["duration_ms"] is not None else "-"
        print(f"{r['target']:28} {('ok' if r['ok'] else 'FAIL'):4} {ms:>7}  {r['note']}")
    print("-" * 72)
    print("ALL OK" if report["all_ok"] else "DEGRADED — see FAIL rows above")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="One warm pass over the demo's hosted twins.")
    ap.add_argument(
        "--targets",
        help="comma-separated target names; default = every warmup:true target",
    )
    ap.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = ap.parse_args(argv)

    try:
        targets = (
            [t.strip() for t in args.targets.split(",") if t.strip()]
            if args.targets
            else _warmable_targets()
        )
    except urllib.error.HTTPError as exc:
        # Reached the console but was rejected. 401 is the common one: the
        # console has A2ALAB_TOKEN set (auth on) and this process sent no /
        # the wrong token. Say so — "cannot reach" would be a lie.
        if exc.code == 401:
            hint = (
                "console rejected the token (401). Set A2ALAB_TOKEN in the "
                "environment to the same value the console uses — it is the "
                "service credential, sent as X-Lab-Token."
            )
        else:
            hint = f"console returned HTTP {exc.code} for /api/warmup"
        print(json.dumps({"error": hint}) if args.json else hint, file=sys.stderr)
        return 2
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        # Can't even reach the console — that IS the finding. Non-zero exit so
        # a looping session / Stop-hook treats it as a failure.
        msg = {"error": f"cannot reach console at {CONSOLE_URL}: {exc}"}
        print(json.dumps(msg) if args.json else msg["error"], file=sys.stderr)
        return 2

    report = watch(targets)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        _print_table(report)
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
