**Verdict: REQUEST CHANGES**

# WS28 B1 proposal critique — AXS-inspired console theme

The route-layer approach is a sound way to preserve the dirty single-file SPA, and the
reference-site observations are accurate. One required authentication correction is missing
from the proposal. Without it, the advertised bookmarkable URL returns `401` in the normal
authenticated console configuration instead of rendering the sign-in-capable public shell.

## Required correction

1. **Add the new shell route to the exact public-shell auth exemptions, and test that boundary.**
   The proposal adds only the handler in `src/console/app.py` and says auth is a non-goal
   (`build-notes/codex/axs-console-redesign-plan.md#L38`,
   `build-notes/codex/axs-console-redesign-plan.md#L69`). The console is returned through
   `TokenAuthMiddleware`, whose public shell allowlist currently contains exact entries only for
   `/` and `/guide` (`src/console/app.py#L4841`, `src/console/app.py#L4843`,
   `src/console/app.py#L4851`). The middleware accepts an unauthenticated request only when its
   path is an exact exemption (or an exempt prefix), and otherwise returns `401` when
   `A2ALAB_TOKEN` is configured (`src/interop/servers/auth.py#L101`,
   `src/interop/servers/auth.py#L103`, `src/interop/servers/auth.py#L145`). This is not optional:
   the browser cannot send its local-storage persona JWT on the initial document navigation; the
   shell must load first so its fetch wrapper can attach that JWT to `/api` calls
   (`src/console/static/index.html#L1782`, `src/console/static/index.html#L1789`). Amend scope 2
   to add `/design/axs` to `exempt_paths`, with the same rationale as `/guide`, and amend the
   FastAPI test to set `A2ALAB_TOKEN` and prove unauthenticated `GET /design/axs` is `200` while a
   non-exempt route remains `401`. Keeping `/static/` public is already covered by the existing
   prefix exemption (`src/console/app.py#L4876`, `src/console/app.py#L4881`).

## Claim verification

| Proposal claim | Assessment |
|---|---|
| The reference site uses a white collapsible icon rail, pale-blue field, radial blue hero, rounded white cards, short transitions, responsive overlay navigation, visible focus, reveal motion, and reduced-motion handling. | Verified by direct retrieval of the user-supplied site on 2026-10-29. The proposal appropriately excludes its proprietary font and assets (`build-notes/codex/axs-console-redesign-plan.md#L13`). |
| `index.html` is dirty and is 10,835 lines, so a separate stylesheet plus server-side shell injection avoids colliding with current user work. | Verified: the worktree has an existing modification to `src/console/static/index.html`; the file is 10,835 lines. The current shell is read from disk in two small handlers, making a third transformed response feasible without duplicating the SPA (`src/console/app.py#L1294`, `src/console/app.py#L1304`, `src/console/app.py#L1311`). |
| Scoped post-shell CSS can leave `/` and `/guide` behavior unchanged. | Verified in principle. The current page already uses root theme attributes and semantic variables (`src/console/static/index.html#L8`, `src/console/static/index.html#L14`, `src/console/static/index.html#L50`); loading the extra stylesheet only from the experimental response and requiring `html[data-design="axs"]` on every rule isolates the normal responses. The proposed negative assertions for `/` and `/guide` are necessary (`build-notes/codex/axs-console-redesign-plan.md#L47`). |
| The route can share APIs, sign-in state, and local storage. | Verified after the auth correction above. API paths are same-origin (`src/console/static/index.html#L1769`, `src/console/static/index.html#L1774`), persona state is local storage (`src/console/static/index.html#L1782`), and only exact `/guide` triggers guide-only boot, so `/design/axs` follows the normal console boot (`src/console/static/index.html#L10654`, `src/console/static/index.html#L10811`). |
| The experimental shell should retain `no-cache`. | Verified. Both current shell responses use the shared header constant (`src/console/app.py#L1294`, `src/console/app.py#L1302`, `src/console/app.py#L1304`, `src/console/app.py#L1311`), and the existing regression test covers only `/` and `/guide` (`tests/unit/test_console.py#L79`, `tests/unit/test_console.py#L86`). The new route belongs in that test. |
| A future promotion needs a full image rebuild, but no new Docker `COPY` target. | Verified. The console image copies all of `src`, including a new stylesheet under `src/console/static/` (`deploy/console/Dockerfile#L34`), so a build is required and an additional `COPY` is not. A visual route on the existing console task does not add or move a deployed component, so no deployment-map change is required. |
| Existing light/dark and protocol/platform semantics can remain intact. | Feasible, not yet evidence: the proposed scope and static contract are the right guardrails. The current protocol and platform hues are centralized in root tokens (`src/console/static/index.html#L20`, `src/console/static/index.html#L22`). The implementation test should reject declarations of those tokens in `axs-theme.css`, as proposed (`build-notes/codex/axs-console-redesign-plan.md#L28`, `build-notes/codex/axs-console-redesign-plan.md#L34`). |
| Visual evidence can cover landing, control panel, experiment canvas, and Lab Guide without forking behavior. | Feasible. The existing SPA already implements the responsive control-panel overlay (`src/console/static/index.html#L10448`, `src/console/static/index.html#L10465`) and same-origin guide state (`src/console/static/index.html#L10518`, `src/console/static/index.html#L10654`). Record exact screenshot paths in the implementation notes so the checked-in evidence promised by scope 3 is auditable. |

## Independent evidence

- `uv run pytest -q`: **718 passed, 47 failed, 10 errors, 3 skipped, 6 deselected**.
  The run inherited a `socks5h://127.0.0.1:52942` proxy while `socksio` is absent; most failures
  and all setup errors are that environment issue. This is a pre-implementation critique run,
  not evidence caused by this batch.
- With proxy variables removed, `.venv/bin/pytest -q tests/unit/test_console.py`:
  **89 passed, 1 failed**. The remaining failure is the pre-existing
  `test_coding_harvest_is_reachable_by_name_but_not_in_the_sweep` expectation at
  `tests/unit/test_console.py#L1317`; it is unrelated to the proposed theme and no batch code
  exists yet.
- `git diff --check`: passed.
- The four Claude honesty skills named by the D79 runbook are not installed in this session's
  available skill catalog, so no claim is made that they were run.

After the auth exemption and its authenticated-configuration regression test are added to the
proposal, the design can proceed to operator settlement.

## Round 1 — implementation cross-review

**Verdict: REQUEST CHANGES**

The implementation correctly resolves the proposal-critique auth boundary: the data-free
`/design/axs` shell is public, the API remains gated, `/` and `/guide` do not receive the design
marker or stylesheet, and the design CSS is scoped to the opt-in marker. The checked-in landing
captures show a coherent visual direction at the promised desktop and phone viewport sizes.
Three settled requirements remain incomplete.

### Required findings

1. **Remove the text-level width caps that reinstate a named console regression.**
   `axs-theme.css` sets `max-width: 900px` on `.hero-body` and `max-width: 820px` on
   `.landing .lede` (`src/console/static/axs-theme.css#L394-L398`,
   `src/console/static/axs-theme.css#L467-L470`). `CLAUDE.md#L149` explicitly names those two
   selectors as the prior premature-wrapping defect and requires flowing prose to take its width
   from the page-level wrapper. The desktop capture exhibits that narrower text column inside the
   1120px hero wrapper. Remove the paragraph-level caps; if a reading-width constraint is still
   intended, put it on the appropriate container as the repository rule specifies.

2. **Supply the settled visual evidence for interactive console states.** The proposal requires
   the desktop and narrow-viewport evidence to cover the landing, Control Panel, an experiment
   canvas, and the Lab Guide drawer
   (`build-notes/codex/axs-console-redesign-plan.md#L60-L67`). Both committed captures show only
   the signed-out landing hero, and the implementation notes list only those two captures
   (`build-notes/codex/reviews/ws28-b1-axs-console-theme.md#L21-L29`). Capture the missing signed-in
   states at desktop and narrow width (separate or composite evidence is fine) so sidebar,
   canvas, drawer, form, and responsive-overlay styling are verified rather than inferred from
   CSS.

3. **Make the implementation commit pass a committed-diff whitespace check.**
   `git diff 2b3a1b6^ 2b3a1b6 --check` reports trailing whitespace at lines 3 and 4 of the proposal
   and a blank line at EOF in the implementation notes, despite the notes recording
   `git diff --check: passed` (`build-notes/codex/reviews/ws28-b1-axs-console-theme.md#L31-L37`).
   Clean those three committed-diff errors and report the commit-range check used; checking only
   the current unstaged worktree does not validate an already committed batch.

### Independent evidence

- `env -u ALL_PROXY -u all_proxy -u HTTP_PROXY -u http_proxy -u HTTPS_PROXY -u https_proxy uv run pytest -q`:
  **777 passed, 3 skipped, 6 deselected** (105 dependency deprecation warnings).
- `.venv/bin/pytest -q tests/unit/test_console.py -k 'axs or shell_asks_browsers_to_revalidate'`:
  **3 passed, 89 deselected**.
- `.venv/bin/ruff check src tests scripts`: **passed**.
- `git diff --check` on the current worktree: **passed**; the implementation commit-range check
  fails with the three whitespace errors described above.
- Visual inspection: the committed PNGs are genuinely 1440x1000 and 390x844 and render the
  landing treatment cleanly; neither contains the other three promised UI states.
- The four Claude honesty skills named by D79 are not installed in this session's available
  skill catalog. They do not fit this isolated local visual-only route closely enough to replace
  the focused route, auth, CSS, viewport, and full-suite checks above, and no claim is made that
  they were run.
