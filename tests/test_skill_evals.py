"""The gate is offline; fake CLI runners exercise paired evidence and failure paths."""
import contextlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import skill_evals as ev


class SkillEvalsTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.git('init', '-q')
        self.git('config', 'user.name', 'test')
        self.git('config', 'user.email', 'test@example.invalid')
        self.skill = 'skills/example/SKILL.md'
        self.put(self.skill, 'original instructions')
        self.directory = self.root / 'plugin/evals'
        self.put('plugin/evals/case/prompt.md', '---\nname: case\n---\nscenario')
        self.put('plugin/evals/case/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill: ['case']}))
        self.git('add', 'skills', 'plugin')
        self.git('commit', '-qm', 'base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(ev.project, 'CORE', self.root).start()
        patch.dict(os.environ, {'PATH': str(self.bin) + os.pathsep + os.environ['PATH']}).start()
        os.environ.pop('HARNESS_PROJECT', None)
        self.fake('claude', '''
import json,sys,os
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['TRACE']).write_text(json.dumps(args))
plugin=Path(args[2])
assert len(list((plugin/'skills').iterdir())) == 1
if os.environ.get('CHECK_BUNDLE'):
 assert (plugin/'harness/core/README.md').read_text() == 'Authoritative profiles.\\n'
 assert (plugin/'harness/core/shared/rules.md').read_text() == 'Referenced rules.\\n'
 assert (plugin/'skills/example/../../README.md').read_text() == 'Authoritative profiles.\\n'
 assert (plugin/'skills/example/../../shared/rules.md').read_text() == 'Referenced rules.\\n'
cases=[p.parent.name for p in (plugin/'evals').rglob('prompt.md')]
with_score=float(os.environ.get('WITH','1'))
without_score=float(os.environ.get('WITHOUT','0.25'))
out=Path(args[args.index('--json')+1])
value={'partial':os.environ.get('PARTIAL')=='1','costUsd':float(os.environ.get('COST','0.1')),
 'suite':{'modelOverride':args[args.index('--model')+1]},
 'cases':[{'name':n,'arms':{'with':[{'score':with_score,'error':None}],
                          'without':[{'score':without_score,'error':None}]}} for n in cases]}
if os.environ.get('MISSING_ARM'): value['cases'][0]['arms'].pop('without')
if os.environ.get('MUTATE'): Path(os.environ['MUTATE']).write_text('changed during evaluation')
out.write_text(json.dumps(value))
sys.exit(int(os.environ.get('EXIT','0')))
''')
        patch.dict(os.environ, {'TRACE': str(self.root/'trace.json'), 'HARNESS_LOAD_GUARD': '0'}).start()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, check=True,
                              capture_output=True, text=True).stdout

    def put(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
        return path

    def fake(self, name, code):
        path = self.put('bin/' + name, '#!' + sys.executable + '\n' + code)
        path.chmod(0o755)

    def cli(self, *args):
        if args[0] == 'run' and '--base' not in args:
            args = (*args, '--base', self.base)
        output = io.StringIO()
        with patch.object(ev.project, 'repo_root', return_value=self.root), \
                contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            result = ev.main(list(args))
        return result, output.getvalue()

    def change(self):
        self.put(self.skill, 'changed instructions')
        self.put('plugin/evals/case/prompt.md', 'changed scenario')

    def run_eval(self, **env):
        with patch.dict(os.environ, env):
            return self.cli('run', '--skill', 'example', '--model', 'fake-model',
                            '--max-cost-usd', '1', '--run-dir', str(self.root/'run'))

    def accept_runs(self, count=ev.BASELINE_RUNS):
        """The accepted results a case needs before a regression is checked."""
        for _ in range(count):
            self.assertEqual(self.run_eval()[0], 0)

    def settings(self):
        settings = ev.config(self.root)
        return settings, ev.inventory(self.root, settings)

    def test_native_eval_packages_whole_declared_reference_bundle(self):
        self.put(self.skill, 'Read `../../README.md`.\n')
        self.put('README.md', 'Authoritative profiles.\n')
        self.put('shared/rules.md', 'Referenced rules.\n')
        self.put('skills/example/eval-inputs.json', '["../../shared/rules.md"]')
        self.put('plugin/evals/case/prompt.md', 'changed scenario')
        code, output = self.run_eval(CHECK_BUNDLE='1')
        self.assertEqual(code, 0, output)

    def test_only_execution_asks_for_capacity_and_passes_guard_environment(self):
        self.change()
        original = ev.subprocess.run
        environments = []
        def execute(cmd, **kwargs):
            if cmd[0] == 'claude':
                environments.append(kwargs['env'])
            return original(cmd, **kwargs)
        def admission(root, **kwargs):
            self.assertEqual(root, self.root)
            self.assertEqual(kwargs['label'], 'skill-evals')
            self.assertEqual(Path(kwargs['run']), self.root / 'run')
            return {**kwargs['env'], 'GUARD_TEST': 'admitted'}
        with patch.object(ev.load_guard, 'wait_for_capacity', side_effect=admission) as capacity, \
             patch.object(ev.subprocess, 'run', side_effect=execute):
            code, output = self.run_eval()
            self.assertEqual(code, 0, output)
            capacity.assert_called_once()
            self.assertEqual(environments[0]['GUARD_TEST'], 'admitted')
        with patch.object(ev.load_guard, 'wait_for_capacity', side_effect=AssertionError('pure operation')):
            self.assertEqual(self.cli('check', '--base', self.base)[0], 0)
            self.assertEqual(self.cli('report')[0], 0)

    def test_codex_runner_keeps_manifest_in_guard_environment(self):
        self.change()
        self.put('harness/project/project.json', json.dumps({'skills': {'eval_command': 'paired'}}))
        self.fake('paired', '''
import json, os, sys
from pathlib import Path
assert os.environ['GUARD_TEST'] == 'admitted'
assert json.loads(Path(os.environ['HARNESS_SKILL_EVAL_MANIFEST']).read_text())['cases']
args = sys.argv[1:]
Path(args[args.index('--out') + 1]).write_text(json.dumps({'model': 'fake', 'cost_usd': 0,
 'cases': [{'name': 'case', 'score_with': 1, 'score_without': 0}]}))
''')
        with patch.object(ev.load_guard, 'wait_for_capacity',
                          side_effect=lambda root, **kw: {**kw['env'], 'GUARD_TEST': 'admitted'}):
            code, output = self.cli('run', '--skill', 'example', '--runtime', 'codex')
        self.assertEqual(code, 0, output)

    def test_external_metadata_bump_does_not_require_new_evidence(self):
        self.put(self.skill, "Read `../../README.md`.\n")
        self.put("README.md", "Version: 1.5.0\nDate: 2026-10-10\nRules stay fixed.\n")
        self.git('add', 'README.md', self.skill)
        self.git('commit', '-qm', 'reference base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.put("README.md", "Version: 1.6.0\nDate: 2026-10-11\nRules stay fixed.\n")
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 0, output)
        self.put("README.md", "Version: 1.6.0\nDate: 2026-10-11\nRules changed.\n")
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('no added/changed eval case', output)

    def test_reference_mode_change_is_not_a_metadata_bump(self):
        self.put(self.skill, "Read `../../README.md`.\n")
        reference = self.put("README.md", "Version: 1.5.0\n")
        self.git('add', 'README.md', self.skill)
        self.git('commit', '-qm', 'reference mode base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        reference.chmod(0o755)
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('no added/changed eval case', output)

    def test_changelog_headings_only_are_noninvalidating(self):
        self.put(self.skill, "Read `../../CHANGELOG.md`.\n")
        self.put("CHANGELOG.md", "## 1.5.0 — Unreleased\n- Fix X\n")
        self.git('add', 'CHANGELOG.md', self.skill)
        self.git('commit', '-qm', 'changelog base')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.put("CHANGELOG.md", "## 1.6.0 — 2026-10-11\n- Fix X\n")
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 0, output)
        self.put("CHANGELOG.md", "## 1.6.0 — 2026-10-11\n- Fix Y\n")
        self.assertEqual(self.cli('check', '--base', self.base)[0], 1)

    def test_invalid_base_is_a_clear_usage_error(self):
        for base in ('missing-ref', '--no-index'):
            code, output = self.cli('check', '--base=' + base)
            self.assertEqual(code, 2)
            self.assertIn('invalid base ref', output)
            self.assertNotIn('Traceback', output)

    def test_changed_skill_without_case_fails(self):
        self.put(self.skill, 'change')
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1)
        self.assertIn('no added/changed eval case', output)
        self.assertIn('no fresh result', output)

    def test_changed_skill_and_case_without_result_fails(self):
        self.change()
        self.assertEqual(self.cli('check', '--base', self.base)[0], 1)

    def test_directory_reference_only_change_trips_gate_and_stales_hash(self):
        reference = self.put('skills/example/references/qa-gotchas.md', 'old boundary checks')
        self.assertEqual(self.run_eval()[0], 0)
        original = ev.read(self.root/'run/evals/example.json')['content_hash']
        self.git('add', str(reference.relative_to(self.root)), 'plugin/evals/history')
        self.git('commit', '-qm', 'accepted skill bundle')
        self.base = self.git('rev-parse', 'HEAD').strip()
        reference.write_text('new boundary checks')
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('no added/changed eval case', output)
        self.assertIn('no fresh result', output)
        self.assertIn('STALE', self.cli('report')[1])
        self.put('plugin/evals/case/prompt.md', 'updated boundary scenario')
        self.assertEqual(self.run_eval()[0], 0)
        self.assertNotEqual(ev.read(self.root/'run/evals/example.json')['content_hash'], original)
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)

    def test_declared_external_and_transitive_references_are_inputs(self):
        self.put(self.skill, 'Read [shared rules](../../shared/rules.md).')
        reference = self.put('shared/rules.md', 'Read `nested/checks.txt`.')
        nested = self.put('shared/nested/checks.txt', 'old checks')
        self.assertEqual(self.run_eval()[0], 0)
        self.git('add', self.skill, 'shared', 'plugin/evals/history')
        self.git('commit', '-qm', 'accepted external dependencies')
        self.base = self.git('rev-parse', 'HEAD').strip()
        for path in (reference, nested):
            path.write_text(path.read_text() + '\nchanged')
            code, output = self.cli('check', '--base', self.base)
            self.assertEqual(code, 1, output)
            self.assertIn('no fresh result', output)
            self.assertIn('STALE', self.cli('report')[1])
            self.assertEqual(self.run_eval()[0], 0)

    def test_directory_input_addition_and_deletion_invalidate_evidence(self):
        self.assertEqual(self.run_eval()[0], 0)
        added = self.put('skills/example/scripts/helper.py', 'print("new behaviour")')
        self.assertEqual(self.cli('check', '--base', self.base)[0], 1)
        self.assertIn('STALE', self.cli('report')[1])
        self.assertEqual(self.run_eval()[0], 0)
        added.unlink()
        self.assertIn('STALE', self.cli('report')[1])

    def test_deleted_external_reference_still_triggers_gate(self):
        self.put(self.skill, 'Read [shared](../../shared/rules.md).')
        reference = self.put('shared/rules.md', 'checks')
        self.assertEqual(self.run_eval()[0], 0)
        self.git('add', self.skill, 'shared', 'plugin/evals/history')
        self.git('commit', '-qm', 'accepted reference')
        self.base = self.git('rev-parse', 'HEAD').strip()
        reference.unlink()
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('no fresh result', output)
        self.assertIn('STALE', self.cli('report')[1])

    def test_explicit_dynamic_glob_and_binary_inputs_are_hashed(self):
        self.put('skills/example/eval-inputs.json', '["../../shared/*.toml"]')
        reference = self.put('shared/settings.toml', 'tone = "formal"')
        self.assertEqual(self.run_eval()[0], 0)
        reference.write_text('tone = "friendly"')
        self.assertIn('STALE', self.cli('report')[1])
        self.assertEqual(self.run_eval()[0], 0)
        asset = self.root/'skills/example/logo.bin'
        asset.write_bytes(b'\x00\xff')
        self.assertIn('STALE', self.cli('report')[1])

    def test_reference_mutation_during_run_cannot_write_fresh_evidence(self):
        reference = self.put('skills/example/references/check.md', 'original')
        code, output = self.run_eval(MUTATE=str(reference))
        self.assertEqual(code, 2, output)
        self.assertIn('changed during the run', output)
        settings, skills = self.settings()
        self.assertFalse(ev.histories(self.skill, skills[self.skill]))

    def test_symlink_targets_and_directory_links_are_inputs(self):
        first = self.put('shared/first/check.md', 'same content')
        self.put('shared/second/check.md', 'same content')
        link = self.root/'skills/example/reference'
        link.symlink_to(first.parent, target_is_directory=True)
        self.assertEqual(self.run_eval()[0], 0)
        link.unlink()
        link.symlink_to(self.root/'shared/second', target_is_directory=True)
        self.assertIn('STALE', self.cli('report')[1])
        self.assertEqual(self.run_eval()[0], 0)
        (self.root/'shared/second/check.md').write_text('changed')
        self.assertIn('STALE', self.cli('report')[1])

    def test_reference_cycle_and_examples_do_not_expand_outside_checkout(self):
        self.put(self.skill, 'Read [rules](../../shared/rules.md).')
        self.put('shared/rules.md', 'Read [skill](../skills/example/SKILL.md).\n'
                 'Example JSON: `["../../elsewhere/*.md"]`.\n'
                 '```json\n["../../../outside.txt"]\n```')
        self.assertEqual(self.run_eval()[0], 0)
        self.assertIn('fresh', self.cli('report')[1])

    def test_unsafe_directory_symlink_is_rejected_without_reading_target(self):
        (self.root/'skills/example/outside').symlink_to(self.root.parent, target_is_directory=True)
        code, output = self.run_eval()
        self.assertEqual(code, 2, output)
        self.assertIn('symlink outside checkout', output)

    def test_reference_case_aliases_hash_and_watch_actual_file_spelling(self):
        self.put(self.skill, 'Read [rules](../../Shared/Rules.md).')
        reference = self.put('shared/rules.md', 'original rules')
        self.assertEqual(self.run_eval()[0], 0)
        result = ev.read(self.root/'run/evals/example.json')
        self.assertIn('core:shared/rules.md', result['input_hashes'])
        self.assertNotIn('core:Shared/Rules.md', result['input_hashes'])
        reference.write_text('new rules')
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('no fresh result', output)

    def test_paired_run_and_gate_pass_with_fresh_hashes(self):
        self.change()
        code, output = self.run_eval()
        self.assertEqual(code, 0, output)
        result = ev.read(self.root/'run/evals/example.json')
        self.assertEqual(result['cases'][0]['delta'], .75)
        self.assertEqual(result['model'], 'fake-model')
        self.assertEqual(result['cost_usd'], .1)
        settings, skills = self.settings()
        self.assertEqual(result['content_hash'], ev.content_hash(skills[self.skill]))
        self.assertIn('core:skills/example/SKILL.md', result['input_hashes'])
        self.assertTrue(result['run_date'])
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)
        args = ev.read(self.root/'trace.json')
        self.assertIn('with-without', args)
        self.assertIn('--no-publish', args)
        self.assertIn('--max-cost-usd', args)

    def test_skill_and_case_edits_invalidate_results(self):
        self.change()
        self.assertEqual(self.run_eval()[0], 0)
        self.put(self.skill, 'newer')
        self.assertEqual(self.cli('check', '--base', self.base)[0], 1)
        self.assertIn('STALE', self.cli('report')[1])
        self.run_eval()
        self.put('plugin/evals/case/graders/score.md', 'changed grader')
        self.assertEqual(self.cli('check', '--base', self.base)[0], 1)

    def test_zero_and_negative_delta_fail(self):
        self.change()
        for without in ('1', '0.9'):
            with self.subTest(without=without):
                code, output = self.run_eval(WITH='0.8' if without=='0.9' else '1', WITHOUT=without)
                self.assertEqual(code, 1, output)
                self.assertIn('Δ must be > 0', output)
                self.assertEqual(self.cli('check', '--base', self.base)[0], 1)

    def test_tolerance_and_regression_at_git_base(self):
        self.accept_runs()
        self.git('add', 'plugin/evals/history')
        self.git('commit', '-qm', 'prior measured result')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.put(self.skill, 'changed instructions')
        self.put('plugin/evals/extra/prompt.md', 'additional acceptance scenario')
        self.put('plugin/evals/extra/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case','extra']}))
        self.assertEqual(self.run_eval(WITH='0.95')[0], 0)
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)
        self.assertEqual(self.run_eval(WITH='0.90')[0], 1)  # baseline at REF remains 1.00
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)  # baseline at REF remains 1.00
        self.assertIn('regressed', output)

    def test_one_lucky_accepted_run_does_not_raise_the_regression_baseline(self):
        for score in ('0.6', '0.6', '1'):
            self.assertEqual(self.run_eval(WITH=score)[0], 0)
        self.git('add', 'plugin/evals/history')
        self.git('commit', '-qm', 'accepted runs: 0.6, 0.6, then a lucky 1.0')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.put(self.skill, 'changed instructions')
        self.put('plugin/evals/extra/prompt.md', 'additional acceptance scenario')
        self.put('plugin/evals/extra/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case','extra']}))
        self.assertEqual(self.run_eval(WITH='0.6')[0], 0)  # median 0.6, not the latest 1.0
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)
        code, output = self.run_eval(WITH='0.5')
        self.assertEqual(code, 1, output)
        self.assertIn('median accepted 0.600', output)
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('regressed', output)

    def test_a_case_with_too_few_accepted_runs_has_no_regression_baseline_yet(self):
        self.accept_runs(ev.BASELINE_RUNS - 1)
        self.git('add', 'plugin/evals/history')
        self.git('commit', '-qm', 'two accepted runs at 1.0')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.put(self.skill, 'changed instructions')
        self.put('plugin/evals/extra/prompt.md', 'additional acceptance scenario')
        self.put('plugin/evals/extra/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case','extra']}))
        self.assertEqual(self.run_eval(WITH='0.6')[0], 0)
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)

    def test_baseline_is_the_median_of_the_same_case_content(self):
        def result(score, digest):
            return {'model': 'm', 'cost_usd': 0, 'case_hashes': {'case': digest} if digest else None,
                    'cases': [{'name': 'case', 'score_with': score, 'score_without': 0}]}
        accepted = [result(.2, 'old'), result(.6, 'h'), result(1, 'h'), result(.6, 'h'),
                    result(.8, 'h')]
        self.assertEqual(ev.baseline(accepted, {'case': 'h'}), {'case': .7})
        self.assertEqual(ev.baseline(accepted, {'case': 'new'}), {})  # other content
        self.assertEqual(ev.baseline([result(.4, None), result(.3, None), result(.9, 'old')],
                                     {'case': 'h'}), {})  # no comparable content
        self.assertEqual(ev.baseline(accepted[1:3], {'case': 'h'}), {})  # one run's luck
        self.assertEqual(ev.baseline([], {'case': 'h'}), {})
        with self.assertRaises(ev.EvalError):
            ev.baseline([{'cases': 'bad'}], {'case': 'h'})

    def test_case_only_changes_need_new_evidence(self):
        self.assertEqual(self.run_eval()[0], 0)
        self.put('plugin/evals/case/graders/score.md', 'updated grader')
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('no fresh result', output)

    def test_new_case_makes_old_suite_stale_until_rerun(self):
        self.assertEqual(self.run_eval()[0], 0)
        self.put('plugin/evals/extra/prompt.md', 'scenario')
        self.put('plugin/evals/extra/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case', 'extra']}))
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('no fresh result', output)
        self.assertIn('STALE', self.cli('report')[1])
        self.assertEqual(self.run_eval()[0], 0)
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)

    def test_committed_failed_attempt_does_not_ratchet_baseline(self):
        self.accept_runs()
        self.put(self.skill, 'changed instructions')
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)
        self.git('add', 'plugin/evals/history')
        self.git('commit', '-qm', 'history with failed attempt')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('regressed', output)

    def test_failed_attempt_cannot_ratchet_regression_baseline(self):
        self.accept_runs()
        self.put(self.skill, 'changed instructions')
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)

    def test_stricter_changed_case_is_not_a_regression_of_old_case(self):
        self.assertEqual(self.run_eval()[0], 0)
        self.git('add', 'plugin/evals/history')
        self.git('commit', '-qm', 'accepted original case')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.change()
        self.assertEqual(self.run_eval(WITH='0.9', WITHOUT='0.3')[0], 0)
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)

    def test_committed_new_case_with_zero_delta_is_not_accepted(self):
        self.change()
        self.git('add', self.skill, 'plugin/evals/case')
        self.git('commit', '-qm', 'committed case without evidence')
        self.base = self.git('rev-parse', 'HEAD').strip()
        code, output = self.run_eval(WITH='1', WITHOUT='1')
        self.assertEqual(code, 1, output)
        self.assertIn('Δ must be > 0', output)
        settings, skills = self.settings()
        self.assertTrue(ev.latest(self.skill, skills[self.skill])['acceptance_findings'])

    def test_run_requires_explicit_unit_base(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            ev.main(['run', '--skill', 'example'])
        self.assertEqual(exc.exception.code, 2)

    def test_custom_tolerance(self):
        self.put('harness/project/project.json', json.dumps({'skills': {'tolerance': .1}}))
        self.assertEqual(ev.config(self.root)['tolerance'], .1)
        self.assertFalse(ev.acceptance({'cases':[{'name':'case','score_with':.9,'delta':.5}]},
                                      {'case': 1}, {'case'}, .1))

    def test_new_untracked_skill_with_case_passes(self):
        new = 'skills/new/SKILL.md'
        self.put(new, 'new')
        self.put('plugin/evals/new-case/prompt.md', 'scenario')
        self.put('plugin/evals/new-case/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case'], new:['new-case']}))
        self.assertEqual(self.cli('run','--skill','new')[0], 0)
        self.assertEqual(self.cli('check','--base',self.base)[0], 0)

    def test_new_case_repeated_in_same_unit_needs_delta_not_prior_attempt_score(self):
        new = 'skills/new/SKILL.md'
        self.put(new, 'new skill')
        self.put('plugin/evals/new-case/prompt.md', 'new scenario')
        self.put('plugin/evals/new-case/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case'], new:['new-case']}))
        self.assertEqual(self.cli('run','--skill','new')[0], 0)
        with patch.dict(os.environ, {'WITH':'0.8', 'WITHOUT':'0.3'}):
            code, output = self.cli('run','--skill','new')
        self.assertEqual(code, 0, output)
        self.assertEqual(self.cli('check','--base',self.base)[0], 0)

    def test_committed_and_staged_changes_are_checked(self):
        self.change()
        self.git('add', self.skill)
        self.git('commit', '-qm', 'instructions')
        self.git('add', 'plugin/evals/case/prompt.md')
        self.assertEqual(self.cli('check','--base',self.base)[0], 1)
        self.assertEqual(self.run_eval()[0], 0)
        self.assertEqual(self.cli('check','--base',self.base)[0], 0)

    def test_deleted_skill_needs_no_eval_when_registry_removed(self):
        (self.root/self.skill).unlink()
        self.put('plugin/evals/cases.json', '{}')
        self.assertEqual(self.cli('check','--base',self.base)[0], 0)

    def test_registry_edit_alone_is_not_case_change(self):
        self.put(self.skill, 'changed')
        self.run_eval()
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case']}, indent=4))
        code, output = self.cli('check','--base',self.base)
        self.assertEqual(code, 1)
        self.assertIn('no added/changed eval case', output)

    def test_no_model_calls_in_check_or_report(self):
        self.change()
        with patch.object(ev.subprocess, 'run', wraps=subprocess.run) as calls:
            self.cli('check','--base',self.base)
            self.cli('report')
        self.assertTrue(all(c.args[0][0]=='git' for c in calls.call_args_list))

    def test_partial_missing_arm_runner_failure_and_budget_reject_evidence(self):
        self.change()
        for env in ({'PARTIAL':'1'}, {'MISSING_ARM':'1'}, {'EXIT':'2'}, {'COST':'2'}):
            with self.subTest(env=env):
                self.assertNotEqual(self.run_eval(**env)[0], 0)
                settings, skills = self.settings()
                self.assertFalse(ev.histories(self.skill, skills[self.skill]))

    def test_claude_threshold_exit_one_still_records_valid_delta(self):
        self.change()
        self.assertEqual(self.run_eval(EXIT='1')[0], 0)

    def product(self):
        skill = 'hermes/agents/skills/voice/SKILL.md'
        self.put(skill, 'voice instructions')
        self.put('evals/skills/tone/case.json', '{}')
        self.put('evals/skills/cases.json', json.dumps({skill:['tone']}))
        self.put('harness/project/project.json', json.dumps({'skills':{
            'dirs':['hermes/**/skills/**'], 'eval_dirs':['evals/skills/'],
            'eval_command':'product-runner --preset voice', 'tolerance':.05}}))
        self.fake('product-runner', '''
import json,sys,os
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['TRACE']).write_text(json.dumps(args))
manifest=json.loads(Path(os.environ['HARNESS_SKILL_EVAL_MANIFEST']).read_text())
assert manifest['skill']==args[args.index('--skill')+1]
assert manifest['cases']==[{'name':'tone','path':str(Path.cwd()/'evals/skills/tone')}]
Path(args[args.index('--out')+1]).write_text(json.dumps({'model':'hermes-actual','cost_usd':.05,
 'cases':[{'name':'tone','score_with':1,'score_without':.4}]}))
''')
        return skill

    def test_codex_only_core_skill_uses_overlay_paired_runner(self):
        self.change()
        self.put('harness/project/project.json', json.dumps({'skills': {'eval_command':'codex-paired'}}))
        self.fake('codex-paired', """
import sys,json,os
from pathlib import Path
args=sys.argv[1:]
Path(os.environ['TRACE']).write_text(json.dumps(args))
Path(args[args.index('--out')+1]).write_text(json.dumps({'model':'codex-actual','cost_usd':.04,
 'cases':[{'name':'case','score_with':1,'score_without':.3}]}))
""")
        code, output = self.cli('run','--skill','example','--runtime','codex','--model','codex-request')
        self.assertEqual(code, 0, output)
        self.assertIn('codex-request', ev.read(self.root/'trace.json'))
        self.assertEqual(self.cli('check','--base',self.base)[0], 0)

    def test_codex_runner_manifest_resolves_vendored_core_case_paths(self):
        core = self.root/'harness/core'
        core.mkdir(parents=True)
        for name in ('skills', 'plugin'):
            shutil.move(str(self.root/name), str(core/name))
        self.put('harness/project/project.json', json.dumps({'skills':{'eval_command':'codex-paired'}}))
        self.fake('codex-paired', '''
import json,sys,os
from pathlib import Path
args=sys.argv[1:]
manifest=json.loads(Path(os.environ['HARNESS_SKILL_EVAL_MANIFEST']).read_text())
assert manifest['skill']=='harness/core/skills/example/SKILL.md'
assert manifest['cases']==[{'name':'case','path':str(Path.cwd()/'harness/core/plugin/evals/case')}]
Path(args[args.index('--out')+1]).write_text(json.dumps({'model':'codex-actual','cost_usd':.04,
 'cases':[{'name':'case','score_with':1,'score_without':.3}]}))
''')
        with patch.object(ev.project, 'CORE', core):
            code, output = self.cli('run','--skill','example','--runtime','codex')
            self.assertEqual(code, 0, output)

    def test_product_runner_and_glob_path(self):
        skill = self.product()
        code, output = self.cli('run','--skill',skill,'--model','hermes-request','--max-cost-usd','1')
        self.assertEqual(code, 0, output)
        settings, skills = self.settings()
        result = ev.latest(skill, skills[skill])
        self.assertEqual(result['cases'][0]['delta'], .6)
        self.assertEqual(result['model'], 'hermes-actual')
        args = ev.read(self.root/'trace.json')
        self.assertIn(skill,args)
        self.assertIn('hermes-request',args)
        self.assertIn('--max-cost-usd',args)
        self.assertEqual(self.cli('check','--base',self.base)[0], 0)

    def test_literal_product_directory_selects_descendant_skills(self):
        skill = self.product()
        path = self.root/'harness/project/project.json'
        settings = ev.read(path)
        settings['skills']['dirs'] = ['hermes']
        path.write_text(json.dumps(settings))
        config, skills = self.settings()
        self.assertIn(skill, skills)

    def test_product_missing_runner_errors(self):
        skill=self.product()
        self.put('harness/project/project.json', json.dumps({'skills':{
            'dirs':['hermes/**'], 'eval_dirs':['evals/skills']}}))
        code, output = self.cli('run','--skill',skill)
        self.assertEqual(code, 2)
        self.assertIn('requires skills.eval_command', output)

    def test_overlay_validation(self):
        for value in ({'unknown':1}, [], {'dirs':'skills/**'}, {'dirs':['../skills']},
                      {'dirs':['/skills/**']}, {'eval_dirs':['evals/**']}, {'eval_command':3},
                      {'eval_command':'"bad'}, {'tolerance':True}, {'tolerance':-1},
                      {'tolerance':2}, {'tolerance':float('nan')}):
            with self.subTest(value=value):
                self.put('harness/project/project.json', json.dumps({'skills':value}))
                self.assertEqual(self.cli('report')[0], 2)

    def test_core_history_survives_vendoring(self):
        self.assertEqual(self.run_eval()[0], 0)
        core = self.root/'harness/core'
        core.mkdir(parents=True)
        for name in ('skills', 'plugin'):
            shutil.move(str(self.root/name), str(core/name))
        with patch.object(ev.project, 'CORE', core):
            settings, skills = self.settings()
            key = 'harness/core/' + self.skill
            result = ev.latest(key, skills[key])
            self.assertIsNotNone(result)
            self.assertTrue(ev.fresh(result, key, skills[key]))

    def test_bad_result_shapes_are_not_fresh(self):
        for result in ({}, {'model':'x','cost_usd':0,'cases':[]},
                       {'model':'x','cost_usd':0,'cases':[{'name':'case','score_with':2,'score_without':0}]},
                       {'model':'x','cost_usd':0,'cases':[{'name':'case','score_with':1,'score_without':0,'delta':'1'}]},
                       {'model':'x','cost_usd':0,'cases':[{'name':'case','score_with':1,'score_without':0,'delta':0}]}):
            with self.subTest(result=result), self.assertRaises(ev.EvalError):
                ev.validate(result, {'case':None})

    def test_registry_invalid_paths_missing_cases_and_ambiguous_names(self):
        for data in ({self.skill:[{}]}, {self.skill:['../escape']}, {self.skill:['missing']},
                     {'skills/no/SKILL.md':['case']}, {self.skill:['case','case']}):
            self.put('plugin/evals/cases.json', json.dumps(data))
            self.assertEqual(self.cli('report')[0],2)
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case']}))
        self.put('skills/other/example/SKILL.md','other')
        self.assertEqual(self.cli('run','--skill','example')[0],2)


if __name__ == '__main__':
    unittest.main()
