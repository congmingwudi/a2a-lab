# Terminal screenshots — Claude as a thought partner

Drop screenshots taken at critical decision points during the build here.
These illustrate the *conversational* side of the build that the notes files
can't: Claude weighing trade-offs, pushing back, or surfacing an option before
a decision was committed to the ADR log.

## Conventions

- Name files `YYYY-MM-DD-<slug>.png` (e.g. `2026-07-18-d24-codex-handoff.png`)
  so they sort chronologically and can be tied to an ADR.
- Add a row to the index below when you drop a file in — the caption is what
  becomes the slide note in the deck.

## Index

| File | ADR / moment | Caption (why this mattered) |
|---|---|---|
| `2026-07-25-classifier-blocks-prod-org-write.png` | D37 remediation deploys | Claude explains why the auto-mode safety classifier held a **production Salesforce org write** for explicit approval — and that it hit the `sf` CLI and the salesforce-dx MCP tool *identically* (the block is at the action class, not the tool), while read-only queries passed through. Claude's own take: "honestly I'd rather it stayed on for prod-org writes." |
| `2026-07-25-allow-rule-vs-hard-guardrail.png` | D37 remediation deploys | The two-tier permission lesson in one reply: a hosted-infra write (`aws lambda update-function-code`) is the kind of block an **allow rule is designed to settle** — Claude drafts the exact narrow `settings.local.json` edit — while Claude editing its *own* permissions file is a **hard guardrail** it asks the human to perform: "if I could grant myself permissions, the allowlist would mean nothing." |
| _(add rows as screenshots land)_ | | |
