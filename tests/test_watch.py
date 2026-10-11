"""Run watcher with persisted dispatches, a fake herdr CLI and a deterministic clock."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import io
from contextlib import redirect_stderr

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
import dashboard
import dashboard_herdr
import lanes
import task_state
import watch

FAKE_HERDR = '''#!{python}
import json, os, sys
s = json.load(open(os.environ["WATCH_FAKE"]))
args = sys.argv[1:]
if args[:2] == ["agent", "list"]:
    if s.get("list_failed"): sys.exit(1)
    print(json.dumps({{"result": {{"agents": s["agents"]}}}}))
elif args[:2] == ["agent", "read"]:
    if s.get("read_failed"): sys.exit(1)
    print(s["screen"])
elif args == ["pane", "current", "--current"]:
    if not s.get("current_pane"): sys.exit(1)
    print(json.dumps({{"result": {{"pane": {{"pane_id": s["current_pane"]}}}}}}))
else:
    sys.exit(2)
'''


class WatchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.run = self.root / ".harness/tasks/demo"
        (self.run / "lanes").mkdir(parents=True)
        self.overlay = self.root / "harness/project"
        self.overlay.mkdir(parents=True)
        self.fake = self.root / "herdr-state.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        executable = self.bin / "herdr"
        executable.write_text(FAKE_HERDR.format(python=sys.executable))
        executable.chmod(0o755)
        env = {"HERDR_ENV": "1", "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "WATCH_FAKE": str(self.fake), "HARNESS_PROJECT": str(self.overlay)}
        self.environment = patch.dict(os.environ, env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = {"checks": [], "frozen_minutes": 15}
        self.state = {"dispatch": "unit1", "work": "implement", "launcher": "pane",
                      "pane_name": "lane-one", "pane_id": "w1:p2", "report_before": None,
                      "report": str(self.run / "report.md"), "started": "2026-10-10T10:00:00Z",
                      "checkout": str(self.root)}  # 1.6.0: no checkout proves no freeze
        self.agent = {"name": "lane-one", "pane_id": "w1:p2", "agent": "codex",
                      "agent_status": "idle"}
        self.save_lane()
        self.live()

    def save_lane(self):
        (self.run / "lanes/unit1.json").write_text(json.dumps(self.state))

    def live(self, screen="Ready", status="idle", **extra):
        self.agent["agent_status"] = status
        self.fake.write_text(json.dumps({"agents": [self.agent], "screen": screen, **extra}))

    def poll(self, now=1000):
        return watch.poll(self.run, self.root, self.root, self.config, now=now)

    def test_codex_questions_and_permission_dialogs(self):
        for screen, detail in [("? 1 question ⇧← to answer", "question dialog"),
                               ("2 questions", "question dialog"),
                               ("⇧← to answer", "question dialog"),
                               ("enter submit", "question dialog"),
                               ("Trust this folder? t trust all", "trust/hook review"),
                               ("hook needs review before it can run",
                                "hook-trust: the user presses t (Trust all and continue) in "
                                "lane-one"),
                               ("Do you want to proceed?", "permission prompt"),
                               ("Allow command?", "permission prompt"),
                               ("Permission required (y/n)", "permission prompt")]:
            with self.subTest(screen=screen):
                self.live(screen)
                self.assertEqual(self.poll(), [f"LANE implement needs-input {detail}"])
                self.live()
                self.assertEqual(self.poll(), [])

    def test_waiting_jobs_show_host_state_and_reset_lane_freeze_clock(self):
        registry = self.root / '.harness/host-jobs'
        self.live('Ready', 'working')
        self.poll(1000)
        def while_waiting(_):
            lines = self.poll(1900)
            self.assertEqual(len(lines), 1, lines)
            self.assertIn('LOAD gate waiting', lines[0])
            self.assertNotIn('frozen', ' '.join(lines))
            self.assertIn('waiting', watch.summary(self.run))
            self.assertEqual(self.poll(2499), [])
        with patch.dict(os.environ, {'HARNESS_LOAD_GUARD_STATE_DIR': str(registry),
                                     'HARNESS_LOAD_GUARD': '1', 'HARNESS_LOAD_GUARD_ANCESTOR': '',
                                     'HERDR_PANE_ID': self.state['pane_id']}), \
             patch.object(watch.load_guard, 'sample_capacity', side_effect=[
                 watch.load_guard.Capacity(100, 8, True), watch.load_guard.Capacity(0, 8, True)]), \
             patch.object(watch.load_guard, 'time', SimpleNamespace(time=time.time,
                 monotonic=time.monotonic, sleep=while_waiting)), \
             redirect_stderr(io.StringIO()):
            watch.load_guard.wait_for_capacity(self.root, label='gate', run=self.run)
            self.assertEqual(self.poll(2501), [])
            self.assertEqual(watch.summary(self.run), 'Watch: ✅ quiet')
            self.assertEqual(self.poll(3401), ['LANE implement frozen 15'])

    def test_dead_wait_marker_does_not_hide_freeze_or_remain_open(self):
        self.live('Ready', 'working')
        self.poll(1000)
        registry = self.root / '.harness/host-jobs'
        env = {'HARNESS_LOAD_GUARD_STATE_DIR': str(registry), 'HERDR_PANE_ID': self.state['pane_id']}
        path = watch.load_guard.wait_record(self.root, self.run, 'gate', env,
            watch.load_guard.Capacity(100, 8, True), 300, watch.load_guard.process_info(os.getpid()))
        record = json.loads(path.read_text())
        record['pid'] = 999999999
        path.write_text(json.dumps(record))
        with patch.dict(os.environ, {'HARNESS_LOAD_GUARD_STATE_DIR': str(registry)}):
            self.assertEqual(self.poll(1900), ['LANE implement frozen 15'])
            self.assertEqual(list(registry.glob('*.json')), [])

    def test_expired_live_wait_marker_clears_without_suppressing_freeze(self):
        self.live('Ready', 'working')
        self.poll(1000)
        registry = self.root / '.harness/host-jobs'
        env = {'HARNESS_LOAD_GUARD_STATE_DIR': str(registry), 'HERDR_PANE_ID': self.state['pane_id']}
        path = watch.load_guard.wait_record(self.root, self.run, 'gate', env,
            watch.load_guard.Capacity(100, 8, True), 300, watch.load_guard.process_info(os.getpid()))
        record = json.loads(path.read_text())
        record.update(since=time.time() - 10, expires=time.time() - 1)
        path.write_text(json.dumps(record))
        with patch.dict(os.environ, {'HARNESS_LOAD_GUARD_STATE_DIR': str(registry)}):
            self.assertEqual(self.poll(1900), ['LANE implement frozen 15'])
            self.assertFalse(path.exists())

    def test_malformed_registry_env_is_reported_without_probing_or_hanging(self):
        with patch.dict(os.environ, {'HARNESS_LOAD_GUARD_STATE_DIR': '   '}), \
             patch.object(watch.load_guard, 'process_info', side_effect=AssertionError('metadata probe')), \
             redirect_stderr(io.StringIO()) as output:
            self.assertEqual(self.poll(), [])
        self.assertIn('HARNESS_LOAD_GUARD_STATE_DIR', output.getvalue())
        self.assertIn('monitoring unavailable', output.getvalue())

    def test_missing_pane_env_still_associates_an_external_tool_wait_with_lane(self):
        self.live('Ready', 'working', current_pane=self.state['pane_id'])
        self.poll(1000)
        def while_waiting(_):
            lines = self.poll(1900)
            self.assertTrue(any('LOAD gate waiting' in line for line in lines), lines)
            self.assertFalse(any('frozen' in line for line in lines), lines)
        with patch.dict(os.environ, {'HARNESS_LOAD_GUARD_STATE_DIR': str(self.root / '.harness/host-jobs'),
                                     'HERDR_PANE_ID': '', 'HARNESS_LOAD_GUARD': '1',
                                     'HARNESS_LOAD_GUARD_ANCESTOR': ''}), \
             patch.object(watch.load_guard, 'sample_capacity', side_effect=[
                 watch.load_guard.Capacity(100, 8, True), watch.load_guard.Capacity(0, 8, True)]), \
             patch.object(watch.load_guard, 'time', SimpleNamespace(time=time.time,
                 monotonic=time.monotonic, sleep=while_waiting)), redirect_stderr(io.StringIO()):
            watch.load_guard.wait_for_capacity(self.root, label='gate')

    def test_unavailable_current_pane_keeps_wait_visible_without_guessing_lane(self):
        self.live('Ready', 'working')
        self.poll(1000)
        def while_waiting(_):
            lines = self.poll(1900)
            self.assertTrue(any('LOAD gate waiting' in line for line in lines), lines)
            self.assertIn('LANE implement frozen 15', lines)
        with patch.dict(os.environ, {'HARNESS_LOAD_GUARD_STATE_DIR': str(self.root / '.harness/host-jobs'),
                                     'HERDR_PANE_ID': '', 'HARNESS_LOAD_GUARD': '1',
                                     'HARNESS_LOAD_GUARD_ANCESTOR': ''}), \
             patch.object(watch.load_guard, 'sample_capacity', side_effect=[
                 watch.load_guard.Capacity(100, 8, True), watch.load_guard.Capacity(0, 8, True)]), \
             patch.object(watch.load_guard, 'time', SimpleNamespace(time=time.time,
                 monotonic=time.monotonic, sleep=while_waiting)), redirect_stderr(io.StringIO()) as output:
            watch.load_guard.wait_for_capacity(self.root, label='gate')
        self.assertIn('lane association unknown', output.getvalue())

    def test_wait_in_another_pane_never_hides_frozen_lane(self):
        self.live('Ready', 'working')
        self.poll(1000)
        def while_waiting(_):
            lines = self.poll(1900)
            self.assertTrue(any('LOAD xreview waiting' in line for line in lines), lines)
            self.assertIn('LANE implement frozen 15', lines)
        with patch.dict(os.environ, {'HARNESS_LOAD_GUARD_STATE_DIR': str(self.root / '.harness/host-jobs'),
                                     'HARNESS_LOAD_GUARD': '1', 'HARNESS_LOAD_GUARD_ANCESTOR': '',
                                     'HERDR_PANE_ID': 'w1:p99'}), \
             patch.object(watch.load_guard, 'sample_capacity', side_effect=[
                 watch.load_guard.Capacity(100, 8, True), watch.load_guard.Capacity(0, 8, True)]), \
             patch.object(watch.load_guard, 'time', SimpleNamespace(time=time.time,
                 monotonic=time.monotonic, sleep=while_waiting)), \
             redirect_stderr(io.StringIO()):
            watch.load_guard.wait_for_capacity(self.root / 'other-worktree', label='xreview')

    def test_herdr_blocked_without_prompt(self):
        self.live("Busy", "blocked")
        self.assertEqual(self.poll(), ["LANE implement needs-input agent_status: blocked"])

    def test_changed_prompt_kind_is_a_new_condition_without_a_quiet_poll(self):
        self.live("1 question")
        self.assertEqual(self.poll(), ["LANE implement needs-input question dialog"])
        self.live("Trust this folder?")
        self.assertEqual(self.poll(1001), ["LANE implement needs-input trust/hook review"])
        self.assertEqual(self.poll(1002), [])

    def test_frozen_clock_and_stable_repeat_key(self):
        self.live("Ready", "working")  # frozen needs a working agent (1.6.0)
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.poll(1899), [])
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])
        self.assertEqual(self.poll(1960), [])
        self.assertEqual(self.poll(2500), ["LANE implement frozen 25"])
        self.live("Changed", "working")
        self.assertEqual(self.poll(2501), [])
        self.assertEqual(self.poll(3401), ["LANE implement frozen 15"])

    def test_working_is_frozen_only_while_nothing_changes(self):
        # 1.6.0: frozen is a working agent whose screen and checkout stay unchanged; an idle
        # one is reported idle instead.
        self.live("Working", "working")
        self.poll()
        self.assertEqual(self.poll(1899), [])
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])
        self.live("Same screen", "working")
        self.assertEqual(self.poll(1901), [])
        self.live("Same screen", "idle")
        self.assertEqual(self.poll(9000), [])  # an idle agent is never frozen

    def test_read_failure_is_not_a_frozen_screen(self):
        self.poll()
        self.live(read_failed=True)
        self.assertEqual(self.poll(2000), [])
        self.live()
        self.assertEqual(self.poll(2001), [])

    def test_failed_read_does_not_clear_a_question(self):
        self.live("1 question", "blocked")
        self.poll()
        self.live(status="blocked", read_failed=True)
        self.assertEqual(self.poll(1001), [])
        self.live("1 question", "blocked")
        self.assertEqual(self.poll(1002), [])

    def test_product_secret_values_are_not_logged_or_cached(self):
        token = "gh" + "p_" + "Z9" * 20
        self.config["checks"] = [{"name": "worker", "command": f"echo {token}", "every": 120}]
        self.assertEqual(self.poll(), ["CHECK worker [redacted]"])
        for path in (self.run / "events.jsonl", self.run / "lanes/_watch.json"):
            self.assertNotIn(token, path.read_text())

    def test_done_and_old_report_and_ended_no_report(self):
        report = Path(self.state["report"])
        report.write_text("old")
        self.state["report_before"] = lanes.report_stamp(report)
        self.save_lane()
        self.assertEqual(self.poll(), [])
        event = task_state.event_record("dispatch.end", '{"dispatch":"unit1","status":"done"}', None)
        task_state.append_event(self.run / "events.jsonl", event)
        self.assertEqual(self.poll(), ["LANE implement ended-no-report"])
        report.write_text("new complete report")
        self.assertEqual(self.poll(), ["LANE implement done"])

    def test_report_present_before_end_is_done(self):
        Path(self.state["report"]).write_text("complete")
        self.assertEqual(self.poll(), ["LANE implement done"])

    def test_exited_and_replaced_and_unrecognized(self):
        for agents in ([], [{**self.agent, "pane_id": "w1:p99"}],
                       [{**self.agent, "agent": None}]):
            self.fake.write_text(json.dumps({"agents": agents, "screen": ""}))
            self.assertEqual(self.poll(), ["LANE implement exited"])
            self.live()
            self.poll()

    def test_repeat_suppression_rearms_after_clear(self):
        self.live("1 question")
        self.assertEqual(len(self.poll()), 1)
        self.assertEqual(self.poll(1599), [])
        self.assertEqual(len(self.poll(1600)), 1)
        self.live()
        self.assertEqual(self.poll(1601), [])
        self.live("1 question")
        self.assertEqual(len(self.poll(1602)), 1)
        events, _ = task_state.read_events(self.run)
        self.assertEqual([e["repeat"] for e in events], [False, True, False])
        self.assertEqual(len({e["id"] for e in events}), 3)
        self.assertTrue(all(e["type"] == "watch" and e["dispatch"] == "unit1" for e in events))

    def test_product_checks_lines_failure_cadence_and_rearm(self):
        self.config["checks"] = [{"name": "worker", "command": "printf 'retry loop\\nqueue growing\\n'; exit 1", "every": 120}]
        self.assertEqual(self.poll(), ["CHECK worker retry loop", "CHECK worker queue growing", "CHECK worker failed"])
        self.assertEqual(self.poll(1050), [])
        self.config["checks"][0]["command"] = "exit 0"
        self.assertEqual(self.poll(1051), [])
        self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")
        self.config["checks"][0]["command"] = "printf 'retry loop\\n'"
        self.assertEqual(self.poll(1052), ["CHECK worker retry loop"])
        self.assertEqual(watch.summary(self.run), "Watch: 🚨 1 open")

    def test_product_schedule_survives_once_processes(self):
        marker = self.root / "counter"
        self.config["checks"] = [{"name": "tick", "command": "echo tick >> counter; echo anomaly", "every": 120}]
        self.poll()
        self.poll(1119)
        self.assertEqual(marker.read_text(), "tick\n")
        self.poll(1120)
        self.assertEqual(marker.read_text(), "tick\ntick\n")

    def test_no_herdr_fallback_does_not_call_cli(self):
        self.state.update(launcher="headless", agent_status="blocked", pid=os.getpid())
        self.save_lane()
        self.config["checks"] = [{"name": "worker", "command": "echo anomaly", "every": 120}]
        with patch.dict(os.environ, {"HERDR_ENV": "0"}), patch.object(watch, "herdr_call", side_effect=AssertionError):
            self.assertEqual(self.poll(), ["LANE implement needs-input agent_status: blocked", "CHECK worker anomaly"])

    def test_herdr_failure_does_not_mean_exited(self):
        self.live(list_failed=True)
        self.assertEqual(self.poll(), [])

    def test_headless_exit_uses_pid_without_mutating_dispatch(self):
        self.state.update(launcher="headless", pid=999999999)
        self.save_lane()
        self.assertEqual(self.poll(), ["LANE implement exited"])
        self.assertFalse(any(e["type"] == "dispatch.end" for e in task_state.read_events(self.run)[0]))

    def test_dashboard_contains_summary_and_escaped_events(self):
        self.config["checks"] = [{"name": "worker", "command": "echo '<script>retry</script>'", "every": 120}]
        self.poll()
        page = dashboard.render(self.run, self.root, self.run / "dashboard.html",
                                datetime.now(timezone.utc), timedelta(minutes=15))
        self.assertIn("Watch: 🚨 1 open", page)
        self.assertIn("&lt;script&gt;retry&lt;/script&gt;", page)
        self.assertNotIn("<script>retry</script>", page)
        event = task_state.read_events(self.run)[0][0]
        self.assertIn("CHECK worker", dashboard_herdr.event_line(event))
        self.assertIn("Watch: 🚨 1 open", dashboard_herdr.status_lines(
            self.run, self.root, datetime.now(timezone.utc), None))

    def test_overlay_validation(self):
        for value in [None, [], {"typo": 1}, {"frozen_minutes": 0}, {"frozen_minutes": True},
                      {"frozen_minutes": float("nan")}, {"stop_command": ""}, {"checks": {}},
                      {"checks": [None]}, {"checks": [{"name": "x", "command": "true", "typo": 1}]},
                      {"checks": [{"name": "two words", "command": "true"}]},
                      {"checks": [{"name": "x", "command": "true", "every": -1}]},
                      {"checks": [{"name": "x", "command": ""}]},
                      {"checks": [{"name": "x", "command": "true"}] * 2}]:
            with self.subTest(value=value):
                (self.overlay / "project.json").write_text(json.dumps({"watch": value}))
                with self.assertRaises(ValueError):
                    watch.settings(self.root)
        (self.overlay / "project.json").write_text(json.dumps({"watch": {"checks": [{"name": "x", "command": "true"}], "stop_command": "./stop"}}))
        self.assertEqual(watch.settings(self.root)["checks"][0]["every"], 120)

    def test_cli_once_and_invalid_overlay_exit_two(self):
        env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        command = [sys.executable, str(CORE / "scripts/watch.py"), "--task", "demo", "--once"]
        self.live("? 1 question ⇧← to answer")
        result = subprocess.run(command, cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("LANE implement needs-input question dialog", result.stdout)
        (self.overlay / "project.json").write_text('{"watch":{"unknown":1}}')
        result = subprocess.run(command, cwd=self.root, env=env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown watch keys", result.stderr)

    def test_cli_interval_validation_and_zero_quiet_repeat(self):
        command = [sys.executable, str(CORE / "scripts/watch.py"), "--task", "demo", "--once"]
        self.live("1 question")
        for flag, value in (("--every", "0"), ("--every", "nan"), ("--quiet-repeat", "-1")):
            result = subprocess.run([*command, flag, value], cwd=self.root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 2, result.stderr)
        for _ in range(2):
            result = subprocess.run([*command, "--quiet-repeat", "0"], cwd=self.root, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("needs-input", result.stdout)

    def test_terminal_conditions_close_after_explicit_acknowledgment(self):
        for kind in ("done", "ended-no-report"):
            with self.subTest(kind=kind):
                if kind == "done":
                    Path(self.state["report"]).write_text("accepted")
                else:
                    Path(self.state["report"]).unlink()
                    task_state.append_event(self.run / "events.jsonl", task_state.event_record(
                        "dispatch.end", '{"dispatch":"unit1","status":"failed"}', None))
                    (self.run / "lanes/_watch.json").unlink()
                self.assertEqual(self.poll(), [f"LANE implement {kind}"])
                command = [sys.executable, str(CORE / "scripts/watch.py"), "--task", "demo", "--ack", "unit1"]
                result = subprocess.run(command, cwd=self.root, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")
                self.assertEqual(self.poll(1700), [])
                result = subprocess.run(command, cwd=self.root, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_ack_cannot_hide_an_input_dialog(self):
        self.live("1 question")
        self.poll()
        result = subprocess.run([sys.executable, str(CORE / "scripts/watch.py"), "--task", "demo",
                                 "--ack", "unit1"], cwd=self.root, text=True, capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("terminal", result.stderr)
        self.assertEqual(watch.summary(self.run), "Watch: 🚨 1 open")

    def test_prose_containing_working_does_not_disable_freeze_detection(self):
        for screen in ("nothing to commit, working tree clean", "Working directory: /tmp"):
            self.live(screen)
            self.poll(1000)
            # Prose is not a working indicator, so the idle agent is never "frozen" (1.6.0:
            # only a working agent is).
            self.assertEqual(self.poll(1900), [])
            self.live("reset")
            self.poll(1901)

    def test_agent_working_indicator_line_prevents_false_prompts_and_freezes(self):
        self.live("• Working (23s • esc to interrupt)\nI have 2 questions\nlog: (y/n)")
        self.assertEqual(self.poll(), [])
        self.live("• Working (17m • esc to interrupt)\nI have 2 questions\nlog: (y/n)")
        self.assertNotIn("needs-input", " ".join(self.poll(2000)))

    def test_working_agent_prose_does_not_trigger_input(self):
        self.live("I have 2 questions about the spec\nlog: (y/n)", "working")
        self.assertEqual(self.poll(), [])

    def test_partial_lane_state_is_logged_without_losing_other_checks(self):
        (self.run / "lanes/unit1.json").write_text('{"dispatch":')
        self.config["checks"] = [{"name": "worker", "command": "echo anomaly", "every": 120}]
        with redirect_stderr(io.StringIO()) as err:
            self.assertEqual(self.poll(), ["CHECK worker anomaly"])
        self.assertIn("unit1", err.getvalue())
        self.save_lane()
        self.live("1 question")
        self.assertEqual(self.poll(1001), ["LANE implement needs-input question dialog"])

    def test_continuous_watch_retries_poll_errors_but_bad_config_exits_two(self):
        for failure in (json.JSONDecodeError("partial", "", 0), OSError("temporary")):
            with patch.object(task_state, "checkouts", return_value=(self.root, self.root)), \
                 patch.object(watch, "poll", side_effect=[failure, [], KeyboardInterrupt]) as polling, \
                 patch.object(watch.time, "sleep"), redirect_stderr(io.StringIO()) as err:
                self.assertEqual(watch.main(["--task", "demo"]), 0)
                self.assertEqual(polling.call_count, 3)
                self.assertIn("retry", err.getvalue())
                self.assertTrue(any(e["type"] == "watch.error" for e in task_state.read_events(self.run)[0]))

    def test_launch_window_does_not_report_exit_before_agent_registration(self):
        self.state.update(launch_pending=True, started="1970-01-01T00:16:40Z", pane_id=None)
        self.save_lane()
        self.fake.write_text('{"agents":[],"screen":""}')
        self.assertEqual(self.poll(1000), [])
        self.state["pane_id"] = "w1:p2"
        self.save_lane()
        self.assertEqual(self.poll(1100), [])
        self.fake.write_text(json.dumps({"agents": [{**self.agent, "agent": None}], "screen": ""}))
        self.assertEqual(self.poll(1101), [])
        self.assertEqual(self.poll(1301), ["LANE implement exited"])

    def test_completed_launch_can_exit_immediately(self):
        self.state.update(launch_pending=False, started="1970-01-01T00:16:40Z")
        self.save_lane()
        self.fake.write_text('{"agents":[],"screen":""}')
        self.assertEqual(self.poll(1001), ["LANE implement exited"])

    def test_live_launcher_keeps_registration_window_open_even_after_five_minutes(self):
        self.state.update(launch_pending=True, launch_pid=os.getpid(), started="1970-01-01T00:16:40Z")
        self.save_lane()
        self.fake.write_text('{"agents":[],"screen":""}')
        self.assertEqual(self.poll(2000), [])

    def test_dead_launcher_is_an_exit_without_waiting_a_fixed_window(self):
        self.state.update(launch_pending=True, launch_pid=999999999, started="1970-01-01T00:16:40Z")
        self.save_lane()
        self.fake.write_text('{"agents":[],"screen":""}')
        self.assertEqual(self.poll(1001), ["LANE implement exited"])

    def test_check_timeout_exit_race_is_a_check_failure(self):
        child = MagicMock()
        child.__enter__.return_value = child
        child.communicate.side_effect = [subprocess.TimeoutExpired("check", 60), ("", None)]
        child.returncode = 0
        with patch.object(watch.subprocess, "Popen", return_value=child), \
             patch.object(watch.os, "killpg", side_effect=ProcessLookupError):
            self.assertEqual(watch.check_command("check", self.root), ("", 1))

    def test_check_timeout_cannot_wait_forever_for_inherited_stdout(self):
        child = MagicMock()
        child.__enter__.return_value = child
        child.communicate.side_effect = [subprocess.TimeoutExpired("check", 60),
                                        subprocess.TimeoutExpired("check", 2, output=b"partial\n")]
        child.wait.return_value = 0
        with patch.object(watch.subprocess, "Popen", return_value=child), patch.object(watch.os, "killpg"):
            self.assertEqual(watch.check_command("check", self.root), ("partial\n", 1))
        self.assertTrue(all(call.kwargs.get("timeout") for call in child.communicate.call_args_list))
        self.assertTrue(all(call.kwargs.get("timeout") for call in child.wait.call_args_list))
        child.stdout.close.assert_called()

    def test_stable_product_key_suppresses_changing_counts_and_rearms(self):
        self.config["checks"] = [{"name": "worker", "command": "check", "every": 120}]
        with patch.object(watch, "check_command", side_effect=[
            ('{"key":"retry-loop","message":"retry loop: 37 attempts"}', 0),
            ('{"key":"retry-loop","message":"retry loop: 38 attempts"}', 0),
            ("", 0), ('{"key":"retry-loop","message":"retry loop: 39 attempts"}', 0)]):
            self.assertEqual(self.poll(), ["CHECK worker retry loop: 37 attempts"])
            self.assertEqual(self.poll(1120), [])
            self.assertEqual(self.poll(1240), [])
            self.assertEqual(self.poll(1360), ["CHECK worker retry loop: 39 attempts"])

    def test_frozen_minute_changes_do_not_rewrite_html_without_emission(self):
        self.live("Ready", "working")
        self.poll()
        self.poll(1900)
        with patch.object(lanes, "regenerate_dashboard") as render:
            self.assertEqual(self.poll(1960), [])
            render.assert_not_called()

    def test_html_watch_history_is_bounded_and_full_events_remain_on_disk(self):
        for i in range(70):
            task_state.append_event(self.run / "events.jsonl", task_state.event_record(
                "watch", json.dumps({"line": f"CHECK worker anomaly-{i:03}"}), None))
        page = dashboard.render(self.run, self.root, self.run / "dashboard.html",
                                datetime.now(timezone.utc), timedelta(minutes=15))
        self.assertNotIn("anomaly-000", page)
        self.assertIn("anomaly-069", page)
        self.assertEqual(page.count("CHECK worker anomaly-"), 50)
        self.assertEqual(len(task_state.read_events(self.run)[0]), 70)


if __name__ == "__main__":
    unittest.main()
