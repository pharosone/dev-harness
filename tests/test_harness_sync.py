"""harness_sync.py installs, reports, updates and proposes the vendored core.

Real Git against local bare repositories only: a bare core with tags v1 and v2, a product
checkout that installs v1, edits it, pulls v2, conflicts, and proposes its edits back. `gh` is a
fake on PATH that records its argv. No network.
"""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
SYNC = SCRIPTS / "harness_sync.py"

RENDER_STUB = """import json, os, sys
log = os.environ.get("FAKE_RENDER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as stream:
        stream.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\\n")
print("Rendered 0 adapter entries.")
"""

CORE_TEST = """import unittest


class CoreTest(unittest.TestCase):
    def test_core(self):
        self.assertTrue(True)
"""

OVERLAY_RED = """import unittest


class OverlayTest(unittest.TestCase):
    def test_overlay_expects_v1(self):
        self.fail("the overlay still expects the v1 core")
"""

FAKE_GH = """import json, os, sys
argv = sys.argv[1:]
body = None
if "--body-file" in argv:
    with open(argv[argv.index("--body-file") + 1], encoding="utf-8") as stream:
        body = stream.read()
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as stream:
    stream.write(json.dumps({"argv": argv, "body": body}) + "\\n")
print(os.environ.get("FAKE_GH_URL", "https://example.invalid/acme/dev-harness/pull/1"))
"""

SKILL_V1 = "line one\nline two\nline three\n"


def subtree_available() -> bool:
    result = subprocess.run(["git", "subtree"], capture_output=True, text=True)
    return "usage: git subtree" in result.stdout + result.stderr


def load_module():
    sys.path.insert(0, str(SCRIPTS))
    try:
        import harness_sync
    finally:
        sys.path.remove(str(SCRIPTS))
    return harness_sync


class HelpersTest(unittest.TestCase):
    def test_repo_slug_from_url_forms(self):
        slug = load_module().repo_slug
        self.assertEqual(slug("git@github.com:acme/dev-harness.git"), "acme/dev-harness")
        self.assertEqual(slug("https://github.com/acme/dev-harness"), "acme/dev-harness")
        self.assertEqual(slug("https://github.com/acme/dev-harness.git/"), "acme/dev-harness")
        self.assertEqual(slug("ssh://git@github.com/acme/dev-harness.git"), "acme/dev-harness")
        self.assertEqual(slug("/srv/git/acme/dev-harness.git"), "acme/dev-harness")

    def test_latest_tag_orders_versions_numerically(self):
        latest = load_module().latest_tag
        self.assertEqual(latest(["v1.2.0", "v1.10.0", "v1.9.3", "nightly"]), "v1.10.0")
        self.assertEqual(latest(["2.0", "v1.0.0"]), "2.0")
        self.assertIsNone(latest(["nightly", "release-candidate"]))
        self.assertIsNone(latest([]))


@unittest.skipUnless(subtree_available(), "git subtree is not installed")
class SyncTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        (self.temp / "tmp").mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        TMPDIR=str(self.temp / "tmp"))
        self.upstream = self.make_upstream()
        self.url = str(self.upstream)
        self.v1 = self.git(self.upstream, "rev-parse", "v1^{commit}")
        self.product = self.make_product()

    # --- fixtures -------------------------------------------------------------------------

    def git(self, cwd, *args, check=True):
        result = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True,
                                text=True)
        if check and result.returncode:
            self.fail(f"git {' '.join(args)} failed: {result.stdout}{result.stderr}")
        return result.stdout.strip()

    def identity(self, repo, name="Core Maintainer", email="core@example.invalid"):
        self.git(repo, "config", "user.name", name)
        self.git(repo, "config", "user.email", email)
        self.git(repo, "config", "commit.gpgsign", "false")

    def write(self, root, files):
        for name, text in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    def make_upstream(self):
        source = self.temp / "core-src"
        source.mkdir()
        self.git(source, "init", "-q", "-b", "main")
        self.identity(source)
        self.write(source, {
            "README.md": "# core v1\n",
            "skills/demo/SKILL.md": SKILL_V1,
            "scripts/render_adapters.py": RENDER_STUB,
            "tests/test_core.py": CORE_TEST,
        })
        self.git(source, "add", ".")
        self.git(source, "commit", "-qm", "core v1")
        self.git(source, "tag", "v1")
        self.write(source, {
            "README.md": "# core v2\n",
            "skills/demo/SKILL.md": "line one\nline two (v2)\nline three\n",
            "roles/new.md": "# new role\n",
        })
        self.git(source, "add", ".")
        self.git(source, "commit", "-qm", "core v2")
        self.git(source, "tag", "-a", "v2", "-m", "core v2")
        bare = self.temp / "acme" / "dev-harness.git"
        bare.parent.mkdir()
        self.git(self.temp, "clone", "-q", "--bare", str(source), str(bare))
        return bare

    def make_product(self):
        product = self.temp / "product"
        product.mkdir()
        self.git(product, "init", "-q", "-b", "main")
        self.identity(product, "Product Dev", "dev@product.invalid")
        self.write(product, {"README.md": "# product\n"})
        self.git(product, "add", ".")
        self.git(product, "commit", "-qm", "init")
        self.origin = self.temp / "product-origin.git"
        self.git(self.temp, "init", "-q", "--bare", str(self.origin))
        self.git(product, "remote", "add", "origin", str(self.origin))
        self.git(product, "push", "-q", "origin", "main")
        return product

    def fake_gh_env(self):
        bin_dir = self.temp / "bin"
        bin_dir.mkdir(exist_ok=True)
        (bin_dir / "gh.py").write_text(FAKE_GH, encoding="utf-8")
        gh = bin_dir / "gh"
        gh.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{bin_dir / "gh.py"}" "$@"\n')
        gh.chmod(0o755)
        self.gh_log = self.temp / "gh.log"
        env = dict(self.env, FAKE_GH_LOG=str(self.gh_log))
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        return env

    def gh_calls(self):
        if not self.gh_log.exists():
            return []
        return [json.loads(line) for line in self.gh_log.read_text().splitlines()]

    # --- helpers --------------------------------------------------------------------------

    def sync(self, *args, env=None):
        return subprocess.run([sys.executable, str(SYNC), *args], cwd=self.product,
                              env=env or self.env, capture_output=True, text=True)

    def ok(self, result, code=0):
        self.assertEqual(result.returncode, code, result.stdout + result.stderr)
        return result.stdout + result.stderr

    def install(self):
        self.ok(self.sync("install", "--repo", self.url, "--ref", "v1"))
        self.git(self.product, "add", "harness/project/project.json")
        self.git(self.product, "commit", "-qm", "pin core v1")

    def config(self):
        return json.loads((self.product / "harness/project/project.json").read_text())

    def commit_core(self, files, message="local core edit"):
        self.write(self.product / "harness/core", files)
        self.git(self.product, "add", "harness/core")
        self.git(self.product, "commit", "-qm", message)

    def head(self):
        return self.git(self.product, "rev-parse", "HEAD")

    def parents(self, rev="HEAD"):
        return self.git(self.product, "rev-list", "--parents", "-n1", rev).split()[1:]

    def worktrees(self):
        return len([line for line in self.git(self.product, "worktree", "list",
                                              "--porcelain").splitlines()
                    if line.startswith("worktree ")])

    def in_merge(self):
        return self.git(self.product, "rev-parse", "-q", "--verify", "MERGE_HEAD",
                        check=False) != ""

    def upstream_branches(self):
        return self.git(self.upstream, "for-each-ref", "--format=%(refname)", "refs/heads")


class InstallTest(SyncTestCase):
    def test_install_adds_the_core_and_pins_it(self):
        output = self.ok(self.sync("install", "--repo", self.url, "--ref", "v1"))
        core = self.product / "harness/core"
        self.assertEqual((core / "README.md").read_text(), "# core v1\n")
        self.assertEqual((core / "skills/demo/SKILL.md").read_text(), SKILL_V1)
        self.assertEqual(self.config(), {"core": {"repo": self.url, "ref": "v1",
                                                  "prefix": "harness/core"}})
        squash = self.git(self.product, "log", "--grep=^git-subtree-dir: harness/core",
                          "--format=%s")
        self.assertTrue(squash.startswith("Squashed 'harness/core/'"), squash)
        self.assertIn("harness/project/project.json", output)
        self.assertIn("status", output)

    def test_install_keeps_overlay_keys_and_honours_the_prefix(self):
        self.write(self.product, {"harness/project/project.json": '{"name": "demo"}\n'})
        self.git(self.product, "add", ".")
        self.git(self.product, "commit", "-qm", "overlay")
        self.ok(self.sync("install", "--repo", self.url, "--ref", "v2",
                          "--prefix", "vendor/core"))
        self.assertEqual((self.product / "vendor/core/README.md").read_text(), "# core v2\n")
        self.assertFalse((self.product / "harness/core").exists())
        self.assertEqual(self.config(), {"name": "demo", "core": {
            "repo": self.url, "ref": "v2", "prefix": "vendor/core"}})

    def test_install_keeps_non_ascii_overlay_text_readable(self):
        self.write(self.product, {"harness/project/project.json":
                                  '{"name": "demo", "note": "проверь журнал"}\n'})
        self.git(self.product, "add", ".")
        self.git(self.product, "commit", "-qm", "overlay")
        self.ok(self.sync("install", "--repo", self.url, "--ref", "v2"))
        text = (self.product / "harness/project/project.json").read_text(encoding="utf-8")
        self.assertIn("проверь журнал", text)
        self.assertNotIn("\\u", text)

    def test_install_refuses_a_dirty_tree(self):
        (self.product / "README.md").write_text("# product, edited\n")
        before = self.head()
        output = self.ok(self.sync("install", "--repo", self.url, "--ref", "v1"), code=2)
        self.assertIn("README.md", output)
        self.assertEqual(self.head(), before)
        self.assertFalse((self.product / "harness/core").exists())
        self.assertFalse((self.product / "harness/project/project.json").exists())


class StatusTest(SyncTestCase):
    def test_clean_core_reports_pin_and_latest_tag(self):
        self.install()
        output = self.ok(self.sync("status"))
        self.assertIn("v1", output)
        self.assertIn("v2", output)
        report = json.loads(self.ok(self.sync("status", "--json")).strip())
        self.assertEqual(report["pinned"], "v1")
        self.assertEqual(report["latest"], "v2")
        self.assertTrue(report["update_available"])
        self.assertEqual(report["local_changes"], [])
        self.assertTrue(report["clean"])
        self.assertEqual(report["prefix"], "harness/core")
        self.assertEqual(self.git(self.product, "for-each-ref", "refs/harness-sync"), "")

    def test_committed_and_uncommitted_core_edits_are_local_changes(self):
        self.install()
        self.commit_core({"skills/demo/SKILL.md": "line one\nline two\nline three (local)\n"})
        output = self.ok(self.sync("status"), code=1)
        self.assertIn("skills/demo/SKILL.md", output)
        self.write(self.product, {"harness/core/extra.md": "scratch\n"})
        result = self.sync("status", "--json")
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["clean"])
        self.assertEqual({(c["path"], c["committed"]) for c in report["local_changes"]},
                         {("skills/demo/SKILL.md", True), ("extra.md", False)})

    def test_strict_fails_naming_propose_and_the_overlay(self):
        self.install()
        self.ok(self.sync("status", "--strict"))
        self.commit_core({"README.md": "# core v1, patched in place\n"})
        result = self.sync("status", "--strict")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("propose", result.stderr)
        self.assertIn("harness/project", result.stderr)
        self.assertIn("README.md", result.stderr)

    def test_missing_pin_is_an_error(self):
        result = self.sync("status")
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("install", result.stderr)


class PullTest(SyncTestCase):
    def test_pull_defaults_to_the_latest_tag_and_runs_the_checks(self):
        self.install()
        before = self.head()
        render_log = self.temp / "render.log"
        output = self.ok(self.sync("pull", env=dict(self.env, FAKE_RENDER_LOG=str(render_log))))
        core = self.product / "harness/core"
        self.assertEqual((core / "README.md").read_text(), "# core v2\n")
        self.assertTrue((core / "roles/new.md").is_file())
        self.assertEqual(self.config()["core"]["ref"], "v2")
        self.assertEqual(len(self.parents()), 2)
        self.assertEqual(self.parents()[0], before)
        calls = [json.loads(line) for line in render_log.read_text().splitlines()]
        # checked in a scratch worktree first, then rendered here after the fast-forward
        self.assertEqual(len(calls), 2, calls)
        self.assertNotEqual(calls[0]["cwd"], str(self.product))
        self.assertEqual(calls[1], {"argv": ["--root", str(self.product)],
                                    "cwd": str(self.product)})
        self.assertEqual(self.worktrees(), 1, "the scratch worktree must be removed")
        self.assertIn("harness/core/tests", output)
        self.assertIn("v2", output)
        # Checks leave nothing behind in the core, so the pin is clean right away.
        self.ok(self.sync("status"))

    def test_an_absolute_overlay_path_is_checked_in_the_scratch_copy(self):
        self.write(self.product, {"harness/project/tests/test_overlay.py": OVERLAY_RED})
        self.git(self.product, "add", ".")
        self.git(self.product, "commit", "-qm", "overlay tests")
        self.install()
        before = self.head()
        env = dict(self.env, HARNESS_PROJECT=str(self.product / "harness/project"))
        self.ok(self.sync("pull", "v2", env=env), code=1)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.config()["core"]["ref"], "v1", "the scratch run moved the real pin")

    def test_pull_of_the_pinned_ref_is_a_no_op(self):
        self.install()
        before = self.head()
        output = self.ok(self.sync("pull", "v1"))
        self.assertEqual(self.head(), before)
        self.assertIn("already", output.lower())

    def test_pull_refuses_a_dirty_tree(self):
        self.install()
        before = self.head()
        (self.product / "README.md").write_text("# product, edited\n")
        output = self.ok(self.sync("pull", "v2"), code=2)
        self.assertIn("README.md", output)
        self.assertEqual(self.head(), before)
        self.assertEqual(self.config()["core"]["ref"], "v1")

    def test_conflict_leaves_the_merge_for_the_user(self):
        self.install()
        self.commit_core({"skills/demo/SKILL.md": "line one\nline two (local)\nline three\n"})
        before = self.head()
        output = self.ok(self.sync("pull", "v2"), code=1)
        self.assertTrue(self.in_merge(), "the conflicted merge must be left in place")
        self.assertEqual(self.head(), before)
        self.assertIn("<<<<<<<",
                      (self.product / "harness/core/skills/demo/SKILL.md").read_text())
        self.assertIn("harness/core/skills/demo/SKILL.md", output)
        self.assertIn("git merge --abort", output)
        self.assertEqual(self.config()["core"]["ref"], "v1")

    def test_red_checks_leave_the_branch_untouched(self):
        self.write(self.product, {"harness/project/tests/test_overlay.py": OVERLAY_RED})
        self.git(self.product, "add", ".")
        self.git(self.product, "commit", "-qm", "overlay tests")
        self.install()
        before = self.head()
        output = self.ok(self.sync("pull", "v2"), code=1)
        self.assertFalse(self.in_merge())
        self.assertEqual(self.head(), before, "a red update must not reach the branch")
        self.assertEqual((self.product / "harness/core/README.md").read_text(), "# core v1\n")
        self.assertEqual(self.config()["core"]["ref"], "v1")
        self.assertEqual(self.worktrees(), 1, "the scratch worktree must be removed")
        self.assertIn("harness/project/tests", output)
        self.assertIn("nothing changed", output.lower())

    def edit_core(self):
        self.install()
        self.commit_core({
            "skills/demo/SKILL.md": "line one\nline two\nline three (fixed)\n",
            "scripts/new_tool.py": "print('tool')\n",
        }, message="fix: demo skill line three")

    def printed(self, output, prefix):
        lines = [line.strip() for line in output.splitlines() if line.strip().startswith(prefix)]
        self.assertEqual(len(lines), 1, output)
        return lines[0]

    def assert_branch(self, branch, title):
        self.assertEqual(self.git(self.upstream, "rev-parse", f"{branch}^"), self.v1)
        self.assertEqual(self.git(self.upstream, "show", f"{branch}:skills/demo/SKILL.md"),
                         "line one\nline two\nline three (fixed)")
        self.assertEqual(self.git(self.upstream, "show", f"{branch}:scripts/new_tool.py"),
                         "print('tool')")
        self.assertEqual(self.git(self.upstream, "show", f"{branch}:README.md"), "# core v1")
        self.assertEqual(self.git(self.upstream, "log", "-1", "--format=%s", branch), title)
        self.assertEqual(self.git(self.upstream, "log", "-1", "--format=%ae", branch),
                         "dev@product.invalid")

    def test_without_yes_prepares_the_branch_and_prints_the_exact_commands(self):
        self.edit_core()
        origin_before = self.git(self.product, "ls-remote", "origin")
        head = self.head()
        output = self.ok(self.sync("propose", "--branch", "fix-demo", "--title", "Fix demo skill"))
        self.assertEqual(self.upstream_branches(), "refs/heads/main")
        push = self.printed(output, "git -C ")
        pr = self.printed(output, "gh pr create")
        argv = shlex.split(pr)
        self.assertEqual(argv[:12], ["gh", "pr", "create", "--repo", "acme/dev-harness",
                                     "--base", "main", "--head", "fix-demo",
                                     "--title", "Fix demo skill", "--body-file"])
        env = self.fake_gh_env()
        for line in (push, pr):
            result = subprocess.run(line, shell=True, cwd=self.temp, env=env,
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assert_branch("fix-demo", "Fix demo skill")
        calls = self.gh_calls()
        self.assertEqual(len(calls), 1)
        self.assertIn("fix: demo skill line three", calls[0]["body"])
        self.assertEqual(self.git(self.product, "ls-remote", "origin"), origin_before)
        self.assertEqual(self.head(), head)
        self.assertEqual(self.git(self.product, "status", "--porcelain"), "")

    def test_yes_pushes_the_core_branch_and_opens_the_pr(self):
        self.edit_core()
        origin_before = self.git(self.product, "ls-remote", "origin")
        env = self.fake_gh_env()
        self.ok(self.sync("propose", "--branch", "fix-demo", "--title", "Fix demo skill",
                          "--yes", env=env))
        self.assert_branch("fix-demo", "Fix demo skill")
        calls = self.gh_calls()
        self.assertEqual(len(calls), 1)
        argv = calls[0]["argv"]
        self.assertEqual(argv[:11], ["pr", "create", "--repo", "acme/dev-harness",
                                     "--base", "main", "--head", "fix-demo",
                                     "--title", "Fix demo skill", "--body-file"])
        self.assertEqual(len(argv), 12)
        self.assertIn("v1", calls[0]["body"])
        self.assertIn("fix: demo skill line three", calls[0]["body"])
        self.assertIn("skills/demo/SKILL.md", calls[0]["body"])
        self.assertEqual(self.git(self.product, "ls-remote", "origin"), origin_before)
        self.assertEqual(list((self.temp / "tmp").iterdir()), [],
                         "a published proposal leaves no work directory behind")

    def test_feedback_attachment_links_draft_and_scrubs_pr_body(self):
        self.edit_core()
        config = self.config()
        config["name"] = "Private Product"
        config["feedback"] = {"redact": ["Customer Jones"]}
        (self.product / "harness/project/project.json").write_text(json.dumps(config))
        draft = self.product / ".harness/feedback/demo.md"
        draft.parent.mkdir(parents=True)
        draft.write_text('<!-- harness-feedback {"kind":"bug","status":"draft","offered":true} -->\n\n'
                         '# Fix demo skill\n\n## What happened\nPrivate Product Customer Jones user@example.invalid\n')
        output = self.ok(self.sync("propose", "--branch", "fix-demo", "--title", "Private Product fix",
                                   "--feedback-file", str(draft)))
        argv = shlex.split(self.printed(output, "gh pr create"))
        body = Path(argv[argv.index("--body-file") + 1]).read_text()
        self.assertIn("[Reviewed feedback draft](#harness-feedback-draft)", body)
        self.assertIn("## Harness feedback draft", body)
        for private in ("Private Product", "Customer Jones", "user@example.invalid", str(draft)):
            self.assertNotIn(private, body)
        self.assertNotIn("Private Product", argv[argv.index("--title") + 1])
        self.assertEqual(self.upstream_branches(), "refs/heads/main")

    def test_feedback_publication_records_the_pr_in_the_queue(self):
        self.edit_core()
        draft = self.product / ".harness/feedback/demo.md"
        draft.parent.mkdir(parents=True)
        draft.write_text('<!-- harness-feedback {"kind":"bug","status":"draft","offered":true} -->\n\n'
                         '# Fix demo skill\n\n## What happened\nSynthetic harness behavior.\n')
        env = self.fake_gh_env()
        env["FAKE_GH_URL"] = "https://github.com/pharosone/dev-harness/pull/123"
        self.ok(self.sync("propose", "--branch", "fix-demo", "--feedback-file", str(draft),
                          "--yes", env=env))
        marker = draft.read_text().splitlines()[0]
        meta = json.loads(marker.removeprefix("<!-- harness-feedback ").removesuffix(" -->"))
        self.assertEqual(meta["status"], "sent")
        self.assertEqual(meta["url"], env["FAKE_GH_URL"])
        self.assertTrue(meta["offered"])
        self.assertIn("## What happened", draft.read_text())
        self.assertEqual(len(self.gh_calls()), 1)

    def test_feedback_pr_scrubs_token_part_names_in_title_commits_and_draft(self):
        self.edit_core()
        config = self.config()
        config['name'] = 'Acme'
        (self.product / 'harness/project/project.json').write_text(json.dumps(config))
        self.commit_core({'README.md': '# core v1\n\nSynthetic fix\n'},
                         message='ACME-123: fix acme_api worker/acme123')
        draft = self.product / '.harness/feedback/demo.md'
        draft.parent.mkdir(parents=True)
        draft.write_text('<!-- harness-feedback {"kind":"bug","status":"draft"} -->\n\n'
                         '# Fix acme-billing guard\n\nacme.worker dev-harness feedback.py\n')
        output = self.ok(self.sync('propose', '--branch', 'fix-demo', '--feedback-file', str(draft),
                                   env=self.fake_gh_env()))
        argv = shlex.split(self.printed(output, 'gh pr create'))
        body = Path(argv[argv.index('--body-file') + 1]).read_text()
        self.assertNotIn('acme', body.lower())
        self.assertNotIn('acme', argv[argv.index('--title') + 1].lower())
        self.assertIn('dev-harness feedback.py', body)
        self.assertEqual(self.upstream_branches(), 'refs/heads/main')
        self.assertEqual(self.gh_calls(), [])

    def test_feedback_attachment_refuses_declined_or_sent_before_publication(self):
        self.edit_core()
        draft = self.product / '.harness/feedback/demo.md'
        draft.parent.mkdir(parents=True)
        for status in ('declined', 'sent'):
            with self.subTest(status=status):
                text = '<!-- harness-feedback ' + json.dumps({'kind': 'bug', 'status': status}) + ' -->\n\n# Fix demo skill\n'
                draft.write_text(text)
                output = self.ok(self.sync('propose', '--branch', 'fix-demo',
                                          '--feedback-file', str(draft), '--yes',
                                          env=self.fake_gh_env()), code=2)
                self.assertIn('draft status', output)
                self.assertNotIn('Traceback', output)
                self.assertEqual(draft.read_text(), text)
                self.assertEqual(self.upstream_branches(), 'refs/heads/main')
                self.assertEqual(self.gh_calls(), [])

    def test_feedback_attachment_errors_are_generic_refusals(self):
        self.edit_core()
        draft = self.product / '.harness/feedback/private-name.md'
        for mode in ('missing', 'invalid-overlay'):
            with self.subTest(mode=mode):
                if mode == 'invalid-overlay':
                    draft.parent.mkdir(parents=True)
                    draft.write_text('<!-- harness-feedback {"kind":"bug","status":"draft"} -->\n\n# Fix demo\n')
                    (self.product / 'harness/project/project.json').write_text('{invalid')
                output = self.ok(self.sync('propose', '--branch', 'fix-demo',
                                          '--feedback-file', str(draft)), code=2)
                self.assertNotIn('Traceback', output)
                self.assertNotIn(str(self.product), output)
                self.assertEqual(self.upstream_branches(), 'refs/heads/main')

    def test_default_title_is_the_latest_core_commit_subject(self):
        self.edit_core()
        output = self.ok(self.sync("propose", "--branch", "fix-demo"))
        argv = shlex.split(self.printed(output, "gh pr create"))
        self.assertEqual(argv[argv.index("--title") + 1], "fix: demo skill line three")

    def test_nothing_to_propose(self):
        self.install()
        output = self.ok(self.sync("propose", "--branch", "fix-demo", "--yes",
                                   env=self.fake_gh_env()))
        self.assertIn("nothing to propose", output.lower())
        self.assertEqual(self.upstream_branches(), "refs/heads/main")
        self.assertEqual(self.gh_calls(), [])

    def test_uncommitted_core_changes_are_refused(self):
        self.edit_core()
        self.write(self.product, {"harness/core/extra.md": "scratch\n"})
        output = self.ok(self.sync("propose", "--branch", "fix-demo", "--yes",
                                   env=self.fake_gh_env()), code=2)
        self.assertIn("extra.md", output)
        self.assertEqual(self.upstream_branches(), "refs/heads/main")
        self.assertEqual(self.gh_calls(), [])


if __name__ == "__main__":
    unittest.main()
