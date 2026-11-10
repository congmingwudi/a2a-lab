"""scripts/publish_agent.py: an agent user named by placeholder (D106, WS27 item 24).

What is pinned: placeholders render from env, a missing key fails loudly, and the
restore puts the placeholder back everywhere the value landed, including files
`sf agent publish` retrieved that never held a placeholder.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import publish_agent as pa  # noqa: E402


@pytest.fixture
def project(tmp_path, monkeypatch):
    default = tmp_path / "default"
    (default / "aiAuthoringBundles" / "Ag").mkdir(parents=True)
    (default / "bots" / "Ag").mkdir(parents=True)
    agent = default / "aiAuthoringBundles" / "Ag" / "Ag.agent"
    agent.write_text('    default_agent_user: "{{env:SF_X_RUNAS_USER}}"\n')
    bot = default / "bots" / "Ag" / "Ag.bot-meta.xml"
    bot.write_text("<botUser>{{env:SF_X_RUNAS_USER}}</botUser>\n")
    monkeypatch.setattr(pa, "DEFAULT", default)
    monkeypatch.setattr(pa, "ROOT", tmp_path)
    return default, agent, bot


def test_render_then_restore_round_trips(project):
    default, agent, bot = project
    files = pa.bundle_files("Ag")
    assert files == [agent, bot]
    used = pa.render(files, {"SF_X_RUNAS_USER": "agent@lab.example"})
    assert '"agent@lab.example"' in agent.read_text()
    # what publish's retrieve writes: the real value, in a file with no placeholder
    planner = default / "genAiPlannerBundles" / "Ag_v2" / "Ag_v2.genAiPlannerBundle"
    planner.parent.mkdir(parents=True)
    planner.write_text("<user>agent@lab.example</user>\n")
    planner.rename(planner.with_suffix(".xml"))
    pa.restore(used)
    assert "{{env:SF_X_RUNAS_USER}}" in agent.read_text()
    assert "{{env:SF_X_RUNAS_USER}}" in bot.read_text()
    assert "lab.example" not in planner.with_suffix(".xml").read_text()


def test_a_missing_env_key_fails_before_anything_publishes(project):
    with pytest.raises(SystemExit, match="SF_X_RUNAS_USER"):
        pa.render(pa.bundle_files("Ag"), {})


def test_restore_leaves_files_that_held_the_value_before_the_run(project):
    """The rendered ECA policy names the run-as for real (gitignored); restoring a
    placeholder into it would deploy `{{env:...}}` as the client-credentials user."""
    default, agent, _ = project
    policy = default / "extlClntAppOauthPolicies" / "app_oauthPlcy.ecaOauthPlcy-meta.xml"
    policy.parent.mkdir(parents=True)
    policy.write_text("<clientCredentialsFlowUser>agent@lab.example</clientCredentialsFlowUser>")
    keep = pa.holding({"agent@lab.example"})
    used = pa.render(pa.bundle_files("Ag"), {"SF_X_RUNAS_USER": "agent@lab.example"})
    pa.restore(used, keep)
    assert "agent@lab.example" in policy.read_text()
    assert "{{env:SF_X_RUNAS_USER}}" in agent.read_text()
