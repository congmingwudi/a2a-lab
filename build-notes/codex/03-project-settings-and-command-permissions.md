# Codex project settings and command permissions

Verified 2026-11-09 against installed Codex CLI 0.167.0 and official documentation.

## Engineering takeaway

Written authorization and runtime permissions are separate. Use AGENTS.md for
working instructions, config.toml for execution defaults and tool configuration,
and .rules files for command-prefix permissions outside the sandbox.

## Where configuration lives

| Purpose | Project | Personal/global |
|---|---|---|
| Settings, sandbox defaults, MCP configuration | `.codex/config.toml` | `~/.codex/config.toml` |
| Command allow/prompt/block rules | `.codex/rules/*.rules` | `~/.codex/rules/*.rules` |
| Agent working instructions | `AGENTS.md` | `~/.codex/AGENTS.md` |

Codex does not use Claude Code's `.claude/settings.json` or
`.claude/settings.local.json` for these permissions. There is no documented
`config.local.toml` counterpart. Keep personal settings in the user layer or
gitignore project-local files when they should not be shared. A filename such
as `salesforce.local.rules` has no special precedence: it is another rules file.

Configuration precedence is CLI overrides, trusted project configuration
(closest directory wins), selected profile, user configuration, cloud-managed
defaults, system configuration, built-in defaults. Enforced managed requirements
constrain these settings. Untrusted projects skip their `.codex` layer.

## Concrete WeeCycle example

Created in `weecycle-estate/.codex/config.toml`:

```toml
approval_policy = "on-request"
sandbox_mode = "workspace-write"
```

Created in `weecycle-estate/.codex/rules/salesforce.rules`:

```python
prefix_rule(
    pattern = ["sf", "data", "query", "--target-org", "weecycle-sandbox-partial"],
    decision = "allow",
)

prefix_rule(
    pattern = ["sf", "data", "query", "--target-org", "weecycle-prod"],
    decision = "allow",
)

prefix_rule(
    pattern = ["sf", "sobject", "describe"],
    decision = "allow",
)
```

The actual file also contains match/not_match validation examples. Query grants
are scoped to the two aliases with that argument order. Describe is allowed for
any authenticated org. These are command permissions, not record-level filters;
Salesforce user permissions still control data access. No new allow rule covers
record mutations, deployment, sandbox refresh or authentication.

`allow` permits matching commands outside the sandbox without prompting;
`prompt` requests approval; `forbidden` blocks them. The most restrictive
matching rule wins. Rules are loaded at startup: restart Codex with the project
trusted. Saved TUI command approvals go to `~/.codex/rules/default.rules`.

Do not broaden this to `sf`, arbitrary shell wrappers, or Python merely to avoid
prompts. Each segment of a simple pipeline is evaluated separately, so allowing
`sf data query` does not necessarily authorize the entire pipeline. Complex
shell expressions can be treated as one wrapper command.

## Tool permissions

For a configured MCP server, use its actual server ID and exposed tool names:

```toml
[mcp_servers.example]
enabled_tools = ["read_records", "describe_object"]
disabled_tools = ["delete_records"]
```

This is illustrative, not an installed server. Tool exposure and approval are
separate. Current documentation also lists
`mcp_servers.<id>.tools.<tool>.approval_mode`,
`apps.<id>.tools.<tool>.approval_mode`, and equivalent plugin-server settings.
Supported values are `auto`, `prompt`, `writes`, and `approve`; verify the
specific host's behavior before changing approval modes. Managed host policies
may constrain or replace local settings. Local Work Cloud executors should not
be assumed to load Codex's local `.rules` files.

## Validation and troubleshooting

Run from the project containing the rules:

```sh
codex execpolicy check --pretty --rules .codex/rules/salesforce.rules -- sf data query --target-org weecycle-sandbox-partial --query 'SELECT IsSandbox FROM Organization' --json
```

This tests policy matching without querying Salesforce. Test allowed examples
and commands that must not match. A nonmatching command is not necessarily
forbidden: other rules and the sandbox/approval policy still apply.

The WeeCycle files passed TOML parsing, three positive and four negative policy
checks, and `git diff --check`. Runtime loading in a new trusted session remains
unverified. No Salesforce operation ran during policy validation.

The motivating failure was `EPERM` when Salesforce CLI tried to write its local
log under `~/.sf/`. A read-only remote query can still need local writes and
network access. Verbal approval does not itself persist a runtime permission.
Do not promise that verbal consent alone removes platform approval prompts.
`approval_policy = "never"` suppresses approval requests but does not grant
blocked filesystem/network access.

This note does not install these permissions in a2a-lab. The applied example is
in the separate WeeCycle estate; its D25 and WS0 item 14 record the change.

## Evidence and limits

- Local evidence: Codex CLI version, `codex execpolicy check`, existing user
  `rules/default.rules`, and the new WeeCycle project files.
- Rules are experimental. Project rules require trust and restart; managed
  policy can still prevent an action. A standalone policy check does not prove
  the current desktop session has loaded the new rules.
- [Official configuration basics](https://learn.chatgpt.com/docs/config-file/config-basic)
- [Official command rules](https://learn.chatgpt.com/docs/agent-configuration/rules)
- [Official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)

## Put this in the presentation

**Three layers:** AGENTS.md explains how to work; config.toml sets execution
defaults and tool availability; .rules grants precise command exceptions.
Show one scoped Salesforce query rule and its offline policy check.
