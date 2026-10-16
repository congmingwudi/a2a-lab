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
    assert len(bodies) == 6 * 2 + 2  # policy + settings each; app file for obs, tab_embed
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
    dirs = [str(d.relative_to(ROOT)) for d in (rp.POLICY_DIR, rp.APP_DIR, rp.SETTINGS_DIR)]
    tracked = subprocess.run(
        ["git", "ls-files", *dirs], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    assert tracked == ""
