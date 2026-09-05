"""Control-plane setup for the cost sentinel (WS12) — a DAILY scheduled agent.

    uv run python scripts/setup_cost_sentinel.py             # create if missing
    uv run python scripts/setup_cost_sentinel.py --recreate  # replace
    uv run python scripts/setup_cost_sentinel.py --run       # + one manual run now

Idempotent (same as the obs analyst): the create path asks the workspace
(agents.list) before creating, so a lost/absent state file no longer spawns a
duplicate — it refuses and points at --recreate, which archives the prior agent
(and its paused deployment) first rather than orphaning it.

Cadence note: WS12/D44 shipped this WEEKLY-and-paused because a scheduled firing
bills a real session. On 2026-07-30 it was moved to DAILY and resumed so the
console surfaces a fresh brief each morning — the cost of that is one billed
session a day, which the operator accepts and can pause from the console's Run
tab or `cost_sentinel.py pause`. The schedule can be changed on a live
deployment (`deployments.update`) without recreating, so this default only
governs a fresh provision or a --recreate.

Reuses the obs MCP server and its vault (D23) rather than deploying a second
server: the numbers it needs already live in `lab.obs_sessions` under
platform='coding', and a second MCP server would be a second thing to deploy,
rotate and reason about. Briefs land in the SAME `lab.obs_briefs` table with
`kind='cost'` — one store, one reader, one migration.

**Why this one IS a Managed Agent when the credential analyst was demoted to a
plain API call.** That analyst failed all three of the tests below; this passes
all three, which is the whole argument (see the postscript in
build-notes/claude/09-secrets-and-environment-identity.md, and D22):

    tools     the numbers are a QUERY, not a paragraph that fits in a prompt.
              "Which repo drove the delta" is SQL against the obs store, and
              the store already has a hosted MCP server.
    schedule  collection is already hosted — coding_source.py runs in the
              6-hourly harvest Lambda against CloudWatch, so unlike credential
              expiry there is NO dependency on the operator's laptop. A cron
              can genuinely run this unattended.
    state     week-over-week comparison needs history, and the store has it.

Created PAUSED, like the obs analyst: a scheduled deployment bills a real
session per firing, so the schedule is opt-in rather than a surprise. (The
2026-07-30 move to daily resumed it deliberately; a fresh --recreate still lands
paused, and resuming is one console click or `cost_sentinel.py resume`.)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

STATE_DIR = Path(os.environ.get("A2ALAB_STATE_DIR", ".a2alab"))
STATE_FILE = STATE_DIR / "cost_sentinel.json"
MANAGED_FILE = STATE_DIR / "managed.json"
MCP_FILE = STATE_DIR / "obs_mcp.json"
ANALYST_FILE = STATE_DIR / "obs_analyst.json"

AGENT_NAME = "A2ALab Cost Sentinel"
MCP_SERVER_NAME = "obs-store"
VAULT_NAME = "A2ALab Obs Store (cost sentinel)"
DEPLOYMENT_NAME = "A2ALab Cost Sentinel Daily"

SYSTEM_PROMPT = """You are the cost sentinel for the A2A Interop Lab — a cross-platform \
agent-to-agent experiment rig built with three coding agents (Claude Code, the OpenAI Codex \
CLI, and Cursor), whose telemetry all reaches CloudWatch and is harvested into the lab's own \
store. Claude Code and Codex ship native OTel exporters; Cursor has none, so its metrics come \
via hooks forwarded to the cursorscope ingestor.

Your input is that store (Aurora Postgres, schema `lab`), reachable only through the \
query_obs_store tool (read-only SQL). The build telemetry lives in `lab.obs_sessions` WHERE \
platform = 'coding': one row per (tool, day), `native_id` shaped '<tool>:<YYYY-MM-DD>', \
`usage_json` carrying input_tokens / output_tokens / cache_read_input_tokens / \
cache_creation_input_tokens / cost_usd_estimated, and `raw_json` carrying tool, sessions, \
active_time_s, and the by_repo and by_model breakdowns as nested jsonb. Your own past briefs \
are in `lab.obs_briefs` WHERE kind = 'cost'. Use jsonb operators (->, ->>) and aggregate in SQL.

WHAT YOU ARE FOR: not reporting the number — the console already shows it — but explaining the \
MOVEMENT. You run DAILY, so lead with the day-over-day: yesterday's total, the delta against the \
day before, and where that sits against the trailing 7-day trend. Then the part only you can \
write: the attribution. "The fan-out experiments ran 40 times yesterday, which is the whole \
delta." "Opus replaced Haiku on the research agent and cost per session tripled." Get that from \
the by_repo and by_model breakdowns, not from the headline. A quiet day is a two-line brief.

RULES that make you useful rather than noisy:

1. NEVER state a number you did not get from a query. Every figure in the brief must trace to \
a query you actually ran. If the data is too thin for a week-over-week comparison, say so \
instead of comparing anyway.
2. LEAD WITH THE CAVEAT, once, briefly. `claude_code.cost.usage` is a CLIENT-SIDE ESTIMATE AT \
LIST PRICE. It is not an invoice, and on a subscription it is not money that changed hands. \
A cost movement is a usage movement; it may not be a billing movement.
3. Only Claude Code publishes a cost metric. Codex and Cursor publish NONE, and their token \
metrics are histograms this pipeline does not consume, so any cross-tool dollar total is Claude \
Code's cost alone, with Codex and Cursor contributing sessions only. Never present a combined \
dollar figure as if it covered all three tools.
4. Tokens are FOUR buckets, not one: uncached input, cache read, cache creation, output. They \
bill at different multiples (cache read ~0.1x, cache write 1.25-2x), so never sum them into one \
"tokens" number and never infer a rate from the sum. When cost moves without token volume \
moving, a shift in the MIX is usually the explanation and is worth naming.
5. SAY PLAINLY WHEN NOTHING HAPPENED. A daily brief that always finds something trains its \
reader to stop reading. "Spend was flat, the mix was unchanged, nothing needs attention" in two \
lines is a correct and valuable brief — and on a daily cadence most days are that.
6. Prefer cost per unit of work over cost in dollars where the data supports it — cost per \
session, tokens per session, cache-read share. Those are engineering numbers that survive a \
price change; the dollar total does not.

Deliver by calling save_brief exactly once with kind='cost' and the complete markdown (under \
~400 words, leading with the verdict), plus your query count. The brief in save_brief IS the \
deliverable — do not only reply with text."""

KICKOFF = """Produce today's build-cost brief for the lab.

Start by establishing the window: query lab.obs_harvest for the 'coding' platform's freshness, \
then the min and max dates present in lab.obs_sessions WHERE platform='coding'. Report against \
what is actually there — if there is only one day, say so rather than inventing a comparison.

Then lead with the day-over-day: the most recent day's modelled cost vs the day before, the \
by_repo and by_model splits for both, the four token buckets and how the mix moved, and sessions \
and active time. Set that against the trailing 7-day trend for context. Explain the delta — name \
the repo, model or day that accounts for it. A flat day is a two-line brief. Finish with \
save_brief (kind='cost')."""


def _setup(client, args) -> dict:
    env_id = json.loads(MANAGED_FILE.read_text())["environment_id"]
    mcp = json.loads(MCP_FILE.read_text())
    if not mcp.get("url"):
        print("no MCP URL in .a2alab/obs_mcp.json — run deploy/obs/expose_mcp.sh first")
        raise SystemExit(1)
    mcp_url = mcp["url"].rstrip("/")

    # Its own vault rather than sharing the analyst's. Same server and the same
    # bearer, but a vault is the revocation unit: pausing or retiring one agent
    # should not require reasoning about what else that vault is attached to.
    vault = client.beta.vaults.create(display_name=VAULT_NAME)
    credential = client.beta.vaults.credentials.create(
        vault_id=vault.id,
        display_name="obs MCP bearer",
        auth={"type": "static_bearer", "mcp_server_url": mcp_url, "token": mcp["token"]},
    )
    print(f"vault: {vault.id} (credential {credential.id})")

    agent = client.beta.agents.create(
        name=AGENT_NAME,
        model=args.model,
        description="Weekly build-cost brief over the lab's coding-agent telemetry (WS12).",
        system=SYSTEM_PROMPT,
        mcp_servers=[{"type": "url", "name": MCP_SERVER_NAME, "url": mcp_url}],
        # always_allow for the same reason as the obs analyst: this workspace
        # evaluates MCP tools as "ask" by default, which deadlocks an
        # unattended scheduled run — no client is connected to confirm.
        tools=[
            {
                "type": "mcp_toolset",
                "mcp_server_name": MCP_SERVER_NAME,
                "default_config": {
                    "enabled": True,
                    "permission_policy": {"type": "always_allow"},
                },
            }
        ],
    )
    print(f"agent: {agent.id} v{agent.version} (model {args.model})")

    deployment = client.beta.deployments.create(
        name=DEPLOYMENT_NAME,
        agent=agent.id,
        environment_id=env_id,
        vault_ids=[vault.id],
        initial_events=[{"type": "user.message", "content": [{"type": "text", "text": KICKOFF}]}],
        schedule={"type": "cron", "expression": args.cron, "timezone": args.timezone},
    )
    client.beta.deployments.pause(deployment.id)
    print(f"deployment: {deployment.id} (cron '{args.cron}' {args.timezone}) — created PAUSED")
    upcoming = getattr(getattr(deployment, "schedule", None), "upcoming_runs_at", None)
    if upcoming:
        print(f"  would next fire: {', '.join(str(u) for u in upcoming[:3])}")
    print("fire ad-hoc: uv run python scripts/cost_sentinel.py run")
    print("enable the schedule: uv run python scripts/cost_sentinel.py resume")

    return {
        "mode": "hosted",
        "agent_name": AGENT_NAME,
        "agent_id": agent.id,
        "agent_version": agent.version,
        "environment_id": env_id,
        "vault_id": vault.id,
        "credential_id": credential.id,
        "deployment_id": deployment.id,
        "mcp_url": mcp_url,
        "model": args.model,
        "cron": args.cron,
        "timezone": args.timezone,
        "brief_kind": "cost",
    }


def _agents_named(client, name: str) -> list:
    """Live (non-archived) agents whose display name is exactly ``name``.

    The idempotency backstop, same as the obs analyst's: STATE_FILE lives only
    on the operator's laptop, so its absence does NOT mean the agent is absent
    — a fresh checkout, a deleted .a2alab/, or a second machine would otherwise
    spawn a DUPLICATE. Ask the workspace by name instead. Archived agents keep
    their name but are tombstones, so skip anything the API marks archived
    (field name varies across SDK versions; check both).
    """
    out = []
    for agent in client.beta.agents.list():
        if getattr(agent, "name", None) != name:
            continue
        if getattr(agent, "archived_at", None) or getattr(agent, "archived", None):
            continue
        out.append(agent)
    return out


def _manual_run(client) -> None:
    state = json.loads(STATE_FILE.read_text())
    deployment_id = state["deployment_id"]
    client.beta.deployments.run(deployment_id)
    print("manual run fired — waiting for the deployment run to record a session...")
    for _ in range(30):
        time.sleep(5)
        for run in client.beta.deployment_runs.list(deployment_id=deployment_id):
            if run.session_id:
                print(f"session: {run.session_id}")
                print(
                    "watch: https://platform.claude.com/workspaces/default/sessions/"
                    f"{run.session_id}"
                )
                print("brief lands in lab.obs_briefs (kind='cost') when the session finishes")
                return
            if getattr(run, "error", None):
                print(f"run failed: {run.error.type}: {run.error.message}")
                return
    print("no session recorded yet — check scripts/cost_sentinel.py status later")


def main() -> int:
    load_dotenv()
    from anthropic import Anthropic

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--recreate", action="store_true")
    parser.add_argument("--run", action="store_true", help="fire one brief after setup")
    parser.add_argument(
        "--model", default=os.environ.get("A2ALAB_SENTINEL_MODEL") or "claude-sonnet-5"
    )
    # Monday morning: the week it reports on is closed, and a person is
    # plausibly about to look at it.
    parser.add_argument("--cron", default="0 7 * * 1")
    parser.add_argument("--timezone", default="America/New_York")
    args = parser.parse_args()

    client = Anthropic()
    if STATE_FILE.exists() and not args.recreate:
        print(f"already provisioned ({STATE_FILE}) — use --recreate to replace")
    else:
        if not MANAGED_FILE.exists():
            print("no .a2alab/managed.json — run scripts/setup_managed_agent.py first")
            return 1
        if not MCP_FILE.exists():
            print("no .a2alab/obs_mcp.json — run deploy/obs/expose_mcp.sh first")
            return 1
        # Idempotency backstop (same as the obs analyst): never create a second
        # agent named AGENT_NAME. STATE_FILE is laptop-local, so its absence
        # does NOT mean the agent is absent — ask the workspace before creating.
        existing = _agents_named(client, AGENT_NAME)
        if existing and not args.recreate:
            ids = ", ".join(a.id for a in existing)
            print(
                f"an agent named {AGENT_NAME!r} already exists ({ids}) but {STATE_FILE} is "
                f"missing — refusing to create a duplicate. Recover the ids into {STATE_FILE}, "
                f"or re-run with --recreate to archive it and provision a fresh one."
            )
            return 1
        if args.recreate:
            # Clean up the prior fleet BEFORE creating, so --recreate replaces
            # rather than orphans: the old deployment (a paused schedule) from
            # the outgoing STATE_FILE, and every same-named agent from the
            # workspace (catching any orphan a previous non-idempotent run
            # already stranded).
            if STATE_FILE.exists():
                old = json.loads(STATE_FILE.read_text())
                old_dep = old.get("deployment_id")
                if old_dep:
                    client.beta.deployments.archive(old_dep)
                    print(f"archived prior deployment {old_dep}")
            for agent in existing:
                client.beta.agents.archive(agent.id)
                print(f"archived prior '{AGENT_NAME}' agent {agent.id}")
        state = _setup(client, args)
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(state, indent=1))
        print(f"ids -> {STATE_FILE}")

    if args.run:
        _manual_run(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
