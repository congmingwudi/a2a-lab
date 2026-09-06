"""WS25 A3 (D80/F06): a hosted entry point with NO auth token must refuse to
start, not start open. Locally the open mode stays available, but only behind
an explicit A2ALAB_ALLOW_UNAUTH=1.

"Hosted" is the shape the deploy scripts create: credentials arrive through
A2ALAB_RUNTIME_SECRET_ARN (D39) or the process runs with A2ALAB_MODE=hosted.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("A2ALAB_RUNTIME_SECRET_ARN", "A2ALAB_MODE", "A2ALAB_ALLOW_UNAUTH", "BRIDGE_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


def test_hosted_with_empty_token_refuses_to_start(clean_env):
    from interop.secret_env import require_token

    clean_env.setenv("A2ALAB_RUNTIME_SECRET_ARN", "arn:aws:secretsmanager:region:acct:secret:x")
    with pytest.raises(SystemExit) as exc:
        require_token("bridge", "BRIDGE_TOKEN")
    assert "BRIDGE_TOKEN" in str(exc.value)


def test_hosted_mode_flag_alone_also_refuses(clean_env):
    from interop.secret_env import require_token

    clean_env.setenv("A2ALAB_MODE", "hosted")
    with pytest.raises(SystemExit):
        require_token("bridge", "BRIDGE_TOKEN")


def test_hosted_with_token_starts(clean_env):
    from interop.secret_env import require_token

    clean_env.setenv("A2ALAB_RUNTIME_SECRET_ARN", "arn:aws:secretsmanager:region:acct:secret:x")
    clean_env.setenv("BRIDGE_TOKEN", "sekrit")
    assert require_token("bridge", "BRIDGE_TOKEN") == "sekrit"


def test_hosted_open_mode_needs_explicit_opt_in(clean_env):
    from interop.secret_env import require_token

    clean_env.setenv("A2ALAB_RUNTIME_SECRET_ARN", "arn:aws:secretsmanager:region:acct:secret:x")
    clean_env.setenv("A2ALAB_ALLOW_UNAUTH", "1")
    assert require_token("bridge", "BRIDGE_TOKEN") is None


def test_local_with_empty_token_stays_open(clean_env):
    from interop.secret_env import require_token

    assert require_token("bridge", "BRIDGE_TOKEN") is None


def test_bridge_main_exits_before_serving_when_hosted_and_tokenless(clean_env):
    import uvicorn

    from bridge import app as bridge_app

    clean_env.setenv("A2ALAB_RUNTIME_SECRET_ARN", "arn:aws:secretsmanager:region:acct:secret:x")
    clean_env.setattr("sys.argv", ["bridge"])

    def _never(*_a, **_k):
        raise AssertionError("uvicorn.run reached with no BRIDGE_TOKEN")

    clean_env.setattr(uvicorn, "run", _never)
    # load_secret_env would call AWS for the ARN; the guard must not depend on it succeeding
    clean_env.setattr(bridge_app, "load_secret_env_and_log", lambda _s: None, raising=False)
    with pytest.raises(SystemExit):
        bridge_app.main()


def test_mcp_lambda_handler_refuses_tokenless_hosted_start(clean_env):
    from mcp_http import ToolRegistry, make_lambda_handler

    clean_env.setenv("A2ALAB_RUNTIME_SECRET_ARN", "arn:aws:secretsmanager:region:acct:secret:x")
    with pytest.raises(SystemExit):
        make_lambda_handler(ToolRegistry(), None)


def test_mcp_lambda_handler_local_tokenless_still_builds(clean_env):
    from mcp_http import ToolRegistry, make_lambda_handler

    assert callable(make_lambda_handler(ToolRegistry(), None))


# ---- the deploy-script half: a token a script SHIPS is a token it REQUIRES ----

_SHIP_RE = re.compile(r"\b([A-Z0-9_]+_TOKEN)\b")


def _deploy_scripts():
    return sorted(ROOT.glob("deploy/*/deploy_*.sh"))


@pytest.mark.parametrize("script", _deploy_scripts(), ids=lambda p: p.parent.name)
def test_deploy_script_requires_every_token_it_ships(script: Path):
    """Every *_TOKEN key a deploy script writes into a secret or task env must
    be guarded by `${KEY:?…}` earlier in the same script, so a missing token
    fails the deploy instead of shipping a silently open service (D80/F06)."""
    text = script.read_text()
    shipped = set()
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # keys named inside python/shell list literals of env vars to ship
        if re.search(r'"[A-Z0-9_]+_TOKEN"', stripped):
            shipped.update(_SHIP_RE.findall(stripped))
    shipped -= {"AWS_SESSION_TOKEN"}  # a credential the script READS, never ships
    if not shipped:
        pytest.skip("ships no token")
    unguarded = sorted(k for k in shipped if f"${{{k}:?" not in text)
    assert not unguarded, f"{script}: ships {unguarded} without a ${{KEY:?}} guard"
