"""WS10 SP1: shape assertions on the committed agentic-network descriptors.
The AgentScript broker is compiler-validated by `agent-network project build`
(operator-run), NOT here — this test asserts only the deterministic YAML/JSON
shape of what we author."""

import json
from pathlib import Path

import yaml

ROOT = Path("mulesoft/agent-network")
SIX = {"claude", "openai", "strands", "guide", "agentforce", "langgraph"}
TOKEN_URL = "https://console-lab.agenticthings.com/oauth/token"


def _network():
    return yaml.safe_load((ROOT / "agent-network.yaml").read_text())


def test_registry_has_the_six_faces():
    net = _network()
    assert set(net["registry"]["agents"]) == SIX


def test_every_connection_is_a2a_oauth2_client_credentials_to_the_console():
    net = _network()
    conns = net["context"]["connections"]
    assert {c[: -len("Conn")] for c in conns} == SIX  # claudeConn, openaiConn, …
    for name, conn in conns.items():
        assert conn["kind"] == "a2a", name
        auth = conn["authentication"]
        assert auth["kind"] == "oauth2-client-credentials", name
        assert auth["token"]["url"] == TOKEN_URL, name
        assert auth["token"]["bodyEncoding"] == "form", name
        # Secrets arrive as deploy variables, never literals.
        assert auth["clientId"] == "${gwClientId}", name
        assert auth["clientSecret"] == "${gwClientSecret}", name


def test_broker_references_the_agentscript_source():
    net = _network()
    assert net["brokers"]["broker1"]["kind"] == "AgentScript"
    assert net["brokers"]["broker1"]["implementation"] == "./brokers/broker1.agent"
    assert (ROOT / "brokers" / "broker1.agent").exists()


# The committed source of truth is the .template — the real exchange.json is
# rendered from it by render_exchange.py (org id substituted from .env) and is
# gitignored, so it may or may not exist on disk during a test run.
EXCHANGE_TEMPLATE = ROOT / "exchange.json.template"


def test_exchange_template_declares_gw_secret_as_secured_variable_and_no_literals():
    ex = json.loads(EXCHANGE_TEMPLATE.read_text())
    variables = ex["metadata"]["variables"]
    assert set(variables) == SIX | {"gwClientId", "gwClientSecret"}
    assert variables["gwClientSecret"]["secret"] is True
    # No secret value is committed — defaults are empty.
    assert variables["gwClientSecret"].get("default", "") == ""
    assert variables["gwClientId"].get("default", "") == ""


def test_exchange_template_has_gav_with_org_id_as_placeholder():
    # `agent-network project build` requires Exchange GAV coordinates, and
    # `project publish` checks the session owns the org named by groupId — so
    # groupId/organizationId are the org id. They must ride a .env placeholder,
    # never a committed literal (root CLAUDE.md: no account identifier in repo).
    raw = EXCHANGE_TEMPLATE.read_text()
    ex = json.loads(raw)
    assert ex["assetId"]  # committed literals, not account identifiers
    assert ex["version"]
    assert ex["groupId"] == "${A2ALAB_MULE_ORG_ID}"
    assert ex["organizationId"] == "${A2ALAB_MULE_ORG_ID}"


def test_no_account_identifiers_in_descriptors():
    # Region-only hostnames are fine; an org id / BG name must never appear.
    # Scan every committed descriptor, but skip two gitignored things this
    # filesystem walk (unlike git) would otherwise pick up: the rendered
    # exchange.json (deliberately carries the real org id locally), and the
    # `target/` Maven/AF build output — binary `project.zip` bundles that
    # `read_text()` can't decode and that carry no committed descriptor anyway.
    files = [
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and p.name != "exchange.json"
        and "target" not in p.relative_to(ROOT).parts
    ]
    blob = " ".join(p.read_text() for p in files)
    assert "00b44e97" not in blob  # the MuleSoft root BG id (auto-memory)
    assert "salesforce-5782" not in blob  # the org domain


def test_broker_target_registered_as_via_fabric():
    from interop.registry import Registry

    reg = Registry.load()
    target = reg.get("mule-broker-a2a")
    assert target.protocol == "a2a"
    assert target.platform == "mulesoft"
    assert target.status == "via-fabric"
