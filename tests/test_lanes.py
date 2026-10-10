"""`lanes.py decide` picks subagent, headless or herdr pane by the spec's rule, with one reason.

Pane-worthy (any): expected minutes over 20, a Tier-3 writer, a compare candidate, the user's
answers needed mid-way, work that outlives the turn, another Git top-level. Pane-worthy becomes
`pane` under HERDR_ENV=1, else `headless` with "no herdr: ...". Otherwise another runtime than
the orchestrator is `headless` and the same runtime is `subagent`.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
LANES = CORE / "scripts" / "lanes.py"
sys.path.insert(0, str(CORE / "scripts"))
import lanes  # noqa: E402

SHORT = {"runtime": "claude", "orchestrator": "claude", "minutes": 5}


def one_sentence(test: unittest.TestCase, reason: str) -> None:
    test.assertIsInstance(reason, str)
    test.assertTrue(reason.endswith("."), reason)
    test.assertNotIn("\n", reason)
    test.assertNotIn(". ", reason, f"more than one sentence: {reason}")


class DecideFunctionTest(unittest.TestCase):
    def decide(self, role="implementer", herdr=True, **kwargs):
        args = {**SHORT, **kwargs}
        result = lanes.decide(role, args.pop("runtime"), args.pop("orchestrator"),
                              args.pop("minutes"), herdr=herdr, **args)
        self.assertEqual(set(result), {"launcher", "reason", "herdr"})
        self.assertEqual(result["herdr"], herdr)
        one_sentence(self, result["reason"])
        return result

    def test_short_same_runtime_is_subagent(self):
        result = self.decide()
        self.assertEqual(result["launcher"], "subagent")
        self.assertIn("same runtime", result["reason"])

    def test_short_other_runtime_is_headless(self):
        result = self.decide(runtime="codex", orchestrator="claude")
        self.assertEqual(result["launcher"], "headless")
        self.assertIn("codex", result["reason"])
        self.assertNotIn("no herdr", result["reason"])

    def test_twenty_minutes_is_not_over(self):
        self.assertEqual(self.decide(minutes=20)["launcher"], "subagent")
        self.assertEqual(self.decide(minutes=20, runtime="codex")["launcher"], "headless")

    def test_twenty_one_minutes_is_a_pane(self):
        result = self.decide(minutes=21)
        self.assertEqual(result["launcher"], "pane")
        self.assertIn("21 min", result["reason"])
        self.assertIn("20", result["reason"])

    def test_needs_user_is_a_pane(self):
        result = self.decide(needs_user=True)
        self.assertEqual(result["launcher"], "pane")
        self.assertIn("user's answers", result["reason"])

    def test_outlives_turn_is_a_pane(self):
        result = self.decide(outlives_turn=True)
        self.assertEqual(result["launcher"], "pane")
        self.assertIn("outlive", result["reason"])

    def test_other_repository_is_a_pane(self):
        result = self.decide(other_repo="/somewhere/else")
        self.assertEqual(result["launcher"], "pane")
        self.assertIn("another repository", result["reason"])
        self.assertIn("/somewhere/else", result["reason"])

    def test_compare_candidate_is_a_pane(self):
        result = self.decide(compare_candidate=True)
        self.assertEqual(result["launcher"], "pane")
        self.assertIn("compare candidate", result["reason"])

    def test_tier3_writer_is_a_pane_and_tier3_reviewer_is_not(self):
        for writer in ("implementer", "simplifier"):
            result = self.decide(role=writer, tier=3)
            self.assertEqual(result["launcher"], "pane", writer)
            self.assertIn("tier-3", result["reason"])
            self.assertIn(writer, result["reason"])
        self.assertEqual(self.decide(role="reviewer", tier=3)["launcher"], "subagent")
        self.assertEqual(self.decide(role="implementer", tier=2)["launcher"], "subagent")

    def test_pane_worthy_without_herdr_is_headless(self):
        for signal, word in ((dict(minutes=21), "21 min"), (dict(needs_user=True), "user's answers"),
                             (dict(outlives_turn=True), "outlive"),
                             (dict(other_repo="/x"), "another repository"),
                             (dict(compare_candidate=True), "compare candidate"),
                             (dict(tier=3), "tier-3")):
            result = self.decide(herdr=False, **signal)
            self.assertEqual(result["launcher"], "headless", signal)
            self.assertTrue(result["reason"].startswith("no herdr: "), result["reason"])
            self.assertIn(word, result["reason"])

    def test_several_signals_are_all_named_in_one_sentence(self):
        result = self.decide(minutes=45, needs_user=True, outlives_turn=True)
        self.assertEqual(result["launcher"], "pane")
        for word in ("45 min", "user's answers", "outlive"):
            self.assertIn(word, result["reason"])

    def test_pane_worthy_ignores_runtime_mismatch(self):
        result = self.decide(runtime="codex", orchestrator="claude", needs_user=True)
        self.assertEqual(result["launcher"], "pane")

    def test_bad_arguments_raise(self):
        for bad in (dict(runtime="gemini"), dict(orchestrator="gemini"), dict(minutes=-1),
                    dict(tier=4)):
            with self.assertRaises(ValueError, msg=bad):
                lanes.decide("implementer", **{**SHORT, **bad}, herdr=True)
        with self.assertRaises(ValueError):
            lanes.decide("", **SHORT, herdr=True)


class DecideCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HERDR_ENV"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.here = self.repo("here")
        self.other = self.repo("other")

    def repo(self, name: str) -> Path:
        path = self.temp / name
        (path / "sub").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=self.env)
        return path

    def run_decide(self, *args: str, herdr: str | None = "1", cwd: Path | None = None):
        env = dict(self.env)
        if herdr is not None:
            env["HERDR_ENV"] = herdr
        return subprocess.run([sys.executable, str(LANES), "decide", *args],
                              cwd=cwd or self.here, capture_output=True, text=True, env=env)

    def decided(self, *args: str, **kwargs) -> dict:
        result = self.run_decide("--role", "implementer", "--runtime", "claude",
                                 "--orchestrator", "claude", *args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 1, result.stdout)
        report = json.loads(lines[0])
        self.assertEqual(set(report), {"launcher", "reason", "herdr"})
        one_sentence(self, report["reason"])
        return report

    def test_cli_rows_and_boundaries(self):
        self.assertEqual(self.decided("--minutes", "20")["launcher"], "subagent")
        self.assertEqual(self.decided("--minutes", "21")["launcher"], "pane")
        self.assertEqual(self.decided("--minutes", "5", "--needs-user")["launcher"], "pane")
        self.assertEqual(self.decided("--minutes", "5", "--outlives-turn")["launcher"], "pane")
        self.assertEqual(self.decided("--minutes", "5", "--compare-candidate")["launcher"], "pane")
        self.assertEqual(self.decided("--minutes", "5", "--tier", "3")["launcher"], "pane")

    def test_cli_other_runtime_is_headless(self):
        result = self.run_decide("--role", "reviewer", "--runtime", "codex",
                                 "--orchestrator", "claude", "--minutes", "5")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["launcher"], "headless")

    def test_cli_tier3_reviewer_is_subagent(self):
        result = self.run_decide("--role", "reviewer", "--runtime", "claude",
                                 "--orchestrator", "claude", "--minutes", "5", "--tier", "3")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["launcher"], "subagent")

    def test_cli_herdr_flag_follows_the_environment(self):
        self.assertTrue(self.decided("--minutes", "5")["herdr"])
        for value in (None, "0", ""):
            report = self.decided("--minutes", "21", herdr=value)
            self.assertFalse(report["herdr"], value)
            self.assertEqual(report["launcher"], "headless")
            self.assertTrue(report["reason"].startswith("no herdr: "), report["reason"])

    def test_cli_same_repository_is_not_another(self):
        for repo in (self.here, self.here / "sub"):
            self.assertEqual(self.decided("--minutes", "5", "--repo", str(repo))["launcher"],
                             "subagent")
        report = self.decided("--minutes", "5", "--repo", "sub", cwd=self.here / "sub" / "..")
        self.assertEqual(report["launcher"], "subagent")

    def test_cli_other_repository_is_a_pane(self):
        report = self.decided("--minutes", "5", "--repo", str(self.other / "sub"))
        self.assertEqual(report["launcher"], "pane")
        self.assertIn("another repository", report["reason"])
        self.assertIn(str(self.other), report["reason"])
        report = self.decided("--minutes", "5", "--repo", str(self.other), herdr=None)
        self.assertEqual(report["launcher"], "headless")
        self.assertTrue(report["reason"].startswith("no herdr: "))

    def test_cli_bad_arguments_exit_2(self):
        base = ["--role", "implementer", "--orchestrator", "claude"]
        cases = [
            [*base, "--runtime", "gemini", "--minutes", "5"],
            ["--role", "implementer", "--runtime", "claude", "--orchestrator", "gemini",
             "--minutes", "5"],
            [*base, "--runtime", "claude", "--minutes", "-1"],
            [*base, "--runtime", "claude", "--minutes", "five"],
            [*base, "--runtime", "claude", "--minutes", "5", "--repo", str(self.temp / "missing")],
            [*base, "--runtime", "claude", "--minutes", "5", "--tier", "7"],
            [*base, "--runtime", "claude"],
        ]
        (self.temp / "file.txt").write_text("x")
        cases.append([*base, "--runtime", "claude", "--minutes", "5",
                      "--repo", str(self.temp / "file.txt")])
        for args in cases:
            result = self.run_decide(*args)
            self.assertEqual(result.returncode, 2, (args, result.stdout, result.stderr))
            self.assertEqual(result.stdout, "", args)
            self.assertTrue(result.stderr.strip(), args)


if __name__ == "__main__":
    unittest.main()
