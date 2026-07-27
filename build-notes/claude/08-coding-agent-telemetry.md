# Measuring what the build cost — per project, per repo, per tool

Claude Code exports OpenTelemetry, and CloudWatch accepts OTLP on a managed
endpoint with no collector in between. Point Claude Code at it and you get cost,
tokens, sessions, commits and PRs as ordinary CloudWatch metrics.

The interesting part for a customer is not that it exports — it's **how you
attribute the export**, because the default answer is "you can't". This note is
the configuration that makes per-project and per-repository usage work, plus the
failures that cost this lab several days of silently-zero telemetry.

## Engineering takeaway

Telemetry is decision-grade only when the identity, the codebase, the signal,
and the credential all agree — and you have proved it by querying the
destination. A config file that parses is not evidence that data arrived.

---

## 1. What Claude Code emits, and what it does not

Eight metrics: `claude_code.session.count`, `.lines_of_code.count`,
`.pull_request.count`, `.commit.count`, `.cost.usage`, `.token.usage`,
`.code_edit_tool.decision`, `.active_time.total`.

Every one of them carries the same standard attributes — `session.id`,
`organization.id`, `user.account_uuid`, `user.account_id`, `user.id`,
`user.email`, `terminal.type` — plus per-metric ones like `model`, `type`
(input / output / cacheRead / cacheCreation), `agent.name`, `skill.name`,
`mcp_server.name`. (`app.version` and `app.entrypoint` exist but are off by
default.)

**None of them names the project, the working directory, or the git
repository.** That is not an oversight in the docs; the attribute does not
exist. Out of the box you can answer "what did this user spend" and "on which
model", and you cannot answer "on which codebase" — which is usually the first
question a customer actually has.

Source: [Claude Code monitoring documentation](https://code.claude.com/docs/en/monitoring-usage).

## 2. The mechanism: `OTEL_RESOURCE_ATTRIBUTES`, made per-project by file location

Custom attributes ride on `OTEL_RESOURCE_ATTRIBUTES` and are included on every
metric datapoint (`OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES` defaults to true).
They flatten into the metrics store as `@resource.<key>`, so they are queryable
labels, not decoration.

The trick that makes this per-project rather than per-machine: **Claude Code
settings live inside the project.** A settings file under the repo's `.claude/`
describes that repo by construction — no runtime detection to get wrong, no
wrapper to remember, and moving between repos re-attributes automatically
because you changed directories.

### The complete configuration, and which layer to put it in

Claude Code merges the `env` object **key-wise** across settings layers —
verified on a live session, where `LOGGING_API_*` arrived from user settings
and `LOGGING_CHANNEL` from the project file simultaneously. So the config can
be split across layers, and the split is a real decision:

- `~/.claude/settings.json` (user) — applies to every project. The right home
  for transport that never varies: exporter type, protocol, endpoint, and the
  credential helper. Note this path is overridable: `CLAUDE_CONFIG_DIR` moves
  user settings elsewhere, and a machine can have both a live and a dormant
  user-settings file (see the warning below).
- `.claude/settings.json` (project, checked in) — shared by everyone who
  clones. The natural home for `project` / `repo` / `team.id`.
- `.claude/settings.local.json` (project, **gitignored**) — one developer, one
  project. Absolute paths and anything operator-specific.

**This lab keeps all of it in the local file.** Not because the split is wrong
— it's the better shape for a team — but because this is a public repository
and a checked-in `.claude/settings.json` changes behavior for everyone who
clones it. The full example, which is *not* in the repo:

```jsonc
// .claude/settings.local.json — local to this developer and project (gitignored)
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "https://monitoring.<region>.amazonaws.com/v1/metrics",
    "OTEL_RESOURCE_ATTRIBUTES": "project=a2a-lab,repo=<owner>/<repo>,tool=claude-code,team.id=a2a-lab,department=engineering"
  },
  "otelHeadersHelper": "/absolute/path/to/scripts/otel_headers.sh"
}
```

Two things that are *not* in there, on purpose:

- **No `user.id` or `user.email`.** They were, and they did nothing: custom
  keys never override built-ins, so Claude Code kept its own values and
  discarded these silently. Dead config that reads as if it works. If you need
  your own identity dimension, use a non-reserved key such as `enduser.id`.
- **No credential.** The bearer token is fetched at runtime by
  `otelHeadersHelper`; nothing durable lands on the laptop.

Gitignore caveat worth a caption, because it is a good small lesson: this file
was excluded only by the developer's **global** gitignore, not by anything in
the repository. It was never at risk on *this* machine — and that is the trap.
Protection that lives outside the repo doesn't travel with a clone, so every
other checkout was one `git add -A` away from committing personal settings to
a public repo. It has since been added to the repo's own `.gitignore`
(alongside `*.bak*`, which is where hand-edited config backups land). **If a
file's safety depends on configuration that isn't in the repository, the
repository isn't safe — it's just lucky.**

### What is actually listening on that endpoint

`https://monitoring.<region>.amazonaws.com/v1/metrics` is **CloudWatch's own
managed OTLP ingest** — an AWS service endpoint, not a collector this lab runs
and not something on this machine. That is the whole reason the setup is short:
there is no OpenTelemetry Collector, no sidecar, no Prometheus server, no
agent. Claude Code POSTs OTLP/protobuf straight to AWS, authenticated with a
bearer token.

The sharp part is what happens on the other side. Ingested OTLP metrics do
**not** appear in the classic `ListMetrics` / `GetMetricStatistics` APIs at
all. They land in a **Prometheus-compatible store**, queried over SigV4-signed
HTTP at `https://monitoring.<region>.amazonaws.com/api/v1/query_range` — a
different path on the same host, with the OTel data model flattened into PromQL
labels (`__name__` for the metric, `@resource.<attr>` for resource attributes,
bare names for datapoint attributes). Write with a bearer token, read with
SigV4: two different auth models on one hostname.

This cost real time. The first version of the reader used `ListMetrics`:
ingestion returned HTTP 200 and discovery returned nothing, so both halves
looked healthy in isolation and the harvest would have reported "no coding
metrics yet, switch the exporters on" forever while the exporters worked
perfectly. `src/observability/promql.py` is the client that resulted, and its
docstring records the label conventions verified live.

> ⚠️ **Check for a pre-existing OTel block before adding yours.** The endpoint
> variable above is the metrics-specific `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT`,
> not the generic `OTEL_EXPORTER_OTLP_ENDPOINT` — and that distinction matters
> because a user-level or corporate-managed settings file may already point the
> generic one at a different destination entirely. On this machine one does: a
> second, dormant user-settings file carrying a corporate Bedrock + internal
> telemetry-proxy configuration, inert only because `CLAUDE_CONFIG_DIR`
> redirects user settings elsewhere. Enabling telemetry without reading every
> layer first is how work gets exported somewhere nobody intended.

### Is the enable flag alone dangerous to check in? Measured: no

The question that decides whether telemetry config can live in a checked-in
`.claude/settings.json`: if someone clones the repo with
`CLAUDE_CODE_ENABLE_TELEMETRY=1` but has no exporter configured, does anything
leave their machine? The docs imply not; this was tested rather than assumed,
with a local listener on `127.0.0.1:4318` (the OTel SDK default) and headless
`claude` runs from a directory with no project settings:

| Configuration | Result |
|---|---|
| `CLAUDE_CODE_ENABLE_TELEMETRY=1` only | **nothing sent** — zero requests |
| `+ OTEL_METRICS_EXPORTER=otlp`, no endpoint set | **6,178 bytes POSTed to `localhost:4318/v1/metrics`** |

The enable flag is a gate, not a destination: with no exporter selected,
nothing is constructed and nothing leaves. But row two is the real lesson —
the moment any layer selects an OTLP exporter, the SDK supplies a default
endpoint of its own, and if that layer also sets a generic endpoint the data
goes *there*. "I only turned on the exporter, I never said where" still
produces traffic.

The lab's conclusion for a public repo: keep the whole block in the gitignored
local file. Checking in the attributes alone would be defensible; checking in
the enable flag is safe for a cloner with no OTel config and a behavior change
for one who has a corporate layer.

Four details that are easy to get wrong:

- **The `/v1/metrics` path and `http/protobuf` are both required.** The endpoint
  variable is the metrics-specific one (`..._METRICS_ENDPOINT`), not the generic
  `OTEL_EXPORTER_OTLP_ENDPOINT` — that matters if a user-level settings file
  already points the generic one somewhere else. (`otelHeadersHelper` only
  applies to `http/protobuf` and `http/json`; the gRPC exporter takes static
  headers only.)
- **Values cannot contain spaces.** Use `_` or percent-encoding.
- **Custom keys never override built-ins.** A collision keeps the built-in
  value silently — so don't put `user.id` or `user.email` in the attribute
  string at all. It reads as if it works and does nothing. If you need your own
  identity dimension, use a non-reserved key such as `enduser.id`.
- **Every custom key is a label on every series.** `project`, `repo`, `team.id`
  are bounded and worth it; anything session- or file-scoped will cost you at
  the metrics backend. `OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES=false` keeps
  attributes in the resource block only.

Once set, `@resource.project` and `@resource.repo` are group-by dimensions —
cost per repo, tokens per team, sessions per project, from the same query
surface as everything else.

## 3. Credentials: a helper, not a pasted token

CloudWatch's OTLP endpoint wants a bearer token. The lab's rule (D39) is that
AWS auth is the only interactive human login in the path and every other
credential is a service identity fetched *with* it — so the token lives in
Secrets Manager and `otelHeadersHelper` fetches it at runtime. Claude Code
re-runs the helper roughly every 29 minutes (tunable via
`CLAUDE_CODE_OTEL_HEADERS_HELPER_DEBOUNCE_MS`), so a short-lived token works and
nothing durable lands on the laptop.

```bash
# scripts/otel_headers.sh — prints {"Authorization": "Bearer ..."} on stdout
aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" ...
```

### Failure #1 — the helper runs in Claude Code's environment, not yours

This one is worth the whole note. The helper is designed to degrade gracefully:
if it cannot fetch a token it prints `{}` and exits 0, because a missing token
must mean "no telemetry", never "your session is broken".

It ran with **no `AWS_PROFILE` set**, because Claude Code does not inherit the
shell you configured it in. The AWS CLI fell back to the *default* profile — a
different account, which cannot read that secret. So the helper returned `{}`,
the exporter sent unauthenticated requests, and the metrics store stayed
completely empty for days. Every config file was correct. Nothing was logged.

> Silent degradation plus ambient credential resolution is the same failure mode
> D39 was written about — the difference between "can *someone here* do this?"
> and "can *this identity* do this?". The fix is the same one: resolve the
> identity explicitly.

The helper now reads `AWS_PROFILE` from the repo's `.env` when the environment
doesn't set it, and prints the resolved AWS account to stderr when the fetch
fails, so the next person gets a reason instead of silence.

**Check it the way it will actually run**, with a scrubbed environment:

```bash
env -u AWS_PROFILE scripts/otel_headers.sh   # must print a real Authorization header
```

Then go look at the destination. A working helper proves the credential
resolved, not that anything was ingested.

### Failure #2 — the token only works on one endpoint

A CloudWatch *metrics* bearer token authenticates against the OTLP metrics
endpoint and nothing else. Setting `OTEL_LOGS_EXPORTER=otlp` against the same
URL silently gets you nothing. Per-event logs need the Logs endpoint and their
own separate credential.

The general rule, which §4 then violates: **signal, endpoint, and credential
are one unit.** Metrics, logs, and traces are three delivery paths, and each
needs its own smoke test.

## 4. Codex: same destination, four silent bugs to get there

The Codex CLI ships its own OpenTelemetry exporter, configured in
`~/.codex/config.toml`. Two structural differences from Claude Code:

1. **No headers-helper hook.** Codex takes literal headers with `${VAR}`
   interpolation from the process environment (`env_http_headers`), resolved
   **once at launch**. There is no periodic re-fetch for OTel credentials, so a
   session outliving the token's rotation window stops exporting (it does not
   fail).
2. **No per-project OTel layer at all.** Codex does load project-local
   `.codex/config.toml` overrides, but the config reference is explicit that it
   "ignores `openai_base_url`, `chatgpt_base_url` … and `otel` when they appear
   in a project-local `.codex/config.toml`; put provider, notification, and
   telemetry keys in user-level config instead." (The `[projects."…"]` sections
   are about trust level, not attributes.) So the repo identity has to be
   supplied at launch.

Both are handled by launching through a wrapper rather than `codex` directly —
`scripts/codex_otel.sh` fetches the token from the same secret and derives
`project` and `repo` from git:

```bash
export CW_METRICS_TOKEN="$token"
export OTEL_RESOURCE_ATTRIBUTES="tool=codex,project=$project,repo=$repo"
exec codex "$@"
```

> Portability note that cost a debugging cycle: extracting `owner/name` from a
> git remote with `sed -E 's#...([^/]+/[^/]+?)(\.git)?$#\1#'` works on Linux and
> fails **silently to empty** on macOS, because BSD `sed` has no lazy
> quantifier. Parameter expansion plus `awk -F'[:/]'` is portable.

### Failure #3 — the Codex path exported nothing, and it was failure #2 again

**Measured 2026-07-26.** A real Codex session — the one that produced the
presentation-format edits to these very notes — was launched through
`scripts/codex_otel.sh`. Afterwards, queried against CloudWatch over the same
2-day window, on the same endpoint, with the same credential:

| Query | Result |
|---|---|
| `claude_code.token.usage` | 28 series, **119,754,973 tokens** |
| `claude_code.session.count` grouped by resource labels | **3 sessions**, all tagged `tool=claude-code`, `project=a2a-lab`, `repo=congmingwudi/a2a-lab`, `team.id=a2a-lab` |
| `codex.cost.usage` / `codex.token.usage` / `codex.session.count` | **0 series each** |

So the Claude Code half of this note is now proven end to end — the
per-project and per-repo attribution genuinely arrives as queryable dimensions,
which is the whole claim. And the Codex half is proven *false*.

The cause is not the wrapper. It's `~/.codex/config.toml`, and it is failure #2
committed against ourselves. **Codex has three separate exporter settings, not
one:**

| Setting | Signal | Default |
|---|---|---|
| `otel.exporter` | logs / events | `none` |
| `otel.trace_exporter` | traces | `none` |
| `otel.metrics_exporter` | **metrics** | **`statsig`** — not OTLP |

The `[otel] exporter = { otlp-http = { endpoint = ".../v1/metrics" } }` shape
this lab shipped points the **logs** exporter at the CloudWatch **metrics**
endpoint, authenticates it with a **metrics-only** bearer token, and never sets
`metrics_exporter` at all — so metrics stayed on the `statsig` default and
never went to OTLP. Three mismatches in one line, each one silent.

That is the slide. The same team, the same afternoon, wrote a note warning that
signal, endpoint, and credential are one unit — while running a config that
violated it in three places, and had no way to notice because every layer
failed quietly. The Claude Code column is green because someone eventually
queried the destination. The Codex column is empty for exactly as long as
nobody did.

Source: the Codex manual on
[project configuration](https://learn.chatgpt.com/docs/config-file/config-basic)
and [OpenTelemetry configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

### Closing it out took a third bug the docs actively mislead you about

Fixing `metrics_exporter` made the export *fire* — and it still failed, now
loudly enough to see under `RUST_LOG=opentelemetry=debug`:

```
HttpMetricsClient.ExportFailed error="Status(403)" url=".../v1/metrics"
```

The config used `headers = { Authorization = "Bearer ${CW_METRICS_TOKEN}" }`,
and the lab had documented that as "interpolated from the environment". **Codex
does not interpolate `${VAR}` in OTel exporter headers.** The literal
placeholder string was being sent as the bearer token. Substituting a resolved
token turned `ExportFailed Status(403)` into `ExportSucceeded, Ok(())` on the
next run — the same config, one value different, which is what makes it proof
rather than a guess.

The reason this was believed in the first place is instructive: `env_http_headers`
*does* exist in Codex config, and does exactly what was assumed — but it belongs
to **MCP server configuration**, not to `[otel]`. A documentation search for
"does Codex interpolate env vars in headers" returns a confident yes about a
different subsystem. The schema was ultimately settled by reading the field
names out of the shipped binary and validating with `codex exec --strict-config`,
not from prose.

So the credential is now resolved by `scripts/codex_otel.sh` and injected at
launch with `codex -c`, which keeps it out of every config file on disk — the
same D39 posture as `otelHeadersHelper`, reached by a different mechanism
because Codex offers no helper hook.

### Acceptance test — now passing

1. ✅ `codex.*` datapoints on the metrics endpoint;
2. ✅ authenticated request, `ExportSucceeded`, clean flush on shutdown;
3. ✅ queryable `tool=codex`, `project`, `repo` dimensions;
4. ✅ prompt content redacted (`prompt=[REDACTED]`, `log_user_prompt = false`);
5. ⬜ repeat after credential rotation — untested, and the known weak point,
   since Codex resolves headers once at launch and cannot re-fetch.

**Metric parity was the last surprise, and the biggest.** The names this lab
had been querying — `codex.cost.usage`, `codex.token.usage`,
`codex.session.count` — were mirrored from Claude Code's schema and **do not
exist**. What Codex actually emits is its own scheme, and two differences are
structural rather than cosmetic:

- **There is no cost metric at all.** Claude Code reports `cost.usage` in USD;
  Codex reports nothing equivalent, so cross-tool cost must be modelled from
  tokens and a price table.
- **`codex.turn.token_usage` is a delta Histogram** dimensioned by
  `token_type`, where Claude Code's `token.usage` is a delta **Sum** dimensioned
  by `type`. `sum_over_time` returns the series but no scalar for a histogram on
  this surface, so the roll-up arithmetic does not transfer.

What is wired up today are the Sums that do: `codex.thread.started` (sessions)
and `codex.conversation.turn.count` (turns). The console states this coverage
per tool and renders **`n/a`** rather than `$0.00` where a tool publishes no
such measure — a zero would read as "this tool was free", the opposite of "we
cannot see it".

Four bugs, then, in one path: wrong exporter, wrong endpoint, uninterpolated
credential, and invented metric names. Every one of them silent, and three of
them documented as working before anyone queried the destination.

## 5. Why this matters to a customer

The three questions a platform team asks about coding-agent spend are "how
much", "by whom", and "on what". The first two are built in. The third is a
five-minute configuration that nobody discovers by default, and it is the one
that turns a bill into a decision — cost per repository is what tells you which
codebases the agents are actually earning their keep on.

The shape generalises past coding agents: **the tool tells you about itself, and
you have to tell it about your world.** Resource attributes are where that
context goes, and putting them in a per-project file rather than a shell profile
is what keeps them correct without anyone maintaining them.

The corollary this lab learned twice: **prove it at the destination.** Both
failures above were silent, both looked like correct configuration, and neither
was detectable from the client side.

## Evidence and limits

- **Vendor-documented:** Claude Code's metrics, standard attributes, resource
  attributes and their cardinality cost, the 29-minute header-helper refresh and
  its protocol limits, and per-signal endpoints; Codex's user-level-only `otel`
  block, its three separate exporter settings, and `${VAR}` interpolation at
  launch.
- **Repository-backed:** `scripts/otel_headers.sh`, `scripts/codex_otel.sh`,
  `src/observability/coding_source.py`, the console's `BUILD_TELEMETRY_SETUP`,
  and the harvest fix in commit `9d30739`.
- **Measured 2026-07-26:** the Claude Code path end to end — 28 series /
  119,754,973 tokens / 3 sessions, each carrying queryable `@resource.tool`,
  `@resource.project`, `@resource.repo`, and `@resource.team.id`. The
  attribution claim in §2 is proven, not asserted.
- **Measured 2026-07-26:** zero `codex.*` datapoints after a real Codex session
  run through the wrapper, with the three-way exporter/endpoint/credential
  mismatch identified in the live `~/.codex/config.toml` as the cause — then,
  after fixing all three, `ExportSucceeded` and `codex.*` series arriving with
  `tool=codex`, `project`, and `repo`. `${VAR}` non-interpolation was isolated
  by changing exactly one value (placeholder → resolved token) and watching
  `Status(403)` become `Ok(())`.
- **Measured 2026-07-26:** `env` merges key-wise across settings layers; and
  `CLAUDE_CODE_ENABLE_TELEMETRY=1` with no exporter selected sends nothing,
  while an OTLP exporter with no endpoint falls back to `localhost:4318`. Both
  tested directly rather than inferred from the docs, which state neither.
- **Observed in this project:** the wrong AWS profile, the empty headers, the
  endpoint mismatch, the macOS `sed` failure, and a dormant second
  user-settings file pointing the generic OTLP endpoint at a corporate proxy —
  all of it cost real days.
- **Still open:** Codex token and cost figures. The Sums (sessions, turns) are
  read; the token Histogram is not, and there is no cost metric to read. So
  Codex is a working column for *activity* and a blank one for *spend* — say
  which, rather than showing a single "Codex" tick.

## Put this in the presentation

**Slide headline:** Telemetry configuration is not telemetry evidence.

- The tool reports its own world; you have to add the codebase and team.
- Signal, endpoint, and credential are one unit — metrics tokens don't do logs.
- Verify the labels at the destination before anyone allocates cost with them.

**Visual:** agent → authenticated OTLP signal → CloudWatch query, with the real
numbers on the Claude Code path (119.7M tokens, 3 sessions, labeled by repo)
and the Codex path shown as *activity yes, spend no*. The asymmetry is the
slide: same endpoint, same credential, same afternoon, two tools that do not
report the same things.

**Second slide, if there's room:** the silent failures side by side — wrong
ambient profile, wrong endpoint for the signal, wrong exporter entirely,
uninterpolated credential, invented metric names. Every one had correct-looking
config, every one produced zero, none of them logged anything until someone
turned on debug output and queried the destination. Land it on the closing
line: **the difference between a green column and an empty one was never the
configuration — it was whether anybody checked.**

---

**In this repo:** `scripts/otel_headers.sh`, `scripts/codex_otel.sh`,
`.claude/settings.local.json` (gitignored), `src/observability/coding_source.py`
(reads the metrics back via PromQL — OTLP metrics do **not** appear in
`ListMetrics`), `src/console/app.py` → `BUILD_TELEMETRY_SETUP` (the steps,
rendered in the console's Coding Agents Telemetry section).
