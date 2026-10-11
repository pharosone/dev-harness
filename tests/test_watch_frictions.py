"""Frictions seen in the outbound-p01 run (1.6.0), the watch side: items 5, 6, 13, 14 and 16.

5   frozen only when the agent is working AND its screen is unchanged AND no file in the lane's
    checkout changed for frozen_minutes (Codex screens often show only the prompt line).
6   a flag file the lane wrote since its start (<report>.flag.md) is "needs-input flag".
13  done only when the dispatch ended, or the agent is idle/done with a report written since
    its start; a report written while the agent works is not done.
14  Codex's startup "hooks need review" dialog is "needs-input hook-trust" with the one key the
    user presses.
16  writes to <report>.progress.md are activity, never done.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
import lanes  # noqa: E402
import task_state  # noqa: E402
import watch  # noqa: E402

FAKE_HERDR = '''#!{python}
import json, os, sys
s = json.load(open(os.environ["WATCH_FAKE"]))
args = sys.argv[1:]
if args[:2] == ["agent", "list"]:
    print(json.dumps({{"result": {{"agents": s["agents"]}}}}))
elif args[:2] == ["agent", "read"]:
    if s.get("read_failed"): sys.exit(1)
    print(s["screen"])
else:
    sys.exit(2)
'''


class WatchFrictionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        git = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        self.run = self.root / ".harness/tasks/demo"
        (self.run / "lanes").mkdir(parents=True)
        self.overlay = self.root / "harness/project"
        self.overlay.mkdir(parents=True)
        self.checkout = self.root / "work"
        self.checkout.mkdir()
        for args in (("init", "-q"), ("config", "user.name", "T"),
                     ("config", "user.email", "t@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.run(["git", *args], cwd=self.checkout, check=True, env={**os.environ, **git})
        (self.checkout / "app.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=self.checkout, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.checkout, check=True,
                       env={**os.environ, **git})
        self.fake = self.root / "herdr-state.json"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        executable = self.bin / "herdr"
        executable.write_text(FAKE_HERDR.format(python=sys.executable))
        executable.chmod(0o755)
        env = {"HERDR_ENV": "1", "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
               "WATCH_FAKE": str(self.fake), "HARNESS_PROJECT": str(self.overlay), **git}
        self.environment = patch.dict(os.environ, env)
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.config = {"checks": [], "frozen_minutes": 15}
        self.report = self.root / "reports" / "unit1.md"
        self.report.parent.mkdir()
        self.state = {"dispatch": "unit1", "work": "implement", "launcher": "pane",
                      "pane_name": "lane-one", "pane_id": "w1:p2", "report_before": None,
                      "flag_before": None, "report": str(self.report),
                      "checkout": str(self.checkout), "started": "2026-10-10T10:00:00Z"}
        self.agent = {"name": "lane-one", "pane_id": "w1:p2", "agent": "codex",
                      "agent_status": "working"}
        self.save_lane()
        self.live()

    def save_lane(self):
        (self.run / "lanes/unit1.json").write_text(json.dumps(self.state))

    def live(self, screen="› ", status="working", **extra):
        self.agent["agent_status"] = status
        self.fake.write_text(json.dumps({"agents": [self.agent], "screen": screen, **extra}))

    def poll(self, now=1000):
        return watch.poll(self.run, self.root, self.root, self.config, now=now)

    def touch(self, name, text):
        path = self.checkout / name
        path.write_text(text)
        return path

    # --- item 5 ---------------------------------------------------------------------------

    def test_working_with_unchanged_screen_and_files_is_frozen(self):
        self.assertEqual(self.poll(), [])
        self.assertEqual(self.poll(1899), [])
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])

    def test_a_file_change_in_the_checkout_is_activity(self):
        self.poll()
        self.touch("app.py", "x = 2\n")
        self.assertEqual(self.poll(1500), [])
        self.assertEqual(self.poll(1900), [])
        self.assertEqual(self.poll(2400), ["LANE implement frozen 15"])

    def test_a_new_untracked_file_and_a_rewrite_are_activity(self):
        self.poll()
        self.touch("new.py", "a\n")
        self.poll(1500)
        self.touch("new.py", "bb\n")
        self.assertEqual(self.poll(2300), [])
        self.assertEqual(self.poll(3199), [])
        self.assertEqual(self.poll(3200), ["LANE implement frozen 15"])

    def test_progress_notes_are_activity_never_done(self):
        self.poll()
        Path(f"{self.report}.progress.md").write_text("half way\n")
        self.assertEqual(self.poll(1500), [])
        self.assertEqual(self.poll(2399), [])

    def test_a_changing_screen_is_activity(self):
        self.poll()
        self.live("• Working (5m)")
        self.assertEqual(self.poll(1900), [])

    def test_only_a_working_agent_is_frozen(self):
        for status in ("idle", "done", "unknown", None):
            with self.subTest(status=status):
                (self.run / "lanes/_watch.json").unlink(missing_ok=True)
                self.live("› ", status)
                self.poll()
                self.assertEqual(self.poll(1900), [])
                self.assertEqual(self.poll(9000), [])

    def test_a_tracked_file_touched_with_the_same_content_is_activity(self):
        self.poll()
        app = self.checkout / "app.py"
        os.utime(app, (app.stat().st_atime, app.stat().st_mtime + 500))
        self.assertEqual(self.poll(1500), [])
        self.assertEqual(self.poll(2399), [])
        self.assertEqual(self.poll(2400), ["LANE implement frozen 15"])

    # Fix-up after the Claude review: ignored files are not scanned (a node_modules or .venv
    # tree pushed every lane past the budget, so frozen never fired), and unknown activity is
    # said once instead of silently disabling freeze detection.

    def test_ignored_output_is_not_activity(self):
        (self.checkout / ".gitignore").write_text("build/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.checkout, check=True)
        (self.checkout / "build").mkdir()
        (self.checkout / "build" / "test.log").write_text("1\n")
        self.poll()
        (self.checkout / "build" / "test.log").write_text("1\n2\n")
        self.assertEqual(self.poll(1500), [])
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])

    def test_a_large_ignored_tree_keeps_activity_known(self):
        (self.checkout / ".gitignore").write_text("node_modules/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.checkout, check=True)
        (self.checkout / "node_modules").mkdir()
        for n in range(5):
            (self.checkout / "node_modules" / f"m{n}.js").write_text("x")
        with patch.object(watch, "ACTIVITY_MAX_FILES", 3):
            self.assertIsNotNone(watch.checkout_activity(self.state))
            self.assertEqual(self.poll(), [])
            self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])

    def test_unknown_activity_is_said_once(self):
        del self.state["checkout"]
        self.save_lane()
        self.assertEqual(self.poll(), [
            "LANE implement activity-unknown (no checkout recorded; frozen cannot be detected)"])
        self.assertEqual(self.poll(1001), [])
        self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")
        self.assertEqual(self.poll(5000), [])

    def test_unknown_activity_names_the_scan_budget(self):
        for n in range(5):
            self.touch(f"f{n}.txt", "x")
        with patch.object(watch, "ACTIVITY_MAX_FILES", 3):
            self.assertEqual(self.poll(), [
                "LANE implement activity-unknown (more than 3 files to scan; frozen cannot be "
                "detected)"])
            self.assertEqual(self.poll(9000), [])

    def test_unknown_activity_is_not_said_for_an_idle_agent(self):
        del self.state["checkout"]
        self.save_lane()
        self.live("› ", "idle")
        self.assertEqual(self.poll(), [])

    # Fix-up: the working indicator's spinner glyph and elapsed timer are not screen activity,
    # so a lane stuck in one tool call still looks frozen; token counters still are activity.

    def test_screens_differing_only_in_spinner_and_timer_are_the_same(self):
        for before, after in [
                ("output\n⠋ Working (14m 3s • esc to interrupt)\n› ",
                 "output\n⠙ Working (15m 12s • esc to interrupt)\n› "),
                ("✻ Cogitating… (59s · ↓ 2.1k tokens · esc to interrupt)",
                 "✽ Cogitating… (1h 2m 3s · ↓ 2.1k tokens · esc to interrupt)"),
                ("• Working (3s)", "◦ Working (2m 0s)")]:
            with self.subTest(before=before):
                self.assertEqual(watch.screen_digest(before), watch.screen_digest(after))
        self.assertNotEqual(watch.screen_digest("a\n• Working (3s)"),
                            watch.screen_digest("b\n• Working (3s)"))
        self.assertNotEqual(watch.screen_digest("✻ Cogitating… (5s · ↓ 2.1k tokens)"),
                            watch.screen_digest("✻ Cogitating… (5s · ↓ 2.4k tokens)"))
        self.assertNotEqual(watch.screen_digest("Built in 14m 3s"),
                            watch.screen_digest("Built in 15m 3s"))

    def test_only_a_status_line_is_normalized(self):
        # Ordinary output keeps its timer: only bare "Working" or a status verb after a
        # spinner glyph is a working indicator.
        for before, after in [("Result (14m 3s)", "Result (15m 3s)"),
                              ("• Result (14m 3s)", "• Result (15m 3s)"),
                              ("* Result (14m 3s)", "* Result (15m 3s)"),
                              ("Cogitating… (14m 3s)", "Cogitating… (15m 3s)")]:
            with self.subTest(before=before):
                self.assertNotEqual(watch.screen_digest(before), watch.screen_digest(after))
        for before, after in [("Working (14m 3s)", "Working (15m 3s)"),
                              ("Working… (14m 3s)", "Working… (15m 3s)"),
                              ("✶ Cogitating… (14m 3s)", "✢ Cogitating… (15m 3s)"),
                              ("· Pondering… (3s)", "✳ Pondering… (4s)")]:
            with self.subTest(before=before):
                self.assertEqual(watch.screen_digest(before), watch.screen_digest(after))

    def test_a_lane_stuck_in_one_tool_call_is_frozen(self):
        self.live("$ pytest -x\n⠋ Working (1m 3s • esc to interrupt)")
        self.poll()
        self.live("$ pytest -x\n⠼ Working (9m 41s • esc to interrupt)")
        self.assertEqual(self.poll(1500), [])
        self.live("$ pytest -x\n⠧ Working (16m 2s • esc to interrupt)")
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])

    def test_a_growing_token_count_is_activity(self):
        self.live("✻ Cogitating… (1m 3s · ↓ 2.1k tokens · esc to interrupt)")
        self.poll()
        self.live("✶ Cogitating… (16m 2s · ↓ 2.4k tokens · esc to interrupt)")
        self.assertEqual(self.poll(1900), [])

    def test_a_checkout_over_the_scan_budget_is_unknown_activity(self):
        for n in range(5):
            self.touch(f"f{n}.txt", "x")
        with patch.object(watch, "ACTIVITY_MAX_FILES", 3):
            self.assertIsNone(watch.checkout_activity(self.state))
            self.poll()
            self.assertEqual(self.poll(9000), [])

    def test_a_state_without_a_checkout_cannot_prove_a_freeze(self):
        del self.state["checkout"]
        self.save_lane()
        self.poll()
        self.assertEqual(self.poll(9000), [])

    def test_a_failed_screen_read_cannot_keep_a_freeze_after_file_activity(self):
        self.poll()
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])
        self.touch("app.py", "x = 2\n")
        self.live(read_failed=True)
        self.poll(1901)
        self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")

    def test_native_subagent_canonical_and_tripwire_flags_are_visible(self):
        (self.run / "lanes/unit1.json").unlink()
        task_state.append_event(self.run / "events.jsonl", task_state.event_record(
            "dispatch.start", '{"dispatch":"native-1","work":"02","launcher":"subagent","report":null}', None))
        lane = lanes.lane_flag(self.run, "02")
        lane.write_text("Need a decision\n")
        self.assertEqual(self.poll(), ["LANE 02 needs-input flag"])
        lane.unlink()
        self.poll(1001)
        unit = lanes.unit_flag(self.run, "02")
        unit.parent.mkdir(exist_ok=True)
        unit.write_text("Need a fixture decision\n")
        self.assertEqual(self.poll(1002), ["LANE 02 needs-input flag"])

    def test_a_failed_read_restarts_the_freeze_clock(self):
        self.poll()
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])
        self.live(read_failed=True)
        self.poll(1901)
        self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")
        for status in ("idle", "done", "unknown"):
            with self.subTest(status=status):
                self.live(status=status, read_failed=True)
                self.poll(1902)
                self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")
                self.live(read_failed=True)
                self.poll(1903)
                self.live()
                self.poll(1904)
                self.poll(2804)

    def test_a_failed_read_keeps_an_unanswered_dialog(self):
        self.live("1 question", "blocked")
        self.assertEqual(self.poll(), ["LANE implement needs-input question dialog"])
        self.live(status="idle", read_failed=True)
        self.poll(1001)
        self.assertEqual(watch.summary(self.run), "Watch: 🚨 1 open")

    def test_observer_run_state_is_not_activity(self):
        (self.checkout / ".harness").mkdir()
        self.poll()
        (self.checkout / ".harness" / "events.jsonl").write_text("{}\n")
        self.assertEqual(self.poll(1900), ["LANE implement frozen 15"])

    def test_an_unreadable_checkout_cannot_prove_a_freeze(self):
        self.state["checkout"] = str(self.root / "missing")
        self.save_lane()
        self.poll()
        self.assertEqual(self.poll(9000), [])

    # --- item 13 --------------------------------------------------------------------------

    def test_a_report_written_while_working_is_not_done(self):
        self.poll()
        self.report.write_text("partial\n")
        self.assertEqual(self.poll(1001), [])
        self.live("› ", "idle")
        self.assertEqual(self.poll(1002), ["LANE implement done"])

    def test_a_running_headless_report_waits_for_the_end(self):
        self.state.update(launcher="headless", pid=os.getpid(), pane_name=None, pane_id=None)
        self.save_lane()
        self.report.write_text("partial\n")
        self.assertEqual(self.poll(), [])
        task_state.append_event(self.run / "events.jsonl", task_state.event_record(
            "dispatch.end", '{"dispatch":"unit1","status":"done"}', None))
        self.assertEqual(self.poll(1001), ["LANE implement done"])

    def test_a_vanished_agent_with_a_fresh_report_is_exited_until_its_end(self):
        self.report.write_text("progress so far\n")
        self.fake.write_text(json.dumps({"agents": [], "screen": ""}))
        self.assertEqual(self.poll(), ["LANE implement exited"])
        task_state.append_event(self.run / "events.jsonl", task_state.event_record(
            "dispatch.end", '{"dispatch":"unit1","status":"done"}', None))
        self.assertEqual(self.poll(1001), ["LANE implement done"])

    def test_a_dead_headless_process_with_a_fresh_report_is_exited(self):
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait()
        self.state.update(launcher="headless", pid=dead.pid, pane_name=None, pane_id=None)
        self.save_lane()
        self.report.write_text("early progress\n")
        self.assertEqual(self.poll(), ["LANE implement exited"])

    def test_a_pane_without_herdr_uses_the_kept_observation(self):
        os.environ["HERDR_ENV"] = "0"
        self.report.write_text("partial\n")
        self.state["agent_status"] = "working"
        self.save_lane()
        self.assertEqual(self.poll(), [])
        self.state["agent_status"] = "idle"
        self.save_lane()
        self.assertEqual(self.poll(1001), ["LANE implement done"])

    # --- item 6 ---------------------------------------------------------------------------

    def test_the_canonical_flag_is_needs_input_until_removed(self):
        flag = self.run / "lanes" / "implement.flag.md"
        self.assertEqual(lanes.lane_flag(self.run, "implement"), flag)
        flag.write_text("Which column name: a or b?\n")
        self.assertEqual(self.poll(), ["LANE implement needs-input flag"])
        self.assertEqual(self.poll(1001), [])
        flag.unlink()
        self.poll(1002)
        self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")

    def test_a_work_name_is_made_safe_for_its_flag_file(self):
        self.assertEqual(lanes.lane_flag(self.run, "U9 / api").name, "u9-api.flag.md")
        self.assertEqual(lanes.lane_flag(self.run, "U9.b").name, "U9.b.flag.md")

    def test_an_open_tripwire_flag_is_needs_input_until_resolved(self):
        (self.run / "units").mkdir()
        flag = self.run / "units" / "implement-flag.md"
        flag.write_text("spec and test disagree\n")
        self.assertEqual(self.poll(), ["LANE implement needs-input flag"])
        flag.unlink()  # tripwire.py resolve archives it
        self.poll(1001)
        self.assertEqual(watch.summary(self.run), "Watch: ✅ quiet")

    def test_the_report_side_flag_counts_only_when_written_since_the_start(self):
        flag = Path(f"{self.report}.flag.md")
        flag.write_text("old question\n")
        self.state["report_flag_before"] = lanes.report_stamp(flag)
        self.save_lane()
        self.assertEqual(self.poll(), [])
        flag.write_text("a new question\n")
        self.assertEqual(self.poll(1001), ["LANE implement needs-input flag"])

    def test_the_flag_text_is_never_copied(self):
        (self.run / "lanes" / "implement.flag.md").write_text("token ghp_" + "Z9" * 20 + "\n")
        self.poll()
        for path in (self.run / "events.jsonl", self.run / "lanes/_watch.json"):
            self.assertNotIn("Z9Z9", path.read_text())

    # --- item 14 --------------------------------------------------------------------------

    def test_codex_hook_review_is_hook_trust_with_its_one_key(self):
        screen = ("Hooks need review\n1 hook is new or changed.\n"
                  "› 1. Trust all and continue\n  2. Continue without trusting (hooks won't run)")
        self.live(screen, "blocked")
        self.assertEqual(self.poll(), [
            "LANE implement needs-input hook-trust: the user presses t (Trust all and continue) "
            "in lane-one"])

    def test_folder_trust_stays_a_trust_review(self):
        self.live("Do you trust this folder?", "blocked")
        self.assertEqual(self.poll(), ["LANE implement needs-input trust/hook review"])


if __name__ == "__main__":
    unittest.main()
