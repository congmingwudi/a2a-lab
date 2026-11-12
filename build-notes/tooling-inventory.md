# Project tooling inventory — plugins, skills, and MCP servers

Last verified: 2026-11-12

This is the durable inventory for external coding-agent tooling used to build and operate the
A2A Interop Lab. It records ownership and reproducibility boundaries; it is not an inventory of
the lab's runtime platform dependencies.

## Salesforce CLI plugins

No community Salesforce CLI plugins are installed. `sf` runs on its bundled Node.

### Retired 2026-11-12: community `sf data360` runtime

| Item | Value |
|---|---|
| CLI package | `@gthoppae/sf-cli-plugin-data360` 0.0.7 (unlinked with `sf plugins unlink`) |
| Source | <https://github.com/Jaganpro/sf-cli-plugin-data360>: not archived, but no push since 2026-03-26 |
| Local checkout | `~/.sf-community-tools/datacloud/sf-cli-plugin-data360` (left on disk, no longer linked) |
| Why retired | It served only the archived Data 360 phase skills below. It was community-maintained, and it needed a Node override to run at all. |

Its JWT dependency (`buffer-equal-constant-time`) calls a Node API that Node 26 removed. To run
it, a custom runtime slot `~/.local/share/oclif/node/node-custom` pointed `sf` at Node 24.22.1.
That symlink was removed along with the plugin. After the removal, `sf --version` reports
`@salesforce/cli/2.157.8 darwin-arm64 node-v26.13.0` and `sf data360` is no longer a command.

**Follow-up:** the memory probe's identity resolution and real-time data graph
(`config/agentic_memory_probe_{identity_resolution,customer_graph}.json`) were first created with
`sf data360 identity-resolution create` / `data-graph create` (`plan/09-deployment-map.md`, the
`setup_agentic_memory.py` row). A rebuild must send those bodies through the SSOT REST API
(`sf api request rest`) instead. The objects already exist in the org, so nothing currently running
depends on the CLI. To restore it temporarily: `sf plugins link .` in the checkout, and recreate the
Node 24 override.

## Salesforce skills

The active public library is <https://github.com/forcedotcom/sf-skills>.
`github.com/forcedotcom/afv-library` redirects to the same repository. The older community
<https://github.com/Jaganpro/sf-skills> is archived and points there. A browsable catalog is
at <https://www.skills.sh/forcedotcom/sf-skills>.

### Data 360 skills: what remains

- **Kept, still shipped by forcedotcom:** `data360-schema-get` (DLO/DMO metadata over SSOT
  REST) and `data360-code-extension-generate`.
- **Archived 2026-11-12:** `data360-orchestrate`, `-connect`, `-prepare`, `-harmonize`,
  `-segment`, `-activate` and `-query`. These were renamed forks of the archived Jaganpro
  `sf-datacloud-*` family, and forcedotcom removed all seven from `sf-skills` on 2026-08-25
  (commit `0851d45f`, "Removing 7 data360-* skills", W-23980880). With no upstream left, they
  were moved to `~/.codex/skills-archived/` and their `~/.claude/skills` links were removed.
  Nothing was deleted. To restore one, move it back and re-create its link.
- For Data 360 work, use the lab's own SSOT REST scripts (`scripts/setup_agentic_memory.py`,
  `scripts/graph_context_probe.py`), the `salesforce-dx` MCP server, and `sf api request rest`.

### User-level installation for Codex and Claude Code

On 2026-11-11, all 74 Salesforce skill directories (67 after the 2026-11-12 Data 360 archive) that had been copied directly into this
project were migrated to user scope. The project-local copies were removed after byte-for-byte
comparison; `.agents/skills/handoff` remains because it is a project-specific tracked symlink.

| Consumer | User-level location | Role |
|---|---|---|
| Codex | `~/.codex/skills/<skill>/` | canonical skill files |
| Claude Code | `~/.claude/skills/<skill>` | symlink to `../../.codex/skills/<skill>` |

For example:

```text
~/.codex/skills/data360-schema-get/
~/.claude/skills/data360-schema-get
  -> ../../.codex/skills/data360-schema-get
```

This makes one canonical copy available across projects while preventing the two agents from
drifting onto different skill versions. The installed snapshot was migrated from the complete
project-local directories, including scripts, references and assets. That source was not a Git
checkout and did not retain an upstream revision, so updates are manual: obtain a reviewed
snapshot from `forcedotcom/sf-skills`, replace the canonical Codex directories, and verify the
Claude links. Restart both Codex and Claude Code after installation or updates; filesystem
verification does not prove that an already-running session has refreshed its skill catalog.

## MCP servers

The checked-in `.mcp.json` is the source of truth for project MCP configuration. It currently
defines one server:

| Server | Package | Toolsets | Purpose |
|---|---|---|---|
| `salesforce-dx` | `@salesforce/mcp` via `npx -y` | `orgs,metadata,data,testing,users` | org access, metadata operations, data operations, Apex tests, and users |

It targets `DEFAULT_TARGET_ORG`; each operator's authenticated Salesforce CLI state resolves the
actual org. Raw `sf` and SSOT REST remain documented fallbacks where the MCP surface lacks a Data
360 operation.

Session-provided connectors that are not declared in `.mcp.json` are environment capabilities,
not project dependencies. Add a row here and checked-in configuration when one becomes required
for a repeatable project workflow.

## Maintenance checklist

1. Run `sf plugins` and confirm no unexpected community plugins are linked.
2. Confirm `~/.claude/skills/<skill>` links resolve to `~/.codex/skills/<skill>`, with no dangling links:
   `find ~/.claude/skills -maxdepth 1 -type l ! -exec test -e {} \; -print`.
3. Compare the installed skills with the active upstream. A local skill that upstream no
   longer ships is a candidate for `~/.codex/skills-archived/`.
4. Confirm `.mcp.json` matches the MCP rows above.
5. When skill sources move or are renamed, update both the public links and local paths here.
