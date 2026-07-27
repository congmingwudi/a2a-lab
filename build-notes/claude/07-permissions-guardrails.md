# Permissions, classifiers, and hard guardrails — production-aware autonomy

**Feature area:** Claude Code permission modes (auto mode + its safety
classifier), narrow allow rules, protected paths, and the guardrails that don't
yield to rules.

## Engineering takeaway

Safe autonomy is layered, and the layers have different owners. Allow rules,
the auto-mode classifier, protected paths, and managed deny rules each block
for a different reason — so the first question when the agent stops is *which
layer stopped it*, not *how do I turn it off*.

Screenshots: [`2026-07-25-classifier-blocks-prod-org-write.png`](screenshots/2026-07-25-classifier-blocks-prod-org-write.png),
[`2026-07-25-allow-rule-vs-hard-guardrail.png`](screenshots/2026-07-25-allow-rule-vs-hard-guardrail.png).

## The story

During the D37 remediation deploys (secrets → Secrets Manager, per-caller
identity — real writes against hosted AWS infra and the **production**
Salesforce org), the session ran in auto mode and surfaced the whole layered
system in one afternoon:

1. **Read-only work flowed untouched.** `sf` queries, inspection commands —
   no prompts, full speed.
2. **Production-org writes were held for explicit approval** by the classifier,
   even in auto mode. The detail worth teaching: the hold hit the raw `sf` CLI
   and the salesforce-dx **MCP tool identically** — the classifier judged the
   *action class* (a hard-to-reverse write to a production environment it
   doesn't recognize as yours), not the tool name, so routing around it via a
   different tool didn't help. Claude's own commentary in the moment: it can't
   bypass the hold, and it *endorsed* it — "honestly I'd rather it stayed on for
   prod-org writes."
3. **Hosted-infra writes** (`aws lambda update-function-code`) also got held —
   and this is where the operator learning happened: that class of block is
   exactly what a **permission rule is designed to settle**. Claude drafted the
   precise `settings.local.json` edit — two narrow entries
   (`Bash(aws lambda update-function-code *)`,
   `Bash(aws secretsmanager put-secret-value *)`), not a blanket `Bash(aws *)`
   — and noted `/permissions` does the same through the UI.
4. **One block would not yield to any rule:** Claude editing its own
   permissions file. That's a hard guardrail, and Claude explained why while
   declining: *"if I could grant myself permissions, the allowlist would mean
   nothing."*  The human makes that edit; the agent then resumes and verifies
   the deploy under the new identity.

Point 4 turned out to be **vendor-documented, and stronger than the session
implied**. `.claude/` is a [protected path](https://code.claude.com/docs/en/permission-modes#protected-paths),
and `permissions.allow` rules do not pre-approve protected-path writes at all:
the safety check runs *before* settings allow rules are evaluated, so an entry
like `Edit(.claude/**)` changes nothing. The same instinct shows up one level
up — Claude Code ignores `defaultMode: "auto"` when it appears in
`.claude/settings.json` or `.claude/settings.local.json`, **so that a
repository cannot grant itself auto mode**. Self-authorization is closed off at
both the file layer and the mode layer, on purpose.

## How the layers actually work

Worth getting right on a slide, because the operator's mental model depends on
it. From the current [permission-mode documentation](https://code.claude.com/docs/en/permission-modes#eliminate-prompts-with-auto-mode):

**Auto mode's classifier is a separate model, not your session model.** It runs
on Claude Sonnet 5 by default rather than your `/model` selection, and a
classifier model Anthropic configures server-side takes precedence over that
default. (It falls back to the session's model when that model is Sonnet 4.6 or
when `availableModels` excludes Sonnet 5, and to an Opus model when the session
runs on Fable 5.) The classifier model is settled by the session's first
auto-mode request and doesn't change after that.

**The documented decision order:**

1. Matching allow / ask / deny rules resolve first — except that
   protected-path writes route to the classifier *even when an allow rule
   matches*, and org-`ask` connector tools and `requiresUserInteraction` MCP
   tools always prompt.
2. Read-only actions and working-directory file edits are auto-approved,
   except writes to protected paths.
3. Everything else goes to the classifier.
4. On a block, Claude receives the reason and tries an alternative. In v2.1.208
   and later that reason is usually the fixed text `Blocked by classifier`
   rather than a written explanation.

Three more behaviors that belong on the slide because they *confirm* the
teaching points rather than complicate them:

- **Entering auto mode drops broad allow rules** that grant arbitrary code
  execution; narrow ones like `Bash(npm test)` carry over, and the dropped ones
  are restored on leaving. "Allow rules are scalpels" isn't a style preference
   — a blunt rule is discarded by the product.
- **Repeated blocks pause auto mode**: 3 in a row or 20 total, and Claude Code
  resumes prompting. Not configurable. Repeated blocks usually mean the
  classifier is missing context about your infrastructure, which is what
  `autoMode.environment` trusted-infrastructure config is for.
- **Boundaries stated in conversation are treated as block signals** — "don't
  push until I review" blocks matching actions even when the default rules
  would allow them. But they're re-read from the transcript on each check, so
  compaction can lose one. For a hard guarantee, use a deny rule.

Operator tip surfaced by the same doc: `claude auto-mode defaults` prints the
full default rule lists as JSON.

## The field observation, and what it does and doesn't establish

First-hand, this project: after months on earlier models and a switch to Opus 5
on release day, the same tech stack and the same deploy motions produced
**immediately noticeable holds on actions that had previously passed** — the
production-write and hosted-infra prompts in the screenshots above. That
happened, and it is the reason this note exists.

What it does *not* establish is a causal link to the main-model upgrade. The
[Opus 5 announcement](https://www.anthropic.com/news/claude-opus-5) (2026-07-24)
does document classifiers shipping with the model — cyber classifiers expected
to "intervene around 85% less often than they do for Fable 5", with flagged
requests falling back to Opus 4.8 in Claude Code — but those are *dual-use
capability* classifiers (cyber/bio), the change is in the **less**-restrictive
direction, and the announcement says nothing about action-level permission
gating. And the permission classifier is a separate, server-configured model
that doesn't follow `/model` at all. So a `/model` switch is not evidence that
the permission classifier changed.

The honest version is more useful anyway: **the gating does move under you,
just not because you picked a different model.** Two documented mechanisms do
it — Anthropic can change the server-side classifier configuration at any time,
and the classifier's default rules change across CLI releases. The
permission-modes doc is annotated with exactly that history: what the
classifier decides changed in v2.1.200 (mid-session git remotes no longer
trusted), v2.1.203 (default-branch pushes, dotfiles-repo exception), v2.1.208
(denial text), v2.1.211 (branch-push rules), and v2.1.218 (`rm -rf /` handling).

The operator response is the same either way, and it's the slide-worthy part:
tighten with **narrow allow rules for the classes you've consciously accepted**
rather than loosening the mode — and treat a CLI or platform upgrade as a
permissions event, the way you'd treat it as a performance-baseline event.

## Why this belongs in the deck

This is the counterpart to the hooks note (05): hooks tell you when the agent
*stopped*; the permission system decides what it may *do* unattended. The
design worth teaching is the **gradient**, not any single block:

| Tier | Example from this project | Who resolves it |
|---|---|---|
| Pass-through | read-only `sf` queries, `git status` | nobody — never reaches the classifier |
| Allow-rule-settable | `aws lambda update-function-code` | narrow rule in `settings.local.json` (gitignored — never in the public repo) |
| Classifier-held | production Salesforce org metadata/Apex writes; both CLI and MCP paths | human approves the exact action, or an admin declares the infrastructure trusted |
| Protected path | the agent editing its own `.claude/` config | human edits it; allow rules explicitly do not apply |
| Organization hard stop | managed `permissions.deny`, `disableAutoMode` | admin only; project intent cannot override it |

## Teaching points for the deck

- **Action-class gating beats tool-name gating.** The same production write was
  caught through the CLI and through MCP — permissioning at the seam, the same
  instinct as the lab's own delegation guard (D27). (Observed here; the
  documented rules are about action classes, not a per-tool guarantee.)
- **Allow rules are scalpels.** The narrowest rule that covers the accepted
  risk is the fix for a tedious prompt — and auto mode enforces this by
  dropping broad execution-granting rules on entry.
- **Nothing self-authorizes.** Not the agent editing `.claude/`, not a repo
  granting itself auto mode. Both are closed at the product level, not by
  convention.
- **The agent is a good permissions consultant and an unreliable narrator of
  its own blocks.** Claude drafted the exact edit and articulated which of its
  blocks should never be rule-settable — genuinely better than reading the docs
  cold. But it usually receives only `Blocked by classifier`, so its
  explanation of *why* is inference. Confirm against `claude auto-mode
  defaults` before building policy on it.
- **Autonomy is earned per action class.** The holds on the 5% are what justify
  auto-approval of the 95%.
- **Diagnose the layer before loosening it.** Allow rule, classifier, protected
  path, and managed deny have different owners and different remedies.

## Evidence and limits

- **Vendor-documented:** the separate server-configured classifier and its
  independence from `/model`; the four-step decision order; protected paths and
  the fact that allow rules don't pre-approve them; broad-rule dropping on
  entering auto mode; the 3-consecutive / 20-total fallback; conversational
  boundaries and their loss to compaction; `defaultMode: "auto"` ignored from
  project settings.
- **Observed in this project:** the specific Salesforce and AWS holds, the
  matching CLI/MCP result, Claude's commentary in the two screenshots, and the
  step-change in hold frequency around the Opus 5 switch.
- **Not established:** that the main-model upgrade caused the new holds. Do not
  connect the screenshots to a model-release claim on a slide.
- Auto mode reduces prompts; it does not guarantee safety. Sensitive production
  work still needs explicit scope, review, and the target platform's own
  controls.

## Put this in the presentation

**Slide headline:** Autonomy is a permission gradient, not an on/off switch.

- Allow rules settle the command patterns you've consciously accepted — and
  only the narrow ones survive auto mode.
- The classifier evaluates everything else, on a separate model you don't pick.
- Protected paths and managed denies preserve boundaries the agent cannot
  self-authorize around.

**Visual:** the five-tier gradient table as the main object, with the two
terminal screenshots called out against the "Classifier-held" and "Protected
path" rows respectively. Optional second slide: the four-step decision order as
a flow, with the protected-path arrow bypassing the allow-rule box — that
bypass is the single most surprising detail for an engineering audience.

**Do not:** pair the screenshots with a "the model upgrade tightened the
guardrails" caption. The supportable caption is "classifier rules and
server-side configuration change across releases — re-check your allowlist on
upgrade."
