"""Exercise durable state and frozen-test evidence across real Git worktrees."""
from datetime import datetime, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

try:
    import fcntl
except ImportError:  # Windows: the events log is appended without a lock.
    fcntl = None

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/task_state.py"


class TaskStateTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.root / "spec.md").write_text("# Approved spec\n")
        (self.root / "tests").mkdir()
        (self.root / "tests/test one.py").write_text("assert 1 == 1\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.worktree = Path(self.temp.name) / "lane"
        self.git("worktree", "add", "--detach", str(self.worktree), "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env, text=True)

    def run_state(self, *args, cwd=None):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=cwd or self.root,
                              env=self.env, capture_output=True, text=True)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def red_log(self, text="--- FAIL: TestOne (0.00s)\n", name="red.log"):
        path = Path(self.temp.name) / name
        path.write_text(text)
        return path

    def freeze(self, *args, cwd=None, red=None):
        return self.run_state("freeze", *args, "--red-log", str(red or self.red_log()), cwd=cwd)

    def test_new_state_is_in_primary_checkout_from_either_worktree(self):
        path = Path(self.ok(self.run_state("init", "feature", "--spec", "spec.md", cwd=self.worktree)))
        self.assertEqual(path, self.root.resolve() / ".harness/tasks/feature")
        self.assertTrue((path / "run.md").is_file())
        self.assertEqual(self.ok(self.run_state("locate", "feature")), str(path))
        ledger = (path / "run.md").read_text()
        self.assertIn(self.git("rev-parse", "HEAD").strip(), ledger)
        self.assertIn(str(self.worktree.resolve()), ledger)
        (path / "run.md").write_text(ledger + "\nUnit 1 done\n")
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.assertIn("Unit 1 done", (path / "run.md").read_text())
        self.git("worktree", "remove", str(self.worktree))
        self.assertTrue((path / "run.md").is_file())

    def test_init_without_a_spec_starts_an_investigation_or_discovery_run(self):
        path = Path(self.ok(self.run_state("init", "question")))
        ledger = (path / "run.md").read_text()
        self.assertIn("- Spec: none yet", ledger)
        self.assertIn(self.git("rev-parse", "HEAD").strip(), ledger)

    def test_legacy_run_is_resumed_without_copying(self):
        legacy = self.root / ".claude/tasks/feature"
        legacy.mkdir(parents=True)
        (legacy / "run.md").write_text("old evidence\n")
        self.assertEqual(Path(self.ok(self.run_state("locate", "feature", cwd=self.worktree))), legacy.resolve())
        self.ok(self.run_state("init", "feature", "--spec", "spec.md", cwd=self.worktree))
        self.assertEqual((legacy / "run.md").read_text(), "old evidence\n")
        self.assertFalse((self.root / ".harness/tasks/feature").exists())

    def test_two_run_directories_are_not_silently_merged(self):
        for prefix in (".claude", ".harness"):
            (self.root / prefix / "tasks/feature").mkdir(parents=True)
        result = self.run_state("locate", "feature")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("both", result.stderr.lower())

    def test_frozen_tests_detect_edits_deletion_and_refuse_overwrite(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.ok(self.freeze("feature", "01", "tests/test one.py", cwd=self.worktree))
        self.ok(self.run_state("verify", "feature", "01"))
        manifest = self.root / ".harness/tasks/feature/units/01-tests.json"
        before = manifest.read_bytes()
        self.assertIn("tests/test one.py", manifest.read_text())
        (self.worktree / "tests/test one.py").write_text("assert True\n")
        result = self.run_state("verify", "feature", "01", cwd=self.worktree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tests/test one.py", result.stderr)
        self.assertNotEqual(self.freeze("feature", "01", "tests/test one.py", cwd=self.worktree).returncode, 0)
        self.assertEqual(before, manifest.read_bytes())
        (self.worktree / "tests/test one.py").unlink()
        self.assertNotEqual(self.run_state("verify", "feature", "01", cwd=self.worktree).returncode, 0)

    def test_invalid_identifiers_and_paths_cannot_escape(self):
        for task in ("../escape", "/tmp/escape", ".", "a/b"):
            self.assertNotEqual(self.run_state("init", task, "--spec", "spec.md").returncode, 0)
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.assertNotEqual(self.freeze("feature", "../escape", "spec.md").returncode, 0)
        self.assertNotEqual(self.freeze("feature", "01", "../outside").returncode, 0)
        self.assertNotEqual(self.run_state("verify", "feature", "missing").returncode, 0)

    def test_tampered_manifest_is_not_success(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.ok(self.freeze("feature", "01", "tests/test one.py"))
        manifest = self.root / ".harness/tasks/feature/units/01-tests.json"
        manifest.write_text(json.dumps({"files": {}}))
        self.assertNotEqual(self.run_state("verify", "feature", "01").returncode, 0)

    def test_freeze_requires_a_red_log(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        result = self.run_state("freeze", "feature", "01", "tests/test one.py")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--red-log", result.stderr)
        self.assertFalse((self.root / ".harness/tasks/feature/units/01-tests.json").exists())

    def test_freeze_rejects_a_missing_empty_or_passing_red_log(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        manifest = self.root / ".harness/tasks/feature/units/01-tests.json"
        result = self.freeze("feature", "01", "tests/test one.py", red=Path(self.temp.name) / "absent.log")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("absent.log", result.stderr)
        for text in ("", "  \n\n"):
            result = self.freeze("feature", "01", "tests/test one.py", red=self.red_log(text))
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("empty", result.stderr.lower())
        # Look-alikes: passing output, a mid-line FAIL, lower-case "tests ... failed".
        passing = ("ok  \tpkg\t0.12s\nPASS (no FAIL)\n12 passed, 0 skipped\n"
                   "all tests passed, 0 failed\nno errors found\n")
        result = self.freeze("feature", "01", "tests/test one.py", red=self.red_log(passing))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("red log shows no failing test", result.stderr)
        # A passing run whose test names mention an error type is still a pass.
        named = ("test_raises_AssertionError (tests.test_x) ... ok\n"
                 "test_maps_ValueError_to_422 ... ok\n\nRan 2 tests in 0.01s\n\nOK\n")
        result = self.freeze("feature", "01", "tests/test one.py", red=self.red_log(named, name="named.log"))
        self.assertIn("red log shows no failing test", result.stderr)
        self.assertFalse(manifest.exists())

    def test_each_kind_of_failure_evidence_freezes_with_its_hash(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        samples = (
            "--- FAIL: TestExport (0.00s)\n",
            "ok\nFAIL\texample.com/app/x\t0.01s\n",
            "tests/test_x.py::test_y FAILED\n",
            "./export_test.go:3:2: undefined: Export\n",
            "cannot find package \"x\" in any of\n",
            "TypeError: add is not a function\n",
            "src/a.ts(1,1): error: TS2304\n",
            "raise AssertionError\n",
            "Traceback (most recent call last)\n",
            " ✗ renders the page\n",
            " × renders the page\n",
            " Tests  1 failed | 3 passed\n",
            "Test suite failed to run\n",
        )
        for index, text in enumerate(samples):
            with self.subTest(text=text):
                red = self.red_log(text, name=f"red-{index}.log")
                self.ok(self.freeze("feature", f"e{index}", "tests/test one.py", red=red))
                data = json.loads((self.root / f".harness/tasks/feature/units/e{index}-tests.json").read_text())
                self.assertEqual(data["version"], 1)
                self.assertEqual(list(data["files"]), ["tests/test one.py"])
                self.assertEqual(data["red_log"], {
                    "path": str(red.resolve()),
                    "sha256": hashlib.sha256(red.read_bytes()).hexdigest(),
                })

    def test_red_log_path_is_resolved_and_ignored_by_verify(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        red = self.worktree / "logs/red.txt"
        red.parent.mkdir()
        red.write_text("FAIL: test_one (tests.test_one)\nAssertionError: 1 != 2\n")
        self.ok(self.run_state("freeze", "feature", "01", "tests/test one.py",
                               "--red-log", "../logs/red.txt", cwd=self.worktree / "tests"))
        manifest = self.root / ".harness/tasks/feature/units/01-tests.json"
        self.assertEqual(json.loads(manifest.read_text())["red_log"]["path"], str(red.resolve()))
        red.write_text("rewritten after green\n")
        self.ok(self.run_state("verify", "feature", "01", cwd=self.worktree))
        red.unlink()
        self.ok(self.run_state("verify", "feature", "01"))
        (self.worktree / "tests/test one.py").write_text("assert True\n")
        self.assertNotEqual(self.run_state("verify", "feature", "01", cwd=self.worktree).returncode, 0)

    def legacy_tests(self):
        legacy = self.root / ".claude/tasks/feature"
        units = legacy / "units"
        units.mkdir(parents=True)
        (legacy / "run.md").write_text("legacy red evidence\n")
        names = ["tests/test one.py", "spec.md"]
        snapshot = units / "01-tests.sha"
        snapshot.write_text(self.git("hash-object", *names))
        return snapshot, names

    def test_legacy_freeze_is_blocked_and_verify_explains_import(self):
        snapshot, names = self.legacy_tests()
        before = snapshot.read_bytes()
        result = self.freeze("feature", "01", *names)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("import-legacy", result.stderr)
        result = self.run_state("verify", "feature", "01", cwd=self.worktree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("import-legacy feature 01", result.stderr)
        self.assertFalse(snapshot.with_suffix(".json").exists())
        self.assertEqual(snapshot.read_bytes(), before)

    def test_legacy_import_preserves_baseline_and_verifies_across_worktrees(self):
        snapshot, names = self.legacy_tests()
        before = snapshot.read_bytes()
        self.ok(self.run_state("import-legacy", "feature", "01", *names, cwd=self.worktree / "tests"))
        manifest = snapshot.with_suffix(".json")
        frozen = manifest.read_bytes()
        self.assertEqual(list(json.loads(frozen)["files"]), sorted(names))
        self.assertNotIn("red_log", json.loads(frozen))
        self.ok(self.run_state("verify", "feature", "01"))
        (self.worktree / names[0]).write_text("assert True\n")
        result = self.run_state("verify", "feature", "01", cwd=self.worktree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(names[0], result.stderr)
        self.assertNotEqual(self.run_state("import-legacy", "feature", "01", *names).returncode, 0)
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertEqual(manifest.read_bytes(), frozen)

    def test_legacy_import_rejects_wrong_order_count_and_changed_tests(self):
        snapshot, names = self.legacy_tests()
        before = snapshot.read_bytes()
        for supplied in (names[::-1], names[:1], [*names, names[0]]):
            result = self.run_state("import-legacy", "feature", "01", *supplied)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("legacy", result.stderr.lower())
            self.assertFalse(snapshot.with_suffix(".json").exists())
        (self.worktree / names[0]).write_text("assert True\n")
        result = self.run_state("import-legacy", "feature", "01", *names, cwd=self.worktree)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(names[0], result.stderr)
        self.assertEqual(snapshot.read_bytes(), before)
        self.assertFalse(snapshot.with_suffix(".json").exists())

    def test_legacy_import_rejects_malformed_and_symlinked_evidence(self):
        snapshot, names = self.legacy_tests()
        for content in ("", "garbage\n", self.git("hash-object", names[0]) + "\n\n"):
            snapshot.write_text(content)
            result = self.run_state("import-legacy", "feature", "01", *names)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("legacy", result.stderr.lower())
            self.assertFalse(snapshot.with_suffix(".json").exists())
        snapshot.unlink()
        snapshot.symlink_to(self.root / "spec.md")
        result = self.run_state("import-legacy", "feature", "01", *names)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("symlink", result.stderr.lower())


    # Events log: `event <task> <type> --data JSON [--id ID]` and read_events(run).

    def module(self):
        spec = importlib.util.spec_from_file_location("task_state_under_test", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def events_file(self, task="feature"):
        return self.root / ".harness/tasks" / task / "events.jsonl"

    def lines(self, task="feature"):
        return [json.loads(line) for line in self.events_file(task).read_text().splitlines()]

    def event(self, *args, data="{}", cwd=None):
        return self.run_state("event", "feature", *args, "--data", data, cwd=cwd)

    def test_event_appends_one_line_with_envelope_first_and_prints_the_id(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        data = {"dispatch": "d1", "role": "implementer", "usage": {"tool_calls": None}}
        printed = self.ok(self.event("dispatch.start", data=json.dumps(data), cwd=self.worktree))
        self.assertRegex(printed, r"^[0-9a-f]{32}$")
        events = self.lines()
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(list(event)[:4], ["v", "id", "at", "type"])
        self.assertEqual(event["v"], 1)
        self.assertEqual(event["id"], printed)
        self.assertEqual(event["type"], "dispatch.start")
        self.assertEqual({k: event[k] for k in list(event)[4:]}, data)
        self.assertRegex(event["at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:Z|\+00:00)$")
        at = datetime.fromisoformat(event["at"])
        self.assertEqual(at.utcoffset(), timedelta(0))
        self.assertLess(abs((datetime.now(at.tzinfo) - at).total_seconds()), 120)
        second = self.ok(self.event("run.start", "--id", "explicit-1"))
        self.assertEqual(second, "explicit-1")
        self.assertEqual([e["id"] for e in self.lines()], [printed, "explicit-1"])
        self.assertTrue(self.events_file().read_text().endswith("\n"))

    def test_event_with_a_known_id_is_skipped_as_duplicate(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.ok(self.event("gate.open", "--id", "hook-7", data='{"gate": "G1"}'))
        before = self.events_file().read_bytes()
        result = self.event("gate.open", "--id", "hook-7", data='{"gate": "other"}', cwd=self.worktree)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "duplicate")
        self.assertEqual(self.events_file().read_bytes(), before)
        self.assertEqual(self.lines()[0]["gate"], "G1")

    def test_event_requires_an_initialized_run(self):
        result = self.event("run.start")
        self.assertEqual(result.returncode, 1)
        self.assertIn("init first", result.stderr)
        self.assertFalse((self.root / ".harness/tasks/feature").exists())

    def test_event_data_and_type_errors_exit_2_and_write_nothing(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        bad_data = ("{bad", "[]", '"text"', "null", "1",
                    '{"v": 2}', '{"id": "x"}', '{"at": "now"}', '{"type": "run.start"}')
        for data in bad_data:
            with self.subTest(data=data):
                result = self.event("run.start", data=data)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn("task-state:", result.stderr)
        for kind in ("Run.start", "run.", ".run", "run..start", "run_start", "run.start1", "", "run start"):
            with self.subTest(type=kind):
                result = self.event(kind)
                self.assertEqual(result.returncode, 2, result.stderr)
        self.assertFalse(self.events_file().exists())

    def test_read_events_skips_and_counts_unreadable_lines_in_file_order(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        run = self.root / ".harness/tasks/feature"
        read_events = self.module().read_events
        self.assertEqual(read_events(run), ([], 0))
        first = {"v": 1, "id": "a", "at": "2026-10-09T00:00:00Z", "type": "run.start"}
        last = {"v": 1, "id": "b", "at": "2026-10-09T00:00:01Z", "type": "run.end"}
        self.events_file().write_bytes(
            (json.dumps(first) + "\n[1, 2]\n\"text\"\n").encode()
            + b"\xff\xfe not utf-8\n" + (json.dumps(last) + "\n").encode()
            + b'{"v": 1, "id": "cut sh')
        self.assertEqual(read_events(run), ([first, last], 4))

    def test_event_after_a_cut_short_line_starts_a_new_line(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.events_file().write_text('{"v": 1, "id": "a"}\n{"v": 1, "id": "cut sh')
        printed = self.ok(self.event("run.start", data='{"depth": "full"}'))
        events, unreadable = self.module().read_events(self.root / ".harness/tasks/feature")
        self.assertEqual(unreadable, 1)
        self.assertEqual([e["id"] for e in events], ["a", printed])
        self.assertEqual(events[1]["depth"], "full")

    @unittest.skipIf(fcntl is None, "the events log is not locked on Windows")
    def test_event_waits_for_the_exclusive_lock_on_the_events_file(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        self.events_file().touch()
        with self.events_file().open("a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            writer = subprocess.Popen(
                [sys.executable, str(SCRIPT), "event", "feature", "run.start", "--data", "{}",
                 "--id", "locked"], cwd=self.root, env=self.env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(writer.kill)
            time.sleep(1.5)
            self.assertIsNone(writer.poll(), "event wrote while another process held the lock")
            self.assertEqual(self.events_file().read_text(), "")
            fcntl.flock(held, fcntl.LOCK_UN)
        out, err = writer.communicate(timeout=30)
        self.assertEqual(writer.returncode, 0, err)
        self.assertEqual(out.strip(), "locked")
        self.assertEqual([e["id"] for e in self.lines()], ["locked"])

    def test_concurrent_writers_lose_nothing_and_write_only_valid_lines(self):
        self.ok(self.run_state("init", "feature", "--spec", "spec.md"))
        writers, per_writer = 8, 25
        loop = (
            "import json, subprocess, sys\n"
            "script, w, n = sys.argv[1], sys.argv[2], int(sys.argv[3])\n"
            "pad = 'x' * 6000\n"
            "ids = [f'w{w}-{i}' for i in range(n)]\n"
            "ids.insert(n // 2, 'shared')\n"
            "for event_id in ids:\n"
            "    data = json.dumps({'writer': w, 'pad': pad})\n"
            "    subprocess.run([sys.executable, script, 'event', 'feature', 'load.test',\n"
            "                    '--data', data, '--id', event_id], check=True,\n"
            "                   stdout=subprocess.DEVNULL)\n"
        )
        procs = [subprocess.Popen([sys.executable, "-c", loop, str(SCRIPT), str(w), str(per_writer)],
                                  cwd=self.worktree if w % 2 else self.root, env=self.env,
                                  stderr=subprocess.PIPE, text=True)
                 for w in range(writers)]
        for proc in procs:
            _, err = proc.communicate(timeout=300)
            self.assertEqual(proc.returncode, 0, err)
        raw = self.events_file().read_text().split("\n")
        self.assertEqual(raw[-1], "")
        parsed = [json.loads(line) for line in raw[:-1]]
        self.assertTrue(all(isinstance(e, dict) for e in parsed))
        events, unreadable = self.module().read_events(self.root / ".harness/tasks/feature")
        self.assertEqual(unreadable, 0)
        self.assertEqual(events, parsed)
        ids = [e["id"] for e in events]
        expected = {f"w{w}-{i}" for w in range(writers) for i in range(per_writer)} | {"shared"}
        self.assertEqual(len(ids), len(expected))
        self.assertEqual(set(ids), expected)
        for e in events:
            self.assertEqual(e["pad"], "x" * 6000)
        for w in range(writers):
            own = [int(i.split("-")[1]) for i in ids if i.startswith(f"w{w}-")]
            self.assertEqual(own, sorted(own))


if __name__ == "__main__":
    unittest.main()
