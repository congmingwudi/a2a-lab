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
