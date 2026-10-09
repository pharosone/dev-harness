"""Core scripts find the overlay in the checkout, or where HARNESS_PROJECT points."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import project  # noqa: E402


class ProjectTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        os.environ.pop("HARNESS_PROJECT", None)

    def test_default_overlay_and_defaults(self):
        self.assertEqual(project.repo_root(self.root), self.root)
        self.assertEqual(project.overlay_dir(self.root), self.root / "harness/project")
        self.assertEqual(project.load(self.root, "tiers.json", {}), {})

    def test_overlay_file_and_type_check(self):
        overlay = self.root / "harness/project"
        overlay.mkdir(parents=True)
        (overlay / "project.json").write_text(json.dumps({"name": "demo"}))
        self.assertEqual(project.load(self.root, "project.json", {})["name"], "demo")
        (overlay / "gates.json").write_text("[]")
        with self.assertRaises(project.OverlayError):
            project.load(self.root, "gates.json", {})

    def overlay_cli(self, env=None):
        result = subprocess.run([sys.executable, str(Path(project.__file__)), "overlay"],
                                cwd=self.root, capture_output=True, text=True,
                                env={**os.environ, **(env or {})})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def test_the_overlay_in_use_is_printed_for_skills(self):
        self.assertEqual(self.overlay_cli(), "harness/project")
        self.assertEqual(self.overlay_cli({"HARNESS_PROJECT": "config/harness"}), "config/harness")

    def test_env_override(self):
        os.environ["HARNESS_PROJECT"] = "custom/layer"
        self.addCleanup(os.environ.pop, "HARNESS_PROJECT", None)
        self.assertEqual(project.overlay_dir(self.root), self.root / "custom/layer")


if __name__ == "__main__":
    unittest.main()
