"""The [A2A-LAB ROUTING] fanout-dispatch block (WS11): the console injects it,
the bridge reads the mode and strips the block before the legs see it. These
are the pure string helpers — the Agentforce round-trip itself cannot be tested
without the org, so the contract we CAN pin is: absent → sync, clamp unknowns,
strip every block."""

from interop import af_channel


def test_dispatch_block_carries_the_mode_and_the_marker():
    block = af_channel.dispatch_block("async")
    assert af_channel.ROUTING_MARKER in block
    assert "fanout-dispatch: async" in block
    assert block.rstrip().endswith("[/A2A-LAB ROUTING]")


def test_dispatch_block_clamps_an_unknown_mode_to_sync():
    # A stray value must not reach the bridge as a dispatch directive.
    assert "fanout-dispatch: sync" in af_channel.dispatch_block("banana")
    assert "fanout-dispatch: sync" in af_channel.dispatch_block("")


def test_read_dispatch_mode_defaults_to_sync_when_absent():
    # A never-injected or stripped block degrades to the blocking path.
    assert af_channel.read_dispatch_mode("just a plain situation") == "sync"
    assert af_channel.read_dispatch_mode("") == "sync"
    assert af_channel.read_dispatch_mode(None) == "sync"


def test_read_dispatch_mode_reads_what_the_block_asked_for():
    msg = "A port strike halts traffic." + af_channel.dispatch_block("async")
    assert af_channel.read_dispatch_mode(msg) == "async"


def test_read_dispatch_mode_is_case_insensitive():
    assert af_channel.read_dispatch_mode("fanout-dispatch: ASYNC") == "async"


def test_strip_routing_blocks_removes_the_block_and_leaves_the_situation():
    situation = "A port strike halts traffic through Rotterdam."
    msg = situation + af_channel.dispatch_block("async")
    stripped = af_channel.strip_routing_blocks(msg)
    assert af_channel.ROUTING_MARKER not in stripped
    assert "fanout-dispatch" not in stripped
    assert stripped == situation


def test_strip_routing_blocks_removes_every_block_in_one_pass():
    # An orchestrator might forward both the topology and the dispatch block.
    msg = "situation" + af_channel.topology_block("delegated") + af_channel.dispatch_block("async")
    stripped = af_channel.strip_routing_blocks(msg)
    assert af_channel.ROUTING_MARKER not in stripped
    assert stripped == "situation"


def test_strip_routing_blocks_is_a_noop_on_a_clean_message():
    assert af_channel.strip_routing_blocks("nothing to strip") == "nothing to strip"
    assert af_channel.strip_routing_blocks(None) == ""


# ---- the a2a-native channel (D106/WS29) ------------------------------------

import pytest  # noqa: E402

from interop import delegation  # noqa: E402

NATIVE_ENV = ("SF_MY_DOMAIN", "SF_AF_A2A_CLIENT_ID", "SF_AF_A2A_CLIENT_SECRET")


def test_the_native_channel_is_a_third_routing_choice():
    assert af_channel.CHANNEL_TOOLS["a2a-native"] == "ask_agentforce_a2a_native"
    block = af_channel.routing_block("a2a-native")
    assert "agentforce-channel: a2a-native" in block
    assert "ask_agentforce_a2a_native" in block


def test_each_platform_consults_its_own_twin(monkeypatch):
    # D25 on the native channel: the bot id IS the platform's twin agent id.
    monkeypatch.setenv("SF_AGENT_ID", "0Xxclaude")
    monkeypatch.setenv("SF_ADK_AGENT_ID", "0Xxadk")
    monkeypatch.setenv("SF_STRANDS_AGENT_ID", "0Xxstrands")
    monkeypatch.delenv("SF_OPENAI_AGENT_ID", raising=False)
    assert af_channel.native_twin_id("adk") == "0Xxadk"
    assert af_channel.native_twin_id("strands") == "0Xxstrands"
    # An unset twin falls back to the Claude twin, like the shim's map does.
    assert af_channel.native_twin_id("openai") == "0Xxclaude"


async def test_ask_native_names_what_is_missing(monkeypatch):
    for k in NATIVE_ENV:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SF_AGENT_ID", "0Xxclaude")
    with pytest.raises(RuntimeError, match="SF_AF_A2A_CLIENT_SECRET"):
        await af_channel.ask_native("q", platform="claude")


async def test_ask_native_points_the_client_at_the_twin(monkeypatch):
    for k in NATIVE_ENV:
        monkeypatch.setenv(k, "x")
    monkeypatch.setenv("SF_ADK_AGENT_ID", "0Xxadk")
    monkeypatch.setattr(af_channel, "_native_clients", {})
    seen = {}

    class FakeClient:
        def __init__(self, url, **kw):
            seen["url"], seen["kw"] = url, kw

        async def ask(self, req):
            seen["meta"] = req.metadata

            class R:
                text = "from the twin"

            return R()

    import interop.clients.a2a as a2a_mod

    monkeypatch.setattr(a2a_mod, "A2AClient", FakeClient)
    text = await af_channel.ask_native("q", {"delegation": {"depth": 1}}, platform="adk")
    assert text == "from the twin"
    assert seen["url"] == af_channel.NATIVE_A2A_BASE + "0Xxadk"
    assert seen["kw"]["auth"]["scheme"] == "salesforce-cc"
    assert seen["kw"]["protocol_version"] == "1.0"
    assert seen["kw"]["card_path"] == "/v1/card"
    assert seen["meta"] == {"delegation": {"depth": 1}}


async def test_consult_native_refuses_at_the_delegation_ceiling(monkeypatch):
    called = []
    monkeypatch.setattr(af_channel, "ask_native", lambda *a, **k: called.append(a))
    text = await af_channel.consult_native(
        "q",
        caller="x",
        platform="claude",
        inbound_depth=delegation.max_depth(),
        trace_id=None,
    )
    assert text == delegation.refusal("ask_agentforce_a2a_native")
    assert not called


async def test_consult_native_stamps_the_rider_and_never_raises(monkeypatch):
    seen = {}

    async def boom(message, metadata, trace_id=None, *, platform):
        seen.update(message=message, metadata=metadata, platform=platform)
        raise RuntimeError("gateway said no")

    monkeypatch.setattr(af_channel, "ask_native", boom)
    text = await af_channel.consult_native(
        "what is open?", caller="adk-gemini-agent", platform="adk", inbound_depth=0, trace_id="t1"
    )
    assert text.startswith("Native Agentforce A2A call failed: RuntimeError: gateway said no")
    assert "delegation" in seen["metadata"] and seen["platform"] == "adk"
    assert seen["message"].startswith("what is open?")
