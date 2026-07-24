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


def _private_key() -> str:
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


ROLE_PASSWORD_ENVS = {
    "operator": "A2ALAB_OPERATOR_PASSWORD",
    "viewer": "A2ALAB_VIEWER_PASSWORD",
}


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


def issue_token(username: str, users: dict[str, dict] | None = None) -> str:
    """A lab JWT for a directory user: {sub, name, role, iss, iat, exp}."""
    directory = users if users is not None else load_users()
    entry = directory.get(username)
    if entry is None:
        raise ValueError(f"unknown user '{username}' — see config/users.yaml")
    now = int(time.time())
    ttl = int(os.environ.get(TTL_ENV, str(DEFAULT_TTL_S)))
    claims = {
        "iss": ISSUER,
        "sub": username,
        "name": entry.get("name") or username,
        "role": entry.get("role") or "viewer",
        "iat": now,
        "exp": now + ttl,
    }
    return jwt.encode(claims, _private_key(), algorithm="RS256")


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
