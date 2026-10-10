"""Public feedback uses synthetic checkouts and a fake gh; never creates real issues."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
import feedback

FAKE_GH = '''import json, os, sys
args = sys.argv[1:]
record = {"args": args}
if "--body-file" in args:
    record["body"] = open(args[args.index("--body-file") + 1], encoding="utf-8").read()
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(record) + "\\n")
if args[:2] == ["auth", "status"]:
    sys.exit(int(os.environ.get("FAKE_GH_AUTH", "0")))
if args[:2] == ["issue", "list"]:
    print(os.environ.get("FAKE_GH_DUPLICATES", "[]"))
    sys.exit(int(os.environ.get("FAKE_GH_LIST_EXIT", "0")))
if args[:2] == ["issue", "create"]:
    print("https://github.com/pharosone/dev-harness/issues/123")
    sys.exit(int(os.environ.get("FAKE_GH_CREATE_EXIT", "0")))
'''


class FeedbackTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "private-product"
        self.root.mkdir()
        self.git("init", "-q")
        self.overlay = self.root / "harness/project"
        self.overlay.mkdir(parents=True)
        self.config = {"name": "Secret Product", "core": {"repo": "https://github.com/pharosone/dev-harness.git",
                       "ref": "v1.3.0", "prefix": "harness/core"},
                       "feedback": {"redact": ["Customer Jones", "Private Person"]}}
        self.save_config()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fake = self.bin / "gh"
        self.fake.write_text(f"#!{sys.executable}\n" + FAKE_GH)
        self.fake.chmod(0o755)
        self.log = self.root / "gh-log.jsonl"
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_") and key not in ("HARNESS_PROJECT",)}
        self.env.update(PATH=str(self.bin) + os.pathsep + os.environ["PATH"],
                        FAKE_GH_LOG=str(self.log), PYTHONDONTWRITEBYTECODE="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        self.patch_env = patch.dict(os.environ, self.env, clear=True)
        self.patch_env.start()
        self.addCleanup(self.patch_env.stop)

    def save_config(self):
        (self.overlay / "project.json").write_text(json.dumps(self.config))

    def git(self, *args):
        result = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def cli(self, *args, code=0):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            actual = feedback.main(list(args))
        self.assertEqual(actual, code, out.getvalue() + err.getvalue())
        return out.getvalue() + err.getvalue()

    def draft(self, title="Freeze refuses authorized amendments", **options):
        args = ["draft", "--kind", "friction", "--title", title]
        for key, value in options.items():
            args += ["--" + key.replace("_", "-"), str(value)]
        return Path(self.cli(*args).strip())

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []

    def sent(self):
        return [call for call in self.calls() if call["args"][:2] == ["issue", "create"]]

    def clean_vendored_core(self):
        core = self.root / "harness/core"
        core.mkdir(parents=True)
        (core / "README.md").write_text("# synthetic core\n")
        self.git("add", "harness/core/README.md")
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                 "commit", "-qm", "synthetic core")
        tree = self.git("rev-parse", "HEAD:harness/core")
        result = subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                                 "-c", "commit.gpgsign=false", "commit-tree", tree], cwd=self.root,
                                input="Squashed 'harness/core/' synthetic\n\ngit-subtree-dir: harness/core\n",
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        squash = result.stdout.strip()
        # The squash appears in HEAD ancestry, like a real subtree install.
        self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                 "merge", "--allow-unrelated-histories", "-s", "ours", "--no-ff", squash, "-m", "synthetic sync")
        return core

    def test_draft_template_list_and_no_overwrite(self):
        path = self.draft()
        meta, body = feedback.read(path)
        self.assertEqual(meta, {"kind": "friction", "status": "draft", "offered": False})
        self.assertEqual(path.parent, self.root / ".harness/feedback")
        for heading in feedback.HEADINGS:
            self.assertIn("## " + heading, body)
        self.assertIn("v1.3.0", body)
        self.assertIn("draft", self.cli("list"))
        original = path.read_text()
        self.assertEqual(self.draft(), path)
        self.assertEqual(path.read_text(), original)

    def test_from_file_sanitizes_title_and_body_and_keeps_complete_template(self):
        source = self.root / "source.md"
        source.write_text("\n\n".join("## " + h + "\nSecret Product Customer Jones" for h in feedback.HEADINGS))
        path = self.draft(title="Secret Product fails", from_file=source)
        self.assertNotIn("Secret Product", path.read_text())
        self.assertNotIn("Customer Jones", path.read_text())
        self.assertEqual(feedback.read(path)[1].count("## Evidence"), 1)

    def test_run_dir_and_task_use_durable_queue(self):
        run = self.overlay / "runs/one"
        run.mkdir(parents=True)
        self.assertEqual(self.draft(run_dir=run).parent, run / "feedback")
        self.assertEqual(self.draft(task="two").parent, self.root / ".harness/tasks/two/feedback")
        self.cli("draft", "--kind", "bug", "--title", "bad", "--task", "one", "--run-dir", str(run), code=2)

    def test_overlay_environment_resolves_redaction(self):
        custom = self.root / "custom"
        custom.mkdir()
        (custom / "project.json").write_text(json.dumps({"feedback": {"redact": ["Special Client"]}}))
        with patch.dict(os.environ, HARNESS_PROJECT="custom"):
            self.assertNotIn("Special Client", feedback.sanitize("Special Client", self.root))

    def test_offers_once_and_decline_stays_declined_on_repeat(self):
        path = self.draft()
        output = self.cli("offer", path.stem)
        self.assertEqual(output.strip(), 'Offer harness feedback “Freeze refuses authorized amendments” for your review?')
        self.assertEqual(self.cli("offer", path.stem), "")
        self.cli("decline", path.stem)
        self.draft()
        self.assertIn("declined", self.cli("list"))
        self.assertEqual(self.cli("list", "--offerable"), "")
        self.assertEqual(self.cli("list", "--summary").strip(), "0 harness feedback drafts waiting: none")
        self.cli("send", path.stem, "--yes")
        self.assertFalse(self.calls())

    def test_summary_includes_offered_unsent_and_excludes_sent(self):
        path = self.draft()
        self.cli("offer", path.stem)
        self.assertIn("1 harness feedback drafts waiting", self.cli("list", "--summary"))
        meta, body = feedback.read(path)
        meta.update(status="sent", url="https://github.com/pharosone/dev-harness/issues/1")
        feedback.write(path, meta, body)
        self.assertIn("sent: https://github.com/pharosone/dev-harness/issues/1", self.cli("list"))
        self.assertIn("0 harness feedback", self.cli("list", "--summary"))

    def test_send_previews_sanitized_body_and_records_url(self):
        self.clean_vendored_core()
        path = self.draft()
        meta, body = feedback.read(path)
        body += "\nSecret Product customer jones user@example.invalid\n"
        feedback.write(path, meta, body)
        output = self.cli("send", path.stem, "--yes")
        self.assertEqual(len(self.sent()), 1)
        self.assertIn(self.sent()[0]["body"], output)
        self.assertNotIn("Secret Product", self.sent()[0]["body"])
        self.assertNotIn("Customer Jones", self.sent()[0]["body"])
        self.assertIn("-R", self.sent()[0]["args"])
        self.assertIn("pharosone/dev-harness", self.sent()[0]["args"])
        meta, saved = feedback.read(path)
        self.assertEqual(meta["status"], "sent")
        self.assertEqual(meta["url"], "https://github.com/pharosone/dev-harness/issues/123")
        self.assertEqual(saved, self.sent()[0]["body"])
        self.cli("send", path.stem, "--yes")
        self.assertEqual(len(self.sent()), 1)

    def test_noninteractive_send_without_yes_does_not_create(self):
        self.clean_vendored_core()
        path = self.draft()
        with patch.object(sys.stdin, "isatty", return_value=False):
            self.assertIn("Not sent", self.cli("send", path.stem))
        self.assertFalse(self.sent())

    def test_interactive_send_approval(self):
        self.clean_vendored_core()
        path = self.draft()
        with patch.object(sys.stdin, "isatty", return_value=True), patch("builtins.input", return_value="yes"):
            self.cli("send", path.stem)
        self.assertEqual(len(self.sent()), 1)

    def test_duplicate_no_create_and_draft_retained(self):
        path = self.draft()
        duplicate = [{"title": "Existing freeze problem", "url": "https://github.com/pharosone/dev-harness/issues/9"}]
        with patch.dict(os.environ, FAKE_GH_DUPLICATES=json.dumps(duplicate)):
            self.assertIn("Possible duplicates", self.cli("send", path.stem, "--yes"))
        self.assertFalse(self.sent())
        self.assertEqual(feedback.read(path)[0]["status"], "draft")
        self.assertIn("--search", self.calls()[-1]["args"])

    def test_duplicate_failure_refuses(self):
        path = self.draft()
        with patch.dict(os.environ, FAKE_GH_LIST_EXIT="1"):
            self.cli("send", path.stem, "--yes", code=2)
        self.assertFalse(self.sent())

    def test_no_gh_or_auth_url_does_not_mark_sent(self):
        path = self.draft()
        for mode in ("missing", "unauthorized"):
            with self.subTest(mode=mode):
                with contextlib.ExitStack() as stack:
                    if mode == "missing":
                        stack.enter_context(patch.object(feedback.shutil, "which", return_value=None))
                    else:
                        stack.enter_context(patch.dict(os.environ, FAKE_GH_AUTH="1"))
                    output = self.cli("send", path.stem, "--yes")
                url = output.splitlines()[-1]
                parsed = urlsplit(url)
                self.assertEqual(parsed.path, "/pharosone/dev-harness/issues/new")
                query = parse_qs(parsed.query)
                self.assertEqual(query["title"], ["Freeze refuses authorized amendments"])
                self.assertEqual(query["body"], [feedback.read(path)[1]])
                self.assertEqual(feedback.read(path)[0]["status"], "draft")
        self.assertFalse(self.sent())

    def test_core_edits_offer_propose_and_issue_requires_choice(self):
        core = self.clean_vendored_core()
        path = self.draft()
        for committed in (False, True):
            with self.subTest(committed=committed):
                (core / "README.md").write_text("# changed core\n")
                if committed:
                    self.git("add", "harness/core/README.md")
                    self.git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "commit", "-qm", "core fix")
                output = self.cli("send", path.stem, "--yes")
                self.assertIn("harness_sync.py propose", output)
                self.assertIn("--feedback-file", output)
                self.assertIn(str(path), output)
                self.assertFalse(self.sent())
        self.cli("send", path.stem, "--yes", "--issue")
        self.assertEqual(len(self.sent()), 1)

    def test_wrong_pin_refuses_even_browser_fallback(self):
        path = self.draft()
        self.config["core"]["repo"] = "https://github.com/private/product.git"
        self.save_config()
        with patch.object(feedback.shutil, "which", return_value=None):
            self.cli("send", path.stem, "--yes", code=2)
        self.assertFalse(self.calls())

    def test_secret_remaining_refuses_before_gh_or_preview(self):
        path = self.draft()
        meta, body = feedback.read(path)
        sensitive = "sk-" + "shortbutsecret"
        feedback.write(path, meta, body + "\n" + sensitive)
        output = self.cli("send", path.stem, "--yes", code=2)
        self.assertNotIn(sensitive, output)
        self.assertFalse(self.calls())

    def test_failed_create_retains_draft(self):
        self.clean_vendored_core()
        path = self.draft()
        with patch.dict(os.environ, FAKE_GH_CREATE_EXIT="1"):
            self.cli("send", path.stem, "--yes", code=2)
        self.assertEqual(feedback.read(path)[0]["status"], "draft")

    def test_absolute_paths_names_emails_ips_and_extra_terms(self):
        raw = f'{SCRIPTS}/task_state.py /Users/alice/product/src/private.py "{self.root}/a b.py" C:\\Users\\Alice\\client.py Secret Product private-product Customer Jones Private Person user@acme.invalid 10.2.3.4 [2001:db8::1] ::1 ::ffff:192.0.2.1'
        clean = feedback.sanitize(raw, self.root)
        self.assertIn("<harness>/scripts/task_state.py", clean)
        self.assertIn("<product>/…", clean)
        for private in ("alice", "Alice", "client.py", "a b.py", "Secret Product", "private-product", "Customer Jones", "Private Person", "user@acme.invalid", "10.2.3.4", "2001:db8::1", "::1", "192.0.2.1"):
            self.assertNotIn(private, clean)
        self.assertEqual(clean.count("[ip]"), 4)

    def test_public_url_allowlist_and_query_scrubbing(self):
        raw = 'https://github.com/pharosone/dev-harness/issues/1?customer=secret https://docs.python.org/3/library/re.html#secret https://product.invalid/private https://github.com/pharosone/dev-harness-private/issues/2 https://docs.python.org.evil.invalid/private https://person:password@docs.python.org/private http://docs.python.org/private'
        clean = feedback.sanitize(raw, self.root)
        self.assertIn("https://github.com/pharosone/dev-harness/issues/1", clean)
        self.assertIn("https://docs.python.org/3/library/re.html", clean)
        for private in ("secret", "product.invalid", "harness-private", "evil.invalid", "person", "password", "http://"):
            self.assertNotIn(private, clean)
        self.assertEqual(clean.count("[url]"), 5)

    def test_allowed_documentation_url_does_not_bypass_email_or_ip_redaction(self):
        raw = "https://docs.python.org/user@example.invalid/10.0.0.1 https://docs.github.com/[2001:db8::1]"
        clean = feedback.sanitize(raw, self.root)
        for private in ("user@example.invalid", "10.0.0.1", "2001:db8::1"):
            self.assertNotIn(private, clean)

    def test_windows_unc_absolute_paths_are_scrubbed(self):
        raw = r'\\private-host\customer\report.md "\\private-host\customer\private report.md"'
        clean = feedback.sanitize(raw, self.root)
        for private in ("private-host", "customer", "report.md"):
            self.assertNotIn(private, clean)
        self.assertEqual(clean.count("<product>/…"), 2)

    def test_secret_patterns_are_scrubbed(self):
        secrets = ["sk-" + "s" * 40, "ghp_" + "a" * 36, "AKIA" + "A" * 16,
                   "github_pat_" + "a" * 30, "xoxb-" + "a" * 12, "AIza" + "a" * 35,
                   "eyJhbGciOiJub25lIn0" + ".eyJ1c2VyIjoiYSJ9.signature"]
        raw = " ".join(secrets) + '\nAPI_KEY="private-value"\nAuthorization: Bearer abcde\npassword: abcdef\n'
        clean = feedback.sanitize(raw, self.root)
        for secret in [*secrets, "private-value", "abcde", "abcdef"]:
            self.assertNotIn(secret, clean)
        self.assertIn("[redacted]", clean)

    def test_private_key_block_removed_and_unclosed_key_refused(self):
        header = "-----BEGIN " + "PRIVATE KEY-----"
        tail = "-----END " + "PRIVATE KEY-----"
        self.assertEqual(feedback.sanitize(header + "\n" + "A" * 45 + "\n" + tail, self.root), "[redacted]")
        with self.assertRaises(feedback.FeedbackError):
            feedback.sanitize(header + "\nshort-body", self.root)
        with self.assertRaises(feedback.FeedbackError):
            feedback.sanitize(header + "\n" + "A" * 45, self.root)
        with self.assertRaises(feedback.FeedbackError):
            feedback.sanitize("api_key =\nsecret on another line", self.root)

    def test_invalid_config_and_slug_refuse(self):
        self.cli("send", "../../private", code=2)
        for invalid in ({"redact": "client"}, {"redact": [""]}, {"unknown": []}):
            self.config["feedback"] = invalid
            self.save_config()
            self.cli("draft", "--kind", "bug", "--title", "test", code=2)

    def test_paths_after_redirects_and_inside_tags_preserve_placeholders(self):
        raw = (f'<code>/Users/alice/acme-client/report.md</code> '
               f'python3 x.py 2>/Users/alice/acme-client/err.log '
               f'<code>{SCRIPTS}/feedback.py</code> '
               '<harness>/scripts/freeze.py <product>/…')
        clean = feedback.sanitize(raw, self.root)
        self.assertEqual(clean, '<code><product>/…</code> python3 x.py 2><product>/… '
                         '<code><harness>/scripts/feedback.py</code> '
                         '<harness>/scripts/freeze.py <product>/…')
        self.assertEqual(feedback.sanitize(clean, self.root), clean)

    def test_issue_choice_needs_no_subtree_history(self):
        core = self.root / 'harness/core'
        core.mkdir(parents=True)
        (core / 'README.md').write_text('synthetic copied core')
        self.git('add', 'harness/core')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'copied core without subtree')
        path = self.draft()
        with patch.object(feedback, 'core_edits', wraps=feedback.core_edits) as inspect:
            self.cli('send', path.stem, '--yes', '--issue')
            inspect.assert_not_called()
        self.assertEqual(len(self.sent()), 1)

    def test_checkout_names_match_words_and_keep_harness_terms(self):
        for name, raw in [('app', 'app application happy /relative/app/file'),
                          ('lanes', 'lanes.py lanes Freeze refuses authorized amendments'),
                          ('freeze', 'Freeze refuses authorized amendments lanes.py freeze')]:
            with self.subTest(name=name):
                with patch.object(feedback.task_state, 'checkouts', return_value=(Path('/synthetic') / name, None)):
                    clean = feedback.sanitize(raw, self.root)
                if name == 'app':
                    self.assertEqual(clean, '[private] application happy <product>/…')
                else:
                    self.assertEqual(clean, raw)
        self.config['name'] = 'app'
        self.save_config()
        self.assertEqual(feedback.sanitize('app application happy', self.root),
                         '[private] application happy')
        for name in (*feedback.UPSTREAM.split('/'), 'freeze', 'lanes'):
            with self.subTest(harness_name=name):
                self.config['name'] = name
                self.save_config()
                raw = 'dev-harness Freeze lanes.py https://github.com/pharosone/dev-harness/issues/1'
                self.assertEqual(feedback.sanitize(raw, self.root), raw)

    def test_duplicate_confirmation_shows_links_and_still_requires_approval(self):
        path = self.draft()
        link = 'https://github.com/pharosone/dev-harness/issues/9'
        duplicates = [{'title': 'Existing freeze problem', 'url': link}]
        with patch.dict(os.environ, FAKE_GH_DUPLICATES=json.dumps(duplicates)):
            first = self.cli('send', path.stem, '--yes', '--issue')
            self.assertIn(link, first)
            self.assertIn('--not-a-duplicate', first)
            self.assertFalse(self.sent())
            with patch.object(sys.stdin, 'isatty', return_value=False):
                output = self.cli('send', path.stem, '--issue', '--not-a-duplicate')
            self.assertIn(link, output)
            self.assertFalse(self.sent())
            output = self.cli('send', path.stem, '--yes', '--issue', '--not-a-duplicate')
        self.assertIn(link, output)
        self.assertEqual(len(self.sent()), 1)

    def test_secret_prose_can_be_drafted_and_declined(self):
        for title in ('Guard misses private key files', 'Guard misses PRIVATE KEY files',
                      'SK-123 bug report', 'sk-learn compatibility'):
            with self.subTest(title=title):
                path = self.draft(title=title)
                self.assertEqual(feedback.title_of(feedback.read(path)[1]), title)
                self.cli('decline', path.stem)
                self.assertEqual(feedback.read(path)[0]['status'], 'declined')

    def test_url_replacement_preserves_markdown_and_sentence_punctuation(self):
        raw = '[docs](https://docs.python.org/3/). (https://private.invalid/path),'
        self.assertEqual(feedback.sanitize(raw, self.root),
                         '[docs](https://docs.python.org/3/). ([url]),')

    def test_unicode_and_truncated_titles_have_stable_distinct_slugs(self):
        titles = ['Ошибка во freeze', 'Ошибка в гейте', 'Ошибка в очереди', 'x' * 100 + ' first',
                  'x' * 100 + ' second']
        paths = [self.draft(title=title) for title in titles]
        self.assertEqual(len(set(paths)), len(titles))
        for title, path in zip(titles, paths):
            self.assertLessEqual(len(path.stem), 90)
            self.assertEqual(self.draft(title=title), path)
            self.cli('decline', path.stem)
            self.assertEqual(self.draft(title=title), path)
            self.assertEqual(feedback.read(path)[0]['status'], 'declined')

    def test_duplicate_public_titles_do_not_fail_closed(self):
        path = self.draft()
        link = 'https://github.com/pharosone/dev-harness/issues/9'
        duplicates = [{'title': 'Guard misses private key headers sk-learn', 'url': link}]
        with patch.dict(os.environ, FAKE_GH_DUPLICATES=json.dumps(duplicates)):
            output = self.cli('send', path.stem, '--yes')
        self.assertIn('Guard misses private key headers sk-learn', output)
        self.assertIn(link, output)
        self.assertFalse(self.sent())

    def test_all_six_seed_examples_create_sanitized_drafts(self):
        fixtures = json.loads((Path(__file__).parent / "fixtures/feedback/seeds.json").read_text())
        self.assertEqual(len(fixtures), 6)
        for seed in fixtures:
            body = f'# {seed["title"]}\n\n## What happened\n{seed["happened"]}\n\n## Expected\n{seed["expected"]}\n\n## Repro (minimal, harness-only)\n{seed["repro"]}\n\n## Evidence\n- Harness version: owner report, 2026-10-10; exact version unknown\n- Script: {seed["script"]}\n- Exit code: unknown\n- Sanitized output: {seed["output"]}\n\n## Proposal\n{seed["proposal"]}\n'
            source = self.root / "seed.md"
            source.write_text(body)
            path = Path(self.cli("draft", "--kind", seed["kind"], "--title", seed["title"], "--from-file", str(source)).strip())
            self.assertEqual(feedback.read(path)[1], body)
        self.assertEqual(len(feedback.entries(self.root / ".harness/feedback")), 6)
        self.assertFalse(self.calls())


if __name__ == "__main__":
    unittest.main()
