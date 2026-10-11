"""Frictions seen in the outbound-p01 run (1.6.0), the lanes side: items 3, 4, 6, 15 and 16.

3   start waits (bounded) for a new pane's shell to reach its prompt before `agent start`, and
    retries once on herdr's agent_pane_busy ("not an available shell").
4   a reused pane's shell is reset (kitty keyboard mode popped, stty sane) and its prompt
    verified before the worker's directory and environment are typed into it.
6   the worker is told its flag file (<report>.flag.md), the sanctioned channel to the
    coordinator, and never to type into other panes.
15  the shadow router gets a profile derived from --role/--tier when --profile is absent.
16  the worker is told to keep progress in <report>.progress.md and write the report at the end.

A fake `herdr` first on PATH models what the real one (0.9) did in the run: a new pane whose
shell is still running its rc files has the shell's process group in the foreground but another
process in it, and `agent start` then fails with agent_pane_busy.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[1]
LANES = CORE / "scripts" / "lanes.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
sys.path.insert(0, str(CORE / "scripts"))
import dashboard_herdr  # noqa: E402
import lanes  # noqa: E402

FAKE_HERDR = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
path = os.environ["FAKE_HERDR_STATE"]
with open(path, encoding="utf-8") as stream:
    s = json.load(stream)
with open(os.environ["FAKE_HERDR_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\n")
def save():
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(s, stream)
def reply(result):
    print(json.dumps({{"id": "cli", "result": result}}))
def fail(code, message="failed"):
    print(json.dumps({{"error": {{"code": code, "message": message}}}}), file=sys.stderr)
    sys.exit(1)
def opt(name):
    return args[args.index(name) + 1] if name in args else None
def find(name):
    return next((a for a in s["agents"] if a.get("name") == name or a["pane_id"] == name), None)
head = args[:2]
if head == ["agent", "list"]:
    reply({{"agents": s["agents"]}})
elif head == ["pane", "split"]:
    s["next"] = s.get("next", 2) + 1
    pane = "w1:p%d" % s["next"]
    s.setdefault("panes", {{}})[pane] = {{"busy": s.get("new_busy", 0)}}
    save()
    reply({{"pane": {{"pane_id": pane}}}})
elif head == ["pane", "rename"]:
    reply({{}})
elif head == ["pane", "process-info"]:
    if s.get("no_process_info"):
        fail("unknown_command", "no such command")
    pane = s["panes"][opt("--pane")]
    procs = [{{"pid": 100, "name": "zsh"}}]
    if pane["busy"] > 0 or pane.get("never_ready"):
        pane["busy"] -= 1
        procs.append({{"pid": 101, "name": "bash"}})  # rc files still running in the group
    save()
    reply({{"process_info": {{"pane_id": opt("--pane"), "shell_pid": 100,
                              "foreground_process_group_id": 100,
                              "foreground_processes": procs}}}})
elif head == ["agent", "start"]:
    name, pane = args[2], opt("--pane")
    if s["panes"][pane]["busy"] > 0 or s.get("start_busy", 0) > 0:
        s["start_busy"] = max(0, s.get("start_busy", 0) - 1)
        save()
        fail("agent_pane_busy", "agent target pane %s is not an available shell" % pane)
    agent = {{"name": name, "agent": opt("--kind"), "pane_id": pane, "agent_status": "idle",
              "workspace_id": "w1"}}
    s["agents"].append(agent)
    save()
    reply({{"agent": agent}})
elif head == ["agent", "prompt"]:
    find(args[2])["agent_status"] = "working"
    s.setdefault("prompts", []).append(args[3])
    save()
    reply({{"agent": find(args[2])}})
elif head == ["agent", "get"]:
    agent = find(args[2])
    if agent is None:
        fail("agent_not_found")
    reply({{"agent": agent}})
elif head == ["agent", "send-keys"]:
    s["agents"] = [a for a in s["agents"] if a is not find(args[2])]
    save()
    reply({{}})
else:
    fail("unexpected", " ".join(args))
'''


class PaneStartTest(unittest.TestCase):
    """Items 3, 6 and 16 through `lanes.py start --launcher pane` with the fake herdr."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo, self.bin = self.temp / "repo", self.temp / "bin"
        for directory in (self.repo, self.bin):
            directory.mkdir()
        self.herdr_log = self.temp / "herdr.jsonl"
        self.herdr_state = self.temp / "herdr.json"
        self.write_herdr({"agents": []})
        fake = self.bin / "herdr"
        fake.write_text(FAKE_HERDR.format(python=sys.executable))
        fake.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "HERDR_", "CLAUDE_CODE_EFFORT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_HERDR_LOG=str(self.herdr_log), FAKE_HERDR_STATE=str(self.herdr_state),
                        HERDR_ENV="1", HERDR_WORKSPACE_ID="w1", HERDR_PANE_ID="w1:p1")
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Harness Test"),
                     ("config", "user.email", "harness@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True)
        (self.repo / "README").write_text("x\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, env=self.env,
                       check=True)
        subprocess.run([sys.executable, str(TASK_STATE), "init", "t1"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        self.run_dir = self.repo / ".harness" / "tasks" / "t1"
        self.brief = self.temp / "brief.md"
        self.brief.write_text("Do the unit.\n")
        self.report = self.temp / "reports" / "U9.md"

    def write_herdr(self, state):
        self.herdr_state.write_text(json.dumps(state))

    def herdr_calls(self):
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in self.herdr_log.read_text().splitlines()]

    def start(self, launcher="pane"):
        return subprocess.run(
            [sys.executable, str(LANES), "start", "--task", "t1", "--work", "U9", "--role",
             "reviewer", "--runtime", "codex", "--model", "model-x", "--effort", "high",
             "--brief", str(self.brief), "--report", str(self.report), "--checkout",
             str(self.repo), "--launcher", launcher, "--orchestrator", "claude"],
            cwd=self.repo, env=self.env, capture_output=True, text=True, timeout=60)

    def kinds(self):
        return [" ".join(c[:2]) for c in self.herdr_calls()]

    # --- item 3 ---------------------------------------------------------------------------

    def test_start_waits_for_the_new_panes_shell_before_agent_start(self):
        self.write_herdr({"agents": [], "new_busy": 3})
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        kinds = self.kinds()
        self.assertEqual(kinds.count("agent start"), 1, kinds)
        before = kinds[:kinds.index("agent start")]
        self.assertGreaterEqual(before.count("pane process-info"), 4, kinds)

    def test_an_agent_pane_busy_start_is_retried_once(self):
        self.write_herdr({"agents": [], "start_busy": 1})
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.kinds().count("agent start"), 2)

    def test_a_second_agent_pane_busy_fails_the_launch(self):
        self.write_herdr({"agents": [], "start_busy": 5})
        result = self.start()
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("not an available shell", result.stderr)
        self.assertEqual(self.kinds().count("agent start"), 2)

    def test_a_herdr_without_process_info_still_starts(self):
        self.write_herdr({"agents": [], "no_process_info": True})
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.kinds().count("pane process-info"), 1)

    # --- items 6 and 16 -------------------------------------------------------------------

    def test_the_pane_prompt_names_the_progress_and_flag_files(self):
        result = self.start()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        prompt = json.loads(self.herdr_state.read_text())["prompts"][0]
        self.assertIn(f"{self.report}.progress.md", prompt)
        self.assertIn(str(self.run_dir / "lanes" / "U9.flag.md"), prompt)
        self.assertIn("tripwire.py flag", prompt)
        self.assertIn("never type into another pane", prompt)

    def test_the_headless_prompt_keeps_the_channels_on_the_report_line(self):
        state = self.dispatch_state(self.start_headless())
        line = next(l for l in state["prompt"].splitlines() if "Write your full report to" in l)
        self.assertIn(f"{self.report}.progress.md", line)
        self.assertIn(str(self.run_dir / "lanes" / "U9.flag.md"), line)
        self.assertIn(f"{self.report}.flag.md", line)  # where the run directory is not writable
        self.assertIn("report_flag_before", state)

    def start_headless(self):
        for name in ("claude", "codex"):
            fake = self.bin / name
            fake.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n")
            fake.chmod(0o755)
        result = self.start("headless")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)["dispatch"]

    def dispatch_state(self, dispatch):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:  # let the supervisor end before cleanup
            subprocess.run([sys.executable, str(LANES), "status", "--task", "t1", "--dispatch",
                            dispatch], cwd=self.repo, env=self.env, capture_output=True)
            if any(json.loads(l).get("type") == "dispatch.end"
                   for l in (self.run_dir / "events.jsonl").read_text().splitlines() if l):
                break
            time.sleep(0.2)
        return json.loads((self.run_dir / "lanes" / f"{dispatch}.json").read_text())


class ShellReadinessTest(unittest.TestCase):
    """Item 3's bound and item 4's reset, with herdr replaced in-process."""

    def calls_with(self, answers):
        calls = []

        def herdr(*args):
            calls.append(list(args))
            answer = answers(list(args))
            if isinstance(answer, Exception):
                raise answer
            return answer
        return calls, herdr

    def test_await_shell_is_bounded(self):
        busy = {"process_info": {"shell_pid": 100, "foreground_process_group_id": 100,
                                 "foreground_processes": [{"pid": 100}, {"pid": 101}]}}
        calls, fake = self.calls_with(lambda args: busy)
        with patch.object(lanes, "herdr", fake), patch.object(lanes, "SHELL_READY_SECONDS", 0.3):
            began = time.monotonic()
            self.assertIs(lanes.await_shell("w1:p3"), False)
        self.assertLess(time.monotonic() - began, 2)
        self.assertGreater(len(calls), 1)

    def test_a_shell_that_never_reaches_its_prompt_is_not_started(self):
        busy = {"process_info": {"shell_pid": 100, "foreground_process_group_id": 100,
                                 "foreground_processes": [{"pid": 100}, {"pid": 101}]}}
        calls, fake = self.calls_with(lambda args: busy if args[1] == "process-info" else {})
        with patch.object(lanes, "herdr", fake), patch.object(lanes, "SHELL_READY_SECONDS", 0.3):
            with self.assertRaisesRegex(lanes.LaneError, "not an available shell"):
                lanes.start_agent("u9", "codex", "w1:p3", [])
        self.assertNotIn("agent start", [" ".join(c[:2]) for c in calls])

    def test_an_unavailable_shell_message_without_its_code_is_retried(self):
        ready = {"process_info": {"shell_pid": 100, "foreground_process_group_id": 100}}
        refusals = [lanes.LaneError("herdr agent start: agent target pane w1:p3 is not an "
                                    "available shell")]

        def answers(args):
            if args[:2] == ["agent", "start"] and refusals:
                return refusals.pop()
            return ready if args[1] == "process-info" else {"agent": {"name": "u9"}}
        calls, fake = self.calls_with(answers)
        with patch.object(lanes, "herdr", fake):
            self.assertEqual(lanes.start_agent("u9", "codex", "w1:p3", []), {"agent": {"name": "u9"}})
        self.assertEqual([" ".join(c[:2]) for c in calls].count("agent start"), 2)

    def test_another_start_failure_is_not_retried(self):
        ready = {"process_info": {"shell_pid": 100, "foreground_process_group_id": 100}}
        failure = lanes.LaneError("herdr agent start: agent_not_ready")
        failure.code = "agent_not_ready"
        calls, fake = self.calls_with(
            lambda args: failure if args[:2] == ["agent", "start"] else ready)
        with patch.object(lanes, "herdr", fake):
            with self.assertRaises(lanes.LaneError):
                lanes.start_agent("u9", "codex", "w1:p3", [])
        self.assertEqual([" ".join(c[:2]) for c in calls].count("agent start"), 1)

    def test_await_shell_without_process_info_cannot_tell(self):
        calls, fake = self.calls_with(lambda args: lanes.LaneError("herdr pane process-info: no"))
        with patch.object(lanes, "herdr", fake):
            self.assertIsNone(lanes.await_shell("w1:p3"))
        self.assertEqual(len(calls), 1)

    def test_a_reused_pane_is_reset_and_verified_before_cd(self):
        ready = {"process_info": {"shell_pid": 100, "foreground_process_group_id": 100}}
        calls, fake = self.calls_with(lambda args: ready if args[1] == "process-info" else {})
        with patch.object(lanes, "herdr", fake), patch.object(dashboard_herdr, "herdr", fake):
            dashboard_herdr.prepare("w1:p3", Path("/work"), {"A": "1"}, [])
        kinds = [" ".join(c[:2]) for c in calls]
        self.assertEqual(kinds[:3], ["pane send-text", "pane send-text", "pane wait-output"])
        self.assertEqual(calls[0][3], "\x03")
        reset = calls[1][3]
        self.assertIn("\\033[<99u", reset)
        self.assertIn("stty sane", reset)
        self.assertTrue(reset.endswith("\r"))
        token = calls[2][calls[2].index("--match") + 1]
        self.assertTrue(token.startswith(lanes.READY_MARK))
        self.assertNotIn(token, reset)  # the typed line itself can never match
        # What the shell echoes is the typed line; only printf's output joins mark and token.
        echoed = reset.rstrip("\r")
        self.assertNotIn(token, echoed)
        printed = subprocess.run(["sh", "-c", echoed.split("; ", 2)[2]], capture_output=True,
                                 text=True).stdout
        self.assertEqual(printed, token + "\n")
        self.assertEqual(kinds[-1], "pane run")
        self.assertIn("cd -- /work", calls[-1][3])

    def test_an_unverified_prompt_types_nothing_more(self):
        def answers(args):
            if args[1] == "wait-output":
                return lanes.LaneError("herdr pane wait-output: timed out waiting for output match")
            return {}
        calls, fake = self.calls_with(answers)
        with patch.object(lanes, "herdr", fake), patch.object(dashboard_herdr, "herdr", fake):
            with self.assertRaises(lanes.LaneError):
                dashboard_herdr.prepare("w1:p3", Path("/work"), {}, [])
        self.assertNotIn("pane run", [" ".join(c[:2]) for c in calls])


class DerivedProfileTest(unittest.TestCase):
    """Item 15: the profile the shadow router gets when --profile is absent."""

    def test_documented_mapping(self):
        for role, tier, profile in [("implementer", 0, "routine"), ("implementer", 1, "routine"),
                                    ("implementer", 2, "implementation"),
                                    ("implementer", 3, "critical-review"),
                                    ("implementer", None, "implementation"),
                                    ("simplifier", 1, "critical-review"),
                                    ("qa-verifier", 2, "routine"),
                                    ("qa-verifier", 3, "critical-review"),
                                    ("investigator", 3, "deep-reasoning"),
                                    ("security-auditor", None, "critical-review"),
                                    ("reviewer", 3, "critical-review"),
                                    ("reviewer", 2, None)]:
            with self.subTest(role=role, tier=tier):
                self.assertEqual(lanes.derive_profile(role, tier), profile)

    def test_start_passes_the_derived_profile_to_the_shadow_router(self):
        import argparse
        import router_shadow
        seen = {}

        def evaluate(root, run, primary, **fields):
            seen.update(fields)
            return {"mode": "shadow", "status": "skipped"}
        args = argparse.Namespace(task="t1", runtime="codex", model="m", effort="high",
                                  role="implementer", tier=2, profile=None, brief=None)
        with patch.object(lanes.project, "repo_root", return_value=Path("/repo")), \
                patch.object(router_shadow, "mode", return_value="shadow"), \
                patch.object(router_shadow, "evaluate", evaluate):
            lanes.shadow_for_args(args, "lanes.start", primary=Path("/p"), run=Path("/r"),
                                  emit=False)
        self.assertEqual(seen["profile"], "implementation")

    def test_an_explicit_profile_wins(self):
        import argparse
        import router_shadow
        seen = {}

        def evaluate(root, run, primary, **fields):
            seen.update(fields)
            return {}
        args = argparse.Namespace(task="t1", runtime="codex", model="m", effort="high",
                                  role="implementer", tier=2, profile="escalation", brief=None)
        with patch.object(lanes.project, "repo_root", return_value=Path("/repo")), \
                patch.object(router_shadow, "mode", return_value="shadow"), \
                patch.object(router_shadow, "evaluate", evaluate):
            lanes.shadow_for_args(args, "lanes.start", primary=Path("/p"), run=Path("/r"),
                                  emit=False)
        self.assertEqual(seen["profile"], "escalation")


if __name__ == "__main__":
    unittest.main()
