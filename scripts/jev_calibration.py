"""Experiment C: is Jev calibrated on this lab's own wire data? (WS26, D82)

    uv run python scripts/jev_calibration.py extract
    uv run python scripts/jev_calibration.py run --limit 200
    uv run python scripts/jev_calibration.py run --limit 200 --compare haiku
    uv run python scripts/jev_calibration.py run --record     # append aggregates to plan/03

`extract` walks `trace_events` (traces/lab.db by default) and writes the
judgeable hops, with their wire labels, to traces/jev/calibration_set.jsonl.
`run` asks Jev one `noul` per hop — over the request/response TEXT only, never
the status column the label came from — and reports calibration against the
wire label, overall and for the hops the wire called `ok` (where refusals and
placeholders hide). Every disagreement goes to traces/jev/review.md for the
operator to adjudicate.

Everything this writes stays under traces/, which is gitignored: the extracted
text includes CRM answers from the lab's org. Only aggregate numbers are ever
recorded in plan/03-results.md, and only with --record.

Answers are cached in traces/jev/answers.jsonl per (hop, model), so a rerun or a
larger --limit pays only for hops not yet asked.

Method, label rules and the bias in the `yes` label: plan/17-jev-system-one.md.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dotenv import load_dotenv  # noqa: E402

from interop.models import new_trace_id  # noqa: E402
from platforms.jev import JevError, SystemOneClient, question  # noqa: E402
from platforms.jev.calibration import (  # noqa: E402
    STATEMENT,
    Item,
    disagreements,
    extract,
    report,
)

OUT_DIR = ROOT / "traces" / "jev"
SET_FILE = OUT_DIR / "calibration_set.jsonl"
CACHE_FILE = OUT_DIR / "answers.jsonl"
REVIEW_FILE = OUT_DIR / "review.md"
RESULTS = ROOT / "plan" / "03-results.md"


def _load_set() -> list[Item]:
    if not SET_FILE.exists():
        raise SystemExit(f"no {SET_FILE.relative_to(ROOT)} — run `extract` first")
    return [Item(**json.loads(line)) for line in SET_FILE.read_text().splitlines() if line]


def _sample(items: list[Item], limit: int, seed: str) -> list[Item]:
    """Deterministic sample: the same --seed and --limit pick the same hops, so a
    rerun is comparable and the cache is reused."""
    ranked = sorted(items, key=lambda it: hashlib.sha256(f"{seed}:{it.id}".encode()).hexdigest())
    return ranked[:limit] if limit else ranked


def _load_cache() -> dict[str, tuple[float, str]]:
    """{who|id: (p, served model)} — the served version is kept so a fully
    cached rerun still reports which Jev version produced the numbers."""
    cache: dict[str, tuple[float, str]] = {}
    if CACHE_FILE.exists():
        for line in CACHE_FILE.read_text().splitlines():
            if line:
                row = json.loads(line)
                cache[f"{row['who']}|{row['id']}"] = (row["p"], row.get("model", ""))
    return cache


def _append_cache(who: str, item_id: str, p: float, usage: dict, model: str = "") -> None:
    with CACHE_FILE.open("a") as fh:
        row = {"who": who, "id": item_id, "p": p, "model": model, "usage": usage}
        fh.write(json.dumps(row) + "\n")


def cmd_extract(args) -> int:
    con = sqlite3.connect(args.db)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute("SELECT * FROM trace_events ORDER BY ts")]
    items, excluded = extract(rows)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SET_FILE.write_text("".join(json.dumps(it.to_dict()) + "\n" for it in items))
    yes = sum(it.label for it in items)
    print(f"read      {len(rows)} hops from {args.db}")
    print(
        f"judgeable {len(items)} unique (question, answer) pairs — {yes} yes / {len(items) - yes} no"
    )
    reasons: dict[str, int] = {}
    for it in items:
        reasons[it.reason] = reasons.get(it.reason, 0) + 1
    print(f"labels    {reasons}")
    print(f"excluded  {excluded}")
    print(f"wrote     {SET_FILE.relative_to(ROOT)}")
    return 0


async def _ask_jev(items, cache, client, trace_id, concurrency, usage_total):
    sem = asyncio.Semaphore(concurrency)
    model = client.model
    out: dict[str, float] = {}
    served: set[str] = set()  # the version Jev reports, not the requested alias
    failures = 0

    async def one(it: Item):
        nonlocal failures
        key = f"jev:{model}|{it.id}"
        if key in cache:
            out[it.id], cached_model = cache[key]
            if cached_model:
                served.add(cached_model)
            return
        async with sem:
            try:
                res = await client.ask(
                    it.state,
                    {"answered": question("noul", STATEMENT)},
                    trace_id=trace_id,
                    source="jev-calibration",
                    detail="calibration",
                )
            except JevError as exc:
                failures += 1
                print(f"  jev failed on {it.id}: {exc}")
                return
        p = res.answers["answered"].noul
        served.add(res.model)
        out[it.id] = p
        for k, v in res.usage.items():
            if isinstance(v, (int, float)):
                usage_total[k] = usage_total.get(k, 0) + v
        _append_cache(f"jev:{model}", it.id, p, res.usage, res.model)

    await asyncio.gather(*(one(it) for it in items))
    return out, failures, ", ".join(sorted(served)) or f"{model} (cached)"


async def _ask_baseline(items, cache, trace_id, concurrency, usage_total):
    from platforms.jev.baseline import DEFAULT_MODEL, MODEL_ENV, BaselineError, llm_noul
    import os

    from anthropic import AsyncAnthropic

    model = os.environ.get(MODEL_ENV) or DEFAULT_MODEL
    client = AsyncAnthropic()
    sem = asyncio.Semaphore(concurrency)
    out: dict[str, float] = {}
    failures = 0

    async def one(it: Item):
        nonlocal failures
        key = f"llm:{model}|{it.id}"
        if key in cache:
            out[it.id] = cache[key][0]
            return
        async with sem:
            try:
                res = await llm_noul(
                    it.state,
                    {"answered": STATEMENT},
                    trace_id=trace_id,
                    source="jev-calibration",
                    client=client,
                    model=model,
                )
            except (BaselineError, Exception) as exc:  # noqa: BLE001 - a failed item is data
                failures += 1
                print(f"  baseline failed on {it.id}: {type(exc).__name__}: {str(exc)[:120]}")
                return
        p = res.probabilities["answered"]
        out[it.id] = p
        for k, v in res.usage.items():
            if isinstance(v, (int, float)):
                usage_total[k] = usage_total.get(k, 0) + v
        _append_cache(f"llm:{model}", it.id, p, res.usage, model)

    await asyncio.gather(*(one(it) for it in items))
    return out, failures, model


def _print_report(name: str, rep) -> None:
    band = "—" if rep.band_accuracy is None else f"{rep.band_accuracy:.3f}"
    print(
        f"  {name:<22} n={rep.n:<4} yes={rep.positives:<4} brier={rep.brier:<7} "
        f"ece={rep.ece:<7} acc@0.5={rep.accuracy:<7} "
        f"acts-on={rep.band_coverage:.0%} band-acc={band}"
    )


def _reports(items: list[Item], probs: dict[str, float]):
    judged = [it for it in items if it.id in probs]
    ok_only = [it for it in judged if it.status == "ok"]
    whole = report([probs[it.id] for it in judged], [it.label for it in judged])
    status_ok = report([probs[it.id] for it in ok_only], [it.label for it in ok_only])
    return judged, whole, status_ok


def _load_verdicts() -> dict[str, str]:
    """{item id: 'wire'|'model'} from the review file, so a rerun keeps the
    operator's adjudication instead of overwriting it."""
    verdicts: dict[str, str] = {}
    if not REVIEW_FILE.exists():
        return verdicts
    current = None
    for line in REVIEW_FILE.read_text().splitlines():
        if line.startswith("## "):
            current = line[3:].split(" ", 1)[0]
        elif line.startswith("verdict:") and current:
            v = line.split(":", 1)[1].strip().split(" ", 1)[0].lower()
            if v in ("wire", "model"):
                verdicts[current] = v
    return verdicts


def _write_review(items: list[Item], runs: dict[str, dict[str, float]], verdicts) -> int:
    """One file for the UNION of every model's disagreements with the wire —
    adjudicating only Jev's would correct Jev's errors and leave the baseline's
    standing, which biases the comparison."""
    flagged: dict[str, Item] = {}
    for probs in runs.values():
        judged = [it for it in items if it.id in probs]
        for it, _ in disagreements(judged, [probs[it.id] for it in judged]):
            flagged[it.id] = it
    lines = [
        "# Jev calibration — disagreements to adjudicate",
        "",
        f"Generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC by "
        "scripts/jev_calibration.py. LOCAL ONLY — traces/ is gitignored; this "
        "file quotes CRM answers.",
        "",
        "Every hop where ANY model disagrees with the wire label. For each, decide "
        "against the statement asked: was the WIRE label right, or the model? Write "
        "`verdict: wire` or `verdict: model` (anything after the word is a note). "
        "Verdicts survive a rerun; the next `run` reports adjudicated metrics.",
        "",
        f"Statement: {STATEMENT}",
        "",
    ]
    for it in flagged.values():
        wire = "yes" if it.label else "no"
        ps = ", ".join(
            f"{who} p={probs[it.id]:.2f}" for who, probs in runs.items() if it.id in probs
        )
        lines += [
            f"## {it.id} — {it.target} ({it.protocol}) — wire {wire} [{it.reason}] — {ps}",
            "",
            f"**Request:** {it.question[:400]}",
            "",
            f"**Response:** {it.answer[:800] or '(empty)'}",
            "",
            f"verdict: {verdicts.get(it.id, '')}",
            "",
        ]
    REVIEW_FILE.write_text("\n".join(lines))
    return len(flagged)


def _adjudicated(items: list[Item], verdicts: dict[str, str]) -> list[Item]:
    """Items with the label flipped wherever the verdict sided with the model."""
    out = []
    for it in items:
        if verdicts.get(it.id) == "model":
            it = Item(**{**it.to_dict(), "label": 1 - it.label, "reason": "adjudicated"})
        out.append(it)
    return out


def _record(lines: list[str]) -> None:
    with RESULTS.open("a") as fh:
        fh.write("\n" + "\n".join(lines) + "\n")


async def cmd_run(args) -> int:
    items = _sample(_load_set(), args.limit, args.seed)
    cache = _load_cache()
    client = SystemOneClient()
    if not client.configured:
        print("TYPESAFE_API_KEY is not set — add it to .env (and env_sync push, D39)")
        return 2
    trace_id = new_trace_id()
    yes = sum(it.label for it in items)
    print(f"sample    {len(items)} hops ({yes} yes / {len(items) - yes} no), seed '{args.seed}'")
    print(f"trace     {trace_id}")
    print(f"statement {STATEMENT}\n")

    runs: dict[str, dict[str, float]] = {}
    usage: dict[str, dict] = {}
    jev_usage: dict = {}
    jev_probs, jev_fail, jev_model = await _ask_jev(
        items, cache, client, trace_id, args.concurrency, jev_usage
    )
    runs[f"Jev {jev_model}"], usage[f"Jev {jev_model}"] = jev_probs, jev_usage
    if args.compare == "haiku":
        llm_usage: dict = {}
        llm_probs, _, llm_model = await _ask_baseline(
            items, cache, trace_id, args.concurrency, llm_usage
        )
        runs[f"{llm_model} (baseline)"], usage[f"{llm_model} (baseline)"] = llm_probs, llm_usage

    verdicts = _load_verdicts()
    n_flagged = _write_review(items, runs, verdicts)
    sample_ids = {it.id for it in items}
    decided = {k: v for k, v in verdicts.items() if k in sample_ids}
    adjudicated = _adjudicated(items, decided)
    n_model = sum(v == "model" for v in decided.values())

    table = []
    for who, probs in runs.items():
        judged, whole, ok_only = _reports(items, probs)
        _, adj, _ = _reports(adjudicated, probs)
        print(f"{who} — {len(judged)} judged, usage {usage[who]}")
        _print_report("vs wire label", whole)
        _print_report("vs wire, status ok", ok_only)
        if decided:
            _print_report("vs adjudicated label", adj)
        print("  reliability (vs adjudicated):" if decided else "  reliability (vs wire):")
        for row in (adj if decided else whole).reliability:
            print(
                f"    {row['bin']}  n={row['n']:<4} mean p={row['mean_p']:<6} observed={row['observed']}"
            )
        print()
        table.append(
            f"| {who} | {whole.n} | {whole.brier} / {whole.ece} | {ok_only.brier} / {ok_only.ece} | "
            + (f"{adj.brier} / {adj.ece} | {adj.accuracy} | " if decided else "— | — | ")
            + f"{whole.band_coverage:.0%} | {_fmt_acc(adj if decided else whole)} | {usage[who]} |"
        )

    print(
        f"{n_flagged} hops where some model disagrees with the wire → {REVIEW_FILE.relative_to(ROOT)}"
    )
    print(
        f"adjudicated {len(decided)}/{n_flagged} — {n_model} sided with the model, "
        f"{len(decided) - n_model} with the wire"
    )
    if len(decided) < n_flagged:
        print(
            "\nCaveat: unadjudicated disagreements remain. The wire 'yes' label cannot see a "
            "refusal or placeholder that arrived as a 200; adjudicate before quoting a number."
        )

    if args.record:
        _record(
            [
                f"## {dt.date.today():%Y-%m-%d} — WS26 Experiment C: Jev calibration on lab wire data (D82)",
                "",
                f"`scripts/jev_calibration.py run --limit {args.limit} --seed {args.seed}` over "
                f"`trace_events`; trace `{trace_id}`. {len(items)} hops ({yes} wire-yes / "
                f"{len(items) - yes} wire-no). Label = the wire's own verdict "
                "(plan/17-jev-system-one.md); the model sees request/response text only. "
                f"{n_flagged} hops where some model disagreed with the wire; {len(decided)} "
                f"adjudicated — {n_model} for the model, {len(decided) - n_model} for the wire.",
                "",
                "| Model | n | Brier / ECE vs wire | status-ok Brier / ECE | Brier / ECE adjudicated | acc@0.5 adjudicated | acts on (p≥0.9 or ≤0.1) | accuracy when acting | usage |",
                "|---|---|---|---|---|---|---|---|---|",
                *table,
            ]
        )
        print(f"recorded  {RESULTS.relative_to(ROOT)}")
    return 0 if not jev_fail else 1


def _fmt_acc(rep) -> str:
    return "—" if rep.band_accuracy is None else f"{rep.band_accuracy:.3f}"


def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="build the labelled set from trace_events")
    ex.add_argument("--db", default=str(ROOT / "traces" / "lab.db"))
    run = sub.add_parser("run", help="ask Jev (and optionally a baseline), report calibration")
    run.add_argument("--limit", type=int, default=200, help="hops to judge (0 = all)")
    run.add_argument("--seed", default="ws26", help="sampling seed — same seed, same hops")
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--compare", choices=("none", "haiku"), default="none")
    run.add_argument("--record", action="store_true", help="append aggregates to plan/03")
    args = ap.parse_args()
    if args.cmd == "extract":
        return cmd_extract(args)
    return asyncio.run(cmd_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
