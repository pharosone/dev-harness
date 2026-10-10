"""The harness eval plugin stays loadable: manifest, skill links, and well-formed cases."""
import json
from pathlib import Path
import re
import unittest

PLUGIN = Path(__file__).resolve().parents[1] / "plugin"
PROMPT_KEYS = {"name", "description", "tags", "plugins", "runs", "expected_outcome", "model",
               "max_turns", "timeout_seconds", "allowed_tools", "append_system_prompt", "env"}
GRADER_TYPES = {"regex", "tool_used", "tool_order", "file_exists", "llm", "baseline"}


def frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines and lines[0] == "---", f"{path}: missing frontmatter"
    end = lines.index("---", 1)
    pairs = [line.split(":", 1) for line in lines[1:end] if re.match(r"^[a-z_]+:", line)]
    return {key: value.strip() for key, value in pairs}


class EvalPluginTest(unittest.TestCase):
    def test_manifest_and_skill_links_resolve(self):
        manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text())
        self.assertEqual(manifest["name"], "dev-harness-evals")
        registry = json.loads((PLUGIN / 'evals/cases.json').read_text())
        for path in registry:
            skill = Path(path).parent.name
            link = PLUGIN / "skills" / skill
            self.assertTrue(link.is_symlink(), skill)
            self.assertTrue((link / "SKILL.md").is_file(), skill)

    def test_every_case_is_well_formed(self):
        cases = sorted(p for p in (PLUGIN / "evals").iterdir()
                       if p.is_dir() and p.name not in {"results", "history"})
        self.assertGreaterEqual(len(cases), 4)
        for case in cases:
            header = frontmatter(case / "prompt.md")
            self.assertLessEqual(set(header), PROMPT_KEYS, case.name)
            self.assertEqual(header.get("name"), case.name)
            self.assertIn("runs", header, case.name)
            graders = sorted((case / "graders").glob("*.md"))
            self.assertTrue(graders, f"{case.name}: no graders")
            types = {frontmatter(g).get("type") for g in graders}
            self.assertLessEqual(types, GRADER_TYPES, case.name)
            for grader in graders:
                # Graders run as JavaScript regexes: inline flags such as (?i) throw.
                self.assertNotRegex(grader.read_text(encoding="utf-8"), r"\(\?[a-z]+\)", grader.name)
            # Most of the score must come from free, deterministic graders.
            self.assertTrue(types & {"regex", "tool_used", "tool_order", "file_exists"}, case.name)


if __name__ == "__main__":
    unittest.main()
