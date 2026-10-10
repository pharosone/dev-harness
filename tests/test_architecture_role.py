"""Architecture conformance in the process: the reviewer role, the Placement section, the map.

The `architecture-reviewer` is a core, read-only lens on `critical-review` that joins the
`pharos-dev` panel on every Tier 2–3 milestone, next to `arch_check.py`; `pharos-discover`
makes every spec carry `## Placement`; `design-review` reads the same map; `docs/overlay.md`
documents `architecture.json` and `architecture.md`; the README lists the role and the script.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest

CORE = Path(__file__).resolve().parents[1]
ROLE = "architecture-reviewer"
FIXTURE = Path(__file__).resolve().parent / "fixture/harness/project"
ENV = {key: value for key, value in os.environ.items() if key != "HARNESS_PROJECT"}
NUMBERS = {6: "Six", 7: "Seven", 8: "Eight", 9: "Nine", 10: "Ten"}


def read(relative: str) -> str:
    return (CORE / relative).read_text(encoding="utf-8")


def section(text: str, heading: str) -> str:
    """The body of a `## heading` section up to the next `## ` heading."""
    match = re.search(rf"(?m)^## {re.escape(heading)}\s*$", text)
    if not match:
        raise AssertionError(f"no '## {heading}' section")
    rest = text[match.end():]
    end = re.search(r"(?m)^## ", rest)
    return rest[:end.start()] if end else rest


def table_rows(text: str) -> list[list[str]]:
    return [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in text.splitlines() if line.startswith("|")]


class RoleTest(unittest.TestCase):
    def test_the_role_has_a_read_only_critical_review_manifest_entry(self):
        manifest = json.loads(read("roles/manifest.json"))
        entry = manifest[ROLE]
        self.assertEqual(entry["profile"], "critical-review")
        self.assertEqual(entry["sandbox"], "read-only")
        self.assertEqual(entry["tools"], ["Read", "Grep", "Glob", "Bash", "Write"])
        self.assertTrue(entry["description"].strip())
        self.assertNotIn("Edit", entry["tools"])

    def test_the_role_builds_its_picture_from_the_map_the_placement_and_the_check(self):
        text = read(f"roles/{ROLE}.md")
        for needle in ("architecture.md", "architecture.json", "## Placement", "arch_check.py",
                       "Read-only"):
            with self.subTest(needle=needle):
                self.assertIn(needle, text)
        self.assertRegex(text, r"\*\*Findings\*\*")
        self.assertRegex(text, r"\*\*Checked and holds\*\*")
        self.assertRegex(text, r"\*\*Not covered\*\*")
        self.assertRegex(text, r"(?i)amended")

    def test_the_role_closes_with_the_shared_findings_paragraph(self):
        shared = read("roles/security-auditor.md").rsplit("\n## Findings\n", 1)[1].strip()
        text = read(f"roles/{ROLE}.md")
        self.assertTrue(text.rstrip().endswith(shared))
        self.assertEqual(text.rsplit("\n## Findings\n", 1)[1].strip(), shared)


class RenderTest(unittest.TestCase):
    """The core under test, linked at harness/core, renders the role for both runtimes."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "harness").mkdir()
        (self.root / "harness/core").symlink_to(CORE, target_is_directory=True)
        shutil.copytree(FIXTURE, self.root / "harness/project")
        result = subprocess.run([sys.executable, str(CORE / "scripts/render_adapters.py"),
                                 "--root", str(self.root)], capture_output=True, text=True, env=ENV)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_claude_and_codex_agents_carry_the_role(self):
        models = json.loads(read("adapters/models.json"))
        core_text = read(f"roles/{ROLE}.md").strip()
        claude = (self.root / f".claude/agents/{ROLE}.md").read_text()
        front, body = claude.split("---", 2)[1:]
        self.assertIn(f'name: "{ROLE}"', front)
        self.assertIn(f'model: "{models["claude"]["critical-review"]["model"]}"', front)
        self.assertIn(f'effort: "{models["claude"]["critical-review"]["effort"]}"', front)
        for tool in ("Read", "Grep", "Glob", "Bash", "Write"):
            self.assertIn(tool, front)
        self.assertNotIn("Edit", front)
        self.assertIn(core_text, body)
        codex = tomllib.loads((self.root / f".codex/agents/{ROLE}.toml").read_text())
        self.assertEqual(codex["sandbox_mode"], "read-only")
        self.assertEqual(codex["model"], models["codex"]["critical-review"]["model"])
        self.assertEqual(codex["model_reasoning_effort"],
                         models["codex"]["critical-review"]["effort"])
        self.assertIn(core_text, codex["developer_instructions"])


class DevPanelTest(unittest.TestCase):
    def setUp(self):
        self.text = read("skills/pharos-dev/SKILL.md")

    def tier_panel(self, tier: str) -> str:
        table = self.text.split("## Tier decides the process", 1)[1]
        rows = [row for row in table_rows(table) if row and row[0].startswith(tier + " ")]
        self.assertEqual(len(rows), 1, tier)
        return rows[0][-1]

    def test_the_tier_table_puts_the_architecture_lens_on_tier_two_and_three(self):
        self.assertRegex(self.tier_panel("2"), r"(?i)architecture")
        tier3 = self.tier_panel("3")
        self.assertTrue(tier3.startswith("+") or re.search(r"(?i)architecture", tier3), tier3)
        for tier in ("0", "1"):
            self.assertNotRegex(self.tier_panel(tier), r"(?i)architecture")

    def test_the_panel_names_the_reviewer_on_every_tier_two_three_milestone(self):
        panel = section(self.text, "QA and panel")
        self.assertIn(f"`{ROLE}`", panel)
        self.assertRegex(panel, r"(?i)every Tier 2[–-]3 milestone")
        self.assertRegex(panel, r"(?i)not only (the |a )?PR")

    def test_the_milestone_runs_the_architecture_check(self):
        panel = section(self.text, "QA and panel")
        self.assertRegex(panel, r"arch_check\.py --base <milestone base>")
        self.assertIn("architecture.json", panel)
        self.assertIn("gates.json", panel)


class DiscoverPlacementTest(unittest.TestCase):
    def setUp(self):
        self.text = read("skills/pharos-discover/SKILL.md")

    def test_every_spec_carries_placement(self):
        placement = section(self.text, "Placement")
        for needle in ("layer", "module", "file"):
            self.assertIn(needle, placement)
        self.assertIn("architecture", placement)
        self.assertRegex(placement, r"not applicable: <reason>")
        self.assertRegex(placement, r"(?i)harness-only|docs-only")

    def test_the_spec_template_and_the_writing_step_include_placement(self):
        template = self.text.split("## Sources and output", 1)[1].split("\n## Red flags", 1)[0]
        self.assertRegex(template, r"(?m)^## Placement$")
        writing = next(line for line in self.text.splitlines() if "specs/YYYY-MM-DD" in line)
        start = self.text.index(writing)
        self.assertIn("Placement", self.text[start:start + 600])


class DesignReviewMapTest(unittest.TestCase):
    def test_design_review_reads_the_map_before_the_ownership_grid(self):
        text = read("skills/design-review/SKILL.md")
        grid = text.index("Build the ownership grid")
        for name in ("architecture.json", "architecture.md"):
            with self.subTest(name=name):
                self.assertIn(name, text)
                self.assertLess(text.index(name), grid)


class OverlayDocTest(unittest.TestCase):
    def setUp(self):
        self.text = read("docs/overlay.md")

    def test_architecture_json_keys_defaults_and_exit_codes(self):
        body = section(self.text, "architecture.json")
        for key in ("layers", "paths", "may_depend_on", "imports", "files", "pattern", "target",
                    "resolve", "placement", "kind", "allowed", "claim", "naming", "roots"):
            with self.subTest(key=key):
                self.assertIn(f"`{key}`", body)
        self.assertIn("arch_check.py", body)
        self.assertRegex(body, r"--base")
        self.assertRegex(body, r"--all")
        for code in ("0", "1", "2"):
            self.assertRegex(body, rf"\b{code}\b")
        self.assertRegex(body, r"(?i)default")
        self.assertRegex(body, r"```json")

    def test_architecture_md_is_documented(self):
        body = section(self.text, "architecture.md")
        self.assertIn(ROLE, body)
        self.assertIn("design-review", body)

    def test_both_files_are_in_the_tables(self):
        glance = section(self.text, "Files at a glance")
        combine = section(self.text, "How overlay values combine with the core")
        for name in ("architecture.json", "architecture.md"):
            with self.subTest(name=name):
                self.assertTrue(any(f"`{name}`" in row[0] for row in table_rows(glance)), name)
                self.assertTrue(any(name in row[0] for row in table_rows(combine)), name)

    def test_the_core_role_list_names_every_core_role(self):
        roles = section(self.text, "roles/")
        for name in json.loads(read("roles/manifest.json")):
            with self.subTest(role=name):
                self.assertIn(f"`{name}`", roles)


class ReadmeTest(unittest.TestCase):
    def setUp(self):
        self.text = read("README.md")

    def test_the_roles_section_counts_and_lists_every_core_role(self):
        manifest = json.loads(read("roles/manifest.json"))
        roles = section(self.text, "Roles")
        self.assertIn(f"{NUMBERS[len(manifest)]} core roles", roles)
        for name in manifest:
            with self.subTest(role=name):
                self.assertIn(f"`{name}`", roles)

    def test_the_scripts_table_has_arch_check(self):
        rows = table_rows(section(self.text, "Scripts"))
        self.assertTrue(any("arch_check.py" in row[0] for row in rows))

    def test_the_changelog_records_the_reviewer_and_placement(self):
        unreleased = read("CHANGELOG.md").split("## 1.2.0", 1)[1].split("\n## ", 1)[0]
        self.assertIn(ROLE, unreleased)
        self.assertIn("Placement", unreleased)


if __name__ == "__main__":
    unittest.main()
