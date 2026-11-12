from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))


def test_setup_listing_filters_the_unpublished_global_route(monkeypatch):
    import setup_agentic_memory as setup

    seen = []

    def fake_api(method, path, body=None, base=setup.API):
        seen.append((method, path, body, base))
        return {
            "setupConfigs": [
                {"name": "default", "dataspaceName": "default"},
                {"name": "probe", "dataspaceName": "A2ALabMemoryProbe"},
            ]
        }

    monkeypatch.setattr(setup, "api", fake_api)
    monkeypatch.setattr(setup, "data_space", lambda: "A2ALabMemoryProbe")

    assert setup.setups() == [{"name": "probe", "dataspaceName": "A2ALabMemoryProbe"}]
    assert seen == [("GET", "/setup", None, setup.API)]


def test_space_probe_registration_declares_custom_unified_profile_and_graph(monkeypatch):
    import setup_agentic_memory as setup

    monkeypatch.setattr(setup, "data_space", lambda: "A2ALabMemoryProbe")

    body = setup.register_body(setup.AGENTS["space-probe"])

    assert body["dataspaceName"] == "A2ALabMemoryProbe"
    assert body["configuration"] == {
        "profileDmos": [{"name": "amp_UnifiedIndividualAmp1__dlm"}],
        "contextDataSources": {
            "dataGraphs": [{"name": "A2ALab_MemoryProbe_C360V2"}]
        },
    }
