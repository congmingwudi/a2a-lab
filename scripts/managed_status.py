"""One-command view of the lab's Anthropic Managed Agents resources — the
server-side counterpart to .a2alab/managed.json, since the beta is API-first
and claude.ai/the Console may not surface these resources.

    uv run python scripts/managed_status.py              # definition + recent sessions
    uv run python scripts/managed_status.py --sessions 20

Needs ANTHROPIC_API_KEY.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

from platforms.claude.managed_backend import STATE_FILE, load_managed_ids


def _fmt_ts(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return str(value or "?")


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--sessions", type=int, default=10, help="recent sessions to list")
    args = parser.parse_args()

    from anthropic import Anthropic

    client = Anthropic()
    agent_id, env_id = load_managed_ids()

    agent = client.beta.agents.retrieve(agent_id)
    model = getattr(agent.model, "id", agent.model)
    tools = [getattr(t, "name", None) or getattr(t, "type", "?") for t in (agent.tools or [])]
    print("agent")
    print(f"  id:          {agent.id}  (version {agent.version})")
    print(f"  name:        {agent.name}")
    print(f"  model:       {model}")
    print(f"  tools:       {', '.join(tools)}")
    system = (agent.system or "").replace("\n", " ")
    print(f"  system:      {system[:140]}{'…' if len(system) > 140 else ''}")

    env = client.beta.environments.retrieve(env_id)
    print("environment")
    print(f"  id:          {env.id}")
    print(f"  name:        {env.name}")

    print(f"recent sessions (newest {args.sessions})")
    try:
        sessions = client.beta.sessions.list(limit=args.sessions)
        rows = list(getattr(sessions, "data", sessions) or [])
    except Exception as exc:
        print(f"  (could not list sessions: {type(exc).__name__}: {exc})")
        return
    ours = [s for s in rows if getattr(getattr(s, "agent", None), "id", None) == agent_id]
    if not ours:
        print("  none")
    for s in ours:
        title = getattr(s, "title", "") or ""
        status = getattr(s, "status", "?")
        created = _fmt_ts(getattr(s, "created_at", None))
        usage = getattr(s, "usage", None)
        tokens = (
            f"in {getattr(usage, 'input_tokens', 0)} / out {getattr(usage, 'output_tokens', 0)}"
            if usage
            else ""
        )
        print(f"  {s.id}  {created}  {status:<10} {title:<24} {tokens}")

    if STATE_FILE.exists():
        print(f"local state: {STATE_FILE} -> {json.loads(STATE_FILE.read_text())}")


if __name__ == "__main__":
    main()
