#!/usr/bin/env python3
"""Both native hook adapters obey the same contract in isolated Git repositories, with the
fixture project's drift.json in the overlay."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import test_check_docs_drift as drift

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "runtime_hook.py"


class RuntimeHookTest(unittest.TestCase):
    overlay = True
    new_repo = drift.RegistryRuleTest.new_repo

    def run_hook(self, repo, runtime, session="test-session", *, active=False,
                 event_cwd=None, process_cwd=None):
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--runtime", runtime],
            cwd=process_cwd or repo.root,
            env=repo.env,
            input=json.dumps({
                "session_id": session,
                "cwd": str(event_cwd or repo.root),
                "stop_hook_active": active,
                "hook_event_name": "Stop",
            }),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def assertClean(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {})
        self.assertEqual(result.stderr, "")

    def assertBlocked(self, result):
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn(drift.REGISTRY, result.stderr)
        self.assertIn(drift.REGISTRY_CLAIM, result.stderr)

    def test_clean_hook_returns_empty_object_for_each_runtime(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                self.assertClean(self.run_hook(self.new_repo(), runtime))

    def test_drift_blocks_for_each_runtime(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                self.assertBlocked(self.run_hook(repo, runtime))

    def test_same_session_suppresses_only_already_seen_findings(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                self.assertBlocked(self.run_hook(repo, runtime))
                self.assertClean(self.run_hook(repo, runtime))
                self.assertBlocked(self.run_hook(repo, runtime, session="other-session"))
                repo.append("app/config.py", "# additional env var\n")
                result = self.run_hook(repo, runtime)
                self.assertBlocked(result)
                self.assertIn(drift.CONFIG_DOC, result.stderr)

    def test_active_hook_skips_without_recording_suppression(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                self.assertClean(self.run_hook(repo, runtime, active=True))
                self.assertBlocked(self.run_hook(repo, runtime))

    def test_event_cwd_subdirectory_resolves_repository(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                with tempfile.TemporaryDirectory() as outside:
                    result = self.run_hook(
                        repo, runtime,
                        event_cwd=Path(repo.root) / "app/items",
                        process_cwd=outside,
                    )
                self.assertBlocked(result)

    def test_main_and_linked_worktree_have_independent_suppression(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                with tempfile.TemporaryDirectory() as tmp:
                    location = os.path.join(tmp, "linked")
                    repo.git("worktree", "add", "-q", "-b", "feat/linked", location, "main")
                    linked = drift.Repo(location)
                    repo.append(drift.ROUTES, drift.OPERATION)
                    linked.append(drift.ROUTES, drift.OPERATION)
                    self.assertBlocked(self.run_hook(repo, runtime))
                    self.assertBlocked(self.run_hook(linked, runtime))
                    self.assertClean(self.run_hook(repo, runtime))
                    self.assertClean(self.run_hook(linked, runtime))

    def test_untrusted_session_id_cannot_escape_cache(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                with tempfile.TemporaryDirectory() as outside:
                    escaped = Path(outside) / "escaped-cache"
                    malicious = "../" * 20 + str(escaped).lstrip("/")
                    self.assertBlocked(self.run_hook(repo, runtime, session=malicious))
                    self.assertClean(self.run_hook(repo, runtime, session=malicious))
                    self.assertFalse(escaped.exists())
                    self.assertEqual(list(Path(outside).iterdir()), [])

    def test_hook_suppression_does_not_affect_stateless_cli(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                self.assertBlocked(self.run_hook(repo, runtime))
                self.assertClean(self.run_hook(repo, runtime))
                result = repo.run_cli()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn(drift.REGISTRY, result.stdout + result.stderr)

    def test_read_only_git_metadata_preserves_findings_and_suppression(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                metadata = Path(repo.root) / ".git"
                entries = [metadata, *metadata.rglob("*")]
                modes = {path: path.stat().st_mode & 0o777 for path in entries}
                before = {str(path.relative_to(metadata)) for path in entries}
                try:
                    for path in reversed(entries):
                        path.chmod(modes[path] & ~0o222)
                    self.assertBlocked(self.run_hook(repo, runtime))
                    self.assertClean(self.run_hook(repo, runtime))
                    self.assertEqual(before, {".", *(
                        str(path.relative_to(metadata)) for path in metadata.rglob("*")
                    )})
                    self.assertTrue((Path(repo.root) / ".harness/cache").is_dir())
                finally:
                    for path, mode in modes.items():
                        path.chmod(mode)

    def test_unavailable_cache_never_hides_findings(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                repo.write(".harness", "A regular file prevents cache creation\n")
                self.assertBlocked(self.run_hook(repo, runtime))
                self.assertBlocked(self.run_hook(repo, runtime))
                self.assertClean(self.run_hook(repo, runtime, active=True))

    def test_cache_directory_symlinks_cannot_escape_checkout(self):
        for runtime in ("claude", "codex"):
            for location in (".harness", ".harness/cache"):
                with self.subTest(runtime=runtime, location=location):
                    repo = self.new_repo()
                    repo.append(drift.ROUTES, drift.OPERATION)
                    with tempfile.TemporaryDirectory() as outside:
                        link = Path(repo.root) / location
                        link.parent.mkdir(parents=True, exist_ok=True)
                        link.symlink_to(outside, target_is_directory=True)
                        self.assertBlocked(self.run_hook(repo, runtime))
                        self.assertBlocked(self.run_hook(repo, runtime))
                        self.assertEqual(list(Path(outside).iterdir()), [])

    def test_without_mappings_the_hook_checks_only_the_harness_log(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                self.overlay = False
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                self.assertClean(self.run_hook(repo, runtime))
                repo.append("harness/project/roles/implementer.md", "New specific\n")
                result = self.run_hook(repo, runtime)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(drift.LOG, result.stderr)
                self.assertNotIn(drift.REGISTRY, result.stderr)

    def test_unusable_mappings_block_instead_of_passing(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.write("harness/project/drift.json", "{not json")
                result = self.run_hook(repo, runtime)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("drift.json", result.stderr)

    def test_cache_file_symlink_cannot_suppress_or_modify_external_file(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                repo = self.new_repo()
                repo.append(drift.ROUTES, drift.OPERATION)
                self.assertBlocked(self.run_hook(repo, runtime))
                cache_files = list((Path(repo.root) / ".harness/cache").glob("*"))
                self.assertEqual(len(cache_files), 1)
                cache = cache_files[0]
                fingerprint = cache.read_text()
                with tempfile.TemporaryDirectory() as outside:
                    sentinel = Path(outside) / "sentinel"
                    sentinel.write_text(fingerprint)
                    cache.unlink()
                    cache.symlink_to(sentinel)
                    self.assertBlocked(self.run_hook(repo, runtime))
                    self.assertEqual(sentinel.read_text(), fingerprint)


if __name__ == "__main__":
    unittest.main()
