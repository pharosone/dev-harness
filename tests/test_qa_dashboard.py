"""QA findings on the run dashboard and the hooks (v1.2 QA: M2, M3, M4, m1, m2, m3, m14)."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import test_adapters as adapters
import test_dashboard as dash

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
RUNTIME_HOOK = SCRIPTS / "runtime_hook.py"
row, cell, section = dash.row, dash.cell, dash.section


class DashboardHarness(unittest.TestCase):
    """The dashboard test harness (temp repo, initialized run) without its test methods."""
    setUp = dash.DashboardTest.setUp
    git = dash.DashboardTest.git
    script = dash.DashboardTest.script
    ok = dash.DashboardTest.ok
    event = dash.DashboardTest.event
    write_events = dash.DashboardTest.write_events
    write = dash.DashboardTest.write
    render = dash.DashboardTest.render
    start = dash.DashboardTest.start
    end = dash.DashboardTest.end


class DashboardQA(DashboardHarness):
    # --- M2: the file list ------------------------------------------------------------

    def test_m2_file_list_skips_snapshots_and_git_and_lists_reports_and_gates_first(self):
        src = self.run_dir / "compare/c1/private/0a1b2c3d/scratch/src"
        for i in range(600):
            self.write(f"compare/c1/private/0a1b2c3d/scratch/src/f{i:03}.txt", "x\n")
        for i in range(20):
            self.write(f"compare/c1/private/0a1b2c3d/scratch/src/.git/objects/o{i}", "x\n")
        self.assertTrue(src.is_dir())
        self.write("compare/c1/comparison.md", "summary\n")
        self.write("lanes/repo/.git/HEAD", "ref\n")
        for i in range(600):
            self.write(f"lanes/d{i:03}.log", "line\n")
        self.write("reports/U1-xreview.md", "review\n")
        self.write("reports/QA.md", "qa\n")
        self.write("gates/G4.md", "card\n")
        self.write_events()
        page = self.render()
        files = section(page, "reports")
        for href in ("reports/U1-xreview.md", "reports/QA.md", "gates/G4.md", "run.md",
                     "events.jsonl"):
            self.assertIn(f'href="{href}"', files, href)
        self.assertNotIn("/private/", files)
        self.assertNotIn(".git/", files)
        listable = [p for p in self.run_dir.rglob("*") if p.is_file()
                    and "private" not in p.relative_to(self.run_dir).parts
                    and ".git" not in p.relative_to(self.run_dir).parts
                    and p.name != "dashboard.html"]
        self.assertEqual(len(re.findall(r"<li>", files)), 500)
        match = re.search(r'<p id="files-not-listed"[^>]*>(.*?)</p>', files, re.DOTALL)
        self.assertIsNotNone(match, files[-500:])
        self.assertIn(f"{len(listable) - 500} more files not listed", match.group(1))

    def test_m2_a_short_file_list_says_nothing_was_left_out(self):
        self.write("reports/U1.md", "r\n")
        self.write_events()
        files = section(self.render(), "reports")
        self.assertIn('href="reports/U1.md"', files)
        self.assertNotIn('id="files-not-listed"', files)

    # --- M3: dispatch counts and the ledger -------------------------------------------

    def test_m3a_legacy_run_shows_unknown_dispatches_not_zero(self):
        self.write("units/U1-spend.jsonl", json.dumps({"tool_uses": 9}) + "\n")
        self.write("units/U2-tests.json", json.dumps({"files": {}}) + "\n")
        page = self.render()
        self.assertEqual(cell(row(page, "spend-U2"), "dispatches"), "unknown")
        self.assertEqual(cell(row(page, "spend-U1"), "dispatches"), "1")

    def test_m3b_ledger_lines_without_a_dispatch_event_are_merged(self):
        self.event("run.start", "2026-10-09T09:00:00Z", workflow="dev")
        self.start("d5", "2026-10-09T10:00:00Z", work="U3")
        self.end("d5", "2026-10-09T10:20:00Z", tool_calls=5)
        self.write_events()
        self.write("units/U1-spend.jsonl", json.dumps({"tool_uses": 9}) + "\n"
                   + json.dumps({"tool_uses": 4}) + "\n")
        # Recorded by the hook for d5 already: matched by dispatch id, not counted again.
        self.write("units/U3-spend.jsonl", json.dumps({"tool_uses": 5, "dispatch": "d5"}) + "\n")
        page = self.render()
        u1, u3 = row(page, "spend-U1"), row(page, "spend-U3")
        self.assertEqual((cell(u1, "dispatches"), cell(u1, "tool_calls")), ("2", "13"))
        self.assertEqual((cell(u3, "dispatches"), cell(u3, "tool_calls")), ("1", "5"))

    def test_m3b_an_end_without_a_start_is_counted(self):
        self.event("run.start", "2026-10-09T09:00:00Z", workflow="dev")
        self.event("dispatch.end", "2026-10-09T10:00:00Z", dispatch="orphan", status="done",
                   work="U4", usage={"tool_calls": 7, "input_tokens": None,
                                     "output_tokens": None, "duration_s": None})
        self.write_events()
        page = self.render()
        u4 = row(page, "spend-U4")
        self.assertEqual((cell(u4, "dispatches"), cell(u4, "tool_calls")), ("1", "7"))

    def test_m3b_spend_with_a_dispatch_is_counted_once_and_under_its_unit(self):
        self.event("run.start", "2026-10-09T09:00:00Z", workflow="dev")
        self.write_events()
        self.ok(self.script(dash.TRIPWIRE, "spend", "feature", "U6", "--tool-uses", "3",
                            "--dispatch", "dX"))
        self.ok(self.script(dash.TRIPWIRE, "spend", "feature", "U6", "--tool-uses", "2"))
        page = self.render()
        u6 = row(page, "spend-U6")
        self.assertEqual((cell(u6, "dispatches"), cell(u6, "tool_calls")), ("2", "5"))

    # --- M4: waiting for the user -----------------------------------------------------

    def test_m4_blocked_and_idle_lanes_are_shown_on_the_first_screen(self):
        for name in ("pending", "herdr", "lanes", "idle", "plain", "broken"):
            self.start(name, "2026-10-09T11:50:00Z", launcher="pane", pane=f"p-{name}")
        self.write_events()
        recent = "2026-10-09T11:58:00Z"
        self.write("lanes/pending.json", json.dumps({"prompt_pending": True, "last_activity": recent}))
        self.write("lanes/herdr.json", json.dumps({"agent_status": "blocked"}))
        self.write("lanes/lanes.json", json.dumps({"state": "blocked", "last_activity": recent}))
        self.write("lanes/idle.json", json.dumps({"agent_status": "idle", "last_activity": recent}))
        self.write("lanes/broken.json", "{not json")
        page = self.render()
        running = section(page, "running")
        for name in ("pending", "herdr", "lanes"):
            self.assertEqual(cell(row(running, f"dispatch-{name}"), "state"),
                             "blocked — waiting for the user", name)
        self.assertEqual(cell(row(running, "dispatch-idle"), "state"), "idle — waiting")
        self.assertEqual(cell(row(running, "dispatch-plain"), "state"), "running")
        self.assertEqual(cell(row(running, "dispatch-broken"), "state"), "running")
        first = page.index('<section id="running">')
        note = re.search(r'<p id="waiting-for-user"[^>]*>(.*?)</p>', page, re.DOTALL)
        self.assertIsNotNone(note)
        self.assertLess(note.start(), first)
        self.assertIn("3", note.group(1))
        for name in ("pending", "herdr", "lanes"):
            self.assertIn(name, note.group(1))

    def test_m4_no_note_when_nothing_waits(self):
        self.start("d1", "2026-10-09T11:55:00Z")
        self.write_events()
        self.assertNotIn('id="waiting-for-user"', self.render())

    # --- m1: a compare that died ------------------------------------------------------

    def test_m1_compare_without_result_goes_stale_after_the_window(self):
        self.event("compare.start", "2026-10-09T11:00:00Z", compare="dead", kind="design")
        self.event("compare.start", "2026-10-09T11:00:00Z", compare="busy", kind="design")
        self.event("compare.launch", "2026-10-09T11:58:00Z", compare="busy", kind="design",
                   slot="a", dispatch=None)
        self.event("compare.start", "2026-10-09T10:00:00Z", compare="waiting", kind="design")
        self.event("compare.launch", "2026-10-09T10:01:00Z", compare="waiting", kind="design",
                   slot="a", dispatch="cand-a")
        self.start("cand-a", "2026-10-09T10:01:00Z", work="waiting-a", launcher="headless")
        self.event("compare.start", "2026-10-09T08:00:00Z", compare="over", kind="design")
        self.event("compare.result", "2026-10-09T08:40:00Z", compare="over", confidence="high")
        self.write_events()
        self.write("lanes/cand-a.json", json.dumps({"last_activity": "2026-10-09T11:59:00Z"}))
        compare = section(self.render(), "compare")
        self.assertEqual(cell(row(compare, "compare-dead"), "state"), "stale")
        self.assertEqual(cell(row(compare, "compare-busy"), "state"), "running")
        self.assertEqual(cell(row(compare, "compare-waiting"), "state"), "running")
        self.assertEqual(cell(row(compare, "compare-over"), "state"), "done")

    # --- m14: frozen tests changed ----------------------------------------------------

    def test_m14_frozen_tests_changed_note_is_shown_while_not_tripped(self):
        red = self.root / "red.txt"
        red.write_text("FAILED (failures=1)\n")
        self.ok(self.script(dash.TRIPWIRE, "scope", "feature", "U1", "--base", "HEAD", "tests/**"))
        self.ok(self.script(dash.TASK_STATE, "freeze", "feature", "U1", "--red-log", "red.txt",
                            "tests/test_unit.py"))
        (self.root / "tests/test_unit.py").write_text("# weakened\n")
        (self.run_dir / "units/U1-trips.json").write_text(json.dumps({"verify_failures": 1}))
        self.write_events()
        page = self.render()
        self.assertEqual(cell(row(page, "spend-U1"), "status"),
                         "ok — frozen tests changed (1 dispatch)")
        trip = row(section(page, "tripwires"), "trip-U1")
        self.assertIn("Frozen tests changed", trip)


class FailedAgentHookQA(DashboardHarness):
    """m2: a tagged Agent call that fails ends its dispatch as failed."""

    def hook(self, event_name, **extra):
        payload = {"hook_event_name": event_name, "tool_name": "Agent", "cwd": str(self.root),
                   "session_id": "s1", "tool_use_id": "toolu_fail",
                   "tool_input": {"description": "[pharos task=feature unit=U1] red",
                                  "prompt": "Write the tests.", "subagent_type": "implementer"},
                   **extra}
        result = subprocess.run([sys.executable, str(dash.TRIPWIRE), "hook"], cwd=self.root,
                                env=self.env, input=json.dumps(payload), capture_output=True,
                                text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def test_m2_post_tool_use_failure_ends_the_dispatch_failed(self):
        self.hook("PreToolUse")
        self.hook("PostToolUseFailure", error="Agent crashed", is_interrupt=False,
                  duration_ms=4187)
        self.hook("PostToolUseFailure", error="Agent crashed", is_interrupt=False,
                  duration_ms=4187)
        events = [json.loads(line) for line in
                  (self.run_dir / "events.jsonl").read_text().splitlines()]
        ends = [e for e in events if e["type"] == "dispatch.end"]
        self.assertEqual(len(ends), 1)
        self.assertEqual((ends[0]["id"], ends[0]["dispatch"], ends[0]["status"]),
                         ("toolu_fail:end", "toolu_fail", "failed"))
        self.assertIsNone(ends[0]["usage"]["tool_calls"])
        self.assertEqual(ends[0]["usage"]["duration_s"], 4.187)
        page = (self.run_dir / "dashboard.html").read_text()
        self.assertNotIn("dispatch-toolu_fail", section(page, "running"))
        self.assertEqual(cell(row(section(page, "finished"), "dispatch-toolu_fail"), "state"),
                         "failed")
        self.assertEqual(cell(row(page, "spend-U1"), "dispatches"), "1")


class FailureHookRenderQA(adapters.RenderCase):
    def setUp(self):
        super().setUp()
        adapters.shutil.copytree(adapters.CORE / "scripts", self.root / "harness/core/scripts",
                                 ignore=adapters.shutil.ignore_patterns("__pycache__"))
        self.script = self.root / "harness/core/scripts/render_adapters.py"
        self.write("harness/core/skills/dev-flow/SKILL.md", adapters.skill("dev-flow"))
        self.write("harness/core/roles/qa-verifier.md", "# QA\nRun independent tests.\n")
        self.write("harness/core/roles/manifest.json", json.dumps({"qa-verifier": {
            "description": "Independent QA", "profile": "critical-review"}}))
        self.write("harness/core/adapters/models.json", json.dumps({
            "claude": {"critical-review": {"model": "fable", "effort": "xhigh"}},
            "codex": {"critical-review": {"model": "gpt-6-astra", "effort": "xhigh"}}}))

    def test_m2_settings_register_post_tool_use_failure_for_agent(self):
        self.rendered()
        hooks = json.loads(self.read(".claude/settings.json"))["hooks"]
        post = [h["command"] for g in hooks["PostToolUse"] if g.get("matcher") == "Agent"
                for h in g["hooks"] if "tripwire.py" in h["command"]]
        failure = [h["command"] for g in hooks.get("PostToolUseFailure", [])
                   if g.get("matcher") == "Agent" for h in g["hooks"]]
        self.assertEqual(failure, post)
        self.assertEqual(len(failure), 1)
        self.rendered()
        hooks = json.loads(self.read(".claude/settings.json"))["hooks"]
        self.assertEqual(len([h for g in hooks["PostToolUseFailure"] for h in g["hooks"]]), 1)
        self.rendered("--check")
        self.assertNotIn("PostToolUseFailure", json.loads(self.read(".codex/hooks.json"))["hooks"])


class StopHookWithoutBaseQA(unittest.TestCase):
    """m3: a candidate snapshot (no remote, no main) is not blocked by the drift hook."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "snap"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        for args in (("init", "-q", "-b", "feature"), ("config", "user.name", "T"),
                     ("config", "user.email", "t@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.run(["git", *args], cwd=self.root, env=self.env, check=True)
        (self.root / "a.txt").write_text("a\n")
        subprocess.run(["git", "add", "."], cwd=self.root, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "c"], cwd=self.root, env=self.env, check=True)
        subprocess.run(["git", "checkout", "-q", "--detach"], cwd=self.root, env=self.env,
                       check=True)

    def hook(self, runtime, env):
        return subprocess.run(
            [sys.executable, str(RUNTIME_HOOK), "--runtime", runtime], cwd=self.root,
            env=env, capture_output=True, text=True, timeout=30,
            input=json.dumps({"session_id": "s", "cwd": str(self.root),
                              "hook_event_name": "Stop"}))

    def test_m3_no_base_exits_zero_with_a_one_line_note(self):
        # Only a compare candidate's snapshot (lanes sets HARNESS_CANDIDATE_SNAPSHOT=1) is
        # exempt; any other checkout without a base still fails (R6 finding 1).
        snapshot = {**self.env, "HARNESS_CANDIDATE_SNAPSHOT": "1"}
        plain = {k: v for k, v in self.env.items() if k != "HARNESS_CANDIDATE_SNAPSHOT"}
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                result = self.hook(runtime, snapshot)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})
                lines = result.stderr.strip().splitlines()
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertIn("not checked", lines[0])
                self.assertNotIn("hook failed", lines[0])
                result = self.hook(runtime, plain)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("not checked", result.stderr)


if __name__ == "__main__":
    unittest.main()
