"""When does each credential in this lab stop working?

    uv run python scripts/expiry_report.py            # table
    uv run python scripts/expiry_report.py --json     # for the console
    uv run python scripts/expiry_report.py --write    # -> .a2alab/expiry.json

`identity_preflight.py` answers *can this identity do its job right now*. This
answers the question that arrives later and hurts more: **when does it stop?**
A credential that works today and expires in nine days is indistinguishable
from a healthy one until the morning it isn't — and every failure it causes
looks like something else. The CloudWatch metrics key expiring, for instance,
would present as "the telemetry dashboard went empty", which this lab has
already spent three debugging sessions chasing for unrelated reasons.

Everything here is **read-only and deterministic**: query each provider for the
expiry it already knows, report days remaining. No rotation, no writes. The
interesting judgment — what to rotate first given a demo on a date — is a
separate layer, deliberately (D22's rule: deterministic collection below, agent
interpretation above).

Anything the providers cannot tell us is declared in `config/credentials.yaml`
with `source: declared`, so the report distinguishes *measured* from *asserted*
instead of quietly presenting one as the other.
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STATE = REPO / ".a2alab" / "expiry.json"

# Thresholds. `warn` is deliberately generous for credentials whose rotation
# needs a person and a console session rather than a script.
CRITICAL_DAYS = 7
WARN_DAYS = 30


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse(ts: str | dt.date | dt.datetime | None) -> dt.datetime | None:
    if not ts:
        return None
    # YAML parses an unquoted `2027-01-24` into a date object, not a string —
    # so the declared entries arrive in a different type than the API ones.
    if isinstance(ts, dt.datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
    if isinstance(ts, dt.date):
        return dt.datetime(ts.year, ts.month, ts.day, tzinfo=dt.timezone.utc)
    ts = ts.strip().replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=dt.timezone.utc)


def _run(cmd: list[str], timeout: int = 25) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None
    return out.stdout if out.returncode == 0 else None


# Google writes 9999-12-31 for a key that never expires. Reporting that as
# "2,912,235 days left" is technically true and useless; it also buries the
# real finding, which is that the key has NO expiry and rotation is on you.
NEVER_AFTER = dt.datetime(9000, 1, 1, tzinfo=dt.timezone.utc)


def _entry(name, kind, expires, source, detail="", error="") -> dict:
    days = None
    if expires and expires >= NEVER_AFTER:
        return {
            "name": name,
            "kind": kind,
            "expires_at": None,
            "days_left": None,
            "status": "no-expiry",
            "source": source,
            "detail": (detail + " — no expiry set; rotation is manual").strip(" —"),
            "error": error,
        }
    if expires:
        days = round((expires - _utcnow()).total_seconds() / 86400, 1)
    status = "unknown"
    if error:
        status = "error"
    elif days is not None:
        status = (
            "expired"
            if days < 0
            else ("critical" if days < CRITICAL_DAYS else ("warn" if days < WARN_DAYS else "ok"))
        )
    return {
        "name": name,
        "kind": kind,
        "expires_at": expires.isoformat() if expires else None,
        "days_left": days,
        "status": status,
        "source": source,  # measured | declared
        "detail": detail,
        "error": error,
    }


# ---- collectors ------------------------------------------------------------
# Each returns a list of entries and never raises: a provider being unreachable
# is a finding ("cannot tell you"), not a crash that hides the other five.


def aws_sso_session() -> list[dict]:
    """The operator's own SSO session. Expires daily, and its expiry is the one
    that silently turns every other check into 'error'."""
    latest = None
    for path in glob.glob(os.path.expanduser("~/.aws/sso/cache/*.json")):
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, ValueError):
            continue
        when = _parse(data.get("expiresAt"))
        # Several tokens are cached; the session is the soonest to lapse that
        # has not already lapsed.
        if when and when > _utcnow() and (latest is None or when < latest):
            latest = when
    if latest is None:
        return [
            _entry(
                "AWS SSO session",
                "session",
                None,
                "measured",
                error="no unexpired token cached — run 'aws sso login'",
            )
        ]
    return [
        _entry(
            "AWS SSO session",
            "session",
            latest,
            "measured",
            "refresh with 'aws sso login' (Zscaler ON)",
        )
    ]


def aws_service_credentials(user: str | None) -> list[dict]:
    """CloudWatch metrics API key — the WS9 telemetry path's only credential."""
    if not user:
        return []
    raw = _run(
        [
            "aws",
            "iam",
            "list-service-specific-credentials",
            "--user-name",
            user,
            "--region",
            os.environ.get("AWS_REGION", "us-east-1"),
            "--output",
            "json",
        ]
    )
    if raw is None:
        return [
            _entry(
                f"IAM service credential ({user})",
                "api-key",
                None,
                "measured",
                error="could not query IAM (session expired, or no permission)",
            )
        ]
    out = []
    for cred in json.loads(raw).get("ServiceSpecificCredentials", []):
        out.append(
            _entry(
                f"{cred.get('ServiceName', 'service')} key — {user}",
                "api-key",
                _parse(cred.get("ExpirationDate")),
                "measured",
                f"status {cred.get('Status')}; rotate with "
                "'aws iam create-service-specific-credential'",
            )
        )
    return out or [
        _entry(
            f"IAM service credential ({user})",
            "api-key",
            None,
            "measured",
            error="no service-specific credentials on this user",
        )
    ]


def azure_app_secret(client_id: str | None) -> list[dict]:
    """Entra service principal secret — the Foundry path's credential."""
    if not client_id:
        return []
    raw = _run(["az", "ad", "app", "credential", "list", "--id", client_id, "-o", "json"])
    if raw is None:
        return [
            _entry(
                "Entra app secret",
                "client-secret",
                None,
                "measured",
                error="could not query Entra (az not signed in, or no directory read)",
            )
        ]
    out = []
    for cred in json.loads(raw):
        out.append(
            _entry(
                f"Entra app secret{' — ' + cred['displayName'] if cred.get('displayName') else ''}",
                "client-secret",
                _parse(cred.get("endDateTime")),
                "measured",
                "rotate in Entra → App registrations → Certificates & secrets",
            )
        )
    return out


def gcp_service_account_keys(project: str | None) -> list[dict]:
    """User-managed SA keys. Google-managed keys rotate themselves and are
    excluded — listing them as 'expiring' would be noise, not signal."""
    if not project:
        return []
    accounts = _run(
        [
            "gcloud",
            "iam",
            "service-accounts",
            "list",
            "--project",
            project,
            "--format",
            "value(email)",
        ]
    )
    if accounts is None:
        return [
            _entry(
                "GCP service-account keys",
                "key",
                None,
                "measured",
                error="could not list service accounts (gcloud not authenticated)",
            )
        ]
    out = []
    for email in [a for a in accounts.split() if a.startswith("a2alab")]:
        raw = _run(
            [
                "gcloud",
                "iam",
                "service-accounts",
                "keys",
                "list",
                "--iam-account",
                email,
                "--managed-by",
                "user",
                "--format",
                "value(name,validBeforeTime)",
            ]
        )
        if not raw:
            continue
        for line in raw.strip().splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            key_id = parts[0].rsplit("/", 1)[-1][:8]
            out.append(
                _entry(
                    f"GCP key {key_id} — {email.split('@')[0]}",
                    "key",
                    _parse(parts[1]),
                    "measured",
                    "rotate with 'gcloud iam service-accounts keys create'",
                )
            )
    return out


def acm_certificates() -> list[dict]:
    """TLS certificates on the ALB — including the Cloudflare origin cert
    imported for Path A's Full (strict) hop.

    Long-dated (15 years) and therefore easy to forget entirely, which is the
    argument for listing it rather than the argument against. A certificate
    nobody is tracking is one whose expiry will be discovered by Salesforce
    failing a callout.
    """
    raw = _run(
        [
            "aws",
            "acm",
            "list-certificates",
            "--region",
            os.environ.get("AWS_REGION", "us-east-1"),
            "--output",
            "json",
        ]
    )
    if raw is None:
        return [
            _entry(
                "ACM certificates",
                "certificate",
                None,
                "measured",
                error="could not list ACM certificates (session expired, or no permission)",
            )
        ]
    out = []
    for cert in json.loads(raw).get("CertificateSummaryList", []):
        out.append(
            _entry(
                f"TLS cert — {cert.get('DomainName', '?')}",
                "certificate",
                _parse(cert.get("NotAfter")),
                "measured",
                "imported into ACM for the ALB; re-issue in Cloudflare "
                "(SSL/TLS -> Origin Server) and re-import",
            )
        )
    return out


def declared(path: Path) -> list[dict]:
    """Credentials no API will tell us about, asserted in config with a date.

    Marked `source: declared` on purpose — an asserted expiry is a note someone
    wrote, and the report should never let it pass for a measurement.
    """
    if not path.exists():
        return []
    import yaml

    doc = yaml.safe_load(path.read_text()) or {}
    return [
        _entry(
            item["name"],
            item.get("kind", "credential"),
            _parse(item.get("expires")),
            "declared",
            item.get("detail", ""),
        )
        for item in doc.get("credentials", [])
        if item.get("expires")
    ]


def collect() -> dict:
    entries: list[dict] = []
    entries += aws_sso_session()
    entries += aws_service_credentials(os.environ.get("A2ALAB_CW_METRICS_USER"))
    entries += azure_app_secret(os.environ.get("AZURE_CLIENT_ID"))
    entries += gcp_service_account_keys(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    entries += acm_certificates()
    entries += declared(REPO / "config" / "credentials.yaml")

    rank = {
        "expired": 0,
        "critical": 1,
        "warn": 2,
        "error": 3,
        "ok": 4,
        "no-expiry": 5,
        "unknown": 6,
    }
    entries.sort(
        key=lambda e: (
            rank.get(e["status"], 9),
            e["days_left"] if e["days_left"] is not None else 1e9,
        )
    )
    worst = entries[0]["status"] if entries else "unknown"
    return {
        "generated_at": _utcnow().isoformat(),
        "worst_status": worst,
        "counts": {s: sum(1 for e in entries if e["status"] == s) for s in rank},
        "thresholds": {"critical_days": CRITICAL_DAYS, "warn_days": WARN_DAYS},
        "credentials": entries,
    }


ICON = {
    "expired": "✗",
    "critical": "!",
    "warn": "~",
    "ok": "✓",
    "error": "?",
    "no-expiry": "∞",
    "unknown": "?",
}


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true", help="also write .a2alab/expiry.json")
    args = ap.parse_args()

    report = collect()
    if args.write:
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(report, indent=2))
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    print(f"credential expiry — {report['generated_at'][:19]}Z\n")
    for e in report["credentials"]:
        d = e["days_left"]
        # Under a day, "0.0d" reads as expired. Hours are the useful unit
        # for the one credential that lapses during a working session.
        left = "—" if d is None else (f"{d * 24:>6.1f}h" if abs(d) < 1 else f"{d:>6.0f}d")
        src = "" if e["source"] == "measured" else "  (declared)"
        print(f"  {ICON.get(e['status'], '?')} {left}  {e['name']}{src}")
        if e["error"]:
            print(f"          {e['error']}")
    counts = {k: v for k, v in report["counts"].items() if v}
    print("\n" + ", ".join(f"{v} {k}" for k, v in counts.items()))
    # Non-zero when something needs attention, so it works as a pre-demo gate.
    return 1 if report["worst_status"] in {"expired", "critical"} else 0


if __name__ == "__main__":
    sys.exit(main())
