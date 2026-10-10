"""FM10: the round-7 cross-review findings on balance.py.

1. (Major) A Codex observation is as old as its rate_limits line, not the session file: its
   observed_at is the earlier of the line's `timestamp` and the file's mtime, so a line
   appended later without rate_limits does not make old usage beat a newer manual override.
2. (Major) Claude usage found in a run's lanes logs is recorded machine-wide (the observations
   file), once per log and time: another task, with no Claude logs of its own, sees it.
3. (Minor) A pick regenerates the run's dashboard (best effort), which shows the pick.
"""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_balance as tb  # noqa: E402

SEVEN_DAY, NOW = tb.SEVEN_DAY, tb.NOW


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


class CodexLineTimeTest(tb.BalanceTest):
    def test_an_appended_line_does_not_make_old_usage_beat_a_newer_override(self):
        line = tb.codex_line((10.0, SEVEN_DAY, 1791963927))
        line["timestamp"] = stamp(time.time() - 3600)
        path = tb.write_lines(self.codex_home / "sessions" / "2026" / "10" / "10"
                              / "rollout-x.jsonl", [line])
        self.balance("usage", "set", "--vendor", "codex", "--window", "seven_day",
                     "--used", "0.9", "--resets-at", "1791963927")
        with path.open("a", encoding="utf-8") as stream:  # later traffic, no rate_limits
            stream.write(json.dumps({"type": "response_item", "payload": {}}) + "\n")
        os.utime(path, (time.time() + 600, time.time() + 600))
        seven = self.window(self.usage(), "codex", "seven_day")
        self.assertAlmostEqual(seven["used"], 0.9)
        self.assertEqual(seven["source"], "manual")

    def test_a_newer_line_still_beats_an_older_override(self):
        self.balance("usage", "set", "--vendor", "codex", "--window", "seven_day",
                     "--used", "0.9", "--resets-at", "1791963927")
        line = tb.codex_line((10.0, SEVEN_DAY, 1791963927))
        line["timestamp"] = stamp(time.time() + 300)
        tb.write_lines(self.codex_home / "sessions" / "2026" / "10" / "10" / "rollout-x.jsonl",
                       [line], mtime=time.time() + 600)
        seven = self.window(self.usage(), "codex", "seven_day")
        self.assertAlmostEqual(seven["used"], 0.1)
        self.assertNotEqual(seven["source"], "manual")


class ClaudeSharedAcrossTasksTest(tb.BalanceTest):
    def init_task(self, task):
        subprocess.run([sys.executable, str(tb.TASK_STATE), "init", task], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)

    def test_another_task_sees_claude_usage_from_this_runs_logs(self):
        self.claude_usage(five=(0.9, NOW + 3600))
        self.assertEqual(self.window(self.usage("--task", "t1"), "claude", "five_hour")["used"],
                         0.9)
        self.init_task("t2")
        report = self.usage("--task", "t2")
        self.assertEqual(report["claude"]["status"], "known")
        self.assertAlmostEqual(self.window(report, "claude", "five_hour")["used"], 0.9)
        self.assertAlmostEqual(self.window(self.usage(), "claude", "five_hour")["used"], 0.9)

    def test_the_same_log_is_recorded_once(self):
        self.claude_usage(five=(0.5, NOW + 3600))
        self.usage("--task", "t1")
        self.usage("--task", "t1")
        path = self.state_home / "dev-harness" / "claude-observations.jsonl"
        lines = [line for line in path.read_text().splitlines() if line.strip()]
        self.assertEqual(len(lines), 1)


class PickDashboardTest(tb.BalanceTest):
    def test_a_pick_regenerates_the_dashboard(self):
        result = self.pick()
        pages = list(self.repo.rglob("dashboard.html"))
        self.assertEqual(len(pages), 1, pages)
        page = pages[0].read_text(encoding="utf-8")
        self.assertIn("implementer", page)
        self.assertIn(result["runtime"], page)


if __name__ == "__main__":
    unittest.main()
