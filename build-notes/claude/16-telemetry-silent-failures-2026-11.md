# Coding-agent telemetry: four silent failures, and how to fix them anywhere

*2026-11-02. Found when the cost sentinel (WS12/D44) reported "telemetry is
stale, not quiet — harvest has been blocked since 2026-09-09". Written as a
**portable checklist**: the same pipeline (Claude Code / Codex → CloudWatch
OTLP → harvest → store → dashboard) was copied into another project and AWS
account, and every item below has to be checked there too. Names in `<angle
brackets>` are that project's equivalents of this lab's; no account ids appear
here by design (CLAUDE.md).*

The shape of all four: **each component was built to degrade quietly instead of
breaking a developer's session, and every one of them did.** Nothing errored.
The only symptom was an empty table, noticed weeks later by a downstream agent.

| # | Failure | What it looked like | Signal it hid |
|---|---|---|---|
| 1 | Metrics API key expired | harvest `blocked: no claude_code.* metrics` | cost / tokens (WS9) |
| 2 | Expiry report never watched that key | expiry report "0 expired" | the warning for #1 |
| 3 | Claude Code renamed `event.name` | logs harvest `blocked`, logs present | behaviour (WS16) |
| 4 | Corporate settings turned content logging ON | harvest said "(content off)" | privacy guarantee (D59) |

---

## 1. The CloudWatch metrics API key expired

**What happened.** The OTLP metrics bearer token is an IAM *service-specific
credential* for `cloudwatch.amazonaws.com` on a dedicated IAM user. The first
key was minted with a 90-day expiry (2026-07-26 → 2026-10-24). The header helper
(`scripts/otel_headers.sh`) kept returning `Authorization: Bearer …` — the
secret was readable, the token inside it was dead — and CloudWatch answered
every export with `403 Authentication failed: invalid API key`, which no
terminal shows.

**Check** (in the other account):
```sh
aws iam list-service-specific-credentials --user-name <metrics-iam-user> \
  --query 'ServiceSpecificCredentials[].[ServiceName,Status,CreateDate,ExpirationDate]' --output text
# and prove the stored token directly — 400 = authenticated (empty body rejected), 403 = dead key
tok=$(bash <path>/otel_headers.sh | python3 -c 'import json,sys;print(json.load(sys.stdin).get("Authorization",""))')
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://monitoring.<region>.amazonaws.com/v1/metrics \
  -H "Authorization: $tok" -H 'Content-Type: application/x-protobuf' --data-binary ''
```

**Fix.** Copy `scripts/rotate_cw_metrics_key.sh` and point its env vars at the
other project (`A2ALAB_CW_METRICS_USER`, `A2ALAB_CW_METRICS_SECRET`,
`A2ALAB_CW_METRICS_REGION`, plus whatever account preflight that project uses).
It mints the key, writes it straight into Secrets Manager without printing it,
verifies through the same helper the exporters use, and only then deletes the
old key. Two things learned running it:

- **A new key is not accepted immediately.** The first probe got 403; the same
  key returned 400 a minute later. The script now retries for ~90s.
- **Don't re-run after a failed verify.** IAM allows two keys per user per
  service and each run mints one; re-probe, then delete the old key by id.
- **Expiry is not an AWS rule.** `--credential-age-days` is Bedrock-only, and a
  CloudWatch key minted via the CLI reports *no* expiration. The 90 days came
  from how the first key was created (most likely the console). Check how the
  other project's key was made — it may have the same 90-day clock.

After rotating: restart the agents from their wrappers (Claude Code re-runs
`otelHeadersHelper` ~every 29 min anyway), work a few minutes, re-harvest.
**Metrics not accepted while the key was dead are gone** — telemetry is not
retroactive.

Codex shares the key (`scripts/codex_otel.sh` reads the same secret), so it went
dark on the same day. Note too that on a corporate laptop `~/.codex/config.toml`
may carry a managed `[otel]` block (here: `# BEGIN MHTC Codex otel`, exporting
logs/traces to a local collector at `127.0.0.1:4319`) — a plain `codex` then
never reaches CloudWatch; only the wrapper, which injects the exporter at
launch, does.

## 2. The expiry report did not watch the key

**What happened.** `scripts/expiry_report.py` *does* check IAM service-specific
credentials — for the user named in `A2ALAB_CW_METRICS_USER`. The hosted harvest
Lambda that runs the report was never given that variable, and an unset user
returned `[]`: **no entry**, indistinguishable from "nothing to watch". The
hosted report read "0 expired" for nine days after the key died. The Lambda role
already had `iam:ListServiceSpecificCredentials`; only the name was missing.

**Fix (two parts — port both):**
- `deploy/obs/deploy_harvest.sh`: add `"A2ALAB_CW_METRICS_USER"` to the list of
  `.env` keys carried into the harvest secret, then `deploy_harvest.sh --secret`.
- `scripts/expiry_report.py` `aws_service_credentials()`: an unset user now
  returns an `error` entry ("…not set — this key is NOT being watched") instead
  of `[]`. Test: `tests/unit/test_expiry_report.py`.

**Check** in the other project: does its hosted report list the metrics key at
all? If the key is absent from the list, it's #2, whatever its status.

## 3. Claude Code renamed `event.name`; the logs harvest parsed nothing

**What happened.** The behavioural-logs harvest (`coding_logs_source.py`) read
`attributes["event.name"]` and matched it against `claude_code.tool_decision`,
`claude_code.api_request`, … — the shape verified 2026-07-30. By Claude Code
2.1.312 the attribute is **unprefixed** (`tool_decision`); the prefixed name
appears only in `body`. The code took the attribute first (`event.name or
body`), so the `body` fallback never ran: **1,313 events in the window, 0
parsed**, status `blocked: no claude_code.* log events`, with the logs sitting
in the log group the whole time.

**Fix** — accept either field, either form, and normalise:
```python
EVENT_PREFIX = "claude_code."
event = None
for candidate in (attrs.get("event.name"), rec.get("body")):
    if not isinstance(candidate, str):
        continue
    name = candidate if candidate.startswith(EVENT_PREFIX) else EVENT_PREFIX + candidate
    if name in EVENT_NAMES:
        event = name
        break
```
Test: `test_parse_accepts_unprefixed_event_name_from_current_claude_code`.

**Recovery.** Unlike metrics, these logs were never lost — just unread. With no
retention limit on the group, a one-off wider harvest backfills them (rows are
upserted per day, never deleted, so later 7-day runs leave them alone):
```sh
A2ALAB_OBS_STORE=postgres A2ALAB_PG_SECRET_ARN="$<writer-secret-arn>" \
A2ALAB_CODING_LOGS_WINDOW_DAYS=30 uv run python scripts/obs_harvest.py coding-logs
```
(The default `.env` Postgres secret is the read-only role — a backfill needs the
writer.) Here: 574 events across 4 days recovered. Then redeploy the harvest
code (`deploy/obs/build_zips.sh && deploy/obs/deploy_harvest.sh --code`); its
post-deploy invoke is synchronous and prints nothing for several minutes — not a
hang.

## 4. Corporate user settings turned content logging ON

**What happened.** D59 promised behaviour, not content: the wrapper
(`scripts/claude_otel.sh`) set no `OTEL_LOG_*` flag and relied on them
defaulting off. The corporate Claude Code rollout writes into the **user**
`~/.claude/settings.json`:
```json
"OTEL_LOG_USER_PROMPTS": "1", "OTEL_LOG_TOOL_CONTENT": "1", "OTEL_LOG_TOOL_DETAILS": "1"
```
Claude Code merges `env` across settings layers (user < project < local <
managed), so those applied to this project, and the wrapper sent the result to
the lab's log group. Every exported day from 2026-10-14 carried **prompt text,
assistant responses and tool arguments/content**, while the harvest labelled
each run "(content off)". The store held only aggregates; the raw log group
(no retention limit) held the text.

**Check** in the other project:
```sh
grep -n OTEL_LOG_ ~/.claude/settings.json <project>/.claude/settings*.json
```

**Fix (three parts — port all three):**
1. **Pin the flags in the project's `.claude/settings.local.json`** `env` (local
   outranks user):
   ```json
   "OTEL_LOG_USER_PROMPTS": "0", "OTEL_LOG_TOOL_CONTENT": "0", "OTEL_LOG_TOOL_DETAILS": "0"
   ```
   Trade-off: env is process-wide, so the corporate telemetry pipeline also stops
   receiving content from sessions in that project.
2. **Wrapper guard.** `scripts/claude_otel.sh` now resolves every `OTEL_LOG_*`
   flag across launch env → user → project → local → managed settings (last
   writer wins) and, if any is truthy, prints which file set it and starts
   Claude **without** the logs exporter. Copy that block as-is; it only needs
   `REPO`.
3. **Measure, don't label.** `coding_logs_source.py` checks *every* record
   (including `assistant_response`, which the aggregates ignore) for `prompt`
   (other than `<REDACTED>`), `response`, `tool_input`, `tool_parameters`,
   `tool_output`, `content`, and reports `CONTENT PRESENT in N event(s)` instead
   of a fixed "(content off)". Tests: `test_harvest_reports_content_when_records_carry_it`,
   `test_redacted_prompt_is_not_content`.

**Purge what already shipped.** Backfill the aggregates first (§3), then delete
the content-bearing stream — the wrapper recreates it empty at next launch:
```sh
aws logs delete-log-stream --region <region> --log-group-name <log-group> --log-stream-name claude-code
```

---

## Order to run it in the other project

1. §4 check + settings pin — stop new content shipping first.
2. §1 check; rotate if 403.
3. §3 parser patch → tests → backfill with a wide window → redeploy harvest code.
4. §4 purge the stream (after the backfill — it is the only raw copy).
5. §2 carry the metrics-user env var into the hosted report; redeploy secret.
6. Port the §4 wrapper guard and harvest content check.
7. Verify: work a few minutes from the wrapper, then harvest `coding` and
   `coding-logs` — both `ok`, the latter saying `(content off)` *because it
   measured none*.

## The general lesson

Each fallback was locally reasonable — a missing token must not break a session;
an unknown record is skipped, not fatal; an unset watch has nothing to report; a
flag we never set is off. Composed, they produced a pipeline that could be fully
broken and fully non-compliant while every status read green. The fixes all
have the same shape: **turn a silence into a status** — a 400-vs-403 probe, an
`error` entry for an unconfigured watch, a parser that accepts the schema it
actually receives, and a content claim that is measured rather than asserted.
Related: D39 (silent degradation + ambient credentials), D44 (the sentinel that
caught it), D59 (and its 2026-11-02 amendment), `plan/10-operations.md`
§"Rotate the coding-agent telemetry key", and `08-coding-agent-telemetry.md`
for the original build and its earlier silent failures.
