"""Backfill traces/lab.db from the JSONL archive (D19).

JSONL is the append-only raw record; the SQLite DB is rebuildable from it
at any time:

    uv run python scripts/trace_import.py            # default traces/ dir
    uv run python scripts/trace_import.py path/to/traces
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from interop.trace import DEFAULT_TRACE_DIR, SqliteSink


def main() -> int:
    trace_dir = Path(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TRACE_DIR)
    files = sorted(trace_dir.glob("*.jsonl"))
    if not files:
        print(f"no JSONL files in {trace_dir}/")
        return 1
    sink = SqliteSink(db_path=trace_dir / "lab.db")
    imported = skipped = 0
    for path in files:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                sink.emit(json.loads(line))
                imported += 1
            except (ValueError, KeyError):
                skipped += 1
    print(
        f"imported {imported} events from {len(files)} file(s) into {sink.db_path}"
        + (f" ({skipped} malformed lines skipped)" if skipped else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
