# WS25 batch 4 — shim session key: close the platform/subject collision (A11 follow-up)

Proposer: claude · Critic: codex · Raised 2026-10-18 · Follows D81 (ws25-b3, deployed 2026-10-06)

## The defect

ws25-b3 (A11) keys the hosted shim's reused Agentforce session by
`(platform, verified subject)`. The key is built by string concatenation
(`src/platforms/agentforce/proxy.py#L73-L75`):

```python
subject = (req.metadata or {}).get("verified_subject")
base = f"shim-shared-{platform or 'direct'}"
req.session_id = f"{base}-{subject}" if subject else base
```

`subject` is trusted: `AdapterExecutor.execute` overwrites or drops it from the
verified JWT (`src/interop/servers/a2a.py#L85-L93`). `platform` is **not**:
`delegation.platform_of()` (`src/interop/delegation.py#L78-L90`) returns the
caller's own `metadata["delegation"]["platform"]` verbatim, or the rider-text
match `caller-platform:\s*([\w.-]+)` (`#L34`), which admits `-`.

So the joiner `-` can be supplied from the untrusted half:

| Caller | platform | verified subject | key |
|---|---|---|---|
| alice (JWT) | `foundry` | `alice` | `shim-shared-foundry-alice` |
| anyone with the legacy shared token | `foundry-alice` | — | `shim-shared-foundry-alice` |

The second caller lands in alice's cached Agentforce session — the exact
cross-caller sharing A11/F02 set out to remove. Between two verified callers
the collision also exists but is narrower (`platform="a-b"`, subject `c` vs
`platform="a"`, subject `b-c`): it requires one subject to be a `-`-suffix of
another's.

The existing suite does not catch it: every test in
`tests/unit/test_shim_session_isolation.py` uses `platform=None` ("direct"),
and `test_no_verified_subject_takes_the_platform_only_fallback` (L126) asserts
the *intent* ("not on some other caller's per-subject session") only for that
one platform value. Raised by the push security review of `f55e9bf`, confirmed
by reading the source.

## Fix — one item, one file

**File:** `src/platforms/agentforce/proxy.py` (the key construction only).

Make the key injective in `(kind, platform, subject)`: separate namespaces for
the verified and the shared case, and percent-encode each component so no
component can contain the separator.

```python
from urllib.parse import quote

def _shim_session_key(platform: str | None, subject: str | None) -> str:
    p = quote(platform or "direct", safe="")
    if subject:
        return f"shim:sub:{quote(subject, safe='')}:{p}"
    return f"shim:shared:{p}"
```

- `quote(..., safe="")` encodes `:` (and `/`), so a component never contains
  the separator, and the `sub`/`shared` tag means a shared-token key can never
  equal a verified key whatever the platform string. Subject goes first so the
  verified namespace is partitioned by identity before the untrusted field.
- Not a hash: the key stays readable in traces and in `AgentforceClient`'s
  session cache, and injectivity is provable by inspection, not by collision
  odds.
- Behaviour otherwise unchanged: same override-in-reuse-mode, same fallback
  for the legacy shared token, same twin routing (which reads `platform`
  separately, `proxy.py#L63`).

**Not in scope** (named so the critic can push back if it disagrees):
- Validating `platform` against a whitelist. It would also close this, but the
  platform value feeds twin routing and the D27 rider grammar; constraining it
  is a contract change across every delegation seam. Encoding the key is local
  and sufficient for isolation.
- Retiring the legacy shared token at the shim. The real reduction in exposure,
  and the operator's call (who still uses it) — noted under Deploy.

## Tests (written first, red against `main` at `f55e9bf`)

In `tests/unit/test_shim_session_isolation.py`:

1. `test_shared_token_cannot_forge_a_subject_key_via_platform` — run alice
   (JWT, `platform="foundry"`) and a shared-token caller with
   `metadata.delegation.platform="foundry-alice"`; assert the two session keys
   differ. **Red today** (both are `shim-shared-foundry-alice`).
2. `test_rider_text_platform_cannot_forge_a_subject_key` — same, with the
   platform delivered only via rider text (`caller-platform: foundry-alice`),
   since `platform_of` falls back to the message scan.
3. `test_verified_subjects_cannot_collide_across_platforms` — subject `c` on
   `platform="a-b"` vs subject `b-c` on `platform="a"`; assert distinct.
   **Red today.**
4. `test_separator_in_platform_is_encoded` — `platform="x:sub:alice"` on the
   shared token does not equal alice's key.

Existing assertions on literal keys (`shim-shared-direct-alice`, …, 13 sites in
that file plus `tests/unit/test_af_proxy.py#L71`) change to the new literal
form — the **property** each asserts (two subjects → two keys, same subject →
one key, unverified → shared key, asserted body subject ignored) is kept, not
weakened. Codex's `readiness_probes.py#L113` asserts the pre-b3 behaviour and
already went red at b3; it stays as the historical reproduction.

`uv run pytest -q` and `uv run ruff check .` green. Baseline on this machine
(2026-10-18, `f55e9bf`): 754 passed, 2 failed, 5 skipped — both failures are
`FileExistsError: '.a2alab'` (the chezmoi-managed secrets dir is absent on this
machine; environment, not code) in `test_oauth_token_endpoint` and
`test_openai_agents_backend`.

## Deploy (operator, work machine — this machine has no AWS access)

1. `git pull` on the work machine; merge `ws25-b4-shim-session-key` after the
   cross-review PASS.
2. `deploy/shim/build_zip.sh && deploy/shim/deploy_shim.sh` (full code
   deploy — the Lambda bundle carries `src/`).
3. Smoke: two JWT callers on the same platform get two Agentforce sessions; a
   shared-token call with `platform="<p>-<subject>"` does **not** continue the
   verified caller's conversation (ask alice's session a recall question from
   the forged call and expect no recall). Record in
   `build-notes/claude/reviews/ws25-b4.md`.
4. Side effect: every cached shim session key changes, so in-flight shim
   conversations start a fresh Agentforce session once. The cache is
   per-Lambda-instance and in-memory; nothing persists the old keys.
5. Interim, operator's call: until step 2 runs, the exposure is open to any
   holder of the legacy shared token. Rotating it, or refusing it at the shim
   if nothing still depends on it, closes the exposure without a code deploy.

## Docs touched on implement

- `plan/07-workstreams.md` WS25: one `N. ⏳` item line for this batch.
- `plan/00-decisions.md`: the settling ADR (operator).
- No diagram or console copy names the key format (`git grep shim-shared` hits
  only tests and build-notes), so no architecture-sweep change.
