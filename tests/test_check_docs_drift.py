#!/usr/bin/env python3
"""Integration tests for the stateless, runtime-independent documentation drift CLI.

Each case uses a throwaway Git repository and isolated Git configuration. The document
mappings are the project's (`drift.json` in the overlay); the cases run on the fixture
project's mappings in tests/fixture/harness/project, and without them.
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
SCRIPT = CORE / "scripts" / "check_docs_drift.py"
OVERLAY = Path(__file__).resolve().parent / "fixture" / "harness" / "project"

REGISTRY = "api/registry.go"
ROUTES = "api/routes.go"
ITEMS = "api/items.go"
ITEMS_TEST = "api/items_test.go"
REGISTRY_CLAIM = "route registry"
CONFIG_DOC = "docs/config.md"
DOMAIN_DOC = "docs/domain.md"
LOG = "harness/project/HARNESS_LOG.md"

# The baseline already holds a registered operation and router routes, so an edit that adds no
# route still has route lines in its diff context: only added lines may count.
ROUTES_GO = """package api

import "net/http"

func registerOrders(api Registrar, h Handler) {
	api.Register(Operation{
		OperationID: "list-orders",
		Method:      http.MethodGet,
		Path:        "/api/v1/orders",
	}, h)
}

func mountHealth(r Router, h http.HandlerFunc) {
	r.Get("/healthz", h)
	r.Get("/readyz", h)
}
"""

REGISTRY_GO = """package api

// Excluded lists operations that are deliberately not registered, with the reason.
var Excluded = map[string]string{
	"list-orders": "covered by the orders listing",
}
"""

BASELINE = {
    "AGENTS.md": "# AGENTS.md\n",
    CONFIG_DOC: "# Configuration\n",
    DOMAIN_DOC: "# Domain model\n",
    LOG: "# Harness log\n",
    "harness/project/roles/implementer.md": "# Project specifics\n",
    "harness/project/skills/deploy/SKILL.md": "# Deploy\n",
    "harness/core/roles/implementer.md": "# Implementer\n",
    "harness/core/skills/dev/SKILL.md": "# Development\n",
    ".claude/agents/implementer.md": "generated\n",
    ".codex/hooks.json": "{}\n",
    "app/config.py": "SETTINGS = {}\n",
    "app/items/service.py": "def items():\n    return []\n",
    "app/migrations/0001_init.sql": "CREATE TABLE items(id int);\n",
    "api/dto.go": "package api\n",
    "ui/types.ts": "export type Placeholder = never;\n",
    ROUTES: ROUTES_GO,
    REGISTRY: REGISTRY_GO,
}

OPERATION = """
func registerItems(api Registrar, h Handler) {
	api.Register(Operation{
		OperationID: "list-items",
		Method:      http.MethodGet,
		Path:        "/api/v1/items",
	}, h)
}
"""

ROUTER_GET = """
func mountItems(r Router, h http.HandlerFunc) {
	r.Get("/api/v1/items", h)
}
"""

# A helper with string literals that look route-ish but register nothing: a query lookup and
# a URL-path prefix check.
HELPER = """
func itemStatus(req *http.Request) string {
	if !strings.HasPrefix(req.URL.Path, "/api/v1/") {
		return ""
	}
	return req.URL.Query().Get("status")
}
"""

REGISTRY_ENTRY = """
func init() {
	Excluded["list-items"] = "staff-only listing"
}
"""


def hermetic_env():
    """No inherited GIT_* (a hook run from inside git would aim at the wrong repo), no global
    git config (signing, hooks or a default branch on the developer's machine) and no overlay
    override from the developer's shell."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


class Repo:
    def __init__(self, root):
        self.root = root
        self.env = hermetic_env()

    def git(self, *args):
        r = subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, capture_output=True, text=True
        )
        if r.returncode != 0:
            raise AssertionError(f"git {' '.join(args)} failed: {r.stderr}")
        return r.stdout

    def write(self, path, text):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w") as f:
            f.write(text)

    def append(self, path, text):
        with open(os.path.join(self.root, path), "a") as f:
            f.write(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "--no-verify", "-m", message)

    def run_cli(self, *extra, base="main", cwd=None, script=SCRIPT, **env):
        return subprocess.run(
            [sys.executable, str(script), "--base", base, *extra],
            cwd=cwd or self.root,
            env={**self.env, **env},
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=30,
        )


class DriftTest(unittest.TestCase):
    overlay = True

    def new_repo(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(tmp.name)
        repo.git("init", "-q", "-b", "main")
        repo.git("config", "user.name", "Hook Test")
        repo.git("config", "user.email", "hook-test@example.invalid")
        repo.git("config", "commit.gpgsign", "false")
        if self.overlay:
            shutil.copytree(OVERLAY, os.path.join(tmp.name, "harness", "project"),
                            dirs_exist_ok=True)
        for path, text in BASELINE.items():
            repo.write(path, text)
        repo.commit("baseline")
        repo.git("checkout", "-q", "-b", "feat/x")
        return repo

    def assertRegistryFinding(self, result):
        detail = f"exit={result.returncode}\nstderr:\n{result.stderr}"
        self.assertEqual(result.returncode, 1, detail)
        self.assertIn(REGISTRY, (result.stdout + result.stderr), detail)
        self.assertIn(REGISTRY_CLAIM, (result.stdout + result.stderr), detail)

    def assertNoFinding(self, result):
        detail = f"exit={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        self.assertNotIn(REGISTRY_CLAIM, (result.stdout + result.stderr), detail)
        self.assertNotIn(REGISTRY, (result.stdout + result.stderr), detail)
        self.assertEqual(result.returncode, 0, detail)
        self.assertEqual(result.stderr, "", detail)

    def assertDrift(self, result, doc):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(doc, result.stdout + result.stderr)

    def findings(self, result):
        self.assertIn(result.returncode, (0, 1), result.stderr)
        return json.loads(result.stdout)["findings"]


class RegistryRuleTest(DriftTest):
    # Fixture guard: the baseline keeps every existing rule quiet, so a pass of a no-finding
    # case is the new rule's doing, not an artefact of the repo shape.
    def test_fixture_without_changes_is_quiet(self):
        repo = self.new_repo()
        self.assertNoFinding(repo.run_cli())

    def test_committed_operation_path_without_registry_blocks(self):
        repo = self.new_repo()
        repo.append(ROUTES, OPERATION)
        repo.commit("feat(api): items operation")
        self.assertRegistryFinding(repo.run_cli())

    def test_route_before_the_registry_exists_is_not_a_finding(self):
        # Until the registry lands there is nothing to register into; the registry's own
        # coverage test enforces the contract from then on.
        repo = self.new_repo()
        repo.git("rm", "-q", REGISTRY)
        repo.commit("chore: no registry yet")
        repo.git("checkout", "-q", "-b", "feat/routes")
        repo.append(ROUTES, OPERATION)
        repo.commit("feat(api): items operation")
        self.assertNoFinding(repo.run_cli(base="feat/x"))

    def test_committed_router_get_route_without_registry_blocks(self):
        repo = self.new_repo()
        repo.append(ROUTES, ROUTER_GET)
        repo.commit("feat(api): items route")
        self.assertRegistryFinding(repo.run_cli())

    def test_committed_router_route_forms_without_registry_block(self):
        forms = (
            'r.Post("/api/v1/items", h)',
            'cr.Put("/api/v1/items/{id}", h)',
            'cr.Patch("/api/v1/items/{id}", h)',
            'cr.Delete("/api/v1/items/{id}", h)',
            'r.Handle("/api/v1/items/export.csv", h)',
            'r.Method(http.MethodGet, "/api/v1/items/stream", h)',
        )
        for line in forms:
            with self.subTest(route=line):
                repo = self.new_repo()
                repo.append(
                    ROUTES,
                    f"\nfunc mountItems(r, cr Router, h http.Handler) {{\n\t{line}\n}}\n",
                )
                repo.commit("feat(api): items route")
                self.assertRegistryFinding(repo.run_cli())

    def test_committed_new_route_file_without_registry_blocks(self):
        repo = self.new_repo()
        repo.write(ITEMS, "package api\n" + OPERATION)
        repo.commit("feat(api): items operation in its own file")
        self.assertRegistryFinding(repo.run_cli())

    def test_route_file_in_a_subdirectory_counts(self):
        repo = self.new_repo()
        repo.write("api/v2/items.go", "package v2\n" + ROUTER_GET)
        self.assertRegistryFinding(repo.run_cli())

    def test_uncommitted_route_without_registry_blocks(self):
        repo = self.new_repo()
        repo.append(ROUTES, OPERATION)  # working tree only: neither staged nor committed
        self.assertRegistryFinding(repo.run_cli())

    def test_untracked_route_file_without_registry_blocks(self):
        repo = self.new_repo()
        repo.write(ITEMS, "package api\n" + ROUTER_GET)
        self.assertRegistryFinding(repo.run_cli())

    def test_registry_touched_on_branch_clears_finding(self):
        repo = self.new_repo()
        repo.append(ROUTES, OPERATION)
        repo.append(REGISTRY, REGISTRY_ENTRY)
        repo.commit("feat(api): items operation with its registry entry")
        self.assertNoFinding(repo.run_cli())

    def test_registry_touched_in_working_tree_clears_committed_route(self):
        repo = self.new_repo()
        repo.append(ROUTES, ROUTER_GET)
        repo.commit("feat(api): items route")
        repo.append(REGISTRY, REGISTRY_ENTRY)  # uncommitted
        self.assertNoFinding(repo.run_cli())

    def test_helper_without_route_is_not_a_finding(self):
        repo = self.new_repo()
        repo.append(ROUTES, HELPER)
        repo.commit("refactor(api): item status helper")
        self.assertNoFinding(repo.run_cli())

    def test_route_in_excluded_test_file_is_not_a_finding(self):
        repo = self.new_repo()
        repo.write(
            ITEMS_TEST,
            "package api\n"
            + OPERATION
            + "\nfunc mountTestRoutes(r Router, h http.HandlerFunc) {\n"
            + '\tr.Get("/api/v1/test-only", h)\n}\n',
        )
        repo.commit("test(api): fake items operation")
        self.assertNoFinding(repo.run_cli())

    def test_route_outside_the_route_files_is_not_a_finding(self):
        repo = self.new_repo()
        repo.write("app/items/web.go", "package items\n" + ROUTER_GET)
        repo.commit("feat(app): a route outside api/")
        self.assertNoFinding(repo.run_cli())

    def test_staged_route_without_registry_blocks(self):
        repo = self.new_repo()
        repo.append(ROUTES, ROUTER_GET)
        repo.git("add", ROUTES)
        self.assertRegistryFinding(repo.run_cli())

    def test_repeat_cli_reports_same_findings_without_writing_cache(self):
        repo = self.new_repo()
        repo.append(ROUTES, OPERATION)
        before = {str(p.relative_to(repo.root)) for p in Path(repo.root).rglob("*")}
        first = repo.run_cli()
        second = repo.run_cli()
        self.assertRegistryFinding(first)
        self.assertRegistryFinding(second)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(first.stderr, second.stderr)
        after = {str(p.relative_to(repo.root)) for p in Path(repo.root).rglob("*")}
        self.assertEqual(before, after)

    def test_json_output_is_machine_readable(self):
        repo = self.new_repo()
        repo.append(ROUTES, OPERATION)
        result = repo.run_cli("--format", "json")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertEqual(self.findings(result), [{
            "document": REGISTRY,
            "claim": "the route registry: every route is a listed entry or an excluded one",
            "paths": [ROUTES],
        }])

    def test_explicit_invalid_base_never_reports_clean(self):
        repo = self.new_repo()
        result = repo.run_cli(base="missing-revision")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing-revision", result.stderr)

    def test_cli_works_from_repository_subdirectory(self):
        repo = self.new_repo()
        repo.append(ROUTES, OPERATION)
        self.assertRegistryFinding(repo.run_cli(cwd=os.path.join(repo.root, "app/items")))

    def test_cli_works_in_linked_worktree(self):
        repo = self.new_repo()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        location = os.path.join(tmp.name, "worktree")
        repo.git("worktree", "add", "-q", "-b", "feat/linked", location, "main")
        linked = Repo(location)
        linked.append(ROUTES, OPERATION)
        self.assertRegistryFinding(linked.run_cli())
        self.assertNoFinding(repo.run_cli())


class DocumentRuleTest(DriftTest):
    def test_watched_config_and_both_mirror_directions(self):
        cases = (
            ("app/config.py", CONFIG_DOC),
            ("api/dto.go", "ui/types.ts"),
            ("ui/types.ts", "api/dto.go"),
        )
        for source, mirror in cases:
            with self.subTest(source=source):
                repo = self.new_repo()
                repo.append(source, "// change\n")
                self.assertDrift(repo.run_cli(), mirror)
                repo.append(mirror, "\nUpdated mirror\n")
                self.assertNoFinding(repo.run_cli())

    def test_mirror_finding_names_the_side_that_changed(self):
        repo = self.new_repo()
        repo.append("api/dto.go", "// change\n")
        self.assertEqual(self.findings(repo.run_cli("--format", "json")), [{
            "document": "ui/types.ts", "claim": "the hand-written wire mirror",
            "paths": ["api/dto.go"]}])

    def test_config_change_is_not_cleared_by_the_map(self):
        repo = self.new_repo()
        repo.append("app/config.py", "# new env var\n")
        repo.append("AGENTS.md", "Updated map\n")
        self.assertDrift(repo.run_cli(), CONFIG_DOC)
        repo.append(CONFIG_DOC, "\nNew env row\n")
        self.assertNoFinding(repo.run_cli())

    def test_added_migration_requires_domain_doc_not_the_map(self):
        repo = self.new_repo()
        repo.write("app/migrations/0002_widgets.sql", "new contents\n")
        self.assertDrift(repo.run_cli(), DOMAIN_DOC)
        repo.append("AGENTS.md", "Updated map\n")
        self.assertDrift(repo.run_cli(), DOMAIN_DOC)
        repo.append(DOMAIN_DOC, "\nNew table\n")
        self.assertNoFinding(repo.run_cli())

    def test_edited_migration_is_not_structural(self):
        repo = self.new_repo()
        repo.append("app/migrations/0001_init.sql", "-- comment\n")
        self.assertNoFinding(repo.run_cli())

    def test_new_package_requires_repository_map(self):
        for path in ("app/widgets/widgets.py", "cmd/widgets/main.go"):
            with self.subTest(path=path):
                repo = self.new_repo()
                repo.write(path, "new contents\n")
                self.assertDrift(repo.run_cli(), "AGENTS.md")
                repo.append(DOMAIN_DOC, "\nUnrelated topic doc\n")
                self.assertDrift(repo.run_cli(), "AGENTS.md")
                repo.append("AGENTS.md", "Updated map\n")
                self.assertNoFinding(repo.run_cli())

    def test_extra_file_in_existing_package_does_not_require_guide(self):
        repo = self.new_repo()
        repo.write("app/items/helpers.py", "def helper():\n    pass\n")
        self.assertNoFinding(repo.run_cli())

    def test_deleted_or_renamed_migration_requires_domain_doc(self):
        original = "app/migrations/0001_init.sql"
        for operation in ("delete", "rename-outside-watched-path"):
            with self.subTest(operation=operation):
                repo = self.new_repo()
                if operation == "delete":
                    repo.git("rm", original)
                else:
                    repo.git("mv", original, "archived-migration.sql")
                repo.commit("change migration location")
                self.assertDrift(repo.run_cli(), DOMAIN_DOC)


class HarnessLogRuleTest(DriftTest):
    def test_edit_project_layer_or_generated_files_requires_harness_log(self):
        for path in ("harness/project/roles/implementer.md",
                     "harness/project/skills/deploy/SKILL.md", "harness/project/drift.json",
                     ".claude/agents/implementer.md", ".codex/hooks.json"):
            with self.subTest(path=path):
                repo = self.new_repo()
                repo.append(path, "\n")
                self.assertDrift(repo.run_cli(), LOG)
                repo.append(LOG, "Recorded change\n")
                self.assertNoFinding(repo.run_cli())

    def test_deleted_project_role_requires_harness_log(self):
        repo = self.new_repo()
        repo.git("rm", "harness/project/roles/implementer.md")
        self.assertDrift(repo.run_cli(), LOG)

    def test_core_changes_need_no_project_log(self):
        repo = self.new_repo()
        repo.append("harness/core/roles/implementer.md", "New instruction\n")
        repo.append("harness/core/skills/dev/SKILL.md", "New step\n")
        repo.write("harness/core/scripts/new.py", "print('new')\n")
        repo.git("rm", "-q", "--cached", "harness/core/skills/dev/SKILL.md")
        self.assertNoFinding(repo.run_cli())

    def test_a_core_change_does_not_cover_a_project_change(self):
        repo = self.new_repo()
        repo.append("harness/core/roles/implementer.md", "New instruction\n")
        repo.append("harness/project/roles/implementer.md", "New specific\n")
        self.assertEqual(self.findings(repo.run_cli("--format", "json")), [{
            "document": LOG, "claim": "the harness change log",
            "paths": ["harness/project/roles/implementer.md"]}])

    def test_untracked_personal_agent_is_not_a_harness_change_until_added(self):
        repo = self.new_repo()
        repo.write(".claude/agents/personal.md", "my own agent\n")
        self.assertNoFinding(repo.run_cli())
        repo.git("add", ".claude/agents/personal.md")
        self.assertDrift(repo.run_cli(), LOG)

    def test_the_log_alone_is_not_a_finding(self):
        repo = self.new_repo()
        repo.append(LOG, "A note\n")
        self.assertNoFinding(repo.run_cli())


class NoMappingsTest(DriftTest):
    """Without drift.json only the harness-log rule runs, with its defaults."""

    overlay = False

    def test_project_paths_are_not_checked(self):
        repo = self.new_repo()
        repo.append("app/config.py", "# change\n")
        repo.append("api/dto.go", "// change\n")
        repo.append(ROUTES, OPERATION)
        repo.write("app/migrations/0002_x.sql", "new\n")
        repo.write("app/widgets/widgets.py", "new\n")
        self.assertNoFinding(repo.run_cli())

    def test_the_harness_log_rule_runs_with_its_default_path(self):
        repo = self.new_repo()
        repo.append("harness/project/roles/implementer.md", "New specific\n")
        repo.append("harness/core/roles/implementer.md", "New instruction\n")
        self.assertEqual(self.findings(repo.run_cli("--format", "json")), [{
            "document": LOG, "claim": "the harness change log",
            "paths": ["harness/project/roles/implementer.md"]}])
        repo.append(LOG, "Recorded\n")
        self.assertNoFinding(repo.run_cli())


class MappingConfigurationTest(DriftTest):
    overlay = False

    def mappings(self, repo, data, where="harness/project"):
        repo.write(f"{where}/drift.json", json.dumps(data))
        repo.commit("mappings")

    def test_log_path_and_harness_paths_are_configurable(self):
        repo = self.new_repo()
        self.mappings(repo, {"harness_log": {"path": "docs/harness-log.md",
                                             "paths": ["tools/agents/**"]}})
        repo.write("tools/agents/x.md", "new\n")
        repo.append("harness/project/roles/implementer.md", "not watched any more\n")
        self.assertEqual(self.findings(repo.run_cli("--format", "json")), [{
            "document": "docs/harness-log.md", "claim": "the harness change log",
            "paths": ["tools/agents/x.md"]}])

    def test_harness_project_relocates_the_overlay_and_its_log(self):
        repo = self.new_repo()
        self.mappings(repo, {"watched": [{"paths": ["app/*.py"], "doc": CONFIG_DOC,
                                          "claim": "the env table"}]}, where="config/harness")
        repo.append("app/config.py", "# change\n")
        repo.append("config/harness/drift.json", "\n")
        self.assertNoFinding(repo.run_cli())  # harness/project holds no mappings
        found = self.findings(repo.run_cli("--format", "json", HARNESS_PROJECT="config/harness"))
        self.assertEqual(found, [
            {"document": "config/harness/HARNESS_LOG.md", "claim": "the harness change log",
             "paths": ["config/harness/drift.json"]},
            {"document": CONFIG_DOC, "claim": "the env table", "paths": ["app/config.py"]},
        ])

    def test_registry_rule_without_exclusions(self):
        repo = self.new_repo()
        self.mappings(repo, {"registries": [{"registry": REGISTRY, "files": ["api/*.go"],
                                             "route": r'\.Get\(\s*"/', "claim": "the registry"}]})
        repo.write(ITEMS_TEST, "package api\n" + ROUTER_GET)
        self.assertDrift(repo.run_cli(), REGISTRY)

    def test_the_core_is_wherever_the_running_script_lives(self):
        repo = self.new_repo()
        engine = Path(repo.root) / "harness" / "engine" / "scripts"
        engine.mkdir(parents=True)
        for name in ("check_docs_drift.py", "project.py", "tripwire.py", "task_state.py"):
            shutil.copy(CORE / "scripts" / name, engine / name)
        repo.commit("vendored core at another prefix")
        repo.append("harness/engine/scripts/project.py", "\n")
        self.assertNoFinding(repo.run_cli(script=engine / "check_docs_drift.py"))
        repo.append("harness/core/roles/implementer.md", "\n")  # not the running core here
        self.assertDrift(repo.run_cli(script=engine / "check_docs_drift.py"), LOG)

    def test_unusable_mappings_never_report_clean(self):
        bad = (
            [],
            {"unknown": []},
            {"watched": {"paths": ["a"], "doc": "b", "claim": "c"}},
            {"watched": [{"paths": ["a"], "doc": "b"}]},
            {"watched": [{"paths": "a", "doc": "b", "claim": "c"}]},
            {"structural": [{"paths": [""], "doc": "b", "claim": "c"}]},
            {"new_packages": [{"paths": ["a/*/*.py"], "doc": "", "claim": "c"}]},
            {"mirrors": [{"paths": ["only/one.go"], "claim": "c"}]},
            {"mirrors": [{"paths": ["a/*.go", "b.ts"], "claim": "c"}]},
            {"registries": [{"registry": "r.go", "files": ["api/*.go"], "claim": "c"}]},
            {"registries": [{"registry": "r.go", "files": ["api/*.go"], "route": "(",
                             "claim": "c"}]},
            {"harness_log": {"path": ""}},
            {"harness_log": {"paths": "harness/**"}},
            {"harness_log": {"other": 1}},
        )
        for data in bad:
            with self.subTest(data=data):
                repo = self.new_repo()
                repo.write("harness/project/drift.json", json.dumps(data))
                result = repo.run_cli()
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("drift.json", result.stderr)
        repo = self.new_repo()
        repo.write("harness/project/drift.json", "{not json")
        result = repo.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn("drift.json", result.stderr)


del DriftTest  # the base class holds no cases of its own


if __name__ == "__main__":
    unittest.main()
