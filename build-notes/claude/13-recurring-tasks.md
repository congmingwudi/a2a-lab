# Recurring vs. one-shot vs. scheduled — four ways Claude "keeps going"

**Feature area:** the `/loop` slash command, background Bash tasks, the task
list (TaskCreate), and how each differs from a *scheduled* hosted job. Plus one
real, copy-pasteable use for this lab.

## Engineering takeaway

"Make the agent keep doing this" is four different mechanisms, and choosing the
wrong one is a silent failure. `/loop` re-runs a prompt on a timer **inside the
live session** — perfect for a bounded, attended watch, and exactly wrong for
standing monitoring, which needs a scheduled job that runs with no human
present. Know which axis you're on: *recurring vs one-shot*, and *in-session vs
survives-the-session*.

## The four mechanisms, and why they get conflated

| Mechanism | What it is | Recurring? | Survives session close? | Runs subagents? |
|---|---|---|---|---|
| **`/loop N /cmd`** | Re-injects a prompt/command into *this* session every N min (default 10m) | ✅ interval | ❌ dies with the session / laptop sleep | no (each tick is a normal turn) |
| **Background Bash** (`run_in_background`) | Detach one command; the harness *pushes* a notification when it exits | ❌ one-shot | ❌ tied to the session | no |
| **Task list** (TaskCreate) | A checklist the main loop walks down itself | ❌ | n/a (just state) | no — it's *me*, sequentially |
| **Scheduled Lambda / cron** (obs analyst D23, cost sentinel WS12/D44) | EventBridge fires a hosted job | ✅ interval | ✅ no human needed | it *is* the agent |

Three clarifications that this lab's own build kept running into:

- **The AWS-deploy-with-a-listener is NOT a loop.** Kicking off a deploy with
  `run_in_background` and getting re-invoked on completion is a *one-shot
  background task with a push notification*. There's nothing to poll, so there's
  nothing to loop. `/loop` is for the opposite case — **no completion push, and
  you want to keep checking**: poll a rollout until healthy, keep demo targets
  warm, re-run a babysit command. Rule of thumb: *if the thing hands you a
  completion callback, you don't need `/loop`; if it doesn't, `/loop` is the
  polling harness.*
- **A task list is not subagents.** Walking down a TaskCreate checklist is the
  main loop executing tasks in its own single context, sequentially. Subagents
  only exist when the **Agent** tool (separate context windows, parallel/
  background-capable) or the **Workflow** tool (deterministic multi-agent
  fan-out — see [01-workflows.md](01-workflows.md)) is called explicitly.
- **Parallelism never pools approvals.** When parallel subagents run, each tool
  call is classified by the permission model *at the moment it executes*,
  independently — approvals aren't batched. A subagent that hits a
  production-org write or a hosted-infra change (the holds in
  [07-permissions-guardrails.md](07-permissions-guardrails.md)) pauses on that
  call and surfaces to the operator while the others keep going. The classifier
  sits at the tool boundary, *below* the orchestration layer, so "run these in
  parallel" and "each write still gets classified" are orthogonal.

## Where `/loop` fits — and the line it must not cross

`/loop` runs in a live session, so it stops when the terminal closes or the
laptop sleeps. That single property draws the line:

- ✅ **Bounded, attended watch.** "Babysit this incident / this demo for the
  next hour." A human is present and *wants* to be in the loop. Bonus: each tick
  ends a turn, so the existing **Stop-hook → Slack** wiring
  ([05-hooks-notifications.md](05-hooks-notifications.md)) already pings you
  every pass — the two features compose for free.
- ❌ **Standing SRE monitoring.** A `/loop` monitor silently dies when you close
  your laptop — *worse* than no monitor, because you believe it's running. This
  lab already does standing monitoring the right way: **scheduled Lambdas** (the
  observability analyst D23, the cost sentinel WS12/D44) fire on EventBridge with
  no human present. The same operator-run-vs-scheduled distinction is drawn in
  [09-secrets-and-environment-identity.md](09-secrets-and-environment-identity.md)
  and [11-rules-and-sweeps.md](11-rules-and-sweeps.md).

The honest one-liner for a deck: **`/loop` is a watch, not a monitor. A watch
ends when you walk away; a monitor doesn't. Don't build a monitor out of a
watch.**

## The real use for this lab: a pre-demo warm watch

Cold starts are a documented, recurring demo hazard — `config/targets.yaml`
says it outright: the AgentCore/ADK/Foundry twins cold-start ~31–56s, which
blows the tight Path-A action budget (~85–90s measured, plan/03-results.md)
mid-demo. The console already exposes the machinery to fight it: `GET
/api/warmup` lists the `warmup: true` targets, `POST /api/warmup/{name}`
composes the correct (delegated) ping and records the duration to
`warmups.jsonl`, and `GET /healthz` is the liveness probe.

So the watch writes itself, using only surfaces that already exist:

```
/loop 8m uv run python scripts/demo_watch.py
```

`scripts/demo_watch.py` (added with this note) is a **thin driver**, not a
reimplementation: it discovers the warmable targets from the console, fires each
warm-up endpoint, and checks `/healthz`. It deliberately reuses `POST
/api/warmup/{name}` rather than composing its own ping, so the cold-start
numbers it produces are the *same* numbers the console publishes — forking that
logic would fork the evidence. It sends the shared `A2ALAB_TOKEN` as
`X-Lab-Token` (the service credential `TokenAuthMiddleware` documents), because
`/api/warmup` is gated when auth is on — `/healthz` is exempt, `/api/warmup` is
not — and it `load_dotenv()`s so a bare `uv run` picks the token up from `.env`.
On any failure it exits non-zero, so the Stop-hook turns each pass into a
walk-away signal: silence means the demo is warm, a Slack ping means a target
went cold before you did.

Run one pass by hand first (`uv run python scripts/demo_watch.py`), confirm the
table, *then* wrap it in `/loop` for the length of the demo.

**Where to loop it.** Against the **dev console** (`run_console.sh`, which sets
`A2ALAB_MODE=hosted`) is the honest target: on `:8200` it warms the *hosted
twins* a demo actually hits, and localhost sidesteps any corporate proxy in
front of the public console hostname. From a **machine off that proxy** aimed at
the public console, set both the URL and the token (that box has no `.env`):

```
A2ALAB_CONSOLE_URL=https://<console-host> A2ALAB_TOKEN=<token> \
  uv run python scripts/demo_watch.py
```

Interval, measured: a full first pass over the seven warmable targets is
**~3.5 min** (serial warms; cold starts ranged 6.7s–59.4s in one run). Set the
`/loop` interval above the pass time, not below it — `8m` keeps everything hot
with headroom, and an overlapping pass is handled anyway: a warm-up already in
flight returns 409, which the driver reports as "already warming", not a
failure.

## Evidence and limits

- **Repository-backed:** the warm-up endpoints (`GET /api/warmup`, `POST
  /api/warmup/{name}`), `/healthz`, and the `warmup: true` targets are in
  `src/console/app.py` and `config/targets.yaml`; `scripts/demo_watch.py` calls
  only those. The cold-start figures (~31–56s) are the comment in
  `config/targets.yaml`; the ~85–90s action budget is measured in
  `plan/03-results.md`. `/api/warmup` is gated by `TokenAuthMiddleware` and
  `/healthz` is exempt — both verifiable in `src/console/app.py`'s exempt list.
- **Measured 2026-08-10:** one live pass against the dev console warmed all
  seven targets (`ALL OK`, exit 0) with per-target durations 6.7s–59.4s and a
  full pass of ~3.5 min — the spread the watch exists to hide, and the number
  that sets the `/loop` interval.
- **Vendor-documented:** `/loop` re-runs a prompt or slash command on an
  interval (default 10m) within the running session; background Bash tasks push
  a completion notification; the task list and the permission classifier are
  harness features.
- **Observed in this project:** the Stop-hook → Slack composition
  ([05](05-hooks-notifications.md)) is a working operating setup, not a
  reproducible recipe. `/loop`'s "dies with the session" property is the
  behavior that makes it a watch and not a monitor — stated as a design fact,
  not a measured one.

## Put this in the presentation

**Slide headline:** "Keep going" is four mechanisms — pick by two questions.

- *Recurring or one-shot?* and *does it need to survive me walking away?* — the
  2×2 that separates `/loop`, background tasks, task lists, and scheduled jobs.
- A deploy-with-a-listener is a one-shot with a push, not a loop; a task list is
  the main loop, not subagents; parallel subagents still get classified per
  call.
- `/loop` is a **watch, not a monitor** — great for babysitting a demo for an
  hour, wrong for 24/7 SRE, which is what the scheduled analyst/sentinel are for.

**Visual:** the four-mechanism table above, with the two-axis label
(*recurring?* × *survives session?*) and a live terminal showing `/loop 8m uv
run python scripts/demo_watch.py` keeping the demo targets warm — the real pass
output (seven `ok` rows, 6.7s–59.4s) landing a green line in the Stop-hook Slack
channel.

<!-- TODO(ryan): screenshot a real `/loop` warm pass — the terminal tick plus
     the matching Slack Stop-notification — the pair is the whole story. -->
