# WS25 batch 4 — shim session key: close the platform/subject collision (A11 follow-up)

Proposer: claude · Critic: codex · Raised 2026-10-18 · Follows D81 (ws25-b3, deployed 2026-10-06)

**Rev 2 (2026-11-10)** — answers Codex's REQUEST CHANGES
(`build-notes/codex/reviews/ws25-b4.md`). This file is the authoritative
corrected plan (plan/16 "Corrections go back to the source file"); rev 1 is
in git history only.

| Codex finding | Answer | Where it changed |
|---|---|---|
| 1. Blocker — `quote(platform or "direct")` maps `None` and literal `"direct"` to one key, so the function is not injective | **Fixed.** Platform presence is now part of the key: `None` → `none`, any string (incl. `""`, `"none"`, `"direct"`) → `value:<encoded>`. Injectivity is stated over `(str \| None)²` and backed by a test-local left inverse | Fix; tests H1, H4, E5 |
| 2. Blocker — the four planned tests pass for a tags-only, unencoded implementation | **Fixed.** Exact-literal and escape-alias tests pin encoding in **each** component; a left-inverse test over a hostile corpus; a verified↔verified colon collision re-derived for the rev-2 format; a mutation check the implementer must record. Rev-1 test 4 is retired | Tests |
| 3. Correction — `readiness_probes.py` session probe has not gone red | **Fixed.** Rerun 2026-11-10: it passes at head, correctly (it never reaches the executor, so both callers are unverified). Restated, and the way it *will* break after b4 is named | Tests → "Codex's historical probe" |
| 4. Correction — rotating a still-supported shared token does not close the exposure | **Fixed.** Rotation only shrinks the set of holders. Refusing the shared-token path closes the attack class, and that path is currently Foundry's credential | Deploy step 5 |

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
verified JWT (`src/interop/servers/a2a.py#L85-L93`; the middleware publishes it
only from a verified lab JWT, whose `sub` is required and is the username
string, `src/interop/servers/auth.py#L129-L143`). `platform` is **not**:
`delegation.platform_of()` (`src/interop/delegation.py#L78-L90`) returns the
caller's own `metadata["delegation"]["platform"]` verbatim (`#L83-L85` — any
truthy value, so `:`, `%` and the literal `direct` are all admissible), or the
rider-text match `caller-platform:\s*([\w.-]+)` (`#L34`), which admits `-`.

So the joiner `-` can be supplied from the untrusted half:

| Caller | platform | verified subject | key |
|---|---|---|---|
| alice (JWT) | `foundry` | `alice` | `shim-shared-foundry-alice` |
| anyone with the legacy shared token | `foundry-alice` | — | `shim-shared-foundry-alice` |

The second caller lands in alice's cached Agentforce session, which is exactly
the cross-caller sharing that A11/F02 set out to remove. The same collision exists
between two verified callers, but it is narrower (`platform="a-b"`, subject `c` vs
`platform="a"`, subject `b-c`) because one subject has to be a `-`-suffix of
another's.

A second, non-exploitable flattening rides along: `platform or 'direct'` gives
an absent platform and a literal `"direct"` the same key (Codex finding 1;
rev 1 kept it). In each colliding pair both sides are the same principal: one
verified subject, or the shared-token population, which can already choose any
platform string. So it leaks nothing across callers, but it makes the
"injective" claim false. Rev 2 fixes it instead of narrowing the claim. A
mechanically checkable invariant with no reserved word is cheaper to keep
honest than one with an exception every reader has to re-derive. No seam in the
repo sends a literal `direct` platform (no hit under `src/`, `config/` or
`deploy/`; the only mentions are in this plan and Codex's review), so the split
costs nothing in practice.

The existing suite does not catch the defect. Every test in
`tests/unit/test_shim_session_isolation.py` uses `platform=None` ("direct"),
and `test_no_verified_subject_takes_the_platform_only_fallback` (L126) asserts
the *intent* ("not on some other caller's per-subject session") for that one
platform value only. Raised by the push security review of `f55e9bf` and
confirmed by reading the source. None of `proxy.py`, `delegation.py`,
`auth.py`, `a2a.py` or the isolation test file has changed since `f55e9bf`
(`git log f55e9bf..HEAD` on those paths is empty at `6b816c0`).

## Fix — one item, one file

**File:** `src/platforms/agentforce/proxy.py`. Only the key construction
changes: three module-level helpers plus a one-line call site.

```python
from urllib.parse import quote


def _enc(value: str) -> str:
    # safe="" percent-encodes everything outside [A-Za-z0-9_.~-]: ":" (the
    # separator), "/", and "%" itself, so a literal "%3A" becomes "%253A" and
    # cannot alias an encoded ":". surrogatepass keeps this TOTAL: JSON can
    # carry a lone surrogate, which strict UTF-8 would raise on.
    return quote(value, safe="", errors="surrogatepass")


def _opt(value: str | None) -> str:
    # Presence is part of the identity: None and every string are disjoint.
    return "none" if value is None else f"value:{_enc(value)}"


def _shim_session_key(platform: str | None, subject: str | None) -> str:
    if subject is None:
        return f"shim:shared:{_opt(platform)}"
    return f"shim:sub:{_enc(subject)}:{_opt(platform)}"
```

Call site in `handle()` (reuse mode only, same override semantics as b3):

```python
req.session_id = _shim_session_key(platform, (req.metadata or {}).get("verified_subject"))
```

**The invariant, stated exactly:** `_shim_session_key` is injective and total
over `(platform, subject) ∈ (str | None)²`, with `None` distinct from every
string, including `""` and `"direct"`. Rev 1 said "(kind, platform, subject)";
`kind` is a function of `subject is None`, so it was redundant.

**Why it holds, by inspection:** `_enc` output never contains `:`, so
`key.split(":")` always yields one of four shapes, told apart by field 1 and
the field count:

| shape | fields |
|---|---|
| shared, no platform | `shim` `shared` `none` |
| shared, platform | `shim` `shared` `value` `E(p)` |
| verified, no platform | `shim` `sub` `E(s)` `none` |
| verified, platform | `shim` `sub` `E(s)` `value` `E(p)` |

Each `E` inverts with `unquote(..., errors="surrogatepass")`, so the key has a
left inverse, and a function with a left inverse is injective. Test H4 runs
that inverse over a hostile corpus, so the claim is checked, not just argued.
Run on 2026-11-10 (`uv run python -I`): `quote` with these arguments round-trips `a:b`,
`a%3Ab` (→ `a%253Ab`), `\ud800` (→ `%ED%A0%80`), `é`, `""`, `direct` and `a-b`.

- **Subject first** partitions the verified namespace by identity before the
  untrusted field. The `sub`/`shared` tag means a shared-token key can never
  equal a verified key, whatever the platform string.
- **Subject typing.** The executor stamps only a truthy JWT `sub`
  (`a2a.py#L89-L93`), so the call site sees `None` or a non-empty string. If a
  `""` were ever stamped, it would get its own `shim:sub::…` key rather than
  joining the shared-token pool, which fails closed.
- **Not a hash.** The key stays readable in traces and in `AgentforceClient`'s
  session cache, and injectivity is provable by inspection, not by collision
  odds.
- **The key never leaves the process.** `AgentforceClient` uses the lab
  `session_id` only as a cache/lock key (`client.py#L167-L181`). Salesforce gets
  a fresh `externalSessionKey` UUID (`client.py#L141`), so `:` and `%` never
  reach the Agent API.
- **Behaviour otherwise unchanged:** same override in reuse mode, same
  fallback for the legacy shared token, same twin routing (which reads
  `platform` separately, `proxy.py#L63`).
- **Residual, by design (D81):** shared-token callers still share one session
  per platform string among themselves. b4 closes the forge *into a verified
  caller's* session. It does not make the shared-token pool per-caller, which
  the shared token cannot do because it carries no identity.

**Not in scope** (named so the critic can push back if it disagrees):
- Validating `platform` against a whitelist. It would also close this, but the
  platform value feeds twin routing and the D27 rider grammar, so constraining
  it is a contract change across every delegation seam. Encoding the key is
  local and sufficient for isolation.
- Retiring the legacy shared token at the shim. That is the real reduction in
  exposure, and it is the operator's call because Foundry depends on it (Deploy
  step 5).

## Tests (written first)

All in `tests/unit/test_shim_session_isolation.py`. "Red today" means against
the current proxy (unchanged since `f55e9bf`). Every helper-level test is
trivially red today because `_shim_session_key` does not exist yet. The
behavioural reds are called out per test.

**Helper level** (`from platforms.agentforce.proxy import _shim_session_key`):

- **H1 `test_key_keeps_absent_platform_distinct_from_literal_direct`.**
  `(None, "alice") != ("direct", "alice")` and `(None, None) != ("direct", None)`.
  *(Finding 1.)*
- **H2 `test_key_pins_the_encoding_of_both_components`.** Exact literals:
  - `_shim_session_key("p:%/x", "s:%/y") == "shim:sub:s%3A%25%2Fy:value:p%3A%25%2Fx"`
  - `_shim_session_key("p:%/x", None) == "shim:shared:value:p%3A%25%2Fx"`
  - `_shim_session_key(None, "alice") == "shim:sub:alice:none"`
  - `_shim_session_key(None, None) == "shim:shared:none"`

  Any implementation that leaves either component unencoded, encodes `:` but
  not `%`, or widens the safe set fails an exact string. *(Finding 2: encoding
  is required in both components.)*
- **H3 `test_literal_escape_does_not_alias_an_encoded_separator`.** Checked in
  each component separately:
  - subject `"a:b"` vs `"a%3Ab"` on platform `"p"`;
  - platform `"a:b"` vs `"a%3Ab"`, once with subject `"s"` and once with
    `None`.

  All keys must be distinct. This catches an encoder that rewrites `:` and
  passes `%` through. *(Finding 2, the `%` double-interpretation case.)*
- **H4 `test_key_has_a_left_inverse_over_a_hostile_corpus`.** Corpus `C`:
  `""`, `"direct"`, `"none"`, `"value"`, `"shared"`, `"sub"`, `"shim"`, `":"`,
  `"::"`, `"%"`, `"%3A"`, `"%253A"`, `"a-b"`, `"b-c"`, `"a:b"`, `"a:value:b"`,
  `"b:value:c"`, `"x:sub:alice"`, `"é"`, `"\ud800"`. For every `(p, s)` in
  `(C ∪ {None})²` (441 pairs), `_decode(_shim_session_key(p, s)) == (p, s)`.
  `_decode` is a short test-local inverse: split on `:`, dispatch on the four
  shapes above, then `unquote(..., errors="surrogatepass")`. The test proves
  injectivity (a left inverse) and totality (no raise on the lone surrogate).
  *(Findings 1 and 2.)*

**Executor chain** (the real `AdapterExecutor` → proxy, as the file already
does; E1 also goes through the real `TokenAuthMiddleware`, so at least one
regression covers the full authentication chain; `_ctx` gains an optional
`text` for E2):

- **E1 `test_shared_token_cannot_forge_a_subject_key_via_platform`.** alice
  (JWT, through the middleware) with `metadata.delegation.platform="foundry"`,
  and a shared-token caller (through the middleware) with
  `platform="foundry-alice"`; the keys differ. **Red today** (both are
  `shim-shared-foundry-alice`). Honest scope: under the fix this passes on the
  `sub`/`shared` tags alone, so it guards the namespace split, not the
  encoding.
- **E2 `test_rider_text_platform_cannot_forge_a_subject_key`.** Same as E1 with
  the platform delivered only via rider text (`caller-platform: foundry-alice`),
  because `platform_of` falls back to the message scan. **Red today.** Same
  scope note as E1.
- **E3 `test_verified_subjects_cannot_collide_across_platforms`.** Subject `c`
  on `platform="a-b"` vs subject `b-c` on `platform="a"`; distinct. **Red
  today.** Under the fix it passes on subject-first ordering alone, so it
  guards the ordering.
- **E4 `test_verified_subjects_cannot_collide_through_colons_in_metadata`**,
  parametrized over two pairs:
  - Codex's pair, `(s="a:b", p="c")` vs `(s="a", p="b:c")`;
  - `(s="a:value:b", p="c")` vs `(s="a", p="b:value:c")`.

  Both pairs are delivered via `metadata.delegation.platform`, which skips the
  rider regex. **Green today** (the old format has no `:` structure) and **red
  against a tags-only variant** of the new format. That is the point: with
  encoding removed, the second pair is `shim:sub:a:value:b:value:c` on both
  sides. Codex's pair alone would *not* collide under the `value:` prefix, so
  a test using only it would pass for the wrong reason, the same trap
  finding 2 names. It is kept for the variant that omits the prefix.
  *(Finding 2, verified ↔ verified.)*
- **E5 `test_absent_platform_is_not_the_literal_direct_platform`.** alice with
  no delegation metadata vs alice with `delegation.platform="direct"`, and the
  same pair for a shared-token caller; distinct in both. **Red today** (keys
  `shim-shared-direct-alice` / `shim-shared-direct`). *(Finding 1, end to end.)*

**Retired:** rev 1's test 4 (`test_separator_in_platform_is_encoded`). Codex
was right: it compared a `shared` key against a `sub` key, so it passed with
the colon unencoded. Its intent now lives in H2/H3, inside one namespace.

**Mutation check (the implementer records it in the notes; mutants are never
committed).** Swap each mutant in for `_shim_session_key` and confirm that at
least the named tests go red:

| mutant | drops | must go red |
|---|---|---|
| M1 tags + subject-first, no encoding | encoding in both | H2, H4, E4 |
| M2 encode the subject only | platform encoding | H2, H4 |
| M3 encode the platform only | subject encoding | H2, H4 |
| M4 `s.replace(":", "%3A")` in both | `%` (and `/`) encoding | H2, H3, H4 |
| M5 `_opt(p or "direct")` (rev-1 presence collapse) | presence | H1, H4, E5 |
| M6 `quote(..., safe="")` with default strict errors | totality | H4 (raises on `"\ud800"`) |

A mutant that leaves every test green is a gap in the tests and blocks
`built`. The proposer traced this table by hand on 2026-11-10; it was not
executed (a scratch run was not permitted in the planning session). M5 also
turns H2 red, which is consistent with "at least". The implementer's run is
the evidence.

**Literal updates.** The existing literal-key assertions change to the new form,
and the **property** each asserts is kept, not weakened (two subjects → two
keys, same subject → one key, unverified → shared key, asserted body subject
ignored). That is 13 assertion lines in the isolation file plus
`tests/unit/test_af_proxy.py#L71`:

| old | new |
|---|---|
| `shim-shared-direct-alice` | `shim:sub:alice:none` |
| `shim-shared-direct-bob` | `shim:sub:bob:none` |
| `shim-shared-direct` | `shim:shared:none` |
| `shim-shared-adk` | `shim:shared:value:adk` |

**Codex's historical probe (corrected; finding 3).** Rev 1 said
`readiness_probes.py#L113` "already went red at b3". That was wrong.
`session_probe()` (`build-notes/codex/codebase-review/readiness_probes.py#L101-L114`)
**passes at head**: Codex reran it, and so did Claude on 2026-11-10 at
`6b816c0` (it prints `CONFIRMED: two distinct callers receive the same
hosted-shim session key`). The pass is correct behaviour, not a reproduction.
The probe sets only a caller-asserted `metadata["user_context"]["sub"]` and
calls the proxy without the executor, so no `verified_subject` exists, and both
callers rightly take the shared-token fallback. It is cited as neither a
post-b3 reproduction of A11 nor a reproduction of this delimiter defect.

After b4 it will break, but not on its assertion. `selected()` AST-slices only
`TWIN_ENV_BY_PLATFORM` and `AgentforceProxyAdapter` out of the module
(`readiness_probes.py#L25-L36`, `#L107-L109`). The new module-level helpers and
the `quote` import are therefore absent from the probe's namespace, and
`handle()` raises `NameError`. That is the probe's source-slicing harness
meeting a refactor; it says nothing about isolation either way. Per plan/16 the
probe stays unchanged; it is Codex's file, and whether to add the helpers to
its selection is Codex's call. The cross-review records the break as a harness
break, not as "fixed" and not as a regression.

**Gates.** `uv run pytest -q` and `uv run ruff check .` green.
- Rev-1 full-suite baseline (2026-10-18, `f55e9bf`, another machine): 754
  passed, 2 failed, 5 skipped. Both failures were `FileExistsError: '.a2alab'`
  in `test_oauth_token_endpoint` and `test_openai_agents_backend`, an
  environment issue (the chezmoi-managed secrets dir was absent), not code.
- Rev 2 (2026-11-10, `6b816c0`): only the targeted pair was rerun —
  `tests/unit/test_shim_session_isolation.py tests/unit/test_af_proxy.py`,
  16 passed. That matches Codex's count. The implementer re-baselines the full
  suite on the implement branch and records any failures in the notes.

## Deploy (operator, work machine — this machine has no AWS access)

1. `git pull` on the work machine; merge `ws25-b4-shim-session-key` after the
   cross-review PASS.
2. `deploy/shim/build_zip.sh && deploy/shim/deploy_shim.sh` (full code
   deploy — the Lambda bundle carries `src/`, `deploy/shim/build_zip.sh#L27-L35`).
3. Smoke: two JWT callers on the same platform get two Agentforce sessions.
   Then send a shared-token call with `platform="<p>-<subject>"`, ask it a
   recall question from alice's session, and confirm there is **no** recall
   (it must not continue the verified caller's conversation). Record the
   result in `build-notes/claude/reviews/ws25-b4.md`.
4. Side effect: every cached shim session key changes, so in-flight shim
   conversations start a fresh Agentforce session once. The cache is
   per-Lambda-instance and in-memory; nothing persists the old keys.
5. **Interim, operator's call (corrected; finding 4).** Until step 2 runs, any
   holder of the legacy shared token can forge into a verified caller's
   session. The middleware accepts that token by design as an identity-less
   credential (`src/interop/servers/auth.py#L124-L159`):
   - **Rotating it does not close the exposure.** It locks out old or unknown
     holders, but every holder of the *replacement* token can still forge
     until step 2 ships.
   - **Only refusing the shared-token path at the shim closes the class.** A
     config-only way to do that is to set the shim's `A2ALAB_TOKEN` to a fresh
     value issued to no caller (JWT verification does not depend on it,
     `auth.py#L129-L143`).
   - **Refusing it is not free.** Foundry's A2A connection authenticates to the
     shim with the shared token as a static `x-lab-token` key
     (`deploy/foundry/provision_foundry.py#L103`), so Foundry → shim breaks
     until that connection moves to another credential.

   The step-2 code deploy is therefore the practical close. Refusal is the
   structural one, and it waits on the Foundry credential decision.

## Docs touched on implement

- `plan/07-workstreams.md` WS25: one `N. ⏳` item line for this batch.
- `plan/00-decisions.md`: the settling ADR (operator).
- No diagram or console copy names the key format. `git grep shim-shared` hits
  only `proxy.py` (the line being changed), tests, and `build-notes/`
  (including Codex's probe). Nothing under `plan/`, `config/` or `index.html`
  matches, so there is no architecture-sweep change.
