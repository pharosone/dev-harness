"""Calibrated decisions, append-only outcomes and correction fixtures."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HARNESS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HARNESS / 'scripts'))
import review_calibration as calibration
import xreview
from project import OverlayError

PILOT = {'stage': 'pilot', 'invariants': []}


def finding(likelihood='will', impact='functional', extra='', label='Major', defect='broken'):
    text = (f'- [{label}] src/app.py:2 — {defect} — {likelihood}/{impact} — '
            f'entry() -> app() — input -> failure — evidence: src/app.py:2{extra}')
    return calibration.parse_findings(text)[0]


class MatrixTest(unittest.TestCase):
    def test_every_cell_pilot_and_production(self):
        for stage in ('pilot', 'production'):
            for likelihood in calibration.LIKELIHOODS:
                for impact in calibration.IMPACTS:
                    for workaround in (None, 'none', 'edit title'):
                        with self.subTest(stage=stage, likelihood=likelihood, impact=impact, workaround=workaround):
                            f = finding(likelihood, impact)
                            f['workaround'] = workaround
                            expected = ((impact == 'severe' and (likelihood != 'edge' or stage == 'production'))
                                        or (impact == 'functional' and (likelihood == 'will'
                                            or likelihood == 'plausible' and workaround == 'none')))
                            result = calibration.compute(f, {'stage': stage, 'invariants': []})
                            self.assertEqual(result['verdict'], 'block' if expected else 'backlog')
                            self.assertEqual(result['label'], ('Critical' if impact == 'severe' else 'Major') if expected else 'Minor')

    def test_exact_overlay_invariant_required(self):
        f = finding('edge', 'severe', ' — invariant: no public leak')
        self.assertEqual(calibration.compute(f)['verdict'], 'backlog')
        self.assertEqual(calibration.compute(f, {'stage': 'pilot', 'invariants': ['no public leak']})['verdict'], 'block')
        self.assertEqual(calibration.compute(f, {'stage': 'pilot', 'invariants': ['some other invariant']})['verdict'], 'backlog')

    def test_fields_and_memory_downgrade(self):
        f = finding('plausible', 'functional', ' — workaround: none', label='Critical')
        self.assertEqual((f['likelihood'], f['impact'], f['reachable_path'], f['evidence'], f['workaround']),
                         ('plausible', 'functional', 'entry() -> app()', 'src/app.py:2', 'none'))
        for key in ('location', 'defect', 'likelihood', 'impact', 'reachable_path', 'scenario', 'evidence'):
            bad = {**f, key: ''}
            self.assertEqual(calibration.compute(bad)['label'], 'Minor', key)
        for evidence in ('memory', 'none', 'unknown', 'src/app.py'):
            self.assertEqual(calibration.compute({**f, 'evidence': evidence})['label'], 'Minor')
        old = calibration.parse_findings('- [Critical] src/app.py:2 — broken — input -> failure')[0]
        self.assertEqual(calibration.compute(old)['verdict'], 'backlog')

    def test_round_two_new_only_severe(self):
        f = finding()
        self.assertEqual(calibration.compute(f, round_number=2)['verdict'], 'backlog')
        self.assertEqual(calibration.compute(f, round_number=2, previous_ids={calibration.finding_id(f)})['verdict'], 'block')
        moved = {**f, 'location': 'src/app.py:12', 'evidence': 'src/app.py:12'}
        self.assertEqual(calibration.compute(moved, round_number=2, previous_ids={calibration.finding_id(f)})['verdict'], 'block')
        self.assertEqual(calibration.compute(finding(impact='severe'), round_number=2)['verdict'], 'block')
        self.assertEqual(calibration.compute(finding('edge', 'severe'), round_number=2)['verdict'], 'backlog')

    def test_product_day_expected_computed(self):
        cases = json.loads((HARNESS / 'tests/fixtures/review-calibration.json').read_text())
        self.assertEqual(len(cases), 9)
        for case in cases:
            with self.subTest(case=case['name']):
                f, = calibration.parse_findings(case['finding'])
                self.assertEqual(calibration.compute(f, case['review'])['verdict'], case['expected'])


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / 'run'

    def record(self, f=None, milestone='M1', round_number=1):
        f = f or finding()
        calibration.append_event(self.run, {'type': 'review', 'milestone': milestone, 'round': round_number}, reserve_round=True)
        calibration.record_findings(self.run, [f], PILOT, milestone, round_number, set())
        return list(calibration.snapshots(self.run))[-1]

    def test_ledger_preserves_reviewer_computation_triage_and_outcome(self):
        identity = self.record(finding('edge', 'functional', label='Critical'))
        calibration.triage(self.run, identity, 'backlog', 'code confirms rare startup race')
        calibration.mark_outcome(self.run, identity, 'failure', 'user-report', 'startup later failed')
        events = calibration.read_events(self.run)
        self.assertEqual([e['type'] for e in events], ['review', 'finding', 'triage', 'outcome'])
        state = calibration.snapshots(self.run)[identity]
        self.assertEqual(state['reviewer_rating'], 'Critical')
        self.assertEqual(state['computed_verdict'], 'backlog')
        self.assertEqual(state['orchestrator_triage'], 'backlog')
        self.assertTrue(state['missed'])
        self.assertIn('failure', (self.run / 'backlog.md').read_text())

    def test_triage_validates_code_evidence_and_matrix(self):
        identity = self.record()
        with self.assertRaisesRegex(ValueError, 'contradicts matrix'):
            calibration.triage(self.run, identity, 'backlog', 'seems narrow')
        calibration.triage(self.run, identity, 'backlog', 'code shows startup-only race', likelihood='edge')
        self.assertEqual(calibration.snapshots(self.run)[identity]['likelihood'], 'edge')
        calibration.triage(self.run, identity, 'backlog', 'unverified; no code read', evidence='memory')
        self.assertEqual(calibration.snapshots(self.run)[identity]['evidence'], 'memory')
        with self.assertRaises(ValueError):
            calibration.triage(self.run, identity, 'block', 'no code', likelihood='will', evidence='memory')

    def test_over_blocked_user_override_and_harmless(self):
        identity = self.record()
        calibration.mark_outcome(self.run, identity, None, 'user-report', 'owner accepts recoverable failure', 'backlog')
        state = calibration.snapshots(self.run)[identity]
        self.assertTrue(state['over_blocked'])
        self.assertIsNone(state['outcome'])
        calibration.mark_outcome(self.run, identity, 'harmless', 'review', 'scenario did not happen')
        self.assertTrue(calibration.snapshots(self.run)[identity]['over_blocked'])
        # An accepted blocker can be confirmed, without either calibration error flag.
        second = self.record(finding(defect='second'), milestone='M2')
        calibration.mark_outcome(self.run, second, 'confirmed', 'fix-up', 'fix reproduced the failure')
        self.assertFalse(calibration.snapshots(self.run)[second]['over_blocked'])

    def test_round_limit_and_previous_observation(self):
        identity = self.record()
        self.record(round_number=2)
        self.assertEqual(len(calibration.snapshots(self.run)), 1)
        self.assertEqual(len([e for e in calibration.read_events(self.run) if e['type'] == 'finding']), 2)
        with self.assertRaisesRegex(ValueError, 'at most 2'):
            self.record(round_number=2)
        other = self.record(milestone='M2')
        self.assertNotEqual(identity, other)
        with self.assertRaisesRegex(ValueError, 'expected round 1'):
            self.record(milestone='M3', round_number=2)

    def test_multiple_lenses_share_round_and_cannot_reopen_it(self):
        for report in ('qa.md', 'security.md'):
            calibration.append_event(self.run, {'type': 'review', 'milestone': 'M1', 'round': 1, 'report': report}, reserve_round=True)
        self.assertEqual(calibration.summary([self.run])['runs'][0]['milestones']['M1']['rounds_used'], 1)
        calibration.append_event(self.run, {'type': 'review', 'milestone': 'M1', 'round': 2, 'report': 'qa-fixed.md'}, reserve_round=True)
        with self.assertRaisesRegex(ValueError, 'cannot return to round 1'):
            calibration.append_event(self.run, {'type': 'review', 'milestone': 'M1', 'round': 1, 'report': 'new-lens.md'}, reserve_round=True)
        self.assertEqual(calibration.summary([self.run])['runs'][0]['milestones']['M1']['rounds_used'], 2)

    def test_record_role_report_and_verify_missing_fields_via_cli(self):
        report = self.root / 'qa.md'
        report.write_text('- [Major] src/app.py:2 — broken — no canonical fields\n')
        self.assertEqual(calibration.main(['record', '--run', str(self.run), '--root', str(self.root), '--report', str(report), '--milestone', 'M1']), 0)
        identity, = calibration.snapshots(self.run)
        self.assertEqual(calibration.snapshots(self.run)[identity]['computed_verdict'], 'backlog')
        args = ['triage', '--run', str(self.run), '--id', identity, '--decision', 'block', '--reason', 'checked main path',
                '--likelihood', 'will', '--impact', 'functional', '--reachable-path', 'main() -> app()',
                '--scenario', 'normal input -> failure', '--evidence', 'src/app.py:2']
        self.assertEqual(calibration.main(args), 0)
        self.assertEqual(calibration.snapshots(self.run)[identity]['orchestrator_triage'], 'block')
        self.assertEqual(calibration.main(['outcome', '--run', str(self.run), '--id', identity, '--user-override', 'backlog',
                                          '--source', 'user-report', '--reason', 'owner accepts']), 0)
        self.assertTrue(calibration.snapshots(self.run)[identity]['over_blocked'])

    def test_backlog_retains_notes_deduplicates_and_removes_blocker(self):
        self.run.mkdir()
        (self.run / 'backlog.md').write_text('# Owner notes\n\nKeep this.\n')
        identity = self.record(finding('edge'))
        calibration.write_backlog(self.run)
        text = (self.run / 'backlog.md').read_text()
        self.assertIn('Keep this.', text)
        self.assertEqual(text.count(identity), 1)
        calibration.triage(self.run, identity, 'block', 'main path proven', likelihood='will')
        self.assertNotIn(identity, (self.run / 'backlog.md').read_text())

    def test_rates_thresholds_and_across_runs(self):
        for i in range(3):
            identity = self.record(finding(defect=f'blocker {i}'), milestone=f'B{i}')
            calibration.mark_outcome(self.run, identity, 'harmless', 'review', 'harmless')
            identity = self.record(finding('edge', 'severe', defect=f'backlog {i}'), milestone=f'E{i}')
            calibration.mark_outcome(self.run, identity, 'failure', 'incident', 'real failure')
        data = calibration.summary([self.run, self.run])
        self.assertEqual(len(data['runs']), 1)
        self.assertEqual(data['across_runs']['over_blocked_rate'], 1)
        self.assertEqual(data['across_runs']['missed_rate'], 1)
        self.assertEqual(len(data['suggestions']), 2)
        self.assertEqual(len(data['runs'][0]['suggestions']), 2)
        self.assertIn('product invariant', data['suggestions'][1])
        self.assertEqual(data['runs'][0]['milestones']['B0'], {'blockers': 1, 'rounds_used': 1})
        self.assertFalse(calibration.summary([self.run], over_block_threshold=1, missed_threshold=1)['suggestions'])
        self.assertFalse(calibration.summary([self.run], min_outcomes=4)['suggestions'])
        another = self.root / 'other'
        calibration.append_event(another, {'type': 'review', 'milestone': 'M', 'round': 1}, reserve_round=True)
        calibration.record_findings(another, [finding()], PILOT, 'M', 1, set())
        both = calibration.summary([self.run, another])
        self.assertEqual(both['across_runs']['blockers'], 4)
        self.assertEqual(both['across_runs']['over_blocked_rate'], .75)
        self.assertIn('1/2 rounds', calibration.render_summary(both))

    def test_corrupt_ledger_and_unknown_id_are_errors(self):
        with self.assertRaisesRegex(ValueError, 'unknown finding'):
            calibration.mark_outcome(self.run, 'missing', 'failure', 'incident', 'failure')
        self.run.mkdir()
        calibration.ledger_path(self.run).write_text('{bad json}\n')
        with self.assertRaisesRegex(ValueError, 'invalid ledger'):
            calibration.summary([self.run])

    def test_sanitized_correction_fixture_never_copies_run_text(self):
        identity = self.record()
        calibration.triage(self.run, identity, 'backlog', 'private operator path was narrow', likelihood='edge')
        case = {'name': 'owner-correction', 'finding': finding('edge')['raw'], 'review': PILOT}
        out = self.root / 'fixtures.json'
        calibration.export_fixture(self.run, identity, case, out)
        exported, = json.loads(out.read_text())
        self.assertEqual(exported['expected'], 'backlog')
        self.assertNotIn('private operator', out.read_text())
        for private in ('/Users/private/person', 'https://private.example', 'person@example.invalid'):
            with self.assertRaisesRegex(ValueError, 'sanitize'):
                calibration.export_fixture(self.run, identity, {**case, 'name': private}, out)
        self.assertEqual(len(json.loads(out.read_text())), 1)

    def test_cli_report_and_validation(self):
        self.record()
        result = subprocess.run([sys.executable, str(HARNESS / 'scripts/review_calibration.py'), 'report', '--run', str(self.run), '--json'], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['across_runs']['blockers'], 1)
        self.assertEqual(calibration.main(['report', '--run', str(self.run), '--min-outcomes', '0']), 2)
        self.assertEqual(calibration.main(['report', '--run', str(self.root / 'missing')]), 2)


class OverlayPolicyTest(unittest.TestCase):
    def test_defaults_validation_and_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = os.environ.pop('HARNESS_PROJECT', None)
            try:
                self.assertEqual(calibration.review_config(root), PILOT)
                path = root / 'harness/project/project.json'
                path.parent.mkdir(parents=True)
                for review in (None, [], {'stage': 'staging'}, {'stage': False}, {'invariants': 'text'},
                               {'invariants': ['']}, {'invariants': [1]}, {'invariants': ['a\nb']}, {'unknown': True}):
                    path.write_text(json.dumps({'review': review}))
                    with self.assertRaises(OverlayError, msg=str(review)):
                        calibration.review_config(root)
                path.write_text(json.dumps({'review': {'stage': 'production', 'invariants': ['no public leak']}}))
                self.assertEqual(calibration.review_config(root)['stage'], 'production')
                os.environ['HARNESS_PROJECT'] = 'other'
                self.assertEqual(calibration.review_config(root), PILOT)
            finally:
                if old is None:
                    os.environ.pop('HARNESS_PROJECT', None)
                else:
                    os.environ['HARNESS_PROJECT'] = old


if __name__ == '__main__':
    unittest.main()
