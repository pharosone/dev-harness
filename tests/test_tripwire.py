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
sys.path.insert(0, str(SCRIPTS))
sys.dont_write_bytecode = True
from task_state import read_events  # noqa: E402


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


    # Dispatch events -----------------------------------------------------------------------

    def agent_hook(self, hook_event, description="[pharos task=feature unit=01] green", *,
                   tool_use_id="toolu_01", status=None, background=False, model=None,
                   prompt="Implement the unit.", response=None):
        tool_input = {"description": description, "prompt": prompt,
                      "subagent_type": "implementer", "run_in_background": background}
        if model:
            tool_input["model"] = model
        payload = {"hook_event_name": hook_event, "tool_name": "Agent", "cwd": str(self.root),
                   "session_id": "session-1", "tool_input": tool_input}
        if tool_use_id:
            payload["tool_use_id"] = tool_use_id
        if hook_event == "PostToolUse":
            payload["tool_response"] = response or (
                {"status": "async_launched", "agentId": "a1b2", "isAsync": True}
                if status == "async_launched" else
                {"status": status or "completed", "totalToolUseCount": 12,
                 "totalDurationMs": 4200, "totalTokens": 5100,
                 "resolvedModel": "claude-opus-5-5",
                 "usage": {"input_tokens": 10, "output_tokens": 900,
                           "cache_read_input_tokens": 4190}})
        result = self.tripwire("hook", stdin=json.dumps(payload))
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def events(self, task="feature"):
        events, unreadable = read_events(self.root / ".harness/tasks" / task)
        self.assertEqual(unreadable, 0)
        return events

    def of_type(self, kind, task="feature"):
        return [e for e in self.events(task) if e["type"] == kind]

    def test_a_tagged_dispatch_writes_start_and_end_once_when_hooks_fire_twice(self):
        for _ in range(2):
            self.assertEqual(self.agent_hook("PreToolUse", model="sonnet").stdout, "")
        for _ in range(2):
            self.assertEqual(self.agent_hook("PostToolUse").stdout, "")
        [start] = self.of_type("dispatch.start")
        self.assertEqual(start["id"], "toolu_01:start")
        self.assertEqual({k: v for k, v in start.items() if k not in ("v", "id", "at", "type")}, {
            "dispatch": "toolu_01", "role": "implementer", "runtime": "claude", "model": "sonnet",
            "effort": None, "launcher": "subagent", "work": "01", "report": None, "pane": None,
            "background": False})
        [end] = self.of_type("dispatch.end")
        self.assertEqual(end["id"], "toolu_01:end")
        self.assertEqual(end["dispatch"], "toolu_01")
        self.assertEqual(end["status"], "done")
        # The response's `usage` is the final request only: dispatch tokens are unknown, not 10/900.
        self.assertEqual(end["usage"], {"tool_calls": 12, "input_tokens": None,
                                        "output_tokens": None, "duration_s": 4.2})
        self.assertEqual([e["type"] for e in self.events()], ["dispatch.start", "dispatch.end"])
        # A repeated completion delivery does not count the dispatch twice.
        ledger = (self.units / "01-spend.jsonl").read_text().splitlines()
        self.assertEqual(len(ledger), 1)
        self.assertEqual(json.loads(ledger[0])["tool_uses"], 12)

    def test_a_dispatch_without_a_model_or_usage_records_unknowns(self):
        self.agent_hook("PreToolUse")
        self.agent_hook("PostToolUse", response={"status": "completed"})
        [start] = self.of_type("dispatch.start")
        self.assertIsNone(start["model"])
        [end] = self.of_type("dispatch.end")
        self.assertEqual(end["usage"], {"tool_calls": None, "input_tokens": None,
                                        "output_tokens": None, "duration_s": None})

    def test_a_background_launch_writes_the_start_once_and_no_end_or_spend(self):
        self.agent_hook("PreToolUse", background=True)
        for _ in range(2):
            self.assertEqual(self.agent_hook("PostToolUse", status="async_launched",
                                             background=True).stdout, "")
        [start] = self.of_type("dispatch.start")
        self.assertEqual((start["id"], start["background"]), ("toolu_01:start", True))
        self.assertEqual(self.of_type("dispatch.end"), [])
        self.assertFalse((self.units / "01-spend.jsonl").exists())
        # Without the PreToolUse hook (older settings) the launch itself writes the start.
        self.agent_hook("PostToolUse", status="async_launched", background=True, tool_use_id="toolu_02")
        self.assertEqual([e["id"] for e in self.of_type("dispatch.start")],
                         ["toolu_01:start", "toolu_02:start"])

    def test_untagged_dispatches_write_no_events(self):
        for hook_event, status in (("PreToolUse", None), ("PostToolUse", "async_launched"),
                                   ("PostToolUse", "completed")):
            result = self.agent_hook(hook_event, "explore the repo", status=status)
            self.assertEqual(result.stdout, "")
        self.assertFalse((self.root / ".harness/tasks/feature/events.jsonl").exists())

    def test_a_dispatch_for_a_missing_run_writes_nothing_and_exits_zero(self):
        for hook_event, status in (("PreToolUse", None), ("PostToolUse", "async_launched"),
                                   ("PostToolUse", "completed")):
            result = self.agent_hook(hook_event, "[pharos task=missing unit=01] red", status=status)
            self.assertEqual(result.stdout, "")
        self.assertFalse((self.root / ".harness/tasks/missing").exists())
        self.assertFalse((self.root / ".harness/tasks/feature/events.jsonl").exists())
        broken = self.tripwire("hook", stdin="not json")
        self.assertEqual((broken.returncode, broken.stdout), (0, ""))

    def test_without_a_tool_use_id_the_dispatch_is_a_stable_hash(self):
        self.agent_hook("PreToolUse", tool_use_id=None)
        self.agent_hook("PostToolUse", tool_use_id=None)
        self.agent_hook("PreToolUse", tool_use_id=None, prompt="Fix the finding.")
        starts = self.of_type("dispatch.start")
        [end] = self.of_type("dispatch.end")
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0]["dispatch"], end["dispatch"])
        self.assertNotEqual(starts[0]["dispatch"], starts[1]["dispatch"])
        self.assertEqual(end["id"], f"{end['dispatch']}:end")

    def test_spend_with_a_dispatch_writes_the_end_event_once(self):
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "5"))
        self.assertEqual(self.events(), [])
        for _ in range(2):
            self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "9", "--model", "gpt-6",
                                  "--dispatch", "agent-7"))
        [end] = self.events()
        self.assertEqual((end["type"], end["id"], end["dispatch"], end["status"]),
                         ("dispatch.end", "agent-7:end", "agent-7", "done"))
        self.assertEqual(end["usage"], {"tool_calls": 9, "input_tokens": None,
                                        "output_tokens": None, "duration_s": None})
        ledger = [json.loads(line) for line in (self.units / "01-spend.jsonl").read_text().splitlines()]
        self.assertEqual([e["tool_uses"] for e in ledger], [5, 9])
        self.assertEqual(self.tripwire("spend", "missing", "01", "--tool-uses", "1",
                                       "--dispatch", "x").returncode, 2)

if __name__ == "__main__":
    unittest.main()
