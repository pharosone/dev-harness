"""`balance.py usage` and `balance.py pick`: vendor usage from captured streams, and the pick.

Every reader points at fixture directories: the run's lanes logs, $CODEX_HOME for Codex session
files, $XDG_STATE_HOME for the machine-wide state (manual overrides, Claude observations) and
$HARNESS_PROJECT for the overlay. The user's real ~/.codex and ~/.claude are never read.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

CORE = Path(__file__).resolve().parents[1]
BALANCE = CORE / "scripts" / "balance.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
MODELS = json.loads((CORE / "adapters" / "models.json").read_text(encoding="utf-8"))
sys.path.insert(0, str(CORE / "scripts"))
import balance  # noqa: E402

NOW = 1_791_600_000  # the --now every pick test passes
FIVE_HOUR, SEVEN_DAY = 300, 10080


def resets_for(elapsed: float, minutes: int, now: int = NOW) -> int:
    """resets_at such that `elapsed` of a `minutes` window has passed at `now`."""
    return int(round(now + (1 - elapsed) * minutes * 60))


def claude_event(five=None, seven=None) -> dict:
    """A Claude stream-json rate_limit_event; five / seven are (utilization, resetsAt)."""
    windows = {}
    if five:
        windows["five_hour"] = {"utilization": five[0], "resetsAt": five[1]}
    if seven:
        windows["seven_day"] = {"utilization": seven[0], "resetsAt": seven[1]}
    main = seven or five
    return {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed_warning", "resetsAt": main[1], "rateLimitType": "seven_day",
        "utilization": main[0], "isUsingOverage": False, "unifiedWindows": windows}}


def codex_line(primary=None, secondary=None) -> dict:
    """A Codex session line; primary / secondary are (used_percent, window_minutes, resets_at)."""
    def window(value):
        if value is None:
            return None
        return {"used_percent": value[0], "window_minutes": value[1], "resets_at": value[2]}
    # FM10: a line's timestamp bounds its observed_at, so the fixture's line is written now.
    now = time.time()
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + f".{int(now % 1 * 1000):03d}Z"
    return {"timestamp": stamp, "type": "event_msg", "payload": {
        "type": "token_count", "info": None, "rate_limits": {
            "limit_id": "codex", "limit_name": None, "primary": window(primary),
            "secondary": window(secondary), "credits": None, "plan_type": "plus"}}}


def write_lines(path: Path, lines: list, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join((line if isinstance(line, str) else json.dumps(line)) + "\n"
                            for line in lines), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class BalanceTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo = self.temp / "repo"
        self.project = self.temp / "project"
        self.codex_home = self.temp / "codex"
        self.state_home = self.temp / "state"
        for directory in (self.repo, self.project, self.codex_home, self.state_home):
            directory.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "HERDR_", "HARNESS_", "CODEX_", "XDG_"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        HARNESS_PROJECT=str(self.project), CODEX_HOME=str(self.codex_home),
                        XDG_STATE_HOME=str(self.state_home), HOME=str(self.temp / "home"))
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Harness Test"),
                     ("config", "user.email", "harness@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True)
        (self.repo / "README").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, env=self.env,
                       check=True)
        subprocess.run([sys.executable, str(TASK_STATE), "init", "t1"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        self.run_dir = self.repo / ".harness" / "tasks" / "t1"
        self.lanes = self.run_dir / "lanes"

    # Helpers

    def balance(self, *args, expect=0):
        result = subprocess.run([sys.executable, str(BALANCE), *args], cwd=self.repo,
                                env=self.env, capture_output=True, text=True, timeout=60)
        if expect is not None:
            self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def usage(self, *args) -> dict:
        return json.loads(self.balance("usage", "--json", *args).stdout)

    def pick(self, *args, expect=0, profile="implementation", role="implementer") -> dict:
        result = self.balance("pick", "--task", "t1", "--profile", profile, "--role", role,
                              "--now", str(NOW), *args, expect=expect)
        return json.loads(result.stdout)

    def event(self, kind: str, **fields):
        subprocess.run([sys.executable, str(TASK_STATE), "event", "t1", kind, "--data",
                        json.dumps(fields)], cwd=self.repo, env=self.env, check=True,
                       capture_output=True)

    def dispatches(self, claude=0, codex=0, compare_codex=0):
        for index in range(claude):
            self.event("dispatch.start", dispatch=f"c{index}", runtime="claude", work="U1")
        for index in range(codex):
            self.event("dispatch.start", dispatch=f"x{index}", runtime="codex", work="U1")
        for index in range(compare_codex):
            self.event("dispatch.start", dispatch=f"m{index}", runtime="codex",
                       work=f"cmp-design-{index}")

    def claude_usage(self, five=None, seven=None, name="d1"):
        write_lines(self.lanes / f"{name}.log", [{"type": "system"}, claude_event(five, seven),
                                                  {"type": "result", "result": "ok"}])

    def codex_usage(self, primary=None, secondary=None, mtime=None):
        write_lines(self.codex_home / "sessions" / "2026" / "10" / "09" / "rollout-x.jsonl",
                    [{"type": "session_meta"}, codex_line(primary, secondary)], mtime)

    def events(self) -> list:
        path = self.run_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    @staticmethod
    def window(report: dict, vendor: str, name: str) -> dict:
        found = [w for w in report[vendor]["windows"] if w["name"] == name]
        if len(found) != 1:
            raise AssertionError(f"{vendor} has no single window {name}: {report[vendor]}")
        return found[0]


class ClaudeUsageTest(BalanceTest):
    def test_newest_event_across_the_runs_lanes_logs(self):
        old = time.time() - 3600
        write_lines(self.lanes / "old.log", [claude_event((0.9, 1791603600), (0.95, 1792108800))],
                    mtime=old)
        newest = write_lines(self.lanes / "new.log", [
            claude_event((0.5, 1791603600), (0.6, 1792108800)),
            {"type": "assistant", "message": {"content": []}},
            claude_event((0.12, 1791603600), (0.4, 1792108800)),
            "not json at all",
        ], mtime=old + 600)
        report = self.usage("--task", "t1")
        self.assertEqual(report["claude"]["status"], "known")
        five = self.window(report, "claude", "five_hour")
        seven = self.window(report, "claude", "seven_day")
        self.assertAlmostEqual(five["used"], 0.12)
        self.assertEqual(five["window_minutes"], FIVE_HOUR)
        self.assertEqual(five["resets_at"], 1791603600)
        self.assertAlmostEqual(seven["used"], 0.4)
        self.assertEqual(seven["window_minutes"], SEVEN_DAY)
        self.assertEqual(seven["resets_at"], 1792108800)
        self.assertAlmostEqual(seven["observed_at"], newest.stat().st_mtime, delta=1)
        self.assertIn("new.log", seven["source"])

    def test_observe_feeds_usage_without_a_task(self):
        first = write_lines(self.temp / "first.log", [claude_event((0.3, 1791603600))],
                            mtime=time.time() - 600)
        second = write_lines(self.temp / "second.log",
                             [claude_event((0.2, 1791603600), (0.55, 1792108800))])
        self.balance("observe", "--from", str(second))
        self.balance("observe", "--from", str(first))
        self.assertTrue((self.state_home / "dev-harness" / "claude-observations.jsonl").is_file())
        report = self.usage()
        self.assertEqual(report["claude"]["status"], "known")
        self.assertAlmostEqual(self.window(report, "claude", "seven_day")["used"], 0.55)
        self.assertAlmostEqual(self.window(report, "claude", "five_hour")["used"], 0.2)

    def test_observe_without_an_event_records_nothing(self):
        log = write_lines(self.temp / "plain.log", [{"type": "result", "result": "ok"}])
        result = self.balance("observe", "--from", str(log), expect=1)
        self.assertIn("rate_limit_event", result.stderr)
        self.assertEqual(self.usage()["claude"]["status"], "unknown")


class CodexUsageTest(BalanceTest):
    def test_newest_session_file_last_rate_limits(self):
        sessions = self.codex_home / "sessions" / "2026" / "10"
        old = time.time() - 7200
        write_lines(sessions / "08" / "rollout-a.jsonl",
                    [codex_line((50.0, SEVEN_DAY, 1791963927))], mtime=old)
        write_lines(sessions / "09" / "rollout-b.jsonl", [
            codex_line((2.0, SEVEN_DAY, 1791963927)),
            {"type": "response_item", "payload": {"type": "message"}},
            codex_line((3.0, SEVEN_DAY, 1791963927)),
        ], mtime=old + 600)
        report = self.usage()
        self.assertEqual(report["codex"]["status"], "known")
        self.assertEqual(len(report["codex"]["windows"]), 1, report["codex"])
        seven = self.window(report, "codex", "seven_day")
        self.assertAlmostEqual(seven["used"], 0.03)
        self.assertEqual(seven["window_minutes"], SEVEN_DAY)
        self.assertEqual(seven["resets_at"], 1791963927)
        self.assertIn("rollout-b.jsonl", seven["source"])

    def test_secondary_window_when_not_null(self):
        self.codex_usage((40.0, FIVE_HOUR, 1791603600), (10.0, SEVEN_DAY, 1791963927))
        report = self.usage()
        self.assertAlmostEqual(self.window(report, "codex", "five_hour")["used"], 0.4)
        self.assertAlmostEqual(self.window(report, "codex", "seven_day")["used"], 0.1)

    def test_newest_file_without_rate_limits_falls_back_to_the_previous(self):
        sessions = self.codex_home / "sessions" / "2026" / "10"
        write_lines(sessions / "08" / "rollout-a.jsonl",
                    [codex_line((7.0, SEVEN_DAY, 1791963927))], mtime=time.time() - 600)
        write_lines(sessions / "09" / "rollout-b.jsonl", [{"type": "session_meta"}])
        self.assertAlmostEqual(self.window(self.usage(), "codex", "seven_day")["used"], 0.07)


class UnknownAndOverrideTest(BalanceTest):
    def test_no_data_is_unknown(self):
        report = self.usage("--task", "t1")
        for vendor in ("claude", "codex"):
            self.assertEqual(report[vendor], {"status": "unknown", "windows": []})
        text = self.balance("usage", "--task", "t1").stdout
        self.assertIn("unknown", text)

    def test_manual_override_wins_over_an_older_observation(self):
        self.codex_usage((3.0, SEVEN_DAY, 1791963927), mtime=time.time() - 600)
        self.balance("usage", "set", "--vendor", "codex", "--window", "seven_day",
                     "--used", "0.7", "--resets-at", "1791963927")
        self.assertTrue((self.state_home / "dev-harness" / "vendor-usage.json").is_file())
        seven = self.window(self.usage(), "codex", "seven_day")
        self.assertAlmostEqual(seven["used"], 0.7)
        self.assertEqual(seven["window_minutes"], SEVEN_DAY)
        self.assertEqual(seven["source"], "manual")

    def test_override_loses_to_a_newer_observation(self):
        self.balance("usage", "set", "--vendor", "codex", "--window", "seven_day",
                     "--used", "0.7", "--resets-at", "1791963927")
        self.codex_usage((3.0, SEVEN_DAY, 1791963927), mtime=time.time() + 600)
        seven = self.window(self.usage(), "codex", "seven_day")
        self.assertAlmostEqual(seven["used"], 0.03)
        self.assertNotEqual(seven["source"], "manual")

    def test_override_of_a_window_with_no_observation(self):
        self.balance("usage", "set", "--vendor", "claude", "--window", "custom", "--used",
                     "0.25", "--resets-at", "1791603600", "--window-minutes", "60")
        report = self.usage()
        self.assertEqual(report["claude"]["status"], "known")
        custom = self.window(report, "claude", "custom")
        self.assertEqual(custom["window_minutes"], 60)
        self.assertAlmostEqual(custom["used"], 0.25)

    def test_bad_override_is_exit_2(self):
        self.balance("usage", "set", "--vendor", "claude", "--window", "custom", "--used",
                     "0.25", "--resets-at", "1791603600", expect=2)  # no length known
        self.balance("usage", "set", "--vendor", "claude", "--window", "five_hour", "--used",
                     "1.5", "--resets-at", "1791603600", expect=2)
        self.balance("usage", "set", "--vendor", "other", "--window", "five_hour", "--used",
                     "0.5", "--resets-at", "1791603600", expect=2)


class RunwayTest(unittest.TestCase):
    def test_known_points(self):
        # Half the window gone, a quarter used: 0.75 / 0.5.
        self.assertAlmostEqual(balance.runway(0.25, NOW + 150 * 60, FIVE_HOUR, NOW), 1.5)
        # Window just started: 1 - used.
        self.assertAlmostEqual(balance.runway(0.2, NOW + 300 * 60, FIVE_HOUR, NOW), 0.8)
        # A reset further away than the window clamps elapsed to 0.
        self.assertAlmostEqual(balance.runway(0.2, NOW + 400 * 60, FIVE_HOUR, NOW), 0.8)
        # One minute before reset: the remaining time is floored at 0.01.
        self.assertAlmostEqual(balance.runway(0.5, NOW + 60, FIVE_HOUR, NOW), 50.0)
        # Seven-day window, three quarters gone, 0.4 used: 0.6 / 0.25.
        self.assertAlmostEqual(
            balance.runway(0.4, resets_for(0.75, SEVEN_DAY), SEVEN_DAY, NOW), 2.4, places=3)


class PickTest(BalanceTest):
    def test_vendor_runway_is_its_smallest_window(self):
        self.claude_usage(five=(0.25, NOW + 150 * 60), seven=(0.4, resets_for(0.75, SEVEN_DAY)))
        self.codex_usage((10.0, SEVEN_DAY, resets_for(0.5, SEVEN_DAY)))
        result = self.pick()
        self.assertEqual(set(result), {"runtime", "model", "effort", "reason", "shares",
                                       "runway", "usage_status"})
        self.assertAlmostEqual(result["runway"]["claude"], 1.5, places=3)
        self.assertAlmostEqual(result["runway"]["codex"], 1.8, places=3)
        self.assertEqual(result["runtime"], "codex")
        self.assertEqual(result["usage_status"], {"claude": "known", "codex": "known"})

    def test_unknown_usage_is_neutral_and_said(self):
        self.claude_usage(seven=(0.4, resets_for(0.75, SEVEN_DAY)))
        result = self.pick()
        self.assertEqual(result["usage_status"], {"claude": "known", "codex": "unknown"})
        self.assertEqual(result["runway"]["codex"], 1.0)
        self.assertIn("usage unknown", result["reason"])
        self.assertEqual(result["runtime"], "claude")  # 2.4 against a neutral 1.0

    def test_both_unknown_still_picks(self):
        result = self.pick()
        self.assertIn(result["runtime"], ("claude", "codex"))
        self.assertEqual(result["runway"], {"claude": 1.0, "codex": 1.0})
        self.assertIn("usage unknown", result["reason"])

    def test_exclusion_at_stop_at(self):
        # Runway 0.15 / 0.05 = 3.0 would win, but 0.85 used is at the stop line.
        self.claude_usage(seven=(0.85, resets_for(0.95, SEVEN_DAY)))
        result = self.pick()
        self.assertEqual(result["runtime"], "codex")
        self.assertIn("claude", result["reason"])
        self.assertIn("0.85", result["reason"])

    def test_just_below_stop_at_is_not_excluded(self):
        self.claude_usage(seven=(0.84, resets_for(0.95, SEVEN_DAY)))
        self.assertEqual(self.pick()["runtime"], "claude")

    def test_exclusion_on_projected_exhaustion(self):
        # Codex is under its minimum share, but 0.5 used a quarter into the window runs out.
        self.dispatches(claude=3)
        self.codex_usage((50.0, SEVEN_DAY, resets_for(0.25, SEVEN_DAY)))
        result = self.pick()
        self.assertAlmostEqual(result["runway"]["codex"], 0.5 / 0.75, places=3)
        self.assertEqual(result["runtime"], "claude")
        self.assertIn("codex", result["reason"])

    def test_both_excluded_is_exit_6(self):
        self.claude_usage(five=(0.9, NOW + 60))
        # FM8: projected exhaustion alone no longer excludes; codex is at its stop line too.
        self.codex_usage((90.0, SEVEN_DAY, resets_for(0.95, SEVEN_DAY)))
        result = self.pick(expect=6)
        self.assertIsNone(result["runtime"])
        self.assertIn("both vendors are near their limits; ask the user", result["reason"])
        self.assertFalse(any(e["type"] == "balance.pick" for e in self.events_or_empty()))

    def events_or_empty(self):
        return self.events() if (self.run_dir / "events.jsonl").is_file() else []

    def test_minimum_share_from_events_ignores_compare_candidates(self):
        self.dispatches(claude=3, codex=1, compare_codex=2)
        self.claude_usage(seven=(0.4, resets_for(0.75, SEVEN_DAY)))  # claude would win on runway
        result = self.pick()
        self.assertEqual(result["runtime"], "codex")
        self.assertAlmostEqual(result["shares"]["claude"], 0.75)
        self.assertAlmostEqual(result["shares"]["codex"], 0.25)
        self.assertIn("share", result["reason"])

    def test_share_at_the_minimum_leaves_it_to_runway(self):
        self.dispatches(claude=7, codex=3)
        self.claude_usage(seven=(0.4, resets_for(0.75, SEVEN_DAY)))
        self.assertEqual(self.pick()["runtime"], "claude")

    def test_hysteresis_keeps_the_previous_vendor(self):
        self.dispatches(claude=1, codex=1)
        self.event("balance.pick", runtime="codex", profile="implementation", role="implementer")
        # Claude 0.55 / 0.5 = 1.1 against codex's neutral 1.0: a gap of 0.1 <= 0.15.
        self.claude_usage(five=(0.45, NOW + 150 * 60))
        self.assertEqual(self.pick()["runtime"], "codex")
        # Claude 0.6 / 0.5 = 1.2: a gap of 0.2 > 0.15 switches.
        self.claude_usage(five=(0.4, NOW + 150 * 60))
        self.assertEqual(self.pick()["runtime"], "claude")

    def test_without_a_previous_pick_the_larger_runway_wins(self):
        self.dispatches(claude=1, codex=1)
        self.claude_usage(five=(0.45, NOW + 150 * 60))
        self.assertEqual(self.pick()["runtime"], "claude")

    def test_overlay_balance_changes_the_defaults(self):
        (self.project / "project.json").write_text(json.dumps(
            {"balance": {"hysteresis": 0.05, "stop_at": 0.5, "min_share": 0.1}}))
        self.dispatches(claude=1, codex=1)
        self.event("balance.pick", runtime="codex", profile="implementation", role="implementer")
        self.claude_usage(five=(0.45, NOW + 150 * 60))  # gap 0.1 > 0.05 now
        self.assertEqual(self.pick()["runtime"], "claude")
        self.claude_usage(five=(0.55, NOW + 60))  # 0.55 >= stop_at 0.5
        self.assertEqual(self.pick()["runtime"], "codex")

    def test_bad_overlay_balance_is_exit_2(self):
        (self.project / "project.json").write_text(json.dumps({"balance": {"stop_at": "high"}}))
        self.balance("pick", "--task", "t1", "--profile", "implementation", "--role", "r",
                     expect=2)

    def test_not_runtime_keeps_the_review_on_the_other_vendor(self):
        self.claude_usage(seven=(0.4, resets_for(0.75, SEVEN_DAY)))
        result = self.pick("--not-runtime", "claude", profile="critical-review", role="reviewer")
        self.assertEqual(result["runtime"], "codex")
        self.assertEqual(result["model"], MODELS["codex"]["critical-review"]["model"])
        self.assertEqual(result["effort"], MODELS["codex"]["critical-review"]["effort"])

    def test_not_runtime_leaving_an_excluded_vendor_is_exit_6(self):
        self.claude_usage(seven=(0.9, resets_for(0.95, SEVEN_DAY)))
        result = self.pick("--not-runtime", "codex", expect=6)
        self.assertIsNone(result["runtime"])
        self.assertIn("ask the user", result["reason"])

    def test_profile_from_the_merged_models(self):
        self.claude_usage(seven=(0.4, resets_for(0.75, SEVEN_DAY)))
        result = self.pick()
        self.assertEqual(result["runtime"], "claude")
        self.assertEqual(result["model"], MODELS["claude"]["implementation"]["model"])
        self.assertEqual(result["effort"], MODELS["claude"]["implementation"]["effort"])
        (self.project / "models.json").write_text(json.dumps(
            {"claude": {"implementation": {"effort": "xhigh"}}}))
        result = self.pick()
        self.assertEqual(result["model"], MODELS["claude"]["implementation"]["model"])
        self.assertEqual(result["effort"], "xhigh")

    def test_unknown_profile_is_exit_2(self):
        result = self.balance("pick", "--task", "t1", "--profile", "nope", "--role", "r",
                              expect=2)
        self.assertIn("nope", result.stderr)

    def test_profile_on_one_vendor_only_is_exit_2(self):
        (self.project / "models.json").write_text(json.dumps(
            {"claude": {"solo": {"model": "m", "effort": "high"}}}))
        result = self.balance("pick", "--task", "t1", "--profile", "solo", "--role", "r",
                              "--not-runtime", "codex", expect=2)
        self.assertIn("codex", result.stderr)

    def test_pick_writes_a_balance_pick_event(self):
        self.dispatches(claude=1, codex=1)
        self.claude_usage(seven=(0.4, resets_for(0.75, SEVEN_DAY)))
        result = self.pick(role="reviewer", profile="critical-review")
        last = self.events()[-1]
        self.assertEqual(last["type"], "balance.pick")
        self.assertEqual(last["runtime"], result["runtime"])
        self.assertEqual(last["profile"], "critical-review")
        self.assertEqual(last["role"], "reviewer")
        self.assertEqual(last["reason"], result["reason"])
        self.assertEqual(last["runway"], result["runway"])
        self.assertEqual(last["shares"], result["shares"])

    def test_importable_pick(self):
        self.claude_usage(seven=(0.4, resets_for(0.75, SEVEN_DAY)))
        previous_cwd, previous_env = Path.cwd(), dict(os.environ)
        self.addCleanup(os.chdir, previous_cwd)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(previous_env)))
        os.chdir(self.repo)
        os.environ.clear()
        os.environ.update(self.env)
        result = balance.pick("t1", "implementation", "implementer", now=NOW)
        self.assertEqual(result["runtime"], "claude")
        self.assertEqual(self.events()[-1]["type"], "balance.pick")


if __name__ == "__main__":
    unittest.main()
