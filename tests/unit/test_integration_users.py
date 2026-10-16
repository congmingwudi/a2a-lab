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


def test_every_app_runs_as_a_spec_user_holding_its_gate():
    by_env = {u.env_key: u for u in iu.USERS.values()}
    for p in rp.POLICIES.values():
        user = by_env[p.run_as_env]
        assert user.bundle[-1] == (None, p.preauth_permission_set), p.name


def test_only_the_embed_user_has_a_ui_license():
    ui = {k for k, u in iu.USERS.items() if u.license != iu.INTEGRATION[0]}
    assert ui == {"tab_embed"}


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
