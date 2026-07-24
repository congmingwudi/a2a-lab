"""F1: the hosted runtimes' Secrets Manager env loader."""

import json
import sys
import types

import pytest

from interop import secret_env


@pytest.fixture(autouse=True)
def _reset_loaded():
    secret_env._loaded = False
    yield
    secret_env._loaded = False


class _FakeSecrets:
    def __init__(self, payload, arn_seen):
        self._payload = payload
        self._arn_seen = arn_seen

    def get_secret_value(self, SecretId):  # noqa: N803 - boto3 signature
        self._arn_seen.append(SecretId)
        return {"SecretString": self._payload}


def _install_fake_boto3(monkeypatch, payload, arn_seen=None):
    module = types.ModuleType("boto3")
    module.client = lambda service: _FakeSecrets(payload, arn_seen if arn_seen is not None else [])
    monkeypatch.setitem(sys.modules, "boto3", module)


def test_no_arn_is_a_noop(monkeypatch):
    # Local development: no AWS call, no import of boto3, nothing set.
    monkeypatch.delenv(secret_env.ARN_VAR, raising=False)
    monkeypatch.setitem(sys.modules, "boto3", None)  # would blow up if imported
    assert secret_env.load_secret_env() == []


def test_loads_keys_into_environ(monkeypatch):
    arn_seen = []
    _install_fake_boto3(
        monkeypatch,
        json.dumps({"ANTHROPIC_API_KEY": "sk-secret", "SF_CLIENT_SECRET": "shh"}),
        arn_seen,
    )
    monkeypatch.setenv(secret_env.ARN_VAR, "arn:aws:secretsmanager:us-east-1:1:secret:x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("SF_CLIENT_SECRET", raising=False)

    keys = secret_env.load_secret_env()

    assert keys == ["ANTHROPIC_API_KEY", "SF_CLIENT_SECRET"]
    assert arn_seen == ["arn:aws:secretsmanager:us-east-1:1:secret:x"]
    import os

    assert os.environ["ANTHROPIC_API_KEY"] == "sk-secret"
    assert os.environ["SF_CLIENT_SECRET"] == "shh"


def test_explicit_env_wins_over_secret(monkeypatch):
    # setdefault semantics: a var already on the runtime config is an
    # override, not something the secret silently replaces.
    _install_fake_boto3(monkeypatch, json.dumps({"OPENAI_API_KEY": "from-secret"}))
    monkeypatch.setenv(secret_env.ARN_VAR, "arn:secret")
    monkeypatch.setenv("OPENAI_API_KEY", "from-runtime-config")

    secret_env.load_secret_env()

    import os

    assert os.environ["OPENAI_API_KEY"] == "from-runtime-config"


def test_fetch_happens_once(monkeypatch):
    arn_seen = []
    _install_fake_boto3(monkeypatch, json.dumps({"A": "1"}), arn_seen)
    monkeypatch.setenv(secret_env.ARN_VAR, "arn:secret")

    secret_env.load_secret_env()
    secret_env.load_secret_env()

    assert arn_seen == ["arn:secret"]


def test_non_object_secret_is_rejected(monkeypatch):
    _install_fake_boto3(monkeypatch, json.dumps(["not", "a", "mapping"]))
    monkeypatch.setenv(secret_env.ARN_VAR, "arn:secret")

    with pytest.raises(ValueError, match="JSON object"):
        secret_env.load_secret_env()


def test_fetch_failure_raises(monkeypatch):
    # A hosted runtime must refuse to boot rather than start credential-less.
    module = types.ModuleType("boto3")

    class _Boom:
        def get_secret_value(self, SecretId):  # noqa: N803 - boto3 signature
            raise RuntimeError("AccessDeniedException")

    module.client = lambda service: _Boom()
    monkeypatch.setitem(sys.modules, "boto3", module)
    monkeypatch.setenv(secret_env.ARN_VAR, "arn:secret")

    with pytest.raises(RuntimeError, match="AccessDenied"):
        secret_env.load_secret_env()


def test_explicit_arn_argument_overrides_env(monkeypatch):
    arn_seen = []
    _install_fake_boto3(monkeypatch, json.dumps({"A": "1"}), arn_seen)
    monkeypatch.delenv(secret_env.ARN_VAR, raising=False)

    secret_env.load_secret_env("arn:explicit")

    assert arn_seen == ["arn:explicit"]
