"""The architecture check: layers and their allowed dependencies, files outside every layer,
files of a kind outside their places, and names off their pattern.

The declared architecture is the project's (`architecture.json` in the overlay); these cases
run on the fixture project's file in tests/fixture/harness/project, on the example overlay in
examples/project, on broken copies of it, and without one.
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
SCRIPT = CORE / "scripts" / "arch_check.py"
OVERLAY = Path(__file__).resolve().parent / "fixture" / "harness" / "project"
EXAMPLE = CORE / "examples" / "project"
CONFIG = "harness/project/architecture.json"

# A tree that keeps every rule of the fixture's architecture.json.
CLEAN = {
    "README.md": "# Shop\n",
    "docs/guide.md": "# Guide\n",
    "app/config.py": "SETTINGS = {}\n",
    "app/items/service.py": "import os\nfrom app.config import SETTINGS\n\n\ndef items():\n    return []\n",
    "app/auth/session.py": (
        "from __future__ import annotations\n"
        "from ..items import service\n"
        "from . import tokens\n"
        "from app.config import SETTINGS\n"
    ),
    "app/auth/tokens.py": "TOKENS = []\n",
    "app/migrations/0001_init.sql": "CREATE TABLE items(id int);\n",
    "api/items.go": (
        "package api\n"
        "\n"
        "import (\n"
        "\t\"net/http\"\n"
        "\n"
        "\titems \"example.com/shop/app/items\"\n"
        "\t\"example.com/shop/api/internal\"\n"
        "\t\"example.com/shop/vendor/lib\"\n"
        "\t\"github.com/other/lib\"\n"
        ")\n"
        "\n"
        "func list(w http.ResponseWriter) { fmt.Println(\"ok\") }\n"
    ),
    "api/internal/errors.go": "package internal\n",
    "cli/main.go": "package main\n\nimport \"example.com/shop/api\"\n",
    "ui/types.ts": "export type Item = { id: number };\n",
    "ui/src/main.ts": (
        "import { list } from \"@shop/views/list\";\n"
        "import React from \"react\";\n"
    ),
    "ui/src/views/list.ts": "import { render } from \"./render\";\nexport const list = render;\n",
    "ui/src/views/render.ts": "export const render = () => null;\n",
    "harness/project/notes.md": "outside every root\n",
}

GO_BLOCK = (
    "package main\n"
    "\n"
    "import (\n"
    "\t\"fmt\"\n"
    "\n"
    "\t\"example.com/shop/app/store\"\n"
    ")\n"
)
GO_SINGLE = "package api\n\nimport \"example.com/shop/ui/src/widgets\"\n"
TS_RELATIVE = (
    "import { render } from \"./render\";\n"
    "import type { Item } from \"../../../api/dto\";\n"
)


class ArchCheckCase(unittest.TestCase):
    overlay = True

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        if self.overlay:
            shutil.copytree(OVERLAY, self.root / "harness" / "project", dirs_exist_ok=True)
        for name, text in CLEAN.items():
            self.write(name, text)
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.base = self.git("rev-parse", "HEAD").strip()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env, text=True)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def run_check(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True)

    def lines(self, result):
        return result.stdout.strip().splitlines()

    def findings(self, *args):
        result = self.run_check(*args, "--format", "json")
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return json.loads(result.stdout)["findings"]


class FindingsTest(ArchCheckCase):
    def test_the_clean_tree_is_clean(self):
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("clean", self.lines(result)[-1])
        self.assertEqual(self.findings("--all"), [])

    def test_a_forbidden_go_import_in_a_block_is_reported_with_its_line(self):
        self.write("cli/main.go", GO_BLOCK)
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines(result)[0],
                         "cli/main.go:6 — cli may not depend on app (example.com/shop/app/store)")
        self.assertIn("1 finding", self.lines(result)[-1])

    def test_a_forbidden_single_line_go_import_is_reported(self):
        self.write("api/render.go", GO_SINGLE)
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines(result)[:-1],
                         ["api/render.go:3 — api may not depend on ui (example.com/shop/ui/src/widgets)"])

    def test_a_forbidden_relative_import_resolves_against_the_file_directory(self):
        self.write("ui/src/views/list.ts", TS_RELATIVE)
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines(result)[:-1],
                         ["ui/src/views/list.ts:2 — ui may not depend on api (../../../api/dto)"])

    def test_a_python_relative_import_leaving_its_layer_is_reported(self):
        # Two dots climb one package: app/ → the checkout root → cli/.
        self.write("app/tool.py", "from ..cli import commands\n")
        self.assertEqual(self.findings("--all"), [
            {"path": "app/tool.py", "line": 1, "rule": "dependency",
             "message": "app may not depend on cli (..cli)"}])

    def test_a_file_under_a_root_outside_every_layer_is_reported(self):
        self.write("ui/legacy/old.ts", "export const old = 1;\n")
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines(result)[:-1],
                         ["ui/legacy/old.ts — not in any layer of architecture.json"])

    def test_a_file_of_a_kind_outside_its_places_is_reported(self):
        self.write("app/items/0002_extra.sql", "ALTER TABLE items ADD name text;\n")
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines(result)[:-1],
                         ["app/items/0002_extra.sql — SQL migrations live in app/migrations/"])

    def test_a_name_off_its_pattern_is_reported(self):
        self.write("app/migrations/add-items.sql", "ALTER TABLE items ADD name text;\n")
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.lines(result)[:-1],
                         ["app/migrations/add-items.sql — a migration is named NNNN_snake_case.sql"])

    def test_json_lists_every_finding_with_its_rule(self):
        self.write("cli/main.go", GO_BLOCK)
        self.write("ui/legacy/old.ts", "export const old = 1;\n")
        self.write("app/items/0002_extra.sql", "SELECT 1;\n")
        self.write("app/migrations/add-items.sql", "SELECT 1;\n")
        result = self.run_check("--all", "--format", "json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(json.loads(result.stdout)["findings"], [
            {"path": "app/items/0002_extra.sql", "line": None, "rule": "placement",
             "message": "SQL migrations live in app/migrations/"},
            {"path": "app/migrations/add-items.sql", "line": None, "rule": "naming",
             "message": "a migration is named NNNN_snake_case.sql"},
            {"path": "cli/main.go", "line": 6, "rule": "dependency",
             "message": "cli may not depend on app (example.com/shop/app/store)"},
            {"path": "ui/legacy/old.ts", "line": None, "rule": "layer",
             "message": "not in any layer of architecture.json"},
        ])


class ChangedFilesTest(ArchCheckCase):
    def setUp(self):
        super().setUp()
        # A violation already at the base: --base never reports it, --all does.
        self.write("cli/old.go", GO_BLOCK)
        self.git("add", ".")
        self.git("commit", "-qm", "old violation")
        self.base = self.git("rev-parse", "HEAD").strip()

    def change_everything(self):
        self.write("api/render.go", GO_SINGLE)                       # committed after the base
        self.git("add", ".")
        self.git("commit", "-qm", "render")
        self.write("app/items/0002_extra.sql", "SELECT 1;\n")        # staged
        self.git("add", "app/items/0002_extra.sql")
        self.write("ui/src/views/list.ts", TS_RELATIVE)              # unstaged
        self.write("ui/legacy/old.ts", "export const old = 1;\n")   # untracked
        (self.root / "docs/guide.md").unlink()                      # deleted: skipped
        self.git("mv", "app/migrations/0001_init.sql", "app/migrations/bad-name.sql")  # renamed

    def test_base_checks_only_the_changed_files_untracked_included(self):
        self.change_everything()
        self.assertEqual([f["path"] for f in self.findings("--base", self.base)], [
            "api/render.go", "app/items/0002_extra.sql", "app/migrations/bad-name.sql",
            "ui/legacy/old.ts", "ui/src/views/list.ts"])

    def test_all_checks_every_tracked_and_untracked_file(self):
        self.change_everything()
        self.assertEqual([f["path"] for f in self.findings("--all")], [
            "api/render.go", "app/items/0002_extra.sql", "app/migrations/bad-name.sql",
            "cli/old.go", "ui/legacy/old.ts", "ui/src/views/list.ts"])

    def test_without_base_or_all_the_base_is_the_merge_base_with_main(self):
        self.git("checkout", "-qb", "feature")
        self.write("ui/legacy/old.ts", "export const old = 1;\n")
        self.git("add", ".")
        self.git("commit", "-qm", "feature work")
        self.assertEqual([f["path"] for f in self.findings()], ["ui/legacy/old.ts"])

    def test_an_unchanged_tree_is_clean(self):
        result = self.run_check("--base", self.base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class ConfigurationTest(ArchCheckCase):
    def config(self, text):
        self.write(CONFIG, text)

    def test_no_architecture_json_turns_the_check_off_with_a_note(self):
        (self.root / CONFIG).unlink()
        self.write("ui/legacy/old.ts", "export const old = 1;\n")
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("architecture check is off: no architecture.json in harness/project",
                      result.stdout)

    def test_each_broken_configuration_exits_2_naming_the_problem(self):
        layer = {"name": "app", "paths": ["app/**"]}
        cases = {
            "unknown top-level key": ({"layers": [layer], "extras": 1}, "extras"),
            "unknown layer key": ({"layers": [{**layer, "colour": "red"}]}, "colour"),
            "layers not a list": ({"layers": {"app": ["app/**"]}}, "layers"),
            "layer without paths": ({"layers": [{"name": "app"}]}, "paths"),
            "duplicate layer": ({"layers": [layer, layer]}, "app"),
            "unknown dependency": ({"layers": [{**layer, "may_depend_on": ["ghost"]}]}, "ghost"),
            "bad import regex": ({"layers": [layer], "imports": [
                {"files": "**/*.py", "pattern": "(", "resolve": {}}]}, "pattern"),
            "import regex without target": ({"layers": [layer], "imports": [
                {"files": "**/*.py", "pattern": "import (\\w+)"}]}, "target"),
            "resolve not an object": ({"layers": [layer], "imports": [
                {"files": "**/*.py", "pattern": "import (?P<target>\\w+)",
                 "resolve": ["app"]}]}, "resolve"),
            "allowed not a list": ({"placement": [
                {"kind": "sql", "files": "**/*.sql", "allowed": "app/**", "claim": "x"}]},
                "allowed"),
            "bad naming regex": ({"naming": [
                {"files": "**/*.py", "pattern": "[", "claim": "x"}]}, "pattern"),
            "naming without claim": ({"naming": [
                {"files": "**/*.py", "pattern": "\\w+\\.py"}]}, "claim"),
            "roots not a list": ({"roots": "app/**"}, "roots"),
        }
        for name, (config, problem) in cases.items():
            with self.subTest(name):
                self.config(json.dumps(config))
                result = self.run_check("--all")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn(problem, result.stderr)
                self.assertNotIn("clean", result.stdout)
        for name, text in {"invalid JSON": "{", "not an object": "[]"}.items():
            with self.subTest(name):
                self.config(text)
                result = self.run_check("--all")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("architecture.json", result.stderr)

    def test_a_file_in_two_layers_is_a_configuration_error_naming_both(self):
        self.config(json.dumps({"layers": [
            {"name": "core", "paths": ["app/**"]},
            {"name": "catalogue", "paths": ["app/items/**"]}]}))
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        for word in ("app/items/service.py", "core", "catalogue"):
            self.assertIn(word, result.stderr)

    def test_usage_errors_exit_2(self):
        for args in (["--base", "HEAD", "--all"], ["--base", "no-such-revision"]):
            with self.subTest(args=args):
                self.assertEqual(self.run_check(*args).returncode, 2)


class ExampleOverlayTest(ArchCheckCase):
    overlay = False
    TREE = {
        "app/settings.py": "DEBUG = False\n",
        "app/schemas.py": "from app.settings import DEBUG\n",
        "app/routes/items.py": "from ..schemas import Item\nfrom app.schemas import Item\n",
        "app/migrations/0001_init.sql": "CREATE TABLE items(id int);\n",
        "web/src/types.ts": "export type Item = { id: number };\n",
        "web/src/api-client/index.ts": "import type { Item } from \"../types\";\n",
        "web/src/app.ts": "import type { Item } from \"@/types\";\nimport React from \"react\";\n",
        "docs/data-model.md": "# Data model\n",
    }

    def setUp(self):
        super().setUp()
        shutil.copytree(EXAMPLE, self.root / "harness" / "project", dirs_exist_ok=True)
        for name, text in self.TREE.items():
            self.write(name, text)

    def test_the_example_overlay_is_valid_and_clean_on_its_tree(self):
        result = self.run_check("--all")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_the_example_overlay_catches_a_cross_layer_import(self):
        self.write("web/src/bad.ts", "import { x } from \"../../app/schemas\";\n")
        self.assertEqual(self.findings("--all"), [
            {"path": "web/src/bad.ts", "line": 1, "rule": "dependency",
             "message": "web may not depend on api (../../app/schemas)"}])


if __name__ == "__main__":
    unittest.main()
