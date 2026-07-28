"""WS11 — measure WHICH A2A endpoints implement the protocol's asynchronous half.

    uv run python scripts/a2a_async_probe.py                  # every a2a target
    uv run python scripts/a2a_async_probe.py foundry-a2a ...  # named targets
    uv run python scripts/a2a_async_probe.py --append         # -> plan/03-results.md

**The question.** The matrix records who *speaks* A2A. It does not record who
implements the half that matters for hosting: `SendMessage` MUST return
immediately, and processing MAY continue afterwards. "MAY" is doing a lot of
work in that sentence — an implementation is free to block and still be
conformant, and a caller cannot tell the difference from the agent card. Only a
measurement can, and the result is a finding either way. "Everyone ships the
sync subset" would be a first-class result; so would the opposite.

**How it discriminates.** Submit with `configuration.return_immediately`, then
poll. Three outcomes:

    async     the response came back fast and non-terminal, and `tasks/get`
              later reported a terminal state. The work outlived the request.
    blocking  the response only arrived once the work was done (terminal task,
              submit latency ~= total). Conformant, but the request holds the
              connection — so this leg still inherits any gateway ceiling.
    no-tasks  a bare Message came back, or `tasks/get` is unimplemented. There
              is no task lifecycle here at all.

`ratio` is submit_ms / total_ms — the number that decides it. Near 0.0 means the
work happened off the request; near 1.0 means the request WAS the work. The
threshold is deliberately loose (0.5): this separates architectures, not
milliseconds, and a fast agent makes every ratio noisy.

Needs whatever credentials the target needs (Entra for Foundry, ADC for Agent
Engine) and the local stack running for the localhost targets — so it is a
`live` exercise, not a unit test. The loopback proof lives in
tests/e2e/test_a2a_async.py, which needs nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.clients.a2a import A2AClient  # noqa: E402
from interop.models import AgentRequest  # noqa: E402
from interop.registry import Registry  # noqa: E402

# Long enough for a cold Agent Engine leg (39.8s measured at the D41 cutover)
# without hanging a sweep on a dead endpoint.
POLL_TIMEOUT_S = 90.0
POLL_INTERVAL_S = 1.0
BLOCKING_RATIO = 0.5

QUESTION = "In one sentence: what is a supply chain?"


@dataclass
class Probe:
    target: str
    verdict: str = "error"
    submit_ms: int = 0
    total_ms: int = 0
    polls: int = 0
    state_at_submit: str = ""
    final_state: str = ""
    detail: str = ""
    states_seen: list[str] = field(default_factory=list)

    @property
    def ratio(self) -> float:
        return (self.submit_ms / self.total_ms) if self.total_ms else 1.0


async def probe(name: str, client: A2AClient) -> Probe:
    result = Probe(target=name)
    start = time.perf_counter()
    try:
        handle = await client.submit(AgentRequest(message=QUESTION))
    except Exception as exc:  # noqa: BLE001 - a failed probe is a recorded result
        result.detail = f"{type(exc).__name__}: {str(exc)[:160]}"
        # A Message reply instead of a Task is a real answer to the question,
        # not a transport failure: this endpoint has no task lifecycle.
        if "no task lifecycle" in str(exc) or "returned a message" in str(exc):
            result.verdict = "no-tasks"
        result.total_ms = int((time.perf_counter() - start) * 1000)
        return result

    result.submit_ms = handle.submit_ms
    result.state_at_submit = handle.state
    result.states_seen.append(handle.state)

    if handle.answered_immediately:
        # The server ignored return_immediately and ran the work first. Honest
        # and conformant — and it means the connection was held the whole time.
        result.total_ms = int((time.perf_counter() - start) * 1000)
        result.final_state = handle.state
        result.verdict = "blocking"
        result.detail = "task was already terminal in the send response"
        return result

    deadline = time.time() + POLL_TIMEOUT_S
    snapshot = None
    while time.time() < deadline:
        try:
            snapshot = await client.poll(handle.task_id)
        except Exception as exc:  # noqa: BLE001
            result.total_ms = int((time.perf_counter() - start) * 1000)
            result.verdict = "no-tasks"
            result.detail = f"tasks/get failed: {type(exc).__name__}: {str(exc)[:140]}"
            return result
        result.polls += 1
        if not result.states_seen or result.states_seen[-1] != snapshot.state:
            result.states_seen.append(snapshot.state)
        if snapshot.done or snapshot.interrupted:
            break
        await asyncio.sleep(POLL_INTERVAL_S)

    result.total_ms = int((time.perf_counter() - start) * 1000)
    if snapshot is None:
        result.detail = "no snapshot"
        return result
    result.final_state = snapshot.state
    if not (snapshot.done or snapshot.interrupted):
        result.verdict = "timeout"
        result.detail = f"still {snapshot.state} after {POLL_TIMEOUT_S:.0f}s"
    elif result.ratio >= BLOCKING_RATIO:
        result.verdict = "blocking"
        result.detail = "send returned only once the work was done"
    else:
        result.verdict = "async"
        result.detail = snapshot.detail or f"{snapshot.text[:60]}"
    return result


def render(results: list[Probe]) -> str:
    lines = [
        "| Target | Verdict | submit | total | ratio | polls | state@submit → final |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for r in results:
        arrow = f"{r.state_at_submit or '—'} → {r.final_state or '—'}"
        lines.append(
            f"| `{r.target}` | **{r.verdict}** | {r.submit_ms}ms | {r.total_ms}ms | "
            f"{r.ratio:.2f} | {r.polls} | {arrow} |"
        )
    return "\n".join(lines)


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("targets", nargs="*", help="target names; default = every a2a target")
    ap.add_argument("--append", action="store_true", help="append the table to plan/03-results.md")
    args = ap.parse_args()
    load_dotenv()

    registry = Registry.load()
    names = args.targets or [
        name for name, target in registry.targets.items() if target.protocol == "a2a"
    ]
    if not names:
        print("no a2a targets found")
        return 1

    results: list[Probe] = []
    for name in names:
        try:
            # exact=True for the same reason matrix.py uses it: this measures
            # the target it names, not whatever A2ALAB_MODE remaps it to.
            client = registry.client_for(name, exact=True)
        except Exception as exc:  # noqa: BLE001 - unresolved env var, etc.
            results.append(Probe(target=name, detail=f"unresolved: {str(exc)[:120]}"))
            print(f"{name:26s} error (unresolved)")
            continue
        if not isinstance(client, A2AClient):
            continue
        result = await probe(name, client)
        results.append(result)
        print(
            f"{result.target:26s} {result.verdict:9s} submit={result.submit_ms:>6}ms "
            f"total={result.total_ms:>6}ms ratio={result.ratio:.2f} polls={result.polls}"
        )
        if result.detail:
            print(f"{'':26s}   {result.detail}")

    table = render(results)
    print("\n" + table)

    if args.append:
        stamp = time.strftime("%Y-%m-%d")
        path = Path("plan/03-results.md")
        with path.open("a") as fh:
            fh.write(
                f"\n\n## {stamp} — WS11 A2A async-lifecycle probe\n\n"
                "Who implements the asynchronous half of A2A, measured rather than "
                "read off an agent card. `ratio` = submit / total; near 0 means the "
                "work outlived the request, near 1 means the request was the work.\n\n"
                f"{table}\n"
            )
        print(f"\nappended to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
