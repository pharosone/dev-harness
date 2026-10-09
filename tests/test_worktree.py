"""Lane worktrees live under the primary checkout's .claude/worktrees and are never removed dirty."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

WORKTREE = Path(__file__).resolve().parents[1] / "scripts" / "worktree.py"
FIXTURE = Path(__file__).resolve().parent / "fixture"  # worktree.node_dirs: web, packages/ui

FAKE_NPM = """#!/bin/sh
printf '%s %s\\n' "$(pwd -P)" "$*" >> "$FAKE_NPM_MARKER"
echo "added 3 packages"
exit "${FAKE_NPM_EXIT:-0}"
"""


class WorktreeTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.root = self.temp / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        HARNESS_PROJECT=str(FIXTURE))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.root / ".gitignore").write_text(".claude/worktrees/\n.harness/\nnode_modules/\n")
        for folder in ("web", "packages/ui"):
            (self.root / folder).mkdir(parents=True)
            (self.root / folder / "package.json").write_text('{"name": "x"}\n')
        self.git("add", ".")
        self.git("commit", "-qm", "first")
        self.first = self.git("rev-parse", "HEAD")
        (self.root / "README.md").write_text("# repo\n")
        self.git("add", "README.md")
        self.git("commit", "-qm", "second")
        self.head = self.git("rev-parse", "HEAD")
        self.lanes = self.root / ".claude" / "worktrees"

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.root, env=self.env,
                                       text=True).strip()

    def worktree(self, *args, cwd=None, env=None):
        return subprocess.run([sys.executable, str(WORKTREE), *args], cwd=cwd or self.root,
                              env=env or self.env, capture_output=True, text=True)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def registered(self):
        listing = self.git("worktree", "list", "--porcelain")
        return {Path(line[len("worktree "):]).resolve()
                for line in listing.splitlines() if line.startswith("worktree ")}

    def fake_npm(self, exit_code=0):
        bin_dir = self.temp / "bin"
        bin_dir.mkdir(exist_ok=True)
        npm = bin_dir / "npm"
        npm.write_text(FAKE_NPM)
        npm.chmod(0o755)
        marker = self.temp / "npm-called"
        env = dict(self.env, PATH=f"{bin_dir}{os.pathsep}{self.env.get('PATH', '')}",
                   FAKE_NPM_MARKER=str(marker), FAKE_NPM_EXIT=str(exit_code))
        return env, marker

    def test_add_creates_a_branch_named_after_the_lane_from_head(self):
        path = Path(self.ok(self.worktree("add", "lane-a")))
        self.assertEqual(path, self.lanes / "lane-a")
        self.assertIn(path, self.registered())
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=path), "lane-a")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=path), self.head)

    def test_add_with_base_and_branch(self):
        path = Path(self.ok(self.worktree("add", "lane-b", "--base", self.first,
                                          "--branch", "topic/lane-b")))
        self.assertEqual(self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=path), "topic/lane-b")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=path), self.first)

    def test_add_from_a_linked_worktree_uses_the_primary_checkout(self):
        other = self.temp / "elsewhere"
        self.git("worktree", "add", "-q", "--detach", str(other), "HEAD")
        path = Path(self.ok(self.worktree("add", "lane-c", cwd=other)))
        self.assertEqual(path, self.lanes / "lane-c")
        self.assertFalse((other / ".claude").exists())

    def test_add_refuses_an_existing_lane_or_branch(self):
        self.ok(self.worktree("add", "lane-d"))
        again = self.worktree("add", "lane-d")
        self.assertNotEqual(again.returncode, 0)
        self.assertIn("lane-d", again.stderr)
        self.git("branch", "taken")
        clash = self.worktree("add", "lane-e", "--branch", "taken")
        self.assertNotEqual(clash.returncode, 0)
        self.assertFalse((self.lanes / "lane-e").exists())

    def test_add_rejects_names_that_are_not_plain_identifiers(self):
        for name in ("../escape", "a/b", "-x", ""):
            with self.subTest(name=name):
                result = self.worktree("add", "--", name) if name.startswith("-") else \
                    self.worktree("add", name)
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / ".claude" / "escape").exists())
        self.assertFalse((self.temp / "escape").exists())
        self.assertEqual(self.registered(), {self.root})

    def npm_calls(self, marker):
        """(directory, arguments) per npm call, in order."""
        return [(Path(line.split(" ", 1)[0]).resolve(), line.split(" ", 1)[1])
                for line in marker.read_text().splitlines()]

    def use_overlay(self, data):
        folder = Path(tempfile.mkdtemp(dir=self.temp, prefix="overlay-"))
        (folder / "project.json").write_text(json.dumps(data))
        self.env["HARNESS_PROJECT"] = str(folder)

    def test_deps_runs_npm_ci_in_every_node_dir(self):
        env, marker = self.fake_npm()
        result = self.worktree("add", "lane-w", "--deps", env=env)
        path = Path(self.ok(result))
        self.assertEqual(path, self.lanes / "lane-w", "stdout carries only the path")
        self.assertEqual(self.npm_calls(marker),
                         [(path / "web", "ci"), (path / "packages/ui", "ci")])
        self.assertEqual(result.stderr.count("exit=0"), 2)
        self.assertIn("log: ", result.stderr)

    def test_web_is_a_deprecated_alias_of_deps(self):
        env, marker = self.fake_npm()
        result = self.worktree("add", "lane-v", "--web", env=env)
        path = Path(self.ok(result))
        self.assertEqual(self.npm_calls(marker),
                         [(path / "web", "ci"), (path / "packages/ui", "ci")])
        self.assertIn("deprecated", result.stderr)
        self.assertIn("--deps", result.stderr)

    def test_node_dirs_come_from_the_lane_overlay(self):
        del self.env["HARNESS_PROJECT"]
        (self.root / "harness/project").mkdir(parents=True)
        (self.root / "harness/project/project.json").write_text(
            json.dumps({"worktree": {"node_dirs": ["packages/ui"]}}))
        self.git("add", "harness/project/project.json")
        self.git("commit", "-qm", "overlay")
        env, marker = self.fake_npm()
        path = Path(self.ok(self.worktree("add", "lane-o", "--deps", env=env)))
        self.assertEqual(self.npm_calls(marker), [(path / "packages/ui", "ci")])

    def test_failed_npm_ci_keeps_the_lane_and_its_exit_code(self):
        env, marker = self.fake_npm(exit_code=7)
        result = self.worktree("add", "lane-x", "--deps", env=env)
        self.assertEqual(result.returncode, 7)
        self.assertIn("exit=7", result.stderr)
        self.assertIn("npm ci", result.stderr)
        self.assertIn(self.lanes / "lane-x", self.registered())
        self.assertTrue(marker.exists())

    def test_without_deps_npm_is_not_called(self):
        env, marker = self.fake_npm()
        self.ok(self.worktree("add", "lane-n", env=env))
        self.assertFalse(marker.exists())

    def test_deps_without_node_dirs_is_refused_and_keeps_the_lane(self):
        for name, overlay in (("lane-e1", None), ("lane-e2", {"worktree": {"node_dirs": []}})):
            with self.subTest(overlay=overlay):
                if overlay is None:
                    self.env["HARNESS_PROJECT"] = str(self.temp / "no-overlay")
                else:
                    self.use_overlay(overlay)
                env, marker = self.fake_npm()
                result = self.worktree("add", name, "--deps", env=env)
                self.assertEqual(result.returncode, 1)
                self.assertIn("node_dirs", result.stderr)
                self.assertEqual(Path(result.stdout.strip()), self.lanes / name)
                self.assertIn(self.lanes / name, self.registered())
                self.assertFalse(marker.exists())

    def test_a_missing_node_dir_is_reported_and_the_others_still_install(self):
        self.use_overlay({"worktree": {"node_dirs": ["missing", "web"]}})
        env, marker = self.fake_npm()
        result = self.worktree("add", "lane-g", "--deps", env=env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing", result.stderr)
        path = self.lanes / "lane-g"
        self.assertEqual(self.npm_calls(marker), [(path / "web", "ci")])

    def test_node_dirs_outside_the_lane_are_refused(self):
        for name, dirs in (("lane-p1", ["../outside"]), ("lane-p2", ["/abs/web"]),
                           ("lane-p3", "web"), ("lane-p4", [3])):
            with self.subTest(dirs=dirs):
                self.use_overlay({"worktree": {"node_dirs": dirs}})
                env, marker = self.fake_npm()
                result = self.worktree("add", name, "--deps", env=env)
                self.assertEqual(result.returncode, 1)
                self.assertIn("node_dirs", result.stderr)
                self.assertIn(self.lanes / name, self.registered())
                self.assertFalse(marker.exists())

    def test_remove_a_clean_lane_keeps_its_branch(self):
        path = Path(self.ok(self.worktree("add", "lane-r")))
        (path / "web" / "node_modules").mkdir()
        (path / "web" / "node_modules" / "dep.js").write_text("ignored\n")
        self.ok(self.worktree("remove", "lane-r"))
        self.assertFalse(path.exists())
        self.assertNotIn(path, self.registered())
        self.assertEqual(self.git("rev-parse", "--verify", "refs/heads/lane-r"), self.head)

    def test_remove_refuses_uncommitted_changes(self):
        path = Path(self.ok(self.worktree("add", "lane-m")))
        (path / "README.md").write_text("# edited\n")
        result = self.worktree("remove", "lane-m")
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md", result.stderr)
        self.assertTrue((path / "README.md").exists())
        self.assertIn(path, self.registered())

    def test_remove_refuses_untracked_files(self):
        path = Path(self.ok(self.worktree("add", "lane-u")))
        (path / "notes.txt").write_text("not committed\n")
        result = self.worktree("remove", "lane-u")
        self.assertEqual(result.returncode, 1)
        self.assertIn("notes.txt", result.stderr)
        self.assertTrue((path / "notes.txt").exists())

    def test_remove_refuses_staged_changes(self):
        path = Path(self.ok(self.worktree("add", "lane-s")))
        (path / "new.txt").write_text("staged\n")
        self.git("add", "new.txt", cwd=path)
        result = self.worktree("remove", "lane-s")
        self.assertEqual(result.returncode, 1)
        self.assertIn("new.txt", result.stderr)

    def test_remove_an_unknown_lane_is_an_error(self):
        result = self.worktree("remove", "never-made")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("never-made", result.stderr)

    def test_remove_ignores_a_directory_that_is_not_a_registered_worktree(self):
        stray = self.lanes / "stray"
        stray.mkdir(parents=True)
        (stray / "keep.txt").write_text("mine\n")
        result = self.worktree("remove", "stray")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue((stray / "keep.txt").exists())

    def test_remove_from_inside_another_lane(self):
        first = Path(self.ok(self.worktree("add", "lane-1")))
        second = Path(self.ok(self.worktree("add", "lane-2")))
        self.ok(self.worktree("remove", "lane-2", cwd=first))
        self.assertFalse(second.exists())


if __name__ == "__main__":
    unittest.main()
