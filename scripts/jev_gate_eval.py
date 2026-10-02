"""Experiment B, scored: does the Jev gate consult the units a situation needs? (WS26, D82)

    uv run python scripts/jev_gate_eval.py
    uv run python scripts/jev_gate_eval.py --compare haiku
    uv run python scripts/jev_gate_eval.py --only rotterdam-strike,office-move
    uv run python scripts/jev_gate_eval.py --compare haiku --record   # append to plan/03

Runs the GATE ONLY — one Jev call per situation in config/jev_situations.yaml,
asking the same per-unit nouls the live gate asks (orchestration.gate) — and
scores the decisions against the labelled units. No business-unit leg is
dispatched, so this costs one Jev call (plus one baseline call with --compare)
per situation, not a fan-out. The live gated fan-out is the console's Unit gate
radio or `run_fanout.py --gate jev`.

The situations are AUTHOR-LABELLED (see the yaml header): every number printed
here is agreement with the author. Method: plan/17-jev-system-one.md.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.models import new_trace_id  # noqa: E402
from orchestration.gate import thresholds, unit_question  # noqa: E402
from orchestration.gate_eval import aggregate, by, load_situations, score_situation  # noqa: E402
from orchestration.legs import legs_for  # noqa: E402
from platforms.jev import JevError, SystemOneClient, question  # noqa: E402

SITUATIONS = ROOT / "config" / "jev_situations.yaml"
RESULTS = ROOT / "plan" / "03-results.md"
SOURCE = "jev-gate-eval"
CAVEAT = (
    "Situations are AUTHOR-LABELLED (config/jev_situations.yaml) — these numbers "
    "measure agreement with the author, not with incident ground truth."
)


def _fmt(x) -> str:
    return "—" if x is None else f"{x:.2f}"


async def _jev(situations, legs, trace_id):
    client = SystemOneClient()
    if not client.configured:
        raise SystemExit("TYPESAFE_API_KEY is not set — add it to .env (and env_sync push, D39)")
    questions = {leg.role: question("noul", unit_question(leg)) for leg in legs}
    out, usage, latency, model = {}, {}, [], client.model
    for s in situations:
        try:
            res = await client.ask(
                s.text, questions, trace_id=trace_id, source=SOURCE, detail="gate eval"
            )
        except JevError as exc:
            print(f"  jev failed on {s.id}: {exc}")
            continue
        model = res.model
        out[s.id] = {role: a.noul for role, a in res.answers.items()}
        latency.append(res.latency_ms)
        for k, v in res.usage.items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    return f"Jev {model}", out, usage, latency


async def _baseline(situations, legs, trace_id):
    from platforms.jev.baseline import DEFAULT_MODEL, MODEL_ENV, BaselineError, llm_noul
    import os

    model = os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    statements = {leg.role: unit_question(leg) for leg in legs}
    out, usage, latency = {}, {}, []
    for s in situations:
        try:
            res = await llm_noul(s.text, statements, trace_id=trace_id, source=SOURCE, model=model)
        except (BaselineError, Exception) as exc:  # noqa: BLE001 - a failed call is data
            print(f"  baseline failed on {s.id}: {type(exc).__name__}: {str(exc)[:120]}")
            continue
        out[s.id] = res.probabilities
        latency.append(res.latency_ms)
        for k, v in res.usage.items():
            if isinstance(v, (int, float)):
                usage[k] = usage.get(k, 0) + v
    return f"{model} (baseline)", out, usage, latency


def _score(name, situations, probs, dispatch_at, skip_at, usage, latency):
    by_id = {s.id: s for s in situations}
    rows = [
        r for sid, p in probs.items() for r in score_situation(by_id[sid], p, dispatch_at, skip_at)
    ]
    total = aggregate(rows)
    med = sorted(latency)[len(latency) // 2] if latency else 0
    print(f"\n{name} — {len(probs)}/{len(situations)} situations, median {med} ms, usage {usage}")
    roles = sorted(next(iter(probs.values())))
    print(f"  {'situation':<28} " + " ".join(f"{role:>16}" for role in roles))
    for sid, p in probs.items():
        s = by_id[sid]
        cells = []
        for role in sorted(p):
            mark = "*" if role in s.expected else " "
            r = next(x for x in rows if x.situation == sid and x.role == role)
            cells.append(f"{mark}{r.p:.2f} {r.decision[:4]:>4}".rjust(16))
        print(f"  {sid:<28} " + " ".join(cells))
    print("  (* = unit the label says is needed)")
    print(
        f"  overall: recall {_fmt(total.recall)}  precision {_fmt(total.precision)}  "
        f"legs saved {total.legs_saved:.0%}  wrong skips {total.fn}  "
        f"uncertain {total.uncertain}  brier {total.brier}"
    )
    for role, sc in sorted(by(rows, lambda r: r.role).items()):
        print(
            f"    {role:<16} recall {_fmt(sc.recall)}  precision {_fmt(sc.precision)}  saved {sc.legs_saved:.0%}"
        )
    for kind, sc in sorted(by(rows, lambda r: by_id[r.situation].kind).items()):
        print(
            f"    kind {kind:<11} recall {_fmt(sc.recall)}  saved {sc.legs_saved:.0%}  wrong skips {sc.fn}"
        )
    for r in total.wrong_skips:
        print(f"  WRONG SKIP: {r.situation} / {r.role} (p={r.p:.2f}) — {by_id[r.situation].why}")
    return total, med


async def run(args) -> int:
    situations = load_situations(yaml.safe_load(SITUATIONS.read_text()))
    if args.only:
        wanted = set(args.only.split(","))
        situations = [s for s in situations if s.id in wanted]
    legs = legs_for(args.scenario)
    dispatch_at, skip_at = thresholds()
    trace_id = new_trace_id()
    print(
        f"{len(situations)} situations × {len(legs)} units; bands dispatch ≥{dispatch_at}, skip ≤{skip_at}"
    )
    print(f"trace {trace_id}")
    print(CAVEAT)

    runs = [await _jev(situations, legs, trace_id)]
    if args.compare == "haiku":
        runs.append(await _baseline(situations, legs, trace_id))

    table = []
    for name, probs, usage, latency in runs:
        if not probs:
            print(f"\n{name}: no situations answered")
            continue
        total, med = _score(name, situations, probs, dispatch_at, skip_at, usage, latency)
        table.append(
            f"| {name} | {len(probs)} | {_fmt(total.recall)} | {_fmt(total.precision)} | "
            f"{total.legs_saved:.0%} | {total.fn} | {total.uncertain} | {total.brier} | "
            f"{med} ms | {usage} |"
        )
    print("\n" + CAVEAT)

    if args.record and table:
        with RESULTS.open("a") as fh:
            fh.write(
                "\n".join(
                    [
                        "",
                        f"## {dt.date.today():%Y-%m-%d} — WS26 Experiment B: Jev unit gate on labelled situations (D82)",
                        "",
                        f"`scripts/jev_gate_eval.py` — gate only, {len(situations)} situations × "
                        f"{len(legs)} units, bands dispatch ≥{dispatch_at} / skip ≤{skip_at}; "
                        f"trace `{trace_id}`. {CAVEAT} Method: plan/17-jev-system-one.md.",
                        "",
                        "| Model | situations | recall | precision | legs saved | wrong skips | uncertain | Brier | median latency | usage |",
                        "|---|---|---|---|---|---|---|---|---|---|",
                        *table,
                        "",
                    ]
                )
            )
        print(f"recorded {RESULTS.relative_to(ROOT)}")
    return 0


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--compare", choices=("none", "haiku"), default="none")
    ap.add_argument("--only", help="comma-separated situation ids")
    ap.add_argument("--scenario", default="supplier-disruption")
    ap.add_argument("--record", action="store_true", help="append aggregates to plan/03")
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
