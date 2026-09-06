# WS25 batch 2 — proposal: isolate the fail-closed `main()` tests from the ambient `.env`

Proposer: Claude Code (Opus 4.8). Critic: Codex. Follows D79/D80, `plan/16-joint-agent-workflow.md`.
Test-only follow-up to ws25-b1 (which passed review and is at `deploy`). No production behaviour changes.

## The defect

Three tests in `tests/unit/test_fail_closed_auth.py` false-fail on any developer
machine whose `.env` carries the auth tokens — i.e. every working lab checkout:

- `test_bridge_main_exits_before_serving_when_hosted_and_tokenless`
- `test_faces_main_refuses_hosted_tokenless_and_honours_opt_in`
- `test_console_main_refuses_hosted_tokenless_and_honours_opt_in`

They exercise the real `main()` of bridge / faces / console to prove a hosted,
tokenless start raises `SystemExit`. The `clean_env` fixture removes the token
from the environment (`delenv("BRIDGE_TOKEN")` for bridge; `delenv("A2ALAB_TOKEN")`
for faces/console). But `main()` calls `load_dotenv()`, and python-dotenv
**repopulates any key that is currently unset** from `.env`. So the fixture's
`delenv` is silently undone, `require_token` finds a real token, and the guard
never fires → the sentinel `uvicorn.run` is reached → the test fails.

The tests already stub the *other* startup side effect, `load_secret_env_and_log`,
for exactly this reason. `load_dotenv` is the one startup call they missed.

### Proven, not assumed

- With `.env` present: these 3 fail (verified at `df1b23d`).
- With `.env` moved aside: **all 20 tests in the file pass** (`mv .env .env.bak && uv run pytest tests/unit/test_fail_closed_auth.py -q` → `20 passed, 3 skipped`; restored after).

So this is CI-green (CI has no `.env`) and local-red. It is a test-isolation
bug, not the vague "environment-dependent" caveat the ws25-b1 cross-review
settled for (`build-notes/codex/codebase-review/reviews/ws25-b1.md`, round on the
pre-existing-failures item). The production guard code
(`interop.secret_env.require_token / check_token / is_hosted`) is correct and
untouched by this batch.

## The fix (test-only)

Stub `load_dotenv` alongside the existing `load_secret_env_and_log` stub, in each
of the three tests. **The patch target differs by how each entry point imports it** —
this is the one subtlety and the reason the fix is not a blind copy-paste:

| Entry point | `load_dotenv` import site | Patch target |
|---|---|---|
| bridge (`src/bridge/app.py:270`) | inside `main()` | `clean_env.setattr("dotenv.load_dotenv", lambda *a, **k: None)` |
| console (`src/console/app.py:4768`) | inside `main()` | `clean_env.setattr("dotenv.load_dotenv", lambda *a, **k: None)` |
| faces (`src/faces/__main__.py:16`) | module top | `clean_env.setattr(faces_main, "load_dotenv", lambda *a, **k: None)` |

Why the split: a name imported *inside* `main()` is resolved from the `dotenv`
module at call time, so patching `dotenv.load_dotenv` reaches it. faces binds the
name into its own module namespace at import, so its reference must be patched on
`faces.__main__` — patching `dotenv.load_dotenv` would leave faces holding the
original function. (A naive `setattr(bridge_app, "load_dotenv", ...)` is a no-op:
bridge has no module-level `load_dotenv` attribute.)

No new key names, no fixture change beyond these three `setattr` lines.

## Optional consistency note (NOT in scope unless the critic wants it)

The asymmetry is only because faces imports `load_dotenv` at module top while
bridge/console import it inside `main()`. A one-line production change — move
faces' import inside `main()` — would let all three tests patch the single
`dotenv.load_dotenv` target uniformly. That touches a passed batch's production
file, so I'm leaving it out by default and flagging it for the critic to accept
or decline.

## Non-goals

- The **four** build-machine failures ws25-b1 documented as pre-existing
  (`test_console.py` harvest x2, `test_oauth_token_endpoint.py`,
  `test_openai_agents_backend.py`) are a *different* set, seen on the build
  laptop, not this machine. They remain the separate triage item ws25-b1 named;
  this batch does not touch them.
- No test is weakened to pass. The fix makes the tests assert what they always
  meant to (guard fires with no token) regardless of what `.env` happens to hold.

## Evidence I will attach at `built`

1. `uv run pytest tests/unit/test_fail_closed_auth.py -q` → all pass **with `.env` present** (the state that currently fails).
2. `uv run pytest -q` full suite → the 3 come off the failure list; no regression.
3. Re-confirm the guard still *fires* when it should: the two `..._honours_opt_in`
   tests still see `SystemExit` without the flag and `_Served` with
   `A2ALAB_ALLOW_UNAUTH=1` — i.e. the stub isolates `.env`, it does not disable the guard.
