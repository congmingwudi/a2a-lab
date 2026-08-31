"""Console client-credentials token endpoint (WS10 SP1). The MuleSoft Omni
Gateway POSTs form-encoded client_credentials and gets a short-lived RS256 lab
JWT for sub=mulesoft-omni-gateway."""

from fastapi.testclient import TestClient


def _client(monkeypatch):
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_ID", "gw-id-123")
    monkeypatch.setenv("A2ALAB_MULE_GW_CLIENT_SECRET", "gw-secret-abc")
    monkeypatch.setenv("A2ALAB_SERVICE_JWT_TTL_S", "300")
    from console.app import create_console_app

    return TestClient(create_console_app())


def test_oauth_token_happy_path(monkeypatch):
    from interop import identity

    client = _client(monkeypatch)
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "gw-id-123",
            "client_secret": "gw-secret-abc",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "Bearer"
    assert body["expires_in"] == 300
    claims = identity.verify_token(body["access_token"])
    assert claims is not None and claims["sub"] == "mulesoft-omni-gateway"


def test_oauth_token_bad_creds_returns_401(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/oauth/token",
        data={
            "grant_type": "client_credentials",
            "client_id": "gw-id-123",
            "client_secret": "wrong",
        },
    )
    assert resp.status_code == 401


def test_oauth_token_wrong_grant_type_returns_400(monkeypatch):
    client = _client(monkeypatch)
    resp = client.post(
        "/oauth/token",
        data={"grant_type": "password", "client_id": "gw-id-123", "client_secret": "gw-secret-abc"},
    )
    assert resp.status_code == 400
