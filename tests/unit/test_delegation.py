from interop import delegation
from interop.models import AgentRequest


def test_origin_request_depth_zero():
    req = AgentRequest(message="what is A2A?")
    assert delegation.depth_of(req) == 0
    assert delegation.allowed(req)


def test_metadata_depth_wins():
    req = AgentRequest(
        message="plain question",
        metadata={"delegation": {"caller": "x", "platform": "y", "depth": 2}},
    )
    assert delegation.depth_of(req) == 2
    assert not delegation.allowed(req)


def test_rider_scan_fallback():
    # Depth survives a text-only platform hop: no metadata, rider in message.
    message, meta = delegation.delegate(
        "who owns the Apple account?",
        caller="claude-sdk-agent",
        platform="claude",
        inbound_depth=0,
    )
    assert delegation.MARKER in message and message.endswith(delegation.END_MARKER)
    assert meta["delegation"] == {"caller": "claude-sdk-agent", "platform": "claude", "depth": 1}
    req = AgentRequest(message=message)  # metadata lost, rider text kept
    assert delegation.depth_of(req) == 1
    assert not delegation.allowed(req)  # default max depth 1


def test_mangled_rider_still_counts_as_delegated():
    req = AgentRequest(message=f"question\n{delegation.MARKER}\ngarbage\n")
    assert delegation.depth_of(req) == 1


def test_max_depth_env(monkeypatch):
    monkeypatch.setenv("A2ALAB_MAX_DELEGATION_DEPTH", "2")
    req = AgentRequest(message="q", metadata={"delegation": {"depth": 1}})
    assert delegation.allowed(req)
    req2 = AgentRequest(message="q", metadata={"delegation": {"depth": 2}})
    assert not delegation.allowed(req2)


def test_refusal_names_seam_and_is_instructive():
    text = delegation.refusal("bridge")
    assert "bridge" in text and "circular" in text
