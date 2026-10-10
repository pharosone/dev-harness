"""`lanes.py start/status/wait/result/cancel` against fake `claude`, `codex` and `herdr` on PATH.

The real CLIs are never called: the fakes come first on PATH and record their argv. Every
launcher writes `dispatch.start`, then exactly one `dispatch.end`, to the run's events log.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

CORE = Path(__file__).resolve().parents[1]
LANES = CORE / "scripts" / "lanes.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
sys.path.insert(0, str(CORE / "scripts"))
import lanes  # noqa: E402

FAKE_CLI = """#!{python}
import json, os, sys, time
name = os.path.basename(sys.argv[0])
prompt = sys.stdin.read()
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({{"argv": [name, *sys.argv[1:]], "pid": os.getpid(), "prompt": prompt,
                          "effort": os.environ.get("CLAUDE_CODE_EFFORT_LEVEL")}}) + "\\n")
def emit(value):
    print(json.dumps(value), flush=True)
silent = os.environ.get("FAKE_SILENT") == "1"
if not silent:
    if name == "claude":
        emit({{"type": "system", "subtype": "init"}})
        emit({{"type": "assistant", "message": {{"content": [
            {{"type": "tool_use", "name": "Read"}}, {{"type": "tool_use", "name": "Bash"}}]}}}})
    else:
        emit({{"type": "thread.started"}})
        emit({{"type": "item.completed", "item": {{"type": "command_execution"}}}})
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")))
if not silent:
    if name == "claude":
        emit({{"type": "result", "subtype": "success", "result": "fake claude report",
               "duration_ms": 4000, "usage": {{"input_tokens": 100, "output_tokens": 20}}}})
    else:
        emit({{"type": "turn.completed", "usage": {{"input_tokens": 70, "output_tokens": 7}}}})
        out = sys.argv[sys.argv.index("--output-last-message") + 1]
        with open(out, "w", encoding="utf-8") as stream:
            stream.write("fake codex report\\n")
sys.exit(int(os.environ.get("FAKE_EXIT", "0")))
"""

FAKE_HERDR = """#!{python}
import json, os, sys
args = sys.argv[1:]
state_path = os.environ["FAKE_HERDR_STATE"]
with open(state_path, encoding="utf-8") as stream:
    state = json.load(stream)
with open(os.environ["FAKE_HERDR_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(args) + "\\n")
def save():
    with open(state_path, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
def reply(result):
    print(json.dumps({{"id": "cli", "result": result}}))
def new_pane():
    state["next"] = state.get("next", 2) + 1
    pane = "w1:p%d" % (state["next"] - 1)
    state.setdefault("panes", []).append(pane)
    return pane
def find(name):
    return next((a for a in state["agents"] if a.get("name") == name or a["pane_id"] == name), None)
head = args[:2]
if head == ["agent", "list"]:
    reply({{"agents": state["agents"]}})
elif head == ["pane", "split"]:
    pane = new_pane()
    save()
    reply({{"pane": {{"pane_id": pane}}}})
elif head == ["tab", "create"]:
    pane = new_pane()
    state["next_tab"] = state.get("next_tab", 2) + 1
    save()
    reply({{"tab": {{"tab_id": "w1:t%d" % (state["next_tab"] - 1)}}, "root_pane": {{"pane_id": pane}}}})
elif head == ["agent", "start"]:
    name, kind, pane = args[2], args[args.index("--kind") + 1], args[args.index("--pane") + 1]
    agent = {{"name": name, "agent": kind, "pane_id": pane, "agent_status": "idle",
              "workspace_id": "w1"}}
    state["agents"].append(agent)
    save()
    reply({{"agent": agent}})
elif head == ["agent", "prompt"]:
    agent = find(args[2])
    agent["agent_status"] = "working"
    save()
    reply({{"agent": agent}})
elif head == ["agent", "get"]:
    agent = find(args[2])
    if agent is None:
        print(json.dumps({{"error": {{"code": "agent_not_found", "message": "not found"}}}}),
              file=sys.stderr)
        sys.exit(1)
    reply({{"agent": agent}})
elif head == ["agent", "send-keys"]:
    state["agents"] = [a for a in state["agents"] if a is not find(args[2])]
    save()
    reply({{}})
else:
    print(json.dumps({{"error": {{"code": "unexpected", "message": " ".join(args)}}}}),
          file=sys.stderr)
    sys.exit(2)
"""

FOREIGN = [{"name": "someone-else", "agent": "codex", "pane_id": "w1:p1",
            "agent_status": "working", "workspace_id": "w1"},
           {"agent": "claude", "pane_id": "w1:p9", "agent_status": "idle",
            "workspace_id": "w1"}]
SPEC_START = {"dispatch", "role", "runtime", "model", "effort", "launcher", "work", "report",
              "pane"}
ENVELOPE = {"v", "id", "at", "type"}
ENDED = ("done", "failed", "timeout", "cancelled")


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # a zombie still answers kill(0); it is not running
        status = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True,
                                text=True).stdout.strip()
    except OSError:
        return True
    return bool(status) and not status.startswith("Z")


class LaunchTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo = self.temp / "repo"
        self.bin = self.temp / "bin"
        for directory in (self.repo, self.bin):
            directory.mkdir()
        self.log = self.temp / "calls.jsonl"
        self.herdr_log = self.temp / "herdr.jsonl"
        self.herdr_state = self.temp / "herdr.json"
        self.herdr_state.write_text(json.dumps({"agents": []}))
        for name in ("claude", "codex"):
            fake = self.bin / name
            fake.write_text(FAKE_CLI.format(python=sys.executable))
            fake.chmod(0o755)
        fake = self.bin / "herdr"
        fake.write_text(FAKE_HERDR.format(python=sys.executable))
        fake.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "HERDR_", "CLAUDE_CODE_EFFORT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_LOG=str(self.log), FAKE_HERDR_LOG=str(self.herdr_log),
                        FAKE_HERDR_STATE=str(self.herdr_state))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "README").write_text("x\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        subprocess.run([sys.executable, str(TASK_STATE), "init", "t1"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        self.run_dir = self.repo / ".harness" / "tasks" / "t1"
        self.brief = self.temp / "brief.md"
        self.brief.write_text("Do the unit.\n")
        self.started: list[str] = []
        self.addCleanup(self.cleanup)

    def cleanup(self):
        for dispatch in self.started:
            self.lanes("cancel", "--task", "t1", "--dispatch", dispatch)

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.repo, env=self.env,
                                       text=True)

    def herdr_env(self, **extra):
        return {"HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "w1", "HERDR_PANE_ID": "w1:p1", **extra}

    def lanes(self, *args, cwd=None, **env):
        return subprocess.run([sys.executable, str(LANES), *args], cwd=cwd or self.repo,
                              env={**self.env, **env}, capture_output=True, text=True,
                              timeout=60)

    def start(self, *extra, runtime="claude", role="reviewer", work="U9", report=None,
              orchestrator="claude", minutes="5", env=None, expect=0):
        report = report or self.temp / "reports" / f"{work}-{role}.md"
        result = self.lanes(
            "start", "--task", "t1", "--work", work, "--role", role, "--runtime", runtime,
            "--model", "model-x", "--effort", "high", "--brief", str(self.brief),
            "--report", str(report), "--checkout", str(self.repo), "--minutes", minutes,
            "--orchestrator", orchestrator, *extra, **(env or {}))
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        if expect != 0:
            return result
        report_json = json.loads(result.stdout)
        self.assertEqual(set(report_json), {"dispatch", "launcher", "reason", "pane"})
        self.started.append(report_json["dispatch"])
        return report_json

    def wait(self, dispatch, *extra, env=None, expect=0):
        result = self.lanes("wait", "--task", "t1", "--dispatch", dispatch, "--timeout", "30",
                            *extra, **(env or {}))
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def status(self, dispatch, *extra, env=None):
        result = self.lanes("status", "--task", "t1", "--dispatch", dispatch, *extra,
                            **(env or {}))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def events(self, dispatch=None):
        path = self.run_dir / "events.jsonl"
        if not path.exists():
            return []
        found = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return [e for e in found if dispatch is None or e.get("dispatch") == dispatch]

    def calls(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def herdr_calls(self):
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in self.herdr_log.read_text().splitlines()]

    def state_file(self, dispatch):
        return json.loads((self.run_dir / "lanes" / f"{dispatch}.json").read_text())

    def set_agent(self, name, status):
        state = json.loads(self.herdr_state.read_text())
        for agent in state["agents"]:
            if agent.get("name") == name:
                agent["agent_status"] = status
        self.herdr_state.write_text(json.dumps(state))

    def assert_start_then_one_end(self, dispatch, status):
        events = self.events(dispatch)
        self.assertEqual([e["type"] for e in events], ["dispatch.start", "dispatch.end"], events)
        start, end = events
        self.assertEqual(set(start) - ENVELOPE, SPEC_START)
        self.assertEqual(set(end) - ENVELOPE, {"dispatch", "status", "usage"})
        self.assertEqual(end["status"], status)
        return start, end

    # --- headless ---------------------------------------------------------------------

    def test_headless_claude_returns_at_once_and_records_start_then_end(self):
        began = time.monotonic()
        out = self.start("--launcher", "headless", env={"FAKE_SLEEP": "2"})
        self.assertLess(time.monotonic() - began, 1.8, "start must not wait for the worker")
        self.assertEqual(out["launcher"], "headless")
        self.assertIsNone(out["pane"])
        self.assertEqual(self.status(out["dispatch"])["state"], "running")
        final = self.wait(out["dispatch"])
        self.assertEqual(final["state"], "done")
        start, end = self.assert_start_then_one_end(out["dispatch"], "done")
        self.assertEqual((start["launcher"], start["runtime"], start["model"], start["effort"],
                          start["role"], start["work"], start["pane"]),
                         ("headless", "claude", "model-x", "high", "reviewer", "U9", None))
        self.assertEqual(end["usage"], {"tool_calls": 2, "input_tokens": 100,
                                        "output_tokens": 20, "duration_s": 4.0})
        (call,) = self.calls()
        argv = call["argv"]
        self.assertEqual(argv[:5], ["claude", "-p", "--output-format", "stream-json", "--verbose"])
        self.assertEqual(argv[argv.index("--model") + 1], "model-x")
        self.assertEqual(call["effort"], "high")
        self.assertIn("Do the unit.", call["prompt"])
        self.assertIn(start["report"], call["prompt"])
        log = self.run_dir / "lanes" / f"{out['dispatch']}.log"
        self.assertIn('"type": "result"', log.read_text())
        state = self.state_file(out["dispatch"])
        self.assertIsInstance(state["pid"], int)
        self.assertTrue(state["started"])
        self.assertTrue(state["last_activity"])
        self.assertEqual(Path(start["report"]).read_text().strip(), "fake claude report")
        result = json.loads(self.lanes("result", "--task", "t1", "--dispatch",
                                       out["dispatch"]).stdout)
        self.assertEqual(result["report"], start["report"])
        self.assertEqual(result["usage"], end["usage"])

    def test_headless_codex_carries_the_guard_and_writes_the_last_message(self):
        out = self.start("--launcher", "headless", runtime="codex")
        self.wait(out["dispatch"])
        start, end = self.assert_start_then_one_end(out["dispatch"], "done")
        self.assertEqual(end["usage"]["input_tokens"], 70)
        self.assertEqual(end["usage"]["output_tokens"], 7)
        self.assertEqual(end["usage"]["tool_calls"], 1)
        (call,) = self.calls()
        argv = call["argv"]
        self.assertEqual(argv[:3], ["codex", "exec", "--json"])
        self.assertEqual(argv[argv.index("-m") + 1], "model-x")
        self.assertIn("model_reasoning_effort=high", argv)
        hooks = [a for a in argv if a.startswith("hooks.PreToolUse=")]
        self.assertEqual(len(hooks), 1)
        self.assertIn(str(CORE / "scripts" / "guard.py"), hooks[0])
        self.assertEqual(argv[argv.index("--output-last-message") + 1], start["report"])
        self.assertEqual(Path(start["report"]).read_text(), "fake codex report\n")

    def test_a_failing_cli_ends_failed(self):
        out = self.start("--launcher", "headless", env={"FAKE_EXIT": "3"})
        self.assertEqual(self.wait(out["dispatch"])["state"], "failed")
        self.assert_start_then_one_end(out["dispatch"], "failed")

    def test_timeout_ends_timeout_and_stops_the_cli(self):
        out = self.start("--launcher", "headless", "--timeout", "1", env={"FAKE_SLEEP": "30"})
        self.assertEqual(self.wait(out["dispatch"])["state"], "timeout")
        self.assert_start_then_one_end(out["dispatch"], "timeout")
        (call,) = self.calls()
        time.sleep(0.3)
        self.assertFalse(alive(call["pid"]), "the timed-out CLI is still running")

    def test_cancel_ends_cancelled_and_terminates_the_process_group(self):
        out = self.start("--launcher", "headless", env={"FAKE_SLEEP": "30"})
        for _ in range(50):
            if self.calls():
                break
            time.sleep(0.1)
        (call,) = self.calls()
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "cancelled")
        time.sleep(0.5)
        self.assertFalse(alive(call["pid"]), "the cancelled CLI is still running")
        self.assertFalse(alive(self.state_file(out["dispatch"])["pid"]))
        self.assert_start_then_one_end(out["dispatch"], "cancelled")
        self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"])
        self.assertEqual(self.status(out["dispatch"])["state"], "cancelled")
        self.assert_start_then_one_end(out["dispatch"], "cancelled")

    def test_a_silent_worker_goes_stale_never_failed(self):
        out = self.start("--launcher", "headless", env={"FAKE_SLEEP": "30", "FAKE_SILENT": "1"})
        time.sleep(1.5)
        report = self.status(out["dispatch"], "--stale-minutes", "0.01")
        self.assertEqual(report["state"], "stale")
        self.assertIn("liveness unknown", report["reason"])
        self.assertEqual([e["type"] for e in self.events(out["dispatch"])], ["dispatch.start"])
        self.assertEqual(self.status(out["dispatch"])["state"], "running")

    def test_duplicate_status_calls_never_write_a_second_end(self):
        out = self.start("--launcher", "headless")
        self.wait(out["dispatch"])
        for _ in range(3):
            self.assertEqual(self.status(out["dispatch"])["state"], "done")
            self.wait(out["dispatch"])
        self.assert_start_then_one_end(out["dispatch"], "done")

    def test_a_supervisor_killed_without_an_end_is_ended_once_by_status(self):
        out = self.start("--launcher", "headless", env={"FAKE_SLEEP": "30"})
        for _ in range(50):
            if self.calls():
                break
            time.sleep(0.1)
        state = self.state_file(out["dispatch"])
        os.killpg(state["pid"], signal.SIGKILL)
        time.sleep(0.5)
        for _ in range(3):
            self.assertEqual(self.status(out["dispatch"])["state"], "failed")
        self.assert_start_then_one_end(out["dispatch"], "failed")

    def test_status_without_dispatch_lists_every_dispatch(self):
        first = self.start("--launcher", "headless")
        second = self.start("--launcher", "headless", work="U8", runtime="codex")
        self.wait(first["dispatch"])
        self.wait(second["dispatch"])
        result = self.lanes("status", "--task", "t1")
        listed = {d["dispatch"]: d["state"] for d in json.loads(result.stdout)}
        self.assertEqual(listed, {first["dispatch"]: "done", second["dispatch"]: "done"})

    # --- subagent and decisions -------------------------------------------------------

    def test_a_subagent_decision_is_not_launched(self):
        result = self.start(expect=3)
        report = json.loads(result.stdout)
        self.assertEqual(report["launcher"], "subagent")
        self.assertIsNone(report["pane"])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events(), [])

    def test_the_decision_picks_headless_for_another_runtime(self):
        out = self.start(runtime="codex")
        self.assertEqual(out["launcher"], "headless")
        self.wait(out["dispatch"])
        self.assert_start_then_one_end(out["dispatch"], "done")

    def test_a_worktree_of_the_same_repository_is_not_another_repository(self):
        worktree = self.temp / "wt"
        self.git("worktree", "add", "-q", str(worktree), "-b", "lane")
        other = self.temp / "other"
        other.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=other, check=True, env=self.env)
        self.assertIsNone(lanes.other_repository(str(worktree), self.repo))
        self.assertIsNone(lanes.other_repository(str(self.repo), worktree))
        self.assertEqual(lanes.other_repository(str(other), self.repo), str(other.resolve()))
        decided = self.lanes("decide", "--role", "implementer", "--runtime", "claude",
                             "--orchestrator", "claude", "--minutes", "5", "--repo",
                             str(worktree), HERDR_ENV="1")
        self.assertEqual(json.loads(decided.stdout)["launcher"], "subagent", decided.stderr)
        result = self.lanes(
            "start", "--task", "t1", "--work", "U9", "--role", "reviewer", "--runtime",
            "claude", "--model", "m", "--effort", "e", "--brief", str(self.brief), "--report",
            str(self.temp / "r.md"), "--checkout", str(worktree), "--minutes", "5",
            "--orchestrator", "claude", **self.herdr_env())
        self.assertEqual(result.returncode, 3, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["launcher"], "subagent")

    # --- herdr panes ------------------------------------------------------------------

    def test_pane_launch_and_headless_emit_equivalent_events(self):
        pane = self.start("--launcher", "pane", env=self.herdr_env())
        self.assertEqual(pane["launcher"], "pane")
        name = pane["pane"]
        self.assertTrue(name)
        calls = self.herdr_calls()
        split = next(c for c in calls if c[:2] == ["pane", "split"])
        self.assertIn("--no-focus", split)
        self.assertEqual(split[split.index("--cwd") + 1], str(self.repo))
        started = next(c for c in calls if c[:2] == ["agent", "start"])
        self.assertEqual(started[2], name)
        self.assertEqual(started[started.index("--kind") + 1], "claude")
        self.assertEqual(started[started.index("--pane") + 1], "w1:p2")
        self.assertIn("--", started)
        passed = started[started.index("--") + 1:]
        self.assertEqual(passed[passed.index("--model") + 1], "model-x")
        prompted = next(c for c in calls if c[:2] == ["agent", "prompt"])
        self.assertEqual(prompted[2], name)
        report = self.temp / "reports" / "U9-reviewer.md"
        self.assertIn(str(self.brief), prompted[3])
        self.assertIn(str(report), prompted[3])
        self.assertEqual(self.status(pane["dispatch"], env=self.herdr_env())["state"], "running")
        self.assertEqual(self.state_file(pane["dispatch"])["pane_name"], name)
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("pane report\n")
        self.set_agent(name, "idle")
        self.assertEqual(self.wait(pane["dispatch"], env=self.herdr_env())["state"], "done")
        pane_start, pane_end = self.assert_start_then_one_end(pane["dispatch"], "done")
        self.assertEqual(pane_start["pane"], name)
        self.assertIsNone(pane_end["usage"])

        headless = self.start("--launcher", "headless", work="U8")
        self.wait(headless["dispatch"])
        head_start, head_end = self.assert_start_then_one_end(headless["dispatch"], "done")
        self.assertEqual(set(pane_start), set(head_start))
        self.assertEqual(set(pane_end), set(head_end))
        self.assertEqual(self.calls()[0]["argv"][0], "claude")  # only the headless one ran a CLI

    def test_codex_pane_carries_the_guard(self):
        out = self.start("--launcher", "pane", runtime="codex", env=self.herdr_env())
        started = next(c for c in self.herdr_calls() if c[:2] == ["agent", "start"])
        self.assertEqual(started[started.index("--kind") + 1], "codex")
        passed = started[started.index("--") + 1:]
        self.assertEqual(passed[passed.index("-m") + 1], "model-x")
        self.assertIn("model_reasoning_effort=high", passed)
        hooks = [a for a in passed if a.startswith("hooks.PreToolUse=")]
        self.assertEqual(len(hooks), 1)
        self.assertIn(str(CORE / "scripts" / "guard.py"), hooks[0])
        self.assertEqual(self.calls(), [])
        self.assertEqual(out["launcher"], "pane")

    def test_a_blocked_agent_is_surfaced_and_never_answered(self):
        out = self.start("--launcher", "pane", env=self.herdr_env())
        self.set_agent(out["pane"], "blocked")
        before = len(self.herdr_calls())
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "blocked")
        self.assertEqual(self.wait(out["dispatch"], env=self.herdr_env(), expect=5)["state"],
                         "blocked")
        answered = [c for c in self.herdr_calls()[before:]
                    if c[:2] in (["agent", "prompt"], ["agent", "send-keys"],
                                 ["pane", "send-keys"], ["pane", "send-text"])]
        self.assertEqual(answered, [])
        self.assertEqual([e["type"] for e in self.events(out["dispatch"])], ["dispatch.start"])

    def test_over_three_live_agents_report_only_work_runs_headless(self):
        busy = [{"name": f"busy{i}", "agent": "claude", "pane_id": f"w1:p{20 + i}",
                 "agent_status": "working", "workspace_id": "w1"} for i in range(3)]
        self.herdr_state.write_text(json.dumps({"agents": busy}))
        out = self.start(minutes="30", env=self.herdr_env())
        self.assertEqual(out["launcher"], "headless")
        self.assertIn("3", out["reason"])
        self.assertFalse([c for c in self.herdr_calls() if c[:2] != ["agent", "list"]])
        self.wait(out["dispatch"])
        self.assert_start_then_one_end(out["dispatch"], "done")

    def test_over_three_live_agents_writer_work_is_queued(self):
        busy = [{"name": f"busy{i}", "agent": "claude", "pane_id": f"w1:p{20 + i}",
                 "agent_status": "idle", "workspace_id": "w1"} for i in range(3)]
        self.herdr_state.write_text(json.dumps({"agents": busy}))
        result = self.start("--launcher", "pane", role="implementer", env=self.herdr_env(),
                            expect=4)
        self.assertIn("queued: ", result.stdout + result.stderr)
        self.assertFalse([c for c in self.herdr_calls() if c[:2] != ["agent", "list"]])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events(), [])

    def test_the_second_live_pane_opens_a_tab_named_after_the_task(self):
        first = self.start("--launcher", "pane", env=self.herdr_env())
        second = self.start("--launcher", "pane", work="U8", env=self.herdr_env())
        calls = self.herdr_calls()
        tabs = [c for c in calls if c[:2] == ["tab", "create"]]
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0][tabs[0].index("--label") + 1], "t1")
        self.assertIn("--no-focus", tabs[0])
        starts = [c for c in calls if c[:2] == ["agent", "start"]]
        self.assertEqual([c[c.index("--pane") + 1] for c in starts], ["w1:p2", "w1:p3"])
        self.assertNotEqual(first["pane"], second["pane"])

    def test_foreign_panes_are_never_closed_or_reused(self):
        self.herdr_state.write_text(json.dumps({"agents": FOREIGN}))
        out = self.start("--launcher", "pane", env=self.herdr_env())
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"],
                            **self.herdr_env())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "cancelled")
        for call in self.herdr_calls():
            self.assertNotIn("close", call)
            self.assertNotIn("w1:p9", call)
            self.assertNotIn("someone-else", call)
            if call[:2] in (["agent", "send-keys"], ["agent", "prompt"], ["agent", "get"]):
                self.assertEqual(call[2], out["pane"])
        names = {a.get("name") for a in json.loads(self.herdr_state.read_text())["agents"]}
        self.assertIn("someone-else", names)
        self.assert_start_then_one_end(out["dispatch"], "cancelled")
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "cancelled")
        self.assert_start_then_one_end(out["dispatch"], "cancelled")

    def test_pane_without_herdr_is_refused(self):
        result = self.start("--launcher", "pane", expect=2)
        self.assertIn("HERDR_ENV", result.stderr)
        self.assertEqual(self.herdr_calls(), [])
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
