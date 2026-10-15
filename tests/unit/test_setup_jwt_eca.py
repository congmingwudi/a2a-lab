"""The JWT-bearer ECA renderer (scripts/setup_jwt_eca.py, plan/18).

The script exists so the four ECA files never get committed: they are rendered
from .env at deploy time. These tests pin the parts that cost a round trip when
wrong — the scope set JWT bearer needs, the consumer key only being sent on an
update, and every grant except JWT bearer staying off.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import setup_jwt_eca as eca  # noqa: E402

CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"


def _files(consumer_key=None):
    return eca.render(
        eca.PRESETS["memory"], "runas@example.com", "a&b@example.com", CERT, consumer_key
    )


def test_memory_preset_carries_refresh_token_and_cdp_scopes():
    # JWT bearer 400s "refresh_token scope is required" without RefreshToken,
    # and the a360 exchange refuses a core token missing any cdp_* scope.
    scopes = {s.strip() for s in eca.PRESETS["memory"].scopes.split(",")}
    assert {"Api", "RefreshToken", "CDPIngest", "CDPProfile", "CDPQuery"} <= scopes


def test_renders_the_four_f6_files():
    names = sorted(_files())
    assert [n.split("/")[0] for n in names] == [
        "externalClientApps",
        "extlClntAppGlobalOauthSets",
        "extlClntAppOauthPolicies",
        "extlClntAppOauthSettings",
    ]


def test_consumer_key_only_sent_on_update():
    new = next(v for k, v in _files().items() if "glbloauth" in k)
    upd = next(v for k, v in _files("3MVGxyz").items() if "glbloauth" in k)
    assert "<consumerKey>" not in new
    assert "<consumerKey>3MVGxyz</consumerKey>" in upd


def test_only_jwt_bearer_is_enabled_and_xml_is_escaped():
    files = _files()
    glbl = next(v for k, v in files.items() if "glbloauth" in k)
    plcy = next(v for k, v in files.items() if "oauthPlcy" in k)
    for flag in ("isClientCredentialsFlowEnabled", "isDeviceFlowEnabled", "isTokenExchangeEnabled"):
        assert f"<{flag}>false</{flag}>" in glbl
    assert "<isClientCredentialsFlowEnabled>false</isClientCredentialsFlowEnabled>" in plcy
    assert "<permittedUsersPolicyType>AdminApprovedPreAuthorized<" in plcy
    eca_file = next(v for k, v in files.items() if k.startswith("externalClientApps/"))
    assert "a&amp;b@example.com" in eca_file


def test_set_env_value_upserts_and_uncomments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\n#SF_CLIENT_ID_MEMORY=\nB=2\n")
    monkeypatch.setattr(eca, "ENV_FILE", env)
    # set_env_value also writes os.environ; register both keys so they're restored.
    monkeypatch.setenv("SF_CLIENT_ID_MEMORY", "")
    monkeypatch.setenv("NEW_KEY", "")
    eca.set_env_value("SF_CLIENT_ID_MEMORY", "k1")
    eca.set_env_value("NEW_KEY", "v")
    assert env.read_text() == "A=1\nSF_CLIENT_ID_MEMORY=k1\nB=2\nNEW_KEY=v\n"


def test_memory_policy_preauthorizes_the_gate_permission_set_not_the_admin_profile():
    # D86: pre-authorizing the System Administrator profile admits every admin
    # as a JWT `sub`; the gate permission set admits only the integration user.
    plcy = next(v for k, v in _files().items() if "oauthPlcy" in k)
    assert (
        "<commaSeparatedPermissionSet>A2ALab_Memory_Integration</commaSeparatedPermissionSet>"
        in plcy
    )
    assert "commaSeparatedProfile" not in plcy


def test_a_gated_preset_refuses_an_admin_run_as_user(monkeypatch):
    import pytest

    monkeypatch.setattr(
        eca, "run_as_user", lambda org, u: {"Email": "x@example.com",
                                            "Profile": {"Name": "System Administrator"}}
    )  # fmt: skip
    with pytest.raises(SystemExit, match="dedicated integration user"):
        eca.require_integration_user(eca.PRESETS["memory"], "org", "admin@example.com")
    monkeypatch.setattr(
        eca, "run_as_user", lambda org, u: {"Email": "x@example.com",
                                            "Profile": {"Name": "Minimum Access - API Only Integrations"}}
    )  # fmt: skip
    assert eca.require_integration_user(eca.PRESETS["memory"], "org", "m@example.com")["Email"]
