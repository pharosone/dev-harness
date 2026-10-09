"""Tripwires stop a lane on deterministic evidence, not on the orchestrator's judgement."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
TRIPWIRE = SCRIPTS / "tripwire.py"
TASK_STATE = SCRIPTS / "task_state.py"


class TripwireTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.root / ".gitignore").write_text(".harness/\n")
        (self.root / "spec.md").write_text("# Approved spec\n")
        for name in ("server/export/csv.go", "server/auth/middleware.go", "tests/csv_test.go"):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"// {name}\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.worktree = Path(self.temp.name) / "lane"
        self.git("worktree", "add", "-q", "--detach", str(self.worktree), "HEAD")
        self.ok(self.run_script(TASK_STATE, "init", "feature", "--spec", "spec.md"))
        self.units = self.root / ".harness/tasks/feature/units"

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.root, env=self.env, text=True)

    def run_script(self, script, *args, cwd=None, stdin=None):
        return subprocess.run([sys.executable, str(script), *args], cwd=cwd or self.root,
                              env=self.env, capture_output=True, text=True, input=stdin)

    def tripwire(self, *args, cwd=None, stdin=None):
        return self.run_script(TRIPWIRE, *args, cwd=cwd, stdin=stdin)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def tripped(self, result):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        return result.stdout

    def scope(self, *globs, budget=None):
        extra = ["--turn-budget", str(budget)] if budget else []
        return self.ok(self.tripwire("scope", "feature", "01", "--base", "HEAD", *extra, *globs,
                                     cwd=self.worktree))

    def hook(self, description, tool_uses=12, status="completed"):
        payload = {
            "hook_event_name": "PostToolUse",
            "tool_name": "Agent",
            "cwd": str(self.root),
            "tool_input": {"description": description, "prompt": "Implement the unit."},
            "tool_response": {"status": status, "totalToolUseCount": tool_uses,
                              "totalDurationMs": 4200, "totalTokens": 5100,
                              "resolvedModel": "claude-opus-5-5"},
        }
        return self.tripwire("hook", stdin=json.dumps(payload))

    def test_a_unit_inside_its_paths_passes(self):
        self.scope("server/export/**", "tests/csv_test.go")
        (self.worktree / "server/export/csv.go").write_text("// changed\n")
        (self.worktree / "server/export/rows.go").write_text("// new file\n")
        self.assertIn("ok", self.ok(self.tripwire("check", "feature", "01")))

    def test_a_file_outside_the_unit_paths_trips_and_is_named(self):
        self.scope("server/export/**")
        (self.worktree / "server/auth/middleware.go").write_text("// widened\n")
        (self.worktree / "server/auth/bypass.go").write_text("// untracked\n")
        out = self.tripped(self.tripwire("check", "feature", "01"))
        self.assertIn("server/auth/middleware.go", out)
        self.assertIn("server/auth/bypass.go", out)
        self.assertNotIn("server/export", out)

    def test_committed_changes_since_the_unit_base_count(self):
        self.scope("server/export/**")
        (self.worktree / "server/auth/middleware.go").write_text("// committed\n")
        self.git("commit", "-qam", "unit work", cwd=self.worktree)
        self.assertIn("server/auth/middleware.go", self.tripped(self.tripwire("check", "feature", "01")))

    def test_single_star_does_not_cross_directories(self):
        self.scope("server/*")
        (self.worktree / "server/export/csv.go").write_text("// nested\n")
        self.assertIn("server/export/csv.go", self.tripped(self.tripwire("check", "feature", "01")))

    def test_an_agent_flag_trips_with_its_reason(self):
        self.ok(self.tripwire("flag", "feature", "01", "Spec says 422, frozen test expects 409"))
        out = self.tripped(self.tripwire("check", "feature", "01"))
        self.assertIn("Spec says 422", out)

    def test_a_dispatch_over_the_turn_limit_trips(self):
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "40"))
        self.ok(self.tripwire("check", "feature", "01"))
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "81"))
        self.assertIn("81", self.tripped(self.tripwire("check", "feature", "01")))

    def test_cumulative_spend_over_twice_the_budget_trips(self):
        self.scope("server/export/**", budget=30)
        for uses in ("25", "25"):
            self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", uses))
        self.ok(self.tripwire("check", "feature", "01"))
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "15"))
        self.assertIn("65", self.tripped(self.tripwire("check", "feature", "01")))

    def test_frozen_tests_changed_twice_trip_once_recorded(self):
        red = self.units.parent / "01-red.txt"
        red.write_text("--- FAIL: TestCSV\n")
        self.ok(self.run_script(TASK_STATE, "freeze", "feature", "01", "--red-log", str(red),
                                "tests/csv_test.go", cwd=self.worktree))
        self.scope("server/export/**", "tests/**")
        (self.worktree / "tests/csv_test.go").write_text("// weakened\n")
        first = self.ok(self.tripwire("check", "feature", "01", "--record"))
        self.assertIn("changed", first)
        self.tripped(self.tripwire("check", "feature", "01", "--record"))

    def test_hook_records_spend_and_blocks_on_a_trip(self):
        self.scope("server/export/**")
        (self.worktree / "server/auth/middleware.go").write_text("// widened\n")
        result = self.hook("[pharos task=feature unit=01] green")
        self.assertEqual(result.returncode, 0, result.stderr)
        decision = json.loads(result.stdout)
        self.assertEqual(decision["decision"], "block")
        self.assertIn("server/auth/middleware.go", decision["reason"])
        self.assertIn("gate", decision["reason"].lower())
        ledger = (self.units / "01-spend.jsonl").read_text().splitlines()
        self.assertEqual(json.loads(ledger[-1])["tool_uses"], 12)
        self.assertEqual(json.loads(ledger[-1])["model"], "claude-opus-5-5")

    def test_hook_is_silent_for_untagged_unknown_and_background_dispatches(self):
        for description in ("explore the repo", "[pharos task=missing unit=01] red"):
            result = self.hook(description)
            self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        self.scope("server/export/**")
        (self.worktree / "server/auth/middleware.go").write_text("// widened\n")
        result = self.hook("[pharos task=feature unit=01] green", status="async_launched")
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        self.assertFalse((self.units / "01-spend.jsonl").exists())

    def test_report_sums_spend_per_unit_and_model(self):
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "30", "--model", "opus", "--agent", "implementer"))
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "12", "--model", "sonnet", "--agent", "qa-verifier"))
        self.ok(self.tripwire("spend", "feature", "02", "--tool-uses", "5", "--model", "opus"))
        self.ok(self.tripwire("flag", "feature", "02", "spec says 422, test says 409"))
        out = self.ok(self.tripwire("report", "feature"))
        lines = out.splitlines()
        self.assertIn("| 01 | 2 | 42 | opus, sonnet | ok |", lines)
        self.assertIn("| 02 | 1 | 5 | opus | TRIPPED |", lines)
        self.assertIn("| total | 3 | 47 | | |", lines)

    def test_unknown_usage_is_not_reported_as_zero(self):
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "10", "--model", "opus"))
        with (self.units / "01-spend.jsonl").open("a") as stream:
            stream.write(json.dumps({"at": "2026-10-09T00:00:00+00:00", "model": "opus"}) + "\n")
        lines = self.ok(self.tripwire("report", "feature")).splitlines()
        self.assertIn("| 01 | 2 | 10 + unknown | opus | ok |", lines)
        self.assertIn("| total | 2 | 10 + unknown | | |", lines)

    def test_a_gone_checkout_is_reported_as_unchecked(self):
        self.scope("server/export/**")
        self.git("worktree", "remove", "--force", str(self.worktree))
        self.assertIn("| 01 | 0 | 0 |  | unchecked |", self.ok(self.tripwire("report", "feature")).splitlines())

    def test_a_unit_with_only_frozen_tests_is_reported(self):
        red = Path(self.temp.name) / "red.log"
        red.write_text("--- FAIL: TestCSV (0.00s)\n")
        self.ok(self.run_script(TASK_STATE, "freeze", "feature", "04", "--red-log", str(red),
                                "tests/csv_test.go"))
        self.assertIn("| 04 | 0 | 0 |  | ok |", self.ok(self.tripwire("report", "feature")).splitlines())

    def test_a_damaged_ledger_line_does_not_break_the_report(self):
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "7", "--model", "opus"))
        with (self.units / "01-spend.jsonl").open("a") as stream:
            stream.write('{"at": "2026-10-09T00:00:00+00:00", "tool_us')
        lines = self.ok(self.tripwire("report", "feature")).splitlines()
        self.assertIn("| 01 | 1 | 7 | opus | ok, 1 unreadable ledger line |", lines)

    def test_a_closed_unit_stops_checking_and_a_resolved_flag_keeps_its_decision(self):
        self.scope("server/export/**")
        (self.worktree / "server/auth/middleware.go").write_text("// next unit's work\n")
        self.ok(self.tripwire("flag", "feature", "01", "Spec says 422, frozen test expects 409"))
        self.tripped(self.tripwire("check", "feature", "01"))
        self.ok(self.tripwire("resolve", "feature", "01", "Spec wins: test amended in unit 02"))
        self.assertFalse((self.units / "01-flag.md").exists())
        resolved = (self.units / "01-flag-resolved.md").read_text()
        self.assertIn("409", resolved)
        self.assertIn("Spec wins", resolved)
        self.assertIn("server/auth/middleware.go", self.tripped(self.tripwire("check", "feature", "01")))
        self.ok(self.tripwire("close", "feature", "01"))
        self.assertIn("closed", self.ok(self.tripwire("check", "feature", "01")))
        self.assertIn("| 01 | 0 | 0 |  | closed |", self.ok(self.tripwire("report", "feature")).splitlines())
        self.assertEqual(self.tripwire("resolve", "feature", "02", "nothing to resolve").returncode, 2)
        # Closing is not a waiver: a new flag trips, and a new scope reopens the unit.
        self.ok(self.tripwire("flag", "feature", "01", "fix-up found a second conflict"))
        self.assertIn("second conflict", self.tripped(self.tripwire("check", "feature", "01")))
        self.ok(self.tripwire("resolve", "feature", "01", "handled"))
        self.scope("server/export/**")
        self.assertIn("server/auth/middleware.go", self.tripped(self.tripwire("check", "feature", "01")))

    def test_hook_allows_a_clean_dispatch_and_keeps_the_ledger(self):
        self.scope("server/export/**")
        result = self.hook("[pharos task=feature unit=01] red", tool_uses=20)
        self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
        self.assertTrue((self.units / "01-spend.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
