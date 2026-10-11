"""QA findings m10, m11, m16, m19 and the m18 scrub: docs, example gates and release metadata."""
import json
from pathlib import Path
import re
import unittest

CORE = Path(__file__).resolve().parents[1]


def read(*parts: str) -> str:
    return CORE.joinpath(*parts).read_text(encoding="utf-8")


class DiscoverInitializesItsRunTest(unittest.TestCase):
    def test_discover_initializes_the_run_without_a_spec(self):
        text = read("skills", "pharos-discover", "SKILL.md")
        self.assertRegex(text, r"task_state\.py init <task>(?! --spec)")
        self.assertIn("<run-dir>", text)
        self.assertLess(text.index("task_state.py init"), text.index("task_state.py event"))

    def test_compare_exit_two_covers_configuration_and_missing_init(self):
        text = read("skills", "pharos-discover", "SKILL.md")
        self.assertRegex(text, r"(?is)exit 2.{0,400}(not initiali[sz]ed|configuration)")


class ScriptsAreDocumentedTest(unittest.TestCase):
    def test_readme_lists_snapshot_cleanup_and_candidate_capability(self):
        readme = read("README.md")
        for needle in ("snapshot-cleanup", "--capability candidate", "--scratch", "--max-budget-usd"):
            self.assertIn(needle, readme, needle)

    def test_changelog_names_the_candidate_snapshot_and_cleanup(self):
        section = read("CHANGELOG.md").split("## 1.2.0", 1)[1].split("\n## ", 1)[0]
        for needle in ("snapshot-cleanup", "--capability candidate", "snapshot", "--max-budget-usd"):
            self.assertIn(needle, section, needle)

    def test_codex_adapter_has_no_spend_without_dispatch(self):
        for name in ("adapters/codex.md", "skills/pharos-dev/SKILL.md"):
            text = read(*name.split("/"))
            for match in re.finditer(r"tripwire\.py spend", text):
                tail = text[match.end():match.end() + 160]
                self.assertIn("--dispatch", tail, f"{name}: bare spend near {tail[:60]!r}")


class ExampleRunsTheArchitectureCheckTest(unittest.TestCase):
    def test_example_gates_have_an_arch_check_step(self):
        gates = json.loads(read("examples", "project", "gates.json"))
        steps = [(area, step) for area, body in gates.items() if isinstance(body, dict) and "steps" in body
                 for step in body["steps"]]
        found = [(area, step) for area, step in steps if any("arch_check.py" in part for part in step["cmd"])]
        self.assertTrue(found, "no gates.json step runs arch_check.py")
        area, step = found[0]
        self.assertIn("--base", step["cmd"])
        covered = " ".join(gates[area]["paths"])
        for code in ("app/", "web/"):
            self.assertIn(code, covered, f"area {area} does not cover {code}")


class PluginVersionTest(unittest.TestCase):
    def test_plugin_version_is_the_release(self):
        plugin = json.loads(read("plugin", ".claude-plugin", "plugin.json"))
        self.assertEqual(plugin["version"], "1.7.0")


if __name__ == "__main__":
    unittest.main()
