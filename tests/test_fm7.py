"""FM7: a headless Claude candidate's result that lanes writes into a report the candidate did
not write is its final message too: the state file and the dispatch.end record
`report_source: "final_message"`, as for Codex's copied last message, so compare's seal labels
the memo "from the final message". A Claude worker that is not a candidate keeps the end's
fields unchanged (no report_source), and a candidate's own report records none.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch come first on
PATH.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_r6_fixes as r6  # noqa: E402


class ClaudeFinalMessageSourceTest(r6.CopiedFinalMessageSourceTest):
    def claude(self, *extra, env=None, capability=True):
        out = self.begin("--launcher", "headless", *extra, runtime="claude", env=env,
                         capability=capability)
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        return out["dispatch"]

    def test_a_claude_candidates_result_written_as_the_report_is_its_final_message(self):
        dispatch = self.claude()
        self.assertEqual(self.report.read_text(), "fake claude report\n")
        self.assertEqual(self.state_file(dispatch).get("report_source"), "final_message")
        self.assertEqual(self.end(dispatch).get("report_source"), "final_message")

    def test_a_claude_candidates_own_report_records_no_source(self):
        dispatch = self.claude(env={"FAKE_OWN_REPORT": str(self.report)})
        self.assertEqual(self.report.read_text(), "own report\n")
        self.assertNotIn("report_source", self.end(dispatch))

    def test_a_claude_worker_that_is_not_a_candidate_records_no_source(self):
        dispatch = self.claude(capability=False)
        self.assertNotIn("report_source", self.end(dispatch))


if __name__ == "__main__":
    unittest.main()
