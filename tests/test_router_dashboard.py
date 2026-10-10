"""Shadow evidence is additive, escaped, unvalidated, and never a dispatch selection."""
from datetime import datetime, timedelta, timezone
import importlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
dashboard = importlib.import_module('dashboard')
views = importlib.import_module('dashboard_herdr')
cal = importlib.import_module('router_calibration')


def event(**changes):
    return dict(type='router.shadow', decision_id='d1', status='scored', reason='lower expected total cost',
                recommendation={'runtime': 'codex', 'model': 'gpt-6-astra', 'effort': 'xhigh'},
                actual={'runtime': 'claude', 'model': 'opus', 'effort': 'high'},
                policy_hash='a' * 64, data_hash='b' * 64, dispatch='a', attempt_id='a',
                assumptions=['unmeasured quota conversion'], missing_data=['cash cost'],
                classifier={'validation': 'UNVALIDATED', 'status': 'classified'}, **changes)


class ShadowDashboardTest(unittest.TestCase):
    def test_no_shadow_section_or_terminal_rows_without_shadow_events(self):
        self.assertEqual(dashboard.shadow_section([]), '')
        self.assertEqual(views.shadow_lines([{'type': 'balance.pick'}]), [])

    def test_shadow_vs_actual_effort_and_provenance_are_distinct(self):
        page = dashboard.shadow_section([event()])
        self.assertIn('would pick codex gpt-6-astra xhigh', page)
        self.assertIn('claude opus high', page)
        self.assertIn('Static dispatch remains the actual choice', page)
        self.assertIn('UNVALIDATED classifier', page)
        self.assertIn('unmeasured quota conversion', page)
        self.assertIn('cash cost', page)
        self.assertIn('a' * 64, page)
        lines = '\n'.join(views.shadow_lines([event()]))
        self.assertIn('would pick codex gpt-6-astra xhigh', lines)
        self.assertIn('actual claude opus high', lines)
        self.assertIn('UNVALIDATED classifier', lines)

    def test_html_terminal_and_bidi_injection_are_neutralized(self):
        malicious = event()
        malicious['reason'] = '<script>alert(1)</script>\x1b[2J\nnext\u202ereversed'
        malicious['actual']['model'] = '<img src=x onerror=bad>'
        malicious['assumptions'] = ['\x1b]52;c;payload\x07']
        page = dashboard.shadow_section([malicious])
        self.assertNotIn('<script>', page)
        self.assertNotIn('<img', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertNotIn('\x1b', page)
        self.assertNotIn('\u202e', page)
        lines = '\n'.join(views.shadow_lines([malicious]))
        self.assertNotIn('\x1b', lines)
        self.assertNotIn('\x07', lines)
        self.assertNotIn('\u202e', lines)
        self.assertNotIn('\x1b', views.clip('unsafe\x1b[2J', 100))

    def test_skipped_null_recommendation_never_shows_as_a_pick(self):
        skipped = event(); skipped.update(status='skipped', recommendation=None, reason='missing comparable evidence')
        page = dashboard.shadow_section([skipped])
        self.assertIn('skipped / no recommendation', page)
        self.assertNotIn('would pick', page)
        self.assertIn('missing comparable evidence', page)
        self.assertIn('skipped', views.event_line(skipped))

    def test_classifier_presence_is_always_marked_unvalidated(self):
        forged = event(); forged['classifier'] = {'validation': 'VALIDATED'}
        self.assertIn('UNVALIDATED classifier', dashboard.shadow_section([forged]))
        absent = event(); absent.pop('classifier')
        self.assertNotIn('UNVALIDATED classifier', dashboard.shadow_section([absent]))

    def test_manual_weights_and_disabled_classifier_do_not_claim_a_model_call(self):
        for status, label in (("manual_weights", "manual weights; no classifier call"),
                              ("disabled", "classifier disabled")):
            row = event(); row["classifier"] = {"status": status}
            self.assertIn(label, dashboard.shadow_section([row]))
            self.assertIn(label, cal.shadow_text(row))
            self.assertNotIn("UNVALIDATED classifier", cal.shadow_text(row))

    def test_secrets_redacted_before_truncation_and_controls(self):
        forged = event(); forged['reason'] = 'x' * 600 + ' sk-' + 'x' * 48
        self.assertIn('[redacted]', dashboard.shadow_section([forged]))
        self.assertIn('[redacted]', cal.shadow_text(forged))

    def test_real_html_render_and_status_view_do_not_mutate_events(self):
        with tempfile.TemporaryDirectory() as folder:
            primary = Path(folder).resolve(); run = primary / '.harness/tasks/t'; run.mkdir(parents=True)
            start = dict(v=1, id='a.start', at='2026-10-10T12:00:00Z', type='dispatch.start',
                         dispatch='a', role='implementer', runtime='claude', model='opus', effort='high')
            shadow = dict(v=1, id='a.shadow', at='2026-10-10T12:00:01Z', **event())
            source = run / 'events.jsonl'; source.write_text(json.dumps(start) + '\n' + json.dumps(shadow) + '\n')
            before = source.read_bytes()
            now = datetime(2026, 10, 10, 12, 1, tzinfo=timezone.utc)
            vendors = {'vendors': {}, 'error': None}
            with mock.patch.object(dashboard, 'vendor_state', return_value=vendors):
                page = dashboard.render(run, primary, run / 'dashboard.html', now, timedelta(minutes=15))
            self.assertIn('<section id="router-shadow">', page)
            self.assertIn('would pick codex', page)
            self.assertIn('<td class="model">opus</td>', page)
            status = '\n'.join(views.status_lines(run, primary, now, None))
            self.assertIn('⚖ shadow:', status)
            self.assertIn('actual claude opus high', status)
            self.assertEqual(source.read_bytes(), before)
            self.assertFalse((run / 'dashboard.html').exists())


if __name__ == '__main__':
    unittest.main()
