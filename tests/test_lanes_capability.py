"""`lanes.py start --capability candidate --scratch DIR`: one capability profile on both vendors
(read the source checkout, run a shell, write only inside DIR), `capability_profile` for
callers that compare candidates, the herdr agent limit locked per workspace across runs, and
cancel never delivering a pending brief.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch (herdr behind
the wrapper of test_m1_review_fixes) come first on PATH and record their argv; the CLI fake
here also records its working directory.
"""
import argparse
import contextlib
import fcntl
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_launch as launch  # noqa: E402
import test_m1_review_fixes as fixes  # noqa: E402

lanes = launch.lanes
CORE = launch.CORE
ENVELOPE = launch.ENVELOPE
SPEC_START = launch.SPEC_START
CANDIDATE = {"read_source": True, "shell": True, "writes": "scratch"}
RECORDED = '"effort": os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")'
# Claude Code's Bash sandbox, fail-closed: shell writes only in the cwd (the snapshot), the
# --add-dir scratch and the per-user temp directory.
SANDBOX = ('{"sandbox":{"enabled":true,"failIfUnavailable":true,"allowUnsandboxedCommands":false,'
           '"filesystem":{"denyWrite":["%s"]}}}')


def claude_permissions(scratch: Path, repo: Path) -> list[str]:
    """The candidate permission flags of Claude; `//` makes a rule path absolute."""
    return ["--permission-mode", "dontAsk", "--allowedTools", "Read", "Grep", "Glob", "Bash",
            f"Write(/{scratch}/**)", f"Edit(/{scratch}/**)", "--add-dir", str(scratch),
            "--setting-sources", "project,local", "--settings", SANDBOX % repo]


class CapabilityTest(unittest.TestCase):
    setUp_fixes = fixes.LanesReviewFixTest.setUp
    setUp_launch = launch.LaunchTest.setUp
    cleanup = launch.LaunchTest.cleanup
    git = launch.LaunchTest.git
    herdr_env = launch.LaunchTest.herdr_env
    lanes = launch.LaunchTest.lanes
    wait = launch.LaunchTest.wait
    status = launch.LaunchTest.status
    calls = launch.LaunchTest.calls
    herdr_calls = launch.LaunchTest.herdr_calls
    events = launch.LaunchTest.events
    state_file = launch.LaunchTest.state_file
    set_agent = launch.LaunchTest.set_agent
    report_path = fixes.LanesReviewFixTest.report_path
    of = fixes.LanesReviewFixTest.of

    def setUp(self):
        self.setUp_fixes()
        source = launch.FAKE_CLI.format(python=sys.executable)
        self.assertIn(RECORDED, source)
        source = source.replace(RECORDED, RECORDED + ', "cwd": os.getcwd()')
        for name in ("claude", "codex"):
            (self.bin / name).write_text(source)
        self.env["XDG_RUNTIME_DIR"] = str(self.temp / "xdg")
        (self.temp / "xdg").mkdir()
        self.scratch = self.temp / "compare" / "A" / "scratch"  # missing until the start
        self.snapshot = self.scratch / "src"  # the candidate's private copy of the source

    def begin(self, *extra, runtime="claude", role="reviewer", work="U9", task="t1", env=None,
              expect=0, capability=True):
        flags = ["--capability", "candidate", "--scratch", str(self.scratch)] if capability \
            else []
        result = self.lanes(
            "start", "--task", task, "--work", work, "--role", role, "--runtime", runtime,
            "--model", "model-x", "--effort", "high", "--brief", str(self.brief),
            "--report", str(self.report_path(work, role)), "--checkout", str(self.repo),
            "--minutes", "5", "--orchestrator", "claude", *flags, *extra, **(env or {}))
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        if expect:
            return result
        out = json.loads(result.stdout)
        if task == "t1":
            self.started.append(out["dispatch"])
        return out

    def assert_candidate_recorded(self, out, launcher):
        self.assertEqual(out["launcher"], launcher)
        self.assertEqual((out["capability"], out["scratch"]), ("candidate", str(self.scratch)))
        self.assertEqual(set(out), {"dispatch", "launcher", "reason", "pane", "capability",
                                    "scratch", "snapshot", "snapshot_base"})
        base = self.git("rev-parse", "HEAD").strip()
        self.assertEqual((out["snapshot"], out["snapshot_base"]), (str(self.snapshot), base))
        [start] = [e for e in self.events(out["dispatch"]) if e["type"] == "dispatch.start"]
        self.assertEqual(set(start) - ENVELOPE,
                         SPEC_START | {"capability", "scratch", "snapshot", "snapshot_base"})
        self.assertEqual((start["capability"], start["scratch"]),
                         ("candidate", str(self.scratch)))
        self.assertEqual((start["snapshot"], start["snapshot_base"]), (str(self.snapshot), base))
        self.assertTrue(self.scratch.is_dir(), "the scratch directory is created at the start")

    def guard_hook(self, argv):
        hooks = [a for a in argv if a.startswith("hooks.PreToolUse=")]
        self.assertEqual(len(hooks), 1)
        self.assertIn(str(CORE / "scripts" / "guard.py"), hooks[0])
        return hooks[0]

    # --- capability_profile -----------------------------------------------------------

    def test_the_candidate_profile_is_the_same_for_both_runtimes_and_launchers(self):
        for runtime in ("claude", "codex"):
            for launcher in ("headless", "pane"):
                self.assertEqual(lanes.capability_profile(runtime, launcher, "candidate"),
                                 CANDIDATE, (runtime, launcher))

    def test_the_default_profiles_say_what_each_worker_really_gets(self):
        profile = lanes.capability_profile
        self.assertEqual(profile("claude", "headless", None),
                         {"read_source": True, "shell": False, "writes": "none"})
        self.assertEqual(profile("codex", "headless", None),
                         {"read_source": True, "shell": True, "writes": "none"})
        self.assertNotEqual(profile("claude", "headless", None), profile("codex", "headless", None))
        for runtime in ("claude", "codex"):
            self.assertEqual(profile(runtime, "headless", None, role="implementer"),
                             {"read_source": True, "shell": True, "writes": "checkout"})
            self.assertEqual(profile(runtime, "pane", None),
                             {"read_source": True, "shell": "as-approved",
                              "writes": "as-approved"})
            self.assertNotEqual(profile(runtime, "pane", None), CANDIDATE)
        self.assertEqual(profile("claude", "headless", None, role="reviewer"),
                         profile("claude", "headless", None))

    def test_capability_profile_refuses_what_lanes_does_not_launch(self):
        for args in (("claude", "subagent", "candidate"), ("gemini", "headless", None),
                     ("claude", "headless", "admin")):
            with self.assertRaises(ValueError, msg=args):
                lanes.capability_profile(*args)

    # --- headless ---------------------------------------------------------------------

    def test_headless_claude_candidate_reads_the_source_and_writes_only_the_scratch(self):
        out = self.begin("--launcher", "headless")
        self.assert_candidate_recorded(out, "headless")
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        (call,) = self.calls()
        self.assertEqual(call["argv"], ["claude", "-p", "--output-format", "stream-json",
                                        "--verbose", "--model", "model-x",
                                        *claude_permissions(self.scratch, self.repo)])
        self.assertEqual(call["cwd"], str(self.snapshot),
                         "the snapshot carries the project's hooks; the source is never the cwd")
        self.assertIn(f"The source checkout is {self.repo}", call["prompt"])
        self.assertIn(f"write only inside {self.scratch}", call["prompt"])
        self.assertIn("Do the unit.", call["prompt"])

    def test_headless_codex_candidate_gets_a_scratch_workspace_and_keeps_the_guard(self):
        out = self.begin("--launcher", "headless", runtime="codex")
        self.assert_candidate_recorded(out, "headless")
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        (call,) = self.calls()
        argv = call["argv"]
        hooks = self.guard_hook(argv)
        self.assertEqual(argv, ["codex", "exec", "--json", "--sandbox", "workspace-write",
                                "-m", "model-x", "-c", "model_reasoning_effort=high", "-c", hooks,
                                "-C", str(self.snapshot), "--add-dir", str(self.scratch),
                                "--skip-git-repo-check",
                                "--output-last-message",
                                f"{self.report_path()}.last-message.md", "-"])
        self.assertEqual(call["cwd"], str(self.snapshot))
        self.assertIn(f"The source checkout is {self.repo}", call["prompt"])
        self.assertIn(f"write only inside {self.scratch}", call["prompt"])

    def test_a_writer_role_gets_the_candidate_profile_not_the_writer_one(self):
        out = self.begin("--launcher", "headless", role="implementer")
        self.wait(out["dispatch"])
        (call,) = self.calls()
        self.assertNotIn(lanes.WRITER_TOOLS, call["argv"])
        self.assertEqual(call["argv"][-len(claude_permissions(self.scratch, self.repo)):],
                         claude_permissions(self.scratch, self.repo))

    # --- panes ------------------------------------------------------------------------

    def test_claude_pane_candidate_passes_the_same_flags_through_herdr(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        self.assert_candidate_recorded(out, "pane")
        split = next(c for c in self.herdr_calls() if c[:2] == ["pane", "split"])
        self.assertEqual(split[split.index("--cwd") + 1], str(self.snapshot))
        [started] = self.of(["agent", "start"])
        self.assertEqual(started[started.index("--kind") + 1], "claude")
        self.assertEqual(started[started.index("--") + 1:],
                         ["--model", "model-x", *claude_permissions(self.scratch, self.repo)])
        [prompt] = self.of(["agent", "prompt"])
        self.assertIn(str(self.brief), prompt[3])
        self.assertIn(f"The source checkout is {self.repo}", prompt[3])
        self.assertIn(f"write only inside {self.scratch}", prompt[3])
        self.assertEqual(self.calls(), [])

    def test_codex_pane_candidate_passes_the_same_flags_through_herdr(self):
        out = self.begin("--launcher", "pane", runtime="codex", env=self.herdr_env())
        self.assert_candidate_recorded(out, "pane")
        [started] = self.of(["agent", "start"])
        passed = started[started.index("--") + 1:]
        hooks = self.guard_hook(passed)
        self.assertEqual(passed, ["-m", "model-x", "-c", "model_reasoning_effort=high", "-c",
                                  hooks, "--sandbox", "workspace-write", "-C",
                                  str(self.snapshot), "--add-dir", str(self.scratch)])
        [prompt] = self.of(["agent", "prompt"])
        self.assertIn(f"The source checkout is {self.repo}", prompt[3])
        self.assertIn(f"write only inside {self.scratch}", prompt[3])

    # --- the decision, bad arguments, no change without --capability -------------------

    def test_a_candidate_is_never_handed_back_as_a_subagent(self):
        out = self.begin()  # same runtime as the orchestrator, short: a subagent otherwise
        self.assertEqual(out["launcher"], "headless")
        self.assertIn("compare candidate", out["reason"])
        self.wait(out["dispatch"])
        self.assertEqual(len(self.calls()), 1)

    def test_scratch_and_capability_need_each_other_and_a_scratch_outside_the_source(self):
        cases = [(["--scratch", str(self.scratch)], "--scratch"),
                 (["--capability", "candidate"], "--scratch"),
                 (["--capability", "candidate", "--scratch", str(self.repo)], "scratch"),
                 (["--capability", "candidate", "--scratch", str(self.temp)], "scratch")]
        for flags, word in cases:
            result = self.begin("--launcher", "headless", *flags, capability=False, expect=2)
            self.assertIn(word, result.stderr, flags)
        result = self.begin("--launcher", "headless", "--capability", "admin",
                            "--scratch", str(self.scratch), capability=False, expect=2)
        self.assertIn("--capability", result.stderr)
        self.assertEqual(self.events(), [])
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.scratch.exists())

    def test_without_capability_nothing_changes(self):
        out = self.begin("--launcher", "headless", capability=False)
        self.assertEqual(set(out), {"dispatch", "launcher", "reason", "pane"})
        self.wait(out["dispatch"])
        [start] = [e for e in self.events(out["dispatch"]) if e["type"] == "dispatch.start"]
        self.assertEqual(set(start) - ENVELOPE, SPEC_START)
        (call,) = self.calls()
        self.assertEqual(call["argv"], ["claude", "-p", "--output-format", "stream-json",
                                        "--verbose", "--model", "model-x"])
        self.assertNotIn("The source checkout is", call["prompt"])
        self.assertFalse(self.scratch.exists())

    def test_the_importable_start_takes_the_same_keywords(self):
        args = argparse.Namespace(
            task="t1", work="U9", role="reviewer", runtime="codex", model="model-x",
            effort="high", brief=str(self.brief), report=str(self.report_path()),
            checkout=str(self.repo), minutes=0, needs_user=False, outlives_turn=False, tier=None,
            compare_candidate=False, orchestrator="claude", launcher="headless",
            timeout=lanes.DEFAULT_TIMEOUT, max_budget_usd=None)
        printed = io.StringIO()
        previous = Path.cwd()
        os.chdir(self.repo)
        try:
            with mock.patch.dict(os.environ, self.env, clear=True), \
                    contextlib.redirect_stdout(printed):
                code = lanes.start(args, capability="candidate", scratch=str(self.scratch))
        finally:
            os.chdir(previous)
        self.assertEqual(code, 0)
        out = json.loads(printed.getvalue())
        self.started.append(out["dispatch"])
        self.assert_candidate_recorded(out, "headless")
        self.wait(out["dispatch"])
        (call,) = self.calls()
        self.assertEqual(call["argv"][call["argv"].index("-C") + 1], str(self.snapshot))

    # --- round 2: the herdr agent limit is locked per workspace, across runs ----------

    def test_the_herdr_lock_is_one_file_per_workspace_shared_by_every_run(self):
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.temp / "xdg"),
                                          "HERDR_WORKSPACE_ID": "w1"}):
            first = lanes.herdr_lock_path()
        with mock.patch.dict(os.environ, {"XDG_RUNTIME_DIR": str(self.temp / "xdg"),
                                          "HERDR_WORKSPACE_ID": "w2"}):
            other = lanes.herdr_lock_path()
        env = {k: v for k, v in os.environ.items()
               if k not in ("XDG_RUNTIME_DIR", "HERDR_WORKSPACE_ID")}
        with mock.patch.dict(os.environ, env, clear=True):
            fallback = lanes.herdr_lock_path()
        self.assertEqual(first, self.temp / "xdg" / "dev-harness-herdr-w1.lock")
        self.assertNotEqual(first, other)
        self.assertEqual(fallback, Path(tempfile.gettempdir()) / "dev-harness-herdr-default.lock")

    def test_a_pane_start_of_another_run_waits_for_the_workspace_lock(self):
        subprocess.run([sys.executable, str(launch.TASK_STATE), "init", "t2"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        lock = self.temp / "xdg" / "dev-harness-herdr-w1.lock"
        with lock.open("a", encoding="utf-8") as held:
            fcntl.flock(held, fcntl.LOCK_EX)  # another run is counting or launching
            second = subprocess.Popen(
                [sys.executable, str(launch.LANES), "start", "--task", "t2", "--work", "U8",
                 "--role", "reviewer", "--runtime", "claude", "--model", "model-x",
                 "--effort", "high", "--brief", str(self.brief), "--report",
                 str(self.report_path("U8")), "--checkout", str(self.repo), "--launcher",
                 "pane"], cwd=self.repo, env={**self.env, **self.herdr_env()},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.addCleanup(second.kill)
            with self.assertRaises(subprocess.TimeoutExpired,
                                   msg="task t2 launched while the workspace lock was held"):
                second.wait(timeout=3)
            self.assertEqual(self.of(["agent", "list"]), [])
        out, err = second.communicate(timeout=30)
        self.assertEqual(second.returncode, 0, out + err)
        self.assertEqual(json.loads(out)["launcher"], "pane")
        self.assertEqual(len(self.of(["agent", "start"])), 1)

    # --- round 2: cancel never delivers a pending brief -------------------------------

    def test_cancel_of_an_idle_agent_with_a_pending_brief_sends_no_prompt(self):
        out = self.begin("--launcher", "pane", role="implementer", capability=False,
                         env=self.herdr_env(FAKE_HERDR_NOT_READY="1"))
        self.assertIs(self.state_file(out["dispatch"])["prompt_pending"], True)
        self.set_agent(out["pane"], "idle")  # ready: a status now would deliver the brief
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"],
                            **self.herdr_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "cancelled")
        self.assertEqual(self.of(["agent", "prompt"]), [])
        self.assertTrue(self.of(["agent", "send-keys"]))
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "cancelled")
        self.assertEqual(self.of(["agent", "prompt"]), [])


if __name__ == "__main__":
    unittest.main()
