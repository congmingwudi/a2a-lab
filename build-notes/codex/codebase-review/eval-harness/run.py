#!/usr/bin/env python3
"""Standard-library runner: explicit evidence, exact negative controls, no network."""

from __future__ import annotations

import argparse
import collections
import fnmatch
import hashlib
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from engine import CaseError, evaluate, obj, require, validate
from repository import execute

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def reject_constant(value):
    raise CaseError('non-finite JSON number')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON object key: ' + key)
        result[key] = value
    return result


def read_jsonl(path):
    with path.open(encoding='utf-8') as stream:
        for line_no, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line, parse_constant=reject_constant, object_pairs_hook=unique_object)
                obj(value)
            except (ValueError, RecursionError) as exc:
                raise CaseError(f'{path.name}:{line_no}: invalid JSON object ({type(exc).__name__})') from exc
            yield line_no, value


def load_cases(paths):
    cases, ids = [], set()
    for path in paths:
        for line_no, case in read_jsonl(path):
            try:
                validate(case)
                require(case['id'] not in ids, 'duplicate case id')
            except (CaseError, TypeError) as exc:
                raise CaseError(f'{path.name}:{line_no}: {exc}') from exc
            ids.add(case['id'])
            cases.append(case)
    require(bool(cases), 'no cases loaded')
    return cases


def source_hash(root):
    digest = hashlib.sha256()
    for path in sorted((root / 'src').rglob('*.py')):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def write_report(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')


def write_junit(path, results):
    suite = ET.Element('testsuite', name='a2a-evidence-evals', tests=str(len(results)),
                       failures=str(sum(r['status'] == 'fail' for r in results)),
                       errors=str(sum(r['status'] == 'error' for r in results)))
    for result in results:
        node = ET.SubElement(suite, 'testcase', classname=result['area'], name=result['id'])
        if result['status'] != 'pass':
            ET.SubElement(node, 'error' if result['status'] == 'error' else 'failure',
                          message=result.get('error', ', '.join(result['violations'])))
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(suite).write(path, encoding='utf-8', xml_declaration=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases', type=Path, nargs='+', help='JSONL files; default: bundled corpus')
    parser.add_argument('--area', action='append', help='area filter (repeatable)')
    parser.add_argument('--case', action='append', help='case id glob (repeatable)')
    parser.add_argument('--observations', type=Path, help='export JSONL: {id, actual}; no fixture fallback')
    parser.add_argument('--report', type=Path, help='write machine-readable JSON')
    parser.add_argument('--junit', type=Path, help='write JUnit XML')
    parser.add_argument('--list', action='store_true', help='validate and list selected cases')
    args = parser.parse_args(argv)
    try:
        cases = load_cases(args.cases or sorted((HERE / 'cases').glob('*.jsonl')))
        cases = [c for c in cases if (not args.area or c['area'] in args.area)
                 and (not args.case or any(fnmatch.fnmatchcase(c['id'], p) for p in args.case))]
        require(bool(cases), 'filters selected zero cases')
        observations = None
        if args.observations:
            observations = {}
            for _, record in read_jsonl(args.observations):
                require(set(record) == {'id', 'actual'}, 'observations require only id and actual')
                require(isinstance(record['id'], str), 'observation id must be a string')
                require(record['id'] not in observations, 'duplicate observation id')
                observations[record['id']] = obj(record['actual'])
            require(set(observations) == {c['id'] for c in cases},
                    'observations must exactly match selected case ids (no fallback)')
            require(all(c['area'] != 'repository' and not c.get('expected_violations') for c in cases),
                    'external observations require positive evidence cases; exclude repository/negative cases')
        if args.list:
            for case in cases:
                print(f"{case['id']} [{case['area']}] {case['description']}")
            return 0
        # Never overwrite input evidence with a report.
        protected = {p.resolve() for p in (args.cases or sorted((HERE / 'cases').glob('*.jsonl')))}
        if args.observations:
            protected.add(args.observations.resolve())
        for path in (args.report, args.junit):
            if path:
                require(path.resolve() not in protected, 'report path would overwrite input')
        require(not (args.report and args.junit and args.report.resolve() == args.junit.resolve()),
                'report and junit paths must differ')
    except (CaseError, OSError, UnicodeError, TypeError) as exc:
        print(f'INPUT ERROR: {exc}', file=sys.stderr)
        return 2

    results = []
    for case in cases:
        expected = sorted(case.get('expected_violations', []))
        result = {'id': case['id'], 'area': case['area'], 'expected_violations': expected,
                  'negative_control': bool(expected), 'violations': [],
                  'mode': 'export' if observations is not None else
                          'repository' if case['area'] == 'repository' else 'synthetic-replay'}
        try:
            actual = observations[case['id']] if observations is not None else (
                execute(case['input'], ROOT) if case['area'] == 'repository' else case['input'])
            violations, metrics = evaluate(case, actual, ROOT)
            result.update(violations=violations, metrics=metrics,
                          status='pass' if violations == expected else 'fail')
        except Exception as exc:
            # Exception text may contain raw evidence/credentials. Never echo it.
            result.update(status='error', error=type(exc).__name__)
        results.append(result)
        detail = result.get('error') or ', '.join(result['violations']) or 'accepted'
        print(f"{result['status'].upper():5} {case['id']}: {detail}"
              + (' [negative control]' if expected else ''))
    counts = dict(collections.Counter(r['status'] for r in results))
    try:
        commit = subprocess.run(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], capture_output=True,
                                text=True, timeout=5, check=False).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        commit = None
    report = {'schema_version': 1, 'commit': commit, 'python': sys.version.split()[0],
              'source_sha256': source_hash(ROOT),
              'corpus_sha256': hashlib.sha256(json.dumps(cases, sort_keys=True).encode()).hexdigest(),
              'harness_sha256': hashlib.sha256(b''.join(p.read_bytes() for p in sorted(HERE.glob('*.py')))).hexdigest(),
              'observations_sha256': hashlib.sha256(args.observations.read_bytes()).hexdigest()
                                     if args.observations else None,
              'counts': counts, 'negative_controls': sum(r['negative_control'] for r in results),
              'by_area': {area: dict(collections.Counter(r['status'] for r in results if r['area'] == area))
                          for area in sorted({r['area'] for r in results})},
              'results': results}
    print(f"\n{len(results)} cases: {counts}; {report['negative_controls']} negative controls.")
    print('Synthetic replays validate graders; they do not establish deployed experiment quality.')
    try:
        if args.report:
            write_report(args.report, report)
        if args.junit:
            write_junit(args.junit, results)
    except OSError as exc:
        print(f'REPORT ERROR: {type(exc).__name__}', file=sys.stderr)
        return 2
    return 2 if counts.get('error') else 1 if counts.get('fail') else 0


if __name__ == '__main__':
    raise SystemExit(main())
