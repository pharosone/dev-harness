"""A headless Codex candidate's last message never overwrites the report the candidate wrote as
it went: Codex writes it to `<report>.last-message.md`, and lanes copies it over the report only
when the report is not fresh (the rule Claude's result fallback follows). The line lanes appends
to a candidate's brief starts with the exported constant `CANDIDATE_BRIEF_PREFIX`, which compare
strips.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch come first on
PATH. With FAKE_OWN_REPORT=PATH the CLI fake writes "own report" to PATH before it ends, as a
candidate writing its report as it goes would.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_capability as capability  # noqa: E402
import test_lanes_launch as launch  # noqa: E402

lanes = launch.lanes
OWN = '''prompt = sys.stdin.read()
if os.environ.get("FAKE_OWN_REPORT"):
    with open(os.environ["FAKE_OWN_REPORT"], "w", encoding="utf-8") as own:
        own.write("own report\\n")
'''


class LastMessageTest(unittest.TestCase):
    setUp_capability = capability.CapabilityTest.setUp
    setUp_fixes = capability.CapabilityTest.setUp_fixes
    setUp_launch = capability.CapabilityTest.setUp_launch
    begin = capability.CapabilityTest.begin
    cleanup = launch.LaunchTest.cleanup
    git = launch.LaunchTest.git
    herdr_env = launch.LaunchTest.herdr_env
    lanes = launch.LaunchTest.lanes
    wait = launch.LaunchTest.wait
    calls = launch.LaunchTest.calls
    herdr_calls = launch.LaunchTest.herdr_calls
    events = launch.LaunchTest.events
    report_path = capability.CapabilityTest.report_path
    of = capability.CapabilityTest.of

    def setUp(self):
        self.setUp_capability()
        for name in ("claude", "codex"):
            fake = self.bin / name
            source = fake.read_text()
            self.assertIn("prompt = sys.stdin.read()\n", source)
            fake.write_text(source.replace("prompt = sys.stdin.read()\n", OWN, 1))
        self.report = self.report_path()
        self.last = Path(f"{self.report}.last-message.md")

    def codex(self, *extra, env=None, expect_state="done"):
        out = self.begin("--launcher", "headless", *extra, runtime="codex", env=env)
        self.assertEqual(self.wait(out["dispatch"])["state"], expect_state)
        (call,) = self.calls()
        return call

    # --- the last message goes beside the report -------------------------------------

    def test_a_report_the_candidate_wrote_as_it_went_is_kept(self):
        call = self.codex(env={"FAKE_OWN_REPORT": str(self.report)})
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--output-last-message") + 1], str(self.last))
        self.assertEqual(self.report.read_text(), "own report\n")
        self.assertEqual(self.last.read_text(), "fake codex report\n")

    def test_without_its_own_report_the_last_message_becomes_the_report(self):
        self.codex()
        self.assertEqual(self.report.read_text(), "fake codex report\n")
        self.assertEqual(self.last.read_text(), "fake codex report\n")

    def test_a_report_left_by_an_earlier_run_is_replaced(self):
        self.report.parent.mkdir(parents=True, exist_ok=True)
        self.report.write_text("an earlier run's report\n")
        self.codex()
        self.assertEqual(self.report.read_text(), "fake codex report\n")

    def test_a_failed_run_promotes_no_last_message(self):
        self.codex(env={"FAKE_EXIT": "3"}, expect_state="failed")
        self.assertFalse(self.report.exists())
        self.assertEqual(self.last.read_text(), "fake codex report\n")

    # --- the candidate line of the brief starts with a stable constant ----------------

    def test_the_candidate_line_starts_with_the_exported_prefix(self):
        self.assertEqual(lanes.CANDIDATE_BRIEF_PREFIX, "The source checkout is ")
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                self.scratch = self.temp / runtime / "scratch"
                out = self.begin("--launcher", "headless", runtime=runtime, work=runtime)
                self.wait(out["dispatch"])
        for call in self.calls():
            last = call["prompt"].rstrip("\n").splitlines()[-1]
            self.assertTrue(last.startswith(lanes.CANDIDATE_BRIEF_PREFIX), last)
            self.assertIn(str(self.temp / call["argv"][0] / "scratch" / "src"), last)
            self.assertEqual(call["prompt"].count(lanes.CANDIDATE_BRIEF_PREFIX), 1)
        self.scratch = self.temp / "pane" / "scratch"
        self.begin("--launcher", "pane", work="pane", env=self.herdr_env())
        [prompt] = self.of(["agent", "prompt"])
        self.assertIn(" " + lanes.CANDIDATE_BRIEF_PREFIX, prompt[3])

    def test_a_worker_that_is_not_a_candidate_gets_no_candidate_line(self):
        out = self.begin("--launcher", "headless", runtime="codex", capability=False)
        self.wait(out["dispatch"])
        (call,) = self.calls()
        self.assertNotIn(lanes.CANDIDATE_BRIEF_PREFIX, call["prompt"])
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--output-last-message") + 1], str(self.report))


if __name__ == "__main__":
    unittest.main()
