"""MF7: the round-6 cross-review findings.

1. (Major) The Stop hook's "no base" exemption holds only for a compare candidate's snapshot:
   lanes sets HARNESS_CANDIDATE_SNAPSHOT=1 in the environment of every candidate it starts
   (headless and herdr pane), and runtime_hook skips with a one-line note only when that
   variable is set and no base resolves. An ordinary checkout without main/origin-main fails
   (exit 2) as before; with a base the variable changes nothing.
2. (Major) A blocked or idle transition that status persists in `lanes/<dispatch>.json`
   regenerates the run's dashboard.
3. (Minor) When the supervisor copies Codex's final message over the report it records
   `report_source: "final_message"` in the state file and the dispatch.end; compare's seal
   reads it and labels the memo "from the final message".

No real claude, codex or herdr is called: the fakes of the lanes and compare tests come first on
PATH.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dashboard as dash  # noqa: E402
import test_lanes_capability as capability  # noqa: E402
import test_lanes_lastmsg as lastmsg  # noqa: E402
import test_m1_review_fixes as fixes  # noqa: E402
import test_qa_compare as qa_compare  # noqa: E402

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
import compare  # noqa: E402
import dashboard  # noqa: E402

RUNTIME_HOOK = CORE / "scripts" / "runtime_hook.py"
VARIABLE = "HARNESS_CANDIDATE_SNAPSHOT"
RECORDED = capability.RECORDED


# --- 1. the drift exemption is for candidate snapshots only ---------------------------------

class HookExemptionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "checkout"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k not in ("HARNESS_PROJECT", VARIABLE)}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, env=self.env, check=True,
                       capture_output=True)

    def repository(self, branch):
        for args in (("init", "-q", "-b", branch), ("config", "user.name", "T"),
                     ("config", "user.email", "t@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            self.git(*args)
        (self.root / "a.txt").write_text("a\n")
        self.git("add", ".")
        self.git("commit", "-qm", "c")

    def invalid_overlay(self):
        overlay = self.root / "harness" / "project"
        overlay.mkdir(parents=True)
        (overlay / "drift.json").write_text("{not json")

    def hook(self, runtime, **env):
        return subprocess.run(
            [sys.executable, str(RUNTIME_HOOK), "--runtime", runtime], cwd=self.root,
            env={**self.env, **env}, capture_output=True, text=True, timeout=30,
            input=json.dumps({"session_id": "s", "cwd": str(self.root),
                              "hook_event_name": "Stop"}))

    def test_a_feature_checkout_without_main_and_an_invalid_overlay_is_blocked(self):
        self.repository("feature")
        self.invalid_overlay()
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                result = self.hook(runtime)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertNotIn("not checked", result.stderr)

    def test_a_feature_checkout_without_main_is_not_exempt(self):
        self.repository("feature")
        result = self.hook("claude")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("hook failed", result.stderr)

    def test_a_candidate_snapshot_without_a_base_exits_zero_with_one_note(self):
        self.repository("feature")
        self.git("checkout", "-q", "--detach")
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                result = self.hook(runtime, **{VARIABLE: "1"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(json.loads(result.stdout), {})
                lines = result.stderr.strip().splitlines()
                self.assertEqual(len(lines), 1, result.stderr)
                self.assertIn("not checked", lines[0])

    def test_the_variable_does_not_skip_the_check_when_a_base_resolves(self):
        self.repository("main")
        self.invalid_overlay()
        result = self.hook("claude", **{VARIABLE: "1"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("drift.json", result.stderr)
        self.assertNotIn("not checked", result.stderr)

    def test_a_variable_other_than_1_is_not_the_exemption(self):
        self.repository("feature")
        result = self.hook("claude", **{VARIABLE: "0"})
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)


class CandidateEnvironmentTest(unittest.TestCase):
    """lanes puts HARNESS_CANDIDATE_SNAPSHOT=1 into every candidate's environment."""
    setUp_capability = capability.CapabilityTest.setUp
    setUp_fixes = capability.CapabilityTest.setUp_fixes
    setUp_launch = capability.CapabilityTest.setUp_launch
    begin = capability.CapabilityTest.begin
    cleanup = capability.CapabilityTest.cleanup
    git = capability.CapabilityTest.git
    herdr_env = capability.CapabilityTest.herdr_env
    lanes = capability.CapabilityTest.lanes
    wait = capability.CapabilityTest.wait
    calls = capability.CapabilityTest.calls
    herdr_calls = capability.CapabilityTest.herdr_calls
    events = capability.CapabilityTest.events
    report_path = capability.CapabilityTest.report_path
    of = capability.CapabilityTest.of

    def setUp(self):
        self.setUp_capability()
        for name in ("claude", "codex"):
            fake = self.bin / name
            source = fake.read_text()
            self.assertIn(RECORDED, source)
            fake.write_text(source.replace(
                RECORDED, RECORDED + f', "snapshot_env": os.environ.get("{VARIABLE}")', 1))
        self.env.pop(VARIABLE, None)

    def test_headless_candidates_of_both_runtimes_get_the_variable(self):
        for runtime in ("claude", "codex"):
            self.scratch = self.temp / runtime / "scratch"
            out = self.begin("--launcher", "headless", runtime=runtime, work=runtime)
            self.wait(out["dispatch"])
        calls = self.calls()
        self.assertEqual(sorted(c["argv"][0] for c in calls), ["claude", "codex"])
        for call in calls:
            self.assertEqual(call["snapshot_env"], "1", call["argv"][0])

    def test_a_headless_worker_that_is_not_a_candidate_does_not_get_it(self):
        for runtime in ("claude", "codex"):
            out = self.begin("--launcher", "headless", runtime=runtime, work=runtime,
                             capability=False)
            self.wait(out["dispatch"])
        for call in self.calls():
            self.assertIsNone(call["snapshot_env"], call["argv"][0])

    def envs(self, call):
        return [call[i + 1] for i, arg in enumerate(call) if arg == "--env"]

    def test_a_pane_candidate_gets_it_in_the_pane_environment(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                since = len(self.herdr_calls())
                self.scratch = self.temp / f"pane-{runtime}" / "scratch"
                self.begin("--launcher", "pane", runtime=runtime, work=f"pane-{runtime}",
                           env=self.herdr_env())
                made = self.of(["pane", "split"], since) + self.of(["tab", "create"], since)
                self.assertEqual(len(made), 1, made)
                self.assertIn(f"{VARIABLE}=1", self.envs(made[0]))

    def test_a_pane_worker_that_is_not_a_candidate_does_not_get_it(self):
        self.begin("--launcher", "pane", env=self.herdr_env(), capability=False)
        made = self.of(["pane", "split"]) + self.of(["tab", "create"])
        self.assertEqual(len(made), 1, made)
        self.assertFalse([e for e in self.envs(made[0]) if e.startswith(VARIABLE)])


# --- 2. a persisted blocked or idle transition regenerates the dashboard --------------------

class PersistedTransitionDashboardTest(unittest.TestCase):
    setUp = fixes.LanesReviewFixTest.setUp
    setUp_launch = fixes.LanesReviewFixTest.setUp_launch
    begin = fixes.LanesReviewFixTest.begin
    cleanup = fixes.LanesReviewFixTest.cleanup
    git = fixes.LanesReviewFixTest.git
    herdr_env = fixes.LanesReviewFixTest.herdr_env
    lanes = fixes.LanesReviewFixTest.lanes
    status = fixes.LanesReviewFixTest.status
    events = fixes.LanesReviewFixTest.events
    herdr_calls = fixes.LanesReviewFixTest.herdr_calls
    set_agent = fixes.LanesReviewFixTest.set_agent
    report_path = fixes.LanesReviewFixTest.report_path
    page = fixes.LanesReviewFixTest.page
    state_file = fixes.LanesReviewFixTest.state_file

    def running_state(self, dispatch):
        return dash.cell(dash.row(dash.section(self.page(), "running"), f"dispatch-{dispatch}"),
                         "state")

    def test_a_blocked_pane_seen_by_status_shows_as_blocked_on_the_dashboard(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        self.assertEqual(self.running_state(out["dispatch"]), "running")
        self.set_agent(out["pane"], "blocked")
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "blocked")
        self.assertEqual(self.state_file(out["dispatch"])["state"], "blocked")
        self.assertEqual(self.running_state(out["dispatch"]), dashboard.BLOCKED)
        self.assertIn('id="waiting-for-user"', self.page())

    def test_an_idle_pane_seen_by_status_shows_as_idle_on_the_dashboard(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        self.set_agent(out["pane"], "working")
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "running")
        self.set_agent(out["pane"], "idle")
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "idle")
        self.assertEqual(self.running_state(out["dispatch"]), dashboard.IDLE)

    def test_back_to_running_after_the_answer_is_shown_too(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        self.set_agent(out["pane"], "blocked")
        self.status(out["dispatch"], env=self.herdr_env())
        self.assertEqual(self.running_state(out["dispatch"]), dashboard.BLOCKED)
        self.set_agent(out["pane"], "working")
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "running")
        self.assertEqual(self.running_state(out["dispatch"]), "running")
        self.assertNotIn('id="waiting-for-user"', self.page())


# --- 3. the final-message provenance survives lanes' copy -----------------------------------

class CopiedFinalMessageSourceTest(unittest.TestCase):
    setUp = lastmsg.LastMessageTest.setUp
    setUp_capability = lastmsg.LastMessageTest.setUp_capability
    setUp_fixes = lastmsg.LastMessageTest.setUp_fixes
    setUp_launch = lastmsg.LastMessageTest.setUp_launch
    begin = lastmsg.LastMessageTest.begin
    cleanup = lastmsg.LastMessageTest.cleanup
    git = lastmsg.LastMessageTest.git
    herdr_env = lastmsg.LastMessageTest.herdr_env
    lanes = lastmsg.LastMessageTest.lanes
    wait = lastmsg.LastMessageTest.wait
    calls = lastmsg.LastMessageTest.calls
    herdr_calls = lastmsg.LastMessageTest.herdr_calls
    events = lastmsg.LastMessageTest.events
    report_path = lastmsg.LastMessageTest.report_path
    of = lastmsg.LastMessageTest.of
    codex = lastmsg.LastMessageTest.codex

    def state_file(self, dispatch):
        return json.loads((self.run_dir / "lanes" / f"{dispatch}.json").read_text())

    def end(self, dispatch):
        [end] = [e for e in self.events(dispatch) if e["type"] == "dispatch.end"]
        return end

    def test_a_copied_final_message_is_recorded_as_the_reports_source(self):
        self.codex()
        [start] = [e for e in self.events() if e["type"] == "dispatch.start"]
        dispatch = start["dispatch"]
        self.assertEqual(self.report.read_text(), "fake codex report\n")
        self.assertEqual(self.state_file(dispatch)["report_source"], "final_message")
        self.assertEqual(self.end(dispatch)["report_source"], "final_message")

    def test_a_report_the_candidate_wrote_records_no_final_message_source(self):
        self.codex(env={"FAKE_OWN_REPORT": str(self.report)})
        [start] = [e for e in self.events() if e["type"] == "dispatch.start"]
        dispatch = start["dispatch"]
        self.assertNotEqual(self.state_file(dispatch).get("report_source"), "final_message")
        self.assertNotEqual(self.end(dispatch).get("report_source"), "final_message")

    def test_a_failed_run_records_no_final_message_source(self):
        self.codex(env={"FAKE_EXIT": "3"}, expect_state="failed")
        [start] = [e for e in self.events() if e["type"] == "dispatch.start"]
        self.assertNotEqual(self.end(start["dispatch"]).get("report_source"), "final_message")


class SealReadsTheReportSourceTest(qa_compare.FolderCase):
    def candidates_with(self, ends):
        obj = qa_compare.stub(compare.Compare, self.folder)
        reports = {}
        for entry in qa_compare.CANDIDATES:
            report = self.folder / entry["label"] / "report.md"
            report.parent.mkdir(parents=True)
            report.write_text(f"Memo of {entry['label']}.\n", encoding="utf-8")
            reports[f"candidate-{entry['label']}"] = report
        obj.state = lambda: {"seal": {}, "claim": {}, "launch": {}, "pass": {}, "ends": ends}
        obj.candidate_brief = lambda: self.folder / "brief.md"
        obj.run.emit = lambda kind, fields, event_id: obj.run.emitted.append(
            (kind, fields, event_id)) or False  # another process holds every claim
        obj.launch_of = lambda slot: {"slot": slot, "dispatch": f"d-{slot}",
                                      "report": str(reports[slot])}
        obj.finish = lambda launch, timeout: "done"
        obj.candidates()
        return {f["label"]: f for f in self.emitted(obj, "compare.seal")}

    def test_a_report_lanes_copied_from_the_final_message_is_sealed_as_such(self):
        seals = self.candidates_with({
            "d-candidate-codex": {"type": "dispatch.end", "dispatch": "d-candidate-codex",
                                  "status": "done", "report_source": "final_message"},
            "d-candidate-claude": {"type": "dispatch.end", "dispatch": "d-candidate-claude",
                                   "status": "done"}})
        self.assertEqual(seals["codex"]["source"], "final message")
        self.assertEqual(seals["claude"]["source"], "report")
        self.assertEqual(compare.sealed_from(seals["codex"]), " (from the final message)")
        self.assertEqual(compare.sealed_from(seals["claude"]), "")

    def test_without_an_end_the_report_is_the_source(self):
        seals = self.candidates_with({})
        self.assertEqual({s["source"] for s in seals.values()}, {"report"})


class CompareFinalMessageEndToEndTest(unittest.TestCase):
    """The real chain: a Codex candidate that writes no report, lanes' copy, compare's seal."""
    setUp = qa_compare.CompareEndToEndTest.setUp
    unlock = qa_compare.CompareEndToEndTest.unlock
    design = qa_compare.CompareEndToEndTest.design
    events = qa_compare.CompareEndToEndTest.events

    def test_a_codex_memo_lanes_copied_from_the_final_message_is_labelled(self):
        out, _ = self.design(FAKE_MEMO_claude="Plan alpha.\n", FAKE_MEMO_codex="Plan beta.\n")
        seals = {e["label"]: e for e in self.events() if e["type"] == "compare.seal"}
        self.assertEqual(seals["codex"]["status"], "done")
        self.assertEqual(seals["codex"]["source"], "final message")
        comparison = (self.run_dir / "compare" / out["compare"] / "comparison.md").read_text()
        [row] = [line for line in comparison.splitlines() if "`candidate-codex.md`" in line]
        self.assertIn("(from the final message)", row)


if __name__ == "__main__":
    unittest.main()
