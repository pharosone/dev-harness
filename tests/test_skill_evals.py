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
cases=[p.parent.name for p in (plugin/'evals').rglob('prompt.md')]
with_score=float(os.environ.get('WITH','1'))
without_score=float(os.environ.get('WITHOUT','0.25'))
out=Path(args[args.index('--json')+1])
value={'partial':os.environ.get('PARTIAL')=='1','costUsd':float(os.environ.get('COST','0.1')),
 'suite':{'modelOverride':args[args.index('--model')+1]},
 'cases':[{'name':n,'arms':{'with':[{'score':with_score,'error':None}],
                          'without':[{'score':without_score,'error':None}]}} for n in cases]}
if os.environ.get('MISSING_ARM'): value['cases'][0]['arms'].pop('without')
out.write_text(json.dumps(value))
sys.exit(int(os.environ.get('EXIT','0')))
''')
        patch.dict(os.environ, {'TRACE': str(self.root/'trace.json')}).start()

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

    def settings(self):
        settings = ev.config(self.root)
        return settings, ev.inventory(self.root, settings)

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

    def test_paired_run_and_gate_pass_with_fresh_hashes(self):
        self.change()
        code, output = self.run_eval()
        self.assertEqual(code, 0, output)
        result = ev.read(self.root/'run/evals/example.json')
        self.assertEqual(result['cases'][0]['delta'], .75)
        self.assertEqual(result['model'], 'fake-model')
        self.assertEqual(result['cost_usd'], .1)
        self.assertEqual(result['content_hash'], ev.digest(self.root/self.skill))
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
        self.assertEqual(self.run_eval()[0], 0)
        self.git('add', 'plugin/evals/history')
        self.git('commit', '-qm', 'prior measured result')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.change()
        self.assertEqual(self.run_eval(WITH='0.95')[0], 0)
        self.assertEqual(self.cli('check', '--base', self.base)[0], 0)
        self.assertEqual(self.run_eval(WITH='0.90')[0], 1)  # baseline at REF remains 1.00
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)  # baseline at REF remains 1.00
        self.assertIn('regressed', output)

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
        self.assertEqual(self.run_eval()[0], 0)
        self.change()
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)
        self.git('add', 'plugin/evals/history')
        self.git('commit', '-qm', 'history with failed attempt')
        self.base = self.git('rev-parse', 'HEAD').strip()
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)
        code, output = self.cli('check', '--base', self.base)
        self.assertEqual(code, 1, output)
        self.assertIn('regressed', output)

    def test_failed_attempt_cannot_ratchet_regression_baseline(self):
        self.assertEqual(self.run_eval()[0], 0)
        self.change()
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)
        self.assertEqual(self.run_eval(WITH='0.8')[0], 1)

    def test_custom_tolerance(self):
        self.put('harness/project/project.json', json.dumps({'skills': {'tolerance': .1}}))
        self.assertEqual(ev.config(self.root)['tolerance'], .1)
        self.assertFalse(ev.acceptance({'cases':[{'name':'case','score_with':.9,'delta':.5}]},
                                      {'model':'prior','cost_usd':0,'cases':[{'name':'case','score_with':1,'score_without':.4}]}, {'case'}, .1))

    def test_new_untracked_skill_with_case_passes(self):
        new = 'skills/new/SKILL.md'
        self.put(new, 'new')
        self.put('plugin/evals/new-case/prompt.md', 'scenario')
        self.put('plugin/evals/new-case/graders/score.md', 'grader')
        self.put('plugin/evals/cases.json', json.dumps({self.skill:['case'], new:['new-case']}))
        self.assertEqual(self.cli('run','--skill','new')[0], 0)
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
