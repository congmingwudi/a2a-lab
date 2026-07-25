# Handing work to other coding agents — the contract-file pattern

**Feature area:** multi-agent development across *different* coding agents
(Claude Code as architect/integrator, OpenAI Codex as a contract implementer).

## What happened (D24)

The lab needed an OpenAI Agents SDK interior for the Path C experiments. Rather
than build it in this session, the work was handed to **Codex** — but not as a
loose "please build X" prompt. Claude Code first wrote a **build brief as a
contract file**: `plan/06-openai-codex-handoff.md`.

The rule, recorded in ADR D24 and enforced in `CLAUDE.md`:

> That one file is the contract; the `agents-sdk` backend and its tests are
> Codex's to write, everything else OpenAI-related is ours.

## Why the contract file works

- **The seam was already clean.** The lab's architecture (two abstractions:
  `AgentAdapter` inbound, `RemoteAgentClient` outbound, shared
  `AgentRequest`/`AgentResponse` models) means a backend is a bounded box with
  a typed surface. Codex never needed to understand the bridge, the trace
  layer, or Salesforce — just implement one interface and pass the loopback
  tests.
- **Ownership is written down, not remembered.** When Claude Code later touches
  OpenAI-related code, `CLAUDE.md` tells it which files are Codex's territory.
  Two agents editing the same files is how hybrid-agent projects rot; a
  one-line ownership rule in the project instructions prevents it.
- **The contract doubles as the review checklist.** When the Codex output came
  back, review = diff against the brief, plus the existing test suite
  (`uv run pytest`) as the acceptance gate.

## The repeatable pattern

1. Design the seam in Claude Code; make the interface + tests exist *first*.
2. Write the contract file: scope, interface, non-goals, acceptance tests,
   explicitly listed out-of-bounds files.
3. Record the handoff as an ADR (who owns what, and why this agent for this
   piece).
4. Point the other agent at the one file. Resist side-channel instructions —
   if the contract is missing something, fix the contract.
5. Integrate behind the seam; run the shared test suite as arbiter.

More handoffs are planned as the lab grows (future workstreams in
`plan/07-workstreams.md`); the pattern is agent-agnostic — it would work the
same handing a piece to another Claude Code session, a teammate, or a different
vendor's agent.

## Teaching points for the deck

- Multi-agent development is an **interface design problem**, not a prompting
  problem. The handoff succeeded because the seam existed before the handoff.
- `CLAUDE.md` is where cross-agent ownership rules live — it's read at the
  start of every session, so the boundary survives context loss.
- Shared tests are the neutral arbiter between agents with different styles.
