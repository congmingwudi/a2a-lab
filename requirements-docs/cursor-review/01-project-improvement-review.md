# Project Improvement Review

Cross-cutting review of the A2A Interop Lab repository: setup, code, plan
documentation, and operations. Produced from a Cursor session exploration on
2026-07-31 (baseline commit `51733d8`).

This is a **research lab**, not a prototype. The architecture is clear, honesty
about platform limits is rare, and the documentation culture (ADRs, workstreams,
deployment map) is stronger than most production systems. The items below are
improvement opportunities, not design flaws.

---

## What is already working well

### Two-seam architecture

`AgentAdapter` inbound and `RemoteAgentClient` outbound, both speaking
`AgentRequest`/`AgentResponse`, with `Registry` + `config/targets.yaml`
driving resolution — the right abstraction for adding platform #6 without
touching `interop/`. The loopback e2e suite against an `EchoAdapter` is the
correct proof layer (`tests/e2e/test_loopback.py`).

### Honesty as a feature

`plan/02-matrix.md` with `native / via-bridge / via-shim / blocked-beta`,
declined experiments with reasoning, and insights tagged
`measured / observed / hypothesis` — this is what makes the lab credible. Most
interop demos hide the shim.

### Test discipline

403 tests collected (401 passing locally with `-m 'not live'`), env contract
tests, account-ID guard tests, deploy preflight enforcement — thoughtful
guardrails, not checkbox coverage.

### Operational maturity

Secrets Manager sync (`scripts/env_sync.py`), identity preflight
(`scripts/identity_preflight.py`), Dockerfile comments explaining *why* each
COPY exists, ADRs recording failure modes (D46 built-but-not-deployed,
`--skip-build` trap) — the learning tax is paid and written down.

---

## Setup and tooling (highest ROI)

### 1. Add CI — the biggest gap

There is no `.github/workflows/`. Quality gates are entirely local. At review
time the suite reported **401 passed, 2 failed** — both in
`tests/unit/test_env_contract.py` because local `.env` has 33 keys vs 168 in
`.env.example`, and `TUNNEL_HOSTNAME` is set but deprecated. Those failures are
environment-specific, which is exactly why CI matters.

**Recommendation:** minimal GitHub Actions workflow — `uv sync --all-extras`,
`ruff check`, `pytest -m 'not live'` on every PR.

### 2. Soften the env parity test for partial `.env` files

`test_env_and_example_carry_the_same_keys` requires **exact equality** between
`.env` and `.env.example`. Good for catching drift when fully configured, but
breaks fresh/partial checkouts and makes the suite feel broken when it is not.

**Recommendation:** assert `.env` keys ⊆ `.env.example` (every local key is
documented); optionally a separate strict mode for operators who want full parity.

### 3. Add coverage on the seams, not the whole repo

No `pytest-cov` today.

**Recommendation:** start narrow — `src/interop/` (models, delegation, trace,
clients) and `src/bridge/`. Console and harvest lambdas are harder to measure
meaningfully.

### 4. Pre-commit hooks

Ruff passes cleanly, but nothing enforces it on commit.

**Recommendation:** `.pre-commit-config.yaml` with `ruff check` + `ruff format`.

### 5. Onboarding ladder

The README is thorough (~810 lines) but lacks a **tiered path**:

| Tier | Who | What they need |
|---|---|---|
| Contributor | PR author | `uv sync` + loopback pytest |
| Operator | Hosted console | `.env` from Secrets Manager + `scripts/run_console.sh` |
| Full stack | Adapter work | `scripts/run_local.sh` + 5 clouds + Salesforce prod org |

**Recommendation:** one-page "start here" at the top of `README.md` or
`docs/onboarding.md` with those three paths.

---

## Code

### 1. Console monolith — main maintainability risk

| File | Lines (approx.) |
|---|---|
| `src/console/static/index.html` | ~8,133 |
| `src/console/app.py` | ~3,610 |

Backend API tests in `tests/unit/test_console.py` are solid (~69 tests), but
the frontend is essentially untested beyond render assertions. Every new section
(D57 canvas template, Details panes, chip linkification) adds weight to a
single file.

**Incremental split:**

- **Backend:** `console/routes/traces.py`, `routes/obs.py`,
  `routes/experiments.py`, etc. — imports in `app.py` already hint at natural
  boundaries (`reviews`, `insights`).
- **Frontend:** extract JS modules one section at a time (Observability,
  Architecture, Control Panel) into `static/js/` — no framework required.

**Rule:** new console features do not add 200 lines to `index.html` without
extracting something first.

### 2. Type checking on `src/interop/` first

Dataclasses and `Protocol` are used well, but there is no mypy/pyright. The
interop layer is small (~15 files), stable, and high-value — a good first target.

### 3. Thin-test the blind spots

Under-tested relative to importance:

- `src/interop/cloud_auth.py` — multi-cloud auth resolution
- `src/interop/af_channel.py` — A2A channel to Agentforce shim
- `src/observability/promql.py` — query building for metrics

A few focused unit tests each would catch regressions in the hosted harvest path.

### 4. Package naming

`pyproject.toml` says `name = "rc-a2a"` while the repo is `a2a-lab`. Minor,
but confusing for anyone installing or reading traces. Rename or document why.

### 5. Broad `except Exception` in I/O paths

~35 instances across console, observability harvesters, and platform backends.
Pragmatic for multi-cloud I/O, but consider logging with structured context
(platform, identity, trace_id) so hosted failures are diagnosable without SSH.

---

## Plan and documentation

### 1. Navigation, not content, is the problem

Corpora:

- `plan/` — ADRs, architecture, workstreams (source of truth)
- `requirements-docs/` — formal requirements, traceability matrix
- `build-notes/` — session notes
- `docs/` — two files

Each serves a purpose, but a newcomer will not know which to read.

**Recommendation:** **doc map** (even 20 lines at the top of `README.md`):

```
plan/00-decisions.md     → why we chose X
plan/07-workstreams.md   → what's built, what's next
requirements-docs/       → formal REQ-* traceability (separate corpus)
build-notes/             → how specific things were built (not policy)
```

### 2. Open workstreams — finish or explicitly defer

From `plan/07-workstreams.md` status as of late July 2026:

| WS | State |
|---|---|
| WS11 | Client half measured (D47); fan-out server's submit/check tools remain |
| WS13 | Phases 0–3 done; Phases 4 (traces, beta) and 5 open |
| WS10 | Agent Fabric comparison — still open |

**Recommendation:** single **"current sprint" block** at the top of
`07-workstreams.md` — "these three things are active; everything else is done
or deferred." The file is ~2,500 lines and status paragraphs are scattered.

### 3. Streaming advertised but not exercised

Servers advertise `AgentCapabilities(streaming=True)`; clients hard-code
`streaming=False`. D11 scoped an A2A SSE demo that was never built.

**Recommendation:** build the minimal demo and record in `plan/03-results.md`,
or set `streaming=False` in server capabilities until real.

### 4. `requirements-docs/` drift risk

The formal requirements corpus is valuable, but it is a second tree that can
diverge from `plan/`. `scripts/jira_sync.py` is one-way plan → Jira.

**Recommendation:** similar plan → requirements check, or a note in
`requirements-docs/90-traceability/` saying "last verified against plan commit
X". See also `requirements-docs/codex-review/` for the standards-compliant vs
as-built tension.

---

## Operations and risk

Documented failure modes worth keeping on the radar:

1. **`--skip-build` deploy trap** — config-only deploys can ship stale `src/` in
   the image (CLAUDE.md, three debugging rounds). Deploy scripts could warn when
   `--skip-build` is used and `src/` or `config/` changed since the last image tag.

2. **Auth off when tokens unset** — fine for localhost, dangerous if a tunnel is
   up. `scripts/run_local.sh` could warn when `A2ALAB_TOKEN` is unset and any
   face is bound to `0.0.0.0`.

3. **Salesforce deploys to prod** — Apex coverage and Agent Script publish are
   documented; a pre-deploy checklist in `plan/10-operations.md` for
   org-affecting changes would help.

4. **Dockerfile COPY contract** — console Dockerfile is well commented (including
   `scripts/` for `jira_sync`). Consider a test that greps console route handlers
   for `import`/`open()` paths and asserts each is in the Dockerfile COPY list.

---

## What not to change

- **Do not flatten the platform plugin model** — it is working.
- **Do not merge `plan/` and `requirements-docs/`** — different audiences; link them.
- **Do not add a frontend framework yet** — incremental JS extraction is enough.
- **Do not relax the honest matrix** — it is the lab's differentiator.
- **Do not chase 100% test coverage** — focus on seams, env contracts, deploy preflight.

---

## Suggested priority order

If picking a week's worth of improvements:

1. **GitHub Actions** — ruff + pytest on every PR
2. **Fix/split env parity test** — so local dev is not red by default
3. **Doc map + onboarding tiers** in README
4. **Extract one console route module** — prove the split pattern
5. **pytest-cov on `src/interop/`** with a modest threshold
6. **WS11/WS13 status block** at top of workstreams

---

## Summary

Weaknesses are mostly **scale and tooling** (console size, no CI, onboarding
friction), not design. The interop seams, trace layer, delegation guard, and
honest matrix are the right foundations; the items above make it easier to
extend without the monolith and env wall getting in the way.
