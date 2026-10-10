"""MF4b: QA findings on compare and the architecture check.

1. (M1) The Codex enforcement note names both writable roots, the snapshot (-C) and the scratch
   (--add-dir); a candidate whose report is missing but whose final message was written is
   sealed from that final message, and the seal says so.
2. (m4) With fewer than two valid critiques, whether the answer is disputed is unknown (null),
   never "no".
3. (m5) A single surviving memo is not a winner: no ranking, `unarbitrated: [label]`, and the
   summary says "not arbitrated (one memo)".
4. (M2) After compare.result the round's candidate snapshots are removed (sealed memos stay)
   unless --keep-snapshots; a cleanup that fails is a note, never a failed compare.
5. (m17) arch_check reports a source it cannot read instead of treating it as clean.

No real claude, codex or herdr is called: fakes on PATH, or compare's units on in-memory stubs.
"""
import hashlib
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

COMPARE = CORE / "scripts" / "compare.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
ARCH_CHECK = CORE / "scripts" / "arch_check.py"

FAKE_CLI = r"""#!{python}
import json, os, re, sys, time
name = os.path.basename(sys.argv[0])
prompt = sys.stdin.read()
memos = re.findall(r"^## Memo ([A-Z])\n(.*?)(?=^## Memo [A-Z]\n|^## End of memos\n)", prompt,
                   re.MULTILINE | re.DOTALL)
role = "arbiter" if memos else "candidate"
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"name": name, "role": role, "argv": sys.argv[1:], "prompt": prompt}) + "\n")
def last_message(text):
    out = sys.argv[sys.argv.index("--output-last-message") + 1]
    with open(out, "w", encoding="utf-8") as stream:
        stream.write(text)
def finish(text):
    if name == "claude":
        print(json.dumps({"type": "assistant", "message": {"content": []}}), flush=True)
        print(json.dumps({"type": "result", "result": text, "duration_ms": 10,
                          "usage": {"input_tokens": 1, "output_tokens": 1}}), flush=True)
    else:
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1,
                                                                "output_tokens": 1}}), flush=True)
        if text:
            last_message(text)
if role == "candidate":
    if os.environ.get("FAKE_LOCK_" + name) == "1":
        # A directory in the snapshot that cannot be emptied: its removal fails.
        os.makedirs("locked/inner", exist_ok=True)
        open("locked/inner/file", "w").close()
        os.chmod("locked/inner", 0o500)
    if os.environ.get("FAKE_FAIL_" + name) == "1":
        sys.exit(1)
    text = os.environ.get("FAKE_MEMO_" + name, "A plain memo.\n")
    if os.environ.get("FAKE_HANG_" + name) == "1":
        last_message(text)  # the final message is written, then the run hangs past its time
        time.sleep(120)
    finish(text)
    sys.exit(0)
letters = [letter for letter, _ in memos]
verdict = {"ranking": letters, "failed_mandatory": {}, "disagreements": [], "confidence": "high"}
finish("Arbiter reasoning.\n\n```json\n" + json.dumps(verdict) + "\n```\n")
"""

INTERVIEW = """# Feature interview

## Requirements interview

| # | Item | Question | Answer | Source |
|---|------|----------|--------|--------|
| 1 | Goal | Who uses it? | The operator of a run | user |
"""

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


def critique(answer_disputed=False, **extra):
    return {"valid": True, "agreed": [], "disputed": [], "settles": [],
            "answer_disputed": answer_disputed, **extra}


REPORT = "# Investigation\n\n**Answer:** Enable retries · **Confidence:** high — run\n"
HEADLESS = {"claude": "headless", "codex": "headless"}


class FolderCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name).resolve()

    def emitted(self, obj, kind):
        return [fields for k, fields, _ in obj.run.emitted if k == kind]


# --- 1. M1: the Codex enforcement note, and the final message as the memo -------------------

class CodexEnforcementNoteTest(unittest.TestCase):
    def test_codex_note_names_the_snapshot_and_the_scratch_as_writable_roots(self):
        note = compare.capability_note([{"runtime": "codex"}])
        self.assertNotIn("one writable root", note)
        self.assertIn("writable roots", note)
        self.assertIn("snapshot (-C)", note)
        self.assertIn("scratch (--add-dir)", note)
        both = compare.capability_note([{"runtime": "claude"}, {"runtime": "codex"}])
        self.assertIn("scratch (--add-dir)", both)


class FinalMessageSealTest(FolderCase):
    def test_a_missing_report_is_sealed_from_the_final_message_and_marked(self):
        obj = stub(compare.Compare, self.folder)
        report = self.folder / "private" / "x" / "scratch" / "report.md"
        report.parent.mkdir(parents=True)
        Path(f"{report}.last-message.md").write_text("Plan from the last message.\n",
                                                     encoding="utf-8")
        obj.seal("codex", {"slot": "candidate-codex", "dispatch": "d-1", "report": str(report)},
                 "timeout")
        seal = self.emitted(obj, "compare.seal")[0]
        text = "Plan from the last message.\n"
        self.assertEqual((self.folder / "candidate-codex.md").read_text(encoding="utf-8"), text)
        self.assertEqual(seal["sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertIsNone(seal["excluded"])
        self.assertEqual(seal["source"], "final message")

    def test_a_written_report_wins_over_the_final_message(self):
        obj = stub(compare.Compare, self.folder)
        report = self.folder / "report.md"
        report.write_text("The report as written.\n", encoding="utf-8")
        Path(f"{report}.last-message.md").write_text("Last message.\n", encoding="utf-8")
        obj.seal("codex", {"dispatch": "d-1", "report": str(report)}, "timeout")
        seal = self.emitted(obj, "compare.seal")[0]
        self.assertEqual((self.folder / "candidate-codex.md").read_text(encoding="utf-8"),
                         "The report as written.\n")
        self.assertEqual(seal["source"], "report")

    def test_without_either_file_the_candidate_is_still_excluded(self):
        obj = stub(compare.Compare, self.folder)
        obj.seal("codex", {"dispatch": "d-1", "report": str(self.folder / "report.md")},
                 "timeout")
        seal = self.emitted(obj, "compare.seal")[0]
        self.assertIsNone(seal["sha256"])
        self.assertIn("timeout", seal["excluded"])


# --- 2. m4: fewer than two valid critiques -----------------------------------------------------

class UnknownDisputeTest(FolderCase):
    def conclude(self, reports, passes, seals=None):
        inv = stub(compare.Investigation, self.folder, kind="investigation")
        seals = seals or {label: {"sha256": label[0] * 64} for label in reports}
        judgement = compare.conclude_investigation(inv, seals, reports, passes, HEADLESS)
        return inv, judgement

    def assert_unknown(self, inv, judgement, valid):
        self.assertIsNone(judgement["answer_disputed"])
        self.assertIsNone(self.emitted(inv, "compare.result")[0]["answer_disputed"])
        mapping = (self.folder / "disagreements.md").read_text(encoding="utf-8")
        self.assertIn(f"unknown: only {valid} valid critique(s)", mapping)
        self.assertNotIn("Neither critique disputes", mapping)
        summary = (self.folder / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("| answer disputed | unknown |", summary)
        self.assertNotIn("| answer disputed | no |", summary)
        self.assertEqual(judgement["confidence"], "low")

    def test_one_invalid_critique_leaves_the_dispute_unknown(self):
        inv, judgement = self.conclude(
            {"claude": REPORT, "codex": REPORT},
            {"critique-claude": critique(False),
             "critique-codex": {"valid": False, "error": "no fenced JSON block"}})
        self.assert_unknown(inv, judgement, 1)

    def test_a_missing_report_leaves_the_dispute_unknown(self):
        inv, judgement = self.conclude(
            {"claude": REPORT}, {"critique-claude": critique(False)},
            seals={"claude": {"sha256": "c" * 64}, "codex": {"excluded": "no report"}})
        self.assert_unknown(inv, judgement, 1)

    def test_no_valid_critique_leaves_the_dispute_unknown(self):
        inv, judgement = self.conclude(
            {"claude": REPORT, "codex": REPORT},
            {"critique-claude": {"valid": False, "error": "x"},
             "critique-codex": {"valid": False, "error": "y"}})
        self.assert_unknown(inv, judgement, 0)

    def test_two_valid_critiques_still_say_no(self):
        inv, judgement = self.conclude(
            {"claude": REPORT, "codex": REPORT},
            {"critique-claude": critique(False), "critique-codex": critique(False)})
        self.assertIs(judgement["answer_disputed"], False)
        summary = (self.folder / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("| answer disputed | no |", summary)


# --- 3. m5: a single memo is not a winner --------------------------------------------------------

class SingleMemoTest(FolderCase):
    def test_one_memo_is_unarbitrated_not_ranked(self):
        obj = stub(compare.Compare, self.folder)
        (self.folder / "candidate-claude.md").write_text("Plan.\n", encoding="utf-8")
        seals = {"claude": {"sha256": "a" * 64},
                 "codex": {"sha256": None, "excluded": "no memo or an empty one"}}
        judgement = compare.conclude(obj, seals, {}, HEADLESS)
        self.assertIsNone(judgement["ranking"])
        self.assertIs(judgement["tie"], False)
        self.assertIsNone(judgement["stable"])
        self.assertEqual(judgement["unarbitrated"], ["claude"])
        self.assertEqual(judgement["confidence"], "low")
        result = self.emitted(obj, "compare.result")[0]
        self.assertIsNone(result.get("ranking"))
        self.assertEqual(result["unarbitrated"], ["claude"])
        summary = (self.folder / "comparison.md").read_text(encoding="utf-8")
        self.assertIn("not arbitrated (one memo)", summary)
        winner_row = next(line for line in summary.splitlines() if line.startswith("| winner |"))
        self.assertNotIn("claude", winner_row)

    def test_two_memos_have_nothing_unarbitrated(self):
        obj = stub(compare.Compare, self.folder)
        seals = {"claude": {"sha256": "a" * 64}, "codex": {"sha256": "b" * 64}}
        entry = {"valid": True, "ranking": ["codex", "claude"], "tie": False,
                 "failed_mandatory": {}, "disagreements": [], "confidence": "high"}
        judgement = compare.conclude(obj, seals, {1: entry, 2: entry}, HEADLESS)
        self.assertEqual(judgement["ranking"], ["codex", "claude"])
        self.assertEqual(judgement["unarbitrated"], [])


# --- 4. M2: snapshot cleanup after the result ---------------------------------------------------

class CleanupFailureTest(FolderCase):
    def test_a_cleanup_that_raises_is_a_note(self):
        run = mock.Mock(run=self.folder, primary=self.folder)
        run.events.return_value = []
        with mock.patch.object(compare.lanes, "snapshot_cleanup", side_effect=OSError("boom")):
            notes = compare.remove_snapshots(run, "cmp-test")
        self.assertTrue(notes)
        self.assertIn("boom", " ".join(notes))


class CompareEndToEndTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo, self.bin = self.temp / "repo", self.temp / "bin"
        self.project = self.temp / "project"
        for directory in (self.repo, self.bin, self.project):
            directory.mkdir()
        self.log = self.temp / "calls.jsonl"
        for name in ("claude", "codex"):
            fake = self.bin / name
            fake.write_text(FAKE_CLI.replace("{python}", sys.executable))
            fake.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "HERDR_", "CLAUDE_CODE_EFFORT",
                                         "HARNESS_PROJECT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_LOG=str(self.log), HARNESS_PROJECT=str(self.project))
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Harness Test"),
                     ("config", "user.email", "harness@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.check_output(["git", *args], cwd=self.repo, env=self.env)
        (self.repo / "README").write_text("x\n")
        subprocess.check_output(["git", "add", "."], cwd=self.repo, env=self.env)
        subprocess.check_output(["git", "commit", "-qm", "baseline"], cwd=self.repo, env=self.env)
        subprocess.run([sys.executable, str(TASK_STATE), "init", "t1"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        self.run_dir = self.repo / ".harness" / "tasks" / "t1"
        self.input = self.temp / "interview.md"
        self.input.write_text(INTERVIEW, encoding="utf-8")
        self.addCleanup(self.unlock)

    def unlock(self):
        for path in self.temp.rglob("locked"):
            for sub in [path, *path.rglob("*")]:
                if sub.is_dir():
                    sub.chmod(0o700)

    def design(self, *extra, timeout="30", **env):
        result = subprocess.run(
            [sys.executable, str(COMPARE), "design", "--task", "t1", "--input", str(self.input),
             "--checkout", str(self.repo), "--timeout", timeout, "--arbiter-timeout", "30",
             *extra], cwd=self.repo, env={**self.env, **env}, capture_output=True, text=True,
            timeout=180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout), result.stderr

    def events(self):
        path = self.run_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def snapshots(self):
        return [Path(e["snapshot"]) for e in self.events()
                if e["type"] == "dispatch.start" and e.get("snapshot")]

    def test_snapshots_are_removed_after_the_result_and_memos_stay(self):
        out, _ = self.design(FAKE_MEMO_claude="Plan alpha.\n", FAKE_MEMO_codex="Plan beta.\n")
        snapshots = self.snapshots()
        self.assertEqual(len(snapshots), 2)
        for snapshot in snapshots:
            self.assertFalse(snapshot.exists(), snapshot)
        folder = self.run_dir / "compare" / out["compare"]
        seals = {e["label"]: e for e in self.events() if e["type"] == "compare.seal"}
        for label in ("claude", "codex"):
            memo = folder / f"candidate-{label}.md"
            self.assertEqual(hashlib.sha256(memo.read_bytes()).hexdigest(),
                             seals[label]["sha256"])

    def test_keep_snapshots_keeps_them(self):
        self.design("--keep-snapshots", FAKE_MEMO_claude="Plan alpha.\n",
                    FAKE_MEMO_codex="Plan beta.\n")
        snapshots = self.snapshots()
        self.assertEqual(len(snapshots), 2)
        for snapshot in snapshots:
            self.assertTrue(snapshot.is_dir(), snapshot)

    def test_a_snapshot_that_cannot_be_removed_is_a_note_not_a_failure(self):
        out, stderr = self.design(FAKE_MEMO_claude="Plan alpha.\n",
                                  FAKE_MEMO_codex="Plan beta.\n", FAKE_LOCK_codex="1")
        self.assertIn("snapshot", stderr)
        self.assertIn("not removed", stderr)
        self.assertTrue((self.run_dir / "compare" / out["compare"] / "judgement.json").is_file())

    def test_a_timed_out_codex_candidate_is_sealed_from_its_final_message(self):
        out, _ = self.design(FAKE_MEMO_claude="Plan alpha.\n",
                             FAKE_MEMO_codex="Plan beta, the final message.\n",
                             FAKE_HANG_codex="1", timeout="4")
        seals = {e["label"]: e for e in self.events() if e["type"] == "compare.seal"}
        self.assertEqual(seals["codex"]["status"], "timeout")
        self.assertEqual(seals["codex"]["source"], "final message")
        folder = self.run_dir / "compare" / out["compare"]
        self.assertEqual((folder / "candidate-codex.md").read_text(encoding="utf-8"),
                         "Plan beta, the final message.\n")
        self.assertEqual(out["excluded"], [])
        self.assertIn("from the final message", (folder / "comparison.md").read_text())

    def test_one_memo_prints_no_ranking(self):
        out, _ = self.design(FAKE_FAIL_codex="1", FAKE_MEMO_claude="Plan alpha.\n")
        self.assertIsNone(out["ranking"])
        self.assertEqual(out["unarbitrated"], ["claude"])


# --- 5. m17: arch_check and unreadable sources ---------------------------------------------------

ARCHITECTURE = {
    "layers": [{"name": "api", "paths": ["src/api/**/*.py"]},
               {"name": "data", "paths": ["src/data/**/*.py"]}],
    "imports": [{"files": "src/**/*.py", "pattern": r"^\s*(?:from|import)\s+(?P<target>[\w.]+)",
                 "resolve": {"app.": "src/"}}],
}


class UnreadableSourceTest(unittest.TestCase):
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
        self.write("harness/project/architecture.json", json.dumps(ARCHITECTURE).encode())
        self.write("src/data/store.py", b"def get():\n    return 1\n")

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def check(self, *args):
        return subprocess.run([sys.executable, str(ARCH_CHECK), "--all", *args], cwd=self.root,
                              env=self.env, capture_output=True, text=True)

    def test_a_non_utf8_source_is_a_finding(self):
        self.write("src/api/latin.py", b"# caf\xe9\nfrom app.data.store import get\n")
        result = self.check("--format", "json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["findings"], [
            {"path": "src/api/latin.py", "line": None, "rule": "unreadable",
             "message": "unreadable, dependencies not checked"}])
        text = self.check()
        self.assertEqual(text.returncode, 1)
        self.assertIn("src/api/latin.py — unreadable, dependencies not checked", text.stdout)

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads any file")
    def test_a_source_that_cannot_be_opened_is_a_finding(self):
        path = self.write("src/api/locked.py", b"from app.data.store import get\n")
        path.chmod(0)
        self.addCleanup(path.chmod, 0o600)
        result = self.check("--format", "json")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual([(f["path"], f["rule"]) for f in json.loads(result.stdout)["findings"]],
                         [("src/api/locked.py", "unreadable")])

    def test_a_readable_clean_source_stays_clean(self):
        self.write("src/api/ok.py", b"import os\n")
        result = self.check("--format", "json")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
