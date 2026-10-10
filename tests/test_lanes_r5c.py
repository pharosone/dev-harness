"""Second QA add-ons to the R5 fixes of lanes (unit MF3, frozen as MF3c):

(e) `lanes.py snapshot-cleanup --task T [--dispatch D ...]` (and snapshot_cleanup(run,
    dispatches)) removes only the named dispatches' snapshots; without a filter it is unchanged.
(f) status_of keeps the pane state it observed in `lanes/<dispatch>.json`: agent_status (herdr's),
    state (lanes') and observed_at, so the dashboard can show a pane herdr blocked later.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch (herdr behind
the wrapper of test_m1_review_fixes) come first on PATH and record their argv.
"""
from datetime import datetime
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_capability as capability  # noqa: E402
import test_lanes_launch as launch  # noqa: E402

lanes = launch.lanes


class R5SecondAddOnTest(unittest.TestCase):
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

    # --- (e) a dispatch filter for snapshot-cleanup ----------------------------------------

    def candidates(self, *works):
        outs = []
        for work in works:
            self.scratch = self.temp / "compare" / work / "scratch"
            out = self.begin("--launcher", "headless", work=work)
            self.wait(out["dispatch"])
            outs.append(out)
        return outs

    def cleanup_rows(self, *extra):
        result = self.lanes("snapshot-cleanup", "--task", "t1", *extra)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return {row["dispatch"]: row for row in json.loads(result.stdout)}

    def test_the_filter_removes_only_the_named_dispatches_snapshots(self):
        a, b, c = self.candidates("A", "B", "C")
        rows = self.cleanup_rows("--dispatch", a["dispatch"], "--dispatch", c["dispatch"])
        self.assertEqual(set(rows), {a["dispatch"], c["dispatch"]})
        self.assertIs(rows[a["dispatch"]]["removed"], True)
        self.assertIs(rows[c["dispatch"]]["removed"], True)
        self.assertFalse(Path(a["snapshot"]).exists())
        self.assertFalse(Path(c["snapshot"]).exists())
        self.assertTrue(Path(b["snapshot"]).is_dir(), "another round's snapshot stays")
        rows = self.cleanup_rows()  # no filter: every ended candidate, as before
        self.assertEqual(set(rows), {a["dispatch"], b["dispatch"], c["dispatch"]})
        self.assertIs(rows[b["dispatch"]]["removed"], True)
        self.assertEqual(rows[a["dispatch"]]["reason"], "already removed")

    def test_a_filtered_dispatch_without_a_snapshot_gets_a_row_and_nothing_is_removed(self):
        (a,) = self.candidates("A")
        plain = self.begin("--launcher", "headless", work="P", capability=False)
        self.wait(plain["dispatch"])
        rows = self.cleanup_rows("--dispatch", plain["dispatch"], "--dispatch", "nope-123456")
        self.assertEqual(set(rows), {plain["dispatch"], "nope-123456"})
        for row in rows.values():
            self.assertEqual((row["snapshot"], row["removed"]), (None, False))
            self.assertEqual(row["reason"], "no snapshot recorded for this dispatch in the run")
        self.assertTrue(Path(a["snapshot"]).is_dir())

    def test_the_importable_cleanup_takes_the_same_filter(self):
        a, b = self.candidates("A", "B")
        rows = lanes.snapshot_cleanup(self.run_dir, dispatches=[b["dispatch"]])
        self.assertEqual([(r["dispatch"], r["removed"]) for r in rows], [(b["dispatch"], True)])
        self.assertTrue(Path(a["snapshot"]).is_dir())

    # --- (f) the observed pane state in the state file -------------------------------------

    def observed(self, dispatch):
        state = self.state_file(dispatch)
        datetime.fromisoformat(state["observed_at"].replace("Z", "+00:00"))
        return state["agent_status"], state["state"], state["observed_at"]

    def test_status_keeps_the_observed_pane_state_in_the_state_file(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        env = self.herdr_env()
        self.assertNotIn("agent_status", self.state_file(out["dispatch"]))
        self.status(out["dispatch"], env=env)
        agent, state, first = self.observed(out["dispatch"])
        self.assertEqual((agent, state), ("working", "running"))
        self.set_agent(out["pane"], "blocked")  # herdr blocks it after startup
        self.assertEqual(self.status(out["dispatch"], env=env)["state"], "blocked")
        agent, state, later = self.observed(out["dispatch"])
        self.assertEqual((agent, state), ("blocked", "blocked"))
        self.assertGreaterEqual(later, first)
        self.set_agent(out["pane"], "idle")
        self.status(out["dispatch"], env=env)
        self.assertEqual(self.observed(out["dispatch"])[:2], ("idle", "idle"))
        self.report_path().write_text("pane report\n")
        self.assertEqual(self.wait(out["dispatch"], env=env)["state"], "done")
        self.assertEqual(self.observed(out["dispatch"])[:2], ("idle", "done"))

    def test_a_headless_dispatch_records_no_pane_observation(self):
        out = self.begin("--launcher", "headless")
        self.wait(out["dispatch"])
        self.assertNotIn("agent_status", self.state_file(out["dispatch"]))
        self.assertNotIn("observed_at", self.state_file(out["dispatch"]))


if __name__ == "__main__":
    unittest.main()
