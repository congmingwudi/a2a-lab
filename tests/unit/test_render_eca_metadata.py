"""The client-credentials ECA metadata renderer (scripts/render_eca_metadata.py).

The policy, app and OAuth-settings files name a run-as username, a contact email
or (once retrieved) the org id, so they are gitignored and rendered from .env.
These tests pin the ways that breaks: an identifier leaking back into a tracked
file, and the render drifting from what was deployed (the two refresh-token
shapes are recorded, not normalized; the org-generated ids are never rendered).
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import render_eca_metadata as rp  # noqa: E402


def test_every_policy_takes_its_run_as_from_env():
    body = rp.render(rp.POLICIES["obs"], "a&b@example.com")
    assert "<clientCredentialsFlowUser>a&amp;b@example.com</clientCredentialsFlowUser>" in body
    assert "<externalClientApplication>a2a_lab_obs</externalClientApplication>" in body
    assert "<isClientCredentialsFlowEnabled>true</isClientCredentialsFlowEnabled>" in body


def test_app_keeps_its_inactivity_refresh_window():
    app = rp.render(rp.POLICIES["app"], "u@example.com")
    other = rp.render(rp.POLICIES["shim"], "u@example.com")
    assert "SpecificInactivity" in app and "namedUserJwtSessionTimeoutType" in app
    assert "SpecificLifetime" in other and "namedUserJwtSessionTimeoutType" not in other


def test_missing_run_as_fails_loudly(monkeypatch):
    monkeypatch.setattr(rp, "load_env", lambda: None)
    monkeypatch.delenv("SF_OBS_RUNAS_USER", raising=False)
    try:
        rp.main(["show", "obs"])
    except SystemExit as e:
        assert "SF_OBS_RUNAS_USER" in str(e)
    else:
        raise AssertionError("rendered without a run-as user")


def test_org_generated_ids_are_never_rendered(monkeypatch):
    for p in rp.POLICIES.values():
        monkeypatch.setenv(p.run_as_env, "u@example.com")
    monkeypatch.setenv("SF_ECA_CONTACT_EMAIL", "c@example.com")
    bodies = [b for p in rp.POLICIES.values() for b in rp.files_for(p).values()]
    # policy + settings each; app file for obs, tab_embed, af_a2a; global set for af_a2a
    assert len(bodies) == 7 * 2 + 3 + 1
    assert not any("orgScopedExternalApp" in b or "oauthLink" in b for b in bodies)


def test_each_app_preauthorizes_its_own_gate_not_the_admin_profile():
    # D86/D90: the profile form admits every admin; the gate admits only the
    # integration user assigned it.
    gates = {k: p.preauth_permission_set for k, p in rp.POLICIES.items()}
    assert gates == {
        "app": "A2ALab_Briefs_Integration",
        "claude": "A2ALab_Agent_Caller",
        "openai": "A2ALab_Agent_Caller",
        "shim": "A2ALab_Agent_Caller",
        "obs": "A2ALab_Obs_Integration",
        "tab_embed": "A2ALab_Tab_Embed",
        "af_a2a": "A2ALab_AF_A2A_Inbound",
    }
    body = rp.render(rp.POLICIES["claude"], "u@example.com")
    assert "<commaSeparatedPermissionSet>A2ALab_Agent_Caller</commaSeparatedPermissionSet>" in body
    assert "commaSeparatedProfile" not in body
    gate_dir = rp.META / "permissionsets"
    assert all((gate_dir / f"{g}.permissionset-meta.xml").exists() for g in gates.values())


def test_scopes_stay_least_privilege():
    assert rp.POLICIES["claude"].scopes == "Chatbot, SFApiPlatform"
    assert rp.POLICIES["obs"].scopes == "Api"


def test_no_rendered_file_is_tracked():
    dirs = [
        str(d.relative_to(ROOT))
        for d in (rp.POLICY_DIR, rp.APP_DIR, rp.SETTINGS_DIR, rp.GLOBAL_DIR)
    ]
    tracked = subprocess.run(
        ["git", "ls-files", *dirs], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert tracked == ""


def test_a2a_pilot_app_issues_jwt_tokens_and_lets_the_org_generate_its_key(monkeypatch):
    # D106: the pilot gateway only accepts a JWT-format access token, and that is the
    # isNamedUserJwtEnabled box: without it the app authenticates and every call
    # fails with no hint (plan/04-runbooks.md). The consumer key is org-generated.
    monkeypatch.setenv("SF_AF_A2A_RUNAS_USER", "agent@example.com")
    monkeypatch.setenv("SF_ECA_CONTACT_EMAIL", "c@example.com")
    files = rp.files_for(rp.POLICIES["af_a2a"])
    (glbl,) = [b for path, b in files.items() if path.parent == rp.GLOBAL_DIR]
    assert "<isNamedUserJwtEnabled>true</isNamedUserJwtEnabled>" in glbl
    assert "<isClientCredentialsFlowEnabled>true</isClientCredentialsFlowEnabled>" in glbl
    assert "consumerKey" not in glbl
    assert rp.POLICIES["af_a2a"].scopes == "Api, RefreshToken, SFApiPlatform"


def test_only_new_apps_render_a_global_oauth_set():
    # The deployed apps' global sets carry their org-generated consumerKey; a render
    # would drop it, so only a not-yet-deployed app renders one.
    assert [k for k, p in rp.POLICIES.items() if p.global_oauth] == ["af_a2a"]


def test_only_the_a2a_pilot_app_carries_a_custom_scope():
    """The pilot gateway checks `scp` for a2a_api_pilot (D106); no other app should
    gain a custom scope by accident, and the element precedes the standard scopes."""
    for key, p in rp.POLICIES.items():
        xml = rp.render_settings(p)
        if key == "af_a2a":
            assert "<commaSeparatedCustomScopes>a2a_api_pilot</commaSeparatedCustomScopes>" in xml
            assert xml.index("CustomScopes") < xml.index("OauthScopes")
        else:
            assert "CustomScopes" not in xml, key
