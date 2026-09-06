#!/usr/bin/env python3
"""The baton between coding agents (D79, plan/16-joint-agent-workflow.md).

One JSON file per batch under build-notes/handoffs/<batch>.json records which
station the batch is at, who holds it, the round, and the files each side has
written. Every agent runs the SAME script under its own name, so "whose move
is it, and what do I read" is a fact in git instead of a prompt pasted
between terminals. Stdlib only — every agent's environment can run it.

Stations (D79):  propose → critique → settle → implement → review ⇄ respond
                 → deploy → done

    scripts/handoff.py status [batch] --agent <me>
    scripts/handoff.py propose <batch> --agent <me> --title T --file <plan.md>
    scripts/handoff.py review  <batch> --agent <me> --verdict pass|request-changes --file <verdict.md>
    scripts/handoff.py settle  <batch> --adr D<n> --implementer <agent>        (operator)
    scripts/handoff.py built   <batch> --agent <me> --file <notes.md> --commit <sha>
    scripts/handoff.py respond <batch> --agent <me> --commit <sha>
    scripts/handoff.py deployed <batch> --note "..."                          (operator)

Exit 2 on an illegal move (wrong holder, wrong station, missing verdict line).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

# Add a row to register another coding agent. `reviews` is where that agent
# writes its verdicts and implementer notes; the skill it loads tells it its
# own name. Keep the names lowercase — they are CLI arguments.
AGENTS: dict[str, dict[str, str]] = {
    "claude": {"reviews": "build-notes/claude/reviews"},
    "codex": {"reviews": "build-notes/codex/reviews"},
    "cursor": {"reviews": "build-notes/cursor/reviews"},
    "kiro": {"reviews": "build-notes/kiro/reviews"},
}
OPERATOR = "operator"
STATE_DIR = "build-notes/handoffs"
VERDICT_RE = "**Verdict:"

NEXT_ACTION = {
    "critique": "critique the plan: verify every claim against source, write corrections "
    "with file#L cites to {target}, then `handoff review {batch} --agent {me} "
    "--verdict pass|request-changes --file <that file>`",
    "settle": "OPERATOR decides scope and records the ADR, then `handoff settle {batch} "
    "--adr D<n> --implementer <agent>`",
    "implement": "implement test-first on branch {batch}-<slug>; write implementer notes "
    "(baseline, change table, deploy runbook) to {target}; then `handoff built "
    "{batch} --agent {me} --file <that file> --commit <sha>`",
    "review": "cross-review: read {their_file}, rerun YOUR OWN evidence, write a verdict "
    "whose first line is `**Verdict: PASS**` or `**Verdict: REQUEST CHANGES**` to "
    "{target} (append a `## Round {round}` section if it exists), then "
    "`handoff review {batch} --agent {me} --verdict ... --file <that file>`",
    "respond": "answer the verdict at {their_file}: verify each finding against source, "
    "fix test-first, append a `## Round {next_round}` table (finding | fixed/disputed/"
    "deferred to D<n> | what changed) to {my_file}, commit, then `handoff respond {batch} "
    "--agent {me} --commit <sha>`",
    "deploy": "OPERATOR deploys per the runbook in {my_file} and records smoke/scan "
    'results there, then `handoff deployed {batch} --note "..."`',
    "done": "nothing — flip the WS item lines to ✅ in plan/07 if not already done",
}


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _die(msg: str) -> None:
    print(f"handoff: {msg}", file=sys.stderr)
    sys.exit(2)


def _other(agent: str, state: dict) -> str:
    pair = [state["proposer"], state["critic"]]
    return pair[1] if agent == pair[0] else pair[0]


class Store:
    def __init__(self, root: Path):
        self.root = root
        self.dir = root / STATE_DIR

    def path(self, batch: str) -> Path:
        return self.dir / f"{batch}.json"

    def load(self, batch: str) -> dict:
        p = self.path(batch)
        if not p.exists():
            _die(f"no such batch '{batch}' (known: {', '.join(b for b in self.batches())})")
        return json.loads(p.read_text())

    def save(self, state: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path(state["batch"]).write_text(json.dumps(state, indent=2) + "\n")

    def batches(self) -> list[str]:
        return sorted(p.stem for p in self.dir.glob("*.json")) if self.dir.exists() else []


def _record(state: dict, agent: str, action: str, **extra) -> None:
    state["history"].append({"ts": _now(), "agent": agent, "action": action, **extra})


def _require(state: dict, agent: str, stations: tuple[str, ...]) -> None:
    if state["station"] not in stations:
        _die(
            f"{state['batch']} is at station '{state['station']}', not "
            f"{' / '.join(stations)} — see `handoff status {state['batch']}`"
        )
    if state["holder"] != agent:
        _die(f"{state['holder']} holds the baton for {state['batch']}, not {agent}")


def _agent(name: str | None) -> str:
    if name is None:
        _die(f"--agent is required: which agent are you? one of {', '.join(AGENTS)}")
    if name not in AGENTS:
        _die(f"unknown agent '{name}' — known: {', '.join(AGENTS)} (add a row to AGENTS)")
    return name


# ---- verbs ------------------------------------------------------------------


def propose(store: Store, args) -> None:
    me = _agent(args.agent)
    if store.path(args.batch).exists():
        _die(f"{args.batch} already exists — use `handoff status {args.batch}`")
    if not (store.root / args.file).exists():
        _die(f"plan file not found: {args.file}")
    critic = args.critic or next(a for a in ("codex", "claude") if a != me)
    state = {
        "batch": args.batch,
        "title": args.title,
        "proposer": me,
        "critic": critic,
        "implementer": None,
        "station": "critique",
        "holder": critic,
        "round": 0,
        "verdict": None,
        "adr": None,
        "files": {"plan": args.file, "notes": {}, "verdicts": {}},
        "history": [],
    }
    _record(state, me, "propose", file=args.file)
    store.save(state)
    print(f"{args.batch}: proposed by {me}; {critic} to critique {args.file}")


def review(store: Store, args) -> None:
    me = _agent(args.agent)
    state = store.load(args.batch)
    _require(state, me, ("critique", "review"))
    text = (store.root / args.file).read_text() if (store.root / args.file).exists() else None
    if text is None:
        _die(f"verdict file not found: {args.file}")
    if VERDICT_RE not in text:
        _die(f"{args.file} must carry a line starting `{VERDICT_RE} PASS|REQUEST CHANGES**`")
    state["files"]["verdicts"][me] = args.file
    state["verdict"] = args.verdict
    _record(state, me, "review", verdict=args.verdict, file=args.file, round=state["round"])
    if state["station"] == "critique":
        state["station"], state["holder"] = "settle", OPERATOR
    elif args.verdict == "pass":
        state["station"], state["holder"] = "deploy", OPERATOR
    else:
        state["station"], state["holder"] = "respond", _other(me, state)
    store.save(state)
    print(
        f"{args.batch}: {me} → {args.verdict}; now '{state['station']}' held by {state['holder']}"
    )


def settle(store: Store, args) -> None:
    state = store.load(args.batch)
    if state["station"] != "settle":
        _die(f"{args.batch} is at '{state['station']}', not 'settle'")
    impl = _agent(args.implementer)
    state["adr"], state["implementer"] = args.adr, impl
    state["station"], state["holder"] = "implement", impl
    _record(state, OPERATOR, "settle", adr=args.adr, implementer=impl)
    store.save(state)
    print(f"{args.batch}: settled ({args.adr}); {impl} implements")


def built(store: Store, args) -> None:
    me = _agent(args.agent)
    state = store.load(args.batch)
    _require(state, me, ("implement",))
    if not (store.root / args.file).exists():
        _die(f"notes file not found: {args.file}")
    state["files"]["notes"][me] = args.file
    state["round"] = 1
    state["station"], state["holder"] = "review", _other(me, state)
    _record(state, me, "built", file=args.file, commit=args.commit)
    store.save(state)
    print(f"{args.batch}: built ({args.commit}); {state['holder']} to review round 1")


def respond(store: Store, args) -> None:
    me = _agent(args.agent)
    state = store.load(args.batch)
    _require(state, me, ("respond",))
    state["round"] += 1
    state["station"], state["holder"] = "review", _other(me, state)
    _record(state, me, "respond", commit=args.commit, round=state["round"])
    store.save(state)
    print(
        f"{args.batch}: responded ({args.commit}); {state['holder']} to review round {state['round']}"
    )


def deployed(store: Store, args) -> None:
    state = store.load(args.batch)
    if state["station"] != "deploy":
        _die(f"{args.batch} is at '{state['station']}', not 'deploy'")
    state["station"], state["holder"] = "done", OPERATOR
    _record(state, OPERATOR, "deployed", note=args.note)
    store.save(state)
    print(f"{args.batch}: done")


def status(store: Store, args) -> None:
    me = _agent(args.agent) if args.agent else None
    batches = [args.batch] if args.batch else store.batches()
    if not batches:
        print("no batches — start one with `handoff propose`")
        return
    for batch in batches:
        s = store.load(batch)
        mine = me is not None and s["holder"] == me
        flag = (
            ""
            if me is None
            else ("  ← YOUR MOVE" if mine else f"  (NOT YOUR MOVE — {s['holder']})")
        )
        print(
            f"{batch}  station={s['station']}  holder={s['holder']}  round={s['round']}"
            f"  verdict={s['verdict'] or '-'}{flag}"
        )
        if args.batch or len(batches) == 1:
            other = _other(me, s) if me and me in (s["proposer"], s["critic"]) else s["critic"]
            who = me or s["holder"]
            ctx = {
                "batch": batch,
                "me": me or "<agent>",
                "round": s["round"],
                "next_round": s["round"] + 1,
                # a file this agent already wrote for the batch wins over the default
                "target": s["files"]["verdicts"].get(who)
                or s["files"]["notes"].get(who)
                or f"{AGENTS.get(who, {}).get('reviews', 'build-notes/<me>/reviews')}/{batch}.md",
                "their_file": s["files"]["verdicts"].get(other)
                or s["files"]["notes"].get(other)
                or s["files"]["plan"],
                "my_file": s["files"]["notes"].get(
                    me or s["implementer"] or "", "<your notes file>"
                ),
            }
            print(f"  title: {s['title']}")
            print(f"  plan:  {s['files']['plan']}")
            for who, f in s["files"]["notes"].items():
                print(f"  notes ({who}): {f}")
            for who, f in s["files"]["verdicts"].items():
                print(f"  verdict ({who}): {f}")
            print(f"  next:  {NEXT_ACTION[s['station']].format(**ctx)}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="handoff", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="verb", required=True)

    def add(name, fn, batch=True, agent=False):
        p = sub.add_parser(name)
        # on every verb, so it may follow the subcommand: `handoff status --root X`
        p.add_argument("--root", default=None, help="repo root (default: this script's repo)")
        if batch:
            p.add_argument("batch")
        if agent:
            p.add_argument("--agent", required=False)
        p.set_defaults(fn=fn)
        return p

    p = add("status", status, batch=False, agent=True)
    p.add_argument("batch", nargs="?")
    p = add("propose", propose, agent=True)
    p.add_argument("--title", required=True)
    p.add_argument("--file", required=True, help="the plan, repo-relative")
    p.add_argument("--critic", default=None)
    p = add("review", review, agent=True)
    p.add_argument("--verdict", choices=["pass", "request-changes"], required=True)
    p.add_argument("--file", required=True, help="the verdict file, repo-relative")
    p = add("settle", settle)
    p.add_argument("--adr", required=True)
    p.add_argument("--implementer", required=True)
    p = add("built", built, agent=True)
    p.add_argument("--file", required=True, help="implementer notes, repo-relative")
    p.add_argument("--commit", required=True)
    p = add("respond", respond, agent=True)
    p.add_argument("--commit", required=True)
    p = add("deployed", deployed)
    p.add_argument("--note", default="")

    args = ap.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parents[1]
    args.fn(Store(root), args)


if __name__ == "__main__":
    main()
