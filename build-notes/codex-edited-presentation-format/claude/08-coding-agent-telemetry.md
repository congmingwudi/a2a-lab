# Measuring what the build cost — and proving the attribution works

Claude Code exports OpenTelemetry (OTel), and CloudWatch accepts OTLP metrics
on a managed endpoint without a collector in between. The useful engineering
question is not merely whether telemetry exports; it is whether every record
can answer **who used which tool on which codebase**.

This note separates the verified Claude Code path from the Codex path that
still needs end-to-end validation. That distinction matters because a
configuration that parses cleanly is not evidence that data arrived with the
labels an organization needs.

## Engineering takeaway

Telemetry is decision-grade only when identity, codebase, signal delivery, and
queryability are verified at the destination.

## 1. What Claude Code emits—and what it does not

Claude Code currently documents eight metrics:

- `claude_code.session.count`
- `claude_code.lines_of_code.count`
- `claude_code.pull_request.count`
- `claude_code.commit.count`
- `claude_code.cost.usage`
- `claude_code.token.usage`
- `claude_code.code_edit_tool.decision`
- `claude_code.active_time.total`

Available standard attributes include `session.id`, `organization.id`,
`user.account_uuid`, `user.account_id`, `user.id`, `user.email`, and
`terminal.type`. Some are conditional on authentication or cardinality
settings; `app.version` and `app.entrypoint` are off by default. Individual
metrics add dimensions such as `model`, token `type`, `agent.name`,
`skill.name`, or `mcp_server.name`.

**None of the standard attributes names the project, working directory, or git
repository.** Out of the box, an organization can answer “how much?” and “by
whom?” more easily than “on which codebase?”

Source: [Claude Code monitoring documentation](https://code.claude.com/docs/en/monitoring-usage).

## 2. Claude Code: add project context with resource attributes

Custom values in `OTEL_RESOURCE_ATTRIBUTES` are attached to metric datapoints
when `OTEL_METRICS_INCLUDE_RESOURCE_ATTRIBUTES` is enabled (the documented
default). That makes bounded organizational dimensions such as `project`,
`repo`, `tool`, and `team.id` queryable.

The setting becomes project-specific by location:
`.claude/settings.local.json` belongs to one developer in one project. It is
gitignored, not checked in. A team that wants shared attribution should use an
appropriate shared or managed settings layer.

```jsonc
// .claude/settings.local.json — local to this developer and project
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "https://monitoring.us-east-1.amazonaws.com/v1/metrics",
    "OTEL_RESOURCE_ATTRIBUTES": "project=a2a-lab,repo=congmingwudi/a2a-lab,tool=claude-code,team.id=a2a-lab,department=engineering"
  },
  "otelHeadersHelper": "/absolute/path/to/scripts/otel_headers.sh"
}
```

Details that matter:

- Use the signal-specific `/v1/metrics` endpoint and
  `OTEL_EXPORTER_OTLP_METRICS_ENDPOINT` when another signal may use a different
  endpoint.
- Resource-attribute values cannot contain spaces; use `_` or
  percent-encoding.
- Custom keys never override built-ins. Do not try to redefine `user.id` or
  `user.email`; use a non-reserved organizational key such as `enduser.id` when
  additional identity is required.
- Every custom label adds cardinality. Repository and team are bounded, useful
  dimensions; session- or file-level values can make storage unnecessarily
  expensive.

## 3. Credentials: a helper, not a pasted token

CloudWatch's OTLP endpoint accepts bearer authentication for workloads outside
AWS. The lab keeps the credential in Secrets Manager and configures
`otelHeadersHelper` to fetch it at runtime:

```bash
# scripts/otel_headers.sh — prints {"Authorization": "Bearer ..."} on stdout
aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ID" ...
```

Claude Code runs the helper at startup and periodically thereafter (29 minutes
by default), allowing credentials to refresh without writing them to disk.

### Failure #1: helper identity differed from the operator shell

In this setup the helper ran with no `AWS_PROFILE`. The AWS CLI fell back to a
different default account that could not read the secret. The helper returned
valid but empty header JSON (`{}`), the exporter sent unauthenticated requests,
and the metrics store remained empty for days.

The fix was to resolve the profile explicitly from the project's local
environment and print the resolved AWS account to stderr on failure.

Test the helper the way Claude Code will run it:

```bash
env -u AWS_PROFILE scripts/otel_headers.sh
```

The output must contain a real `Authorization` header. Then confirm a metric at
the destination; helper success alone does not prove export success.

### Failure #2: signal, endpoint, and credential did not agree

The configured URL and bearer credential were for the CloudWatch OTLP
**metrics** endpoint. Enabling `OTEL_LOGS_EXPORTER=otlp` against `/v1/metrics`
does not create a logs pipeline. Logs require the CloudWatch Logs OTLP endpoint
and authorization valid for that service.

Treat metrics, logs, and traces as separate delivery paths and smoke-test each
one.

## 4. Codex: similar controls, not proven parity

Codex has separate OpenTelemetry controls for logs/events, traces, and metrics
in user-level `~/.codex/config.toml`. Current Codex configuration explicitly
ignores `otel` in project-local `.codex/config.toml`, so telemetry cannot be
made repository-specific simply by checking an OTel block into each project.

```toml
[otel]
environment = "prod"
log_user_prompt = false
# Logs/events, traces, and metrics have separate exporter settings.
# Configure only signals whose endpoint and credential have been tested.
```

Important differences from the verified Claude Code path:

1. **No documented equivalent of `otelHeadersHelper`.** Exporter headers can
   interpolate environment variables, but there is no documented periodic
   credential-refresh hook. A wrapper can fetch a token before launch; rotation
   during a long-lived process remains a risk.
2. **No documented per-repository OTel layer.** User-level telemetry config is
   global, while project-local config cannot set `otel`.
3. **Do not assume metric parity.** Claude Code's eight named metrics and
   dimensions are not a contract for Codex output.

The repository includes `scripts/codex_otel.sh`, which fetches the token and
derives `project` and `repo` from git before launch:

```bash
export CW_METRICS_TOKEN="$token"
export OTEL_RESOURCE_ATTRIBUTES="tool=codex,project=$project,repo=$repo"
exec codex "$@"
```

The current Codex manual does not establish that this
`OTEL_RESOURCE_ATTRIBUTES` value becomes queryable project/repository
dimensions in Codex metrics. Until a destination query proves those labels
arrived, this is an **implementation hypothesis**, not a completed result.

Source: the current Codex manual sections on
[project configuration](https://learn.chatgpt.com/docs/config-file/config-basic)
and [OpenTelemetry configuration](https://learn.chatgpt.com/docs/config-file/config-reference).

Portability lesson from the wrapper: a git-remote expression using a lazy
quantifier in `sed -E` worked on Linux and silently returned an empty result on
macOS because BSD `sed` does not support that quantifier. Parameter expansion
plus `awk -F'[:/]'` is portable across the two environments.

### Acceptance test for the Codex path

Do not call the path complete until one short Codex session produces:

1. at least one Codex event or metric on the intended signal endpoint;
2. an authenticated request with no exporter error;
3. queryable `tool=codex`, `project`, and `repo` dimensions—or a documented
   alternative attribution mechanism;
4. redacted prompt content; and
5. a successful repeat after credential rotation, if long-lived sessions are
   in scope.

## 5. Why this matters to an engineering organization

Platform teams ask three questions about coding-agent spend: **how much, by
whom, and on what?** The third turns a bill into a portfolio decision. Cost per
repository shows which codebases are receiving agent investment and creates a
join point for delivery outcomes such as merged changes, cycle time, or
incidents.

The general pattern extends beyond coding agents: **the tool reports its own
world; the organization must add organizational context.** Put stable context
in a project or managed configuration layer, then prove it survived ingestion.

## Evidence and limits

- **Vendor-documented:** Claude Code metrics, standard attributes, resource
  attributes, dynamic-header refresh, and per-signal endpoints; Codex
  user-level OTel configuration, separate exporter controls, environment
  interpolation, and rejection of project-local `otel`.
- **Repository-backed:** `scripts/otel_headers.sh`,
  `scripts/codex_otel.sh`, `src/observability/coding_source.py`, and the
  console's `BUILD_TELEMETRY_SETUP` guidance.
- **Observed in this project:** the wrong AWS profile, empty headers, endpoint
  mismatch, and macOS `sed` failure.
- **Not yet proven:** Codex per-repository labels at the CloudWatch
  destination. Keep this visible as an acceptance item.

## Put this in the presentation

**Slide headline:** Telemetry configuration is not telemetry evidence.

- The tool reports its own identity; the organization must add codebase and
  team context.
- Credentials, signal type, and OTLP endpoint must agree.
- Verify labels at the destination before using them for cost allocation.

**Visual:** Agent telemetry → authenticated OTLP signal → CloudWatch query.
Show green checks for the Claude Code path and an explicit “validate repository
labels” gate on the Codex path.
