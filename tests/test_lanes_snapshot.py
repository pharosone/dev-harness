"""A candidate (`lanes.py start --capability candidate --scratch DIR`) runs in a snapshot of the
source: an independent clone at DIR/src of the checkout's HEAD with the working-tree changes
applied and the untracked files copied. Its writes land in the snapshot, never in the source;
`lanes.py snapshot-cleanup --task T` removes only the snapshots lanes created.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch come first on
PATH. The CLI fake here records its working directory and, with FAKE_WRITE=PATH, writes
"broken" to PATH relative to it, as a candidate's `printf broken > PATH` would.
"""
import json
import os
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_capability as capability  # noqa: E402
import test_lanes_launch as launch  # noqa: E402

lanes = launch.lanes
WRITES = '''prompt = sys.stdin.read()
if os.environ.get("FAKE_WRITE"):
    os.makedirs(os.path.dirname(os.environ["FAKE_WRITE"]) or ".", exist_ok=True)
    with open(os.environ["FAKE_WRITE"], "w", encoding="utf-8") as target:
        target.write("broken\\n")
'''


class SnapshotTest(unittest.TestCase):
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
    state_file = launch.LaunchTest.state_file
    report_path = capability.CapabilityTest.report_path
    of = capability.CapabilityTest.of

    def setUp(self):
        self.setUp_capability()
        for name in ("claude", "codex"):
            fake = self.bin / name
            source = fake.read_text()
            self.assertIn("prompt = sys.stdin.read()\n", source)
            fake.write_text(source.replace("prompt = sys.stdin.read()\n", WRITES, 1))
        (self.repo / "src").mkdir()
        (self.repo / "src" / "main.py").write_text("print('ok')\n")
        (self.repo / "gone.txt").write_text("tracked, deleted in the working tree\n")
        (self.repo / "logo.bin").write_bytes(bytes(range(256)))
        (self.repo / ".gitignore").write_text("*.log\n.harness/\n")
        self.git("add", ".")
        self.git("commit", "-qm", "source")
        self.snapshot = self.scratch / "src"

    def head(self, cwd=None) -> str:
        return self.git("rev-parse", "HEAD", cwd=cwd).strip()

    # --- the acceptance: a shell write to a source path changes the snapshot ------------

    def test_a_candidate_shell_write_lands_in_the_snapshot_not_the_checkout(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                self.scratch = self.temp / runtime / "scratch"
                self.snapshot = self.scratch / "src"
                out = self.begin("--launcher", "headless", runtime=runtime,
                                 env={"FAKE_WRITE": "src/main.py"})
                self.assertEqual(self.wait(out["dispatch"])["state"], "done")
                self.assertEqual((self.repo / "src" / "main.py").read_text(), "print('ok')\n")
                self.assertEqual((self.snapshot / "src" / "main.py").read_text(), "broken\n")
                self.assertEqual(self.git("status", "--porcelain", "--", "src"), "")
        self.assertEqual([c["cwd"] for c in self.calls()],
                         [str(self.temp / r / "scratch" / "src") for r in ("claude", "codex")])

    def test_uncommitted_and_untracked_changes_are_in_the_snapshot(self):
        (self.repo / "src" / "main.py").write_text("print('edited, not staged')\n")
        (self.repo / "README").write_text("staged\n")
        self.git("add", "README")
        (self.repo / "gone.txt").unlink()
        (self.repo / "logo.bin").write_bytes(bytes(reversed(range(256))))
        (self.repo / "notes").mkdir()
        (self.repo / "notes" / "new.md").write_text("untracked\n")
        (self.repo / "debug.log").write_text("ignored\n")
        out = self.begin("--launcher", "headless")
        self.wait(out["dispatch"])
        self.assertEqual((self.snapshot / "src" / "main.py").read_text(),
                         "print('edited, not staged')\n")
        self.assertEqual((self.snapshot / "README").read_text(), "staged\n")
        self.assertFalse((self.snapshot / "gone.txt").exists())
        self.assertEqual((self.snapshot / "logo.bin").read_bytes(), bytes(reversed(range(256))))
        self.assertEqual((self.snapshot / "notes" / "new.md").read_text(), "untracked\n")
        self.assertFalse((self.snapshot / "debug.log").exists(), "ignored files stay out")
        self.assertFalse((self.snapshot / ".harness").exists(), "run state stays out")
        self.assertEqual(self.head(self.snapshot), self.head())
        self.assertEqual(out["snapshot_base"], self.head())
        # The source is untouched by taking the snapshot.
        self.assertEqual((self.repo / "src" / "main.py").read_text(),
                         "print('edited, not staged')\n")
        self.assertEqual(self.git("diff", "--cached", "--name-only").split(), ["README"])

    def test_the_snapshot_is_an_independent_clone(self):
        out = self.begin("--launcher", "headless")
        self.wait(out["dispatch"])
        self.assertEqual(self.git("remote", cwd=self.snapshot), "")
        self.assertFalse((self.snapshot / ".git" / "objects" / "info" / "alternates").exists())
        common = self.git("rev-parse", "--path-format=absolute", "--git-common-dir",
                          cwd=self.snapshot).strip()
        self.assertEqual(Path(common).resolve(), (self.snapshot / ".git").resolve())
        worktrees = [line for line in self.git("worktree", "list", "--porcelain").splitlines()
                     if line.startswith("worktree ")]
        self.assertEqual(worktrees, [f"worktree {self.repo}"], "the source gains no worktree")
        before = self.head()
        self.git("checkout", "-qB", "main", cwd=self.snapshot)
        self.git("-c", "user.name=c", "-c", "user.email=c@example.invalid", "commit", "-q",
                 "--allow-empty", "-m", "candidate", cwd=self.snapshot)
        self.assertEqual(self.head(), before, "a branch moved in the snapshot is not the source's")
        self.assertEqual(self.git("rev-parse", "main").strip(), before)

    # --- the command lines -------------------------------------------------------------

    def test_headless_codex_runs_in_the_snapshot_without_a_repository_check(self):
        out = self.begin("--launcher", "headless", runtime="codex")
        self.wait(out["dispatch"])
        (call,) = self.calls()
        argv = call["argv"]
        self.assertEqual(argv[argv.index("-C") + 1], str(self.snapshot))
        self.assertIn("--skip-git-repo-check", argv)
        self.assertEqual(argv[argv.index("--sandbox") + 1], "workspace-write")

    def test_claude_candidates_run_bash_in_the_fail_closed_sandbox(self):
        out = self.begin("--launcher", "headless")
        self.wait(out["dispatch"])
        (call,) = self.calls()
        argv = call["argv"]
        settings = json.loads(argv[argv.index("--settings") + 1])
        self.assertEqual(settings, {"sandbox": {"enabled": True, "failIfUnavailable": True,
                                                "allowUnsandboxedCommands": False,
                                                "filesystem": {"denyWrite": [str(self.repo)]}}})
        self.assertIn("Bash", argv)
        self.scratch = self.temp / "pane" / "scratch"
        self.begin("--launcher", "pane", work="U10", env=self.herdr_env())
        [started] = self.of(["agent", "start"])
        passed = started[started.index("--") + 1:]
        self.assertEqual(json.loads(passed[passed.index("--settings") + 1]), settings)

    # --- where the scratch may be --------------------------------------------------------

    def test_a_scratch_inside_the_source_is_refused_outside_its_run_state(self):
        for inside in (self.repo / "src", self.repo / "work" / "scratch", self.repo / ".git" / "x",
                       self.repo / ".harnessed"):
            result = self.begin("--launcher", "headless", "--capability", "candidate",
                                "--scratch", str(inside), capability=False, expect=2)
            self.assertIn("scratch", result.stderr, inside)
            self.assertFalse((inside / "src").exists(), inside)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.calls(), [])
        self.assertEqual((self.repo / "src" / "main.py").read_text(), "print('ok')\n")

    def test_a_scratch_under_the_source_run_state_is_accepted(self):
        self.scratch = self.repo / ".harness" / "compare" / "A" / "scratch"
        self.snapshot = self.scratch / "src"
        out = self.begin("--launcher", "headless", env={"FAKE_WRITE": "src/main.py"})
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        self.assertEqual(out["snapshot"], str(self.snapshot))
        self.assertEqual((self.snapshot / "src" / "main.py").read_text(), "broken\n")
        self.assertEqual((self.repo / "src" / "main.py").read_text(), "print('ok')\n")

    def test_an_existing_snapshot_is_never_reused(self):
        (self.snapshot).mkdir(parents=True)
        (self.snapshot / "keep").write_text("someone's\n")
        result = self.begin("--launcher", "headless", expect=2)
        self.assertIn("snapshot", result.stderr)
        self.assertEqual((self.snapshot / "keep").read_text(), "someone's\n")
        self.assertEqual(self.events(), [])

    # --- the snapshot outlives the dispatch; cleanup removes only lanes' own -------------

    def test_cancel_leaves_the_snapshot_in_place(self):
        out = self.begin("--launcher", "headless", env={"FAKE_SLEEP": "30"})
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "cancelled")
        self.assertTrue((self.snapshot / "src" / "main.py").is_file())

    def test_snapshot_cleanup_removes_only_snapshots_lanes_created(self):
        done = self.begin("--launcher", "headless", work="A")
        self.wait(done["dispatch"])
        self.scratch = self.temp / "compare" / "B" / "scratch"
        tampered = self.begin("--launcher", "headless", work="B")
        self.wait(tampered["dispatch"])
        own_b = Path(tampered["snapshot"])
        path = self.run_dir / "lanes" / f"{tampered['dispatch']}.json"
        state = json.loads(path.read_text())
        state["snapshot"] = str(self.repo)  # a state that points at the source itself
        path.write_text(json.dumps(state))
        self.scratch = self.temp / "compare" / "C" / "scratch"
        running = self.begin("--launcher", "headless", work="C", env={"FAKE_SLEEP": "30"})
        plain = self.begin("--launcher", "headless", work="D", capability=False)
        self.wait(plain["dispatch"])
        decoy = self.temp / "decoy" / "src"
        decoy.mkdir(parents=True)
        self.git("init", "-q", cwd=decoy)

        result = self.lanes("snapshot-cleanup", "--task", "t1")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        rows = {row["dispatch"]: row for row in json.loads(result.stdout)}
        self.assertEqual(set(rows), {done["dispatch"], tampered["dispatch"],
                                     running["dispatch"]})
        self.assertIs(rows[done["dispatch"]]["removed"], True)
        self.assertFalse(Path(done["snapshot"]).exists())
        self.assertIs(rows[tampered["dispatch"]]["removed"], False)
        self.assertIs(rows[running["dispatch"]]["removed"], False)
        self.assertTrue(Path(running["snapshot"]).is_dir(), "a running candidate keeps it")
        self.assertTrue((self.repo / "src" / "main.py").is_file())
        self.assertTrue((self.repo / ".git").is_dir())
        self.assertTrue(own_b.is_dir(), "a path the state no longer names is not lanes' to remove")
        self.assertTrue(decoy.is_dir())
        self.assertEqual(self.git("status", "--porcelain", "--", "src", "README"), "")

        again = self.lanes("snapshot-cleanup", "--task", "t1")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        rows = {row["dispatch"]: row for row in json.loads(again.stdout)}
        self.assertIs(rows[done["dispatch"]]["removed"], False)


if __name__ == "__main__":
    unittest.main()
