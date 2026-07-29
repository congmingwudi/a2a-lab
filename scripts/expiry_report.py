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
    """The operator's own SSO session — NO LONGER REPORTED (WS13).

    Kept because it is still the thing that turns every other check in this
    script into "error" when it lapses, and a future caller may want to say so
    at the top of a run. It is deliberately out of `collect()`.

    Why it left the report: since the lab went fully hosted, this session is a
    **deploy-time** credential on one laptop. It expires daily, it is renewed by
    typing `aws sso login`, and nothing in the running stack depends on it —
    the hosted seams authenticate as their own service identities out of
    Secrets Manager (D39). Listing it beside credentials whose expiry would take
    the LAB down made a personal login look like production risk, and put a
    permanent amber row in a panel whose job is to show what actually needs
    rotating.
    """
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
    """CloudWatch metrics API key — the WS9 telemetry path's only credential.

    boto3 rather than the `aws` CLI (WS14): the CLI reads whatever session the
    machine happens to hold, which is exactly the laptop dependency this
    workstream removes. boto3 resolves the ambient chain instead — the operator's
    SSO session locally, the task or Lambda role when hosted — so one code path
    serves both.
    """
    if not user:
        return []
    try:
        import boto3

        iam = boto3.client("iam", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        creds = iam.list_service_specific_credentials(UserName=user).get(
            "ServiceSpecificCredentials", []
        )
    except Exception as exc:  # noqa: BLE001 - unreachable provider is a finding
        return [
            _entry(
                f"IAM service credential ({user})",
                "api-key",
                None,
                "measured",
                error=f"could not query IAM ({type(exc).__name__})",
            )
        ]
    out = []
    for cred in creds:
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


def _azure_via_cli(client_id: str) -> list[dict] | None:
    """The old `az ad app credential list` path, kept as a LOCAL fallback.

    Returns None when the CLI is unavailable or unauthenticated — which is
    always the case in a container, and is how the hosted report knows to
    surface the Graph permission it actually needs (WS14).
    """
    raw = _run(["az", "ad", "app", "credential", "list", "--id", client_id, "-o", "json"])
    if raw is None:
        return None
    try:
        creds = json.loads(raw)
    except ValueError:
        return None
    return [
        _entry(
            "Entra app secret",
            "secret",
            _parse(c.get("endDateTime")),
            "measured",
            "rotate in Entra -> App registrations -> Certificates & secrets",
        )
        for c in creds
    ] or None


def azure_app_secret(client_id: str | None) -> list[dict]:
    """The Entra app registration's client secret — the Foundry caller's only
    credential, and the one with a real expiry rather than a 15-year one.

    Microsoft Graph over a client-credentials token rather than the `az` CLI
    (WS14), so this runs hosted as well as locally. It needs the
    **Application.Read.All** Graph APPLICATION permission, admin-consented on
    the lab's app registration — reading an application object is a directory
    read, and a service principal has none by default. Without it Graph answers
    403 Authorization_RequestDenied, and this reports that rather than pretending
    the secret is fine.
    """
    if not client_id:
        return []
    tenant = os.environ.get("AZURE_TENANT_ID")
    secret = os.environ.get("AZURE_CLIENT_SECRET")
    if not (tenant and secret):
        return [
            _entry(
                "Entra app secret", "secret", None, "measured",
                error="AZURE_TENANT_ID / AZURE_CLIENT_SECRET not set",
            )
        ]
    import json as _json
    import urllib.error
    import urllib.parse
    import urllib.request

    try:
        body = urllib.parse.urlencode({
            "client_id": client_id, "client_secret": secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }).encode()
        token = _json.load(urllib.request.urlopen(urllib.request.Request(
            f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token", data=body
        ), timeout=25))["access_token"]

        url = "https://graph.microsoft.com/v1.0/applications?" + urllib.parse.urlencode({
            "$filter": f"appId eq '{client_id}'",
            "$select": "displayName,passwordCredentials",
        })
        apps = _json.load(urllib.request.urlopen(urllib.request.Request(
            url, headers={"Authorization": "Bearer " + token}
        ), timeout=25)).get("value") or []
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = (_json.loads(exc.read()).get("error") or {}).get("code", "")
        except Exception:  # noqa: BLE001
            pass
        # Graph refused. On a laptop the operator's own `az` login can still
        # answer, so fall back rather than losing a row that used to work —
        # the CLI is absent in a container, where the honest error is the point.
        fallback = _azure_via_cli(client_id)
        if fallback is not None:
            return fallback
        hint = (
            " — grant Application.Read.All (Graph application permission) with admin "
            "consent on this app registration, and it works hosted too"
            if exc.code == 403 or detail == "Authorization_RequestDenied"
            else ""
        )
        return [
            _entry("Entra app secret", "secret", None, "measured",
                   error=f"Graph {exc.code} {detail}{hint}")
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            _entry("Entra app secret", "secret", None, "measured",
                   error=f"could not query Graph ({type(exc).__name__})")
        ]

    out = []
    for app in apps:
        for cred in app.get("passwordCredentials") or []:
            label = cred.get("displayName") or (cred.get("keyId") or "")[:8]
            out.append(
                _entry(
                    f"Entra app secret{' — ' + label if label else ''}",
                    "secret",
                    _parse(cred.get("endDateTime")),
                    "measured",
                    "rotate in Entra -> App registrations -> Certificates & secrets",
                )
            )
    return out or [
        _entry("Entra app secret", "secret", None, "measured",
               error="no password credentials on this app registration")
    ]


def gcp_service_account_keys(project: str | None) -> list[dict]:
    """User-managed SA keys. Google-managed keys rotate themselves and are
    excluded — listing them as 'expiring' would be noise, not signal.

    The IAM REST API over google-auth rather than the `gcloud` CLI (WS14). ADC
    resolves the operator's login locally and the service-account key that
    `observability.credentials.prepare()` materialises when hosted, so the same
    code runs in both places.
    """
    if not project:
        return []
    try:
        import google.auth
        from google.auth.transport.requests import AuthorizedSession

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        session = AuthorizedSession(creds)
        listing = session.get(
            f"https://iam.googleapis.com/v1/projects/{project}/serviceAccounts", timeout=25
        )
        listing.raise_for_status()
        emails = [
            a.get("email", "")
            for a in (listing.json().get("accounts") or [])
            if a.get("email", "").startswith("a2alab")
        ]
    except Exception as exc:  # noqa: BLE001
        return [
            _entry(
                "GCP service-account keys",
                "key",
                None,
                "measured",
                error=f"could not list service accounts ({type(exc).__name__})",
            )
        ]

    out = []
    for email in emails:
        try:
            r = session.get(
                f"https://iam.googleapis.com/v1/projects/{project}/serviceAccounts/{email}/keys",
                params={"keyTypes": "USER_MANAGED"},
                timeout=25,
            )
            r.raise_for_status()
            keys = r.json().get("keys") or []
        except Exception:  # noqa: BLE001 - one account failing is not all of them
            continue
        for key in keys:
            key_id = (key.get("name") or "").rsplit("/", 1)[-1][:8]
            out.append(
                _entry(
                    f"GCP key {key_id} — {email.split('@')[0]}",
                    "key",
                    _parse(key.get("validBeforeTime")),
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
    try:
        import boto3

        acm = boto3.client("acm", region_name=os.environ.get("AWS_REGION", "us-east-1"))
        summaries = acm.list_certificates().get("CertificateSummaryList", [])
    except Exception as exc:  # noqa: BLE001
        return [
            _entry(
                "ACM certificates",
                "certificate",
                None,
                "measured",
                error=f"could not list ACM certificates ({type(exc).__name__})",
            )
        ]
    out = []
    for cert in summaries:
        # NotAfter is on the summary for imported certs; fall back to a describe
        # for any that omit it rather than reporting a blank expiry.
        not_after = cert.get("NotAfter")
        if not_after is None and cert.get("CertificateArn"):
            try:
                not_after = (
                    acm.describe_certificate(CertificateArn=cert["CertificateArn"])
                    .get("Certificate", {})
                    .get("NotAfter")
                )
            except Exception:  # noqa: BLE001
                not_after = None
        out.append(
            _entry(
                f"TLS cert — {cert.get('DomainName', '?')}",
                "certificate",
                _parse(not_after),
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
    # aws_sso_session() is deliberately NOT collected — see its docstring. It is
    # a deploy-time credential on the operator's machine, not something the
    # hosted lab depends on.
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


def _publish(report: dict) -> None:
    """Also push the snapshot to the hosted store (WS13).

    The console renders this report but cannot produce it — collecting expiry
    dates needs the operator's own AWS/az/gcloud sessions, which a container
    does not have. Writing it to `.a2alab/expiry.json` alone therefore makes the
    console unhostable: the file is a laptop dependency wearing a cache costume.

    Best-effort on purpose. This script's job is the report; a store that is
    unreachable must not turn a working local run into a failure, and the file
    write above has already happened."""
    try:
        from observability.pg import STATE_EXPIRY, PgClient, PgObsStore

        if not PgClient.configured():
            return
        # The writer secret, not from_env(): the standard pair is `lab_reader`
        # and an INSERT there fails with `cannot execute INSERT in a read-only
        # transaction` (D46 — the same trap pg_backfill.py sat in for days).
        cluster = os.environ.get("A2ALAB_PG_CLUSTER_ARN")
        writer_secret = os.environ.get("A2ALAB_PG_WRITER_SECRET_ARN")
        client = (
            PgClient(cluster_arn=cluster, secret_arn=writer_secret)
            if cluster and writer_secret
            else PgClient.from_env()
        )
        store = PgObsStore(client)
        try:
            store.put_state(STATE_EXPIRY, report)
            print(f"published to the hosted store (lab_state/{STATE_EXPIRY})")
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - reporting is the job, publishing is a bonus
        print(f"hosted publish skipped ({type(exc).__name__}: {str(exc)[:120]})")


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
        _publish(report)
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
