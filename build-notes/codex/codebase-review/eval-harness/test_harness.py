"""Runner/grader integrity tests; run with Python -B -S and unittest."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

import engine
import prepare
import run


class HarnessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = run.load_cases(sorted((run.HERE / 'cases').glob('*.jsonl')))
        cls.by_id = {c['id']: c for c in cls.cases}

    def grade(self, name, mutate=None):
        case = copy.deepcopy(self.by_id[name])
        if mutate:
            mutate(case)
        return engine.evaluate(case, case['input'], run.ROOT)

    def cli(self, *args, env=None):
        return subprocess.run([sys.executable, '-B', '-S', str(run.HERE / 'run.py'), *args],
                              cwd=tempfile.gettempdir(), capture_output=True, text=True,
                              timeout=20, env=env)

    def test_all_synthetic_controls_have_exact_outcomes(self):
        for case in self.cases:
            if case['area'] == 'repository':
                continue
            with self.subTest(case=case['id']):
                failures, _ = engine.evaluate(case, case['input'], run.ROOT)
                self.assertEqual(failures, sorted(case.get('expected_violations', [])))

    def test_good_case_mutation_is_detected(self):
        failures, _ = self.grade('trace.cross-platform', lambda c: c['input']['hops'].clear())
        self.assertEqual(failures, ['trace.missing', 'trace.route'])

    def test_pending_trace_requires_explicit_policy(self):
        failures, _ = self.grade('trace.pending', lambda c: c['policy'].pop('allowed_statuses'))
        self.assertEqual(failures, ['trace.status'])

    def test_null_payload_is_not_evidence(self):
        failures, _ = self.grade('trace.cross-platform', lambda c: c['input']['hops'][0].update(
            response_payload_raw=None))
        self.assertEqual(failures, ['trace.payload'])

    def test_http_error_cannot_be_saved_by_good_content(self):
        failures, _ = self.grade('protocol.rest', lambda c: c['input'].update(http_status=500))
        self.assertEqual(failures, ['protocol.http_status'])

    def test_failure_marker_must_name_the_missing_leg(self):
        failures, _ = self.grade('experiment.honest-partial', lambda c: c['input'].update(
            text=c['input']['text'].replace('unavailable: finance', 'unavailable: comms')))
        self.assertEqual(failures, ['experiment.hidden_failure'])

    def test_async_working_cannot_regress_to_submitted(self):
        def mutate(c):
            last = copy.deepcopy(c['input']['snapshots'][-1])
            last.update(state='submitted', at_ms=200)
            c['input']['snapshots'].insert(-1, last)
        failures, _ = self.grade('task.fire-poll', mutate)
        self.assertEqual(failures, ['task.regression'])

    def test_unknown_usage_remains_unknown(self):
        _, metrics = self.grade('obs.native-and-rider')
        self.assertEqual(metrics['sessions_with_unknown_usage'], 1)

    def test_denominator_does_not_shrink_with_missing_platform(self):
        _, metrics = self.grade('obs.missing-platform')
        self.assertEqual(metrics['join_rate'], 0.5)
        self.assertEqual(metrics['required_platforms'], 2)

    def test_percentile_keeps_tail_and_excludes_other_cohort(self):
        _, metrics = self.grade('insight.recompute-p95')
        self.assertEqual(metrics['computed'], 900)
        self.assertEqual(metrics['samples'], 3)

    def test_export_cannot_downgrade_measured_claim_to_hypothesis(self):
        failures, _ = self.grade('insight.recompute-p95', lambda c: c['input'].update(
            status='hypothesis', test_plan='Measure later'))
        self.assertEqual(failures, ['insight.status'])

    def test_export_cannot_switch_measurement_cohort(self):
        failures, _ = self.grade('insight.recompute-p95', lambda c: c['input']['measurement'].update(
            cohort='cold-rest', value=5000))
        self.assertEqual(failures, ['insight.measurement_contract', 'insight.samples'])

    def test_secret_detector_handles_serialized_payloads(self):
        self.assertTrue(engine.has_secret(json.dumps({'nested': {'client_secret': 'synthetic'}})))
        self.assertFalse(engine.has_secret({'client_secret': '[REDACTED]'}))

    def test_reference_path_traversal_is_invalid_input(self):
        with self.assertRaises(engine.CaseError):
            engine.local_artifact(run.ROOT, '../outside.md')

    def test_reference_fragment_must_exist(self):
        self.assertIsNone(engine.local_artifact(run.ROOT, 'src/interop/models.py#L99999'))
        self.assertIsNone(engine.local_artifact(run.ROOT, 'README.md#no-such-heading-synthetic'))

    def test_assertions_cannot_pass_on_missing_pointer(self):
        self.assertEqual(engine.assertions({}, [{'name': 'missing', 'path': '/missing',
                                               'op': 'not_contains', 'value': 'bad'}]), {'assert.missing'})

    def test_case_policy_typos_and_wrong_types_rejected(self):
        for update in ({'max_elasped_ms': 10}, {'max_elapsed_ms': True}, {'min_hops': 0},
                       {'allow_partial': 'false'}, {'contains': 'one string'}):
            with self.subTest(update=update):
                case = copy.deepcopy(self.by_id['experiment.delegation'])
                case['policy'].update(update)
                with self.assertRaises(engine.CaseError):
                    engine.validate(case)

    def test_duplicate_ids_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'duplicate.jsonl'
            row = json.dumps(self.by_id['trace.cross-platform']) + '\n'
            path.write_text(row * 2)
            with self.assertRaisesRegex(engine.CaseError, 'duplicate.jsonl:2'):
                run.load_cases([path])

    def test_malformed_json_duplicate_keys_and_nan_rejected(self):
        for text in ('{bad json', '{"id":"a","id":"b"}', '{"x":NaN}', '[]'):
            with self.subTest(text=text), tempfile.TemporaryDirectory() as d:
                path = Path(d) / 'invalid.jsonl'
                path.write_text('\n' + text + '\n')
                with self.assertRaisesRegex(engine.CaseError, 'invalid.jsonl:2'):
                    list(run.read_jsonl(path))

    def test_zero_selection_is_not_success(self):
        result = self.cli('--case', 'no-such-case')
        self.assertEqual(result.returncode, 2)
        self.assertIn('zero cases', result.stderr)

    def test_external_evidence_does_not_fall_back_to_fixture(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'observations.jsonl'
            path.write_text('')
            result = self.cli('--case', 'trace.cross-platform', '--observations', str(path))
            self.assertEqual(result.returncode, 2)
            self.assertIn('exactly match', result.stderr)

    def test_external_export_pass_then_failure_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            path, report, junit = [Path(d) / x for x in ('observations.jsonl', 'report.json', 'junit.xml')]
            actual = copy.deepcopy(self.by_id['trace.cross-platform']['input'])
            path.write_text(json.dumps({'id': 'trace.cross-platform', 'actual': actual}) + '\n')
            args = ('--case', 'trace.cross-platform', '--observations', str(path),
                    '--report', str(report), '--junit', str(junit))
            self.assertEqual(self.cli(*args).returncode, 0)
            body = json.loads(report.read_text())
            self.assertEqual(body['results'][0]['mode'], 'export')
            self.assertEqual(len(body['observations_sha256']), 64)
            actual['hops'] = []
            path.write_text(json.dumps({'id': 'trace.cross-platform', 'actual': actual}) + '\n')
            self.assertEqual(self.cli(*args).returncode, 1)
            self.assertEqual(ET.parse(junit).getroot().get('failures'), '1')

    def test_negative_control_cannot_accept_external_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'observations.jsonl'
            case = self.by_id['trace.fragmented']
            path.write_text(json.dumps({'id': case['id'], 'actual': case['input']}) + '\n')
            result = self.cli('--case', case['id'], '--observations', str(path))
            self.assertEqual(result.returncode, 2)
            self.assertIn('positive evidence cases', result.stderr)

    def test_exception_is_an_error_even_for_negative_control(self):
        with tempfile.TemporaryDirectory() as d:
            path, report = Path(d) / 'cases.jsonl', Path(d) / 'report.json'
            case = copy.deepcopy(self.by_id['trace.fragmented'])
            case['input'] = {}
            path.write_text(json.dumps(case) + '\n')
            result = self.cli('--cases', str(path), '--report', str(report))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(json.loads(report.read_text())['results'][0]['status'], 'error')

    def test_report_cannot_overwrite_case_file(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'case.jsonl'
            original = json.dumps(self.by_id['trace.cross-platform']) + '\n'
            path.write_text(original)
            result = self.cli('--cases', str(path), '--report', str(path))
            self.assertEqual(result.returncode, 2)
            self.assertEqual(path.read_text(), original)

    def test_repository_cases_ignore_hosted_sink_environment(self):
        env = dict(os.environ, A2ALAB_TRACE_SINK='postgres', A2ALAB_PG_DSN='do-not-connect',
                   A2ALAB_TRACE_DIR='/do-not-write', A2ALAB_OBS_STORE='postgres')
        result = self.cli('--case', 'record.*', '--case', 'store.*', '--case', 'request.*', env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_reports_do_not_include_raw_secret_values(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / 'report.json'
            result = self.cli('--case', 'regression.*', '--report', str(report))
            self.assertIn(result.returncode, (0, 1))
            for text in (result.stdout, result.stderr, report.read_text()):
                self.assertNotIn('synthetic-secret-only', text)
                self.assertNotIn('synthetic-opaque-token', text)

    def test_prepare_partitions_without_deduplicating_or_fabricating(self):
        with tempfile.TemporaryDirectory() as d:
            manifest, traces = Path(d) / 'manifest.jsonl', Path(d) / 'traces.jsonl'
            manifest.write_text('\n'.join(json.dumps({'id': trace_id, 'actual': {'trace_id': trace_id}})
                                           for trace_id in ['aaaaaaaa', 'missing']))
            event = copy.deepcopy(self.by_id['trace.cross-platform']['input']['hops'][0])
            unrelated = dict(event, trace_id='bbbbbbbb')
            traces.write_text('\n'.join(json.dumps(e) for e in [event, event, unrelated]))
            observations = prepare.prepare(manifest, [traces])
            self.assertEqual(observations[0]['actual']['hops'], [event, event])
            self.assertEqual(observations[1]['actual']['hops'], [])


if __name__ == '__main__':
    unittest.main()
