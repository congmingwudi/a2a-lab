"""Lab identity provider (WS6 U1): keypair, tokens, middleware acceptance."""

from __future__ import annotations

import time

import jwt as pyjwt
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from interop import identity
from interop.servers.auth import TokenAuthMiddleware

USERS = {
    "ryan": {"name": "Ryan Cox", "role": "operator"},
    "vic": {"name": "Vic the Visitor", "role": "viewer"},
}


@pytest.fixture(autouse=True)
def keypair_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path / "keys"))
    yield tmp_path / "keys"


def test_keypair_generated_once_and_private_is_0600(keypair_dir):
    private1, public1 = identity.ensure_keypair()
    assert private1.exists() and public1.exists()
    assert oct(private1.stat().st_mode & 0o777) == "0o600"
    mtime = private1.stat().st_mtime_ns
    identity.ensure_keypair()  # no regeneration
    assert private1.stat().st_mtime_ns == mtime


def test_issue_and_verify_roundtrip():
    token = identity.issue_token("ryan", users=USERS)
    claims = identity.verify_token(token)
    assert claims["sub"] == "ryan"
    assert claims["role"] == "operator"
    assert identity.user_context(claims) == {
        "sub": "ryan",
        "name": "Ryan Cox",
        "role": "operator",
    }


def test_unknown_user_refused():
    with pytest.raises(ValueError, match="unknown user"):
        identity.issue_token("mallory", users=USERS)


def test_tampered_and_expired_tokens_verify_to_none(monkeypatch):
    token = identity.issue_token("ryan", users=USERS)
    header, payload, sig = token.split(".")
    assert identity.verify_token(f"{header}.{payload}x.{sig}") is None
    # expired
    monkeypatch.setenv(identity.TTL_ENV, "-10")
    assert identity.verify_token(identity.issue_token("ryan", users=USERS)) is None


def test_foreign_issuer_rejected():
    # Same algorithm, different keypair — a token from anyone else's IdP
    # must not verify against the lab's public key.
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = other.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    forged = pyjwt.encode(
        {"iss": identity.ISSUER, "sub": "ryan", "exp": int(time.time()) + 600},
        pem,
        algorithm="RS256",
    )
    assert identity.verify_token(forged) is None


def _client_with_user_echo(monkeypatch):
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    app = FastAPI()

    @app.get("/api/whoami")
    async def whoami(request: Request):
        return {"user": request.scope.get("state", {}).get("lab_user")}

    return TestClient(TokenAuthMiddleware(app))


def test_middleware_accepts_lab_jwt_and_exposes_claims(monkeypatch):
    client = _client_with_user_echo(monkeypatch)
    token = identity.issue_token("vic", users=USERS)
    r = client.get("/api/whoami", headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["user"]["sub"] == "vic"
    assert r.json()["user"]["role"] == "viewer"


def test_middleware_still_accepts_shared_token_without_user(monkeypatch):
    client = _client_with_user_echo(monkeypatch)
    r = client.get("/api/whoami", headers={"x-lab-token": "sekrit"})
    assert r.status_code == 200
    assert r.json()["user"] is None


def test_middleware_rejects_garbage_jwt(monkeypatch):
    client = _client_with_user_echo(monkeypatch)
    bad = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJyeWFuIn0.notarealsignature"
    assert client.get("/api/whoami", headers={"authorization": f"Bearer {bad}"}).status_code == 401


def test_authenticate_role_passwords(monkeypatch):
    # D36: per-ROLE passwords from env; unset role password = fail closed.
    monkeypatch.setenv("A2ALAB_OPERATOR_PASSWORD", "op-pass")
    monkeypatch.setenv("A2ALAB_VIEWER_PASSWORD", "view-pass")
    token = identity.authenticate("ryan", "op-pass", users=USERS)
    assert identity.verify_token(token)["role"] == "operator"
    token = identity.authenticate("vic", "view-pass", users=USERS)
    assert identity.verify_token(token)["role"] == "viewer"
    with pytest.raises(ValueError):  # wrong-role password refused
        identity.authenticate("ryan", "view-pass", users=USERS)
    with pytest.raises(ValueError):
        identity.authenticate("vic", "", users=USERS)
    monkeypatch.delenv("A2ALAB_VIEWER_PASSWORD")
    with pytest.raises(ValueError):  # unset password = login disabled
        identity.authenticate("vic", "view-pass", users=USERS)
