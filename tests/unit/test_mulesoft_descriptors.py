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


def test_exchange_declares_gw_secret_as_secured_variable_and_no_literals():
    ex = json.loads((ROOT / "exchange.json").read_text())
    variables = ex["metadata"]["variables"]
    assert set(variables) == SIX | {"gwClientId", "gwClientSecret"}
    assert variables["gwClientSecret"]["secret"] is True
    # No secret value is committed — defaults are empty.
    assert variables["gwClientSecret"].get("default", "") == ""
    assert variables["gwClientId"].get("default", "") == ""


def test_no_account_identifiers_in_descriptors():
    # Region-only hostnames are fine; an org id / BG name must never appear.
    blob = " ".join(p.read_text() for p in ROOT.rglob("*") if p.is_file())
    assert "00b44e97" not in blob  # the MuleSoft root BG id (auto-memory)
    assert "salesforce-5782" not in blob  # the org domain
