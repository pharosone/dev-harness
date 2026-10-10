"""Evidence validation, exact settings, and reproducible offline chart rendering."""
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('benchmarks', ROOT / 'scripts' / 'benchmarks.py')
benchmarks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmarks)


def fixture():
    envelopes = {}
    model = dict(id='sample', vendor='example', family='Current', runtime='none',
                 aliases=[], price={'input': None, 'output': None, 'unit': 'USD/Mtok'},
                 context=None, url='https://example.org/model', as_of='2026-10-10')
    benchmark = dict(id='sample-test', name='Sample & <test>', description='Test measurement',
                     task_traits=['terminal'], normalization={'min': 0, 'max': 100, 'higher_is_better': True},
                     unit='percent', active=True, saturation_note='Not saturated',
                     url='https://example.org/benchmark')
    result = dict(id='observation', model_id='sample', benchmark_id='sample-test', value=42,
                  unit='percent', setting='high', harness='shell <&>', date='2026-10-10',
                  url='https://example.org/result', self_reported=False, as_of='2026-10-10',
                  date_type='observation', effort='high')
    for key, row in [('models', model), ('benchmarks', benchmark), ('results', result)]:
        envelopes[key] = {'schema_version': 1, 'as_of': '2026-10-10', key: [row]}
    return envelopes


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.data = fixture()

    def validate(self):
        return benchmarks.validate_dataset(self.data, '2026-10-10')

    def test_valid_nullable_model_metadata(self):
        self.assertEqual(self.validate(), {'errors': [], 'warnings': []})

    def test_required_fields(self):
        del self.data['results']['results'][0]['harness']
        self.assertIn('missing harness', '\n'.join(self.validate()['errors']))

    def test_wrong_shapes_do_not_crash(self):
        for data in [None, [], {}, {'models': []}, {'models': {'models': 'wrong'}}]:
            with self.subTest(data=data):
                self.assertTrue(benchmarks.validate_dataset(data, '2026-10-10')['errors'])
        self.data['models']['models'].extend([[], None, 'model'])
        self.assertTrue(self.validate()['errors'])

    def test_schema_version_is_integer_one(self):
        self.data['models']['schema_version'] = True
        self.assertTrue(self.validate()['errors'])

    def test_nonfinite_scores_and_boolean_scores_rejected(self):
        for value in [float('nan'), float('inf'), -float('inf'), True, '42', None]:
            with self.subTest(value=value):
                self.data['results']['results'][0]['value'] = value
                self.assertTrue(self.validate()['errors'])

    def test_missing_references_and_unit_mismatch(self):
        for key, value in [('model_id', 'unknown'), ('benchmark_id', 'unknown'), ('unit', 'Elo')]:
            with self.subTest(key=key):
                data = fixture()
                data['results']['results'][0][key] = value
                self.assertTrue(benchmarks.validate_dataset(data, '2026-10-10')['errors'])

    def test_source_url_required_and_checked(self):
        for value in ['', 'example.org', 'javascript:alert(1)', 'https://', 'https://host/path space', 'https://user:secret@host']:
            with self.subTest(value=value):
                self.data['results']['results'][0]['url'] = value
                self.assertTrue(self.validate()['errors'])

    def test_source_query_urls_allowed(self):
        self.data['results']['results'][0]['url'] = 'https://example.org/page?a=b&c=d'
        self.assertFalse(self.validate()['errors'])

    def test_invalid_future_and_after_snapshot_dates(self):
        for value in ['2026-2-01', '2026-02-30', '2026-10-11', '2026-10-09T00:00:00Z']:
            with self.subTest(value=value):
                self.data['results']['results'][0]['date'] = value
                self.assertTrue(self.validate()['errors'])

    def test_observation_and_publication_dates_distinct(self):
        row = self.data['results']['results'][0]
        row['date'] = '2026-09-28'
        self.assertTrue(self.validate()['errors'])
        row['date_type'] = 'publication'
        self.assertFalse(self.validate()['errors'])

    def test_snapshot_dates_agree(self):
        self.data['models']['as_of'] = '2026-10-09'
        self.assertTrue(self.validate()['errors'])

    def test_staleness_strictly_greater_than_sixty_days(self):
        day = dt.date(2026, 10, 10)
        row = self.data['results']['results'][0]
        row['date_type'] = 'publication'
        row['date'] = (day - dt.timedelta(days=60)).isoformat()
        self.assertFalse(self.validate()['warnings'])
        row['date'] = (day - dt.timedelta(days=61)).isoformat()
        self.assertTrue(self.validate()['warnings'])
        self.assertFalse(self.validate()['errors'])

    def test_ids_duplicate_and_path_traversal(self):
        self.data['results']['results'].append(copy.deepcopy(self.data['results']['results'][0]))
        self.assertIn('duplicate id', '\n'.join(self.validate()['errors']))
        self.data['benchmarks']['benchmarks'][0]['id'] = '../outside'
        self.assertIn('invalid id', '\n'.join(self.validate()['errors']))

    def test_same_observation_with_new_id_rejected(self):
        row = copy.deepcopy(self.data['results']['results'][0])
        row['id'] = 'other'
        self.data['results']['results'].append(row)
        self.assertIn('duplicate observation', '\n'.join(self.validate()['errors']))

    def test_unknown_trait_and_normalization(self):
        row = self.data['benchmarks']['benchmarks'][0]
        row['task_traits'] = ['imagined_trait']
        self.assertTrue(self.validate()['errors'])
        row['task_traits'] = ['terminal']
        for bounds in [(1, 1), (2, 1), (True, 100), (0, float('nan'))]:
            row['normalization'].update(min=bounds[0], max=bounds[1])
            self.assertTrue(self.validate()['errors'])

    def test_planned_runtime_metadata_required(self):
        row = self.data['models']['models'][0]
        row['runtime'] = 'planned'
        self.assertTrue(self.validate()['errors'])
        row.update(planned_runtime='gemini', availability_note='Gemini CLI planned; not executable today')
        self.assertFalse(self.validate()['errors'])
        row['planned_runtime'] = 'invented'
        self.assertTrue(self.validate()['errors'])
        row['runtime'] = 'none'
        self.assertTrue(self.validate()['errors'])

    def test_alias_collision_context_and_price_types(self):
        row = self.data['models']['models'][0]
        other = copy.deepcopy(row)
        other.update(id='other', aliases=['sample'])
        self.data['models']['models'].append(other)
        self.assertTrue(self.validate()['errors'])
        self.data['models']['models'].pop()
        for value in [True, -1, 1.5, '1000']:
            row['context'] = value
            self.assertTrue(self.validate()['errors'])
        row['context'] = 1000
        row['price']['input'] = -1
        self.assertTrue(self.validate()['errors'])

    def test_fallbacks_exist_and_stay_with_vendor(self):
        row = self.data['models']['models'][0]
        for targets in [['missing'], ['sample'], 'sample']:
            row['fallback_models'] = targets
            self.assertTrue(self.validate()['errors'])
        other = copy.deepcopy(row)
        other.update(id='other', vendor='elsewhere', fallback_models=[])
        self.data['models']['models'].append(other)
        row['fallback_models'] = ['other']
        self.assertTrue(self.validate()['errors'])

    def test_confidence_interval_is_score_interval(self):
        row = self.data['results']['results'][0]
        for interval in [[], {'lower': 50, 'upper': 30}, {'lower': 50, 'upper': 60}, {'lower': float('nan'), 'upper': 50}]:
            row['confidence_interval'] = interval
            self.assertTrue(self.validate()['errors'])
        row['confidence_interval'] = {'lower': 40, 'upper': 45}
        self.assertFalse(self.validate()['errors'])
        row['confidence_interval'] = {'lower': None, 'upper': None}
        self.assertFalse(self.validate()['errors'])

    def test_vote_counts_are_integer_or_null(self):
        row = self.data['results']['results'][0]
        for value in [True, -1, 3.5, '100']:
            row['vote_count'] = value
            self.assertTrue(self.validate()['errors'])
        row['vote_count'] = None
        self.assertFalse(self.validate()['errors'])

    def test_observational_agent_is_not_pairwise_votes(self):
        benchmark = self.data['benchmarks']['benchmarks'][0]
        benchmark.update(category='observational_agent', unit='percentage_points')
        row = self.data['results']['results'][0]
        row.update(confidence_interval={'lower': 40, 'upper': 45}, vote_count=100,
                   style_control='not_offered', statistic='causal net improvement',
                   unit='percentage_points', confidence_level=.95, session_count=300,
                   observation_count=1000, missing_metadata={'vote_count': 'Not pairwise voting'})
        self.assertTrue(self.validate()['errors'])
        row['vote_count'] = None
        self.assertFalse(self.validate()['errors'])

    def test_web_research_trait_and_unverified_errors(self):
        self.data['benchmarks']['benchmarks'][0]['task_traits'] = ['web_research']
        row = self.data['results']['results'][0]
        row.update(n_samples=496, reported_error_pp=3.1, uncertainty_kind='source_error_unverified',
                   confidence_interval=None, confidence_level=None)
        self.assertFalse(self.validate()['errors'])
        row['confidence_interval'] = {'lower': 38.9, 'upper': 45.1}
        self.assertIn('unverified error', '\n'.join(self.validate()['errors']))
        row['confidence_interval'] = None
        row['reported_error_pp'] = -1
        self.assertTrue(self.validate()['errors'])
        row['reported_error_pp'] = 3.1
        row['n_samples'] = True
        self.assertTrue(self.validate()['errors'])

    def test_unexplained_arena_nulls_rejected(self):
        self.data['benchmarks']['benchmarks'][0]['category'] = 'preference'
        self.data['results']['results'][0].update(confidence_interval=None, vote_count=None,
                                                style_control=None, statistic='preference rating')
        self.assertIn('unexplained null', '\n'.join(self.validate()['errors']))


class RenderingTests(unittest.TestCase):
    def test_fixed_normalization_and_lower_is_better(self):
        benchmark = fixture()['benchmarks']['benchmarks'][0]
        self.assertEqual(benchmarks.normalize(25, benchmark), .25)
        benchmark['normalization']['higher_is_better'] = False
        self.assertEqual(benchmarks.normalize(25, benchmark), .75)
        self.assertEqual(benchmarks.normalize(-10, benchmark), 1)
        with self.assertRaises(ValueError):
            benchmarks.normalize(float('nan'), benchmark)

    def test_svg_escape_hatching_missing_and_confidence_interval(self):
        data = fixture()
        row = data['results']['results'][0]
        row.update(self_reported=True, confidence_interval={'lower': 35, 'upper': 49})
        svg = benchmarks.bar_chart(data['benchmarks']['benchmarks'][0], [row])
        ET.fromstring(svg)
        self.assertIn('fill="url(#hatch)"', svg)
        self.assertIn('Sample &amp; &lt;test&gt;', svg)
        self.assertNotIn('width="100%"', svg)
        self.assertIn('stroke="#111827"', svg)
        self.assertIn('No verified observations', benchmarks.bar_chart(data['benchmarks']['benchmarks'][0], []))

    def test_heatmap_preserves_effort_and_missing_cells(self):
        data = fixture()
        second = copy.deepcopy(data['results']['results'][0])
        second.update(id='medium-score', effort='medium', setting='medium', value=10)
        rows = data['results']['results'] + [second]
        svg = benchmarks.heatmap(data['models']['models'], data['benchmarks']['benchmarks'], rows)
        ET.fromstring(svg)
        self.assertIn('sample / high', svg)
        self.assertIn('sample / medium', svg)
        self.assertIn('42.0', svg)
        self.assertIn('10.0', svg)

    def test_signed_causal_effect_bars_start_at_zero(self):
        data = fixture()
        benchmark = data['benchmarks']['benchmarks'][0]
        benchmark['normalization'].update(min=-100, max=100)
        row = data['results']['results'][0]
        row['value'] = 10
        svg = benchmarks.bar_chart(benchmark, [row])
        self.assertIn('x="700.00"', svg)
        self.assertIn('width="27.00"', svg)
        row['value'] = -10
        self.assertIn('x="673.00"', benchmarks.bar_chart(benchmark, [row]))

    def test_mercor_error_labels_are_not_ci_lines(self):
        data = fixture()
        row = data['results']['results'][0]
        row.update(reported_error_pp=3.1, n_samples=496, confidence_interval=None)
        svg = benchmarks.bar_chart(data['benchmarks']['benchmarks'][0], [row])
        ET.fromstring(svg)
        self.assertIn('±3.1*', svg)
        self.assertIn('n_samples is not successes', svg)
        self.assertNotIn('stroke="#111827"', svg)

    def test_render_is_deterministic_and_preserves_other_owned_docs(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            (out / 'router-eval.md').write_text('owner data')
            (out / 'charts').mkdir()
            (out / 'charts' / 'old.svg').write_text('old')
            (out / 'charts' / 'handmade.svg').write_text('handmade')
            (out / 'charts-manifest.json').write_text(json.dumps({'files': ['charts/old.svg', '../outside.svg']}))
            report = benchmarks.render(fixture(), out, '2026-10-10')
            self.assertEqual(report['charts'], 2)
            self.assertFalse((out / 'charts' / 'old.svg').exists())
            self.assertEqual((out / 'charts' / 'handmade.svg').read_text(), 'handmade')
            self.assertEqual((out / 'router-eval.md').read_text(), 'owner data')
            first = {p.name: p.read_bytes() for p in (out / 'charts').glob('*.svg')}
            benchmarks.render(fixture(), out, '2026-10-10')
            self.assertEqual(first, {p.name: p.read_bytes() for p in (out / 'charts').glob('*.svg')})
            self.assertTrue((out / 'coverage.csv').exists())

    def test_invalid_data_does_not_render(self):
        data = fixture()
        data['results']['results'][0]['value'] = None
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                benchmarks.render(data, temporary, '2026-10-10')
            self.assertFalse((Path(temporary) / 'README.md').exists())

    def test_cli_handles_invalid_json(self):
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / 'models.json').write_text('{')
            run = subprocess.run([sys.executable, str(ROOT / 'scripts/benchmarks.py'),
                                  'validate', '--data-dir', temporary], capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertTrue(json.loads(run.stdout)['errors'])
            self.assertNotIn('Traceback', run.stderr)


class PublishedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = benchmarks.load_dataset()

    def test_published_dataset_validates(self):
        self.assertEqual(benchmarks.validate_dataset(self.data, '2026-10-10')['errors'], [])

    def test_owner_scope_latest_four_families_only(self):
        models = self.data['models']['models']
        self.assertEqual({m['id'] for m in models}, {'claude-fable-5-1', 'claude-opus-5-5',
                         'claude-sonnet-5-5', 'claude-haiku-5-5', 'gpt-6-astra', 'gpt-6.1-sol',
                         'gpt-6-luna', 'gemini-4-argon', 'grok-4.7'})
        self.assertEqual({m['vendor'] for m in models}, {'anthropic', 'openai', 'google', 'xai'})
        for model in models:
            if model['vendor'] in ('google', 'xai'):
                self.assertEqual(model['runtime'], 'planned')
                self.assertIn(model['planned_runtime'], ('gemini', 'grok'))
        self.assertTrue(all(not m.get('fallback_models') for m in models))

    def test_exact_worked_example_effort_rows_exist(self):
        results = self.data['results']['results']
        expected = {('claude-opus-5-5', 'high', 'terminal-bench-4.0-aa'): 56.5656565656566,
                    ('gpt-6-astra', 'medium', 'terminal-bench-4.0-aa'): 49.4949494949495,
                    ('claude-opus-5-5', 'high', 'automationbench-aa'): 63.20677483186213,
                    ('gpt-6-astra', 'medium', 'automationbench-aa'): 64.59498850561176}
        for key, value in expected.items():
            rows = [r for r in results if (r['model_id'], r.get('effort'), r['benchmark_id']) == key]
            self.assertEqual(len(rows), 1)
            self.assertAlmostEqual(rows[0]['value'], value, places=10)
            self.assertFalse(rows[0]['self_reported'])
            self.assertEqual(rows[0]['date_type'], 'observation')

    def test_search_gap_and_longer_query_report_only(self):
        results = self.data['results']['results']
        index = {b['id']: b for b in self.data['benchmarks']['benchmarks']}
        self.assertFalse(any(r['benchmark_id'] == 'arena-search' for r in results))
        self.assertFalse(index['arena-text-longer-query']['active'])
        self.assertEqual(index['arena-text-coding']['category'], 'preference')
        self.assertEqual(index['arena-agent-code-net-improvement']['category'], 'observational_agent')
        self.assertFalse(index['arena-agent-code-net-improvement']['active'])

    def test_arena_source_fields_preserved(self):
        rows = [r for r in self.data['results']['results'] if r['benchmark_id'].startswith('arena-')]
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(len(row['source_snapshot_sha256']), 64)
            self.assertIn('confidence_interval', row)
            self.assertIn('vote_count', row)
            self.assertIn('style_control', row)
            self.assertIn('statistic', row)
            if row['benchmark_id'] == 'arena-agent-code-net-improvement':
                self.assertIsNone(row['vote_count'])
                self.assertEqual(row['confidence_level'], .95)
                self.assertGreater(row['session_count'], 0)
                self.assertGreater(row['observation_count'], 0)
                self.assertEqual(row['unit'], 'percentage_points')
            elif row['benchmark_id'].startswith('arena-text'):
                self.assertEqual(row['style_control'], 'enabled')
                self.assertIsNone(row['confidence_level'])

    def test_retained_provenance_extracts_match_recorded_scores(self):
        provenance = json.loads((ROOT / 'data/benchmarks/source-extracts.json').read_text())
        index = {o['source_slug']: o for o in provenance['aa']}
        for effort_source in provenance.get('efforts', []):
            match = next(o for o in index.values() if o['url'] == effort_source['url'])
            match['fields'].update(effort_source['fields'])
        for row in self.data['results']['results']:
            if row['id'].startswith('aa-'):
                source = next(o for o in index.values() if o['url'] == row['url'])
                self.assertEqual(source['sha256'], row['source_snapshot_sha256'])
                value = source['fields']
                for part in row['source_field'].split('.'):
                    value = value[part]
                self.assertAlmostEqual(row['value'], value * (100 if row['unit'] == 'percent' else 1), places=10)

    def test_mercor_subsets_and_counts_not_successes(self):
        rows = self.data['results']['results']
        mercor = [r for r in rows if r['benchmark_id'].startswith('browsecomp-mercor')]
        self.assertEqual(len(mercor), 16)
        sol = next(r for r in mercor if r['model_id'] == 'gpt-6.1-sol' and r['task_count'] == 130)
        self.assertEqual(sol['value'], 95.4)
        self.assertEqual(sol['effort'], 'max')
        self.assertEqual(sol['n_samples'], 496)
        self.assertEqual(sol['reported_error_pp'], 3.1)
        self.assertEqual(sol['time_limit_seconds'], 10800)
        self.assertIsNone(sol['confidence_interval'])
        self.assertIsNone(sol['confidence_level'])
        self.assertNotIn('success_count', sol)
        for row in mercor:
            if row['task_count'] == 100:
                for field in ('harness', 'trials_per_task', 'time_limit_seconds', 'judge_model'):
                    self.assertIsNone(row[field])
        self.assertFalse(any(r['model_id'] == 'gemini-4-argon' for r in mercor))

    def test_draco_regrading_and_wandr_soft_f1_kept_distinct(self):
        rows = self.data['results']['results']
        current = next(r for r in rows if r['model_id'] == 'claude-fable-5-1' and
                       r['benchmark_id'] == 'draco-anthropic-opus46-grader-2026-09' and r['effort'] == 'high')
        earlier = next(r for r in rows if r['model_id'] == 'claude-fable-5-1' and
                       r['benchmark_id'] == 'draco-anthropic-fable-card-2026-09-01' and r['effort'] == 'high')
        self.assertEqual(current['value'], 86.5)
        self.assertEqual(earlier['value'], 85.4)
        self.assertNotEqual(current['comparison_group'], earlier['comparison_group'])
        wandr = next(r for r in rows if r['model_id'] == 'claude-opus-5-5' and
                     r['benchmark_id'] == 'wandr-anthropic-frozen-opus46-grader-2026-09' and r['effort'] == 'high')
        self.assertEqual(wandr['value'], 67.3)
        self.assertIn('FROZEN', wandr['harness'])
        self.assertIn('soft F1', wandr['statistic'])
        self.assertIsNone(wandr['time_limit_seconds'])

    def test_epoch_simpleqa_no_tools_is_factuality_proxy(self):
        rows = [r for r in self.data['results']['results'] if r['benchmark_id'] == 'simpleqa-verified-epoch-anti-abstention']
        self.assertEqual(len(rows), 8)
        astra = next(r for r in rows if r['model_id'] == 'gpt-6-astra')
        self.assertEqual(astra['value'], 75.6)
        self.assertEqual(astra['effort'], 'max')
        self.assertEqual(astra['date_type'], 'evaluation')
        self.assertGreaterEqual(astra['date'], '2026-08-27')
        self.assertGreater(astra['standard_error'], 0)
        self.assertIsNone(astra['confidence_interval'])
        self.assertIn('no search', astra['statistic'])

    def test_openai_any_effort_not_labeled_max(self):
        rows = [r for r in self.data['results']['results'] if r['id'].startswith('openai-astra-')]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertIsNone(row['effort'])
            self.assertIn('maximum at any effort', row['setting'])
        browse = next(r for r in rows if r['benchmark_id'].startswith('browsecomp-openai'))
        self.assertEqual(browse['value'], 91.5)

    def test_web_research_sources_explain_product_mapping_gaps(self):
        index = {b['id']: b for b in self.data['benchmarks']['benchmarks']}
        for bid in ('browsecomp-plus-fixed-corpus', 'deepresearch-bench-i-race', 'deepconsult',
                    'gaia-test', 'online-mind2web', 'frames', 'factscore-atomic-precision'):
            self.assertIn('web_research', index[bid]['task_traits'])
            self.assertFalse(index[bid]['active'])
            self.assertFalse(any(r['benchmark_id'] == bid for r in self.data['results']['results']))
        self.assertEqual(index['aa-omniscience-accuracy']['web_research_role'], 'closed_book_factuality_proxy')
        self.assertNotIn('web_research', index['hle-aa-no-tools']['task_traits'])
        self.assertIn('web_research', index['hle-anthropic-tools']['task_traits'])

    def test_effort_support_audit_and_metric_gaps(self):
        rows = self.data['efforts']['rows']
        self.assertEqual(len(rows), 54)
        self.assertEqual(sum(r['supported'] is True for r in rows), 40)
        self.assertEqual(sum(r['observed'] for r in rows), 40)
        self.assertEqual(sum(r['derived_decode_seconds_per_task'] is not None for r in rows), 38)
        grok = next(r for r in rows if (r['model_id'], r['effort']) == ('grok-4.7', 'medium'))
        self.assertTrue(grok['supported'])
        self.assertFalse(grok['observed'])
        self.assertIsNone(grok['aa_intelligence_v4_3_2'])
        argon = next(r for r in rows if (r['model_id'], r['effort']) == ('gemini-4-argon', 'high'))
        self.assertIsNone(argon['supported'])
        self.assertTrue(argon['observed'])
        self.assertIsNone(argon['derived_decode_seconds_per_task'])
        for row in rows:
            for field in ('input_tokens_per_task', 'subscription_quota_per_task',
                          'lead_wall_seconds', 'failure_rework_probability'):
                self.assertIsNone(row[field])

    def test_effort_numerical_provenance_and_decode_definition(self):
        extracts = json.loads((ROOT / 'data/benchmarks/source-extracts.json').read_text())['efforts']
        index = {(r['model_id'], r['effort']): r for r in extracts}
        for row in self.data['efforts']['rows']:
            if not row['observed']:
                continue
            source = index[row['model_id'], row['effort']]
            self.assertEqual(row['source_snapshot_sha256'], source['sha256'])
            self.assertEqual(row['aa_intelligence_v4_3_2'], source['fields']['intelligenceIndex'])
            self.assertEqual(row['derived_decode_seconds_per_task'], source['fields']['intelligenceIndexTimePerTask'])
            for name in ('output', 'reasoning', 'answer'):
                self.assertEqual(row[name + '_tokens_per_task'], source['fields']['intelligenceIndexOutputTokensPerTask'][name])
            self.assertIn('excludes', row['time_method'])
            self.assertIn('not elapsed task time', row['time_method'])

    def test_effort_validation_rejects_fabricated_or_calibrated_metrics(self):
        data = copy.deepcopy(self.data['efforts'])
        models = {m['id']: m for m in self.data['models']['models']}
        row = next(r for r in data['rows'] if not r['observed'])
        row['output_tokens_per_task'] = 0
        self.assertIn('copied metrics', '\n'.join(benchmarks.validate_efforts(data, models)))
        row['output_tokens_per_task'] = None
        row['subscription_quota_per_task'] = 1
        self.assertIn('must remain null', '\n'.join(benchmarks.validate_efforts(data, models)))
        row['subscription_quota_per_task'] = None
        row['input_tokens_per_task'] = 100
        self.assertIn('denominator unverified', '\n'.join(benchmarks.validate_efforts(data, models)))

    def test_effort_curves_do_not_smooth_nonmonotonic_or_bridge_gaps(self):
        models = {m['id']: m for m in self.data['models']['models']}
        rows = copy.deepcopy([r for r in self.data['efforts']['rows'] if r['model_id'] == 'grok-4.7'])
        for row in rows:
            row['aa_intelligence_v4_3_2'] = {'low': 45, 'high': 40, 'xhigh': 46}.get(row['effort'])
        svg = benchmarks.effort_curve(models['grok-4.7'], rows)
        ET.fromstring(svg)
        self.assertIn('45.0', svg)
        self.assertIn('40.0', svg)
        self.assertIn('unmeasured', svg)
        self.assertNotIn('M320 ', svg.split('Weighted output tokens')[0])
        self.assertIn('fixed 0–100', svg)

    def test_effort_artifacts_written_and_plan_not_implemented(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = benchmarks.render(self.data, temporary, '2026-10-10')
            out = Path(temporary)
            self.assertEqual(report['charts'], len(self.data['benchmarks']['benchmarks']) + 10)
            self.assertTrue((out / 'effort-curves.md').exists())
            csv_text = (out / 'effort-curves.csv').read_text()
            self.assertIn('quota_calibration_status', csv_text)
            self.assertIn('grok-4.7,medium', csv_text)
            self.assertFalse((out / 'router.py').exists())


if __name__ == '__main__':
    unittest.main()
