"""The combined protocol-faces app (WS13 item 2).

These faces were the lab's last runtime dependency on the operator's laptop:
nine cells in config/targets.yaml pointed at localhost:80xx, which inside a
container is the container. Hosting them as one ASGI app is what lets the
hosted console run a protocol comparison at all.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from faces import FACES, build_faces_app


def test_every_face_mounts_under_its_target_name():
    """The mount prefix IS the config/targets.yaml target name, so a failing
    cell maps to a URL without a lookup table."""
    app = build_faces_app("https://faces.example.com")
    with TestClient(app) as client:
        listed = client.get("/").json()["faces"]
    assert listed == [prefix for prefix, _, _ in FACES]
    assert {"claude-mcp", "claude-a2a", "openai-mcp", "openai-a2a"} <= set(listed)
    assert {"guide-rest", "guide-mcp", "guide-a2a"} <= set(listed)
    assert {"agentforce-mcp", "agentforce-a2a"} <= set(listed)


def test_healthz_is_open_and_says_nothing_useful():
    """The ALB health check carries no credentials; a gated health path marks
    every task unhealthy and the service never stabilises (same rule as the
    console's, D48)."""
    with TestClient(build_faces_app()) as client:
        resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "healthy"
    assert resp.json()["app"] == "faces"


def test_a2a_cards_advertise_their_own_mounted_url(monkeypatch):
    """An AgentCard advertises an ABSOLUTE url that a client then calls back.
    A mounted app cannot infer its prefix, so the base is passed in — get this
    wrong and the card points somewhere unreachable, which no test of the mount
    itself would catch."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    with TestClient(build_faces_app("https://faces.example.com/")) as client:
        for prefix in ("claude-a2a", "openai-a2a", "guide-a2a", "agentforce-a2a"):
            resp = client.get(
                f"/{prefix}/.well-known/agent-card.json", headers={"X-Lab-Token": "sekrit"}
            )
            assert resp.status_code == 200, prefix
            assert resp.json()["url"] == f"https://faces.example.com/{prefix}/"


def test_mcp_faces_have_a_started_transport(monkeypatch):
    """Starlette's Mount does NOT run a mounted app's lifespan, and FastMCP
    starts its streamable-HTTP session manager there. Without the parent
    lifespan every MCP face answers `RuntimeError: Task group is not
    initialized` — while the route resolves perfectly, which is exactly why
    this needs asserting on a real call rather than on the mount."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    headers = {
        "X-Lab-Token": "sekrit",
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    with TestClient(build_faces_app()) as client:
        for prefix in ("claude-mcp", "openai-mcp", "guide-mcp", "agentforce-mcp"):
            resp = client.post(
                f"/{prefix}/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            )
            assert resp.status_code == 200, f"{prefix}: {resp.text[:200]}"
            assert "Task group is not initialized" not in resp.text


def test_faces_are_behind_the_shared_token(monkeypatch):
    """These are public internet behind an ALB. Only /healthz and / are open."""
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    with TestClient(build_faces_app()) as client:
        assert client.get("/claude-rest/healthz").status_code == 401
        assert client.get("/healthz").status_code == 200


def test_one_broken_face_does_not_take_down_the_others(monkeypatch):
    """Losing the whole board to one missing key is worse than losing a cell —
    the lab's subject is comparing cells side by side."""
    import faces as faces_mod

    real = faces_mod._adapter

    def explode(platform):
        if platform == "openai":
            raise RuntimeError("no OPENAI_API_KEY")
        return real(platform)

    monkeypatch.setattr(faces_mod, "_adapter", explode)
    monkeypatch.setenv("A2ALAB_TOKEN", "sekrit")
    with TestClient(build_faces_app()) as client:
        broken = client.get("/openai-rest/healthz", headers={"X-Lab-Token": "sekrit"})
        assert broken.status_code == 503
        assert "no OPENAI_API_KEY" in broken.json()["detail"]
        assert (
            client.get("/claude-rest/healthz", headers={"X-Lab-Token": "sekrit"}).status_code == 200
        )
