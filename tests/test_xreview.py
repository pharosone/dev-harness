"""Run the cross-vendor review runner against a temp repo and fake vendor CLIs.

The harness core (script, project.py, prompt, models.json, optional tier.py) is copied
outside the reviewed repo, as when an orchestrator reviews another lane's checkout. The
reviewed repo's overlay (harness/project) may override models.json. Fake `codex` and
`claude` executables come first on PATH; the real CLIs are never called.
"""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HARNESS = Path(__file__).resolve().parents[1]

FAKE_CLI = """#!{python}
import json, os, sys, time
argv = [os.path.basename(sys.argv[0]), *sys.argv[1:]]
stdin = sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": argv, "stdin": stdin, "cwd": os.getcwd(),
                          "effort": os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")}}) + "\\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
report = os.environ.get("FAKE_REPORT", "")
if os.environ.get("FAKE_SKIP_WRITE") != "1":
    if argv[0] == "codex":
        with open(argv[argv.index("--output-last-message") + 1], "w", encoding="utf-8") as out:
            out.write(report)
    else:
        sys.stdout.write(report)
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""

FAKE_TIER = """import json, os, sys
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": ["tier.py", *sys.argv[1:]], "cwd": os.getcwd()}}) + "\\n")
print({output!r})
"""

MODELS = {
    "claude": {
        "implementation": {"model": "claude-impl", "effort": "medium"},
        "critical-review": {"model": "claude-crit", "effort": "high"},
    },
    "codex": {
        "implementation": {"model": "codex-impl", "effort": "high"},
        "critical-review": {"model": "codex-crit", "effort": "medium"},
    },
}

PASS = "- [Minor] src/app.py:2 — unclear name — add(1, 1) → reader confusion\n\nVERDICT: pass\n"
BLOCK = "- [Major] src/app.py:2 — off by one — add(2, 2) → 5\n\nVERDICT: block\n"

SPEC = """# Spec

Intro text.

## Design

Design intro.

### Tiers (`tiers.json`)

Tier body.

#### Tier details

Detail body.

### Cross-vendor review

Xreview body.

## Units

```md
## Not a heading
```

Units body.

# Appendix

Appendix body.
"""

# Assembled at run time so secret scanners do not read the tests' synthetic keys as leaks.
PEM_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"


def claude_argv(model):
    return ["claude", "-p", "--model", model, "--output-format", "text", "--permission-mode", "plan",
            "--allowedTools", "Read,Grep,Glob", "--disallowedTools", "Bash,Edit,Write,NotebookEdit"]


def section(text, title):
    return text.split(f"## {title}\n", 1)[1].split("\n## ", 1)[0]


class XReviewTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo = self.temp / "repo"
        self.harness = self.temp / "harness"
        self.bin = self.temp / "bin"
        self.log = self.temp / "calls.jsonl"
        self.report = self.temp / "out" / "A1-xreview.md"
        for directory in (self.repo / "src", self.bin, self.harness / "scripts",
                          self.harness / "prompts", self.harness / "adapters"):
            directory.mkdir(parents=True)
        for name in ("xreview.py", "project.py"):
            shutil.copy(HARNESS / "scripts" / name, self.harness / "scripts" / name)
        shutil.copy(HARNESS / "prompts/xreview.md", self.harness / "prompts/xreview.md")
        self.models(MODELS)
        for name in ("codex", "claude"):
            fake = self.bin / name
            fake.write_text(FAKE_CLI.format(python=sys.executable))
            fake.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "CLAUDE_CODE_EFFORT", "HARNESS_PROJECT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_LOG=str(self.log), FAKE_REPORT=PASS)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "src/app.py").write_text("def add(a, b):\n    return a + b\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        (self.repo / "src/app.py").write_text("def add(a, b):\n    return a + b + 1\n")
        (self.repo / "src/new.py").write_text("NEW = 1\n")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True)

    def models(self, data):
        (self.harness / "adapters/models.json").write_text(json.dumps(data))

    def overlay_models(self, data, where="harness/project"):
        path = self.repo / where / "models.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        return path

    def tier_script(self, output):
        (self.harness / "scripts/tier.py").write_text(FAKE_TIER.format(output=output))

    def review(self, *args, writer="claude", cwd=None, **env):
        return subprocess.run(
            [sys.executable, str(self.harness / "scripts/xreview.py"), "--base", "HEAD",
             "--writer", writer, "--out", str(self.report), *args],
            cwd=cwd or self.repo, env={**self.env, **env}, capture_output=True, text=True, timeout=60)

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def context(self):
        return Path(f"{self.report}.context.md").read_text()

    def codex_argv(self, model, effort):
        guard = self.harness / "scripts/guard.py"
        hooks = ('hooks.PreToolUse=[{matcher="Bash|apply_patch|Edit|Write",hooks=[{type="command",'
                 f'command="python3 \'{guard}\' --runtime codex || true",timeout=10}}]}}]')
        return ["codex", "exec", "--sandbox", "read-only", "-m", model,
                "-c", f"model_reasoning_effort={effort}", "-c", hooks, "-C", str(self.repo),
                "--output-last-message", str(self.report), "-"]

    def test_single_vendor_review_uses_the_stronger_profile_and_says_so(self):
        # Codex-only run: Codex wrote it, Codex reviews it on another profile.
        ledger = self.temp / "run" / "xreview.jsonl"
        result = self.review("--tier", "2", "--reviewer", "codex", "--ledger", str(ledger), writer="codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        [call] = self.calls()
        self.assertEqual(call["argv"], self.codex_argv("codex-crit", "medium"))
        self.assertIn("single-vendor", self.context())
        entry = json.loads(ledger.read_text().splitlines()[-1])
        self.assertTrue(entry["single_vendor"])
        self.assertEqual(entry["profile"], "critical-review")
        # Claude-only run on Tier 3: Claude reviews Claude on critical-review.
        self.log.unlink()
        self.assertEqual(self.review("--tier", "3", "--reviewer", "claude").returncode, 0)
        [call] = self.calls()
        self.assertEqual(call["argv"], claude_argv("claude-crit"))

    def test_a_missing_other_vendor_is_an_explicit_choice(self):
        (self.bin / "codex").unlink()
        # Only the fakes and the system tools: a real codex further on PATH must not be found.
        result = self.review("--tier", "2", PATH=f"{self.bin}{os.pathsep}/usr/bin{os.pathsep}/bin")
        self.assertEqual(result.returncode, 2)
        self.assertIn("--reviewer claude", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_paths_split_a_large_diff_by_area_and_name_the_siblings(self):
        (self.repo / "docs").mkdir()
        (self.repo / "docs/guide.md").write_text("# Guide\n")
        result = self.review("--tier", "2", "--paths", "src/app.py")
        self.assertEqual(result.returncode, 0, result.stderr)
        context = self.context()
        self.assertIn("src/app.py", context)
        self.assertNotIn("NEW = 1", context)          # src/new.py is outside this part
        self.assertNotIn("# Guide", context)
        siblings = context.split("## Reviewed separately", 1)[1]
        self.assertIn("src/new.py", siblings)
        self.assertIn("docs/guide.md", siblings)

    def test_a_key_header_without_a_key_body_is_a_fixture_not_a_secret(self):
        (self.repo / "src/fixture.py").write_text(f'PEM = "{PEM_HEADER}\\nabc\\n"\n')
        self.assertEqual(self.review("--tier", "2").returncode, 0)
        body = "MIIEow" + "IBAAKCAQEAx3Q9" * 6
        (self.repo / "src/fixture.py").write_text(f"{PEM_HEADER}\n{body}\n")
        result = self.review("--tier", "2")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn(body, result.stderr)

    def test_deleted_or_tracked_secret_files_are_withheld_too(self):
        body = "MIIEow" + "IBAAKCAQEAx3Q9" * 6
        (self.repo / "deploy").mkdir()
        (self.repo / "deploy/server.pem").write_text(f"{PEM_HEADER}\n{body}\n")
        (self.repo / "config.env.local").write_text("TOKEN=tracked-secret\n")
        self.git("add", "deploy/server.pem", "config.env.local")
        self.git("commit", "-qm", "tracked secrets")
        base = self.git("rev-parse", "HEAD").strip()
        (self.repo / "deploy/server.pem").unlink()                      # deletion: body only in "-" lines
        (self.repo / "config.env.local").write_text("TOKEN=rotated\n")   # tracked secret file edited
        result = self.review("--tier", "2", "--base", base)
        self.assertEqual(result.returncode, 0, result.stderr)
        context = self.context()
        self.assertNotIn(body, context)
        self.assertNotIn("tracked-secret", context)
        skipped = context.split("## Skipped", 1)[1]
        self.assertIn("deploy/server.pem", skipped)
        # A key body removed from an ordinary file is still caught by the value scan.
        (self.repo / "src/app.py").write_text(f"{PEM_HEADER}\n{body}\n")
        self.git("add", "src/app.py"); self.git("commit", "-qm", "oops")
        base = self.git("rev-parse", "HEAD").strip()
        (self.repo / "src/app.py").write_text("def add(a, b):\n    return a + b\n")
        self.assertEqual(self.review("--tier", "2", "--base", base).returncode, 2)

    def test_secrets_never_reach_the_other_vendor(self):
        (self.repo / ".env").write_text("OPENROUTER_KEY=not-in-context\n")
        (self.repo / "src/key.pem").write_text(f"{PEM_HEADER}\nabc\n")
        result = self.review("--tier", "2")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("not-in-context", self.context())
        self.assertNotIn("BEGIN RSA", self.context())
        self.assertIn(".env", self.context().split("## Skipped", 1)[1])
        # A secret inside the reviewed diff stops the review; the value is not echoed.
        self.log.unlink(missing_ok=True)
        token = "sk-or-v1-" + "a1b2c3d4" * 8
        (self.repo / "src/app.py").write_text(f'KEY = "{token}"\n')
        result = self.review("--tier", "2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("src/app.py", result.stderr)
        self.assertNotIn(token, result.stderr + result.stdout)
        self.assertEqual(self.calls(), [])
        self.assertFalse(Path(f"{self.report}.context.md").exists() and token in self.context())
        (self.repo / "src/app.py").write_text("def add(a, b):\n    return a + b + 1\n")
        result = self.review("--tier", "2", "--addressed", str(self.repo / ".env"))
        self.assertEqual(result.returncode, 2)
        self.assertIn("addressed", result.stderr)

    def test_concurrent_reviews_cannot_pass_the_cap_together(self):
        ledger = self.temp / "run" / "xreview.jsonl"
        args = [sys.executable, str(self.harness / "scripts/xreview.py"), "--base", "HEAD",
                "--writer", "claude", "--tier", "3", "--ledger", str(ledger), "--max-critical", "1"]
        for attempt in range(3):
            ledger.unlink(missing_ok=True)
            runs = [subprocess.Popen([*args, "--out", str(self.temp / "out" / f"{attempt}-{n}.md")],
                                     cwd=self.repo, env=self.env, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True) for n in range(4)]
            codes = [run.wait(timeout=60) for run in runs]
            with self.subTest(attempt=attempt):
                self.assertEqual(codes.count(0), 1, codes)
                self.assertEqual(len(ledger.read_text().splitlines()), 1)

    def test_the_ledger_caps_critical_reviews_per_run(self):
        ledger = self.temp / "run" / "xreview.jsonl"
        for _ in range(2):
            self.assertEqual(self.review("--tier", "3", "--ledger", str(ledger), "--max-critical", "2").returncode, 0)
        self.assertEqual(self.review("--tier", "2", "--ledger", str(ledger), "--max-critical", "2").returncode, 0)
        entries = [json.loads(line) for line in ledger.read_text().splitlines()]
        self.assertEqual([e["profile"] for e in entries], ["critical-review", "critical-review", "implementation"])
        self.assertEqual(entries[0]["reviewer"], "codex")
        result = self.review("--tier", "3", "--ledger", str(ledger), "--max-critical", "2")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cap", result.stderr.lower())
        self.assertEqual(len(self.calls()), 3)

    def test_the_default_cap_is_five_critical_reviews_per_run(self):
        ledger = self.temp / "run" / "xreview.jsonl"
        for _ in range(5):
            self.assertEqual(self.review("--tier", "3", "--ledger", str(ledger)).returncode, 0)
        result = self.review("--tier", "3", "--ledger", str(ledger))
        self.assertEqual(result.returncode, 2)
        self.assertIn("5/5", result.stderr)

    def test_claude_code_is_reviewed_by_codex_on_the_implementation_profile(self):
        result = self.review("--tier", "2", cwd=self.repo / "src")
        self.assertEqual(result.returncode, 0, result.stderr)
        [call] = self.calls()
        self.assertEqual(call["argv"], self.codex_argv("codex-impl", "high"))
        prompt = (self.harness / "prompts/xreview.md").read_text()
        self.assertTrue(call["stdin"].startswith(prompt.strip()))
        self.assertIn(self.context(), call["stdin"])
        self.assertEqual(self.report.read_text(), PASS)

    def test_codex_code_is_reviewed_by_claude_read_only(self):
        result = self.review("--tier", "1", writer="codex", FAKE_REPORT=BLOCK)
        self.assertEqual(result.returncode, 1, result.stderr)
        [call] = self.calls()
        self.assertEqual(call["argv"], claude_argv("claude-impl"))
        self.assertEqual(call["effort"], "medium")
        self.assertEqual(Path(call["cwd"]).resolve(), self.repo)
        self.assertIn(self.context(), call["stdin"])
        self.assertEqual(self.report.read_text(), BLOCK)

    def test_tier_3_uses_critical_review_for_either_vendor(self):
        for writer, argv, effort in (("claude", self.codex_argv("codex-crit", "medium"), None),
                                     ("codex", claude_argv("claude-crit"), "high")):
            with self.subTest(writer=writer):
                self.log.unlink(missing_ok=True)
                result = self.review("--tier", "3", writer=writer)
                self.assertEqual(result.returncode, 0, result.stderr)
                [call] = self.calls()
                self.assertEqual(call["argv"], argv)
                self.assertEqual(call["effort"], effort)
                self.assertIn("Tier: 3", self.context())

    def test_tier_comes_from_tier_script_with_its_tier_3_paths(self):
        self.tier_script(json.dumps({"tier": 3, "reasons": [
            {"path": "src/app.py", "tier": 3, "reason": "auth path"},
            {"path": "src/new.py", "tier": 2, "reason": "default class"},
        ]}))
        result = self.review()
        self.assertEqual(result.returncode, 0, result.stderr)
        tier_call, review_call = self.calls()
        self.assertEqual(tier_call["argv"], ["tier.py", "--base", "HEAD", "--json"])
        self.assertEqual(Path(tier_call["cwd"]).resolve(), self.repo)
        self.assertEqual(review_call["argv"], self.codex_argv("codex-crit", "medium"))
        context = self.context()
        self.assertIn("Tier: 3", context)
        tier3 = section(context, "Tier-3 paths")
        self.assertIn("src/app.py", tier3)
        self.assertIn("auth path", tier3)
        self.assertNotIn("src/new.py", tier3)
        # An explicit --tier wins and does not run tier.py.
        self.log.unlink()
        self.assertEqual(self.review("--tier", "1").returncode, 0)
        [call] = self.calls()
        self.assertEqual(call["argv"], self.codex_argv("codex-impl", "high"))

    def test_without_tier_script_the_tier_is_2_and_a_broken_one_stops(self):
        result = self.review()
        self.assertEqual(result.returncode, 0, result.stderr)
        [call] = self.calls()
        self.assertEqual(call["argv"], self.codex_argv("codex-impl", "high"))
        self.assertIn("Tier: 2", self.context())
        self.assertNotIn("## Tier-3 paths", self.context())
        self.log.unlink()
        self.tier_script("not json")
        result = self.review()
        self.assertEqual(result.returncode, 2)
        self.assertIn("tier", result.stderr.lower())
        self.assertEqual([c["argv"][0] for c in self.calls()], ["tier.py"])

    def test_missing_profile_or_runtime_is_a_clear_error(self):
        self.models({"claude": MODELS["claude"], "codex": {"implementation": MODELS["codex"]["implementation"]}})
        result = self.review("--tier", "3")
        self.assertEqual(result.returncode, 2)
        self.assertIn("critical-review", result.stderr)
        self.assertIn("codex", result.stderr)
        self.models({"codex": MODELS["codex"]})
        result = self.review("--tier", "2", writer="codex")
        self.assertEqual(result.returncode, 2)
        self.assertIn("claude", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.report.exists())

    def test_overlay_models_are_deep_merged_over_the_core_profiles(self):
        # Only the model of one profile changes; its effort and every other profile stay.
        self.overlay_models({"codex": {"implementation": {"model": "codex-local"}}})
        self.assertEqual(self.review("--tier", "2").returncode, 0)
        [call] = self.calls()
        self.assertEqual(call["argv"], self.codex_argv("codex-local", "high"))
        self.log.unlink()
        self.assertEqual(self.review("--tier", "3").returncode, 0)
        [call] = self.calls()
        self.assertEqual(call["argv"], self.codex_argv("codex-crit", "medium"))

    def test_overlay_models_may_add_a_profile_the_core_lacks(self):
        self.models({"claude": MODELS["claude"], "codex": {"implementation": MODELS["codex"]["implementation"]}})
        self.overlay_models({"codex": {"critical-review": {"model": "codex-proj", "effort": "high"}}})
        self.assertEqual(self.review("--tier", "3").returncode, 0)
        [call] = self.calls()
        self.assertEqual(call["argv"], self.codex_argv("codex-proj", "high"))

    def test_overlay_location_follows_harness_project(self):
        self.overlay_models({"codex": {"implementation": {"model": "codex-moved"}}}, where="config/harness")
        self.assertEqual(self.review("--tier", "2").returncode, 0)
        self.assertEqual(self.calls()[-1]["argv"], self.codex_argv("codex-impl", "high"))
        self.assertEqual(self.review("--tier", "2", HARNESS_PROJECT="config/harness").returncode, 0)
        self.assertEqual(self.calls()[-1]["argv"], self.codex_argv("codex-moved", "high"))

    def test_an_unusable_overlay_models_file_is_a_clear_error(self):
        for text in ("{not json", "[]", json.dumps({"codex": {"implementation": {"model": 3}}})):
            with self.subTest(text=text):
                self.overlay_models(text)
                result = self.review("--tier", "2")
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("models.json", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_named_spec_sections_are_extracted_by_heading(self):
        spec = self.temp / "spec.md"
        spec.write_text(SPEC)
        result = self.review("--spec", str(spec), "--sections", "Tiers, Units")
        self.assertEqual(result.returncode, 0, result.stderr)
        context = self.context()
        for text in ("### Tiers (`tiers.json`)", "Tier body.", "#### Tier details", "Detail body.",
                     "## Units", "## Not a heading", "Units body."):
            self.assertIn(text, context)
        for text in ("Intro text.", "Design intro.", "Xreview body.", "Appendix body."):
            self.assertNotIn(text, context)
        for args in (("--spec", str(spec), "--sections", "Nope"), ("--spec", str(spec)),
                     ("--sections", "Tiers")):
            with self.subTest(args=args):
                result = self.review(*args)
                self.assertEqual(result.returncode, 2)
        self.assertIn("Nope", self.review("--spec", str(spec), "--sections", "Nope").stderr)
        self.assertEqual(len(self.calls()), 1)

    def test_addressed_findings_are_included_verbatim(self):
        addressed = self.temp / "addressed.md"
        text = "- [Major] src/app.py:2 — off by one — add(1, 1) → 3 (fixed in r2)\n   odd   spacing kept\n"
        addressed.write_text(text)
        result = self.review("--addressed", str(addressed))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(text, section(self.context(), "Already addressed — do not repeat"))

    def test_context_lists_changes_and_a_plain_diff_with_untracked_files(self):
        external = self.temp / "extdiff"
        external.write_text("#!/bin/sh\necho EXTERNAL DIFF\n")
        external.chmod(0o755)
        self.git("config", "diff.external", str(external))
        (self.repo / ".gitignore").write_text("ignored.txt\n")
        (self.repo / "ignored.txt").write_text("secret\n")
        result = self.review()
        self.assertEqual(result.returncode, 0, result.stderr)
        context = self.context()
        changed = section(context, "Changed files")
        for name in ("src/app.py", "src/new.py", ".gitignore"):
            self.assertIn(name, changed)
        self.assertNotIn("ignored.txt", changed)
        diff = section(context, "Diff")
        self.assertIn("diff --git a/src/app.py b/src/app.py", diff)
        self.assertIn("+    return a + b + 1", diff)
        self.assertIn("new file mode", diff)
        self.assertIn("+NEW = 1", diff)
        self.assertNotIn("EXTERNAL DIFF", context)
        self.assertNotIn("secret", context)

    def test_diff_over_200_kb_is_refused_per_unit(self):
        (self.repo / "src/big.txt").write_text(("x" * 99 + "\n") * 2100)
        result = self.review()
        self.assertEqual(result.returncode, 2)
        self.assertIn("per unit", result.stderr.lower())
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.report.exists())

    def test_no_changes_or_a_bad_base_is_not_reviewed(self):
        self.git("add", ".")
        self.git("commit", "-qm", "everything")
        result = self.review()
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing to review", result.stderr.lower())
        (self.repo / "src/app.py").write_text("changed again\n")
        result = subprocess.run([sys.executable, str(self.harness / "scripts/xreview.py"), "--base",
                                 "no-such-ref", "--writer", "claude", "--out", str(self.report)],
                                cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 2)
        self.assertIn("no-such-ref", result.stderr)
        self.assertEqual(self.calls(), [])

    def test_dry_run_prints_the_command_and_runs_nothing(self):
        result = self.review("--tier", "3", "--dry-run", writer="codex")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        argv = next(line for line in lines if line.startswith("argv: "))
        self.assertEqual(shlex.split(argv[len("argv: "):]), claude_argv("claude-crit"))
        self.assertIn("CLAUDE_CODE_EFFORT_LEVEL=high", result.stdout)
        context = Path(f"{self.report}.context.md")
        self.assertIn(f"context: {context}", lines)
        self.assertTrue(context.is_file())
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.report.exists())

    def test_timeout_is_reported_never_approved(self):
        started = time.monotonic()
        result = self.review("--timeout", "1", FAKE_SLEEP="30")
        self.assertLess(time.monotonic() - started, 20)
        self.assertEqual(result.returncode, 3)
        self.assertIn("VERDICT: timeout", self.report.read_text())
        self.assertIn("timeout", result.stderr.lower())

    def test_missing_or_unknown_verdict_is_not_an_approval(self):
        for report in ("- [Minor] src/app.py:1 — x — a → b\n", "VERDICT: maybe\n",
                       "VERDICT: pass\nVERDICT: lgtm\n", ""):
            with self.subTest(report=report):
                result = self.review(FAKE_REPORT=report)
                self.assertEqual(result.returncode, 3)
                self.assertIn("no verdict", result.stderr.lower())

    def test_the_last_verdict_line_decides(self):
        result = self.review(FAKE_REPORT="VERDICT: block\n\nRe-checked: fine.\n\nVERDICT: pass\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        result = self.review(FAKE_REPORT="- [Major] src/app.py:2 — x — add(2, 2) → 5\n\n**VERDICT: block**\n")
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_pass_with_an_open_critical_or_major_finding_is_a_block(self):
        for severity in ("Critical", "Major"):
            with self.subTest(severity=severity):
                report = f"- [{severity}] src/app.py:2 — off by one — add(2, 2) → 5\n\nVERDICT: pass\n"
                result = self.review(FAKE_REPORT=report)
                self.assertEqual(result.returncode, 1)
                self.assertIn(severity, result.stderr)

    def test_reviewer_failure_or_a_stale_report_is_not_an_approval(self):
        result = self.review(FAKE_EXIT="1")
        self.assertEqual(result.returncode, 3)
        self.report.write_text(PASS)
        result = self.review(FAKE_SKIP_WRITE="1")
        self.assertEqual(result.returncode, 3)
        self.assertNotIn("VERDICT: pass", self.report.read_text() if self.report.exists() else "")

    def test_prompt_is_short_and_states_the_contract(self):
        prompt = (HARNESS / "prompts/xreview.md").read_text()
        self.assertLessEqual(len(prompt.splitlines()), 40)
        for text in ("[Critical|Major|Minor] path:line", "failure scenario", "VERDICT: block",
                     "VERDICT: pass", "Already addressed — do not repeat", "downgrade"):
            self.assertIn(text, prompt)
        self.assertIn("never edit", prompt.lower())


if __name__ == "__main__":
    unittest.main()
