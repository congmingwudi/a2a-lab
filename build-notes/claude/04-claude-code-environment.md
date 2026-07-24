# The Claude Code working environment — context that survives sessions

**Feature area:** CLAUDE.md, project MCP servers, persistent memory, skills,
permission tuning — the scaffolding that makes long multi-week builds work.

## CLAUDE.md as the onboarding doc for the model

`CLAUDE.md` is written the way you'd onboard a new senior engineer: what the
project is, the two-seam architecture, the commands, the conventions, and the
sharp edges (production-org deploys, timeout budgets, "keep the status column
honest"). Every session starts from it — which is why decisions like the Codex
ownership boundary and the delegation-guard rule live *there*, not in chat
history.

## plan/ as the shared decision log

`plan/00-decisions.md` holds ADRs D1–D35, appended as decisions are made (a
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

## Teaching points for the deck

- Context is the scarce resource; the fix is **written artifacts the model
  re-reads** (CLAUDE.md, ADRs, contract files), not longer chats.
- The same docs serve three readers: the human, the coding agent building the
  lab, and the lab's own runtime agents. Write once, ground everything.
- MCP turns risky external systems (a production Salesforce org) into typed,
  auditable tool calls.
- Memory vs repo: repo docs for project truth, memory for personal/operational
  truth (VPN sequencing, SSO states). Don't blur them.
