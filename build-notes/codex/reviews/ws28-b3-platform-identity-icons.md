# WS28 B3 — platform identity icons implementation

## Scope

Implemented D99 on branch `ws28-b3-platform-identity-icons-local`:

| Area | Change |
|---|---|
| Account Intelligence Brief | Declares Claude as the Managed Agents researcher and Salesforce as the delivery org; both trusted marks appear in every execution-title surface. |
| Supplier Disruption | All three variants declare and display the Google ADK, Microsoft Foundry and OpenAI fan-out units as a separate semantic icon group from the orchestrator. |
| Protocol cells | Control Panel target rows and main-canvas headings resolve marks only from `target.platform`; target names remain escaped verbatim. |
| Platform registry | Consolidated the prior Registry and warm-up translations into one complete nine-platform map; Guide retains AWS and MuleSoft is explicitly text-only. |
| Strands title | Forward title is now `AWS Strands → Agentforce`; reverse remains unchanged per the narrow D99 settlement. |
| Accessibility | Icon-only groups expose trusted role-aware `aria-label` and hover text; unknown or text-only platform ids emit no icon group. |

No experiment flow, target, protocol, hosting, observability, tag, or runtime behavior changed. The
pre-existing unrelated working-tree edits were preserved and excluded from this batch's staged
`index.html` diff.

## Test-first evidence

The focused D99 tests were added before the YAML/renderer changes and failed as expected:

```text
2 failed, 92 deselected
KeyError: 'title_identity'
missing scenarioTitleHtml/targetTitleHtml heading expression
```

After implementation:

```text
uv run pytest -q tests/unit/test_console.py
94 passed in 8.14s

uv run pytest -q
779 passed, 3 skipped, 6 deselected, 105 warnings in 91.69s

uv run ruff check src tests scripts
All checks passed!

git diff --check
clean
```

The clean pytest commands removed the execution environment's injected SOCKS proxy variables. The
first baseline attempt inherited `socks5h://127.0.0.1:52568` while the locked environment does not
install `socksio`, producing 47 failures and 10 errors at HTTPX construction. Rerunning without
those proxy variables produced the true pre-edit baseline: **778 passed, 3 skipped, 6 deselected**.

## Browser evidence

A local Chromium run signed into `http://127.0.0.1:8200`, asserted accessible labels and exact SVG
counts, then captured:

- `build-notes/codex/screenshots/ws28-b3/account-brief-skyline-light.png`
- `build-notes/codex/screenshots/ws28-b3/supplier-adk-default-light.png`
- `build-notes/codex/screenshots/ws28-b3/strands-skyline-dark.png`
- `build-notes/codex/screenshots/ws28-b3/protocol-agentforce-default-dark.png`
- `build-notes/codex/screenshots/ws28-b3/supplier-cma-skyline-narrow.png`

The checks prove the Account Brief has two marks, the ADK Supplier variant has its orchestrator
mark plus all three unit marks, the forward Strands title has no `(AWS)`, and
`agentforce-openai-rest` receives Salesforce—not OpenAI—from target metadata. The 760px Skyline
capture shows the heading and cluster wrapping without overlap.

## Local review and publication

The current local console is running at `http://localhost:8200`; no hosted deployment was made.
To review, open the normal console, sign in, and switch among Default/Skyline and light/dark. Check
Account Intelligence Brief, all three Supplier Disruption variants, AWS Strands → Agentforce, and
several Protocol Calls rows.

Any later hosted publication must use a full console image rebuild because `config/` and `src/`
are copied into the image. Do not use `--skip-build`. Component topology is unchanged, so D99 does
not require a deployment-map edit.
