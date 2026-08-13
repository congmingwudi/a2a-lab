# Managing the context window — resume, compaction, and /clear

**Feature area:** the conversation-context lifecycle. What `claude --resume` /
`--continue` actually reloads, what auto-compaction does when the window fills,
and why a deliberate `/clear` at a task boundary beats letting compaction fire on
its own — with the durable-context stores ([04](04-claude-code-environment.md))
as the thing that makes clearing safe.

## Engineering takeaway

Resume replays the *real* transcript, not a memory of it — so a long, messy
session stays long and messy when you come back to it. Auto-compaction is a
safety net that summarizes the past (lossily) to keep you under the limit; it
fires wherever you happen to be, often mid-task, and it carries the *residue* of
every dead end forward in compressed form. A manual `/clear` at a natural seam is
strictly better for keeping the model sharp: you pick the cut, and you drop *all*
the noise instead of compressing it — which is only safe because the things worth
keeping already live outside the chat (CLAUDE.md, memory, files on disk).

## The story

The question that started this: *"when you launch `claude --resume`, does it see
the full chat or just understand where it left off from memory?"* — and then the
sharper follow-up: *"since auto-compaction happens anyway, what's the benefit of
`/clear` or a fresh session if I'm doing it to prevent degradation and stay under
the limit?"*

**Resume is a replay, not a summary.** Every session is written to a transcript
on disk (JSONL under `~/.claude/projects/<project-hash>/<session-id>.jsonl`).
`claude --resume` (`-r`) shows a picker of past sessions; `claude --continue`
(`-c`) grabs the most recent one in the current directory. Either way it reloads
the *actual* message history — every user turn, assistant turn, tool call, and
tool result — and hands it back to the model as context. It is not reading a note
about where you left off; it re-seeds the whole conversation, up to the context
window. This is a different mechanism from **memory** (CLAUDE.md and the
auto-memory files under `.../memory/MEMORY.md`), which is a small, curated fact
set loaded fresh at the *start of every* session, resumed or new. Memory is not a
stand-in for the transcript, and the transcript is not memory.

**The one wrinkle is compaction.** If a session got long enough to trigger
auto-compaction, the transcript already holds a *summary* in place of the older
turns — that happens live, not only on resume. So "full chat vs. summary" on
resume depends on whether the session was ever compacted, not on the act of
resuming. Resume restores whatever the transcript holds at that point: the
summary, plus any full turns after it.

**Which is why the `/clear` question has a real answer.** Compaction and `/clear`
both reduce tokens, but they are not the same move:

- **Auto-compaction** keeps a lossy summary of *everything* — including the
  tangents, abandoned approaches, and wrong turns — and rides it forward into
  every later turn. It fires automatically near the limit, which is often
  mid-task: the worst moment, because it may summarize away the exact detail you
  were about to use.
- **`/clear`** discards the context entirely in the same session; quitting and
  starting fresh does the same. CLAUDE.md and memory reload; the chat does not.
  You choose *when*, so you can cut at a seam where nothing important is lost.

The benefit of `/clear`, then, is **control and cleanliness, not raw token
count**: you pick the boundary, and you remove the noise instead of compressing
it. Degradation starts before the hard limit — a very full window dilutes
attention and "loses the middle" — and a compacted summary can still be a large,
noisy blob. A cleared context is maximally focused, faster, and cheaper. The
catch is the discipline that makes it safe: anything worth keeping has to be in
memory, CLAUDE.md, or a file *before* you clear, or you've thrown it away. Clear
mid-task with state only in the chat and you've lost it — that's the one case
where riding compaction is the better call.

## Why it works this way

- **Two stores, two jobs.** The transcript is the *episodic* record of one
  session (full fidelity, replayed on resume, capped by the window). Memory +
  CLAUDE.md are the *durable* record across sessions (small, curated, always
  loaded). Compaction operates on the first; `/clear` resets the first and leans
  on the second to survive. Keeping them straight is the whole model.
- **Compression is not the same as relevance.** Compaction optimizes for *fitting
  under the limit* while preserving intent; it does not know which past turns were
  dead ends. `/clear` optimizes for *relevance* by letting a human draw the line
  at a point where the residue is genuinely done with.
- **This is the durability discipline from [04](04-claude-code-environment.md),
  used the other direction.** There, the point was that CLAUDE.md + the `plan/`
  decision log stop each session re-deriving the project. Here that same
  durability is what lets you *throw the chat away* on purpose — the plan file and
  the ADRs are still there after the clear.

## Teaching points for the deck

- **Resume replays the transcript; it does not "remember the gist."** If the
  session was long and noisy, it comes back long and noisy. Memory
  (CLAUDE.md / `MEMORY.md`) is the separate always-loaded fact set — not a summary
  of the chat.
- **Compaction is a safety net, `/clear` is a deliberate reset.** Compaction keeps
  the residue of every dead end (compressed) and fires at an arbitrary point;
  clearing drops all of it at a boundary you choose.
- **Clear at task seams, not mid-task.** The win is a focused window — faster,
  cheaper, sharper — but only if the state that matters already lives in memory,
  CLAUDE.md, or a file. Write it down *before* you clear.
- **Degradation starts below the limit.** "Stay under the context limit" is the
  floor; "keep the window relevant" is the real goal. A big compacted summary can
  still degrade output.

## Evidence and limits

- **Product behavior (Claude Code):** the transcript-on-disk model, `--resume` /
  `--continue` replaying the full message history, and auto-compaction summarizing
  older turns when the window fills. Present the file-path detail
  (`~/.claude/projects/<project-hash>/<session-id>.jsonl`) as *illustrative of the
  mechanism* — confirm the current on-disk layout with the
  [claude-code-guide](#) surface before putting an exact path on a slide, since
  internal paths can change between versions.
- **Observed practice, not a measured study.** "Clearing at a boundary keeps the
  model sharper than riding compaction" is a working conclusion from how the two
  mechanisms behave, and it matches the degradation-below-the-limit intuition —
  but this note carries **no repo artifact or benchmark** behind a specific
  turn-count or quality delta. Grade it as *observed / reasoned*, not
  repository-backed, and do not attach a number.
- **Not a vendor performance claim.** Don't state a quantified "X% better" for
  `/clear` vs. compaction; the honest framing is the mechanism (full vs.
  compressed vs. discarded) and the discipline (durable stores make clearing
  safe).

## Put this in the presentation

**Slide headline:** Resume replays the chat — so manage the window on purpose.

- `--resume` / `--continue` reload the *actual* transcript, not a memory of it;
  memory (CLAUDE.md / `MEMORY.md`) is the separate, always-loaded fact set.
- Auto-compaction is a safety net — it summarizes the past (lossily) and fires
  wherever you are; `/clear` is a deliberate reset you place at a task seam.
- Clearing keeps the window focused and cheap — safe *only* because the durable
  stores ([04](04-claude-code-environment.md)) survive the clear. Write state down
  first.

**Visual:** a three-lane timeline of the same session. Lane 1 *Resume*: full bar
of turns reloaded intact. Lane 2 *Auto-compaction*: the early turns collapse into
a shaded "summary" block mid-bar, fired at an arbitrary point, residue shaded to
show it rides forward. Lane 3 *`/clear`*: the bar is cut clean at a labeled task
boundary, with a small persistent "CLAUDE.md + memory + files" chip that survives
the cut. Footer: *"compaction compresses the noise; clear removes it."*

**Do not:** put a quantified quality/turn-count delta on the slide, or imply
resume reconstructs the session "from memory" — it replays the transcript.
