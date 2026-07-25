# Calling the Lab Guide over MCP

The Lab Guide — the console's docent — is also a lab agent served over
REST, MCP, and A2A. Connect any MCP client (Claude Code, Claude Desktop,
or your own) and you can ask it about the lab from anywhere, or skip the
docent and read the lab's records directly with its raw tools.

## What you need

- **The endpoint** — streamable-http:
  - Local stack: `http://localhost:8032/mcp`
  - Public lab: `https://<lab-host>/mcp` (the tunnel hostname the operator
    shares with you)
- **A credential** — one of:
  - the lab token (`X-Lab-Token: <token>`) — the service credential, ask
    the lab operator;
  - or a persona JWT from the console — sign in, then send it as
    `Authorization: Bearer <jwt>` (note: it expires after ~8 hours, so
    the lab token is the better fit for a saved config).

## Claude Code (one command)

```sh
claude mcp add --transport http lab-guide http://localhost:8032/mcp \
  --header "X-Lab-Token: <your-lab-token>"
```

Then just ask in any session: *"Ask the lab guide how the delegation
guard works"* — or call the raw tools directly: *"use lab-guide's
get_trace on the latest run"*.

## Claude Desktop

Add to `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "a2a-lab-guide": {
      "command": "npx",
      "args": [
        "mcp-remote", "http://localhost:8032/mcp",
        "--header", "X-Lab-Token: <your-lab-token>"
      ]
    }
  }
}
```

Restart Claude Desktop; the guide's tools appear under **a2a-lab-guide**.

## What you get — two integration shapes, on purpose

| Tool | Who reasons | What it does |
|---|---|---|
| `ask` | **the lab's model** | Runs the whole guide loop (docs + ADRs + briefs + traces) and returns one grounded, cited answer |
| `get_decision` | your model | One ADR by id (e.g. `D27`) |
| `read_doc` | your model | A whitelisted lab doc in full |
| `list_recent_runs` | your model | Recent lab runs: trace ids, targets, protocols |
| `get_trace` | your model | One run's full hop list from the wire record |
| `list_briefs` / `read_brief` | your model | The hosted analyst's findings briefs |

Ask the same question through `ask` and again by having YOUR model read
the raw tools — the difference between the two answers is itself the
exhibit: whose model reasons over the lab's data?

## Troubleshooting

- **401** — missing or wrong credential; check the header spelling
  (`X-Lab-Token`) and the token value.
- **Connection refused (local)** — the guide server isn't running; start
  the stack (`scripts/run_local.sh`) or just the guide
  (`uv run python -m platforms.guide --protocol mcp --port 8032`).
- **Works locally, not remotely** — the public hostname only exists while
  the lab's tunnel is up; ask the operator.
