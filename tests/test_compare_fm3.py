"""FM3: compare candidates on lanes' candidate profile, full briefs for critics, dashboard
regeneration on compare transitions, and the guard in a Codex candidate's scratch root.

The real CLIs are never called: the fakes of test_compare (design) and
test_compare_investigation (investigation) come first on PATH and record argv and prompt.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(CORE / "scripts"))
import test_compare as design_tests  # noqa: E402  (module import: its TestCase is not re-run)
import test_compare_investigation as investigation_tests  # noqa: E402
from test_qa_compare import wait_for_supervisors  # noqa: E402

COMPARE = CORE / "scripts" / "compare.py"
TASK_STATE = CORE / "scripts" / "task_state.py"

BRIEF = investigation_tests.BRIEF.replace(
    "The cache test fails one time in ten.\n",
    "The cache test fails one time in ten.\nKNOWN-FACT: the cache was rewritten last week.\n")


class CompareFM3Test(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo = self.temp / "repo"
        self.bin = self.temp / "bin"
        self.project = self.temp / "project"
        for directory in (self.repo, self.bin, self.project):
            directory.mkdir()
        self.log = self.temp / "calls.jsonl"
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "HERDR_", "CLAUDE_CODE_EFFORT",
                                         "HARNESS_PROJECT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_LOG=str(self.log), HARNESS_PROJECT=str(self.project))
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Harness Test"),
                     ("config", "user.email", "harness@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            self.git(*args)
        (self.repo / "README").write_text("x\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        subprocess.run([sys.executable, str(TASK_STATE), "init", "t1"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        self.run_dir = self.repo / ".harness" / "tasks" / "t1"
        self.addCleanup(self.drain_supervisors)  # runs before the temporary directory goes

    def drain_supervisors(self):
        """A finished compare's lane supervisors may still record dispatch.end and regenerate
        the dashboard; wait for them before reading the page or removing the run."""
        states = [json.loads(path.read_text()) for path in
                  (self.run_dir / "lanes").glob("*.json") if not path.name.startswith("_")]
        wait_for_supervisors([state["pid"] for state in states if state.get("pid")])

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True)

    def fakes(self, source: str) -> None:
        for name in ("claude", "codex"):
            fake = self.bin / name
            fake.write_text(source.replace("{python}", sys.executable))
            fake.chmod(0o755)

    def compare(self, *args, expect=0, **env):
        result = subprocess.run([sys.executable, str(COMPARE), *args], cwd=self.repo,
                                env={**self.env, **env}, capture_output=True, text=True,
                                timeout=120)
        if expect is not None:
            self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def design(self, **env):
        self.fakes(design_tests.FAKE_CLI)
        source = self.temp / "interview.md"
        source.write_text(design_tests.INTERVIEW, encoding="utf-8")
        result = self.compare("design", "--task", "t1", "--input", str(source), "--checkout",
                              str(self.repo), "--timeout", "30", "--arbiter-timeout", "30",
                              FAKE_MEMO_claude="Plan alpha.\n", FAKE_MEMO_codex="Plan beta. STRONG\n",
                              **env)
        return json.loads(result.stdout), result

    def investigate(self, *extra, **env):
        self.fakes(investigation_tests.FAKE_CLI)
        self.brief = self.temp / "brief.md"
        self.brief.write_text(BRIEF, encoding="utf-8")
        result = self.compare("investigation", "--task", "t1", "--input", str(self.brief),
                              "--checkout", str(self.repo), "--timeout", "30",
                              "--critique-timeout", "30", *extra,
                              FAKE_REPORT_claude=investigation_tests.REPORT_CLAUDE,
                              FAKE_REPORT_codex=investigation_tests.REPORT_CODEX, **env)
        return json.loads(result.stdout), result

    def events(self):
        path = self.run_dir / "events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def calls(self, role):
        calls = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
        return {c["name"]: c for c in calls if c["role"] == role}

    def dispatches(self, role):
        return {e["runtime"]: e for e in self.events()
                if e["type"] == "dispatch.start" and e["role"] == role}

    def judgement(self, cid):
        return json.loads((self.run_dir / "compare" / cid / "judgement.json").read_text())

    @staticmethod
    def after(argv, flag):
        return argv[argv.index(flag) + 1]

    # 1. Candidates start on lanes' candidate profile with a private scratch directory.

    def assert_candidates_on_the_candidate_profile(self, cid, role):
        folder = self.run_dir / "compare" / cid
        starts = self.dispatches(role)
        self.assertEqual(sorted(starts), ["claude", "codex"])
        scratches = set()
        for runtime, event in starts.items():
            with self.subTest(runtime=runtime):
                self.assertEqual(event["capability"], "candidate")
                scratch = Path(event["scratch"])
                self.assertTrue(scratch.is_dir())
                self.assertEqual(scratch.name, "scratch")
                self.assertEqual(scratch.parent.parent, folder / "private")
                # The report lives in the scratch, so a scratch-only candidate can write it
                # as it goes; the sealed copy is still candidate-<label>.md.
                self.assertEqual(Path(event["report"]), scratch / "report.md")
                scratches.add(scratch)
        self.assertEqual(len(scratches), 2)  # one private scratch per candidate
        calls = self.calls(role)
        codex, claude = calls["codex"]["argv"], calls["claude"]["argv"]
        self.assertEqual(self.after(codex, "--sandbox"), "workspace-write")
        self.assertEqual(self.after(codex, "-C"), starts["codex"]["snapshot"])  # FM4: the snapshot
        self.assertEqual(self.after(claude, "--permission-mode"), "dontAsk")
        self.assertIn(f"Write(/{starts['claude']['scratch']}/**)", claude)
        self.assertEqual(self.after(claude, "--add-dir"), starts["claude"]["scratch"])

    def test_design_candidates_start_with_the_candidate_capability_and_a_scratch(self):
        out, _ = self.design()
        self.assert_candidates_on_the_candidate_profile(out["compare"], "candidate")
        arbiters = [e for e in self.events()
                    if e["type"] == "dispatch.start" and e["role"] == "arbiter"]
        self.assertEqual(len(arbiters), 2)
        for event in arbiters:
            self.assertNotIn("capability", event)  # the arbiter keeps its read-only profile
            self.assertEqual(event["launcher"], "headless")
        calls = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
        for call in (c for c in calls if c["role"] == "arbiter"):
            self.assertEqual(self.after(call["argv"], "--sandbox"), "read-only")

    def test_investigators_start_with_the_candidate_capability_and_critics_stay_read_only(self):
        out, _ = self.investigate()
        self.assert_candidates_on_the_candidate_profile(out["compare"], "investigator")
        critics = self.dispatches("critic")
        self.assertEqual(sorted(critics), ["claude", "codex"])
        for event in critics.values():
            self.assertNotIn("capability", event)
            self.assertEqual(event["launcher"], "headless")
        calls = self.calls("critic")
        self.assertEqual(self.after(calls["codex"]["argv"], "--sandbox"), "read-only")
        self.assertNotIn("--permission-mode", calls["claude"]["argv"])
        self.assertNotIn("--add-dir", calls["claude"]["argv"])

    # 1. Equal profiles give capability "equal", with the enforcement asymmetry noted.

    def assert_equal_with_the_note(self, cid):
        start = next(e for e in self.events() if e["type"] == "compare.start")
        self.assertEqual(start["capability"], "equal")
        verdict = self.judgement(cid)
        self.assertEqual(verdict["capability"], "equal")
        reason = verdict["capability_reason"]
        self.assertIn("snapshot", reason)
        self.assertIn("sandbox", reason)
        note = verdict["capability_note"]
        self.assertIn("Claude", note)
        self.assertIn("Bash", note)
        self.assertIn("snapshot", note)
        self.assertIn("sandbox", note)
        self.assertIn("brief", note)
        self.assertIn("Codex", note)
        self.assertIn("sandbox", note)
        self.assertNotIn("capability was reserved as", json.dumps(verdict["notes"]))
        comparison = (self.run_dir / "compare" / cid / "comparison.md").read_text()
        self.assertIn("**equal**", comparison)
        self.assertIn(note, comparison)

    def test_design_capability_is_equal_with_the_enforcement_note(self):
        out, _ = self.design()
        self.assert_equal_with_the_note(out["compare"])

    def test_investigation_capability_is_equal_with_the_enforcement_note(self):
        out, _ = self.investigate()
        self.assert_equal_with_the_note(out["compare"])

    def test_capability_comes_from_lanes_capability_profile(self):
        import compare
        import lanes
        self.assertFalse(hasattr(compare, "CAPABILITY"))
        candidates = [{"label": "claude", "runtime": "claude"},
                      {"label": "codex", "runtime": "codex"}]
        calls = []
        real = lanes.capability_profile

        def recorded(runtime, launcher, capability=None, **kwargs):
            calls.append((runtime, launcher, capability))
            return real(runtime, launcher, capability, **kwargs)

        lanes.capability_profile = recorded
        self.addCleanup(setattr, lanes, "capability_profile", real)
        cap, _ = compare.capability({"claude": "headless", "codex": "pane"}, candidates)
        self.assertEqual(cap, "equal")
        self.assertEqual(sorted(calls), [("claude", "headless", "candidate"),
                                         ("codex", "pane", "candidate")])
        cap, reason = compare.capability({"claude": "headless", "codex": None}, candidates)
        self.assertEqual(cap, "capability-unequal")
        self.assertIn("unknown", reason)

    # 2. Critiques get the full investigation brief and only the other report.

    def test_critique_brief_holds_the_full_brief_and_only_the_other_report(self):
        out, _ = self.investigate()
        calls = self.calls("critic")
        input_hash = self.judgement(out["compare"])["input_hash"]
        for critic, other, own in (("codex", "ALPHA", "BETA"), ("claude", "BETA", "ALPHA")):
            with self.subTest(critic=critic):
                prompt = calls[critic]["prompt"]
                self.assertIn(BRIEF.strip(), prompt)
                self.assertIn("KNOWN-FACT: the cache was rewritten last week.", prompt)
                self.assertIn(input_hash, prompt)
                self.assertIn(f"{other} log line" if other == "ALPHA" else f"{other} timing trace",
                              prompt)
                # Of its own vendor's report only the **Answer:** line, labelled for comparison.
                self.assertNotIn(f"{own} log line" if own == "ALPHA" else f"{own} timing trace",
                                 prompt)
                self.assertEqual(prompt.count(own), 1)
                self.assertIn("## The other investigation's answer, for comparison", prompt)
                self.assertEqual(prompt.count("## Report under review"), 1)

    # 3. Every recorded compare transition regenerates the run dashboard, best effort.

    def bare_run(self):
        import compare
        run = compare.Run.__new__(compare.Run)
        run.task, run.primary, run.run = "t1", self.repo, self.run_dir
        run.log = self.run_dir / "events.jsonl"
        return compare, run

    def test_every_recorded_compare_event_regenerates_the_dashboard(self):
        compare, run = self.bare_run()
        import dashboard
        seen = []
        real = dashboard.regenerate
        dashboard.regenerate = lambda r, p: seen.append((Path(r), Path(p)))
        self.addCleanup(setattr, dashboard, "regenerate", real)
        kinds = ("start", "claim", "launch", "seal", "pass", "result")
        for kind in kinds:
            self.assertTrue(run.emit(f"compare.{kind}", {"compare": "cmp-x", "kind": "design"},
                                     f"cmp-x.{kind}"))
            self.assertEqual(len(seen), kinds.index(kind) + 1, kind)
        self.assertEqual(set(seen), {(self.run_dir, self.repo)})
        self.assertFalse(run.emit("compare.result", {"compare": "cmp-x", "kind": "design"},
                                  "cmp-x.result"))
        self.assertEqual(len(seen), len(kinds))  # a skipped duplicate is no transition

    def test_a_raising_regeneration_is_a_note_and_the_event_is_kept(self):
        compare, run = self.bare_run()
        import dashboard
        real = dashboard.regenerate

        def broken(r, p):
            raise RuntimeError("disk on fire")

        dashboard.regenerate = broken
        self.addCleanup(setattr, dashboard, "regenerate", real)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            self.assertTrue(run.emit("compare.start", {"compare": "cmp-y", "kind": "design"},
                                     "cmp-y.start"))
        self.assertIn("disk on fire", stderr.getvalue())
        self.assertIn("dashboard", stderr.getvalue())
        self.assertEqual([e["id"] for e in self.events()], ["cmp-y.start"])

    def test_the_dashboard_shows_the_compare_result_after_the_round(self):
        out, _ = self.design()
        self.drain_supervisors()
        page = (self.run_dir / "dashboard.html").read_text()
        row = re.search(rf'<tr id="compare-{out["compare"]}">.*?</tr>', page, re.DOTALL)
        self.assertIsNotNone(row, page)
        self.assertIn('<td class="state">done</td>', row.group(0))

    def test_a_failing_regeneration_does_not_fail_the_compare(self):
        (self.run_dir / "dashboard.html").mkdir()  # the page cannot be written
        out, result = self.investigate()
        self.assertIn("not regenerated", result.stderr)
        self.assertIn(self.judgement(out["compare"])["confidence"], ("low", "medium", "high"))
        self.assertEqual(sum(e["type"] == "compare.result" for e in self.events()), 1)

    # 4. The guard's core rules hold in a Codex candidate's scratch root inside the run.

    def test_guard_rules_hold_in_a_codex_candidate_scratch_root(self):
        self.investigate("--keep-snapshots")  # compare removes snapshots after the result (QA M2)
        argv = self.calls("investigator")["codex"]["argv"]
        scratch = Path(self.after(argv, "-C"))
        self.assertTrue(scratch.is_dir())
        self.assertIn(self.run_dir, scratch.parents)
        hook = next(arg for arg in argv if arg.startswith("hooks.PreToolUse="))
        command = re.search(r'command="(.*?)",timeout', hook).group(1)
        self.assertIn("--runtime codex", command)

        def verdict(cmd):
            event = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                     "tool_input": {"command": cmd, "workdir": str(scratch)}, "cwd": str(scratch)}
            done = subprocess.run(["sh", "-c", command], cwd=scratch, env=self.env,
                                  input=json.dumps(event), capture_output=True, text=True)
            self.assertEqual(done.returncode, 0, done.stderr)
            if not done.stdout.strip():
                return None
            specific = json.loads(done.stdout)["hookSpecificOutput"]
            return specific["permissionDecision"], specific["permissionDecisionReason"]

        for cmd, word in (("git push origin main", "push"), ("git add -A", "explicit paths"),
                          ("cat .env", ".env"), (f"cat {self.repo / '.env'}", ".env"),
                          ("printf x > .env.local", ".env")):
            with self.subTest(cmd=cmd):
                found = verdict(cmd)
                self.assertIsNotNone(found, cmd)
                self.assertEqual(found[0], "deny", found)
                self.assertIn(word, found[1])
        self.assertIsNone(verdict("ls"))  # ordinary scratch work is not refused
        self.assertIsNone(verdict("printf x > notes.md"))


if __name__ == "__main__":
    unittest.main()
