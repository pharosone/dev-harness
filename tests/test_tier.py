"""A diff's tier is the strictest of its path classes and its size, with the deciding reasons.

The tier table is the project's (`tiers.json` in the overlay); these cases run on the fixture
project's table in tests/fixture/harness/project, and without one.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
TIER = CORE / "scripts" / "tier.py"
OVERLAY = Path(__file__).resolve().parent / "fixture" / "harness" / "project"

BASELINE = {
    "README.md": 3,
    "docs/guide.md": 40,
    "docs/specs/s.md": 5,
    "app/auth/session.py": 5,
    "app/auth/test_session.py": 5,
    "app/billing/keys.py": 5,
    "app/items/service.py": 5,
    "app/items/test_service.py": 5,
    "app/api/items.py": 5,
    "app/api/items_export.py": 5,
    "app/migrations/0001_init.sql": 5,
    "app/config.py": 5,
    "cli/go.sum": 5,
    "ui/package-lock.json": 5,
    "ui/lib/a.test.ts": 5,
    "ui/AGENTS.md": 5,
    "AGENTS.md": 5,
    "CLAUDE.md": 5,
    ".github/workflows/ci.yml": 5,
    "harness/project/rules/x.md": 5,
    "deploy/demo/data/a.yaml": 5,
    "deploy/chart/values.yaml": 5,
}


class TierTest(unittest.TestCase):
    overlay = True

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.root = self.temp / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.root / ".gitignore").write_text(".harness/\n")
        if self.overlay:
            shutil.copytree(OVERLAY, self.root / "harness" / "project", dirs_exist_ok=True)
        for name, count in BASELINE.items():
            self.write(name, "".join(f"{name} {i}\n" for i in range(count)))
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env, text=True)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def append(self, name, count):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write("".join(f"added {i}\n" for i in range(count)))

    def replace(self, name, count):
        """Rewrite the first `count` lines: `count` added plus `count` deleted."""
        path = self.root / name
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        path.write_text("".join(f"changed {i}\n" for i in range(count)) + "".join(lines[count:]),
                        encoding="utf-8")

    def reset(self):
        self.git("reset", "-q", "--hard")
        self.git("clean", "-qfd")

    def run_tier(self, *args, cwd=None, **env):
        return subprocess.run([sys.executable, str(TIER), *args], cwd=cwd or self.root,
                              env={**self.env, **env}, capture_output=True, text=True)

    def tier(self, base="HEAD", **env):
        result = self.run_tier("--base", base, "--json", **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def assertTier(self, expected, base="HEAD", **env):
        report = self.tier(base, **env)
        self.assertEqual(report["tier"], expected, report)
        return report

    def paths(self, report):
        return {(r["path"], r["tier"]) for r in report["reasons"] if r["path"] is not None}

    def size_reason(self, report):
        found = [r for r in report["reasons"] if r["path"] is None]
        return found[0] if found else None


class FixtureTierTest(TierTest):
    """The fixture project's tiers.json, read from the overlay at harness/project."""

    def test_an_empty_diff_is_tier_zero(self):
        self.assertEqual(self.tier(), {"tier": 0, "lines": 0, "reasons": []})

    def test_small_docs_change_is_tier_zero_with_its_rule(self):
        self.append("docs/guide.md", 3)
        report = self.assertTier(0)
        self.assertEqual(report["lines"], 3)
        self.assertEqual(report["reasons"],
                         [{"path": "docs/guide.md", "tier": 0, "rule": "docs/**"}])

    def test_root_markdown_is_tier_zero(self):
        self.append("README.md", 1)
        self.assertEqual(self.assertTier(0)["reasons"][0]["rule"], "**/*.md")

    def test_tier_zero_line_limit_is_inclusive(self):
        self.append("docs/guide.md", 10)
        self.assertTier(0)
        self.reset()
        self.append("docs/guide.md", 11)
        report = self.assertTier(1)
        self.assertEqual(report["lines"], 11)
        size = self.size_reason(report)
        self.assertIsNotNone(size, report)
        self.assertEqual(size["tier"], 1)
        self.assertIn("tier0_lines", size["rule"])
        self.assertEqual(self.paths(report), set(), "a tier-0 path does not decide tier 1")

    def test_lines_count_added_plus_deleted(self):
        self.replace("docs/guide.md", 5)
        self.assertEqual(self.assertTier(0)["lines"], 10)
        self.reset()
        self.replace("docs/guide.md", 6)
        self.assertEqual(self.assertTier(1)["lines"], 12)

    def test_large_docs_change_is_tier_two_by_size(self):
        self.append("docs/guide.md", 101)
        report = self.assertTier(2)
        self.assertEqual(self.size_reason(report)["tier"], 2)
        self.assertIn("tier1_lines", self.size_reason(report)["rule"])
        self.assertEqual(self.paths(report), set())

    def test_test_files_and_lockfiles_are_tier_one(self):
        for name in ("app/items/test_service.py", "ui/lib/a.test.ts", "ui/lib/b.test.tsx",
                     "cli/tests/test_client.py", "cli/pkg/client_test.go",
                     "ui/package-lock.json", "cli/go.sum"):
            with self.subTest(name=name):
                self.append(name, 1)
                report = self.assertTier(1)
                self.assertEqual(self.paths(report), {(name, 1)})
                self.reset()

    def test_tier_one_line_limit_is_inclusive(self):
        self.append("app/items/test_service.py", 100)
        self.assertTier(1)
        self.reset()
        self.append("app/items/test_service.py", 101)
        report = self.assertTier(2)
        self.assertEqual(self.size_reason(report)["tier"], 2)

    def test_unmatched_paths_are_tier_two(self):
        for name in ("app/items/service.py", "app/api/items.py", "app/auth2/x.py",
                     "app/authz/x.py", "app/configs.py", "app/config.py.bak", "ui/lib/csv.ts",
                     "deploy.sh"):
            with self.subTest(name=name):
                self.append(name, 1)
                report = self.assertTier(2)
                self.assertEqual(report["reasons"], [{"path": name, "tier": 2,
                                                      "rule": "unclassified"}])
                self.reset()

    def test_specs_are_tier_two_not_docs(self):
        self.append("docs/specs/s.md", 1)
        report = self.assertTier(2)
        self.assertEqual(report["reasons"], [{"path": "docs/specs/s.md", "tier": 2,
                                              "rule": "docs/specs/**"}])

    def test_tier_three_paths(self):
        for name in ("app/auth/session.py", "app/auth/test_session.py", "app/billing/keys.py",
                     "app/billing/sub/ledger.py", "app/migrations/0002_x.sql", "app/config.py",
                     "app/api/items_export.py", ".github/workflows/ci.yml",
                     "deploy/chart/values.yaml", "deploy/demonstration/x.yaml",
                     "harness/project/rules/x.md", "harness/core/scripts/x.py",
                     ".claude/agents/x.md", ".codex/agents/x.toml", ".agents/skills/x/SKILL.md",
                     "AGENTS.md", "CLAUDE.md", "ui/AGENTS.md", "cli/go/CLAUDE.md"):
            with self.subTest(name=name):
                self.append(name, 1)
                report = self.assertTier(3)
                self.assertEqual(self.paths(report), {(name, 3)})
                self.reset()

    def test_exports_build_ci_and_agent_docs_are_tier_three(self):
        for name in ("app/api/exports.py", "ui/components/x/export-button.tsx", "Dockerfile",
                     "ui/Dockerfile", "app/Dockerfile.worker", "app/worker.Dockerfile",
                     "docker-compose.yaml", "docker-compose.override.yml",
                     "deploy/prod/docker-compose.prod.yaml", ".gitleaks.toml",
                     ".github/workflows/deploy.yml", "docs/agents/domain.md",
                     "docs/agents/traps.md"):
            with self.subTest(name=name):
                self.append(name, 2)
                report = self.assertTier(3)
                self.assertEqual(self.paths(report), {(name, 3)})
                self.reset()

    def test_invariant_scanners_are_tier_three_although_tests_are_tier_one(self):
        for name in ("app/test_invariants.py", "app/api/test_invariants_auth.py",
                     "app/items/sub/test_invariants_scope.py"):
            with self.subTest(name=name):
                self.append(name, 2)
                report = self.assertTier(3)
                self.assertEqual(report["reasons"], [{"path": name, "tier": 3,
                                                      "rule": "**/test_invariants*.py"}])
                self.reset()

    def test_agent_instructions_and_build_files_beat_the_demo_override(self):
        for name, rule in (("deploy/demo/AGENTS.md", "**/AGENTS.md"),
                           ("deploy/demo/data/CLAUDE.md", "**/CLAUDE.md"),
                           ("deploy/demo/app/Dockerfile", "**/Dockerfile"),
                           ("deploy/demo/app/docker-compose.yaml", "**/docker-compose*.yaml")):
            with self.subTest(name=name):
                self.append(name, 2)
                report = self.assertTier(3)
                self.assertEqual(report["reasons"], [{"path": name, "tier": 3, "rule": rule}])
                self.reset()

    def test_demo_code_is_deploy_not_demo_data(self):
        for name in ("deploy/demo/seed.sh", "deploy/demo/data/.gitattributes",
                     "deploy/demo/tool.py"):
            with self.subTest(name=name):
                self.append(name, 1)
                report = self.assertTier(3)
                self.assertEqual(report["reasons"], [{"path": name, "tier": 3,
                                                      "rule": "deploy/**"}])
                self.reset()

    def test_docs_other_than_agent_docs_keep_their_tier(self):
        for name, tier in (("docs/guide.md", 0), ("docs/agents-old/x.md", 0),
                           ("docs/specs/s.md", 2), ("docs/agents/config.md", 3)):
            with self.subTest(name=name):
                self.append(name, 1)
                self.assertEqual(self.paths(self.assertTier(tier)), {(name, tier)})
                self.reset()

    def test_one_tier_three_path_decides_and_others_are_not_reasons(self):
        self.append("app/auth/session.py", 1)
        self.append("docs/guide.md", 3)
        self.append("app/items/service.py", 2)
        report = self.assertTier(3)
        self.assertEqual(report["lines"], 6)
        self.assertEqual(report["reasons"], [{"path": "app/auth/session.py", "tier": 3,
                                              "rule": "app/auth/**"}])

    def test_path_and_size_both_deciding_are_both_reported(self):
        self.append("app/items/service.py", 150)
        report = self.assertTier(2)
        self.assertEqual(self.paths(report), {("app/items/service.py", 2)})
        self.assertEqual(self.size_reason(report)["tier"], 2)

    def test_demo_data_is_tier_zero_despite_deploy(self):
        self.append("deploy/demo/data/a.yaml", 3)
        report = self.assertTier(0)
        self.assertEqual(report["reasons"], [{"path": "deploy/demo/data/a.yaml", "tier": 0,
                                              "rule": "deploy/demo/**/*.yaml"}])
        self.reset()
        self.append("deploy/demo/data/a.yaml", 50)
        self.assertTier(1)
        self.reset()
        for name in ("deploy/demo/data/README.md", "deploy/demo/data/x/files/r.pdf",
                     "deploy/demo/data/x/files/s.png"):
            with self.subTest(name=name):
                self.append(name, 2)
                self.assertEqual(self.paths(self.assertTier(0)), {(name, 0)})
                self.reset()

    def test_untracked_files_count_their_lines(self):
        self.write("docs/new.md", "".join(f"n {i}\n" for i in range(12)))
        report = self.assertTier(1)
        self.assertEqual(report["lines"], 12)
        self.reset()
        self.write("docs/short.md", "a\nb")
        self.assertEqual(self.assertTier(0)["lines"], 2)
        self.reset()
        self.write("app/auth/new.py", "TOKEN_TTL = 60\n")
        self.assertEqual(self.paths(self.assertTier(3)), {("app/auth/new.py", 3)})

    def test_deleting_or_moving_a_tier_three_file_is_tier_three(self):
        self.git("rm", "-q", "app/billing/keys.py")
        self.assertEqual(self.paths(self.assertTier(3)), {("app/billing/keys.py", 3)})
        self.reset()
        self.git("mv", "app/auth/session.py", "app/items/session.py")
        report = self.assertTier(3)
        self.assertIn(("app/auth/session.py", 3), self.paths(report))

    def test_base_may_be_an_earlier_commit(self):
        self.append("app/auth/session.py", 1)
        self.git("commit", "-qam", "auth change")
        self.assertTier(0)
        self.assertTier(3, base="HEAD~1")

    def test_reasons_are_sorted_by_path(self):
        self.append("ui/lib/a.test.ts", 1)
        self.append("cli/go.sum", 1)
        report = self.assertTier(1)
        self.assertEqual([r["path"] for r in report["reasons"]], ["cli/go.sum", "ui/lib/a.test.ts"])

    def test_text_output(self):
        self.append("app/auth/session.py", 2)
        self.append("docs/guide.md", 20)
        result = self.run_tier("--base", "HEAD", cwd=self.root / "app")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(lines[:2], ["tier=3", "lines=22"])
        self.assertIn("app/auth/session.py: tier 3 (app/auth/**)", lines)
        self.assertNotIn("docs/guide.md", result.stdout)
        self.reset()
        self.append("docs/guide.md", 11)
        lines = self.run_tier("--base", "HEAD").stdout.splitlines()
        self.assertEqual(lines[0], "tier=1")
        self.assertTrue(any(line.startswith("size: 11 lines > tier0_lines 10") for line in lines),
                        lines)

    def test_unknown_base_is_an_error(self):
        result = self.run_tier("--base", "no-such-ref")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no-such-ref", result.stderr)

    def test_fixture_table_shape(self):
        config = json.loads((OVERLAY / "tiers.json").read_text(encoding="utf-8"))
        self.assertEqual(config["limits"], {"tier0_lines": 10, "tier1_lines": 100})
        self.assertNotIn("deploy/demo/**", config["tier0_override"],
                         "the demo override covers demo data, not every file under deploy/demo")
        for key in ("tier3_override", "tier0_override", "tier3", "tier2", "tier1", "tier0"):
            self.assertIsInstance(config[key], list)
            self.assertTrue(all(isinstance(glob, str) and glob for glob in config[key]))


class OverlayLocationTest(TierTest):
    """tiers.json is read from the overlay the checkout names, never from the core."""

    overlay = False

    def table(self, where, commit=True, **lists):
        path = where / "tiers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({**lists, "limits": {"tier0_lines": 10, "tier1_lines": 100}}),
                        encoding="utf-8")
        if commit and path.is_relative_to(self.root):
            self.git("add", str(path))
            self.git("commit", "-qm", "tier table")

    def test_the_core_ships_no_tier_table(self):
        self.assertFalse((CORE / "tiers.json").exists(),
                         "a project's tier table belongs in its overlay, not in the core")

    def test_harness_project_points_at_another_overlay(self):
        self.table(self.root / "config" / "harness", tier3=["app/**"])
        self.append("app/items/service.py", 1)
        self.assertTier(2)  # harness/project holds no table: the default applies
        report = self.assertTier(3, HARNESS_PROJECT="config/harness")
        self.assertEqual(report["reasons"], [{"path": "app/items/service.py", "tier": 3,
                                              "rule": "app/**"}])
        outside = self.temp / "elsewhere"
        self.table(outside, tier1=["app/**"])
        self.assertTier(1, HARNESS_PROJECT=str(outside))

    def test_lists_are_optional_and_limits_required(self):
        self.table(self.root / "harness" / "project", tier0=["docs/**"])
        self.append("docs/guide.md", 1)
        self.assertEqual(self.assertTier(0)["reasons"],
                         [{"path": "docs/guide.md", "tier": 0, "rule": "docs/**"}])
        self.append("app/items/service.py", 1)
        self.assertIn(("app/items/service.py", 2), self.paths(self.assertTier(2)))
        (self.root / "harness" / "project" / "tiers.json").write_text(
            json.dumps({"tier0": ["docs/**"]}), encoding="utf-8")
        result = self.run_tier("--base", "HEAD")
        self.assertEqual(result.returncode, 2)
        self.assertIn("limits", result.stderr)

    def test_an_unusable_table_is_an_error(self):
        path = self.root / "harness" / "project" / "tiers.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        limits = {"tier0_lines": 1, "tier1_lines": 2}
        for text, expected in (("[]", "tiers.json"), ("{not json", "tiers.json"),
                               (json.dumps({"tier4": [], "limits": limits}), "tier4"),
                               (json.dumps({"tier3": "app/**", "limits": limits}), "tier3"),
                               (json.dumps({"tier3": [""], "limits": limits}), "tier3"),
                               (json.dumps({"limits": {"tier0_lines": -1, "tier1_lines": 2}}),
                                "limits")):
            with self.subTest(text=text):
                path.write_text(text, encoding="utf-8")
                result = self.run_tier("--base", "HEAD", "--json")
                self.assertEqual(result.returncode, 2, result.stdout)
                self.assertIn(expected, result.stderr)
                self.assertEqual(result.stdout, "")


class NoTierTableTest(TierTest):
    """Without tiers.json every change is Tier 2: nothing is classified lower by default."""

    overlay = False

    def test_an_empty_diff_is_still_tier_zero(self):
        self.assertEqual(self.tier(), {"tier": 0, "lines": 0, "reasons": []})

    def test_every_changed_path_is_tier_two_whatever_its_size(self):
        self.append("docs/guide.md", 1)
        self.append("app/auth/session.py", 300)
        self.write("docs/new.md", "new\n")
        report = self.assertTier(2)
        self.assertEqual(report["lines"], 302)
        self.assertEqual(report["reasons"], [
            {"path": "app/auth/session.py", "tier": 2, "rule": "no tiers.json"},
            {"path": "docs/guide.md", "tier": 2, "rule": "no tiers.json"},
            {"path": "docs/new.md", "tier": 2, "rule": "no tiers.json"},
        ])

    def test_text_output_names_the_missing_table(self):
        self.append("README.md", 1)
        result = self.run_tier("--base", "HEAD")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ["tier=2", "lines=1", "README.md: tier 2 (no tiers.json)"])

    def test_unknown_base_is_still_an_error(self):
        result = self.run_tier("--base", "no-such-ref")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no-such-ref", result.stderr)


del TierTest  # the base class holds no cases of its own


if __name__ == "__main__":
    unittest.main()
