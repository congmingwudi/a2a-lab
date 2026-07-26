# Measuring what the build cost — per project, per repo, per tool

Claude Code exports OpenTelemetry, and CloudWatch accepts OTLP on a managed
endpoint with no collector in between. Point Claude Code at it and you get cost,
tokens, sessions, commits and PRs as ordinary CloudWatch metrics.

The interesting part for a customer is not that it exports — it's **how you
attribute the export**, because the default answer is "you can't". This note is
the configuration that makes per-project and per-repository usage work, plus the
two failures that cost this lab several days of silently-zero telemetry.

---

## 1. What Claude Code emits, and what it does not

Eight metrics: `claude_code.session.count`, `.lines_of_code.count`,
`.pull_request.count`, `.commit.count`, `.cost.usage`, `.token.usage`,
`.code_edit_tool.decision`, `.active_time.total`.

Every one of them carries the same standard attributes — `session.id`,
`organization.id`, `user.account_uuid`, `user.id`, `user.email`,
`terminal.type` — plus per-metric ones like `model`, `type` (input / output /
cacheRead / cacheCreation), `agent.name`, `mcp_server.name`.

**None of them names the project, the working directory, or the git
repository.** That is not an oversight in the docs; the attribute does not
exist. Out of the box you can answer "what did this user spend" and "on which
model", and you cannot answer "on which codebase" — which is usually the first
question a customer actually has.

## 2. The mechanism: `OTEL_RESOURCE_ATTRIBUTES`, made per-project by file location

Custom attributes ride on `OTEL_RESOURCE_ATTRIBUTES` and are included in every
metric (`OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES` defaults to true). They
flatten into the metrics store as `@resource.<key>`, so they are queryable
labels, not decoration.

The trick that makes this per-project rather than per-machine: **Claude Code
settings are per-repository.** `.claude/settings.local.json` lives inside the
project, so its attributes describe that project by construction. There is no
runtime detection to get wrong, no wrapper to remember, and moving between
repos changes attribution automatically because you changed directories.

```jsonc
// .claude/settings.local.json — checked in per repo (or gitignored per developer)
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "https://monitoring.us-east-1.amazonaws.com/v1/metrics",
    "OTEL_RESOURCE_ATTRIBUTES": "project=a2a-lab,repo=congmingwudi/a2a-lab,tool=claude-code,user.id=ryan.cox,user.email=ryan.cox@salesforce.com,team.id=a2a-lab,department=engineering"
  },
  "otelHeadersHelper": "/absolute/path/to/scripts/otel_headers.sh"
}
```

Three details that are easy to get wrong:

- **The `/v1/metrics` path and `http/protobuf` are both required.** The endpoint
  variable is the metrics-specific one (`..._METRICS_ENDPOINT`), not the generic
  `OTEL_EXPORTER_OTLP_ENDPOINT` — that matters if a user-level settings file
  already points the generic one somewhere else.
- **Values cannot contain spaces.** Use `_` or percent-encoding.
- **Custom keys never override built-ins.** A collision keeps the built-in value
  silently, so don't try to redefine `user.id` semantics.

Once set, `@resource.project` and `@resource.repo` are group-by dimensions —
cost per repo, tokens per team, sessions per project, from the same query
surface as everything else.

## 3. Credentials: a helper, not a pasted token

CloudWatch's OTLP endpoint wants a bearer token. The lab's rule (D39) is that
AWS auth is the only interactive human login in the path and every other
credential is a service identity fetched *with* it — so the token lives in
Secrets Manager and `otelHeadersHelper` fetches it at runtime. Claude Code
re-runs the helper roughly every 29 minutes, so a short-lived token works and
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

### Failure #2 — the token only works on one endpoint

A CloudWatch *metrics* bearer token authenticates against the OTLP metrics
endpoint and nothing else. Setting `OTEL_LOGS_EXPORTER=otlp` against the same
URL silently gets you nothing. Per-event logs need the Logs endpoint and their
own separate credential.

## 4. Codex: same destination, one real asymmetry

The Codex CLI ships its own OpenTelemetry exporter, configured in
`~/.codex/config.toml`:

```toml
[otel]
environment = "prod"
log_user_prompt = false
exporter = { otlp-http = { endpoint = "https://monitoring.us-east-1.amazonaws.com/v1/metrics", protocol = "binary", headers = { "Authorization" = "Bearer ${CW_METRICS_TOKEN}" } } }
```

Two differences from Claude Code, both structural:

1. **No headers-helper hook.** Codex takes literal headers with `${VAR}`
   interpolation from the process environment, resolved **once at launch**.
   There is no periodic re-fetch, so a session outliving the token's rotation
   window stops exporting (it does not fail).
2. **No per-project settings file.** Codex has per-project `[projects."..."]`
   config sections but no per-repo attribute mechanism, so the repo identity has
   to be supplied at launch.

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

---

**In this repo:** `scripts/otel_headers.sh`, `scripts/codex_otel.sh`,
`.claude/settings.local.json`, `src/observability/coding_source.py` (reads the
metrics back via PromQL — OTLP metrics do **not** appear in `ListMetrics`),
`src/console/app.py` → `BUILD_TELEMETRY_SETUP` (the steps, rendered in the
console's Coding Agents Telemetry section).
