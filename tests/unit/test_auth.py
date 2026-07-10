"""TokenAuthMiddleware: the shared auth seam for tunnel-exposed apps."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from interop.servers.auth import TokenAuthMiddleware


def make_client(monkeypatch, token_env=None, **mw_kwargs):
    if token_env is None:
        monkeypatch.delenv("A2ALAB_TOKEN", raising=False)
    else:
        monkeypatch.setenv("A2ALAB_TOKEN", token_env)
    app = FastAPI()

    @app.get("/healthz")
    async def healthz():
        return {"ok": True}

    @app.get("/api/data")
    async def data():
        return {"secret": 42}

    return TestClient(TokenAuthMiddleware(app, **mw_kwargs))


def test_passthrough_when_token_unset(monkeypatch):
    client = make_client(monkeypatch)
    assert client.get("/api/data").status_code == 200


def test_rejects_without_token(monkeypatch):
    client = make_client(monkeypatch, token_env="sekrit")
    assert client.get("/api/data").status_code == 401


def test_accepts_header_and_bearer(monkeypatch):
    client = make_client(monkeypatch, token_env="sekrit")
    assert client.get("/api/data", headers={"x-lab-token": "sekrit"}).status_code == 200
    assert (
        client.get("/api/data", headers={"authorization": "Bearer sekrit"}).status_code == 200
    )
    assert client.get("/api/data", headers={"x-lab-token": "wrong"}).status_code == 401


def test_query_param_only_when_enabled(monkeypatch):
    strict = make_client(monkeypatch, token_env="sekrit")
    assert strict.get("/api/data?token=sekrit").status_code == 401
    lax = make_client(monkeypatch, token_env="sekrit", allow_query_param=True)
    assert lax.get("/api/data?token=sekrit").status_code == 200


def test_exempt_paths_stay_open(monkeypatch):
    client = make_client(monkeypatch, token_env="sekrit")
    assert client.get("/healthz").status_code == 200
