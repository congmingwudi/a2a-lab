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
| `2026-08-06-screenshot-corrects-inferred-design.png` | note 12; Data 360 Identity Match | Four raw screenshots of the Identity Match **vendor doc** overturn a design Claude had already *inferred* — it had guessed a field-equality match rule; the doc shows an `IdentityMatchType`-value selection. Claude: "These screenshots resolve my one genuine unknown — and they correct a conceptual error in my earlier design." Screenshots as ground truth, not just corpus. |
| `2026-08-10-scoped-requirement-well-scoped.png` | note 12; mobile-console ask | A requirement carrying its *purpose* ("I opened the console from my iPhone… not mobile friendly") and a **pre-answered architecture call** ("if this means a `/mobile` context, I'm fine with that"). Claude answers with a plan, not questions: "This is a substantial, **well-scoped** piece of work," then maps the layout before writing CSS. Complete input → few turns. |
| `2026-08-08-cites-references-wont-guess-ips.png` | note 12; D69/D70 region fix | Claude locates the two exact references (`.env:343`, `.env.example:183`), stages the D69/D70 doc corrections, and draws a hard line on the input it lacks: "I won't guess IPs into a firewall" — holding for the researcher's `eu-central-1` /32 list before touching config or the prod SG. |
| `2026-08-10-refuses-to-guess-metadata.png` | note 12; D72 frame-ancestors | The refuse-to-guess complement: chasing why a Tableau Next embed won't frame, Claude waits for authoritative docs rather than poke the org — "I'll wait for that to land rather than guess at another metadata change — the last guess cost a deploy cycle." Good inputs are leverage precisely because the model will wait for the one that matters. |
| `2026-08-02-walk-away-summary-and-decision.png` | note 12 / 05; broader work (off-repo) | **Redacted** (account id + hosted URL removed). Long unattended run: "Enjoy the camping trip — here's the full summary." Real plugin bugs fixed (arm64/x86), then Claude surfaces an A/B decision **without deciding for you** — "I deliberately didn't pick for you." From the author's broader Claude Code work, not this repo. |
| `2026-08-02-root-cause-overturns-own-assumption.png` | note 12 / 07; broader work (off-repo) | **Redacted** (hosted URL removed). Debugging by root cause: found `isNamedUserJwtEnabled=false`, **overturned its own prior "VPN block" assumption**, and declined an unnecessary outward action — "request 'A' (the Slack ticket) is not needed… I did not post it." From the author's broader Claude Code work, not this repo. |
| _(add rows as screenshots land)_ | | |
