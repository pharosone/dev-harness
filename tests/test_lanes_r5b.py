"""QA add-ons to the R5 fixes of lanes (unit MF3, frozen as MF3b):

(a) capability_profile(..., write_roots=True) names the real write roots of a candidate per
    runtime, while the shared keys stay equal on both vendors.
(b) `lanes.py start` takes the orchestrator from --orchestrator, else $HARNESS_ORCHESTRATOR,
    else assumes claude, and prints the orchestrator it did not get from the flag.
(c) A pane seen working that goes idle without a fresh report is `idle` ("waiting: ..."), not
    `running`.
(d) The stale window is the exported constant STALE_MINUTES = 15, shared with the dashboard.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch (herdr behind
the wrapper of test_m1_review_fixes) come first on PATH and record their argv.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_capability as capability  # noqa: E402
import test_lanes_launch as launch  # noqa: E402

lanes = launch.lanes
CANDIDATE = capability.CANDIDATE
ROOTS = {
    "claude": ["scratch (Edit(//<scratch>/**) for the file tools)",
               "snapshot and scratch for Bash (sandbox: the working directory and --add-dir)",
               "per-user temp directory (Bash)"],
    "codex": ["snapshot (workspace-write -C <scratch>/src)", "scratch (--add-dir)",
              "temp directory (workspace-write)"],
}


class R5AddOnTest(unittest.TestCase):
    setUp_capability = capability.CapabilityTest.setUp
    setUp_fixes = capability.CapabilityTest.setUp_fixes
    setUp_launch = capability.CapabilityTest.setUp_launch
    begin = capability.CapabilityTest.begin
    cleanup = launch.LaunchTest.cleanup
    git = launch.LaunchTest.git
    herdr_env = launch.LaunchTest.herdr_env
    lanes = launch.LaunchTest.lanes
    wait = launch.LaunchTest.wait
    status = launch.LaunchTest.status
    calls = launch.LaunchTest.calls
    herdr_calls = launch.LaunchTest.herdr_calls
    events = launch.LaunchTest.events
    state_file = launch.LaunchTest.state_file
    set_agent = launch.LaunchTest.set_agent
    report_path = capability.CapabilityTest.report_path
    of = capability.CapabilityTest.of

    def setUp(self):
        self.setUp_capability()
        self.env.pop("HARNESS_ORCHESTRATOR", None)

    # --- (a) write roots per runtime ------------------------------------------------------

    def test_write_roots_name_each_runtimes_real_roots_and_the_shared_keys_stay_equal(self):
        for runtime in ("claude", "codex"):
            for launcher in ("headless", "pane"):
                profile = lanes.capability_profile(runtime, launcher, "candidate",
                                                   write_roots=True)
                self.assertEqual({k: v for k, v in profile.items() if k != "write_roots"},
                                 CANDIDATE, (runtime, launcher))
                self.assertEqual(profile["write_roots"], ROOTS[runtime], (runtime, launcher))
                self.assertEqual(lanes.capability_profile(runtime, launcher, "candidate"),
                                 CANDIDATE, "write_roots is opt-in")

    def test_write_roots_of_other_workers_restate_their_writes(self):
        profile = lanes.capability_profile
        self.assertEqual(profile("codex", "headless", None, write_roots=True)["write_roots"], [])
        self.assertEqual(profile("claude", "headless", None, role="implementer",
                                 write_roots=True)["write_roots"], ["checkout"])
        self.assertEqual(profile("claude", "pane", None, write_roots=True)["write_roots"],
                         "as-approved")

    # --- (b) the orchestrator of start ----------------------------------------------------

    def start(self, *extra, runtime="claude", expect=0, **env):
        result = self.lanes(
            "start", "--task", "t1", "--work", "U9", "--role", "reviewer", "--runtime", runtime,
            "--model", "model-x", "--effort", "high", "--brief", str(self.brief),
            "--report", str(self.report_path()), "--checkout", str(self.repo), *extra, **env)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        if expect == 2:
            return result
        out = json.loads(result.stdout)
        if out.get("dispatch"):
            self.started.append(out["dispatch"])
        return out

    def test_start_takes_the_orchestrator_from_the_environment_and_says_so(self):
        out = self.start(HARNESS_ORCHESTRATOR="codex")
        self.assertEqual(out["launcher"], "headless")  # a claude worker under codex
        self.assertEqual(out["orchestrator"], "codex")
        self.wait(out["dispatch"])

    def test_start_without_flag_or_environment_assumes_claude_and_prints_it(self):
        out = self.start(expect=lanes.EXIT_SUBAGENT)
        self.assertEqual((out["launcher"], out["orchestrator"]), ("subagent", "claude"))

    def test_the_flag_wins_over_the_environment_and_is_not_echoed(self):
        out = self.start("--orchestrator", "claude", expect=lanes.EXIT_SUBAGENT,
                         HARNESS_ORCHESTRATOR="codex")
        self.assertEqual(out["launcher"], "subagent")
        self.assertNotIn("orchestrator", out)

    def test_an_unknown_orchestrator_in_the_environment_is_refused(self):
        result = self.start(expect=2, HARNESS_ORCHESTRATOR="gemini")
        self.assertIn("HARNESS_ORCHESTRATOR", result.stderr)
        self.assertEqual(self.events(), [])

    # --- (c) an idle pane without a report ------------------------------------------------

    def test_a_pane_that_ends_its_turn_without_a_report_is_idle_not_running(self):
        out = self.start("--launcher", "pane", **self.herdr_env())
        env = self.herdr_env()
        self.assertEqual(self.status(out["dispatch"], env=env)["state"], "running")  # working
        self.set_agent(out["pane"], "idle")
        idle = self.status(out["dispatch"], env=env)
        self.assertEqual(idle["state"], "idle")
        self.assertEqual(idle["reason"], "waiting: the agent ended its turn without a report")
        self.assertEqual([e["type"] for e in self.events(out["dispatch"])], ["dispatch.start"])
        self.set_agent(out["pane"], "working")  # the user answered
        self.assertEqual(self.status(out["dispatch"], env=env)["state"], "running")
        self.report_path().write_text("pane report\n")
        self.set_agent(out["pane"], "idle")
        self.assertEqual(self.wait(out["dispatch"], env=env)["state"], "done")

    # --- (d) one stale window -------------------------------------------------------------

    def test_the_stale_window_is_the_exported_constant_of_15_minutes(self):
        self.assertEqual(lanes.STALE_MINUTES, 15)
        self.assertIsInstance(lanes.STALE_MINUTES, int)
        help_text = self.lanes("status", "--help").stdout
        self.assertIn("default 15", " ".join(help_text.split()))
        self.assertIn("--stale-minutes (default 15)", " ".join(lanes.__doc__.split()))


if __name__ == "__main__":
    unittest.main()
