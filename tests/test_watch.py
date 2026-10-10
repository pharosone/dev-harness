"""Run watcher with persisted dispatches, a fake herdr CLI and a deterministic clock."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
                      "report": str(self.run / "report.md"), "started": "2026-10-10T10:00:00Z"}
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
                               ("hook needs review before it can run", "trust/hook review"),
                               ("Do you want to proceed?", "permission prompt"),
                               ("Allow command?", "permission prompt"),
                               ("Permission required (y/n)", "permission prompt")]:
            with self.subTest(screen=screen):
                self.live(screen)
                self.assertEqual(self.poll(), [f"LANE implement needs-input {detail}"])
                self.live()
                self.assertEqual(self.poll(), [])

    def test_herdr_blocked_without_prompt(self):
        self.live("Busy", "blocked")
        self.assertEqual(self.poll(), ["LANE implement needs-input agent_status: blocked"])

    def test_frozen_clock_and_stable_repeat_key(self):
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.poll(1899), [])
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])
        self.assertEqual(self.poll(1960), [])
        self.assertEqual(self.poll(2500), ["LANE implement frozen 25"])
        self.live("Changed")
        self.assertEqual(self.poll(2501), [])
        self.assertEqual(self.poll(3401), ["LANE implement frozen 15"])

    def test_working_never_frozen(self):
        self.live("Working", "working")
        self.poll()
        self.assertEqual(self.poll(5000), [])
        self.live("Same screen", "working")
        self.assertEqual(self.poll(6000), [])

    def test_read_failure_is_not_a_frozen_screen(self):
        self.poll()
        self.live(read_failed=True)
        self.assertEqual(self.poll(2000), [])
        self.live()
        self.assertEqual(self.poll(2001), [])

    def test_failed_read_does_not_clear_a_question(self):
        self.live("1 question")
        self.poll()
        self.live(read_failed=True)
        self.assertEqual(self.poll(1001), [])
        self.live("1 question")
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


if __name__ == "__main__":
    unittest.main()
