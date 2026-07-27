# The Claude Code working environment — context that survives sessions

**Feature area:** CLAUDE.md, project MCP servers, persistent memory, skills,
permission tuning — the scaffolding that makes long multi-week builds work, and
how to choose between them when a rule needs a home.

## Engineering takeaway

Long-running agent work scales when the important context lives in artifacts
every session reloads. Keep project truth, operator-specific context, and
executable configuration in separate homes, so each has a clear owner and a
clear lifetime.

## CLAUDE.md as the onboarding doc for the model

`CLAUDE.md` is written the way you'd onboard a new senior engineer: what the
project is, the two-seam architecture, the commands, the conventions, and the
sharp edges (production-org deploys, timeout budgets, "keep the status column
honest"). Every session starts from it — which is why decisions like the Codex
ownership boundary and the delegation-guard rule live *there*, not in chat
history.

## plan/ as the shared decision log

`plan/00-decisions.md` holds ADRs D1–D41, appended as decisions are made (a
convention CLAUDE.md itself enforces). This is the single highest-leverage
practice in the whole build: both human and model treat the ADR log as ground
truth, so a new session (or a subagent, or the Lab Guide agent at runtime —
which reads the same file through its `get_decision` tool) reconstructs *why*
things are the way they are without re-litigating. Decisions are cheap to
record at the moment they're made and expensive to reverse-engineer later.

## Project MCP servers — `.mcp.json`

The Salesforce DX MCP server (`@salesforce/mcp`) is registered in the repo's
`.mcp.json`, so org auth, metadata deploys, and Apex test runs happen through
typed MCP tools instead of raw `sf` CLI incantations. Notable because the
target is the user's **production org** — the MCP tools' structured results
(deploy status, test coverage) are what makes it safe to let the agent drive
deploys, with the raw CLI documented as fallback in `plan/04-runbooks.md`.

## Persistent memory

Claude Code's file-based memory carries cross-session operational knowledge
that doesn't belong in the repo: the Zscaler two-sided VPN rule (VPN off for
the local console, ON for AWS SSO deploys), deploy-session pre-flight checks,
and the standing note that `config/insights.yaml` updates are part of "done"
for any lab finding. The repo remembers the project; memory remembers *how
this human works on it*.

Entries carry a **type** — `user`, `feedback`, `project`, `reference` — and the
`feedback` ones are the most valuable, because they record a correction and the
reason for it. The sharpest example in this lab is "never `git add -A` here",
written after blanket staging swept retrieved Salesforce metadata into a public
repo twice, the second time with consumer keys in it. Full story in the speaker
notes at the end of this note.

## Project skills

The two audit workflows are also surfaced as invocable skills
(`/matrix-honesty-sweep`, `/insights-audit`) with `whenToUse` descriptions —
so "prepping for a demo" reliably triggers the right ritual whether invoked by
name or recognized by the model from context.

## Permission tuning

`.claude/settings.local.json` allowlists the read-only commands the project
uses constantly (scoped `git` invocations, the port-check `lsof`, specific
`WebFetch` domains) — fewer prompts, but destructive operations still gate.
There's a built-in skill (`/fewer-permission-prompts`) that mines your own
transcripts to propose this list.

The layering matters and is easy to get wrong: `settings.local.json` is
**personal and gitignored**, so nothing in it is shared repository
configuration. Rules the whole team should get belong in the checked-in
`.claude/settings.json`. See note 07 for what allow rules can and cannot
settle — notably that they do not pre-approve writes to `.claude/` itself.

## Where a standing requirement lives — four homes and a floor

The sections above are an inventory. The question that actually comes up mid-
build is narrower and more useful: *"I want X to happen every time we do Y —
where do I put that?"* This lab answered it four different ways — over a floor
of behaviour it never configured at all — and the difference is not style. It is
**who executes the rule, and who it applies to.**

Worked example, from 2026-07-27: "any time we build and deploy a new component
anywhere, update the architecture document."

| Home | Who executes it | Who it applies to | This lab's example |
|---|---|---|---|
| **Harness defaults** | the harness, always | every repo, every session — not editable | "don't commit or push unless asked"; branch before committing to a default branch |
| **`CLAUDE.md`** | the model, by reading | everyone in the repo — you, a teammate, Codex, a future session | *"Deploying anything new means updating `plan/09-deployment-map.md` in the same change"*, plus the D24 Codex ownership boundary and the delegation-guard rule |
| **Memory** | the model, by reading | this operator only | the Zscaler two-sided VPN rule; "`config/insights.yaml` updates are part of done"; "the honesty sweeps find *underclaims*, so run them after each workstream" |
| **Hooks** (`.claude/settings*.json`) | **the harness**, not the model | the machine (`settings.local.json`) or the team (`settings.json`) | the Stop/Notification hooks that ship events to the AWS logging service and into Slack — note 05 |
| **Saved workflows** (`.claude/workflows/`) | fan-out of subagents, on demand | the repo, when invoked | `insights-audit`, `matrix-honesty-sweep` — note 01 |

**The axis that decides it: intention, enforcement, or verification.**

- `CLAUDE.md` and memory express **intention**. The model reads them and
  complies — reliably, but it is compliance, not a guarantee. Use them for
  conventions where a missed instance is recoverable.
- A hook is **enforcement**. The harness runs it whether or not the model
  thought of it, which is why the notification path is a hook and not a
  sentence in `CLAUDE.md`.
- A workflow is **verification**. It answers "did we actually keep the rule?"
  after the fact, across the whole repo at once. The 2026-07-27 sweeps found
  five stale claims that four separate written conventions had not prevented —
  which is the honest argument for owning all three layers rather than
  believing the first one.

### The fifth layer nobody configures: harness defaults

There is a layer *underneath* all four, and it is easy to spend months not
noticing it. Some behaviour comes from **the agent harness itself** — standing
instructions that ship with Claude Code, apply in every repo and every session,
and are not in any file you can edit.

The one that surfaced in this build: **Claude Code does not commit or push
unless you ask it to.** At the end of a large change it reports what is in the
working tree and waits. That is not the model being polite, and it is not
configured anywhere in this project — it is a harness default. Related defaults
in the same family: branch rather than commit directly to a default branch, and
confirm before irreversible or outward-facing actions.

**Why this matters to know, rather than just enjoy:**

- **You can stop looking for the config.** Hunting `CLAUDE.md`, memory and
  settings for a rule that is not in any of them is a real time sink.
- **You can tell "default" from "decision".** Behaviour you like might be
  universal, or it might be something this project set up. Only one of those
  survives when you start a new repo.
- **You override it in the layers above, not by editing it.** If you wanted
  commits without asking, that is a memory entry ("commit finished work
  without asking; still never push without a nod") — the harness default keeps
  applying everywhere else. This lab deliberately **kept** the default, because
  the approval step is where the operator reviews the diff.

The teaching shape: **defaults are the floor, your files are the ceiling.** Ask
which one you are standing on before you go looking for a switch.

**The split that is easiest to get wrong: `CLAUDE.md` vs memory.** They feel
interchangeable — both are files the model reads at session start — and they are
not. `CLAUDE.md` is checked into a public repo and applies to every reader.
Memory is per-operator, outside the repo, and invisible to collaborators. A
project convention put in memory silently exempts everyone else on the project;
an operator preference put in `CLAUDE.md` becomes an instruction to people it
was never about. The test is one question: **would a teammate cloning this repo
need this rule to be true?**

## Teaching points for the deck

- Context is the scarce resource; the fix is **written artifacts the model
  re-reads** (CLAUDE.md, ADRs, contract files), not longer chats.
- The same docs serve three readers: the human, the coding agent building the
  lab, and the lab's own runtime agents. Write once, ground everything.
- MCP turns risky external systems (a production Salesforce org) into typed,
  auditable tool calls.
- Memory vs repo: repo docs for project truth, memory for personal/operational
  truth (VPN sequencing, SSO states). Don't blur them. One question settles it:
  *would a teammate cloning this repo need this rule to be true?*
- "Every time we do Y, do X" is three different asks, not one: **intention**
  (`CLAUDE.md` / memory), **enforcement** (a hook the harness runs), and
  **verification** (a workflow that audits after the fact). Naming which one
  you want is most of the decision.

## Evidence and limits

- **Repository-backed:** `CLAUDE.md`, the D1–D41 decision log, `.mcp.json`, and
  both saved workflows are checked in and inspectable.
- **Observed in this project:** persistent-memory entries and
  `.claude/settings.local.json` are local operator state — described here, but
  deliberately not something another checkout reproduces.
- MCP makes tool inputs and outputs structured and auditable; it does not by
  itself make a production action safe. Authentication, permissions, tests, and
  human approval are still what supply that boundary (note 07).
- **Harness defaults are observed behaviour, not a documented API.** "Doesn't
  commit or push unless asked" held throughout this build and is stated by the
  agent when asked directly — but it is a product default, not a guarantee this
  lab can prove, and defaults change between versions. Present it as *"there is
  a layer of behaviour you didn't configure, and knowing that saves you hunting
  for a switch"*, not as a specification. Anything a customer must be able to
  rely on belongs in a layer they own — a hook, or a branch protection rule.
- The `git add -A` incidents are dated and repository-verifiable: the
  consumer-key commit was removed from HEAD and the path gitignored, so the
  remediation is inspectable even though the keys remain in history.

## Put this in the presentation

**Slide headline:** Give each kind of context a durable home.

| Artifact | Stores | Value |
|---|---|---|
| `CLAUDE.md` | Architecture, commands, boundaries | Fast, consistent session onboarding |
| ADR log (`plan/00-decisions.md`) | Decisions and their rationale | Stops re-litigating settled design |
| Checked-in repo config | Shared tools, workflows, skills | Reproducible team behavior |
| Local memory + `settings.local.json` | Operator-specific procedure | Personal continuity without polluting project truth |

**Visual:** the four-row table with a hard line between the shared rows and the
local row. The speaker-note point: the same artifacts serve three readers — the
human, the coding agent building the lab, and the lab's own runtime agents,
which read the ADR log through a tool.

**Speaker notes — the memory example that lands.** If the room wants to know
what actually goes in memory, this is the one to tell, because it is a scar
rather than a preference:

> `git add -A` in this repo caused two incidents from the same reflex.
> Salesforce metadata gets retrieved into the working tree for a diagnosis,
> sits untracked, and the next blanket stage sweeps it in. On 2026-07-24 that
> was five `ExtlClntAppOauthSettings` files — scopes only, harmless. On
> 2026-07-25 it was three `ExtlClntAppGlobalOauthSettings` files carrying
> **consumerKey** values, committed and pushed to a **public** GitHub repo.
> Removed from HEAD, path now gitignored — but the keys are in history and had
> to be treated as published.
>
> The memory entry that came out of it is one line of instruction — *stage
> explicit paths in this repo, never `-A`, never `.`* — plus the reason, which
> is what makes it stick: the repo is public, and retrieved org metadata is
> routinely in the tree during any Salesforce debugging session.

Three things to draw out of it, in this order:

1. **Why memory and not `CLAUDE.md`.** It is a working habit, and it is about
   how *this* operator debugs Salesforce. A teammate cloning the repo does not
   need it to be true — they need the `.gitignore`.
2. **Memory records the reason, not just the rule.** "Never `git add -A`"
   without the incident reads as fussiness and gets overridden the first time
   it is inconvenient. With the incident attached, it does not.
3. **The honest ending: memory did not fix this, a structural guard did.**
   The path is gitignored now. An instruction reduces the chance; a `.gitignore`
   removes it. Use this to make the earlier point concrete — intention,
   enforcement, verification are different layers, and the one that actually
   closed this hole was enforcement.

That third beat is the one worth landing. It keeps the memory story from
sounding like "write good instructions and you're safe", which is precisely the
belief that put consumer keys in a public repo.

### Companion slide: "every time we do Y, do X" — where does that live?

The question every engineer in the room will actually have. One worked example
carried across all four homes: *keep the architecture document current whenever
something new is deployed.*

| If you want… | Put it in | Because |
|---|---|---|
| the model to know the convention | `CLAUDE.md` | checked in, every session reads it, applies to teammates and other agents too |
| the model to know *your* way of working | memory | per-operator, outside the repo, never inflicted on collaborators |
| it to happen whether or not the model remembers | a **hook** | the harness executes it; not subject to the model's judgment |
| to find out where the rule was already missed | a **workflow** | fan-out audit across the whole repo, run on demand |

**Visual:** the same sentence — "when we deploy something new, update the
architecture doc" — with four arrows to four boxes labelled *intention*,
*intention (personal)*, *enforcement*, *verification*, sitting on a grey base
labelled *harness defaults — behaviour you didn't configure*.

**Speaker note on the base layer:** the reason it is on the slide at all is
that engineers waste time looking for the config behind behaviour they like.
This lab's example: Claude Code reports what is uncommitted and waits for you to
ask — not a project setting, a harness default. Knowing the floor exists tells
you when to stop searching, and tells you that anything you must *rely* on
belongs in a layer you own.

**Speaker note, and the honest part:** this lab wrote the convention in
`CLAUDE.md` first, and still needed the verification layer — the 2026-07-27
sweeps found five published claims that had gone stale behind the work. Written
conventions reduce drift; they do not detect it. Say that out loud, because the
room's instinct is that a good instruction file is sufficient.
