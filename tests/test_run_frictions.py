"""Service preflights and narrowly normalized reference metadata."""
import os
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import gate
import skill_evals as ev


class ServiceProbeTest(unittest.TestCase):
    def spec(self, **service):
        return {"tests": {"paths": ["**"], "services": [{"name": "test DB", "url_env": "TEST_DB_URL", "start": "docker compose up -d db", **service}], "steps": [{"name": "tests", "cwd": ".", "cmd": ["python3", "-m", "unittest"]}]}}

    def test_declared_service_is_valid_and_probe_fails_without_running_tests(self):
        areas, _ = gate.parse(self.spec(), "gates")
        with socket.socket() as closed:
            closed.bind(("127.0.0.1", 0))
            port = closed.getsockname()[1]
        with patch.dict(os.environ, {"TEST_DB_URL": f"postgresql://secret:password@127.0.0.1:{port}/test"}):
            message = gate.probe_services(areas, {"tests"})
        self.assertEqual(message, f"test DB at 127.0.0.1:{port} is down; start it with docker compose up -d db")
        self.assertNotIn("password", message)

    def test_live_socket_and_default_postgres_port(self):
        areas, _ = gate.parse(self.spec(), "gates")
        with socket.socket() as server:
            server.bind(("127.0.0.1", 0))
            server.listen()
            with patch.dict(os.environ, {"TEST_DB_URL": f"postgres://127.0.0.1:{server.getsockname()[1]}/test"}):
                self.assertIsNone(gate.probe_services(areas, {"tests"}))
        with patch.dict(os.environ, {"TEST_DB_URL": "postgres://127.0.0.1/test"}), patch.object(gate.socket, "create_connection") as connect:
            self.assertIsNone(gate.probe_services(areas, {"tests"}))
            self.assertEqual(connect.call_args.args[0], ("127.0.0.1", 5432))

    def test_unselected_service_and_missing_env(self):
        areas, _ = gate.parse(self.spec(), "gates")
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(gate.probe_services(areas, set()))
            self.assertIn("TEST_DB_URL is unset", gate.probe_services(areas, {"tests"}))

    def test_cli_fails_before_steps_even_with_keep_going(self):
        import test_gate
        repo = test_gate.Repo(self)
        marker = repo.root / ".marks/ran"
        data = self.spec()
        data["tests"]["steps"][0]["cmd"] = [sys.executable, "-c", "from pathlib import Path; Path('.marks/ran').touch()"]
        config = repo.write("gates.json", json.dumps(data))
        env = dict(repo.env)
        env.pop("TEST_DB_URL", None)
        result = test_gate.run(test_gate.GATE, "--config", str(config), "--keep-going", cwd=repo.root, env=env)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        self.assertIn("TEST_DB_URL is unset", result.stdout)
        self.assertFalse(marker.exists())
        dry = test_gate.run(test_gate.GATE, "--config", str(config), "--dry-run", cwd=repo.root, env=env)
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertFalse(marker.exists())

    def test_malformed_services_rejected(self):
        for service in ({"timeout": 0}, {"timeout": True}, {"url_env": ".env"}, {"extra": "x"}, {"name": "DB\nspoof"}):
            with self.subTest(service=service), self.assertRaises(gate.ConfigError):
                gate.parse(self.spec(**service), "gates")


class ReferenceMetadataTest(unittest.TestCase):
    def test_version_and_date_metadata_only_normalize_in_references(self):
        before = b"# Guide\nVersion: 1.5.0\nDate: 2026-10-10\nDo the work.\n"
        after = b"# Guide\nVersion: 1.6.0\nDate: 2026-10-11\nDo the work.\n"
        self.assertEqual(ev.normalized_reference(before, "README.md"), ev.normalized_reference(after, "README.md"))
        self.assertNotEqual(ev.normalized_reference(before, "README.md"), ev.normalized_reference(after.replace(b"Do the work", b"Skip the work"), "README.md"))
        self.assertNotEqual(ev.normalized_reference(b"Use version 1.5.0 now", "README.md"), ev.normalized_reference(b"Use version 1.6.0 now", "README.md"))
        self.assertNotEqual(ev.normalized_reference(before, "rules.py"), ev.normalized_reference(after, "rules.py"))

    def test_fenced_metadata_is_not_normalized(self):
        for fence in ("```", "~~~"):
            before = (fence + "\nVersion: 1.5.0\n" + fence + "\n").encode()
            after = before.replace(b"1.5.0", b"1.6.0")
            self.assertNotEqual(ev.normalized_reference(before, "README.md"), ev.normalized_reference(after, "README.md"))

    def test_only_changelog_release_headings_are_ignored(self):
        self.assertEqual(ev.normalized_reference(b"# Changelog\n## 1.5.0 \xe2\x80\x94 2026-10-10\nFix X\n", "CHANGELOG.md"), ev.normalized_reference(b"# Changelog\n## 1.6.0 \xe2\x80\x94 2026-10-11\nFix X\n", "CHANGELOG.md"))
        self.assertNotEqual(ev.normalized_reference(b"## 1.5.0\nFix X\n", "CHANGELOG.md"), ev.normalized_reference(b"## 1.6.0\nFix Y\n", "CHANGELOG.md"))
        self.assertNotEqual(ev.normalized_reference(b"## Behavior 1.5.0\n", "CHANGELOG.md"), ev.normalized_reference(b"## Behavior 1.6.0\n", "CHANGELOG.md"))

    def test_skill_bundle_keeps_raw_metadata_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            directory = root / "skills/example"
            directory.mkdir(parents=True)
            skill = directory / "SKILL.md"
            ref = root / "README.md"
            skill.write_text("Read `../../README.md`.\nVersion: 1.5.0\n")
            ref.write_text("Version: 1.5.0\n")
            item = {"root": root, "path": skill, "harness": True}
            with patch.object(ev.project, "CORE", root):
                before = ev.input_hashes(item)
                ref.write_text("Version: 1.6.0\n")
                self.assertEqual(before, ev.input_hashes(item))
                skill.write_text("Read `../../README.md`.\nVersion: 1.6.0\n")
                self.assertNotEqual(before, ev.input_hashes(item))


class CleanupRaceTest(unittest.TestCase):
    def test_cleanup_waits_for_supervisor_post_event_writes(self):
        import test_qa_compare as qa
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            proc = subprocess.Popen([sys.executable, "-c", "import time,pathlib; time.sleep(.1); pathlib.Path(__import__('sys').argv[1]).write_text('done')", str(root / "late.md")])
            try:
                qa.wait_for_supervisors([proc.pid], timeout=2)
                self.assertTrue((root / "late.md").is_file())
                self.assertIsNotNone(proc.poll())
            finally:
                if proc.poll() is None:
                    proc.terminate()
                proc.wait()


class RuntimeContractTest(unittest.TestCase):
    def test_both_adapters_use_profiles_and_file_request_channel(self):
        root = Path(__file__).resolve().parents[1]
        for runtime in ("claude", "codex"):
            text = (root / "adapters" / (runtime + ".md")).read_text()
            with self.subTest(runtime=runtime):
                self.assertIn("Never use personal memory", text)
                self.assertIn("harness/core/adapters/models.json", text)
                self.assertIn("harness/project/models.json", text)
                self.assertIn("<report>.progress.md", text)
                self.assertIn("<run>/lanes/<work>.flag.md", text)
                self.assertIn("Never type into another agent pane", text)

    def test_implementer_separates_progress_and_final_and_records_green(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "roles/implementer.md").read_text()
        self.assertIn("<report>.progress.md", text)
        self.assertIn("only\nwhen the dispatch ends", text)
        self.assertIn("--green-log", text)
        self.assertIn("Never type into", text)


if __name__ == "__main__":
    unittest.main()
