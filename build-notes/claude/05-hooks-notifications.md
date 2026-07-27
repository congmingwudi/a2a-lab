# Hooks → AWS logging service → Slack — knowing when Claude needs you

**Feature area:** Claude Code hooks (`Stop`, `SubagentStop`, `Notification`),
layered settings, and wiring the harness to external infrastructure.

## Engineering takeaway

Long-running automation needs an out-of-band control loop. Lifecycle hooks turn
"finished" and "needs you" into deterministic notifications, so a human can
leave the terminal without losing the ability to intervene.

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

### The secret was in the wrong channel, and it took a while to see it

`LOGGING_API_URL` and the API key started in user-level settings `env`, outside
the repo — which sounds right, and is how this note originally described it.
The project file carries only the channel name.

It was still wrong, for a reason worth a slide. **A settings `env` block is a
broadcast: its values land in the environment of every command the agent runs**,
not just the hook. Found by accident, running `env` in a Bash tool and seeing
`LOGGING_API_KEY` sitting there. Codex had the identical problem one config
file over, via `shell_environment_policy.set`.

So an agent that executes arbitrary commands was handing that key to every
subprocess it spawned. One `env` in a log, one crash reporter, one diagnostic
upload, and it is gone. Tellingly, the `OTEL_*` variables did **not** appear in
that environment — Claude Code consumes those internally. The exposure came
specifically from using a general-purpose channel to deliver a secret to one
consumer.

The fix: both hook scripts now read the key from the **macOS Keychain** at
invocation, and it is gone from both config files. It exists only inside the
hook process, for the life of one `curl`.

Keychain rather than Secrets Manager, deliberately — and this is the part that
generalizes. The lab's rule (D39) is "fetch every credential with the one AWS
session you already have", but that session is for the *lab's* account while the
logging service runs in a *personal* one. Pulling from Secrets Manager would
have needed credentials for the other account: the same problem, one layer up.
The house pattern didn't fit, and forcing it would have meant parking a personal
credential in a corporate secret store to satisfy the letter of the rule.

**Two teaching points, and the second is the better one:**

- **Ask what else can read it, not just where it is stored.** "Outside the repo"
  answered the wrong question. The env block was outside the repo *and* inside
  every subprocess.
- **A security rule that doesn't fit deserves scrutiny, not a workaround.** The
  tell that Secrets Manager was wrong here wasn't that it was hard — it's that
  making it fit required doing something that was worse than the problem.

## Evidence and limits

- **Vendor-documented:** `Stop`, `SubagentStop`, and `Notification` hooks
  receive structured event JSON on stdin, and notification hooks are intended
  for exactly this kind of side effect.
- **Observed in this project:** the bridge script, the AWS endpoint, the
  user-level settings, and the Slack route all live *outside* this repository.
  This note documents a working operating setup; it is not a
  copy-paste-reproducible deployment recipe.
- **Measured 2026-07-26:** values in a settings `env` block reach the
  environment of the agent's own tool subprocesses — found by running `env` in a
  Bash tool and seeing the API key. `OTEL_*` values did not, so this is a
  property of the general env channel, not of settings as such. The key now
  comes from the Keychain; a 200 with it and a 401 with a bogus one confirm the
  hook is genuinely reading from there.
- The script deliberately exits 0 on every failure path. That's correct for
  notifications and **wrong** for a policy hook whose job is to block an unsafe
  action — a gate that fails open isn't a gate.

## Put this in the presentation

**Slide headline:** Hooks make long agent runs operable, not just autonomous.

- The harness emits lifecycle events, so delivery doesn't depend on the model
  remembering an instruction.
- One small bridge normalizes events into infrastructure you already run.
- Failure is non-blocking, because a lost notification must never break the work.

**Visual:** Claude Code event → hook script → logging API → Slack, with a dashed
failure branch labeled "exit 0; session continues". Pair it with the pattern
callout: this is the same wiretap shape as the lab's own trace layer, applied to
the build process instead of the product.

<!-- TODO(ryan): screenshot of the Slack channel showing a Stop notification
     arriving from a long deploy — pairs well with the "walk away from long
     runs" talking point. -->
