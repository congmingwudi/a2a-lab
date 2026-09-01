"""Lab identity provider (WS6 U1): named users, RS256-signed lab JWTs.

The demo-scale IdP for the user-security layer: users live in
config/users.yaml, tokens are signed with a lab keypair under .a2alab/
(auto-generated on first use), and ANY seam — including hosted runtimes,
which get only the public key — can verify a token without a shared
secret. That asymmetry is the point: verification travels; the signing
key never does.

RS256 over HS256 deliberately (WS6 design): with a shared-secret HMAC,
every verifying seam could also MINT tokens — the remote platforms the
lab calls would hold god credentials. With RS256 the bridge, shims, and
containers verify with the public key alone.

This module issues and verifies; PROPAGATION across platforms is U2's
seam work, ENFORCEMENT is U3's.
"""

from __future__ import annotations

import os
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import jwt
import yaml

ISSUER = "a2a-lab"
KEY_DIR_ENV = "A2ALAB_JWT_DIR"
DEFAULT_KEY_DIR = ".a2alab"
TTL_ENV = "A2ALAB_JWT_TTL_S"
DEFAULT_TTL_S = 8 * 3600  # a demo day

SERVICE_TTL_ENV = "A2ALAB_SERVICE_JWT_TTL_S"
DEFAULT_SERVICE_TTL_S = 300  # short: the machine caller refreshes (WS10 spec §4.7)

# Machine client-credentials callers (WS10 SP1). Maps the lab subject to mint
# to the env vars holding its expected client_id / client_secret. A machine
# caller has NO console password (ROLE_PASSWORD_ENVS has no 'machine' key), so
# it can never be obtained through /api/login — only through the client-creds
# mint (issue_service_token) below. Add a row to register another machine caller.
SERVICE_CLIENTS: dict[str, tuple[str, str]] = {
    "mulesoft-omni-gateway": ("A2ALAB_MULE_GW_CLIENT_ID", "A2ALAB_MULE_GW_CLIENT_SECRET"),
}

_PRIVATE_PEM = "lab_jwt_private.pem"
_PUBLIC_PEM = "lab_jwt_public.pem"

USERS_PATH = Path("config/users.yaml")


def _key_dir() -> Path:
    return Path(os.environ.get(KEY_DIR_ENV, DEFAULT_KEY_DIR))


def ensure_keypair() -> tuple[Path, Path]:
    """Generate the lab keypair on first use (private stays out of git —
    .a2alab/ is already ignored); no-op when both files exist."""
    key_dir = _key_dir()
    private_path, public_path = key_dir / _PRIVATE_PEM, key_dir / _PUBLIC_PEM
    if private_path.exists() and public_path.exists():
        return private_path, public_path
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    private_path.chmod(0o600)
    public_path.write_bytes(
        key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    return private_path, public_path


PRIVATE_KEY_ENV = "A2ALAB_JWT_PRIVATE_KEY"


def _private_key() -> str:
    """The SIGNING half. Env wins, then the local keypair.

    The env route exists because the hosted console ISSUES tokens (WS13): it
    serves `/api/login`, so "containers must never hold the signing key" —
    true of a seam that only verifies — cannot hold for the issuer. It arrives
    through Secrets Manager like every other hosted credential (D39/F1), never
    on the task definition.

    Without it a container silently generates a FRESH keypair into its own
    ephemeral filesystem on first use. That fails in a way that looks like
    nothing: login succeeds, a token comes back, and every session dies at the
    next deploy because the key that signed it no longer exists.
    """
    from_env = os.environ.get(PRIVATE_KEY_ENV)
    if from_env:
        return from_env.replace("\\n", "\n")
    return ensure_keypair()[0].read_text()


PUBLIC_KEY_ENV = "A2ALAB_JWT_PUBLIC_KEY"


def public_key() -> str:
    """The verification half — safe to hand to any seam or hosted runtime.
    Env wins (that's how deploy scripts ship it to containers, which have
    no keypair and must never hold the signing key); else the local pair."""
    from_env = os.environ.get(PUBLIC_KEY_ENV)
    if from_env:
        return from_env.replace("\\n", "\n")
    return ensure_keypair()[1].read_text()


def load_users(path: str | Path = USERS_PATH) -> dict[str, dict]:
    p = Path(path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("users") or {}


def load_role_labels(path: str | Path = USERS_PATH) -> dict[str, str]:
    """Display-only relabels for the sign-in surface (config/users.yaml
    `role_labels:`). NEVER consulted for authorization — the functional role
    string is the identity; this only changes how a role reads on screen."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text()) or {}
    return raw.get("role_labels") or {}


def role_label(role: str | None, labels: dict[str, str] | None = None) -> str:
    """The on-screen label for a role — the mapped value, else the role
    verbatim. Display only; do not gate on the result."""
    table = labels if labels is not None else load_role_labels()
    return table.get(role or "", role or "")


ROLE_PASSWORD_ENVS = {
    "operator": "A2ALAB_OPERATOR_PASSWORD",
    "viewer": "A2ALAB_VIEWER_PASSWORD",
    # The lab owner's own role (D36). Distinct from operator ONLY so the
    # operator password can be handed to colleagues for running experiments
    # without also handing out the owner's login; the permissions are
    # identical (see OPERATOR_ROLES).
    "master of the universe": "A2ALAB_MASTER_PASSWORD",
}

# Roles that carry the full operator privilege set — everything a viewer
# cannot do (runs, warm-ups, harvest/analyze, config, credential expiry).
# The ONE place that answers "does this role have operator power?", so a new
# owner-tier role gets those surfaces instead of silently 403-ing on them:
# the viewer gate only blocks `viewer`, but the operator checks used to test
# equality with the single string "operator".
OPERATOR_ROLES = frozenset({"operator", "master of the universe"})

# The lab OWNER's role alone — a strict subset of OPERATOR_ROLES. Owner-tier
# surfaces are the ones the owner does not hand to colleagues even with the
# operator password: here, the deep link that launches the in-org "A2A Lab"
# Lightning app (the live Tableau Next dashboard behind a Salesforce login),
# which only the owner can actually authenticate into during a controlled
# presentation. `operator` (Ana) gets every experiment surface but NOT this.
OWNER_ROLES = frozenset({"master of the universe"})


def is_operator_role(role: str | None) -> bool:
    """True for any role with the full operator privilege set (D36)."""
    return role in OPERATOR_ROLES


def is_owner_role(role: str | None) -> bool:
    """True only for the lab owner's role — narrower than operator (D36).
    Gates owner-only affordances the operator password must not unlock."""
    return role in OWNER_ROLES


def authenticate(username: str, password: str, users: dict[str, dict] | None = None) -> str:
    """Password-gated token issue for the public console (D36): each ROLE
    has a shared password from .env — hand colleagues the viewer password,
    never the lab token. The JWT this returns is the browser's only
    credential from then on. Raises ValueError on unknown user, wrong
    password, or a role whose password is unset (fail closed — an unset
    env var must not mean open login on a public site)."""
    import hmac

    directory = users if users is not None else load_users()
    entry = directory.get(username)
    if entry is None:
        raise ValueError(f"unknown user '{username}'")
    role = entry.get("role") or "viewer"
    expected = os.environ.get(ROLE_PASSWORD_ENVS.get(role, ""), "")
    if not expected or not hmac.compare_digest(password or "", expected):
        raise ValueError("wrong password")
    return issue_token(username, users=directory)


def _issue(username: str, ttl: int, directory: dict[str, dict]) -> str:
    """Sign a lab JWT for a directory user with an explicit TTL: the single
    place claims are constructed, so the human (issue_token) and machine
    (issue_service_token) paths cannot drift."""
    entry = directory.get(username)
    if entry is None:
        raise ValueError(f"unknown user '{username}' — see config/users.yaml")
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": username,
        "name": entry.get("name") or username,
        "role": entry.get("role") or "viewer",
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, _private_key(), algorithm="RS256")


def issue_token(username: str, users: dict[str, dict] | None = None) -> str:
    """A lab JWT for a directory user: {sub, name, role, iss, iat, exp}."""
    directory = users if users is not None else load_users()
    ttl = int(os.environ.get(TTL_ENV, str(DEFAULT_TTL_S)))
    return _issue(username, ttl, directory)


def issue_service_token(
    subject: str, ttl: int | None = None, users: dict[str, dict] | None = None
) -> str:
    """A SHORT-LIVED lab JWT for a MACHINE caller — no human password, no
    /api/login. Safe to keep short precisely because the caller refreshes
    (WS10 spec §4.7). Same claim shape as issue_token; only the TTL differs.
    Fails closed unless ``subject`` is a ``machine`` identity: this mint must
    never issue a token for a human/operator subject."""
    directory = users if users is not None else load_users()
    entry = directory.get(subject)
    if entry is None or entry.get("role") != "machine":
        raise ValueError(f"issue_service_token: '{subject}' is not a machine identity")
    if ttl is None:
        ttl = int(os.environ.get(SERVICE_TTL_ENV, str(DEFAULT_SERVICE_TTL_S)))
    return _issue(subject, ttl, directory)


def authenticate_client(client_id: str, client_secret: str) -> str:
    """Validate a machine client-credentials pair against SERVICE_CLIENTS and
    return the lab subject to mint for. Fail CLOSED: missing input, an
    unconfigured client (either env unset), or a mismatch all raise ValueError.
    Both comparisons are constant-time (hmac.compare_digest)."""
    import hmac

    if not client_id or not client_secret:
        raise ValueError("missing client credentials")
    for subject, (id_env, secret_env) in SERVICE_CLIENTS.items():
        expected_id = os.environ.get(id_env, "")
        expected_secret = os.environ.get(secret_env, "")
        if not expected_id or not expected_secret:
            continue  # not configured on this deployment — cannot match
        if hmac.compare_digest(client_id, expected_id) and hmac.compare_digest(
            client_secret, expected_secret
        ):
            return subject
    raise ValueError("unknown or invalid client credentials")


def verify_token(token: str, public_pem: str | None = None) -> dict[str, Any] | None:
    """Claims for a valid lab token, None for anything else (expired,
    tampered, foreign issuer). Seams treat None as 'no verified user' —
    whether that refuses or degrades to asserted-only is the caller's
    policy (U3)."""
    try:
        return jwt.decode(
            token,
            public_pem or public_key(),
            algorithms=["RS256"],
            issuer=ISSUER,
            options={"require": ["exp", "sub", "iss"]},
        )
    except jwt.PyJWTError:
        return None


@lru_cache(maxsize=1)
def _cached_public_key() -> str:
    return public_key()


def looks_like_jwt(value: str) -> bool:
    return value.count(".") == 2 and value.startswith("eyJ")


def user_context(claims: dict[str, Any]) -> dict[str, Any]:
    """The wire shape (U2): what rides metadata["user_context"] — claims
    the remote side can display/log; the JWT itself is the verifiable
    channel."""
    return {
        "sub": claims.get("sub"),
        "name": claims.get("name"),
        "role": claims.get("role"),
    }
