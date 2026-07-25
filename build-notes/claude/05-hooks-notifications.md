# Hooks → AWS logging service → Slack — knowing when Claude needs you

**Feature area:** Claude Code hooks (`Stop`, `SubagentStop`, `Notification`),
layered settings, and wiring the harness to external infrastructure.

## The setup

Three hooks in user-level settings (`~/.claude-personal/settings.json`) all
point at one bridge script, `~/.claude/hooks/claude-notify.sh`:

- **`Stop`** — fires when Claude finishes a turn → Slack level `success`
- **`SubagentStop`** — a background subagent completed → `success`
- **`Notification`** — Claude is waiting on input (permission prompt, question)
  → `notify`

The script reads the hook's JSON event from stdin, shapes a log event, and
POSTs it to a **custom AWS logging service** (API Gateway `/log` endpoint,
authenticated with an `X-Api-Key` header). The service fans out to Slack via a
`SLACK_WEBHOOK_ROUTES` map — so long-running work (a deploy, a matrix sweep, a
30-agent audit workflow) pings a phone the moment it finishes or blocks,
instead of requiring terminal-watching.

## Design details worth teaching

- **Hooks receive structured JSON on stdin.** The script uses `jq` to pull
  `hook_event_name` and a human summary (`.message // .stop_reason //
  .notification.message // ...`), maps event → level, and forwards the full
  payload as `detail` for debugging. There's even a `jq`-less fallback path.
- **Layered settings compose.** The hook + service URL/key live at the *user*
  level (they apply to every project); the *project's*
  `.claude/settings.local.json` contributes only `LOGGING_CHANNEL:
  "claude-notify"` — per-project Slack routing with zero duplication. Source
  defaults to the repo directory name, so messages arrive already labeled
  `rc-a2a`.
- **Failure policy: never block the agent.** Every error path exits 0 and
  `curl` gets a 5-second `--max-time`. A logging outage costs a notification,
  never a Claude Code session. This is the golden rule for any hook that
  isn't intentionally a gate.
- **Hooks are harness-level, not model-level.** "Every time X happens, do Y"
  cannot be a memory or a prompt — the model isn't guaranteed to be running
  when X happens. Hooks are executed by the harness deterministically, which
  is exactly what an every-event guarantee requires.

## The pattern generalized

This is the same shape as the lab's own observability layer (D22/D23):
deterministic capture at the seam, routing/analysis above it. The hook script
is a wiretap on the *build process*, just as `wiretap.py` is a wiretap on the
lab's A2A traffic — evidence of the same architectural instinct applied to
both the product and the tooling around it.

Secrets note: `LOGGING_API_URL`/`LOGGING_API_KEY` live in user-level settings
`env`, outside the repo — the checked-in project settings carry only the
channel name.

<!-- TODO(ryan): screenshot of the Slack channel showing a Stop notification
     arriving from a long deploy — pairs well with the "walk away from long
     runs" talking point. -->
