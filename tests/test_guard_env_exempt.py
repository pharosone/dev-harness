"""guard.env_exempt frees only tracked env files its own repository's overlay names.

A tracked, secret-free web/.env.production may be read and edited when the overlay lists
web/.env.*; an untracked .env.local under the same glob, .env itself, a symlink into an
untracked file and another repository's files stay denied, on both runtimes.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from test_guard import CORE, GuardHarness

RUNTIMES = ("claude", "codex")
EXEMPT = json.dumps({"guard": {"env_exempt": ["web/.env.*"]}})


class EnvExemptTest(GuardHarness, unittest.TestCase):
    def setUp(self):
        super().setUp()
        for name in (".env", "web/.env.production", "web/.env.development",
                     "web/sub/.env.production", "api/.env.production", "web/.env.d/a.txt"):
            self.put(name, "KEY=value\n")
        os.symlink("web/.env.production", self.root / "x.txt")
        os.symlink("../.env", self.root / "web/.env.staging")
        self.git("add", ".env", "web/.env.production", "web/.env.development",
                 "web/sub/.env.production", "api/.env.production", "web/.env.d/a.txt",
                 "x.txt", "web/.env.staging")
        self.git("commit", "-qm", "tracked env files")
        self.put("web/.env.local", "SECRET=1\n")
        self.put("web/.env.d/secret", "SECRET=1\n")
        self.put("secret.txt", "SECRET=1\n")
        os.symlink("web/.env.local", self.root / "y.txt")
        os.symlink("web/.env.production", self.root / "z.txt")

    def read(self, name, runtime="claude", cwd=None):
        return self.verdict(self.event("Read", cwd, file_path=self.path(name)), runtime)

    def denied_everywhere(self, name):
        """cat, Read and Edit of name are denied as .env files on both runtimes."""
        for runtime in RUNTIMES:
            with self.subTest(name=name, runtime=runtime):
                self.assert_denied(self.bash(f"cat {name}", runtime), ".env")
                self.assert_denied(self.read(name, runtime), ".env")
                self.assert_denied(self.edit(name, runtime=runtime), ".env")

    def test_without_the_key_tracked_env_files_stay_denied(self):
        self.denied_everywhere("web/.env.production")

    def test_a_tracked_file_the_glob_names_is_readable_and_editable(self):
        self.use_overlay(EXEMPT)
        for runtime in RUNTIMES:
            for name in ("web/.env.production", "web/.env.development"):
                with self.subTest(name=name, runtime=runtime):
                    self.assertIsNone(self.bash(f"cat {name}", runtime))
                    self.assertIsNone(self.bash(f"grep KEY {name} | head -1", runtime))
                    self.assertIsNone(self.bash(f"sed -i s/value/other/ {name}", runtime))
                    self.assertIsNone(self.bash(f"printf 'KEY=x\\n' > {name}", runtime))
                    self.assertIsNone(self.bash(
                        f"python3 -c \"print(open('{name}').read())\"", runtime))
                    self.assertIsNone(self.read(name, runtime))
                    self.assertIsNone(self.verdict(self.event(
                        "Grep", path=self.path(name), pattern="KEY"), runtime))
                    self.assertIsNone(self.edit(name, runtime=runtime))
                    self.assertIsNone(self.write(name, runtime=runtime))
                    self.assertIsNone(self.patch(f"*** Update File: {name}\n@@\n-KEY=value\n"
                                                 "+KEY=other", runtime))
        # A relative path from a subdirectory names the same tracked file.
        self.assertIsNone(self.verdict(self.event("Bash", self.root / "web",
                                                  command="cat .env.production")))

    def test_untracked_or_unnamed_files_under_the_glob_stay_denied(self):
        self.use_overlay(EXEMPT)
        for name in ("web/.env.local", "web/sub/.env.production", "api/.env.production",
                     ".env"):
            self.denied_everywhere(name)
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                # A new file is not tracked yet.
                self.assert_denied(self.write("web/.env.new", runtime=runtime), ".env")
                self.assert_denied(self.patch("*** Add File: web/.env.new\n+KEY=x", runtime),
                                   ".env")
                self.assert_denied(self.bash("cp secret.txt web/.env.new", runtime), ".env")
                # A directory the glob names may hold untracked files.
                self.assert_denied(self.bash("grep -r SECRET web/.env.d", runtime), ".env")
                # A glob argument is judged by its name alone.
                self.assert_denied(self.bash("cat web/.env.*", runtime), ".env")

    def test_only_an_exact_tracked_file_counts(self):
        """The index entry, not a tracked prefix, and a file on disk, not a directory."""
        self.use_overlay(EXEMPT)
        shutil.rmtree(self.root / "web/.env.d")  # its files stay in the index
        (self.root / "web/.env.production").unlink()
        (self.root / "web/.env.production").mkdir()  # tracked file replaced by a directory
        (self.root / "web/.env.production/secret").write_text("SECRET=1\n")
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.assert_denied(self.write("web/.env.d", runtime=runtime), ".env")
                self.assert_denied(self.bash("grep -r SECRET web/.env.production", runtime),
                                   ".env")

    def test_entries_that_could_name_dot_env_itself_are_rejected(self):
        bad = [".env", "*/.env", "web/.env", ".env*", "*", "web/*", ".[e]nv.x",
               "web/**/.env.*", "**/.env.*", "../web/.env.*", "web/../.env.x", "/abs/.env.*",
               "./web/.env.*", "web//.env.x", "web\\.env.x", "", "  ", 7, None]
        self.use_overlay(json.dumps({"guard": {"env_exempt": bad}}))
        result = self.run_guard(json.dumps(self.event("Bash", command="cat .env")))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deny", result.stdout)
        notes = [line for line in result.stderr.splitlines()
                 if "invalid guard.env_exempt entry" in line]
        self.assertEqual(len(notes), len(bad), result.stderr)
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.assert_denied(self.bash("cat .env", runtime), ".env")
                self.assert_denied(self.edit(".env", runtime=runtime), ".env")
                self.assert_denied(self.bash("cat web/.env.production", runtime), ".env")
        # A valid glob at the root still never matches .env itself.
        self.use_overlay(json.dumps({"guard": {"env_exempt": [".env.*", "web/.env.*"]}}))
        self.denied_everywhere(".env")
        self.use_overlay(json.dumps({"guard": {"env_exempt": "web/.env.*"}}))
        result = self.run_guard(json.dumps(self.event("Bash", command="cat web/.env.production")))
        self.assertIn("guard.env_exempt is not a list", result.stderr)
        self.assertIn("deny", result.stdout)

    def test_symlinks_count_only_when_link_and_target_are_tracked(self):
        self.use_overlay(EXEMPT)
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                # Tracked link to a tracked, named file.
                self.assertIsNone(self.bash("cat x.txt", runtime))
                self.assertIsNone(self.read("x.txt", runtime))
                self.assertIsNone(self.edit("x.txt", runtime=runtime))
        # Tracked link to an untracked file; untracked link to a tracked file; a tracked
        # link the glob names whose target is .env.
        for name in ("y.txt", "z.txt", "web/.env.staging"):
            self.denied_everywhere(name)

    def test_the_overlay_of_another_repository_exempts_nothing_here(self):
        """Repository overlays at <repo>/harness/project: one repository's key frees only its
        own files."""
        del self.env["HARNESS_PROJECT"]
        other = Path(self.temp.name) / "other"
        (other / "web").mkdir(parents=True)
        (other / "web/.env.production").write_text("KEY=value\n")
        subprocess.check_call(["git", "init", "-q", str(other)], env=self.env)
        subprocess.check_call(["git", "-C", str(other), "add", "web/.env.production"],
                              env=self.env)
        self.put("harness/project/project.json", EXEMPT)
        target = other / "web/.env.production"
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                self.assertIsNone(self.bash("cat web/.env.production", runtime))
                self.assert_denied(self.bash(f"cat {target}", runtime), ".env")
                self.assert_denied(self.verdict(self.event("Read", file_path=str(target)),
                                                runtime), ".env")
        (other / "harness/project").mkdir(parents=True)
        (other / "harness/project/project.json").write_text(EXEMPT)
        self.assertIsNone(self.bash(f"cat {target}"))

    def test_the_core_checkouts_overlay_does_not_exempt_the_command_repository(self):
        """A separate core checkout's overlay still adds rules but cannot loosen the .env rule
        for the repository the guard protects."""
        del self.env["HARNESS_PROJECT"]
        core = Path(self.temp.name) / "core"
        (core / "scripts").mkdir(parents=True)
        for module in ("guard.py", "project.py"):
            shutil.copy(CORE / "scripts" / module, core / "scripts" / module)
        subprocess.check_call(["git", "init", "-q", str(core)], env=self.env)
        (core / "harness/project").mkdir(parents=True)
        (core / "harness/project/project.json").write_text(json.dumps(
            {"guard": {"env_exempt": ["web/.env.*"], "prod_compose": ["compose.prod*"]}}))

        def verdict(command, runtime):
            event = json.dumps(self.event("Bash", command=command))
            result = subprocess.run([sys.executable, str(core / "scripts/guard.py"),
                                     "--runtime", runtime], cwd=self.root, env=self.env,
                                    input=event, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout else None

        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                found = verdict("cat web/.env.production", runtime)
                self.assertEqual(found and found["permissionDecision"], "deny")
                self.assertIn(".env", found["permissionDecisionReason"])
                # The core checkout's lists still add to the rules.
                found = verdict("docker compose -f compose.prod.yaml up -d", runtime)
                self.assertEqual(found and found["permissionDecision"], "deny")
        self.put("harness/project/project.json", EXEMPT)
        self.assertIsNone(verdict("cat web/.env.production", "claude"))

    def test_editing_the_key_still_needs_the_user(self):
        overlay = self.use_overlay(EXEMPT)
        for runtime in RUNTIMES:
            with self.subTest(runtime=runtime):
                verdict = self.verdict(self.event(
                    "Edit", file_path=str(overlay / "project.json"), old_string="web",
                    new_string="api"), runtime)
                self.assert_guarded(verdict, runtime, "guard")


if __name__ == "__main__":
    unittest.main()
