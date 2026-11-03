"""The per-seam integration users (scripts/integration_users.py, D86/D90).

What is pinned: every app's run-as key and gate line up with a user spec, each
bundle ends with its gate, licenses come before the permission sets that need
them, and the planner only asks for what a user is missing (so --apply is
idempotent).
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import integration_users as iu  # noqa: E402
import render_eca_metadata as rp  # noqa: E402
import setup_jwt_eca as jwt_eca  # noqa: E402


def test_every_app_runs_as_a_spec_user_holding_its_gate():
    by_env = {u.env_key: u for u in iu.USERS.values()}
    for p in rp.POLICIES.values():
        user = by_env[p.run_as_env]
        assert user.bundle[-1] == (None, p.preauth_permission_set), p.name


def test_every_jwt_bearer_app_runs_as_a_spec_user_holding_its_gate():
    by_env = {u.env_key: u for u in iu.USERS.values()}
    for p in jwt_eca.PRESETS.values():
        assert by_env[p.run_as_env].bundle[-1] == (None, p.preauth_permission_set), p.name


def test_only_the_embed_employee_and_agent_users_leave_the_integration_license():
    """The embed needs a UI session; an Employee agent needs a session user holding
    EinsteinCopilotUser, which the Integration licence refuses (D95); the A2A pilot's
    caller runs as a Service agent's Digital Agent User (D106). Nothing else."""
    ui = {k for k, u in iu.USERS.items() if u.license != iu.INTEGRATION[0]}
    assert ui == {"tab_embed", "employee", "af_a2a_agent"}


def test_the_embed_user_can_run_the_dashboards_queries():
    """Tableau Next Consumer alone renders "We couldn't display this content":
    the Data 360 engine runs the dashboard's queries as the viewer, so the viewer
    needs Data Cloud User too (plan/13 §5, measured again 2026-10-16)."""
    held = {ps for _, ps in iu.USERS["tab_embed"].bundle}
    assert {"TableauUser", "GenieUserEnhancedSecurity"} <= held


def test_the_embed_user_can_open_the_dashboard_tab():
    """The console's "Open the live dashboard" link lands in the browser session
    the embed's frontdoor left behind, which is the embed user, so that user needs
    the tab or Salesforce reports the page as missing (measured 2026-10-16)."""
    held = {ps for _, ps in iu.USERS["tab_embed"].bundle}
    assert "A2A_Lab_Access" in held


def test_plan_assigns_license_before_its_permission_set_and_skips_what_is_held():
    spec = iu.USERS["briefs"]
    steps = iu.plan_steps(spec, "b@example.com", False, set(), set())
    assert [s[2] + ":" + s[4] for s in steps] == [
        "permsetlicense:SalesforceAPIIntegrationPsl",
        "permset:A2ALab_Brief_Writer",
        "permset:A2ALab_Briefs_Integration",
    ]
    held = iu.plan_steps(
        spec, "b@example.com", True, {"SalesforceAPIIntegrationPsl"},
        {"A2ALab_Brief_Writer", "A2ALab_Briefs_Integration"},
    )  # fmt: skip
    assert held == []


def test_default_username_takes_the_operators_domain():
    assert iu.default_username(iu.USERS["obs"], "admin@lab.example") == "a2alab.obs@lab.example"


def test_aliases_fit_salesforce_limit():
    assert all(len(u.alias) <= 8 for u in iu.USERS.values())


def test_plan_assigns_groups_after_the_bundle_and_skips_held_ones():
    """A permission set group cannot go through `sf org assign permset` (D106), and
    assigned() reports it under its own name, so a held group plans nothing."""
    spec = iu.USERS["af_a2a_agent"]
    steps = iu.plan_steps(spec, "a@example.com", False, set(), set())
    assert steps[-1] == ("group", "AgentforceServiceAgentUserPsg", "a@example.com")
    assert [s[4] for s in steps if s[0] == "org"][:3] == [
        "GenieDataPlatformStarterPsl", "EinsteinGPTPromptTemplatesPsl",
        "AgentforceServiceAgentUserPsl",
    ]  # fmt: skip
    held = {ps for _, ps in spec.bundle if ps} | set(spec.groups)
    psls = {psl for psl, _ in spec.bundle if psl}
    assert iu.plan_steps(spec, "a@example.com", True, psls, held) == []


def test_the_a2a_pilot_agent_user_is_named_by_exactly_one_agent():
    """The pilot runs every inbound caller as the agent's user, so that user must
    serve one agent only (D106; the guide's "Limitation 2"). Bundles name it by
    placeholder, which scripts/publish_agent.py renders from .env (D88)."""
    bundles = ROOT / "salesforce/force-app/main/default/aiAuthoringBundles"
    ref = "{{env:" + iu.USERS["af_a2a_agent"].env_key + "}}"
    users = [f.parent.name for f in bundles.glob("*/*.agent") if ref in f.read_text()]
    assert users == ["A2ALab_Research_Assistant_Script"]
