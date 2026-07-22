import pytest

from interop.registry import Registry

YAML = """
targets:
  echo-rest:
    platform: echo
    protocol: rest
    endpoint: http://localhost:9001
    status: native
  echo-mcp:
    platform: echo
    protocol: mcp
    endpoint: http://localhost:9002/mcp
  echo-a2a:
    platform: echo
    protocol: a2a
    endpoint: http://localhost:9003
  with-env:
    platform: echo
    protocol: rest
    endpoint: http://${A2ALAB_TEST_HOST}:9001
    auth: {header_name: X-Bridge-Token, header_value: "${A2ALAB_TEST_TOKEN}"}
    status: via-bridge
  echo-hosted:
    platform: echo
    protocol: rest
    endpoint: http://hosted:9001
    status: native

modes:
  hosted:
    echo-rest: echo-hosted
"""


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("A2ALAB_TEST_HOST", "envhost")
    monkeypatch.setenv("A2ALAB_TEST_TOKEN", "sekrit")
    path = tmp_path / "targets.yaml"
    path.write_text(YAML)
    return Registry.load(path)


def test_load_and_defaults(registry):
    t = registry.get("echo-rest")
    assert t.platform == "echo" and t.status == "native"
    assert registry.get("echo-mcp").status == "native"  # default


def test_env_expansion(registry):
    t = registry.get("with-env")
    assert t.endpoint == "http://envhost:9001"
    assert t.auth["header_value"] == "sekrit"
    assert t.status == "via-bridge"


def test_unknown_target(registry):
    with pytest.raises(KeyError, match="unknown target"):
        registry.get("nope")


def test_client_for_types(registry):
    from interop.clients.a2a import A2AClient
    from interop.clients.mcp import McpClient
    from interop.clients.rest import RestClient

    assert isinstance(registry.client_for("echo-rest"), RestClient)
    assert isinstance(registry.client_for("echo-mcp"), McpClient)
    assert isinstance(registry.client_for("echo-a2a"), A2AClient)


def test_mode_remap(registry, monkeypatch):
    # Default mode: no remap.
    assert registry.mode == "local"
    assert registry.resolve_name("echo-rest") == "echo-rest"

    monkeypatch.setenv("A2ALAB_MODE", "hosted")
    assert registry.mode == "hosted"
    assert registry.resolve_name("echo-rest") == "echo-hosted"
    # Unmapped names and unknown modes resolve to themselves.
    assert registry.resolve_name("echo-mcp") == "echo-mcp"
    monkeypatch.setenv("A2ALAB_MODE", "nonesuch")
    assert registry.resolve_name("echo-rest") == "echo-rest"


def test_mode_remap_client_and_exact(registry, monkeypatch):
    monkeypatch.setenv("A2ALAB_MODE", "hosted")
    # client_for follows the remap and the client is honest about the
    # target it actually talks to; exact=True (the matrix harness) doesn't.
    assert registry.client_for("echo-rest").target_name == "echo-hosted"
    assert registry.client_for("echo-rest", exact=True).target_name == "echo-rest"


def test_alias_resolves_to_canonical_target(tmp_path):
    """Renamed targets keep answering to their old names: platform-side
    callers (the Agentforce twins' published scripts) pin bridge target
    names the lab cannot hot-swap."""
    cfg = tmp_path / "targets.yaml"
    cfg.write_text(
        """
targets:
  google-adk-a2a:
    aliases: [adk-a2a]
    platform: adk
    protocol: a2a
    endpoint: http://localhost:9
"""
    )
    from interop.registry import Registry

    reg = Registry.load(cfg)
    assert reg.get("adk-a2a").name == "google-adk-a2a"
    assert reg.resolve_name("adk-a2a") == "google-adk-a2a"
