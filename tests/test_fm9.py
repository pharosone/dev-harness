"""FM9: an unreadable Claude observations file (`<state>/claude-observations.jsonl`) is no
observation, never a crash: `usage` reports Claude as unknown and `pick` still picks."""
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_balance as tb  # noqa: E402


@unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
class UnreadableObservationsTest(tb.BalanceTest):
    def setUp(self):
        super().setUp()
        path = self.state_home / "dev-harness" / "claude-observations.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text("{}\n")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)

    def test_usage_reports_claude_unknown(self):
        report = self.usage()
        self.assertEqual(report["claude"]["status"], "unknown")

    def test_pick_still_picks(self):
        result = self.pick()
        self.assertIn(result["runtime"], ("claude", "codex"))


if __name__ == "__main__":
    unittest.main()
