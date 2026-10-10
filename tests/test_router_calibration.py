"""Explicit outcomes, review proof, append identities and private aggregate reports."""
from concurrent.futures import ThreadPoolExecutor
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
cal = importlib.import_module('router_calibration')
shadow_module = importlib.import_module('router_shadow')


class CalibrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.run = Path(self.temp.name).resolve()
        self.events = []
        self.start('a')
        self.save()

    def start(self, dispatch, tier=3, parent=None, root=None, shadow=True):
        actual = {'runtime': 'claude', 'model': 'opus', 'effort': 'high'}
        fields = dict(dispatch=dispatch, attempt_id=dispatch, root_attempt_id=root or dispatch,
                      parent_attempt_id=parent, decision_id='decision-' + dispatch,
                      profile='critical-review', tier=tier, policy_hash='a' * 64, data_hash='b' * 64)
        self.events.append(dict(v=1, id=dispatch + '.start', at='2026-10-10T12:00:00Z',
                                type='dispatch.start', **actual, **fields))
        if shadow:
            self.events.append(dict(v=1, id=dispatch + '.shadow', at='2026-10-10T12:00:01Z',
                                    type='router.shadow', status='scored', recommendation=None,
                                    actual=actual, selected_tuple=actual, **fields))

    def save(self):
        (self.run / 'events.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in self.events))

    def review(self, verdict='pass', status='complete', suffix='; reviewer VERDICT: block overridden'):
        reference = dict(attempt_id='review-a', milestone='m1', round=1,
                         report='/private/report.md', verdict=verdict)
        reason = ('VERDICT: needs triage — not an approval' if verdict == 'needs_triage'
                  else 'VERDICT: ' + verdict + ' (computed from matrix)' + suffix)
        records = [dict(type='review', attempt_id='review-a', milestone='m1', round=1,
                        report='/private/report.md', reviewer='codex', status='running'),
                   dict(type='review_result', attempt_id='review-a', status=status, reason=reason)]
        (self.run / 'review-ledger.jsonl').write_text(''.join(json.dumps(e) + '\n' for e in records))
        return reference

    def label(self, status='accepted', **kwargs):
        return cal.label_attempt(self.run, 'a', status, authority='operator', criteria_id='gate-v1',
                                 revision='revision-a', **kwargs)

    def test_done_and_review_reservations_are_not_acceptance(self):
        self.events.append(dict(v=1, id='a.end', at='2026-10-10T12:02:00Z', type='dispatch.end',
                                dispatch='a', status='done', usage=None))
        self.save()
        self.review()
        report = cal.calibration_report(self.run)
        self.assertEqual(report['eligible_outcome_n'], 0)
        self.assertEqual(report['review_reservations_n'], 1)
        self.assertIsNone(report['estimates']['failure_probability'])
        self.assertFalse(report['fit_ready'])

    def test_root_generated_decision_fields_join_and_reject_contradictions(self):
        self.events.clear(); self.start('a', tier=1)
        start, advisory = self.events
        start.pop('decision_id')
        start.update(shadow_module.dispatch_fields(advisory))
        end = dict(v=1, id='a.end', at='2026-10-10T12:02:00Z', type='dispatch.end',
                   dispatch='a', status='done', usage=None,
                   **shadow_module.completion_fields({**start, 'log': str(self.run / 'missing.log')}))
        self.events.append(end); self.save()
        self.assertTrue(self.label())
        self.assertEqual(cal.calibration_report(self.run)['eligible_outcome_n'], 1)
        for record in (start, end):
            saved = record['router_decision_id']
            record['router_decision_id'] = 'wrong-decision'
            self.save()
            with self.assertRaises(cal.CalibrationError):
                self.label()
            self.assertEqual(cal.calibration_report(self.run)['eligible_outcome_n'], 0)
            record['router_decision_id'] = saved
        start['decision_id'] = 'conflicting-alias'
        self.save()
        with self.assertRaises(cal.CalibrationError):
            self.label()

    def test_real_14_review_writer_and_computed_verdict_link(self):
        review_ledger = importlib.import_module('review_calibration')
        xreview = importlib.import_module('xreview')
        report = self.run / 'review.md'
        report.write_text('No findings.\nVERDICT: pass\n')
        reference = dict(attempt_id='review-real-api', milestone='m1', round=1,
                         report=str(report), verdict='pass')
        review_ledger.append_event(self.run, {**reference, 'type': 'review', 'status': 'running'})
        code, reason = xreview.verdict(report, run_dir=self.run, milestone='m1')
        self.assertEqual(code, 0)
        review_ledger.finish_review(self.run, reference['attempt_id'], 'complete', reason)
        self.assertTrue(self.label(review=reference))
        coverage = cal.calibration_report(self.run)
        self.assertEqual(coverage['eligible_outcome_n'], 1)
        self.assertEqual(coverage['review_linked_labels_n'], 1)
        self.assertFalse(coverage['fit_ready'])

    def test_high_tiers_need_authoritative_pass(self):
        for tier in (2, 3):
            with self.subTest(tier=tier):
                self.events.clear(); self.start('a', tier=tier); self.save()
                with self.assertRaises(cal.CalibrationError): self.label()
                for verdict, status in [('block', 'complete'), ('needs_triage', 'complete'), ('pass', 'failed')]:
                    with self.assertRaises(cal.CalibrationError): self.label(review=self.review(verdict, status))
        self.assertTrue(self.label(review=self.review()))

    def test_low_tier_acceptance_is_explicit_operator_decision(self):
        for tier in (0, 1):
            with self.subTest(tier=tier):
                self.events.clear(); self.start('a', tier=tier); self.save()
                self.assertEqual(cal.calibration_report(self.run)['eligible_outcome_n'], 0)
                self.assertTrue(self.label())
                report = cal.calibration_report(self.run)
                self.assertEqual(report['operator_accepted_labels_n'], 1)
                self.assertEqual(report['initial_explicit_outcome_counts'], {'accepted': 1})
                self.assertTrue(any('operator acceptance' in w for w in report['warnings']))
        with self.assertRaises(cal.CalibrationError):
            cal.label_attempt(self.run, 'a', 'accepted', authority='', criteria_id='gate', revision='rev')

    def test_supplied_low_tier_review_is_always_validated(self):
        self.events.clear(); self.start('a', tier=0); self.save()
        ref = self.review()
        for field, bad in [('attempt_id', 'unknown'), ('milestone', 'm2'), ('round', 2), ('report', '/other'), ('verdict', 'block')]:
            with self.subTest(field=field), self.assertRaises(cal.CalibrationError):
                self.label(review={**ref, field: bad})
        self.assertTrue(self.label(review=ref))

    def test_computed_first_verdict_overrides_raw_later_word(self):
        ref = self.review(suffix='; reviewer VERDICT: block overridden')
        self.assertEqual(cal.review_snapshot(self.run, ref)['verdict'], 'pass')
        self.assertTrue(self.label(review=ref))
        self.assertEqual(cal.calibration_report(self.run)['eligible_outcome_n'], 1)

    def test_reservation_or_raw_pass_does_not_prove_review(self):
        ref = self.review()
        path = self.run / 'review-ledger.jsonl'
        records = [json.loads(line) for line in path.read_text().splitlines()]
        path.write_text(json.dumps(records[0]) + '\n')
        with self.assertRaises(cal.CalibrationError): self.label(review=ref)
        records[1]['reason'] = 'VERDICT: pass'
        path.write_text(''.join(json.dumps(e) + '\n' for e in records))
        with self.assertRaises(cal.CalibrationError): self.label(review=ref)

    def test_stale_review_result_withdraws_eligible_label(self):
        ref = self.review(); self.label(review=ref)
        with (self.run / 'review-ledger.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(type='review_result', attempt_id='review-a', status='failed', reason='revoked')) + '\n')
        report = cal.calibration_report(self.run)
        self.assertEqual(report['eligible_outcome_n'], 0)
        self.assertEqual(report['invalid_label_n'], 1)

    def test_replay_is_noop_and_conflicting_event_id_is_rejected(self):
        ref = self.review()
        self.assertTrue(self.label(review=ref, event_id='label-a'))
        first = (self.run / 'events.jsonl').read_bytes()
        with (self.run / 'review-ledger.jsonl').open('a') as stream:
            stream.write(json.dumps(dict(type='review', attempt_id='unrelated')) + '\n')
        self.assertFalse(self.label(review=ref, event_id='label-a'))
        self.assertEqual((self.run / 'events.jsonl').read_bytes(), first)
        self.assertTrue(self.label(review=ref))
        generated = (self.run / 'events.jsonl').read_bytes()
        self.assertFalse(self.label(review=ref))
        self.assertEqual((self.run / 'events.jsonl').read_bytes(), generated)
        with self.assertRaises(cal.CalibrationError): self.label('rejected', event_id='label-a')

    def test_parallel_replay_appends_once(self):
        ref = self.review()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.label(review=ref), range(8)))
        self.assertEqual(results.count(True), 1)
        self.assertEqual(cal.calibration_report(self.run)['label_event_counts'], {'accepted': 1})

    def test_explicit_parent_links_and_fixup_are_separate_from_acceptance(self):
        self.start('b', parent='a', root='a'); self.save()
        self.assertTrue(cal.link_attempt(self.run, 'b', 'decision-b'))
        self.assertFalse(cal.link_attempt(self.run, 'b', 'decision-b'))
        self.assertTrue(cal.link_fixup(self.run, 'a', 'b'))
        self.assertFalse(cal.link_fixup(self.run, 'a', 'b'))
        self.assertEqual(cal.calibration_report(self.run)['eligible_outcome_n'], 0)
        self.assertEqual(cal.calibration_report(self.run)['fixup_links_n'], 1)
        with self.assertRaises(cal.CalibrationError): cal.link_fixup(self.run, 'b', 'a')

    def test_unknown_dispatch_tuple_mismatch_and_cycles_fail_closed(self):
        with self.assertRaises(cal.CalibrationError): cal.link_attempt(self.run, 'nope', 'decision-a')
        self.events[-1]['actual']['model'] = 'sonnet'; self.save()
        with self.assertRaises(cal.CalibrationError): self.label('rejected')
        self.events.clear(); self.start('a', parent='b', root='a'); self.start('b', parent='a', root='a'); self.save()
        with self.assertRaises(cal.CalibrationError): self.label('rejected')

    def test_usage_and_serving_groups_never_guess_alias_identity(self):
        self.events.append(dict(v=1, id='a.end', at='2026-10-10T12:02:00Z', type='dispatch.end', dispatch='a', status='done',
                                usage={'input_tokens': 100, 'cached_input_tokens': 80, 'output_tokens': 0, 'duration_s': None},
                                serving_identity={'models_observed': ['claude-opus-5-5'], 'resolved_model': 'claude-opus-5-5',
                                                  'status': 'single_observed', 'resolved_effort': None, 'effort_status': 'requested_only'}))
        self.save(); before = (self.run / 'events.jsonl').read_bytes()
        report = cal.calibration_report(self.run)
        self.assertEqual(report['usage']['input_tokens']['sum_observed'], 100)
        self.assertEqual(report['usage']['output_tokens']['explicit_zero_n'], 1)
        self.assertIsNone(report['usage']['duration_s']['sum_observed'])
        self.assertIsNone(report['usage']['cost_usd']['sum_observed'])
        self.assertEqual(report['serving_identity']['single_stream_model_n'], 1)
        self.assertEqual(report['serving_identity']['proven_serving_tuple_n'], 0)
        self.assertEqual(report['tuple_coverage'][0]['model'], 'opus')
        self.assertEqual(report['tuple_basis'], 'requested_launch')
        self.assertEqual((self.run / 'events.jsonl').read_bytes(), before)

    def test_inconsistent_serving_and_partial_usage_remain_unknown(self):
        self.events.append(dict(v=1, id='a.end', at='2026-10-10T12:02:00Z', type='dispatch.end', dispatch='a', status='cancelled',
                                serving_identity={'status': 'single_observed', 'models_observed': ['a', 'b'], 'resolved_model': 'a'}))
        self.save(); report = cal.calibration_report(self.run)
        self.assertEqual(report['serving_identity']['single_stream_model_n'], 0)
        self.assertEqual(report['eligible_outcome_n'], 0)

    def test_symlink_ledgers_and_missing_locking_refuse_writes(self):
        outside = self.run / 'outside'
        outside.write_text('secret')
        (self.run / 'review-ledger.jsonl').symlink_to(outside)
        with self.assertRaises(cal.CalibrationError): cal.review_snapshot(self.run, dict(attempt_id='r', milestone='m', round=1, report='x', verdict='pass'))
        with mock.patch.object(cal, 'fcntl', None), self.assertRaises(cal.CalibrationError): self.label('rejected')
        self.assertEqual(outside.read_text(), 'secret')

    def test_end_provenance_mismatch_and_duplicate_ends_withhold_outcomes(self):
        self.events.append(dict(v=1, id='a.end', at='2026-10-10T12:02:00Z', type='dispatch.end',
                                dispatch='a', status='done', data_hash='c' * 64))
        self.save()
        with self.assertRaises(cal.CalibrationError): self.label(review=self.review())
        self.assertEqual(cal.calibration_report(self.run)['linked_attempts_n'], 0)
        self.events[-1].pop('data_hash')
        self.events.append({**self.events[-1], 'id': 'duplicate-end'})
        self.save()
        self.assertEqual(cal.calibration_report(self.run)['duplicate_dispatch_ends_n'], 1)
        with self.assertRaises(cal.CalibrationError): self.label(review=self.review())

    def test_cli_low_tier_label_and_high_tier_review_requirement(self):
        args = [sys.executable, str(SCRIPTS / 'router_calibration.py'), 'label', '--run', str(self.run),
                '--attempt-id', 'a', '--status', 'accepted', '--authority', 'operator',
                '--criteria-id', 'gate', '--revision', 'rev']
        high = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(high.returncode, 2)
        self.events.clear(); self.start('a', tier=1); self.save()
        low = subprocess.run(args, capture_output=True, text=True)
        self.assertEqual(low.returncode, 0, low.stderr)
        self.assertEqual(cal.calibration_report(self.run)['eligible_outcome_n'], 1)

    def test_unknown_process_status_and_private_prose_not_exported(self):
        self.events[0]['reason'] = 'PRIVATE-CUSTOMER-CONTEXT'
        self.events[1]['reason'] = 'PRIVATE-CUSTOMER-CONTEXT'
        self.save(); report = cal.calibration_report(self.run)
        self.assertNotIn('PRIVATE-CUSTOMER-CONTEXT', json.dumps(report))
        for status in ['done', 'complete', 'failed', 'timeout', 'cancelled']:
            with self.assertRaises(cal.CalibrationError): self.label(status)

    def test_cli_report_is_read_only_and_invalid_label_is_refused(self):
        before = (self.run / 'events.jsonl').read_bytes()
        result = subprocess.run([sys.executable, str(SCRIPTS / 'router_calibration.py'), 'report', '--run', str(self.run), '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['eligible_outcome_n'], 0)
        self.assertEqual((self.run / 'events.jsonl').read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
