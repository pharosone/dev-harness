"""FM8: projected exhaustion is a soft, damped signal in `balance.py pick`.

- A vendor is hard-excluded when any current window has used >= stop_at.
- Projected exhaustion (runway < 1) counts only once min_elapsed (default 0.25) of that window
  has passed: early in a window one burst would otherwise exclude a vendor.
- It is soft: it steers the pick to the other vendor and overrides the minimum share, but by
  itself never makes pick exit 6; with both vendors projected the larger runway wins.
- Exit 6 only when every allowed vendor is hard-excluded.
- Overlay `project.json → balance.min_elapsed` in [0, 1] changes the default.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_balance as tb  # noqa: E402

SEVEN_DAY, FIVE_HOUR, NOW = tb.SEVEN_DAY, tb.FIVE_HOUR, tb.NOW


class SoftProjectionTest(tb.BalanceTest):
    def early_codex(self):
        # 10 % used 5 % into the window: runway 0.9 / 0.95 = 0.947 < 1, but early.
        self.dispatches(claude=3)
        self.codex_usage((10.0, SEVEN_DAY, tb.resets_for(0.05, SEVEN_DAY)))

    def test_projection_early_in_a_window_does_not_exclude(self):
        self.early_codex()
        result = self.pick()
        self.assertEqual(result["runtime"], "codex")  # its share 0 is below min_share
        self.assertIn("share", result["reason"])

    def test_both_projected_below_stop_at_picks_the_larger_runway(self):
        # Claude 0.4 / 0.5 = 0.8, codex 0.3 / 0.5 = 0.6: both would run out, neither is at 0.85.
        self.claude_usage(five=(0.6, NOW + 150 * 60))
        self.codex_usage((70.0, SEVEN_DAY, tb.resets_for(0.5, SEVEN_DAY)))
        result = self.pick()
        self.assertEqual(result["runtime"], "claude")
        self.assertIn("would run out", result["reason"])
        self.assertTrue(any(e["type"] == "balance.pick" for e in self.events()))

    def test_overlay_min_elapsed_changes_the_default(self):
        (self.project / "project.json").write_text(json.dumps({"balance": {"min_elapsed": 0}}))
        self.early_codex()
        result = self.pick()
        self.assertEqual(result["runtime"], "claude")
        self.assertIn("codex", result["reason"])

    def test_bad_min_elapsed_is_exit_2(self):
        (self.project / "project.json").write_text(json.dumps({"balance": {"min_elapsed": 1.5}}))
        self.balance("pick", "--task", "t1", "--profile", "implementation", "--role", "r",
                     expect=2)

    def test_both_hard_excluded_is_still_exit_6(self):
        self.claude_usage(five=(0.9, NOW + 60))
        self.codex_usage((90.0, SEVEN_DAY, tb.resets_for(0.95, SEVEN_DAY)))
        result = self.pick(expect=6)
        self.assertIsNone(result["runtime"])
        self.assertIn("ask the user", result["reason"])


if __name__ == "__main__":
    unittest.main()
