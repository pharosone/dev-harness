"""The PreToolUse guard denies risky tool calls before they run; look-alikes stay allowed.

CoreGuardTest runs without any overlay: only the core rules apply. ProjectGuardTest uses
the fixture overlay (tests/fixture/project.json), whose deploy hosts, deploy users, host
files, production compose files and generated directories belong to no real project.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
GUARD = CORE / "scripts" / "guard.py"
FIXTURE = Path(__file__).resolve().parent / "fixture"


def guard_path() -> str:
    """The guard's path relative to the checkout holding the core: scripts/guard.py in the
    core repository itself, harness/core/scripts/guard.py when vendored."""
    for folder in (CORE, *CORE.parents):
        if (folder / ".git").exists():
            return GUARD.relative_to(folder).as_posix()
    return "harness/core/scripts/guard.py"


SELF = guard_path()
SELF_DIR = SELF.rsplit("/", 1)[0]

PUSHES = [
    "git push",
    "git push origin hv2-guard",
    "git -C /tmp/repo push",
    "git --no-pager push --force-with-lease",
    "git -c push.default=current push",
    "/usr/bin/git push",
    "GIT_TRACE=1 git push",
    "sudo git push",
    "cd server && git push",
    "make test; git push",
    "false || git push",
    "git status | git push",
    "ls\ngit push",
    "git \\\n  push",
    "(cd web && git push)",
    "if true; then git push; fi",
    "echo $(git push)",
    "echo `git push`",
    "bash -c 'git push origin main'",
    'sh -c "git fetch && git push"',
    "bash -lc 'git push'",
    "eval 'git push'",
    "git -c alias.p=push p origin HEAD",
    "git -c alias.p='!git push' p",
    "git --config-env=alias.p=PUSHCMD p",
    "git config alias.ship push",
    "git config --global alias.ship 'push origin HEAD'",
    "git config alias.ship push && git ship",
    "git config alias.yolo '!git push'",
    "git send-pack origin HEAD:refs/heads/x",
    "git http-push https://example.invalid/r.git main",
    "git subtree push --prefix=sdk origin sdk",
    "git $(echo push)",
    "v=push; git $v",
    "git `echo push`",
    'echo "$(git push)"',
    "echo push | xargs git",
    "echo main | xargs -n1 git push origin",
    "python3 harness/core/scripts/quiet.py -- git push origin HEAD",
    "harness/core/scripts/quiet.py --log /tmp/x.log -- git push",
    "env -S 'git push'",
    "env -u HOME git push",
    "nohup git push &",
    "timeout -s KILL 60 git push",
    "nice -n 5 git push",
    "sudo -u deploy git push",
    "csh -c 'git push'",
    "bash <<< 'git push'",
    "python3 -c \"import subprocess; subprocess.run(['git', 'push'])\"",
    "python3 -c 'import os; os.system(\"git push origin main\")'",
]

BROAD_ADDS = [
    "git add -A",
    "git add --all",
    "git add -u",
    "git add --update",
    "git add .",
    "git add ./",
    "git add :/",
    "git add -Av",
    "git add -- .",
    "git -C server add .",
    "git add -A && git commit -m wip",
    "bash -c 'git add --all'",
    "git stage -A",
    "git stage .",
    "git add --al",
    "git add --upd",
    "git add --a",
    "git add --no-ignore-removal",
    "git add --no-ignore-r",
    "git add '**'",
    "git add ':(exclude)zz'",
    "git add ':!web'",
    "cd server && git add ..",
    "git -C server add ..",
    "git add ../..",
    'git add "$PWD"',
    "git add $(pwd)",
    "git add --pathspec-from-file=list.txt",
]

COMMIT_ALLS = [
    "git commit -a -m fix",
    "git commit --all -m fix",
    'git commit -am "fix"',
    "git commit -qam fix",
    "git -C web commit -a",
    "git add server/a.go && git commit -am x",
    "git commit -m x .",
    "git commit -m x -- .",
    "git commit -m x -- :/",
    "git commit --al -m x",
    "git commit -m x ..",
    "git commit -m x --pathspec-from-file=list.txt",
]

DEPLOYS = [
    "helm install app deploy/chart/app",
    "helm upgrade --install app deploy/chart/app",
    "helm -n app uninstall app",
    "helm rollback app 3",
    "helm delete app",
    "kubectl apply -f deploy/k8s.yaml",
    "kubectl -n app delete pod api-0",
    "kubectl patch deploy api -p '{}'",
    "kubectl replace -f api.yaml",
    "kubectl create secret generic x",
    "kubectl --context=prod scale deploy/api --replicas=0",
    "kubectl rollout restart deploy/api",
    "ssh deploy@203.0.113.7",
    "ssh -i ~/.ssh/id root@app.example.org 'docker ps'",
    "gh workflow run deploy.yml",
    "gh -R acme/app workflow run cd.yml",
    "railway up",
    "railway deploy",
    "fly deploy",
    "flyctl deploy --app api",
    "fly -t tok deploy",
    "cd deploy && helm upgrade app ./chart",
    "ssh root@ci.example.com uptime",
    "ssh root@198.51.100.23 'cd /opt/app && docker compose up -d'",
    "rsync -az ./ root@198.51.100.23:/opt/app/",
    "ssh -l deploy 198.51.100.23",
    "ssh -o User=deploy 198.51.100.23",
    "ssh -oUser=root build.example.com",
    "scp a.txt 198.51.100.23:/tmp/",
    "ssh ops@198.51.100.24 uptime",
    "sftp 198.51.100.23",
    "rsync -e ssh web/out/ ops@198.51.100.24:/srv/",
    "python3 harness/core/scripts/quiet.py -- ssh ops@198.51.100.23",
]

# The fixture overlay's deploy hosts, users and production compose files; each maps a
# case that the guard once hard-coded for its first project.
PROJECT_DEPLOYS = [
    "ssh -i ~/.ssh/id ops@app.deploy.example.com 'docker ps'",
    "ssh ops@deploy.example.com uptime",
    "rsync -az ./ ops@deploy.example.com:/srv/",
    "scp a.txt deploy.example.com:/tmp/",
    "ssh release@build.example.com uptime",
    "ssh -l release 192.0.2.44 uptime",
    "ssh -oUser=release build.example.com",
    "docker compose -f compose.prod.yaml -f gpu.override.yaml up -d",
    "docker compose -f /opt/app/deploy/prod/compose.prod.yaml ps",
    "docker compose --file=deploy/prod/auth.override.yaml pull",
    "docker-compose -f compose.prod.yml up",
    "COMPOSE_FILE=compose.prod.yaml:gpu.override.yaml docker compose up -d",
]

ENV_READS = [
    "cat .env",
    "cat server/.env.local",
    "cat '.env'",
    "less .env.production",
    "more .env",
    "head -5 .env",
    "tail -n 2 /srv/app/.env",
    "bat .env",
    "grep KEY .env",
    "rg SECRET deploy/.env",
    "sed -n 1p .env",
    "awk '{print}' .env",
    "source .env",
    ". ./.env",
    "set -a; source .env.local; set +a",
    "cat < .env",
    "cat .env*",
    "bash -c 'cat .env'",
    'echo "$(cat .env)"',
    'echo "`cat .env`"',
    "cat .ENV",
    "cat .Env.local",
    "cp .env /tmp/e",
    "dd if=.env of=/tmp/e",
    "ln -s .env e2.txt",
    "python3 harness/core/scripts/quiet.py -- cat .env",
    "python3 -c \"print(open('.env').read())\"",
    "nohup cat .env",
    "timeout -s KILL 5 cat .env",
    "env -S 'cat .env'",
]

ENV_WRITES = [
    "echo X=1 >> .env",
    "printf x > server/.env.local",
    "printf X=1 | tee .env",
    "tee -a .env < /dev/null",
    "cp x .env",
    "cp .env.example .env",
    "mv x deploy/prod/.env",
    "dd if=x of=.env",
    "printf x > .ENV",
    "rm .env",
    "cd server && printf x > .env",
]

GENERATED_WRITES = [
    "printf x > gen/db/models.go",
    "cp /tmp/x gen/db/models.go",
    "cp /tmp/x gen/db/",
    "tee gen/db/extra.go < /dev/null",
    "sed -i '' 's/a/b/' gen/db/models.go",
    "cd gen && echo x > db/models.go",
    "printf x > Gen/DB/models.go",
]

SELF_COMMANDS = [
    f"printf x > {SELF}",
    "sed -i '' 's/a/b/' .claude/settings.json",
    "cp /tmp/x .codex/hooks.json",
    "echo x | tee .codex/config.toml",
    f"rm {SELF}",
    f"mv {SELF} /tmp/g.py",
    "bash -c 'printf x >> .claude/settings.json'",
    "printf x > harness/core/scripts/guard.py",
    "printf '{}' > harness/project/project.json",
    "git config core.worktree /tmp/x",
    "git config core.hooksPath /dev/null",
    "git config --unset core.hooksPath",
    "git config set core.worktree /tmp/x",
    "git config --local core.hooksPath .hooks",
]

CLEANS = [
    "git clean -f",
    "git clean -fd",
    "git clean -fdx",
    "git clean -fdX",
    "git clean -df .claude",
    "git -C web clean -f",
    "git stash -u",
    "git stash push --include-untracked",
    "git stash --all",
    "git stash save -u wip",
    "git stash push -a -m x",
]

PERSONAL_SHELL = [
    "rm .claude/agents/mine.md",
    "rm -f .claude/agents/ignored.md",
    "sed -i '' 's/a/b/' .claude/agents/mine.md",
    "mv .claude/agents/mine.md /tmp/",
    "echo x > .claude/agents/ignored.md",
    "cp /tmp/x .codex/agents/mine.toml",
    "rm -rf .claude/agents",
    "rm -rf .claude/skills",
    "rm -rf .claude",
]

ALLOWED_COMMANDS = [
    "git add server/a.go web/b.ts",
    "git add ./server/a.go",
    "git add -p server/a.go",
    'git commit -m "add all"',
    'git commit -m "-a"',
    "git commit --amend --no-edit",
    "git status",
    "git diff --stat",
    "git log --all --oneline | head -20",
    "git stash push -m wip",
    'echo "git push"',
    "helm template app deploy/chart/app",
    "helm lint deploy/chart/app",
    "kubectl get pods",
    "kubectl -n app logs deploy/api",
    "docker compose up -d",
    "docker compose -f docker-compose.yaml up -d --build",
    "ssh build@ci.example.com uptime",
    "gh workflow list",
    "gh pr view 12",
    "railway status",
    "fly status",
    "flyctl logs -a api",
    "cat .env.example",
    "rg -n DATABASE_URL .env.example",
    "cat .envrc",
    "ls -la .env",
    "grep -r env server/",
    "cat docs/environment.md",
    "python3 -m unittest discover -s harness/core/tests",
    "git -c alias.st=status st",
    "git config alias.st status",
    "git config --get alias.ship",
    "git config core.worktree",
    "git config --get core.hooksPath",
    "git subtree split --prefix=sdk",
    "git --version",
    "git clean -n",
    "git clean -nd",
    "git clean --dry-run -fd",
    "git stash list",
    "git stash pop",
    "git stash push -m 'add all'",
    "git add --intent-to-add server/a.go",
    "git add server/",
    "git add -- server/a.go",
    "git stage server/a.go",
    "git commit -m x server/a.go",
    "git commit -m x -- server/a.go",
    "git commit --amend -m x",
    "git diff $(git merge-base HEAD main)",
    "ssh ops@198.51.100.99 uptime",
    "ssh 127.0.0.1 uptime",
    "scp build@ci.example.com:a.txt .",
    "ssh -l build 192.0.2.44 uptime",
    "rsync -a web/out/ build@ci.example.com:/srv/site/",
    "python3 harness/core/scripts/quiet.py -- go test ./...",
    "nohup make dev",
    "timeout -s KILL 60 make test",
    "nice -n 5 make test",
    "env -u FOO make test",
    "sudo -u build make",
    "xargs -n1 echo",
    "python3 -c \"import subprocess; subprocess.run(['git', 'status'])\"",
    "python3 -c \"print('hello')\"",
    'echo "$(git status)"',
    "echo '$(cat .env)'",
    'echo "\\$(cat .env)"',
    "echo hi | tee out.log",
    "cp a.txt b.txt",
    "mv a.go b.go",
    "dd if=/dev/zero of=out.bin count=1",
    "rm -f out.log",
    "sed -i '' 's/a/b/' ui/lib/a.ts",
    "echo x > notes.md",
    "echo x > .claude/agents/new.md",
    "rm -f .claude/agents/new.md",
    "cat .claude/settings.json",
    "printf '{}' > harness/project/tiers.json",
    f"python3 {SELF} --runtime claude < event.json",
]

# Without an overlay these name nothing the core protects.
PROJECT_ONLY = [
    "ssh ops@app.deploy.example.com uptime",
    "ssh release@build.example.com uptime",
    "ssh -l release 192.0.2.44 uptime",
    "docker compose -f compose.prod.yaml up -d",
    "docker-compose -f compose.prod.yml up",
    "printf x > gen/db/models.go",
    "cp /tmp/x gen/db/",
]

# Look-alikes of the fixture overlay's rules.
PROJECT_ALLOWED = [
    "ssh ops@notdeploy.example.com uptime",
    "ssh ops@deploy.example.community uptime",
    "ssh build@ci.example.com uptime",
    "ssh -l releases 192.0.2.44 uptime",
    "docker compose -f compose.yaml up -d --build",
    "docker compose -f compose.dev.yaml up -d",
    "docker compose -f gpu.yaml up -d",
    "printf x > gen/queries/x.sql",
    "printf x > gen/dbx/x.go",
    "rm gen/db/stale.go",
    "cat gen/db/models.go",
]


class GuardHarness:
    overlay: Path | None = None  # HARNESS_PROJECT for the guard; None: an empty overlay

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        # An explicit overlay keeps any overlay around the vendored core out of the tests.
        self.env["HARNESS_PROJECT"] = str(self.overlay or Path(self.temp.name) / "no-overlay")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        tracked = {
            ".gitignore": ".claude/agents/ignored.md\n",
            "gen/db/models.go": "package db\n",
            "gen/queries/x.sql": "-- name: X :one\n",
            "server/x_test.go": 'func TestX(t *testing.T) {\n\tt.Skip("flaky")\n}\n',
            "ui/lib/a.test.ts": 'it("works", () => {});\n',
            "ui/lib/a.ts": "export const a = 1;\n",
            ".claude/agents/shared.md": "# shared\n",
            "skills/shared/SKILL.md": "# shared skill\n",
            ".github/workflows/deploy.yml": "# DEPLOY_HOST - VM address (e.g. 198.51.100.23)\n",
            ".github/workflows/ci.yml": "# runner 198.51.100.88\n",
            "deploy/observability/README.md": ("Log in with ssh ops@198.51.100.24.\n"
                                               "Grafana listens on 127.0.0.1:3000.\n"),
            "ui/lib/auth.test.ts": 'it("checks authorization", () => {});\n',
            SELF: "# guard\n",
            ".claude/settings.json": "{}\n",
            ".codex/hooks.json": "{}\n",
            ".codex/config.toml": "",
        }
        for name, text in tracked.items():
            self.put(name, text)
        (self.root / ".claude/skills").mkdir(parents=True)
        os.symlink("../../skills/shared", self.root / ".claude/skills/shared")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        for name in (".claude/agents/mine.md", ".claude/skills/own/SKILL.md",
                     ".codex/agents/mine.toml", ".agents/skills/own/SKILL.md",
                     ".claude/agents/ignored.md", "notes.md"):
            self.put(name, "# personal\n")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env, text=True)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def run_guard(self, stdin, args=("--runtime", "claude")):
        return subprocess.run([sys.executable, str(GUARD), *args], cwd=self.root, env=self.env,
                              input=stdin, capture_output=True, text=True)

    def verdict(self, event, runtime="claude"):
        """Return (decision, reason) or None; asserts the hook contract on the way."""
        result = self.run_guard(json.dumps(event), ("--runtime", runtime))
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        self.assertEqual(len(result.stdout.strip().splitlines()), 1, result.stdout)
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {"hookSpecificOutput"})
        specific = output["hookSpecificOutput"]
        self.assertEqual(set(specific),
                         {"hookEventName", "permissionDecision", "permissionDecisionReason"})
        self.assertEqual(specific["hookEventName"], "PreToolUse")
        self.assertIn(specific["permissionDecision"], ("deny", "ask"))
        reason = specific["permissionDecisionReason"]
        self.assertTrue(reason.endswith("."), reason)
        self.assertNotIn("\n", reason)
        self.assertNotIn(". ", reason, "the reason must be one sentence")
        return specific["permissionDecision"], reason

    def event(self, tool, cwd=None, **tool_input):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                "cwd": str(cwd or self.root)}

    def bash(self, command, runtime="claude", tool="Bash"):
        return self.verdict(self.event(tool, command=command), runtime)

    def path(self, name):
        return str(self.root / name)

    def edit(self, name, old="a", new="b", runtime="claude"):
        return self.verdict(self.event("Edit", file_path=self.path(name), old_string=old,
                                       new_string=new), runtime)

    def write(self, name, content="x\n", runtime="claude"):
        return self.verdict(self.event("Write", file_path=self.path(name), content=content), runtime)

    def patch(self, body, runtime="codex", tool="apply_patch"):
        text = f"*** Begin Patch\n{body}\n*** End Patch\n"
        return self.verdict(self.event(tool, command=text), runtime)

    def raw_event(self, tool, tool_input):
        return {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input,
                "cwd": str(self.root)}

    def assert_guarded(self, verdict, runtime, *words):
        """Self-protection: the user approves on claude, agents are refused on codex."""
        self.assertIsNotNone(verdict)
        decision, reason = verdict
        self.assertEqual(decision, "ask" if runtime == "claude" else "deny", reason)
        for word in words:
            self.assertIn(word, reason)

    def assert_denied(self, verdict, *words):
        self.assertIsNotNone(verdict)
        decision, reason = verdict
        self.assertEqual(decision, "deny", reason)
        for word in words:
            self.assertIn(word, reason)

    def use_overlay(self, text):
        """Point the guard at a fresh overlay whose project.json holds text."""
        folder = Path(tempfile.mkdtemp(dir=self.temp.name, prefix="overlay-"))
        (folder / "project.json").write_text(text)
        self.env["HARNESS_PROJECT"] = str(folder)
        return folder


class CoreGuardTest(GuardHarness, unittest.TestCase):
    """The core rules, with no project overlay."""

    # Shell commands

    def test_push_asks_the_user_on_claude(self):
        for command in PUSHES:
            with self.subTest(command=command):
                verdict = self.bash(command)
                self.assertIsNotNone(verdict)
                self.assertEqual(verdict[0], "ask")
                self.assertIn("push", verdict[1])

    def test_push_is_denied_on_codex(self):
        for command in PUSHES:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command, runtime="codex"), "push")

    def test_broad_staging_is_denied(self):
        for command in BROAD_ADDS:
            for runtime in ("claude", "codex"):
                with self.subTest(command=command, runtime=runtime):
                    self.assert_denied(self.bash(command, runtime), "explicit paths")

    def test_commit_all_is_denied(self):
        for command in COMMIT_ALLS:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), "explicit paths")

    def test_deploy_commands_are_denied(self):
        for command in DEPLOYS:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), "CD")

    def test_project_rules_need_an_overlay(self):
        for command in PROJECT_ONLY:
            with self.subTest(command=command):
                self.assertIsNone(self.bash(command))
        self.assertIsNone(self.edit("gen/db/models.go"))

    def test_heredoc_commit_messages_are_data_but_substitutions_are_commands(self):
        message = ("git commit -m \"$(cat <<'EOF'\nfeat: stage explicit paths\n\n"
                   "Never git push or git add -A here.\nEOF\n)\"")
        self.assertIsNone(self.bash(message))
        self.assertIsNone(self.bash("cat > notes.md <<'EOF'\ngit push origin main\nEOF"))
        self.assert_denied(self.bash('echo "$(cat .env)"'), ".env")
        self.assert_denied(self.bash("git commit -m \"$(cat .env)\""), ".env")

    def test_in_place_shell_edits_that_add_skips_are_denied(self):
        for command in ("sed -i 's/it(/it.skip(/g' ui/lib/auth.test.ts",
                        "sed -i '' -e 's/describe(/describe.skip(/' ui/lib/a.test.tsx",
                        "perl -pi -e 's/\\bit\\(/xit(/' web/x.test.ts",
                        "sed -i 's/^func TestX(t \\*testing.T) {/&\\n\\tt.Skip()/' server/a_test.go"):
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), "skip")
        for command in ("sed -i 's/foo/bar/' ui/lib/auth.test.ts",
                        "sed -i 's/it(/it.skip(/' ui/lib/notes.md",
                        "sed -n '/it.skip(/p' ui/lib/auth.test.ts"):
            with self.subTest(command=command):
                self.assertIsNone(self.bash(command))

    def test_reading_env_files_is_denied(self):
        for command in ENV_READS:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), ".env")

    def test_shell_writes_to_env_files_are_denied(self):
        for command in ENV_WRITES:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), ".env")

    def test_shell_writes_to_the_guard_and_hook_config_need_the_user(self):
        for command in SELF_COMMANDS:
            for runtime in ("claude", "codex"):
                with self.subTest(command=command, runtime=runtime):
                    self.assert_guarded(self.bash(command, runtime), runtime, "guard")

    def test_cleaning_or_stashing_untracked_files_is_denied(self):
        for command in CLEANS:
            for runtime in ("claude", "codex"):
                with self.subTest(command=command, runtime=runtime):
                    self.assert_denied(self.bash(command, runtime), "untracked")

    def test_shell_changes_to_personal_files_are_denied(self):
        for command in PERSONAL_SHELL:
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), "personal")

    def test_configured_git_aliases_are_resolved(self):
        self.git("config", "alias.ship", "push")
        self.git("config", "alias.yolo", "!git push origin HEAD")
        self.git("config", "alias.st", "status")
        for command in ("git ship", "git ship origin HEAD", "git yolo", "git -C server ship"):
            with self.subTest(command=command):
                self.assertEqual(self.bash(command)[0], "ask")
                self.assert_denied(self.bash(command, "codex"), "push")
        self.assertIsNone(self.bash("git st"))
        self.assertIsNone(self.bash("git st", "codex"))

    def test_staging_the_repository_root_by_absolute_path_is_denied(self):
        self.assert_denied(self.bash(f"git add {self.root}"), "explicit paths")
        self.assert_denied(self.bash(f"git add {self.root}/"), "explicit paths")
        self.assertIsNone(self.bash(f"git add {self.root}/server/a.go"))

    def test_deploy_hosts_are_read_from_the_repository_at_decision_time(self):
        self.assertIsNone(self.bash("ssh ops@198.51.100.77 uptime"))
        self.put("deploy/prod/hosts.md", "new box 198.51.100.77\n")
        self.assert_denied(self.bash("ssh ops@198.51.100.77 uptime"), "CD")
        self.assertIsNone(self.bash("ssh ops@198.51.100.88 uptime"), "only deploy workflows count")

    def test_allowed_look_alikes_pass(self):
        for command in ALLOWED_COMMANDS:
            for runtime in ("claude", "codex"):
                with self.subTest(command=command, runtime=runtime):
                    self.assertIsNone(self.bash(command, runtime))

    def test_codex_exec_command_cmd_is_checked(self):
        cases = [
            (self.event("exec_command", cmd="git push origin main"), "push"),
            (self.event("exec_command", cmd=["bash", "-lc", "git push"]), "push"),
            (self.event("exec_command", cmd="cat .env"), ".env"),
            (self.event("exec_command", cmd="printf x > .env.local",
                        workdir=str(self.root / "server")), ".env"),
        ]
        for event, word in cases:
            with self.subTest(event=event):
                self.assert_denied(self.verdict(event, "codex"), word)
        self.assertIsNone(self.verdict(self.event("exec_command", cmd="git status"), "codex"))
        self.assertIsNone(self.verdict(self.event("exec_command", cmd=["git", "status"]), "codex"))

    def test_codex_free_form_apply_patch_is_checked(self):
        denied = [
            ("*** Begin Patch\n*** Delete File: .env\n*** End Patch\n", ".env"),
            ("*** Begin Patch\n*** Add File: .ENV\n+A=1\n*** End Patch\n", ".env"),
            (f"*** Begin Patch\n*** Update File: {SELF}\n@@\n-# guard\n+# off\n"
             "*** End Patch\n", "guard"),
        ]
        for body, word in denied:
            with self.subTest(body=body):
                self.assert_denied(self.verdict(self.raw_event("apply_patch", body), "codex"), word)
        allowed = "*** Begin Patch\n*** Add File: server/a.go\n+package server\n*** End Patch\n"
        self.assertIsNone(self.verdict(self.raw_event("apply_patch", allowed), "codex"))

    def test_codex_shell_tools_are_checked_like_bash(self):
        for tool in ("exec_command", "shell", "Bash"):
            with self.subTest(tool=tool):
                self.assert_denied(self.bash("git push", "codex", tool), "push")
                self.assert_denied(self.bash("git add -A", "codex", tool), "explicit paths")
                self.assertIsNone(self.bash("git status", "codex", tool))

    def test_an_argv_list_command_is_checked(self):
        event = self.event("shell", command=["bash", "-lc", "git push origin main"])
        self.assert_denied(self.verdict(event, "codex"), "push")

    def test_unbalanced_quotes_are_still_checked(self):
        self.assertEqual(self.bash('git push "unterminated')[0], "ask")
        self.assert_denied(self.bash("git add -A; echo 'oops"), "explicit paths")

    def test_deny_wins_over_ask_in_one_command(self):
        self.assert_denied(self.bash("git add . && git push"), "explicit paths")

    # Claude file tools

    def test_env_and_local_settings_edits_are_denied(self):
        cases = [
            self.edit(".env"),
            self.write("server/.env.local"),
            self.write(".env.production"),
            self.verdict(self.event("MultiEdit", file_path=self.path(".env"),
                                    edits=[{"old_string": "a", "new_string": "b"}])),
            self.verdict(self.event("NotebookEdit", notebook_path=self.path(".env.ipynb"),
                                    new_source="x")),
        ]
        for verdict in cases:
            self.assert_denied(verdict, ".env")
        self.assert_denied(self.edit(".claude/settings.local.json"), "settings.local.json")
        self.assert_denied(self.write("web/.claude/settings.local.json"), "settings.local.json")

    def test_reading_env_files_with_file_tools_is_denied(self):
        self.assert_denied(self.verdict(self.event("Read", file_path=self.path(".env"))), ".env")
        self.assert_denied(self.verdict(self.event("Read", file_path=self.path("web/.env.local"))),
                           ".env")
        self.assert_denied(self.verdict(self.event("Grep", pattern="KEY", path=self.path(".env"))),
                           ".env")

    def test_env_example_and_ordinary_files_are_allowed(self):
        self.assertIsNone(self.edit(".env.example"))
        self.assertIsNone(self.verdict(self.event("Read", file_path=self.path(".env.example"))))
        self.assertIsNone(self.verdict(self.event("Read",
                                                  file_path=self.path(".claude/settings.local.json"))))
        self.assertIsNone(self.verdict(self.event("Read", file_path=self.path("server/env.go"))))
        self.assertIsNone(self.write("server/env.go", "package server\n"))
        self.assertIsNone(self.verdict(self.event("Grep", pattern="env", path=self.path("server"))))
        self.assertIsNone(self.verdict(self.event("Grep", pattern="x", path=self.path("."),
                                                  glob="*.go")))
        self.assertIsNone(self.verdict(self.event("Grep", pattern="x", path=self.path("."),
                                                  glob=".env.example")))

    def test_env_case_variants_and_grep_globs_are_denied(self):
        cases = [
            self.verdict(self.event("Read", file_path=self.path(".ENV"))),
            self.verdict(self.event("Read", file_path=self.path("web/.Env.local"))),
            self.write(".Env"),
            self.edit("server/.ENV.production"),
            self.verdict(self.event("Grep", pattern="KEY", path=self.path(".ENV"))),
            self.verdict(self.event("Grep", pattern="KEY", path=self.path("."), glob=".env*")),
            self.verdict(self.event("Grep", pattern="KEY", path=self.path("."), glob="**/.env")),
            self.verdict(self.event("Grep", pattern="KEY", glob=".ENV")),
        ]
        for verdict in cases:
            self.assert_denied(verdict, ".env")

    def test_symlinks_to_env_files_are_denied(self):
        self.put(".env", "A=1\n")
        os.symlink(".env", self.root / "e.txt")
        os.symlink("notes.md", self.root / "notes-link.md")
        cases = [
            self.verdict(self.event("Read", file_path=self.path("e.txt"))),
            self.write("e.txt"),
            self.edit("e.txt"),
            self.verdict(self.event("Grep", pattern="A", path=self.path("e.txt"))),
            self.bash("cat e.txt"),
            self.bash("printf x > e.txt"),
        ]
        for verdict in cases:
            self.assert_denied(verdict, ".env")
        self.assertIsNone(self.verdict(self.event("Read", file_path=self.path("notes-link.md"))))
        self.assertIsNone(self.bash("cat notes-link.md"))

    def test_guard_and_hook_config_edits_need_the_user(self):
        for runtime in ("claude", "codex"):
            cases = [
                self.edit(SELF, "# guard", "# off", runtime),
                self.write(".claude/settings.json", "{}\n", runtime),
                self.edit(".codex/hooks.json", "{}", "[]", runtime),
                self.write(".codex/config.toml", "x = 1\n", runtime),
                self.edit(".CLAUDE/Settings.json", "{}", "[]", runtime),
                self.verdict(self.event("MultiEdit", file_path=self.path(SELF),
                                        edits=[{"old_string": "# guard", "new_string": "#"}]),
                             runtime),
                self.patch("*** Update File: .codex/hooks.json\n@@\n-{}\n+[]", runtime),
                self.write("harness/core/scripts/guard.py", "# off\n", runtime),
                self.write("harness/project/project.json", "{}\n", runtime),
            ]
            for verdict in cases:
                with self.subTest(runtime=runtime, verdict=verdict):
                    self.assert_guarded(verdict, runtime, "guard")
        self.assert_denied(self.edit(".claude/settings.local.json"), "settings.local.json")
        self.assertIsNone(self.verdict(self.event("Read", file_path=self.path(SELF))))
        self.assertIsNone(self.edit("tools/gate.py"))
        if SELF_DIR == "scripts":  # the core repository: the guard's siblings are ordinary code
            self.assertIsNone(self.edit("scripts/gate.py"))
        self.assertIsNone(self.edit("docs/settings.json"))
        if SELF.startswith("harness/core/"):  # vendored: a sibling of the guard is core
            self.assert_guarded(self.write(f"{SELF}.bak"), "claude", "vendored core")
        else:
            self.assertIsNone(self.write(f"{SELF}.bak"))
        self.assertIsNone(self.write("harness/project/tiers.json", "{}\n"))
        self.assertIsNone(self.write("docs/project.json", "{}\n"))

    def test_the_guards_own_imports_are_part_of_the_guard(self):
        # a crash in an imported module would disable the guard through the hook's `|| true`
        for runtime in ("claude", "codex"):
            for verdict in (self.write(f"{SELF_DIR}/project.py", "raise SystemExit(1)\n", runtime),
                            self.edit(f"{SELF_DIR}/project.py", "import", "#", runtime),
                            self.bash(f"printf x > {SELF_DIR}/project.py", runtime)):
                with self.subTest(runtime=runtime, verdict=verdict):
                    self.assert_guarded(verdict, runtime, "guard")

    def test_the_vendored_core_is_not_edited_in_place(self):
        for runtime in ("claude", "codex"):
            for verdict in (self.write("harness/core/skills/x/SKILL.md", "x\n", runtime),
                            self.edit("harness/core/scripts/tier.py", "a", "b", runtime),
                            self.bash("sed -i '' 's/a/b/' harness/core/roles/implementer.md",
                                      runtime),
                            self.patch("*** Update File: harness/core/README.md\n@@\n-a\n+b",
                                       runtime)):
                with self.subTest(runtime=runtime, verdict=verdict):
                    self.assert_guarded(verdict, runtime, "vendored core", "propose")
        self.assertIsNone(self.write("harness/project/roles/implementer.md", "x\n"))
        self.assertIsNone(self.write("harness/corex/a.md", "x\n"))
        self.assertIsNone(self.verdict(self.event("Read",
                                                  file_path=self.path("harness/core/README.md"))))

    def test_existing_untracked_personal_files_are_denied(self):
        self.assert_denied(self.edit(".claude/agents/mine.md"), "personal")
        self.assert_denied(self.edit(".claude/agents/ignored.md"), "personal")
        self.assert_denied(self.write(".claude/skills/own/SKILL.md"), "personal")
        self.assert_denied(self.edit(".codex/agents/mine.toml"), "personal")
        self.assert_denied(self.edit(".agents/skills/own/SKILL.md"), "personal")

    def test_case_variants_of_personal_paths_are_denied(self):
        variant = self.edit(".CLAUDE/Agents/mine.md")
        if (self.root / ".GITIGNORE").exists():  # case-insensitive file system
            self.assert_denied(variant, "personal")
        else:
            self.assertIsNone(variant)

    def test_new_tracked_and_linked_personal_paths_are_allowed(self):
        self.assertIsNone(self.write(".claude/agents/new.md"))
        self.assertIsNone(self.write(".agents/skills/fresh/SKILL.md"))
        self.assertIsNone(self.edit(".claude/agents/shared.md"))
        self.assertIsNone(self.edit(".claude/skills/shared/SKILL.md"))
        self.assertIsNone(self.edit("notes.md"))
        self.assertIsNone(self.verdict(self.event("Read",
                                                  file_path=self.path(".claude/agents/mine.md"))))

    def test_adding_a_skip_marker_to_a_test_is_denied(self):
        new_files = [
            ("ui/lib/new.test.ts", 'it.skip("x", () => {});\n'),
            ("ui/lib/new.test.tsx", 'describe.skip("x", () => {});\n'),
            ("ui/lib/new.spec.ts", 'xit("x", () => {});\n'),
            ("ui/lib/new.spec.tsx", 'xdescribe("x", () => {});\n'),
            ("deploy/static/new.test.mjs", 'it.todo("later");\n'),
            ("server/new_test.go", '\tt.Skip("later")\n'),
            ("server/newf_test.go", '\tt.Skipf("later %d", 1)\n'),
            ("server/newnow_test.go", "\tt.SkipNow()\n"),
            ("harness/tests/test_new.py", '@unittest.skip("later")\n'),
            ("harness/tests/test_mark.py", '@pytest.mark.skip(reason="later")\n'),
        ]
        for name, content in new_files:
            with self.subTest(name=name):
                self.assert_denied(self.write(name, content), "tripwire")
        self.assert_denied(self.edit("ui/lib/a.test.ts", 'it("works"', 'it.skip("works"'),
                           "tripwire")
        doubled = 'func TestX(t *testing.T) {\n\tt.Skip("flaky")\n\tt.Skip("again")\n}\n'
        self.assert_denied(self.write("server/x_test.go", doubled), "tripwire")
        multi = self.event("MultiEdit", file_path=self.path("ui/lib/a.test.ts"),
                           edits=[{"old_string": 'it("works"', "new_string": 'it.skip("works"'}])
        self.assert_denied(self.verdict(multi), "tripwire")

    def test_skip_markers_are_counted_in_the_resulting_file(self):
        name = "ui/lib/auth.test.ts"
        self.assert_denied(self.edit(name, "it", "it.skip"), "tripwire")
        replace_all = self.event("Edit", file_path=self.path(name), old_string="it",
                                 new_string="it.skip", replace_all=True)
        self.assert_denied(self.verdict(replace_all), "tripwire")
        multi = self.event("MultiEdit", file_path=self.path(name),
                           edits=[{"old_string": "it(", "new_string": "it.only("},
                                  {"old_string": ".only", "new_string": ".skip"}])
        self.assert_denied(self.verdict(multi), "tripwire")
        self.assertIsNone(self.edit(name, "checks", "verifies"))
        self.assertIsNone(self.edit(name, 'it("checks', 'it("still checks'))

    def test_test_edits_without_new_skips_are_allowed(self):
        kept = 'func TestX(t *testing.T) {\n\tt.Skip("flaky")\n\tt.Log("more")\n}\n'
        self.assertIsNone(self.write("server/x_test.go", kept))
        self.assertIsNone(self.edit("server/x_test.go", '\tt.Skip("flaky")\n', ""))
        self.assertIsNone(self.edit("ui/lib/a.ts", "a = 1", 'a = it.skip("x")'))
        self.assertIsNone(self.write("harness/tests/test_exit.py", "sys.exit(main())\n"))
        self.assertIsNone(self.write("ui/lib/exit.test.ts", "process.exit(1);\n"))
        self.assertIsNone(self.write("ui/lib/b.test.ts", 'it("works", () => {});\n'))

    # Codex file tools

    def test_codex_apply_patch_is_denied_for_guarded_paths(self):
        bodies = [
            ("*** Update File: .env\n@@\n-A=1\n+A=2", ".env"),
            ("*** Delete File: server/.env.local", ".env"),
            ("*** Update File: notes.md\n*** Move to: .claude/settings.local.json\n@@\n-a\n+b",
             "settings.local.json"),
            ("*** Update File: .claude/agents/mine.md\n@@\n-# personal\n+# changed", "personal"),
            ('*** Add File: ui/lib/new.test.ts\n+it.skip("x", () => {});', "tripwire"),
            ('*** Update File: ui/lib/a.test.ts\n@@\n-it("works", () => {});\n'
             '+it.skip("works", () => {});', "tripwire"),
            ("*** Add File: server/a.go\n+package server\n*** Update File: .env\n@@\n-A\n+B",
             ".env"),
        ]
        for body, word in bodies:
            with self.subTest(body=body):
                self.assert_denied(self.patch(body), word)

    def test_codex_apply_patch_allows_ordinary_changes(self):
        bodies = [
            "*** Add File: server/a.go\n+package server",
            '*** Update File: server/x_test.go\n@@\n-\tt.Skip("flaky")\n+\tt.Log("fixed")',
            "*** Add File: .claude/agents/new.md\n+# new",
            "*** Update File: .env.example\n@@\n-A=\n+A=x",
        ]
        for body in bodies:
            with self.subTest(body=body):
                self.assertIsNone(self.patch(body))

    def test_apply_patch_through_the_shell_is_checked(self):
        command = ("apply_patch <<'EOF'\n*** Begin Patch\n*** Update File: .env\n@@\n-A=1\n+A=2\n"
                   "*** End Patch\nEOF")
        self.assert_denied(self.bash(command, "codex", "exec_command"), ".env")

    def test_codex_relative_file_paths_resolve_against_cwd(self):
        event = self.event("Write", file_path=".env", content="A=1\n")
        self.assert_denied(self.verdict(event, "codex"), ".env")
        event = self.event("Edit", cwd=self.root / "server", file_path=".env.local",
                           old_string="a", new_string="b")
        self.assert_denied(self.verdict(event, "codex"), ".env")

    # Fail open

    def test_broken_input_fails_open_with_a_note(self):
        for stdin in ("not json", "[]", '"text"', ""):
            with self.subTest(stdin=stdin):
                result = self.run_guard(stdin)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.assertTrue(result.stderr.strip())

    def test_bad_arguments_fail_open(self):
        for args in ((), ("--runtime", "vim")):
            with self.subTest(args=args):
                result = self.run_guard(json.dumps(self.event("Bash", command="git push")), args)
                self.assertEqual((result.returncode, result.stdout), (0, ""))
                self.assertTrue(result.stderr.strip())

    def test_events_without_a_decision_print_nothing(self):
        events = [
            {"hook_event_name": "PreToolUse", "tool_name": "Bash", "cwd": str(self.root)},
            self.event("Bash", command=42),
            self.event("WebFetch", url="https://example.com"),
            self.event("Edit", old_string="a", new_string="b"),
            self.event("Read", file_path=None),
        ]
        for event in events:
            with self.subTest(event=event):
                result = self.run_guard(json.dumps(event))
                self.assertEqual((result.returncode, result.stdout), (0, ""), result.stderr)
                self.assertEqual(result.stderr, "", "no overlay is not worth a note")


class ProjectGuardTest(GuardHarness, unittest.TestCase):
    """The fixture overlay's rules, on top of the core rules."""

    overlay = FIXTURE

    def test_project_deploy_hosts_users_and_compose_files_are_denied(self):
        for command in PROJECT_DEPLOYS:
            for runtime in ("claude", "codex"):
                with self.subTest(command=command, runtime=runtime):
                    self.assert_denied(self.bash(command, runtime), "CD")

    def test_core_rules_still_apply(self):
        for command in ("ssh root@ci.example.com uptime", "ssh ops@198.51.100.24 uptime",
                        "helm install app deploy/chart/app"):
            with self.subTest(command=command):
                self.assert_denied(self.bash(command), "CD")
        self.assertEqual(self.bash("git push")[0], "ask")
        self.assert_denied(self.bash("cat .env"), ".env")

    def test_project_host_files_are_read_at_decision_time(self):
        for host in ("203.0.113.50", "203.0.113.51", "203.0.113.52"):
            with self.subTest(host=host):
                self.assertIsNone(self.bash(f"ssh ops@{host} uptime"))
        self.put("infra/prod.hosts", "203.0.113.50\n")
        self.put("ops/inventory/eu/web.ini", "web1 ansible_host=203.0.113.51\n")
        self.put("infra/notes.md", "old box 203.0.113.52\n")
        self.assert_denied(self.bash("ssh ops@203.0.113.50 uptime"), "CD")
        self.assert_denied(self.bash("scp a.txt 203.0.113.51:/tmp/"), "CD")
        self.assertIsNone(self.bash("ssh ops@203.0.113.52 uptime"), "only listed files count")

    def test_shell_writes_to_generated_output_are_denied(self):
        for command in GENERATED_WRITES:
            for runtime in ("claude", "codex"):
                with self.subTest(command=command, runtime=runtime):
                    self.assert_denied(self.bash(command, runtime), "gen/db/", "make gen")

    def test_generated_output_edits_are_denied(self):
        self.assert_denied(self.edit("gen/db/models.go"), "make gen")
        self.assert_denied(self.write("gen/db/extra.go"), "make gen")
        self.assert_denied(self.edit("Gen/DB/models.go"), "make gen")
        self.assert_denied(self.patch("*** Add File: gen/db/extra.go\n+package db"), "make gen")
        body = ("*** Begin Patch\n*** Update File: gen/db/models.go\n@@\n"
                "-package db\n+package dbx\n*** End Patch\n")
        self.assert_denied(self.verdict(self.raw_event("apply_patch", body), "codex"), "make gen")
        event = self.event("exec_command", cmd="printf x > db/models.go",
                           workdir=str(self.root / "gen"))
        self.assert_denied(self.verdict(event, "codex"), "make gen")
        event = self.event("Edit", cwd=self.root / "gen", file_path="db/models.go",
                           old_string="a", new_string="b")
        self.assert_denied(self.verdict(event, "codex"), "make gen")
        self.assertIsNone(self.edit("gen/queries/x.sql"))
        self.assertIsNone(self.verdict(self.event("Read", file_path=self.path("gen/db/models.go"))))

    def test_project_look_alikes_pass(self):
        for command in PROJECT_ALLOWED + ALLOWED_COMMANDS:
            for runtime in ("claude", "codex"):
                with self.subTest(command=command, runtime=runtime):
                    self.assertIsNone(self.bash(command, runtime))

    def test_the_overlay_that_configures_the_guard_needs_the_user(self):
        overlay = str(FIXTURE / "project.json")
        for runtime in ("claude", "codex"):
            with self.subTest(runtime=runtime):
                event = self.event("Edit", file_path=overlay, old_string="release",
                                   new_string="x")
                self.assert_guarded(self.verdict(event, runtime), runtime, "guard")
                self.assert_guarded(self.bash(f"printf '{{}}' > {overlay}", runtime), runtime,
                                    "guard")
        self.assertIsNone(self.verdict(self.event("Read", file_path=overlay)))

    def test_an_overlay_in_the_command_repository_applies(self):
        del self.env["HARNESS_PROJECT"]
        self.assertIsNone(self.bash("ssh ops@inrepo.example.net uptime"))
        self.put("harness/project/project.json",
                 json.dumps({"guard": {"deploy_hosts": ["inrepo.example.net"]}}))
        self.assert_denied(self.bash("ssh ops@inrepo.example.net uptime"), "CD")

    def test_overlay_lists_extend_the_core_rules(self):
        self.use_overlay(json.dumps({"guard": {"deploy_users": [], "deploy_host_files": [],
                                               "generated": []}}))
        self.assert_denied(self.bash("ssh root@ci.example.com uptime"), "CD")
        self.assert_denied(self.bash("ssh ops@198.51.100.24 uptime"), "CD")
        self.assertIsNone(self.bash("ssh release@build.example.com uptime"))

    def test_a_generated_directory_without_a_hint_gets_the_default_one(self):
        self.use_overlay(json.dumps({"guard": {"generated": [{"path": "out/api/"}]}}))
        self.assert_denied(self.write("out/api/client.ts"), "out/api/", "regenerate")

    def test_a_broken_overlay_keeps_the_core_rules_with_a_note(self):
        overlays = [
            "{not json",
            "[]",
            json.dumps({"guard": ["deploy.example.com"]}),
            json.dumps({"guard": {"deploy_hosts": "deploy.example.com"}}),
            json.dumps({"guard": {"deploy_host": ["deploy.example.com"]}}),
        ]
        for text in overlays:
            with self.subTest(overlay=text):
                self.use_overlay(text)
                result = self.run_guard(json.dumps(self.event("Bash", command="git push")))
                self.assertEqual(result.returncode, 0)
                self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]
                                 ["permissionDecision"], "ask")
                self.assertIn("project.json", result.stderr)
                self.assert_denied(self.bash("ssh root@ci.example.com uptime"), "CD")
                self.assertIsNone(self.bash("ssh ops@deploy.example.com uptime"))

    def test_invalid_overlay_entries_are_skipped_with_a_note(self):
        self.use_overlay(json.dumps({"guard": {
            "deploy_hosts": ["deploy.example.com", 5, ""],
            "deploy_host_files": ["/etc/hosts", "../outside/*", "infra/*.hosts"],
            "generated": [{"hint": "no path"}, "gen/db/", {"path": "gen/db/", "hint": 3}],
        }}))
        result = self.run_guard(json.dumps(self.event("Bash", command="ssh ops@deploy.example.com")))
        self.assertEqual(json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"],
                         "deny")
        for word in ("deploy_hosts", "deploy_host_files", "generated"):
            self.assertIn(word, result.stderr)
        self.put("infra/prod.hosts", "203.0.113.60\n")
        self.assert_denied(self.bash("ssh ops@203.0.113.60 uptime"), "CD")
        self.assertIsNone(self.edit("gen/db/models.go"))


if __name__ == "__main__":
    unittest.main()
