# Installing and loading skills for Codex and Claude

Updated 2026-11-09.

## Engineering takeaway

Ask the agent to install skills by supplying their location, intended agents,
and installation scope. Treat installation on disk and discovery by a running
session as separate results. Start a new Codex session after installation and
verify discovery instead of assuming the next turn will load everything.

## Asking for an installation

A reusable request:

> Install the skills from <URL, repository, or local folder> at user level for
> both Codex and Claude Code. Preserve existing skills, check name conflicts,
> and tell me what was installed, where it lives, how updates work, and whether
> I need a new session to use it.

For one project instead:

> Install <skill names> from <location> for Codex and Claude Code in this
> project only. Preserve the project's existing skills and workflow.

A link to a guide may point to a separate source repository. The agent should
follow that link, read the installation instructions, identify the intended
skills, and use the appropriate installer. A local folder can be used as the
source when the complete skill files are available there. Do not treat a web
article alone as an installed skill.

Specify selected skills or the complete published set. Experimental or
in-progress folders should not silently expand the requested scope.

## Scope and locations

| Scope | Codex location used here | Claude Code location used here |
|---|---|---|
| User: available across projects | `~/.codex/skills/<name>/` | `~/.claude/skills/<name>/` |
| Project: belongs to one repository | `.agents/skills/<name>/` | `.claude/skills/<name>/` |

These are the filesystem layouts used in this environment, not an exhaustive
list of supported discovery locations. Plugin-managed installations may use
separate caches and registration instead. Follow the selected installation
method rather than installing duplicate copies through multiple methods.

For compatible file-based skills, one canonical copy can serve both agents
through symlinks. The user-level pattern used here is:

```text
~/.codex/skills/<name>/                 canonical files
~/.claude/skills/<name>                 symlink to the canonical directory
```

At project level, this estate already uses `skills/<name>/` as a canonical
source with links from both agent directories. Check compatibility and existing
conventions before sharing files. Installing a skill does not grant shell,
network, cloud, or tool permissions; those remain separately configured.

## What the agent should verify

1. Read the source instructions and determine the requested scope and agents.
2. Check existing names, including project skills that may shadow user skills.
   Preserve existing content and resolve conflicts explicitly.
3. Install the full skill directory, including referenced scripts, templates,
   and other required files. Record the source and revision when available.
4. Verify `SKILL.md` files and any links, then report the exact destinations.
5. State whether updates are manual or managed by the selected installer or
   plugin. A pinned copy does not update itself.
6. Report session discovery separately from filesystem verification.

Repository-specific setup is a separate step when a skill requires it. Global
installation does not choose every project's tracker, labels, documentation
layout, or workflow. Follow the skill's setup instructions in the intended repo.

## Starting a new session

**Operational practice: start a new Codex session after installing skills.**
In the new session, check that the expected skills can be selected or invoked.
Restart Claude Code as well when refreshing its installed skill set.

Do not promise that skills will be available on the next turn merely because
files were copied successfully. Some discovery may refresh during a session,
but that is not sufficient evidence that the complete set is available.

Use precise status wording:

- **Installed:** the files exist at the intended paths.
- **Linked:** any shared-directory links resolve correctly.
- **Discovered:** the intended agent/session exposes the skills through its
  supported skill or command interface.
- **Configured:** any required repository-specific setup is complete.

If discovery has not been checked, say so. A skill missing from the model's
injected catalog may still be user-invoked through a separate command picker;
check the relevant interface before concluding that installation failed.

If a fresh session still cannot find a skill, check its directory, `SKILL.md`
metadata, link target, name collisions, scope, and the installed agent version's
supported discovery mechanism. Do not repeatedly reinstall without identifying
which layer failed.

## Evidence and limits

During a skill installation on 2026-11-09, files and shared links were verified,
but the next turn's injected catalog exposed only part of the new set. The
original statement that the skills would be available next turn was too definite.
The user also reported a previous instance where that advice had failed.

Starting a new session is the recommended practice recorded here; full discovery
in a fresh session was not observed in that exchange. This note does not claim
that every agent version always requires a restart or that restarting alone
fixes every discovery problem.

The separate [permissions note](03-project-settings-and-command-permissions.md)
explains command rules and tool access. Skill installation does not replace them.

## Put this in the presentation

**Installed is not loaded.** Supply a source, choose agents and scope, verify
files and links, then start a new session and verify discovery. Keep installation,
loading, repository setup, and permission grants distinct.
