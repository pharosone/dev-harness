"""Render the core plus a project overlay into native adapters, and resolve shared skills for real.

A product repository vendors the core at `harness/core` and keeps its overlay at
`harness/project`. Most cases build a synthetic core (a copy of the scripts under test plus
small skills, roles and models) so they can change core sources; `RealCoreTest` links the
core under test itself and renders the fixture project in `tests/fixture`.
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
SCRIPT = CORE / "scripts/render_adapters.py"
FIXTURE = Path(__file__).resolve().parent / "fixture/harness/project"
ENV = {key: value for key, value in os.environ.items() if key != "HARNESS_PROJECT"}
HOOK_SCRIPTS = re.compile(r"/(harness/core/scripts/(?:runtime_hook|guard|tripwire)\.py)")


def skill(name, text="Shared workflow."):
    return f"---\nname: {name}\ndescription: {name} skill.\n---\n{text}\n"


class RenderCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # macOS aliases /var to /private/var; compare canonical paths on both sides.
        self.root = Path(self.temp.name).resolve()
        self.script = SCRIPT

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)

    def read(self, name):
        return (self.root / name).read_text()

    def render(self, *args, script=None, cwd=None, env=None, root=True):
        command = [sys.executable, str(script or self.script)]
        if root:
            command += ["--root", str(self.root)]
        return subprocess.run([*command, *args], capture_output=True, text=True, cwd=cwd,
                              env=ENV if env is None else env)

    def rendered(self, *args, **kwargs):
        result = self.render(*args, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def body(self, name):
        """The Claude agent body: everything after the frontmatter."""
        return self.read(f".claude/agents/{name}.md").split("---", 2)[2]

    def codex(self, name):
        return tomllib.loads(self.read(f".codex/agents/{name}.toml"))

    def harness_handlers(self, path):
        hooks = json.loads(self.read(path))["hooks"]
        return {event: [h["command"] for g in groups for h in g["hooks"]
                        if "/scripts/" in h["command"] and "harness" in h["command"]]
                for event, groups in hooks.items()}


class SyntheticCoreTest(RenderCase):
    def setUp(self):
        super().setUp()
        shutil.copytree(CORE / "scripts", self.root / "harness/core/scripts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        self.script = self.root / "harness/core/scripts/render_adapters.py"
        self.write("harness/core/skills/dev-flow/SKILL.md", skill("dev-flow"))
        self.write("harness/core/roles/qa-verifier.md", "# QA\nRun independent tests.\n")
        self.write("harness/core/roles/manifest.json", json.dumps({"qa-verifier": {
            "description": "Independent QA", "profile": "critical-review"}}))
        self.write("harness/core/adapters/models.json", json.dumps({
            "claude": {"critical-review": {"model": "fable", "effort": "xhigh"},
                       "routine": {"model": "sonnet", "effort": "medium"}},
            "codex": {"critical-review": {"model": "gpt-6-astra", "effort": "xhigh"},
                      "routine": {"model": "gpt-6.1-sol", "effort": "medium"}}}))
        self.write("harness/project/rules/server.md", '---\npaths: ["server/**"]\n---\nRead shared rules.\n')

    def project_role(self, name="docs-steward", body="# Docs steward\nKeep docs in step.\n", **metadata):
        role = {"description": "Keeps docs in step", "profile": "routine", **metadata}
        self.write("harness/project/project.json", json.dumps({"name": "demo", "roles": {name: role}}))
        if body is not None:
            self.write(f"harness/project/roles/{name}.md", body)

    # Skills -----------------------------------------------------------------------------

    def test_both_runtimes_discover_the_same_skill_and_new_content(self):
        self.rendered()
        for prefix in (".agents", ".claude"):
            link = self.root / prefix / "skills/dev-flow"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "../../harness/core/skills/dev-flow")
            self.assertEqual(link.resolve(), self.root / "harness/core/skills/dev-flow")
        self.write("harness/core/skills/dev-flow/SKILL.md", skill("dev-flow", "Updated shared workflow."))
        for prefix in (".agents", ".claude"):
            self.assertIn("Updated", self.read(f"{prefix}/skills/dev-flow/SKILL.md"))

    def test_project_skills_link_into_the_overlay(self):
        self.write("harness/project/skills/deploy-app/SKILL.md", skill("deploy-app", "Deploy it."))
        self.rendered()
        for prefix in (".agents", ".claude"):
            link = self.root / prefix / "skills/deploy-app"
            self.assertEqual(os.readlink(link), "../../harness/project/skills/deploy-app")
            self.assertIn("Deploy it.", self.read(f"{prefix}/skills/deploy-app/SKILL.md"))
            self.assertEqual(os.readlink(self.root / prefix / "skills/dev-flow"),
                             "../../harness/core/skills/dev-flow")

    def test_a_project_skill_overrides_the_core_skill_of_the_same_name_and_says_so(self):
        self.write("harness/project/skills/dev-flow/SKILL.md", skill("dev-flow", "Local workflow."))
        result = self.rendered()
        self.assertRegex(result.stdout, r"(?i)dev-flow.*overrid|overrid.*dev-flow")
        for prefix in (".agents", ".claude"):
            self.assertEqual(os.readlink(self.root / prefix / "skills/dev-flow"),
                             "../../harness/project/skills/dev-flow")
            self.assertIn("Local workflow.", self.read(f"{prefix}/skills/dev-flow/SKILL.md"))
        check = self.rendered("--check")
        self.assertRegex(check.stdout, r"(?i)dev-flow.*overrid|overrid.*dev-flow")

    def test_skill_metadata_is_validated_in_both_layers(self):
        self.write("harness/project/skills/broken/SKILL.md", skill("other-name"))
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("broken", result.stderr)
        self.assertFalse((self.root / ".claude").exists())

    def test_personal_skill_collision_is_not_deleted(self):
        self.write(".claude/skills/dev-flow/SKILL.md", "personal skill\n")
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read(".claude/skills/dev-flow/SKILL.md"), "personal skill\n")

    # Roles ------------------------------------------------------------------------------

    def test_agents_carry_the_core_role_text_and_name_its_source(self):
        self.rendered()
        codex_text = self.read(".codex/agents/qa-verifier.toml")
        codex = tomllib.loads(codex_text)
        self.assertEqual(codex["name"], "qa-verifier")
        self.assertEqual(codex["model"], "gpt-6-astra")
        self.assertEqual(codex["model_reasoning_effort"], "xhigh")
        self.assertIn("Run independent tests.", codex["developer_instructions"])
        self.assertIn("harness/core/roles/qa-verifier.md", codex_text)
        claude = self.read(".claude/agents/qa-verifier.md")
        self.assertIn("Run independent tests.", self.body("qa-verifier"))
        self.assertIn("harness/core/roles/qa-verifier.md", claude)
        # The composed role is in the prompt itself: no instruction to open a second file.
        for text in (claude, codex["developer_instructions"]):
            self.assertNotRegex(text, r"Read [^\n]*roles/qa-verifier\.md")
        self.assertNotIn("## Project specifics", claude)
        self.assertIn("harness/project/rules/server.md", self.read(".claude/rules/server.md"))
        for path in (".claude/settings.json", ".codex/hooks.json"):
            hook = json.loads(self.read(path))["hooks"]["Stop"][0]["hooks"][0]
            self.assertIn("harness/core/scripts/runtime_hook.py", hook["command"])
            self.assertNotIn("CLAUDE_PROJECT_DIR", hook["command"])

    def test_roles_get_a_lean_prefix(self):
        self.write("harness/core/roles/implementer.md", "# Implementer\nWrite code.\n")
        self.write("harness/core/roles/manifest.json", json.dumps({
            "qa-verifier": {"description": "Independent QA", "profile": "critical-review",
                            "tools": ["Read", "Grep", "Bash"], "sandbox": "read-only"},
            "implementer": {"description": "Writes code", "profile": "critical-review",
                            "sandbox": "workspace-write"}}))
        self.rendered()
        qa = self.read(".claude/agents/qa-verifier.md")
        self.assertIn('omitClaudeMd: true', qa)
        self.assertIn('tools: "Read, Grep, Bash"', qa)
        self.assertIn("AGENTS.md", self.body("qa-verifier"))
        self.assertIn("harness/project/rules/", self.body("qa-verifier"))
        self.assertNotIn("harness/README.md", qa)
        self.assertNotIn("tools:", self.read(".claude/agents/implementer.md"))
        codex = self.codex("qa-verifier")
        self.assertEqual(codex["sandbox_mode"], "read-only")
        self.assertNotIn("Read AGENTS.md", codex["developer_instructions"])
        self.assertEqual(self.codex("implementer")["sandbox_mode"], "workspace-write")

    def test_invalid_sandbox_is_rejected(self):
        self.write("harness/core/roles/manifest.json", json.dumps({"qa-verifier": {
            "description": "Independent QA", "profile": "critical-review", "sandbox": "danger-full-access"}}))
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sandbox", result.stderr)
        self.write("harness/core/roles/manifest.json", json.dumps({"qa-verifier": {
            "description": "Independent QA", "profile": "critical-review"}}))
        self.project_role(sandbox="danger-full-access")
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("sandbox", result.stderr)

    def test_core_role_mismatch_with_its_manifest_is_rejected(self):
        self.write("harness/core/roles/ghost.md", "# Ghost\n")
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ghost", result.stderr)

    def test_project_roles_are_declared_in_project_json(self):
        self.project_role(tools=["Read", "Grep"], sandbox="read-only")
        self.rendered()
        claude = self.read(".claude/agents/docs-steward.md")
        self.assertIn('model: "sonnet"', claude)
        self.assertIn('tools: "Read, Grep"', claude)
        self.assertIn("Keep docs in step.", self.body("docs-steward"))
        self.assertIn("harness/project/roles/docs-steward.md", claude)
        codex = self.codex("docs-steward")
        self.assertEqual(codex["model"], "gpt-6.1-sol")
        self.assertEqual(codex["sandbox_mode"], "read-only")
        self.assertIn("Keep docs in step.", codex["developer_instructions"])
        self.assertIn("harness/project/roles/docs-steward.md", self.read(".codex/agents/docs-steward.toml"))
        self.rendered("--check")

    def test_project_specifics_are_appended_to_the_core_role(self):
        self.write("harness/project/roles/qa-verifier.md", "Also run the fixture smoke.\n")
        self.rendered()
        claude = self.read(".claude/agents/qa-verifier.md")
        for text in (self.body("qa-verifier"), self.codex("qa-verifier")["developer_instructions"]):
            core = text.index("Run independent tests.")
            heading = text.index("## Project specifics")
            local = text.index("Also run the fixture smoke.")
            self.assertLess(core, heading)
            self.assertLess(heading, local)
            self.assertEqual(text.count("## Project specifics"), 1)
        for source in ("harness/core/roles/qa-verifier.md", "harness/project/roles/qa-verifier.md"):
            self.assertIn(source, claude)
            self.assertIn(source, self.read(".codex/agents/qa-verifier.toml"))
        self.rendered("--check")

    def test_overlay_role_mistakes_are_rejected(self):
        cases = {
            "declared without a body": lambda: self.project_role(body=None),
            "body without a declaration": lambda: self.write("harness/project/roles/ghost.md", "# Ghost\n"),
            "project role named like a core role": lambda: self.project_role(name="qa-verifier"),
            "roles not an object": lambda: self.write("harness/project/project.json",
                                                      json.dumps({"roles": ["docs-steward"]})),
            "project role without a profile": lambda: self.write("harness/project/project.json", json.dumps(
                {"roles": {"docs-steward": {"description": "Docs"}}})),
        }
        expected = {"declared without a body": "docs-steward", "body without a declaration": "ghost",
                    "project role named like a core role": "qa-verifier", "roles not an object": "roles",
                    "project role without a profile": "profile"}
        for label, arrange in cases.items():
            with self.subTest(label):
                shutil.rmtree(self.root / "harness/project/roles", ignore_errors=True)
                (self.root / "harness/project/project.json").unlink(missing_ok=True)
                arrange()
                result = self.render()
                self.assertNotEqual(result.returncode, 0, label)
                self.assertIn(expected[label], result.stderr, label)
                self.assertFalse((self.root / ".claude/agents").exists(), label)

    # Models -----------------------------------------------------------------------------

    def test_overlay_models_are_deep_merged_over_the_core(self):
        self.write("harness/project/models.json", json.dumps({
            "claude": {"critical-review": {"effort": "high"}},
            "codex": {"routine": {"model": "gpt-local", "effort": "low"}}}))
        self.project_role()
        self.rendered()
        qa = self.read(".claude/agents/qa-verifier.md")
        self.assertIn('model: "fable"', qa)
        self.assertIn('effort: "high"', qa)
        self.assertEqual(self.codex("qa-verifier")["model"], "gpt-6-astra")
        self.assertEqual(self.codex("qa-verifier")["model_reasoning_effort"], "xhigh")
        self.assertIn('model: "sonnet"', self.read(".claude/agents/docs-steward.md"))
        self.assertEqual(self.codex("docs-steward")["model"], "gpt-local")
        self.assertEqual(self.codex("docs-steward")["model_reasoning_effort"], "low")
        self.write("harness/project/models.json", "[]")
        self.assertNotEqual(self.render().returncode, 0)

    # Rules, settings, CLAUDE.md -------------------------------------------------------------

    def test_rules_carry_their_text_and_settings_pin_the_subagent_cache(self):
        self.rendered()
        rule = self.read(".claude/rules/server.md")
        self.assertIn("Read shared rules.", rule)
        self.assertIn('paths: ["server/**"]', rule)
        settings = json.loads(self.read(".claude/settings.json"))
        self.assertEqual(settings["subagentPromptCacheTtl"], "5m")
        self.write("harness/project/rules/loose.md", "---\nowner: me\n---\nNo paths.\n")
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("paths", result.stderr)

    def test_claude_md_imports_agents_md_and_codex_config_is_kept(self):
        self.write("CLAUDE.md", "Personal notes\n")
        self.write(".codex/config.toml", 'model = "mine"\n')
        self.rendered()
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n\nPersonal notes\n")
        self.assertEqual(self.read(".codex/config.toml"), 'model = "mine"\n')
        self.rendered()
        self.assertEqual(self.read("CLAUDE.md"), "@AGENTS.md\n\nPersonal notes\n")
        self.rendered("--check")
        self.write(".codex/config.toml", "model = \n")
        self.assertNotEqual(self.render().returncode, 0)

    # Hooks ------------------------------------------------------------------------------

    def test_claude_gets_the_dispatch_tripwire_and_keeps_personal_hooks(self):
        personal = {"matcher": "Write", "hooks": [{"type": "command", "command": "prettier"}]}
        self.write(".claude/settings.json", json.dumps({"hooks": {"PostToolUse": [personal]}}))
        self.rendered()
        groups = json.loads(self.read(".claude/settings.json"))["hooks"]["PostToolUse"]
        self.assertIn(personal, groups)
        agent = [g for g in groups if g.get("matcher") == "Agent"]
        self.assertEqual(len(agent), 1)
        self.assertIn('harness/core/scripts/tripwire.py" hook', agent[0]["hooks"][0]["command"])
        self.rendered()
        self.assertEqual(json.loads(self.read(".claude/settings.json"))["hooks"]["PostToolUse"], groups)
        codex = json.loads(self.read(".codex/hooks.json"))["hooks"]
        self.assertNotIn("PostToolUse", codex)

    def test_both_runtimes_get_the_guard_before_tools(self):
        personal = {"matcher": "Bash", "hooks": [{"type": "command", "command": "my-audit"}]}
        self.write(".codex/hooks.json", json.dumps({"hooks": {"PreToolUse": [personal]}}))
        self.rendered()
        for path, runtime, tools in ((".claude/settings.json", "claude", ("Bash", "Read", "Grep", "Edit", "Write", "NotebookEdit")),
                                     (".codex/hooks.json", "codex", ("Bash", "apply_patch", "Edit", "Write"))):
            groups = json.loads(self.read(path))["hooks"]["PreToolUse"]
            guard = [g for g in groups if any("harness/core/scripts/guard.py" in h["command"] for h in g["hooks"])]
            self.assertEqual(len(guard), 1, path)
            self.assertEqual(set(guard[0]["matcher"].split("|")), set(tools), path)
            self.assertIn(f"--runtime {runtime}", guard[0]["hooks"][0]["command"])
        self.assertIn(personal, json.loads(self.read(".codex/hooks.json"))["hooks"]["PreToolUse"])
        # Exit 2 blocks a tool: a missing or crashing JSON-speaking hook must not brick the session.
        claude = json.loads(self.read(".claude/settings.json"))["hooks"]
        for event in ("PreToolUse", "PostToolUse"):
            for group in claude[event]:
                for handler in group["hooks"]:
                    if "harness/core/scripts/" in handler["command"]:
                        self.assertTrue(handler["command"].endswith(("|| true", "fi")), handler["command"])
        self.rendered("--check")

    def test_guard_hook_fails_closed_when_the_guard_is_missing(self):
        self.rendered()
        (self.root / "harness/core/scripts/guard.py").unlink()
        claude = json.loads(self.read(".claude/settings.json"))["hooks"]["PreToolUse"]
        [group] = [g for g in claude if any("guard.py" in h["command"] for h in g["hooks"])]
        self.assertIn("Grep", group["matcher"].split("|"))
        command = group["hooks"][0]["command"]
        self.assertIn("CLAUDE_PROJECT_DIR", command)
        elsewhere = Path(self.temp.name) / "elsewhere"
        elsewhere.mkdir()
        run = lambda: subprocess.run(["bash", "-c", command], cwd=elsewhere, input="{}", text=True,
                                     capture_output=True, env={"PATH": "/usr/bin:/bin", "CLAUDE_PROJECT_DIR": str(self.root)})
        # No guard script: deny every call rather than silently allowing it.
        self.assertEqual(json.loads(run().stdout)["hookSpecificOutput"]["permissionDecision"], "deny")
        self.write("harness/core/scripts/guard.py", "import sys\nsys.stdin.read()\n")
        self.assertEqual(run().stdout, "")
        codex = json.loads(self.read(".codex/hooks.json"))["hooks"]["PreToolUse"]
        [group] = [g for g in codex if any("guard.py" in h["command"] for h in g["hooks"])]
        outside = subprocess.run(["bash", "-c", group["hooks"][0]["command"]], cwd=elsewhere, input="{}",
                                 text=True, capture_output=True, env={"PATH": "/usr/bin:/bin"})
        self.assertEqual(json.loads(outside.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")

    # Drift, retirement, migration ---------------------------------------------------------

    def test_check_detects_drift_without_rewriting(self):
        self.assertNotEqual(self.render("--check").returncode, 0)
        self.rendered()
        self.rendered("--check")
        path = self.root / ".codex/agents/qa-verifier.toml"
        path.write_text("broken = true\n")
        self.assertNotEqual(self.render("--check").returncode, 0)
        self.assertEqual(path.read_text(), "broken = true\n")
        self.rendered()
        self.write("harness/project/roles/qa-verifier.md", "New project specifics.\n")
        result = self.render("--check")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".claude/agents/qa-verifier.md", result.stderr)
        self.assertNotIn("New project specifics.", self.read(".claude/agents/qa-verifier.md"))

    def test_removed_sources_retire_only_owned_adapters(self):
        self.write("harness/core/skills/remaining/SKILL.md", skill("remaining"))
        self.write("harness/project/skills/deploy-app/SKILL.md", skill("deploy-app"))
        self.project_role()
        self.rendered()
        personal = {
            ".claude/agents/personal.md": "Personal agent\n",
            ".codex/agents/personal.toml": 'name = "personal"\n',
            ".claude/rules/personal.md": "Personal rule\n",
            ".claude/skills/personal/SKILL.md": "Personal skill\n",
        }
        for name, content in personal.items():
            self.write(name, content)
        personal_link = self.root / ".agents/skills/personal"
        personal_link.symlink_to("../../personal-skill", target_is_directory=True)
        (self.root / "harness/core/skills/dev-flow/SKILL.md").unlink()
        (self.root / "harness/core/roles/qa-verifier.md").unlink()
        self.write("harness/core/roles/manifest.json", "{}\n")
        (self.root / "harness/project/rules/server.md").unlink()
        shutil.rmtree(self.root / "harness/project/skills/deploy-app")
        shutil.rmtree(self.root / "harness/project/roles")
        self.write("harness/project/project.json", json.dumps({"roles": {}}))
        stale = [".claude/agents/qa-verifier.md", ".codex/agents/qa-verifier.toml",
                 ".claude/agents/docs-steward.md", ".codex/agents/docs-steward.toml",
                 ".claude/rules/server.md", ".claude/skills/dev-flow", ".agents/skills/dev-flow",
                 ".claude/skills/deploy-app", ".agents/skills/deploy-app"]
        result = self.render("--check")
        self.assertNotEqual(result.returncode, 0, result.stdout)
        for name in stale:
            path = self.root / name
            self.assertTrue(path.exists() or path.is_symlink(), name)
        self.rendered()
        for name in stale:
            path = self.root / name
            self.assertFalse(path.exists() or path.is_symlink(), name)
        for name, content in personal.items():
            self.assertEqual(self.read(name), content)
        self.assertTrue(personal_link.is_symlink())
        self.assertEqual(personal_link.readlink(), Path("../../personal-skill"))
        self.rendered("--check")

    def test_outputs_of_the_pre_core_layout_are_migrated(self):
        legacy_marker = "Generated by harness/scripts/render_adapters.py; edit canonical sources."
        self.write(".claude/agents/retired.md", f"---\nname: retired\n---\n\n<!-- {legacy_marker} -->\n\nOld.\n")
        self.write(".codex/agents/retired.toml", f'# {legacy_marker}\nname = "retired"\n')
        self.write(".claude/rules/retired.md", f"---\npaths: [x]\n---\n\n<!-- {legacy_marker} -->\n\nOld.\n")
        for prefix in (".agents", ".claude"):
            (self.root / prefix / "skills").mkdir(parents=True)
            (self.root / prefix / "skills/dev-flow").symlink_to("../../skills/dev-flow", target_is_directory=True)
            (self.root / prefix / "skills/retired").symlink_to("../../skills/retired", target_is_directory=True)
        scripts = '"$(git rev-parse --show-toplevel)/harness/scripts'
        root = "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel 2>/dev/null)}"
        self.write(".claude/settings.json", json.dumps({"hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": f'python3 {scripts}/runtime_hook.py" --runtime claude'}]}],
            "PreToolUse": [{"matcher": "Bash|Read|Grep|Edit|Write|NotebookEdit", "hooks": [{"type": "command",
                            "command": f'g="{root}/harness/scripts/guard.py"; if [ -f "$g" ]; then python3 "$g" --runtime claude || true; fi'}]}],
            "PostToolUse": [{"matcher": "Agent", "hooks": [{"type": "command",
                             "command": f'python3 {scripts}/tripwire.py" hook || true'}]}]}}))
        self.rendered()
        for name in (".claude/agents/retired.md", ".codex/agents/retired.toml", ".claude/rules/retired.md",
                     ".claude/skills/retired", ".agents/skills/retired"):
            path = self.root / name
            self.assertFalse(path.exists() or path.is_symlink(), name)
        for prefix in (".agents", ".claude"):
            self.assertEqual(os.readlink(self.root / prefix / "skills/dev-flow"), "../../harness/core/skills/dev-flow")
        handlers = self.harness_handlers(".claude/settings.json")
        for event in ("Stop", "PreToolUse", "PostToolUse"):
            self.assertEqual(len(handlers[event]), 1, handlers[event])
            self.assertIn("harness/core/scripts/", handlers[event][0])
            self.assertNotIn("harness/scripts/", handlers[event][0])
        self.rendered("--check")

    # Locations --------------------------------------------------------------------------

    def test_root_defaults_to_the_git_checkout(self):
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        (self.root / "nested/dir").mkdir(parents=True)
        self.rendered(cwd=self.root / "nested/dir", root=False)
        self.assertEqual(os.readlink(self.root / ".claude/skills/dev-flow"), "../../harness/core/skills/dev-flow")
        self.assertFalse((self.root / "nested/dir/.claude").exists())

    def test_harness_project_moves_the_overlay(self):
        (self.root / "harness/project").rename(self.root / "custom-layer")
        self.write("custom-layer/skills/deploy-app/SKILL.md", skill("deploy-app"))
        self.rendered(env={**ENV, "HARNESS_PROJECT": "custom-layer"})
        self.assertEqual(os.readlink(self.root / ".claude/skills/deploy-app"), "../../custom-layer/skills/deploy-app")
        self.assertIn("custom-layer/rules/server.md", self.read(".claude/rules/server.md"))

    def test_core_role_text_names_the_overlay_in_use(self):
        self.write("harness/core/roles/qa-verifier.md",
                   "# QA\nRead harness/project/references/qa-gotchas.md first.\n")
        (self.root / "harness/project").rename(self.root / "custom-layer")
        self.rendered(env={**ENV, "HARNESS_PROJECT": "custom-layer"})
        for text in (self.body("qa-verifier"),
                     self.codex("qa-verifier")["developer_instructions"]):
            self.assertIn("custom-layer/references/qa-gotchas.md", text)
            self.assertNotIn("harness/project/", text)


class RealCoreTest(RenderCase):
    """The core under test, linked at harness/core, renders the fixture project."""

    def setUp(self):
        super().setUp()
        (self.root / "harness").mkdir()
        (self.root / "harness/core").symlink_to(CORE, target_is_directory=True)
        shutil.copytree(FIXTURE, self.root / "harness/project")

    def test_the_core_under_test_renders_the_fixture_project(self):
        self.rendered()
        core_skills = sorted(p.parent.name for p in CORE.glob("skills/*/SKILL.md"))
        self.assertTrue(core_skills)
        for prefix in (".agents", ".claude"):
            for name in core_skills:
                link = self.root / prefix / "skills" / name
                self.assertEqual(os.readlink(link), f"../../harness/core/skills/{name}")
                self.assertEqual(link.resolve(), CORE / "skills" / name)
            self.assertEqual(os.readlink(self.root / prefix / "skills/deploy-app"),
                             "../../harness/project/skills/deploy-app")
        implementer = self.body("implementer")
        core_text = (CORE / "roles/implementer.md").read_text().strip()
        local_text = (FIXTURE / "roles/implementer.md").read_text().strip()
        self.assertIn(core_text, implementer)
        self.assertLess(implementer.index(core_text), implementer.index("## Project specifics"))
        self.assertLess(implementer.index("## Project specifics"), implementer.index(local_text))
        self.assertIn(local_text, self.codex("implementer")["developer_instructions"])
        models = json.loads((CORE / "adapters/models.json").read_text())
        steward = self.read(".claude/agents/docs-steward.md")
        self.assertIn(f'model: "{models["claude"]["routine"]["model"]}"', steward)
        self.assertIn('effort: "high"', steward)
        self.assertEqual(self.codex("docs-steward")["model"], models["codex"]["routine"]["model"])
        self.assertIn("Every module under `app/`", self.read(".claude/rules/app.md"))
        for path in (".claude/settings.json", ".codex/hooks.json"):
            commands = [c for cs in self.harness_handlers(path).values() for c in cs]
            self.assertTrue(commands, path)
            for command in commands:
                [script] = HOOK_SCRIPTS.findall(command)
                self.assertTrue((self.root / script).is_file(), script)
        self.rendered("--check")
        self.rendered("--check", script=self.root / "harness/core/scripts/render_adapters.py")

    def test_a_core_outside_the_repository_is_rejected(self):
        (self.root / "harness/core").unlink()
        result = self.render()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("harness/core", result.stderr)
        self.assertFalse((self.root / ".claude").exists())


class RepositoryTest(unittest.TestCase):
    def test_repository_profiles_follow_the_v2_table(self):
        models = json.loads((CORE / "adapters/models.json").read_text())
        expected = {"mechanical", "routine", "implementation", "critical-review", "escalation",
                    "deep-reasoning"}
        for runtime in ("claude", "codex"):
            self.assertEqual(set(models[runtime]), expected)
            for name, profile in models[runtime].items():
                if name != "deep-reasoning":  # xhigh only where one pass of depth pays
                    self.assertNotIn(profile["effort"], ("xhigh", "max", "ultra"), name)
        self.assertEqual(models["claude"]["deep-reasoning"], {"model": "opus", "effort": "xhigh"})
        self.assertEqual(models["codex"]["deep-reasoning"],
                         {"model": "gpt-6-astra", "effort": "xhigh"})
        self.assertEqual(models["claude"]["implementation"], {"model": "opus", "effort": "medium"})
        self.assertEqual(models["claude"]["escalation"]["model"], "fable")
        self.assertEqual(models["codex"]["critical-review"]["model"], "gpt-6-astra")
        manifest = json.loads((CORE / "roles/manifest.json").read_text())
        self.assertEqual(manifest["simplifier"]["profile"], "critical-review")
        self.assertEqual(manifest["qa-verifier"]["profile"], "routine")
        self.assertNotIn("tools", manifest["ui-design-verifier"])
        self.assertEqual(manifest["investigator"]["profile"], "deep-reasoning")
        self.assertEqual(manifest["investigator"]["sandbox"], "read-only")


if __name__ == "__main__":
    unittest.main()
