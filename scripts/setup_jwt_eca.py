"""Create (or update) a JWT-bearer External Client App in the lab org, headlessly.

The recipe is the F6 ECA runbook (plan/04-runbooks.md §"To add another") plus
the one thing the tab-embed app proved (plan/13 §5): the signing certificate
rides the `certificate` field of ExtlClntAppGlobalOauthSettings, so a
JWT-bearer app needs no Setup upload and no consumer secret at all.

Why a script and not four checked-in files: the four files carry the run-as
username, a contact email, the consumer key and the org-scoped oauthLink — all
environment identifiers that CLAUDE.md keeps out of the repo. So the files are
RENDERED from .env into a scratch SFDX project at deploy time and never land in
`salesforce/`. What is checked in is the recipe (PRESETS below).

    uv run python scripts/setup_jwt_eca.py show memory        # what would deploy
    uv run python scripts/setup_jwt_eca.py create memory --validate   # check-only
    uv run python scripts/setup_jwt_eca.py create memory      # deploy + write .env
    uv run python scripts/setup_jwt_eca.py verify memory      # do its job, or fail

`create` is idempotent: if the app already exists its consumer key is retrieved
first and re-sent, so a re-run updates scopes/cert without rotating the key.
`verify` is the D37/F6 rule — an identity is verified by doing its job, not by
authenticating: for `memory` that is the JWT-bearer grant AND the Data 360
`/services/a360/token` exchange the Agentic Memory runtime APIs need (plan/18).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
API_VERSION = "64.0"


@dataclass(frozen=True)
class Preset:
    name: str  # ECA developer name; the three child files derive from it
    description: str
    scopes: str  # Metadata API labels, comma-separated (not the cdp_* wire names)
    client_id_env: str  # .env key the consumer key is written to
    run_as_env: str  # .env key for the JWT `sub` / pre-authorized user
    key_file: str  # private key under A2ALAB_JWT_DIR (.a2alab by default)
    cdp_exchange: bool = False  # verify also runs the a360 CDP token exchange
    # Pre-authorize the app for THIS permission set instead of the System
    # Administrator profile (D86). Set, it also means the run-as user must be a
    # dedicated integration user: `create` refuses an admin `sub`.
    preauth_permission_set: str | None = None


PRESETS = {
    "memory": Preset(
        name="a2a_lab_memory",
        description=(
            "A2A lab: Data 360 Agentic Memory and Context (plan/18). JWT-bearer as the "
            "run-as user -> core token -> /services/a360/token CDP exchange -> the "
            "tenant's /api/v1/agentic/* conversation, memory and context APIs."
        ),
        # api + cdp_ingest_api + cdp_profile_api + cdp_query_api, per the
        # Agent Memory setup guide, PLUS RefreshToken: JWT bearer never uses a
        # refresh token, but without the scope the grant fails 400 "refresh_token
        # scope is required and the connected app should be installed and
        # preauthorized" (measured 2026-10-12; the guide omits it).
        # Adding more scopes cannot widen tenant access: the a360 exchange
        # filters the tenant token to exactly these three cdp_* scopes (tried
        # every other Data 360 scope + sfap_api + einstein_gpt_api, 2026-10-13).
        scopes="Api, RefreshToken, CDPIngest, CDPProfile, CDPQuery",
        client_id_env="SF_CLIENT_ID_MEMORY",
        run_as_env="SF_MEMORY_RUNAS_USER",
        key_file="memory_jwt_private.pem",
        cdp_exchange=True,
        preauth_permission_set="A2ALab_Memory_Integration",
    ),
}


# ---------------------------------------------------------------- environment


def load_env() -> None:
    """Fill os.environ from .env without overriding what the shell already set."""
    if not ENV_FILE.exists():
        return
    for line in ENV_FILE.read_text().splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if m and m.group(1) not in os.environ:
            os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def set_env_value(key: str, value: str) -> None:
    """Upsert KEY=value in .env, keeping every other line as-is."""
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    pat = re.compile(rf"^\s*#?\s*{re.escape(key)}=")
    for i, line in enumerate(lines):
        if pat.match(line):
            lines[i] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    os.environ[key] = value


def target_org() -> str:
    org = os.environ.get("SF_TARGET_ORG")
    if not org:
        sys.exit("set SF_TARGET_ORG in .env (the sf CLI alias/username of the lab org)")
    return org


def sf(*args: str, cwd: Path | None = None) -> dict:
    """Run an sf CLI command with --json and return `result`, exiting on failure."""
    proc = subprocess.run(
        ["sf", *args, "--json"], cwd=cwd, capture_output=True, text=True, check=False
    )
    try:
        out = json.loads(proc.stdout)
    except json.JSONDecodeError:
        sys.exit(f"sf {' '.join(args[:3])} returned non-JSON:\n{proc.stdout}\n{proc.stderr}")
    if out.get("status", 1) != 0:
        sys.exit(f"sf {' '.join(args[:3])} failed:\n{json.dumps(out, indent=2)[:4000]}")
    return out["result"]


def org_username(org: str) -> str:
    """The sf CLI's authenticated user for the lab org (the operator admin)."""
    return sf("org", "display", "-o", org)["username"]


def run_as_user(org: str, username: str) -> dict:
    r = sf("data", "query", "-o", org, "-q",
           f"SELECT Email, Profile.Name FROM User WHERE Username = '{username}'")  # fmt: skip
    recs = r.get("records") or []
    if not recs:
        sys.exit(f"run-as user {username} not found in the org")
    return recs[0]


def user_email(org: str, username: str) -> str:
    return run_as_user(org, username)["Email"]


ADMIN_PROFILE = "System Administrator"


def require_integration_user(p: Preset, org: str, run_as: str) -> dict:
    """D86: a preset with its own gate permission set runs as a dedicated
    integration user, never an admin. Without this, a stale .env (or the
    fallback to the CLI's own user) quietly puts the org admin back as `sub`."""
    user = run_as_user(org, run_as)
    profile = (user.get("Profile") or {}).get("Name")
    if p.preauth_permission_set and profile == ADMIN_PROFILE:
        sys.exit(f"{p.name}: run-as {run_as} has the {ADMIN_PROFILE} profile. This app runs "
                 f"as a dedicated integration user (D86): create it with "
                 f"`setup_agentic_memory.py user --apply`, which sets {p.run_as_env}.")  # fmt: skip
    return user


def resolve_run_as(p: Preset, org: str) -> str:
    """The JWT `sub`: .env wins; else the CLI's own user (the operator admin)."""
    return os.environ.get(p.run_as_env) or org_username(org)


def key_dir() -> Path:
    return ROOT / os.environ.get("A2ALAB_JWT_DIR", ".a2alab")


# ---------------------------------------------------------------- keypair


def ensure_keypair(p: Preset) -> tuple[Path, str]:
    """Return (private key path, certificate PEM); generate either if missing.

    The cert is self-signed (CN = app name, 10 years) — Salesforce only uses it
    to verify the RS256 assertion signature, so no CA is involved. It is derived
    from the key, so losing the .crt is harmless; losing the key means a new
    keypair and a redeploy of the cert.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    kpath = key_dir() / p.key_file
    cpath = kpath.with_name(kpath.name.replace("_private.pem", "_cert.pem"))
    if kpath.exists():
        key = serialization.load_pem_private_key(kpath.read_bytes(), password=None)
    else:
        kpath.parent.mkdir(parents=True, exist_ok=True)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        kpath.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        kpath.chmod(0o600)
        print(f"generated {kpath.relative_to(ROOT)} — back it up: chezmoi add --encrypt (D45)")
    if cpath.exists():
        return kpath, cpath.read_text()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, p.name)])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    pem = cert.public_bytes(serialization.Encoding.PEM).decode()
    cpath.write_text(pem)
    return kpath, pem


# ---------------------------------------------------------------- metadata


def render(p: Preset, run_as: str, email: str, cert_pem: str, consumer_key: str | None) -> dict:
    """The four F6 files, keyed by path relative to force-app/main/default."""
    n = p.name
    ck = f"    <consumerKey>{consumer_key}</consumerKey>\n" if consumer_key else ""
    preauth = (
        f"    <commaSeparatedPermissionSet>{p.preauth_permission_set}</commaSeparatedPermissionSet>"
        if p.preauth_permission_set
        else "    <commaSeparatedProfile>System Administrator</commaSeparatedProfile>"
    )
    head = '<?xml version="1.0" encoding="UTF-8"?>\n'
    ns = 'xmlns="http://soap.sforce.com/2006/04/metadata"'
    return {
        f"externalClientApps/{n}.eca-meta.xml": f"""{head}<ExternalClientApplication {ns}>
    <contactEmail>{escape(email)}</contactEmail>
    <description>{escape(p.description)}</description>
    <distributionState>Local</distributionState>
    <isProtected>false</isProtected>
    <label>{n}</label>
</ExternalClientApplication>
""",
        # JWT bearer only: every other grant off. isConsumerSecretOptional because
        # the assertion IS the client authentication — no secret ever exists.
        f"extlClntAppGlobalOauthSets/{n}_glbloauth.ecaGlblOauth-meta.xml": f"""{head}<ExtlClntAppGlobalOauthSettings {ns}>
    <callbackUrl>https://login.salesforce.com/services/oauth2/callback</callbackUrl>
{ck}    <certificate>{cert_pem.strip()}</certificate>
    <externalClientApplication>{n}</externalClientApplication>
    <isClientCredentialsFlowEnabled>false</isClientCredentialsFlowEnabled>
    <isCodeCredFlowEnabled>false</isCodeCredFlowEnabled>
    <isCodeCredPostOnly>false</isCodeCredPostOnly>
    <isConsumerSecretOptional>true</isConsumerSecretOptional>
    <isDeviceFlowEnabled>false</isDeviceFlowEnabled>
    <isIntrospectAllTokens>false</isIntrospectAllTokens>
    <isNamedUserJwtEnabled>false</isNamedUserJwtEnabled>
    <isPkceRequired>true</isPkceRequired>
    <isSecretRequiredForRefreshToken>false</isSecretRequiredForRefreshToken>
    <isSecretRequiredForTokenExchange>false</isSecretRequiredForTokenExchange>
    <isTokenExchangeEnabled>false</isTokenExchangeEnabled>
    <label>{n}_glbloauth</label>
    <shouldRotateConsumerKey>false</shouldRotateConsumerKey>
    <shouldRotateConsumerSecret>false</shouldRotateConsumerSecret>
</ExtlClntAppGlobalOauthSettings>
""",
        f"extlClntAppOauthSettings/{n}_oauth.ecaOauth-meta.xml": f"""{head}<ExtlClntAppOauthSettings {ns}>
    <commaSeparatedOauthScopes>{p.scopes}</commaSeparatedOauthScopes>
    <externalClientApplication>{n}</externalClientApplication>
    <isFirstPartyAppEnabled>false</isFirstPartyAppEnabled>
    <label>{n}_oauth</label>
</ExtlClntAppOauthSettings>
""",
        # AdminApprovedPreAuthorized + a pre-authorized profile or permission set
        # is what lets the JWT grant succeed for `sub` with no interactive
        # consent. A preset with its own gate permission set (D86) admits only
        # the users assigned it; the profile form admits every admin.
        f"extlClntAppOauthPolicies/{n}_oauthPlcy.ecaOauthPlcy-meta.xml": f"""{head}<ExtlClntAppOauthConfigurablePolicies {ns}>
{preauth}
    <externalClientApplication>{n}</externalClientApplication>
    <ipRelaxationPolicyType>Enforce</ipRelaxationPolicyType>
    <isClientCredentialsFlowEnabled>false</isClientCredentialsFlowEnabled>
    <isGuestCodeCredFlowEnabled>false</isGuestCodeCredFlowEnabled>
    <isTokenExchangeFlowEnabled>false</isTokenExchangeFlowEnabled>
    <label>{n}_oauthPlcy</label>
    <permittedUsersPolicyType>AdminApprovedPreAuthorized</permittedUsersPolicyType>
    <requiredSessionLevel>STANDARD</requiredSessionLevel>
</ExtlClntAppOauthConfigurablePolicies>
""",
    }


def scratch_project(files: dict) -> Path:
    d = Path(tempfile.mkdtemp(prefix="a2alab-eca-"))
    (d / "sfdx-project.json").write_text(
        json.dumps(
            {
                "packageDirectories": [{"path": "force-app", "default": True}],
                "sourceApiVersion": API_VERSION,
            }
        )
    )
    base = d / "force-app/main/default"
    base.mkdir(parents=True)  # a retrieve-only project still needs the package dir
    for rel, body in files.items():
        f = base / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(body)
    return d


def existing_consumer_key(p: Preset, org: str) -> str | None:
    """The live app's consumer key, or None if the app does not exist yet."""
    d = scratch_project({})
    proc = subprocess.run(
        ["sf", "project", "retrieve", "start", "-o", org, "--json",
         "-m", f"ExtlClntAppGlobalOauthSettings:{p.name}_glbloauth"],
        cwd=d, capture_output=True, text=True, check=False,
    )  # fmt: skip
    hits = list(d.rglob(f"{p.name}_glbloauth.ecaGlblOauth-meta.xml"))
    if proc.returncode != 0 or not hits:
        return None
    m = re.search(r"<consumerKey>([^<]+)</consumerKey>", hits[0].read_text())
    return m.group(1) if m else None


# ---------------------------------------------------------------- verbs


def cmd_show(p: Preset, _args) -> None:
    org = target_org()
    run_as = resolve_run_as(p, org)
    _, cert = ensure_keypair(p)
    for rel, body in render(p, run_as, user_email(org, run_as), cert, None).items():
        print(f"--- {rel}\n{body}")


def cmd_create(p: Preset, args) -> None:
    org = target_org()
    run_as = resolve_run_as(p, org)
    email = require_integration_user(p, org, run_as)["Email"]
    _, cert = ensure_keypair(p)
    ck = existing_consumer_key(p, org)
    print(f"{p.name}: {'exists — updating in place' if ck else 'new app'}; run-as {run_as}")
    d = scratch_project(render(p, run_as, email, cert, ck))
    verb = ["project", "deploy", "validate"] if args.validate else ["project", "deploy", "start"]
    r = sf(*verb, "-o", org, "--source-dir", "force-app", "--wait", "15", cwd=d)
    print(f"{'validated (check-only)' if args.validate else 'deployed'}: "
          f"{r.get('numberComponentsDeployed', '?')} components, status {r.get('status')}")  # fmt: skip
    if args.validate:
        return
    for _ in range(6):  # the consumer key is generated asynchronously on first deploy
        ck = existing_consumer_key(p, org)
        if ck:
            break
        time.sleep(5)
    if not ck:
        sys.exit("deployed, but the consumer key is not retrievable yet — re-run `create`")
    set_env_value(p.client_id_env, ck)
    set_env_value(p.run_as_env, run_as)
    print(f"wrote {p.client_id_env} + {p.run_as_env} to .env — "
          "push with `uv run python scripts/env_sync.py push` (D39)")  # fmt: skip


class CredentialError(RuntimeError):
    """A leg of the credential chain refused; the message says which and why."""


def mint_core_token(p: Preset) -> dict:
    """Leg 1: sign an RS256 assertion as the run-as user → core access token."""
    import httpx
    import jwt as pyjwt

    client_id = os.environ.get(p.client_id_env)
    run_as = os.environ.get(p.run_as_env)
    if not (client_id and run_as):
        raise CredentialError(f"{p.client_id_env} / {p.run_as_env} not set (run `create`)")
    kpath = key_dir() / p.key_file
    if not kpath.exists():
        raise CredentialError(f"signing key {kpath.name} missing from {key_dir().name}/")
    domain = os.environ.get("SF_MY_DOMAIN", "").rstrip("/")
    if not domain:
        raise CredentialError("SF_MY_DOMAIN not set")
    domain = domain if domain.startswith("https://") else f"https://{domain}"
    aud = os.environ.get("SF_LOGIN_URL", "https://login.salesforce.com")
    assertion = pyjwt.encode(
        {"iss": client_id, "sub": run_as, "aud": aud, "exp": int(time.time()) + 180},
        kpath.read_text(),
        algorithm="RS256",
    )
    r = httpx.post(
        f"{domain}/services/oauth2/token",
        data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
        timeout=20,
    )
    if r.status_code != 200:
        raise CredentialError(f"JWT-bearer grant refused (HTTP {r.status_code}): {r.text[:300]}")
    return r.json()


CDP_SCOPES = ("cdp_ingest_api", "cdp_profile_api", "cdp_query_api")


def exchange_cdp(core: dict, agent_id: str | None = None) -> dict:
    """Leg 2: core token → Data 360 tenant token at /services/a360/token.

    Same body the agentic-context SDK sends. `aid` is optional, and a360 rejects
    an UNREGISTERED agent id with access_denied — so it is only passed once the
    agent is registered (plan/18 Phase 1).
    """
    import httpx

    missing = [s for s in CDP_SCOPES if s not in core.get("scope", "")]
    if missing:
        raise CredentialError(f"core token lacks {missing} — the CDP exchange will refuse it")
    body = {
        "grant_type": "urn:salesforce:grant-type:external:cdp",
        "subject_token": core["access_token"],
        "subject_token_type": "urn:ietf:params:oauth:token-type:access_token",
    }
    if agent_id:
        body["aid"] = agent_id
    r = httpx.post(f"{core['instance_url']}/services/a360/token", data=body, timeout=20)
    cdp = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    if r.status_code != 200 or not cdp.get("access_token"):
        raise CredentialError(f"a360 CDP exchange refused (HTTP {r.status_code}): {r.text[:300]}")
    return cdp


def cmd_verify(p: Preset, args) -> None:
    try:
        core = mint_core_token(p)
        print(f"leg 1 ok: JWT-bearer → core token, scope = {core.get('scope', '')!r}")
        if not p.cdp_exchange:
            return
        cdp = exchange_cdp(core, args.agent_id)
    except CredentialError as e:
        sys.exit(str(e))
    # The tenant host is an environment identifier: report its shape, not its value.
    host = cdp.get("instance_url", "")
    print(f"leg 2 ok: a360 CDP token, tenant host ends {host[-24:]!r}, "
          f"expires_in={cdp.get('expires_in')}")  # fmt: skip


def main() -> None:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)
    for verb in ("show", "create", "verify"):
        sp = sub.add_parser(verb)
        sp.add_argument("preset", choices=sorted(PRESETS))
        if verb == "create":
            sp.add_argument("--validate", action="store_true", help="check-only deploy")
        if verb == "verify":
            sp.add_argument("--agent-id", help="also bind a registered AgentApiName (aid)")
    args = ap.parse_args()
    {"show": cmd_show, "create": cmd_create, "verify": cmd_verify}[args.verb](
        PRESETS[args.preset], args
    )


if __name__ == "__main__":
    main()
