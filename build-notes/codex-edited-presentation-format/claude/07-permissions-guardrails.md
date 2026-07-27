# Permissions, classifiers, and hard guardrails — production-aware autonomy

**Feature area:** Claude Code permission modes (auto-approve + safety
classifier), narrow allow rules, and the guardrails that don't yield to rules.

## Engineering takeaway

Safe autonomy is layered. Pre-approved actions, a background classifier,
protected paths, and managed deny rules solve different problems; engineers
need to know which layer blocked an action before changing configuration.

Screenshots: [`2026-07-25-classifier-blocks-prod-org-write.png`](screenshots/2026-07-25-classifier-blocks-prod-org-write.png),
[`2026-07-25-allow-rule-vs-hard-guardrail.png`](screenshots/2026-07-25-allow-rule-vs-hard-guardrail.png).

## The story

During the D37 remediation deploys (secrets → Secrets Manager, per-caller
identity — real writes against hosted AWS infra and the **production**
Salesforce org), the session ran in auto-approve mode and surfaced a layered
guardrail system in action:

1. **Read-only work flowed untouched.** `sf` queries, inspection commands —
   no prompts, full speed.
2. **Production-org writes were held for explicit approval** by auto mode's
   safety classifier. In this session, the same class of write was held through
   both the raw `sf` CLI and the salesforce-dx **MCP tool**. That is a useful
   project observation—not a guarantee that every equivalent tool invocation
   will always receive the same result. Claude's commentary in the moment was
   that the production hold was appropriate.
3. **Hosted-infra writes** (`aws lambda update-function-code`) also got held.
   A narrow **permission allow rule** can pre-approve a consciously accepted
   command pattern before the classifier is consulted. Claude drafted two
   precise `settings.local.json` entries
   (`Bash(aws lambda update-function-code *)`,
   `Bash(aws secretsmanager put-secret-value *)`), not a blanket `Bash(aws *)`
   — and noted `/permissions` does the same through the UI.
4. **One block would not yield to ordinary approval:** Claude editing its own
   permissions file. Claude Code treats its configuration as a protected path,
   so auto mode does not approve the write. The human made the edit; the agent
   then resumed and verified the deploy under the new identity.

## What current Claude Code behavior establishes

Current [permission-mode documentation](https://code.claude.com/docs/en/permission-modes)
describes auto mode as a research preview in which a **separate,
server-configured classifier model** reviews eligible actions. It is
independent of the model selected with `/model`; switching the main Claude
model is therefore not evidence that the permission classifier changed.

The documented decision order is the useful operator model:

1. Matching allow or deny rules resolve first.
2. Read-only work and working-directory edits are normally approved, except
   writes to protected paths.
3. Remaining actions go to the classifier.
4. A classifier denial is reported to Claude, which can try an alternative or
   request manual approval after repeated denials.

The screenshots remain valuable as **observed in this project** examples of
steps 3 and 2 respectively. They should not be used to claim a model-release
change that the artifacts cannot establish.

## Why this belongs in the deck

This is the counterpart to the hooks note (05): hooks tell you when the agent
*stopped*; the permission system decides what it may *do* unattended. The
design worth teaching is the **gradient**, not any single block:

| Tier | Example from this project | Resolution |
|---|---|---|
| Pass-through in this run | read-only `sf` queries, `git status` | none needed |
| Explicitly pre-approved | `aws lambda update-function-code` | narrow allow rule in local settings |
| Classifier-held in this run | production Salesforce metadata/Apex writes | review and manually approve the exact action |
| Protected path | the agent editing its own permission file | human edits configuration |
| Organization hard stop | managed `permissions.deny` rule | cannot be overridden by project intent |

## Teaching points for the deck

- **Action-class gating beats tool-name gating.** The same production write is
  caught through the CLI and through MCP in the captured session. Treat this as
  a useful test case, not a universal guarantee.
- **Allow rules are scalpels.** The fix for a tedious prompt is the narrowest
  rule that covers the accepted risk — never widening the mode or the pattern.
- **The agent as permissions consultant.** Claude both drafted the exact edit
  *and* articulated which of its blocks should never be rule-settable. Asking
  "why were you blocked?" produced a better mental model than the docs.
- **Autonomy is earned per action class.** A production-aware setup is what
  makes it safe to let the agent run deploys at all — the holds on the 5% are
  what justify auto-approval of the 95%.
- **Diagnose the layer before loosening it.** A permission rule, an auto-mode
  classifier denial, a protected path, and a managed deny rule have different
  owners and remedies.

## Evidence and limits

- **Vendor-documented:** auto mode's separate classifier, decision order,
  protected paths, and managed deny behavior.
- **Observed in this project:** the specific Salesforce and AWS holds, the
  equivalent CLI/MCP result, and Claude's explanatory comments in the two
  screenshots.
- Auto mode reduces prompts; it does not guarantee safety. Sensitive production
  work still requires explicit scope, review, and external platform controls.

## Put this in the presentation

**Slide headline:** Autonomy is a permission gradient, not an on/off switch.

- Allow rules settle known, accepted command patterns.
- The classifier evaluates the remaining external or irreversible actions.
- Protected paths and managed denies preserve boundaries the agent cannot
  self-authorize around.

**Visual:** use the five-tier table above, paired with the two terminal
screenshots as concrete examples. Do not connect the screenshots to a main-model
upgrade claim.
