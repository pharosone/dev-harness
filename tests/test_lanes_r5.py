"""R5 cross-vendor review fixes of the candidate capability (`lanes.py start --capability
candidate --scratch DIR`):

1. A Claude candidate loads no user settings (`--setting-sources project,local`), so a user's
   `sandbox.filesystem.allowWrite` cannot reopen the source, and its sandbox settings deny
   writes to the source checkout and to the run directory outside the scratch.
2. A Codex candidate can write its scratch (`--add-dir <scratch>`), where its report lives,
   headless and in a pane.
3. A snapshot carries the working-tree files of the source's initialized submodules, never
   fetched over the network.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch (herdr behind
the wrapper of test_m1_review_fixes) come first on PATH and record their argv.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_capability as capability  # noqa: E402
import test_lanes_launch as launch  # noqa: E402

lanes = launch.lanes
BASE_SANDBOX = {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False}


class R5Test(unittest.TestCase):
    setUp_capability = capability.CapabilityTest.setUp
    setUp_fixes = capability.CapabilityTest.setUp_fixes
    setUp_launch = capability.CapabilityTest.setUp_launch
    begin = capability.CapabilityTest.begin
    guard_hook = capability.CapabilityTest.guard_hook
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

    def settings(self, argv):
        return json.loads(argv[argv.index("--settings") + 1])

    def deny(self, argv):
        settings = self.settings(argv)
        self.assertEqual({k: v for k, v in settings["sandbox"].items() if k != "filesystem"},
                         BASE_SANDBOX)
        self.assertEqual(set(settings["sandbox"]["filesystem"]), {"denyWrite"})
        return settings["sandbox"]["filesystem"]["denyWrite"]

    # --- 1. no user settings; the source and the run directory are write-denied ----------

    def test_headless_claude_candidate_loads_no_user_settings_and_denies_the_source(self):
        out = self.begin("--launcher", "headless")
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        (call,) = self.calls()
        argv = call["argv"]
        scratch = str(self.scratch)
        settings = {"sandbox": {**BASE_SANDBOX, "filesystem": {"denyWrite": [str(self.repo)]}}}
        self.assertEqual(argv, [
            "claude", "-p", "--output-format", "stream-json", "--verbose", "--model", "model-x",
            "--permission-mode", "dontAsk", "--allowedTools", "Read", "Grep", "Glob", "Bash",
            f"Write(/{scratch}/**)", f"Edit(/{scratch}/**)", "--add-dir", scratch,
            "--setting-sources", "project,local", "--settings", argv[-1]])
        self.assertEqual(json.loads(argv[-1]), settings)
        self.assertNotIn("user", argv[argv.index("--setting-sources") + 1].split(","))

    def test_pane_claude_candidate_passes_the_same_settings_through_herdr(self):
        self.begin("--launcher", "pane", env=self.herdr_env())
        [started] = self.of(["agent", "start"])
        passed = started[started.index("--") + 1:]
        scratch = str(self.scratch)
        self.assertEqual(passed[:-1], [
            "--model", "model-x", "--permission-mode", "dontAsk", "--allowedTools", "Read",
            "Grep", "Glob", "Bash", f"Write(/{scratch}/**)", f"Edit(/{scratch}/**)",
            "--add-dir", scratch, "--setting-sources", "project,local", "--settings"])
        self.assertEqual(self.deny(passed), [str(self.repo)])

    def test_a_scratch_inside_the_run_directory_is_writable_and_everything_beside_it_denied(self):
        private = self.run_dir / "compare" / "c1" / "private"
        rival = private / "B" / "scratch"
        rival.mkdir(parents=True)
        (self.run_dir / "compare" / "c1" / "candidate-B.md").write_text("rival\n")
        self.scratch = private / "A" / "scratch"
        self.snapshot = self.scratch / "src"
        out = self.begin("--launcher", "headless")
        self.wait(out["dispatch"])
        (call,) = self.calls()
        denied = [Path(p) for p in self.deny(call["argv"])]
        self.assertEqual(len(denied), len(set(denied)))
        for path in denied:
            self.assertTrue(path.is_absolute(), path)
            self.assertNotEqual(path, self.scratch)
            self.assertNotIn(path, self.scratch.parents, "an ancestor of the scratch is denied")
            self.assertNotIn(self.scratch, path.parents, "a path inside the scratch is denied")
        for path in (self.repo / "README", self.repo / ".git", self.run_dir / "run.md",
                     private / "B", self.run_dir / "compare" / "c1" / "candidate-B.md"):
            self.assertIn(path, denied)
        self.assertNotIn(self.repo, denied)
        self.assertNotIn(self.run_dir, denied)

    def test_a_scratch_outside_the_repository_denies_a_run_directory_elsewhere_whole(self):
        roots = lanes.write_denials(self.scratch, [self.repo, self.temp / "elsewhere" / "run",
                                                   self.repo / ".git"])
        self.assertEqual(roots, [str(self.repo), str(self.temp / "elsewhere" / "run")])

    def test_the_docstring_says_user_settings_are_excluded_and_managed_ones_still_apply(self):
        doc = " ".join(lanes.__doc__.split())
        self.assertIn("--setting-sources project,local", doc)
        self.assertIn("denyWrite", doc)
        self.assertIn("managed", doc.lower())
        self.assertIn("cannot be excluded", doc)

    # --- 2. Codex writes its scratch -----------------------------------------------------

    def test_headless_codex_candidate_can_write_its_scratch(self):
        out = self.begin("--launcher", "headless", runtime="codex")
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        (call,) = self.calls()
        argv = call["argv"]
        hooks = self.guard_hook(argv)
        self.assertEqual(argv, ["codex", "exec", "--json", "--sandbox", "workspace-write",
                                "-m", "model-x", "-c", "model_reasoning_effort=high", "-c", hooks,
                                "-C", str(self.snapshot), "--add-dir", str(self.scratch),
                                "--skip-git-repo-check", "--output-last-message",
                                f"{self.report_path()}.last-message.md", "-"])

    def test_pane_codex_candidate_can_write_its_scratch(self):
        self.begin("--launcher", "pane", runtime="codex", env=self.herdr_env())
        [started] = self.of(["agent", "start"])
        passed = started[started.index("--") + 1:]
        hooks = self.guard_hook(passed)
        self.assertEqual(passed, ["-m", "model-x", "-c", "model_reasoning_effort=high", "-c",
                                  hooks, "--sandbox", "workspace-write", "-C",
                                  str(self.snapshot), "--add-dir", str(self.scratch)])

    def test_a_codex_worker_without_the_capability_gets_no_extra_directory(self):
        out = self.begin("--launcher", "headless", runtime="codex", capability=False)
        self.wait(out["dispatch"])
        (call,) = self.calls()
        self.assertNotIn("--add-dir", call["argv"])

    # --- 3. submodules ---------------------------------------------------------------------

    def make_repo(self, path: Path, files: dict) -> Path:
        path.mkdir(parents=True)
        self.git("init", "-q", "-b", "main", cwd=path)
        for name, text in files.items():
            (path / name).parent.mkdir(parents=True, exist_ok=True)
            (path / name).write_text(text)
        self.git("add", ".", cwd=path)
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm",
                 "init", cwd=path)
        return path

    def add_submodule(self, origin: Path, where: str, cwd: Path) -> None:
        self.git("-c", "protocol.file.allow=always", "submodule", "add", "-q", origin.as_uri(),
                 where, cwd=cwd)

    def commit(self, message: str, cwd: Path) -> None:
        self.git("-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm",
                 message, cwd=cwd)

    def test_initialized_submodules_are_copied_into_the_snapshot_without_a_fetch(self):
        inner = self.make_repo(self.temp / "origins" / "inner", {"inner.h": "int inner;\n"})
        lib = self.make_repo(self.temp / "origins" / "lib",
                             {"header.h": "int lib;\n", ".gitignore": "*.o\n"})
        self.add_submodule(inner, "deep", lib)
        self.commit("nested", lib)
        other = self.make_repo(self.temp / "origins" / "other", {"other.h": "int other;\n"})
        self.add_submodule(lib, "vendor/lib", self.repo)
        self.git("-c", "protocol.file.allow=always", "submodule", "update", "--init",
                 "--recursive", "-q")
        self.add_submodule(other, "vendor/other", self.repo)
        self.commit("submodules", self.repo)
        # vendor/other is declared but not initialized, and its URL cannot be fetched.
        self.git("submodule", "deinit", "-q", "-f", "vendor/other")
        self.git("config", "-f", ".gitmodules", "submodule.vendor/other.url",
                 "https://example.invalid/other.git")
        self.git("config", "submodule.vendor/other.url", "https://example.invalid/other.git")
        work = self.repo / "vendor" / "lib"
        (work / "header.h").write_text("int lib_changed;\n")  # a dirty tracked file
        (work / "notes.txt").write_text("untracked, not ignored\n")
        (work / "build.o").write_text("ignored\n")
        (work / "added.h").write_text("committed in the submodule only\n")
        self.git("add", "added.h", cwd=work)
        self.commit("moves the gitlink", work)

        out = self.begin("--launcher", "headless")
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        copy = self.snapshot / "vendor" / "lib"
        self.assertEqual((copy / "header.h").read_text(), "int lib_changed;\n")
        self.assertEqual((copy / "notes.txt").read_text(), "untracked, not ignored\n")
        self.assertEqual((copy / "added.h").read_text(), "committed in the submodule only\n")
        self.assertEqual((copy / ".gitignore").read_text(), "*.o\n")
        self.assertEqual((copy / "deep" / "inner.h").read_text(), "int inner;\n")
        self.assertFalse((copy / "build.o").exists(), "ignored files stay out")
        self.assertFalse((copy / ".git").exists(), "the submodule's .git is not copied")
        self.assertFalse((copy / "deep" / ".git").exists())
        self.assertEqual(list((self.snapshot / "vendor" / "other").iterdir()), [],
                         "an uninitialized submodule stays empty")
        self.assertEqual((work / "header.h").read_text(), "int lib_changed;\n")


if __name__ == "__main__":
    unittest.main()
