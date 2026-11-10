# Durable Codex behavior with user and project `AGENTS.md`

**Feature area:** Codex instruction discovery, user-level defaults,
repository-specific rules, instruction precedence, and work handoffs.

## Engineering takeaway

Put a personal default in `~/.codex/AGENTS.md`, then refine it in a repository's
`AGENTS.md`. Codex loads both and applies the more specific project instruction
after the user-level default.

## The configuration question

The desired behavior was simple: after Codex completes a piece of work, it
should recommend the most useful next action instead of ending with only a
status report. The behavior should apply generally, while one Data Cloud
experiment needed a more specific handoff tied to its execution plan.

This belongs in `AGENTS.md`, not a skill. It is a durable communication rule
that should apply to ordinary work without requiring a special workflow to be
invoked.

## User-level rule

The personal default lives at `~/.codex/AGENTS.md`:

```md
## Work Handoffs

After completing a piece of work, end the response with a clearly labeled
**Next recommended step**. Recommend one concrete action that most directly
advances the current goal. If several choices are equally useful, provide a
short numbered list and identify the preferred option. If the work is fully
complete, state that no further action is required.
```

This is intentionally generic because it affects every repository in which the
user-level file is loaded.

## Project-level refinement

The Data Cloud estate repository adds an `Experiment Handoffs` section to its
root `AGENTS.md`. It requires each completed task to report what was verified,
name unresolved evidence, and recommend an executable item from
`_estate/plan/amazon-zero-copy-usage-test-plan.md`.

The two rules are complementary:

| Scope | File | Responsibility |
|---|---|---|
| Personal default | `~/.codex/AGENTS.md` | Always recommend the next useful action |
| Repository | `<repo>/AGENTS.md` | Define what a useful handoff means for that project |

Avoid copying extensive project context into the global file. Broad defaults
become ambient instructions in unrelated work and are harder to maintain.

## How precedence works

Codex discovers instruction files from `~/.codex` and then from the repository
root toward the current working directory. Later, more specific directories can
refine or override earlier guidance. A nested `AGENTS.md` can therefore narrow
the behavior again for one subsystem. `AGENTS.override.md` is available when a
directory needs an explicit override rather than an additive guide.

For predictable results, phrase rules as observable output requirements. “End
with a labeled next step” is easier to verify than “be proactive.” Include the
exception for completed work so the agent does not invent unnecessary tasks.

## Evidence and limits

- **Vendor-documented:** Codex loads instruction files from `~/.codex` and from
  the repository path in root-to-leaf order, with later instructions taking
  precedence. See the OpenAI
  [Codex Prompting Guide](https://developers.openai.com/cookbook/examples/gpt-5/codex_prompting_guide#using-agentsmd).
- **Configured locally:** the personal handoff rule is stored in
  `~/.codex/AGENTS.md`; the Data Cloud experiment refinement is stored in that
  repository's root `AGENTS.md`.
- **Limit:** `AGENTS.md` steers model behavior; it is not a deterministic output
  validator. A mechanical guarantee would also require a harness or response
  check.

## Put this in the presentation

**Slide headline:** Layer personal defaults with project-specific definitions
of done.

- User-level `AGENTS.md`: the behavior you want everywhere.
- Repository `AGENTS.md`: the evidence and next action that matter here.
- More-specific instructions load later, so local context can refine the
  default without duplicating the whole policy.
- Write observable rules and define when no further action is appropriate.

**Visual:** a three-layer stack: `~/.codex/AGENTS.md` → repository `AGENTS.md`
→ nested `AGENTS.md`, labeled “general defaults” to “specific execution rules.”
