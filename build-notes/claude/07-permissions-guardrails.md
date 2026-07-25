# Permissions, classifiers, and hard guardrails — production-aware autonomy

**Feature area:** Claude Code permission modes (auto-approve + safety
classifier), narrow allow rules, and the guardrails that don't yield to rules.

Screenshots: [`2026-07-25-classifier-blocks-prod-org-write.png`](screenshots/2026-07-25-classifier-blocks-prod-org-write.png),
[`2026-07-25-allow-rule-vs-hard-guardrail.png`](screenshots/2026-07-25-allow-rule-vs-hard-guardrail.png).

## The story

During the D37 remediation deploys (secrets → Secrets Manager, per-caller
identity — real writes against hosted AWS infra and the **production**
Salesforce org), the session ran in auto-approve mode and surfaced a layered
guardrail system in action:

1. **Read-only work flowed untouched.** `sf` queries, inspection commands —
   no prompts, full speed.
2. **Production-org writes were held for explicit approval** by the safety
   classifier, even in auto mode. The detail worth teaching: the block hit the
   raw `sf` CLI and the salesforce-dx **MCP tool identically** — the
   classifier judges the *action class* (hard-to-reverse, outward-facing
   writes to a known production environment), not the tool name, so routing
   around it via a different tool doesn't help. Claude's own commentary in the
   moment: it can't bypass the hold, and it *endorsed* it — "honestly I'd
   rather it stayed on for prod-org writes."
3. **Hosted-infra writes** (`aws lambda update-function-code`) also got held —
   and this is where the operator learning happened: that class of block is
   exactly what a **permission rule is designed to settle**. Claude drafted
   the precise `settings.local.json` edit — two narrow entries
   (`Bash(aws lambda update-function-code *)`,
   `Bash(aws secretsmanager put-secret-value *)`), not a blanket `Bash(aws *)`
   — and noted `/permissions` does the same through the UI.
4. **One block would not yield to any rule:** Claude editing its own
   permissions file. That's a hard guardrail, and Claude explained why while
   declining: *"if I could grant myself permissions, the allowlist would mean
   nothing."* The human makes that edit; the agent then resumes and verifies
   the deploy under the new identity.

## The operator's model observation

Classifiers are per-model and change with releases — that part is vendor-
documented. The [Opus 5 announcement](https://www.anthropic.com/news/claude-opus-5)
(2026-07-24) details classifier changes shipping with the model, e.g. cyber
classifiers tuned per model tier ("intervene around 85% less often than they
do for Fable 5") with flagged requests falling back to Opus 4.8. Two honest
caveats for the deck: the announcement's documented changes cover *dual-use
capability* classifiers (cyber/bio), not production-action gating, and the
cyber change is in the *less*-restrictive direction relative to Fable 5.

The field observation on top (first-hand, this project): after months on
earlier models and a switch to Opus 5 on release day, the same tech stack and
deploy motions produced **immediately noticeable holds on actions that
previously passed** — the production-write and hosted-infra prompts in the
screenshots above. The announcement confirms the mechanism moves per release;
it doesn't itemize this action class, so the delta is reported as observed
behavior, not a vendor claim. Either way the operator response is the same:
tighten with **narrow allow rules for the classes you've consciously
accepted**, rather than loosening the mode — and expect to re-calibrate your
allowlist on model upgrades, the same way you'd re-baseline performance
numbers.

## Why this belongs in the deck

This is the counterpart to the hooks note (05): hooks tell you when the agent
*stopped*; the permission system decides what it may *do* unattended. The
design worth teaching is the **gradient**, not any single block:

| Tier | Example from this project | Resolution |
|---|---|---|
| Pass-through | read-only `sf` queries, `git status` | none needed |
| Allow-rule-settable | `aws lambda update-function-code` | narrow rule in `settings.local.json` (gitignored — never in the public repo) |
| Held every time | production Salesforce org metadata/Apex writes | human approves each one; both CLI and MCP paths gated |
| Never self-serviceable | the agent editing its own permission file | human-only, by design |

## Teaching points for the deck

- **Action-class gating beats tool-name gating.** The same production write is
  caught through the CLI and through MCP — permissioning at the seam, the same
  instinct as the lab's own delegation guard (D27).
- **Allow rules are scalpels.** The fix for a tedious prompt is the narrowest
  rule that covers the accepted risk — never widening the mode or the pattern.
- **The agent as permissions consultant.** Claude both drafted the exact edit
  *and* articulated which of its blocks should never be rule-settable. Asking
  "why were you blocked?" produced a better mental model than the docs.
- **Autonomy is earned per action class.** A production-aware setup is what
  makes it safe to let the agent run deploys at all — the holds on the 5% are
  what justify auto-approval of the 95%.
- **Model upgrades move the guardrails.** Classifiers ship per model (cited
  above), so a model switch is a permissions event: expect new holds on
  day one, and treat re-tuning the allowlist as part of the upgrade, not as
  friction to route around.
