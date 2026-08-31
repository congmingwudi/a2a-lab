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


def test_the_signing_key_can_come_from_the_environment(monkeypatch, tmp_path):
    """The hosted console ISSUES tokens (/api/login), so "containers must never
    hold the signing key" — true of a seam that only verifies — cannot hold for
    the issuer (WS13).

    Without this route a container generates a FRESH keypair into its own
    ephemeral filesystem on first use, which fails in a way that looks like
    nothing: login succeeds, a token comes back, and every session dies at the
    next deploy because the key that signed it no longer exists.
    """
    from interop import identity

    # A real keypair, generated where nothing else can see it.
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path))
    private_path, public_path = identity.ensure_keypair()
    private_pem, public_pem = private_path.read_text(), public_path.read_text()

    # Now point the key dir somewhere empty: only the env can supply the key.
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv(identity.KEY_DIR_ENV, str(empty))
    monkeypatch.setenv(identity.PRIVATE_KEY_ENV, private_pem)
    monkeypatch.setenv(identity.PUBLIC_KEY_ENV, public_pem)

    users = {"ryan": {"name": "Ryan Cox", "role": "operator"}}
    token = identity.issue_token("ryan", users=users)
    claims = identity.verify_token(token, public_pem=public_pem)
    assert claims and claims["sub"] == "ryan"
    # and the empty dir stayed empty — no keypair was silently generated
    assert not list(empty.iterdir())


def test_escaped_newlines_in_the_env_key_are_restored(monkeypatch, tmp_path):
    """A PEM crosses Secrets Manager and a task definition as one line. The
    public half already handled this; the private half must too, or the key
    parses as garbage."""
    from interop import identity

    monkeypatch.setenv(identity.KEY_DIR_ENV, str(tmp_path))
    private_pem = identity.ensure_keypair()[0].read_text()
    monkeypatch.setenv(identity.PRIVATE_KEY_ENV, private_pem.replace("\n", "\\n"))
    assert identity._private_key() == private_pem


def test_machine_caller_is_in_directory_but_has_no_password_login(monkeypatch):
    from interop import identity

    users = identity.load_users()
    assert "mulesoft-omni-gateway" in users
    assert users["mulesoft-omni-gateway"]["role"] == "machine"

    # A machine caller has NO console password (ROLE_PASSWORD_ENVS has no
    # 'machine' key), so /api/login's authenticate() must fail closed for it
    # even if a password is supplied.
    monkeypatch.delenv("A2ALAB_OPERATOR_PASSWORD", raising=False)
    with pytest.raises(ValueError):
        identity.authenticate("mulesoft-omni-gateway", "anything")


def test_issue_service_token_mints_short_lived_machine_jwt(monkeypatch):
    from interop import identity

    monkeypatch.setenv("A2ALAB_SERVICE_JWT_TTL_S", "120")
    token = identity.issue_service_token("mulesoft-omni-gateway")
    claims = identity.verify_token(token)
    assert claims is not None
    assert claims["iss"] == "a2a-lab"
    assert claims["sub"] == "mulesoft-omni-gateway"
    assert claims["role"] == "machine"
    assert claims["exp"] - claims["iat"] == 120


def test_issue_service_token_rejects_unknown_subject():
    from interop import identity

    with pytest.raises(ValueError):
        identity.issue_service_token("nobody")


def test_authenticate_client_accepts_matching_creds_and_returns_subject(monkeypatch):
    from interop import identity

    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_ID", "gw-id-123")
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_SECRET", "gw-secret-abc")
    assert identity.authenticate_client("gw-id-123", "gw-secret-abc") == "mulesoft-omni-gateway"


def test_authenticate_client_rejects_bad_creds(monkeypatch):
    from interop import identity

    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_ID", "gw-id-123")
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_SECRET", "gw-secret-abc")
    with pytest.raises(ValueError):
        identity.authenticate_client("gw-id-123", "wrong")
    with pytest.raises(ValueError):
        identity.authenticate_client("", "")


def test_authenticate_client_fails_closed_when_unconfigured(monkeypatch):
    from interop import identity

    monkeypatch.delenv("A2ALAB_MULE_GW_CLIENT_ID", raising=False)
    monkeypatch.delenv("A2ALAB_MULE_GW_CLIENT_SECRET", raising=False)
    with pytest.raises(ValueError):
        identity.authenticate_client("gw-id-123", "gw-secret-abc")
