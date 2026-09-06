#!/usr/bin/env python3
"""Join normalized run manifests to existing TraceEvent JSONL exports, offline."""

import argparse
import json
from pathlib import Path
import sys

from engine import CaseError, nonempty, obj, require
from run import read_jsonl


def prepare(manifests, trace_files):
    by_trace = {}
    for path in trace_files:
        for line_no, event in read_jsonl(path):
            require(nonempty(event.get('trace_id')), f'{path.name}:{line_no}: missing trace_id')
            by_trace.setdefault(event['trace_id'], []).append(event)
    result, ids = [], set()
    for line_no, record in read_jsonl(manifests):
        require(set(record) == {'id', 'actual'}, f'{manifests.name}:{line_no}: expected id and actual')
        require(nonempty(record['id']) and record['id'] not in ids, 'missing or duplicate manifest id')
        ids.add(record['id'])
        actual = obj(record['actual'])
        require('hops' not in actual, 'manifest must omit hops; they come from the trace export')
        trace_id = actual.get('trace_id')
        require(nonempty(trace_id), 'manifest actual.trace_id must be nonempty')
        # Do not deduplicate, renumber, sort, or silently fabricate missing hops.
        # Empty evidence reaches the grader and fails trace.missing.
        result.append({'id': record['id'], 'actual': {**actual, 'hops': by_trace.get(trace_id, [])}})
    require(bool(result), 'empty manifest')
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--traces', type=Path, nargs='+', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        require(args.output.resolve() not in {p.resolve() for p in [args.manifest, *args.traces]},
                'output must differ from inputs')
        observations = prepare(args.manifest, args.traces)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(''.join(json.dumps(o, ensure_ascii=False, allow_nan=False) + '\n'
                                      for o in observations), encoding='utf-8')
    except (CaseError, OSError, UnicodeError, TypeError) as exc:
        print(f'INPUT ERROR: {exc}', file=sys.stderr)
        return 2
    print(f'Prepared {len(observations)} observations from existing trace exports.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
