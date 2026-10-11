"""Adaptive admission uses actual host state, never a checkout-local slot count."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from contextlib import redirect_stderr
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import load_guard as guard


class LoadGuardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.registry = self.root / 'host-jobs'
        self.env = {'HARNESS_LOAD_GUARD_STATE_DIR': str(self.registry)}
        self.clock = 0.0

    def settings(self, **values):
        overlay = self.root / 'harness/project'
        overlay.mkdir(parents=True, exist_ok=True)
        (overlay / 'project.json').write_text(json.dumps({'load_guard': values}))

    def sleep(self, seconds):
        self.clock += seconds

    def wait(self, samples, **kwargs):
        output = io.StringIO()
        sleep = MagicMock(side_effect=self.sleep)
        with patch.object(guard, 'sample_capacity', side_effect=samples) as sample, \
             patch.object(guard, 'time', SimpleNamespace(time=time.time,
                 monotonic=lambda: self.clock, sleep=sleep)), \
             redirect_stderr(output):
            environment = guard.wait_for_capacity(self.root, env=self.env, **kwargs)
        return environment, sample, sleep, output.getvalue()

    def test_idle_machine_starts_without_sleep_or_job_record(self):
        environment, sample, sleep, output = self.wait([guard.Capacity(2, 8, True)])
        sleep.assert_not_called()
        self.assertEqual(sample.call_count, 1)
        self.assertEqual(output, '')
        self.assertIn('HARNESS_LOAD_GUARD_ANCESTOR', environment)
        self.assertNotIn('HARNESS_LOAD_GUARD_ANCESTOR', self.env)
        self.assertEqual(guard.waiting_jobs(env=self.env), [])

    def test_busy_machine_waits_then_starts_and_clears_record(self):
        self.settings(backoff_seconds=2)
        seen = []
        def sleep(seconds):
            seen.extend(guard.waiting_jobs(env=self.env))
            self.sleep(seconds)
        with patch.object(guard, 'time', SimpleNamespace(time=time.time,
                monotonic=lambda: self.clock, sleep=sleep)):
            # wait() supplies its own sleep, so exercise the public API directly.
            with patch.object(guard, 'sample_capacity', side_effect=[
                    guard.Capacity(20, 8, True), guard.Capacity(3, 8, True)]), \
                 redirect_stderr(io.StringIO()) as output:
                guard.wait_for_capacity(self.root, label='gate', env=self.env)
        self.assertEqual(self.clock, 2)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0]['label'], 'gate')
        self.assertIn('machine busy (load 20 / 8 cores), waiting up to 300 s', output.getvalue())
        self.assertEqual(guard.waiting_jobs(env=self.env), [])

    def test_memory_pressure_waits_even_at_low_load(self):
        _, _, sleep, output = self.wait([guard.Capacity(0, 8, False),
                                        guard.Capacity(0, 8, True)])
        sleep.assert_called_once()
        self.assertIn('memory pressure', output)

    def test_load_equal_to_threshold_is_busy(self):
        _, _, sleep, _ = self.wait([guard.Capacity(12, 8, True), guard.Capacity(0, 8, True)])
        sleep.assert_called_once()

    def test_timeout_is_bounded_and_proceeds_without_starvation(self):
        self.settings(max_wait_seconds=5, backoff_seconds=2)
        _, _, sleep, output = self.wait([guard.Capacity(100, 8, False)] * 4)
        self.assertEqual(self.clock, 5)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2, 2, 1])
        self.assertIn('starting anyway', output)
        self.assertEqual(guard.waiting_jobs(env=self.env), [])

    def test_targeted_and_disabled_runs_do_not_probe_or_create_ancestry(self):
        self.settings(enabled=False)
        for kwargs, env in [({'heavy': False}, self.env), ({}, self.env),
                            ({}, {**self.env, 'HARNESS_LOAD_GUARD': '0'})]:
            with self.subTest(kwargs=kwargs, env=env), \
                 patch.object(guard, 'sample_capacity', side_effect=AssertionError), \
                 patch.object(guard, 'process_info', side_effect=AssertionError):
                self.assertEqual(guard.wait_for_capacity(self.root, env=env, **kwargs), env)

    def test_targeted_bypasses_invalid_guard_config(self):
        self.settings(typo='ignored for targeted operations')
        self.assertEqual(guard.wait_for_capacity(self.root, heavy=False, env=self.env), self.env)

    def test_env_overrides_project_thresholds_and_preserves_runner_env(self):
        self.settings(load_per_core=1, max_wait_seconds=200, backoff_seconds=1,
                      min_available_percent=30)
        self.env.update(HARNESS_LOAD_PER_CORE='3', HARNESS_LOAD_MAX_WAIT_SECONDS='0',
                        HARNESS_LOAD_MIN_AVAILABLE_PERCENT='5', KEEP='runner input')
        environment, _, sleep, _ = self.wait([guard.Capacity(20, 8, True)])
        sleep.assert_not_called()
        self.assertEqual(environment['KEEP'], 'runner input')
        settings = guard.config(self.root, self.env)
        self.assertEqual(settings['load_per_core'], 3)
        self.assertEqual(settings['min_available_percent'], 5)

    def test_invalid_configuration_rejected_without_sleep(self):
        for value in [{'enabled': 'no'}, {'load_per_core': True}, {'load_per_core': 0},
                      {'max_wait_seconds': -1}, {'backoff_seconds': 0},
                      {'min_available_percent': 101}, {'typo': 1}]:
            with self.subTest(value=value):
                self.settings(**value)
                with self.assertRaises(ValueError):
                    guard.wait_for_capacity(self.root, env=self.env)
        self.settings()
        for key, value in [('HARNESS_LOAD_GUARD', 'typo'), ('HARNESS_LOAD_PER_CORE', 'nan'),
                           ('HARNESS_LOAD_MAX_WAIT_SECONDS', 'inf')]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                guard.config(self.root, {**self.env, key: value})

    def test_sensor_failure_fails_open_but_known_memory_pressure_still_waits(self):
        _, _, sleep, _ = self.wait([guard.Capacity(None, 8, None)])
        sleep.assert_not_called()
        _, _, sleep, _ = self.wait([guard.Capacity(None, 8, False), guard.Capacity(None, 8, True)])
        sleep.assert_called_once()

    def test_actual_child_tree_skips_guard_across_worktrees(self):
        environment, _, _, _ = self.wait([guard.Capacity(0, 8, True)])
        other = self.root / 'another-worktree'
        other.mkdir()
        code = '''
import sys
from pathlib import Path
from unittest.mock import patch
import load_guard
with patch.object(load_guard, 'sample_capacity', side_effect=AssertionError('nested probe')):
    result = load_guard.wait_for_capacity(Path(sys.argv[1]), env=__import__('os').environ)
assert result['HARNESS_LOAD_GUARD_ANCESTOR']
'''
        result = subprocess.run([sys.executable, '-c', code, str(other)], env={
            **os.environ, **environment, 'PYTHONPATH': str(Path(guard.__file__).parent)},
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unrelated_or_reused_pid_marker_cannot_bypass(self):
        for marker in [{'pid': os.getpid(), 'start': 'wrong-start'},
                       {'pid': 999999999, 'start': 'dead'}, {'pid': -1, 'start': 'invalid'}]:
            self.env['HARNESS_LOAD_GUARD_ANCESTOR'] = json.dumps(marker)
            _, sample, _, _ = self.wait([guard.Capacity(0, 8, True)])
            self.assertEqual(sample.call_count, 1)

    def test_sibling_process_is_not_a_nested_job(self):
        sibling = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'])
        self.addCleanup(sibling.wait)
        self.addCleanup(sibling.terminate)
        info = guard.process_info(sibling.pid)
        self.env['HARNESS_LOAD_GUARD_ANCESTOR'] = json.dumps({'pid': sibling.pid, 'start': info[1]})
        _, sample, _, _ = self.wait([guard.Capacity(0, 8, True)])
        self.assertEqual(sample.call_count, 1)

    def test_wait_record_is_host_wide_private_and_never_stores_arbitrary_labels(self):
        captured = []
        secret = 'credential=do-not-record'
        def sleep(seconds):
            jobs = guard.waiting_jobs(env=self.env)
            captured.extend(jobs)
            self.assertEqual(len(jobs), 1)
            self.assertNotIn(secret, json.dumps(jobs))
            self.assertEqual(self.registry.stat().st_mode & 0o777, 0o700)
            self.assertEqual(next(self.registry.glob('*.json')).stat().st_mode & 0o777, 0o600)
            self.sleep(seconds)
        with patch.object(guard, 'sample_capacity', side_effect=[guard.Capacity(90, 8, True),
                                                               guard.Capacity(0, 8, True)]), \
             patch.object(guard, 'time', SimpleNamespace(time=time.time,
                 monotonic=time.monotonic, sleep=sleep)), redirect_stderr(io.StringIO()):
            guard.wait_for_capacity(self.root, label=secret, run=self.root / 'run', env=self.env)
        self.assertEqual(captured[0]['label'], 'heavy job')
        self.assertEqual(guard.waiting_jobs(env=self.env), [])

    def test_interrupted_wait_removes_record(self):
        with patch.object(guard, 'sample_capacity', return_value=guard.Capacity(90, 8, True)), \
             patch.object(guard, 'time', SimpleNamespace(time=time.time,
                 monotonic=time.monotonic, sleep=MagicMock(side_effect=KeyboardInterrupt))), \
             redirect_stderr(io.StringIO()), self.assertRaises(KeyboardInterrupt):
            guard.wait_for_capacity(self.root, env=self.env)
        self.assertEqual(guard.waiting_jobs(env=self.env), [])

    def test_stale_dead_and_other_host_records_clear(self):
        self.registry.mkdir(mode=0o700)
        now = time.time()
        base = {'id': 'a' * 32, 'pid': os.getpid(), 'start': guard.process_info(os.getpid())[1],
                'since': now - 10, 'expires': now + 10, 'host': guard.socket.gethostname()}
        for index, (name, record) in enumerate([('dead', {**base, 'pid': 999999999}),
                             ('reused', {**base, 'start': 'wrong-process'}),
                             ('stale', {**base, 'expires': now - 1}),
                             ('other-host', {**base, 'host': 'another-machine'})]):
            (self.registry / ('wait-' + f'{index:032x}' + '.json')).write_text(json.dumps(record))
        (self.registry / ('wait-' + 'f' * 32 + '.json')).write_text('{')
        self.assertEqual(guard.waiting_jobs(env=self.env), [])
        self.assertEqual(list(self.registry.glob('*.json')), [])

    def test_separate_worktrees_share_wait_records_without_an_idle_slot_limit(self):
        # Real unrelated worker PIDs share one host registry. Existing waiters cannot
        # delay an idle host admission: only actual sensors decide when to start.
        code = '''
from pathlib import Path
import sys, load_guard
root = Path(sys.argv[1])
load_guard.sample_capacity = lambda *_: load_guard.Capacity(100, 8, True)
load_guard.wait_for_capacity(root, label='gate')
'''
        env = {**os.environ, **self.env, 'HARNESS_LOAD_GUARD': '1',
               'HARNESS_LOAD_GUARD_ANCESTOR': '', 'HARNESS_LOAD_BACKOFF_SECONDS': '.05',
               'HARNESS_LOAD_MAX_WAIT_SECONDS': '5', 'PYTHONPATH': str(Path(guard.__file__).parent)}
        children = []
        try:
            for name in ('worktree-one', 'worktree-two'):
                root = self.root / name
                root.mkdir()
                children.append(subprocess.Popen([sys.executable, '-c', code, str(root)],
                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            deadline = time.monotonic() + 3
            jobs = []
            while time.monotonic() < deadline:
                jobs = guard.waiting_jobs(env=self.env)
                if len(jobs) == 2:
                    break
                time.sleep(.02)
            self.assertEqual({job['pid'] for job in jobs}, {child.pid for child in children})
            self.assertEqual(len({job['root'] for job in jobs}), 2)
            _, sample, sleep, _ = self.wait([guard.Capacity(0, 8, True)])
            self.assertEqual(sample.call_count, 1)
            sleep.assert_not_called()
        finally:
            for child in children:
                child.terminate()
                child.wait(timeout=3)
        self.assertEqual(guard.waiting_jobs(env=self.env), [])

    def test_disabled_env_does_not_need_a_readable_project_file(self):
        self.settings()
        (self.root / 'harness/project/project.json').write_text('{partial')
        env = {**self.env, 'HARNESS_LOAD_GUARD': 'off'}
        with patch.object(guard, 'sample_capacity', side_effect=AssertionError):
            self.assertEqual(guard.wait_for_capacity(self.root, env=env), env)

    def test_sensor_files_missing_unknown_values_and_probe_failures_are_safe(self):
        with patch.object(guard.sys, 'platform', 'linux'), \
             patch.object(guard, 'read_limited', side_effect=OSError), \
             patch.object(guard.os, 'getloadavg', side_effect=OSError), \
             patch.object(guard.os, 'cpu_count', return_value=None):
            sample = guard.sample_capacity()
            self.assertEqual((sample.load, sample.cores, sample.memory_normal), (None, 1, None))
        for output in ('unknown', '0', 'broken'):
            with patch.object(guard.sys, 'platform', 'darwin'), \
                 patch.object(guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)):
                self.assertIsNone(guard.sample_capacity().memory_normal)

    def test_symlink_registry_cannot_change_permissions_or_read_outside_records(self):
        outside = self.root / 'outside'
        outside.mkdir(mode=0o755)
        victim = outside / 'victim.json'
        victim.write_text('private')
        self.registry.symlink_to(outside, target_is_directory=True)
        self.assertEqual(guard.waiting_jobs(env=self.env), [])
        self.assertEqual(victim.read_text(), 'private')
        _, _, sleep, _ = self.wait([guard.Capacity(100, 8, True), guard.Capacity(0, 8, True)])
        sleep.assert_called_once()
        self.assertEqual(outside.stat().st_mode & 0o777, 0o755)

    def test_linux_memory_uses_available_not_unused_pages(self):
        with patch.object(guard.sys, 'platform', 'linux'), \
             patch.object(guard, 'read_limited', return_value='MemTotal: 1000 kB\nMemFree: 1 kB\nMemAvailable: 800 kB\n'), \
             patch.object(guard.os, 'getloadavg', return_value=(1, 9, 9)), \
             patch.object(guard.os, 'cpu_count', return_value=4):
            sample = guard.sample_capacity()
            self.assertEqual((sample.load, sample.cores, sample.memory_normal), (1, 4, True))
            self.assertFalse(guard.sample_capacity(90).memory_normal)

    def test_mac_memory_probe_is_native_read_only_and_bounded(self):
        with patch.object(guard.sys, 'platform', 'darwin'), \
             patch.object(guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '2\n')) as probe:
            self.assertFalse(guard.sample_capacity().memory_normal)
            self.assertEqual(probe.call_args.args[0], ['/usr/sbin/sysctl', '-n', 'kern.memorystatus_vm_pressure_level'])
            self.assertLessEqual(probe.call_args.kwargs['timeout'], 1)
        with patch.object(guard.sys, 'platform', 'darwin'), \
             patch.object(guard.subprocess, 'run', side_effect=subprocess.TimeoutExpired('sysctl', 1)):
            self.assertIsNone(guard.sample_capacity().memory_normal)

    def test_linux_psi_busy_even_with_reusable_memory_headroom(self):
        def read(path, *args):
            if str(path) == '/proc/meminfo':
                return 'MemTotal: 1000 kB\nMemAvailable: 900 kB\n'
            return 'some avg10=12.00 avg60=0.00 avg300=0.00 total=123\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=0\n'
        with patch.object(guard.sys, 'platform', 'linux'), patch.object(guard, 'read_limited', side_effect=read):
            sample = guard.sample_capacity()
            self.assertFalse(sample.memory_normal)
            self.assertEqual(dict(sample.memory_sensors), {'mem_available': True, 'memory_psi': False})
            self.assertTrue(guard.sample_capacity(max_memory_some_percent=20).memory_normal)
        with patch.object(guard.sys, 'platform', 'linux'), patch.object(guard, 'read_limited', side_effect=[
                'MemTotal: 1000 kB\nMemAvailable: 900 kB\n',
                'some avg10=2.00 avg60=0 avg300=0 total=1\nfull avg10=1.50 avg60=0 avg300=0 total=1\n']):
            self.assertFalse(guard.sample_capacity().memory_normal)

    def test_linux_psi_missing_malformed_or_unknown_is_explicit_fail_open(self):
        for psi in (OSError('unsupported'), 'some avg10=nan\nfull avg10=0.00\n', 'garbage'):
            with self.subTest(psi=psi), patch.object(guard.sys, 'platform', 'linux'), \
                 patch.object(guard.os, 'getloadavg', return_value=(0, 0, 0)), \
                 patch.object(guard, 'read_limited', side_effect=[
                     'MemTotal: 1000 kB\nMemAvailable: 900 kB\n', psi]):
                sample = guard.sample_capacity()
            self.assertTrue(sample.memory_normal)
            self.assertIsNone(dict(sample.memory_sensors)['memory_psi'])
            _, _, sleep, output = self.wait([sample])
            sleep.assert_not_called()
            self.assertIn('memory_psi sensor unknown (fail-open)', output)

    def test_psi_thresholds_can_be_overridden_without_disabling_other_memory_signal(self):
        self.settings(max_memory_some_percent=50, max_memory_full_percent=20)
        config = guard.config(self.root, {**self.env, 'HARNESS_LOAD_MEMORY_SOME_PERCENT': '5',
                                         'HARNESS_LOAD_MEMORY_FULL_PERCENT': '2'})
        self.assertEqual(config['max_memory_some_percent'], 5)
        self.assertEqual(config['max_memory_full_percent'], 2)

    def test_existing_user_registry_mode_is_preserved_and_monitoring_is_advisory(self):
        self.registry.mkdir(mode=0o755)
        victim = self.registry / 'unrelated.txt'
        victim.write_text('leave alone')
        _, _, sleep, output = self.wait([guard.Capacity(100, 8, True), guard.Capacity(0, 8, True)])
        sleep.assert_called_once()
        self.assertEqual(self.registry.stat().st_mode & 0o777, 0o755)
        self.assertEqual(victim.read_text(), 'leave alone')
        self.assertEqual(list(self.registry.glob('*.json')), [])
        self.assertIn('monitoring unavailable', output)

    def test_private_selected_registry_does_not_delete_unrelated_json(self):
        self.registry.mkdir(mode=0o700)
        unrelated = self.registry / 'settings.json'
        unrelated.write_text('{"user-owned": true}')
        self.assertEqual(guard.waiting_jobs(env=self.env), [])
        self.assertEqual(unrelated.read_text(), '{"user-owned": true}')

    def test_default_registry_is_cross_project_and_ignores_checkout_and_tmpdir(self):
        first = guard.registry({'HARNESS_PROJECT': '/project-one', 'TMPDIR': '/private/one'})
        second = guard.registry({'HARNESS_PROJECT': '/project-two', 'TMPDIR': '/private/two'})
        self.assertEqual(first, second)
        if os.name == 'posix':
            self.assertEqual(first, Path('/tmp') / f'harness-load-guard-{os.getuid()}')

    def test_missing_pane_env_uses_bounded_read_only_current_pane_lookup(self):
        response = json.dumps({'result': {'pane': {'pane_id': 'w1:p2'}}})
        with patch.object(guard.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, response)) as probe:
            self.assertEqual(guard.current_pane({'HERDR_ENV': '1'}), 'w1:p2')
        self.assertEqual(probe.call_args.args[0], ['herdr', 'pane', 'current', '--current'])
        self.assertLessEqual(probe.call_args.kwargs['timeout'], .25)
        with patch.object(guard.subprocess, 'run', side_effect=AssertionError('unneeded lookup')):
            self.assertEqual(guard.current_pane({'HERDR_ENV': '1', 'HERDR_PANE_ID': 'w1:p3'}), 'w1:p3')
            self.assertIsNone(guard.current_pane({'HERDR_ENV': '0'}))

    def test_unavailable_or_malformed_herdr_pane_is_unknown(self):
        for result in (OSError('missing'), subprocess.TimeoutExpired('herdr', .25),
                       subprocess.CompletedProcess([], 0, 'bad JSON'),
                       subprocess.CompletedProcess([], 0, '{"result": []}'),
                       subprocess.CompletedProcess([], 1, '')):
            with self.subTest(result=result):
                kwargs = {'side_effect': result} if isinstance(result, Exception) else {'return_value': result}
                with patch.object(guard.subprocess, 'run', **kwargs):
                    self.assertIsNone(guard.current_pane({'HERDR_ENV': '1'}))

    def test_unavailable_identity_still_admits_loaded_nested_children_without_rewaiting(self):
        with patch.object(guard, 'process_info', return_value=None):
            environment, _, sleep, output = self.wait([guard.Capacity(0, 8, True)])
        sleep.assert_not_called()
        self.assertEqual(environment['HARNESS_LOAD_GUARD'], '0')
        self.assertNotIn('HARNESS_LOAD_GUARD', self.env)
        self.assertNotIn(guard.ANCESTOR, environment)
        self.assertIn('process identity unknown', output)
        code = '''
import os, sys
from pathlib import Path
from unittest.mock import patch
import load_guard
with patch.object(load_guard, 'sample_capacity', return_value=load_guard.Capacity(1000, 8, False)) as sensors, \\
     patch.object(load_guard, 'process_info', side_effect=AssertionError('no new admission')):
    load_guard.wait_for_capacity(Path(sys.argv[1]))
    sensors.assert_not_called()
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.root)],
            env={**os.environ, **environment, 'PYTHONPATH': str(Path(guard.__file__).parent)},
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
