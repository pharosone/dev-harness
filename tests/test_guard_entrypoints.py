"""Vetted deploy and ops entry points: guard.allow_workflows and read-only kubectl rollout.

The guard recognizes deploy tools by name and leaves a repository's own wrapper scripts to
the owner's review. An overlay may name the workflow files a Claude session dispatches with
`gh workflow run`; every other workflow, form, runtime and bare deploy tool stays denied.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path
import unittest

from test_guard import CORE, GuardHarness

ALLOW = json.dumps({"guard": {"allow_workflows": ["env-power.yml", "backfill.yaml",
                                                  "acme/infra:plan.yml"]}})
ALLOWED = [
    "gh workflow run env-power.yml",
    "gh workflow run env-power.yml -f action=up -f environment=dev",
    "gh workflow run backfill.yaml --ref v1.2.3 -f command=status",
    "gh workflow run backfill.yaml --ref=v1.2.3 -F count=2 --raw-field mode=dry",
    "gh -R acme/infra workflow run plan.yml --ref main",
    "gh workflow run plan.yml --repo acme/infra",
    "gh workflow run plan.yml --repo=acme/infra -r main",
    "/usr/bin/gh workflow run env-power.yml",
]
DENIED = [
    "gh workflow run deploy.yml",
    "gh workflow run env-power.yml -R acme/app",
    "gh workflow run plan.yml",
    "gh -R acme/infra -R acme/app workflow run plan.yml",
    "gh workflow run 'Env power'",
    "gh workflow run 12345",
    "gh workflow run",
    "gh workflow run env-power.yml extra",
    "gh workflow run env-power.yml --json",
    "gh workflow run env-power.yml -F payload=@notes.txt",
    "gh workflow run env-power.yml -F @notes.txt",
    "gh workflow run env-power.yml --field=payload=@notes.txt",
    "gh workflow run env-power.yml -Racme/infra",
    "gh workflow run env-power.yml --ref",
    "gh workflow run env-power.yml && echo done",
    "echo ok; gh workflow run env-power.yml",
    "echo up | gh workflow run env-power.yml",
    "bash -c 'gh workflow run env-power.yml'",
    "GH_REPO=acme/app gh workflow run env-power.yml",
    "env gh workflow run env-power.yml",
    "sudo gh workflow run env-power.yml",
    "echo $(gh workflow run env-power.yml)",
    "cat <<EOF\n$(gh workflow run env-power.yml)\nEOF",
    "kubectl apply -f deploy.yaml",
    "kubectl rollout restart deploy/api -n stage",
    "kubectl rollout undo deploy/api",
    "helm upgrade api ./chart",
]
WRAPPERS = [
    "scripts/release.sh v1.2.3",
    "./scripts/release.sh v1.2.3",
    "bash scripts/release.sh v1.2.3",
    "ALLOW_MUTATE=1 ops/kctl --mutates rollout restart deploy/api",
]
READS = [
    "kubectl rollout status deploy/api -n stage",
    "kubectl -n prod rollout status deploy/api",
    "kubectl rollout history deploy/api --revision=2",
    "kubectl --context prod rollout -n prod history deploy/api",
]


class WorkflowAllowListTest(GuardHarness, unittest.TestCase):
    def test_without_the_key_every_workflow_run_is_a_deploy(self):
        for runtime in ("claude", "codex"):
            for command in ALLOWED:
                with self.subTest(command=command, runtime=runtime):
                    self.assert_denied(self.bash(command, runtime), "Deploy")

    def test_listed_workflow_files_run_on_claude(self):
        self.use_overlay(ALLOW)
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assertIsNone(self.bash(command))

    def test_other_files_forms_and_tools_keep_the_deploy_rule(self):
        self.use_overlay(ALLOW)
        for command in DENIED:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), "Deploy")

    def test_codex_lanes_stay_denied(self):
        self.use_overlay(ALLOW)
        for command in ALLOWED:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command, "codex"), "Deploy")

    def test_the_key_relaxes_no_other_rule(self):
        self.use_overlay(ALLOW)
        self.assert_denied(self.bash("cat .env"), ".env")
        self.assert_guarded(self.bash("git push origin main"), "claude", "push")
        overlay = Path(self.env["HARNESS_PROJECT"]) / "project.json"
        for runtime in ("claude", "codex"):
            verdict = self.verdict(self.event("Edit", file_path=str(overlay),
                                              old_string="plan", new_string="apply"), runtime)
            self.assert_guarded(verdict, runtime, "guard")

    def test_invalid_entries_are_noted_and_ignored(self):
        bad = ["deploy", "../x.yml", "a/b/c:x.yml", "x.yml extra", "acme:x.yml", "*.yml",
               "", 7]
        self.use_overlay(json.dumps({"guard": {"allow_workflows": bad}}))
        result = self.run_guard(json.dumps(self.event("Bash", command="gh workflow run x.yml")))
        notes = [line for line in result.stderr.splitlines()
                 if "invalid guard.allow_workflows entry" in line]
        self.assertEqual(len(notes), len(bad), result.stderr)
        self.assertIn("deny", result.stdout)

    def test_the_core_checkouts_overlay_does_not_allow_the_command_repository(self):
        del self.env["HARNESS_PROJECT"]
        core = Path(self.temp.name) / "core"
        (core / "scripts").mkdir(parents=True)
        for module in ("guard.py", "project.py"):
            shutil.copy(CORE / "scripts" / module, core / "scripts" / module)
        subprocess.check_call(["git", "init", "-q", str(core)], env=self.env)
        (core / "harness/project").mkdir(parents=True)
        (core / "harness/project/project.json").write_text(ALLOW)

        def decision(command):
            event = json.dumps(self.event("Bash", command=command))
            result = subprocess.run([sys.executable, str(core / "scripts/guard.py"),
                                     "--runtime", "claude"], cwd=self.root, env=self.env,
                                    input=event, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)["hookSpecificOutput"] if result.stdout else None

        found = decision("gh workflow run env-power.yml")
        self.assertEqual(found and found["permissionDecision"], "deny")
        self.put("harness/project/project.json", ALLOW)
        self.assertIsNone(decision("gh workflow run env-power.yml"))


class EntryPointTest(GuardHarness, unittest.TestCase):
    def test_repository_wrappers_are_not_deploy_commands(self):
        """Vetted wrappers need no overlay entry; what they run is the owner's review."""
        absolute = f"{self.root}/scripts/release.sh v1.2.3"
        for runtime in ("claude", "codex"):
            for command in (*WRAPPERS, absolute):
                with self.subTest(command=command, runtime=runtime):
                    self.assertIsNone(self.bash(command, runtime))

    def test_a_wrapper_does_not_cover_what_follows_it(self):
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                self.assert_denied(self.bash("scripts/release.sh && kubectl apply -f x.yaml",
                                             runtime), "Deploy")
                self.assert_denied(self.bash("scripts/release.sh; helm rollback api 3",
                                             runtime), "Deploy")

    def test_rollout_status_and_history_are_reads(self):
        for runtime in ("claude", "codex"):
            for command in READS:
                with self.subTest(command=command, runtime=runtime):
                    self.assertIsNone(self.bash(command, runtime))


if __name__ == "__main__":
    unittest.main()
