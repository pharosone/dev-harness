"""`lanes.capability_profile(..., reads=True)` says what a worker can read, so compare can report
it: a candidate's writes are confined, its reads are not (the source and the run directory are
readable by absolute path, so its independence from a rival rests on the brief). Without
reads=True the profile is unchanged, {read_source, shell, writes}.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import lanes  # noqa: E402

READS = "source and run directory (independence rests on the brief)"


class ReadsTest(unittest.TestCase):
    def test_a_candidate_profile_says_its_reads_are_not_confined(self):
        self.assertEqual(lanes.CANDIDATE_READS, READS)
        for runtime in ("claude", "codex"):
            for launcher in ("headless", "pane"):
                self.assertEqual(
                    lanes.capability_profile(runtime, launcher, "candidate", reads=True),
                    {"read_source": True, "shell": True, "writes": "scratch", "reads": READS},
                    (runtime, launcher))

    def test_reads_is_added_only_when_asked_for(self):
        for runtime in ("claude", "codex"):
            for launcher in ("headless", "pane"):
                for capability in (None, "candidate"):
                    plain = lanes.capability_profile(runtime, launcher, capability)
                    self.assertNotIn("reads", plain)
                    full = lanes.capability_profile(runtime, launcher, capability, reads=True)
                    self.assertEqual({k: v for k, v in full.items() if k != "reads"}, plain)
                    self.assertIsInstance(full["reads"], str)
                    self.assertTrue(full["reads"])

    def test_the_lanes_docstring_states_the_read_risk(self):
        text = " ".join(lanes.__doc__.split())
        self.assertIn("reads are not confined", text)
        self.assertIn(READS, text)


if __name__ == "__main__":
    unittest.main()
