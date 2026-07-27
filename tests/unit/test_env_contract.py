"""`.env.example` is the contract; `.env` holds the values. Same keys, both files.

This is the cheap half of "do the environment variables actually work" — it
needs no credentials, runs in milliseconds, and catches the failure that
silently degrades every other guarantee: **a variable the code reads that
nothing documents.** When it was first written the repo had 46 of them, so
`.env.example` described about two-thirds of the configuration while the README
called it "the contract that names every key".

The live half — do these values authenticate, and when do they expire — is
`scripts/identity_preflight.py` and `scripts/expiry_report.py`, which need real
credentials and therefore are commands rather than tests.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("env_audit", REPO / "scripts" / "env_audit.py")
env_audit = importlib.util.module_from_spec(spec)
sys.modules["env_audit"] = env_audit
spec.loader.exec_module(env_audit)


def test_every_variable_the_code_reads_is_documented():
    """A var read by code and absent from .env.example is invisible to the next
    person: they pull the secret, get a working value, and have no idea what it
    is for — or they add a var and never document it, because nothing asked."""
    report = env_audit.audit(REPO)
    assert report["undocumented"] == [], (
        "these are read by code but not documented in .env.example:\n  "
        + "\n  ".join(
            f"{n}  <- {(report['refs'].get(n) or ['?'])[0]}" for n in report["undocumented"]
        )
    )


def test_env_and_example_carry_the_same_keys():
    """The parity that makes drift visible. `.env.example` may comment a key out
    (an optional knob); `.env` may too. What must not happen is one file knowing
    about a key the other has never heard of."""
    example = set(env_audit.keys_in(REPO / ".env.example"))
    env = set(env_audit.keys_in(REPO / ".env"))
    if not env:
        import pytest

        pytest.skip("no local .env — nothing to compare (a fresh checkout)")
    assert env == example, (
        f"only in .env: {sorted(env - example)}\nonly in .env.example: {sorted(example - env)}"
    )


def test_no_variable_is_set_but_unreferenced():
    """Dead configuration is a trap: it reads as required, so nobody removes it,
    and it gets copied into every new environment forever.

    Deliberately uses the LOOSE reference test — a false 'unused' is the
    expensive answer, since acting on it deletes a working variable. Anything
    flagged here should be verified by hand and then commented out (with its
    value and a reason) rather than deleted.
    """
    report = env_audit.audit(REPO)
    assert report["possibly_unused"] == [], (
        "set in .env but referenced nowhere in the working tree:\n  "
        + "\n  ".join(report["possibly_unused"])
        + "\nVerify by hand, then comment out with a note — do not delete."
    )


def test_audit_ignores_things_that_are_not_lab_configuration():
    """Guards the scanner's own judgment calls, each of which was a false
    positive that made the report unusable until it was excluded."""
    refs = env_audit.referenced(REPO)
    assert "PATH" not in refs, "ambient shell variables are not lab config"
    assert "VAR" not in refs, "documentation placeholders are not lab config"
    assert not any(f.startswith(".agents/") for files in refs.values() for f in files), (
        "vendored agent skills carry their own env vars; they are not this lab's"
    )
