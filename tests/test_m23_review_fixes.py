"""MF2: fixes for the M2+M3 cross-vendor review.

1. arch_check resolves an extensionless import to the file that exists on disk.
2. investigation: a critique may say the two answers conflict (answers_conflict), which disputes
   the answer; a report without an **Answer:** line gives low confidence.
3. compare resume: a claimed slot is awaited for the claimer's whole launch window before it
   can be sealed as "claimed but never launched".
4. steering: per candidate, false (headless) or "unknown" (a herdr pane), in judgement.json and
   comparison.md, for design and investigation.

No real claude, codex or herdr is called: compare's units run on in-memory stubs, arch_check on
a temporary git checkout.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import mock
import unittest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
import compare  # noqa: E402

ARCH_CHECK = CORE / "scripts" / "arch_check.py"

ARCHITECTURE = {
    "layers": [
        {"name": "api", "paths": ["src/api/**/*.py", "web/api/**/*.ts"]},
        {"name": "data", "paths": ["src/data/**/*.py", "web/data/**/*.ts"]},
    ],
    "imports": [
        {"files": "src/**/*.py", "pattern": r"^\s*(?:from|import)\s+(?P<target>[\w.]+)",
         "resolve": {"app.": "src/"}},
        {"files": "web/**/*.ts", "pattern": r"from\s+\"(?P<target>[^\"]+)\"",
         "resolve": {"@app/": "web/"}},
    ],
}


class ExtensionlessImportTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=self.root, env=self.env,
                       check=True)
        self.write("harness/project/architecture.json", json.dumps(ARCHITECTURE))
        self.write("src/data/store.py", "def get():\n    return 1\n")
        self.write("src/data/models/__init__.py", "MODELS = []\n")
        self.write("web/data/store/index.ts", "export const store = 1;\n")

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def findings(self):
        result = subprocess.run([sys.executable, str(ARCH_CHECK), "--all", "--format", "json"],
                                cwd=self.root, env=self.env, capture_output=True, text=True)
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return [(f["path"], f["line"], f["message"]) for f in json.loads(result.stdout)["findings"]]

    def test_an_extensionless_module_import_resolves_to_the_file_on_disk(self):
        self.write("src/api/handler.py", "import os\nfrom app.data.store import get\n")
        self.assertEqual(self.findings(), [
            ("src/api/handler.py", 2, "api may not depend on data (app.data.store)")])

    def test_a_package_import_resolves_to_its_init_file(self):
        self.write("src/api/handler.py", "import app.data.models\n")
        self.assertEqual(self.findings(), [
            ("src/api/handler.py", 1, "api may not depend on data (app.data.models)")])

    def test_a_directory_import_resolves_to_its_index_file(self):
        self.write("web/api/client.ts", "import { store } from \"@app/data/store\";\n")
        self.assertEqual(self.findings(), [
            ("web/api/client.ts", 1, "api may not depend on data (@app/data/store)")])

    def test_an_import_of_nothing_on_disk_is_still_ignored(self):
        self.write("src/api/handler.py", "from app.data.ghost import get\n")
        self.assertEqual(self.findings(), [])


# --- compare: in-memory stubs -----------------------------------------------------------------

CANDIDATES = [{"label": "claude", "runtime": "claude", "profile": "implementation",
               "model": "m-claude", "effort": "high"},
              {"label": "codex", "runtime": "codex", "profile": "implementation",
               "model": "m-codex", "effort": "high"}]


class StubRun:
    def __init__(self):
        self.emitted = []

    def emit(self, kind, fields, event_id):
        self.emitted.append((kind, fields, event_id))
        return True


def stub(cls, folder: Path, **start):
    obj = cls.__new__(cls)
    obj.run, obj.cid, obj.folder, obj.blocked = StubRun(), "cmp-test", folder, set()
    obj.checkout, obj.input_text, obj.timeout, obj.arbiter_timeout = folder, "input\n", 30.0, 30.0
    obj.start = {"mode": "standard", "input_hash": "0" * 64, "capability": "equal",
                 "candidates": CANDIDATES,
                 "arbiter": {"runtime": "codex", "profile": "critical-review", "model": "m",
                             "effort": "high"},
                 "critics": {"claude": {**CANDIDATES[1], "profile": "critical-review"},
                             "codex": {**CANDIDATES[0], "profile": "critical-review"}},
                 **start}
    obj.question = "Should the client retry?"
    return obj


class FolderCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()


def critique(answer_disputed=False, **extra):
    return {"valid": True, "agreed": [], "disputed": [], "settles": [],
            "answer_disputed": answer_disputed, **extra}


REPORT_ENABLE = "# Investigation\n\n**Answer:** Enable retries · **Confidence:** high — run\n"
REPORT_DISABLE = "# Investigation\n\n**Answer:** Disable retries · **Confidence:** high — run\n"
REPORT_NO_ANSWER = "# Investigation\n\nRetries look fine. **Confidence:** high — run\n"
SEALS = {"claude": {"sha256": "a" * 64}, "codex": {"sha256": "b" * 64}}


class ConflictingAnswersTest(FolderCase):
    def conclude(self, reports, passes):
        inv = stub(compare.Investigation, self.folder, kind="investigation")
        self.run = inv.run
        return compare.conclude_investigation(inv, SEALS, reports, passes,
                                              {"claude": "headless", "codex": "headless"})

    def test_parse_critique_reads_answers_conflict(self):
        block = {"agreed": [], "disputed": [], "settles": [], "answer_disputed": False}
        text = "x\n\n```json\n{}\n```\n"
        parsed = compare.parse_critique(
            text.replace("{}", json.dumps({**block, "answers_conflict": True})))
        self.assertIs(parsed["answers_conflict"], True)
        parsed = compare.parse_critique(text.replace("{}", json.dumps(block)))
        self.assertIs(parsed["answers_conflict"], False)  # not stated: no conflict claimed
        with self.assertRaisesRegex(ValueError, "answers_conflict"):
            compare.parse_critique(text.replace("{}", json.dumps({**block,
                                                                  "answers_conflict": "yes"})))

    def test_conflicting_answers_dispute_the_answer_and_lower_confidence(self):
        judgement = self.conclude(
            {"claude": REPORT_ENABLE, "codex": REPORT_DISABLE},
            {"critique-claude": critique(answers_conflict=True),
             "critique-codex": critique(answers_conflict=False)})
        self.assertTrue(judgement["answer_disputed"])
        self.assertEqual(judgement["confidence"], "low")
        result = [f for kind, f, _ in self.run.emitted if kind == "compare.result"][0]
        self.assertTrue(result["answer_disputed"])
        self.assertEqual(result["confidence"], "low")

    def test_accepted_answers_without_conflict_keep_the_stated_confidence(self):
        judgement = self.conclude(
            {"claude": REPORT_ENABLE, "codex": REPORT_ENABLE},
            {"critique-claude": critique(answers_conflict=False),
             "critique-codex": critique(answers_conflict=False)})
        self.assertFalse(judgement["answer_disputed"])
        self.assertEqual(judgement["confidence"], "high")

    def test_a_report_without_an_answer_line_gives_low_confidence(self):
        judgement = self.conclude(
            {"claude": REPORT_ENABLE, "codex": REPORT_NO_ANSWER},
            {"critique-claude": critique(), "critique-codex": critique()})
        self.assertEqual(judgement["confidence"], "low")
        self.assertIn("codex", judgement["confidence_reason"])
        self.assertIn("Answer", judgement["confidence_reason"])

    def test_the_critique_prompt_asks_for_answers_conflict(self):
        prompt = (CORE / "prompts" / "compare-critique.md").read_text(encoding="utf-8")
        self.assertIn('"answers_conflict": false', prompt)
        self.assertIn("the other investigation's answer, for comparison", prompt)


class Clock:
    """A fake clock: sleep advances both time.time and time.monotonic."""

    def __init__(self, now=1_000_000.0):
        self.now = now
        self.slept = 0.0

    def time(self):
        return self.now

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
        self.slept += seconds


class ClaimWindowTest(FolderCase):
    def waiter(self, clock, claimed_at, launch_after=None):
        """A Compare whose state shows a claim at `claimed_at`, and a launch `launch_after`
        seconds after the claim (never when None)."""
        obj = stub(compare.Compare, self.folder)
        slot = "candidate-codex"
        launch = {"slot": slot, "dispatch": "d-1", "report": "r", "started": claimed_at}

        def state():
            out = {"claim": {slot: {"slot": slot, "pid": 1, "claimed": claimed_at}},
                   "launch": {}, "seal": {}, "pass": {}}
            if launch_after is not None and clock.now >= claimed_at + launch_after:
                out["launch"][slot] = launch
            return out
        obj.state = state
        return obj, slot, launch

    def patched(self, clock):
        return mock.patch.multiple(compare.time, time=clock.time, monotonic=clock.monotonic,
                                   sleep=clock.sleep)

    def test_the_launch_timeout_is_one_constant_used_by_the_claimer(self):
        self.assertGreaterEqual(compare.LAUNCH_TIMEOUT, 120.0)
        obj = stub(compare.Compare, self.folder)
        obj.run.task = "t1"
        seen = {}

        def run(argv, **kwargs):
            seen["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        with mock.patch.object(compare.subprocess, "run", run):
            obj.launch("candidate-codex", CANDIDATES[1], "candidate", self.folder / "b.md", 30,
                       candidate=True)
        self.assertEqual(seen["timeout"], compare.LAUNCH_TIMEOUT)

    def test_a_claim_records_when_it_was_made(self):
        clock = Clock()
        obj = stub(compare.Compare, self.folder)
        obj.state = lambda: {"seal": {}, "claim": {}, "launch": {}, "pass": {}}
        obj.candidate_brief = lambda: self.folder / "brief.md"
        obj.run.emit = lambda kind, fields, event_id: obj.run.emitted.append(
            (kind, fields, event_id)) or False  # another process holds every claim
        obj.launch_of = lambda slot: None
        obj.finish = lambda launch, timeout: "not launched"
        obj.seal = lambda *args: None
        with self.patched(clock):
            obj.candidates()
        claims = [f for kind, f, _ in obj.run.emitted if kind == "compare.claim"]
        self.assertEqual(len(claims), 2)
        for fields in claims:
            self.assertEqual(fields["claimed"], clock.now)

    def test_a_launch_slower_than_grace_but_inside_the_launch_timeout_is_awaited(self):
        clock = Clock()
        obj, slot, launch = self.waiter(clock, clock.now, launch_after=90.0)
        with self.patched(clock):
            self.assertEqual(obj.launch_of(slot), launch)

    def test_no_seal_while_the_claimers_window_is_open(self):
        clock = Clock()
        claimed = clock.now
        obj, slot, _ = self.waiter(clock, claimed)
        with self.patched(clock):
            self.assertIsNone(obj.launch_of(slot))
        self.assertGreaterEqual(clock.now, claimed + compare.LAUNCH_TIMEOUT)

    def test_a_claim_whose_window_closed_long_ago_is_not_awaited_again(self):
        clock = Clock()
        obj, slot, _ = self.waiter(clock, clock.now - 10 * compare.LAUNCH_TIMEOUT)
        with self.patched(clock):
            self.assertIsNone(obj.launch_of(slot))
        self.assertEqual(clock.slept, 0.0)


class SteeringTest(FolderCase):
    LAUNCHERS = {"claude": "headless", "codex": "pane"}
    EXPECTED = {"claude": False, "codex": "unknown"}

    def assert_steerable_lines(self):
        comparison = (self.folder / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("Not steerable (headless, no way to type into it): claude", comparison)
        self.assertIn("Steerable (herdr pane, the user could type into it; not tracked): codex",
                      comparison)

    def test_design_records_steering_per_candidate(self):
        obj = stub(compare.Compare, self.folder)
        judgement = compare.conclude(obj, {}, {}, self.LAUNCHERS)
        self.assertEqual(judgement["user_steered"], self.EXPECTED)
        saved = json.loads((self.folder / "judgement.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["user_steered"], self.EXPECTED)
        self.assert_steerable_lines()

    def test_investigation_records_steering_per_candidate(self):
        obj = stub(compare.Investigation, self.folder, kind="investigation")
        judgement = compare.conclude_investigation(obj, {}, {}, {}, self.LAUNCHERS)
        self.assertEqual(judgement["user_steered"], self.EXPECTED)
        saved = json.loads((self.folder / "judgement.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["user_steered"], self.EXPECTED)
        self.assert_steerable_lines()

    def test_all_headless_is_false_for_every_candidate(self):
        obj = stub(compare.Compare, self.folder)
        judgement = compare.conclude(obj, {}, {}, {"claude": "headless", "codex": "headless"})
        self.assertEqual(judgement["user_steered"], {"claude": False, "codex": False})


if __name__ == "__main__":
    unittest.main()
