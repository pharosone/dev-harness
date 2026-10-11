"""The herdr agent limit from the overlay and the run's herdr dashboard tab (v1.3.0).

A fake `herdr` first on PATH records every call and keeps a small model of tabs, panes (with
their rectangles, labels and whether the shell is idle) and agents, answering with the JSON
shapes herdr 0.9 prints. The real herdr is never called.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
LANES = CORE / "scripts" / "lanes.py"
VIEWS = CORE / "scripts" / "dashboard_herdr.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
sys.path.insert(0, str(CORE / "scripts"))
import dashboard_herdr  # noqa: E402

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
def opt(name, default=None):
    return args[args.index(name) + 1] if name in args else default
def info(pid):
    p = s["panes"][pid]
    return {{"pane_id": pid, "tab_id": p["tab_id"], "workspace_id": "w1", "label": p.get("label"),
             "cwd": p.get("cwd"), "agent_status": "unknown"}}
def new_pane(tab, rect, cwd):
    s["next"] = s.get("next", 10) + 1
    pid = "w1:p%d" % s["next"]
    s["panes"][pid] = {{"tab_id": tab, "rect": rect, "idle": True, "cwd": cwd}}
    return pid
def split(target, direction, ratio):
    r = dict(s["panes"][target]["rect"])
    keep = dict(r)
    new = dict(r)
    if direction == "right":
        keep["width"] = r["width"] * ratio
        new["x"], new["width"] = r["x"] + keep["width"], r["width"] - keep["width"]
    else:
        keep["height"] = r["height"] * ratio
        new["y"], new["height"] = r["y"] + keep["height"], r["height"] - keep["height"]
    s["panes"][target]["rect"] = keep
    return new
def find(name):
    return next((a for a in s["agents"] if a.get("name") == name or a["pane_id"] == name), None)
def drop_empty_tab(tab):
    if not any(p["tab_id"] == tab for p in s["panes"].values()):
        s["tabs"].pop(tab, None)
        return tab
    return None
head = args[:2]
if head == ["agent", "list"]:
    reply({{"agents": s["agents"]}})
elif head == ["agent", "start"]:
    name, kind, pane = args[2], opt("--kind"), opt("--pane")
    if pane not in s["panes"] or not s["panes"][pane]["idle"]:
        fail("pane_not_ready", "pane is not at a shell prompt")
    agent = {{"name": name, "agent": kind, "pane_id": pane, "agent_status": "idle",
              "workspace_id": "w1"}}
    s["agents"].append(agent)
    s["panes"][pane]["idle"] = False
    save()
    reply({{"agent": agent}})
elif head == ["agent", "prompt"]:
    find(args[2])["agent_status"] = "working"
    save()
    reply({{"agent": find(args[2])}})
elif head == ["agent", "get"]:
    agent = find(args[2])
    if agent is None:
        fail("agent_not_found")
    reply({{"agent": agent}})
elif head == ["agent", "send-keys"]:
    agent = find(args[2])
    s["agents"] = [a for a in s["agents"] if a is not agent]
    if agent and agent["pane_id"] in s["panes"]:
        s["panes"][agent["pane_id"]]["idle"] = True  # two ctrl+c leave the agent
    save()
    reply({{}})
elif head == ["tab", "create"]:
    s["next_tab"] = s.get("next_tab", 1) + 1
    tab = "w1:t%d" % s["next_tab"]
    s["tabs"][tab] = {{"label": opt("--label")}}
    pane = new_pane(tab, {{"x": 0, "y": 0, "width": 180.0, "height": 40.0}}, opt("--cwd"))
    save()
    reply({{"tab": {{"tab_id": tab, "label": opt("--label")}}, "root_pane": info(pane),
            "type": "tab_created"}})
elif head == ["tab", "get"]:
    tab = args[2]
    if tab not in s["tabs"]:
        fail("tab_not_found")
    reply({{"tab": {{"tab_id": tab, **s["tabs"][tab]}}}})
elif head == ["pane", "split"]:
    target = os.environ.get("HERDR_PANE_ID") if "--current" in args else args[2]
    if target not in s["panes"]:
        fail("pane_not_found", target)
    rect = split(target, opt("--direction"), float(opt("--ratio", "0.5")))
    pane = new_pane(s["panes"][target]["tab_id"], rect, opt("--cwd"))
    s["panes"][pane]["env"] = [a for a in args if "=" in a and args[args.index(a) - 1] == "--env"]
    save()
    reply({{"pane": info(pane), "type": "pane_info"}})
elif head == ["pane", "list"]:
    reply({{"panes": [info(p) for p in s["panes"]]}})
elif head == ["pane", "rename"]:
    s["panes"][args[2]]["label"] = " ".join(args[3:])
    save()
    reply({{"pane": info(args[2])}})
elif head == ["pane", "run"]:
    command = " ".join(args[3:])
    s["panes"][args[2]].setdefault("ran", []).append(command)
    if not command.startswith(("unset ", "cd -- ")):
        s["panes"][args[2]]["idle"] = False  # a view or a project command keeps running
    save()
elif head == ["pane", "send-text"]:
    s["panes"][args[2]].setdefault("typed", []).append(args[3])
    save()
    reply({{}})
elif head == ["pane", "wait-output"]:
    if s["panes"][args[2]].get("garbled"):  # the reset's mark never comes back
        fail("timeout", "timed out waiting for output match")
    reply({{"matched_line": opt("--match")}})
elif head == ["pane", "process-info"]:
    pane = s["panes"][opt("--pane")]
    reply({{"process_info": {{"pane_id": opt("--pane"), "shell_pid": 100,
                              "foreground_process_group_id": 100 if pane["idle"] else 200}}}})
elif head == ["pane", "layout"]:
    tab = s["panes"][opt("--pane")]["tab_id"]
    reply({{"layout": {{"panes": [{{"pane_id": p, "rect": v["rect"]}}
                                  for p, v in s["panes"].items() if v["tab_id"] == tab]}}}})
elif head == ["pane", "resize"]:
    reply({{"resize": {{"changed": True}}}})
elif head == ["pane", "move"]:
    pane, tab, target = args[2], opt("--tab"), opt("--target-pane")
    old = s["panes"][pane]["tab_id"]
    s["panes"][pane]["rect"] = split(target, opt("--split"), float(opt("--ratio", "0.5")))
    s["panes"][pane]["tab_id"] = tab
    closed = drop_empty_tab(old)
    save()
    reply({{"move_result": {{"changed": True, "closed_tab_id": closed, "pane": info(pane)}}}})
elif head == ["pane", "close"]:
    pane = args[2]
    if pane not in s["panes"]:
        fail("pane_not_found")
    tab = s["panes"].pop(pane)["tab_id"]
    drop_empty_tab(tab)
    save()
    reply({{"type": "ok"}})
else:
    fail("unexpected", " ".join(args))
'''

# A headless lane's CLI: reads its prompt and ends at once (the real CLIs are never called).
FAKE_CLI = """#!{python}
import sys
sys.stdin.read()
"""

CALLER = {"tab_id": "w1:t1", "rect": {"x": 0, "y": 0, "width": 180.0, "height": 40.0},
          "idle": False, "cwd": "/caller"}


class HerdrDashboardTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo, self.bin = self.temp / "repo", self.temp / "bin"
        for directory in (self.repo, self.bin):
            directory.mkdir()
        self.herdr_log = self.temp / "herdr.jsonl"
        self.herdr_state = self.temp / "herdr.json"
        self.write_herdr({"agents": [], "tabs": {"w1:t1": {"label": "1"}},
                          "panes": {"w1:p1": dict(CALLER)}})
        for name, text in (("herdr", FAKE_HERDR), ("claude", FAKE_CLI), ("codex", FAKE_CLI)):
            fake = self.bin / name
            fake.write_text(text.format(python=sys.executable))
            fake.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "HERDR_", "CLAUDE_CODE_EFFORT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_HERDR_LOG=str(self.herdr_log), FAKE_HERDR_STATE=str(self.herdr_state))
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
        self.brief = self.temp / "brief.md"
        self.brief.write_text("Do the unit.\n")

    # --- helpers ------------------------------------------------------------------------

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True)

    def write_herdr(self, state):
        self.herdr_state.write_text(json.dumps(state))

    def herdr_model(self):
        return json.loads(self.herdr_state.read_text())

    def herdr_calls(self):
        if not self.herdr_log.exists():
            return []
        return [json.loads(line) for line in self.herdr_log.read_text().splitlines()]

    def herdr_env(self):
        return {"HERDR_ENV": "1", "HERDR_WORKSPACE_ID": "w1", "HERDR_PANE_ID": "w1:p1"}

    def overlay(self, data):
        folder = self.repo / "harness" / "project"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "project.json").write_text(json.dumps(data))

    def run_script(self, script, *args, herdr=True):
        env = {**self.env, **(self.herdr_env() if herdr else {})}
        return subprocess.run([sys.executable, str(script), *args], cwd=self.repo, env=env,
                              capture_output=True, text=True, timeout=60)

    def dashboard(self, *flags, herdr=True, expect=0):
        result = self.run_script(LANES, "dashboard", "--task", "t1", *flags, herdr=herdr)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def start(self, work="U9", role="reviewer", runtime="claude", expect=0, extra=()):
        result = self.run_script(
            LANES, "start", "--task", "t1", "--work", work, "--role", role, "--runtime", runtime,
            "--model", "model-x", "--effort", "high", "--brief", str(self.brief), "--report",
            str(self.temp / "reports" / f"{work}.md"), "--checkout", str(self.repo),
            "--minutes", "30", "--orchestrator", "claude", *extra)
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        if expect != 0:
            return result
        out = json.loads(result.stdout)
        if out["launcher"] == "headless":  # let its supervisor end before the cleanup
            waited = self.run_script(LANES, "wait", "--task", "t1", "--dispatch",
                                     out["dispatch"], "--timeout", "30")
            self.assertEqual(waited.returncode, 0, waited.stdout + waited.stderr)
        return out

    def end(self, out, exit_agent=True):
        """End a pane lane done (report written, agent idle); exit_agent leaves the shell idle."""
        state = json.loads((self.run_dir / "lanes" / f"{out['dispatch']}.json").read_text())
        Path(state["report"]).write_text("report\n")
        model = self.herdr_model()
        for agent in model["agents"]:
            if agent["name"] == out["pane"]:
                agent["agent_status"] = "idle"
        if exit_agent:
            model["agents"] = [a for a in model["agents"] if a["name"] != out["pane"]]
            model["panes"][state["pane_id"]]["idle"] = True
        self.write_herdr(model)
        result = self.run_script(LANES, "status", "--task", "t1", "--dispatch", out["dispatch"])
        self.assertEqual(json.loads(result.stdout)["state"], "done", result.stderr)
        return state["pane_id"]

    def record(self):
        return json.loads((self.run_dir / "lanes" / "_herdr.json").read_text())

    def board(self):
        return self.record()["dashboard"]

    def pane_of(self, dispatch):
        return json.loads((self.run_dir / "lanes" / f"{dispatch}.json").read_text())["pane_id"]

    def busy_agents(self, count, status="working"):
        model = self.herdr_model()
        model["agents"] = [{"name": f"busy{i}", "agent": "claude", "pane_id": f"w1:p{50 + i}",
                            "agent_status": status, "workspace_id": "w1"} for i in range(count)]
        self.write_herdr(model)

    # --- A. the agent limit -------------------------------------------------------------

    def test_the_limit_comes_from_the_overlay(self):
        self.overlay({"lanes": {"herdr_max_agents": 5}})
        self.busy_agents(4)
        out = self.start()
        self.assertEqual(out["launcher"], "pane", out)
        self.busy_agents(5)
        out = self.start(work="U8", role="implementer", expect=4)
        self.assertIn("at the limit of 5", out.stdout)

    def test_the_default_stays_three_counted_over_the_workspace(self):
        self.busy_agents(3, status="idle")
        out = self.start()
        self.assertEqual(out["launcher"], "headless")
        self.assertIn("at the limit of 3", out["reason"])

    def test_count_run_ignores_agents_the_run_did_not_start(self):
        self.overlay({"lanes": {"herdr_max_agents": 1, "count": "run"}})
        self.busy_agents(4)
        first = self.start()
        self.assertEqual(first["launcher"], "pane")
        refused = self.start(work="U8", role="implementer", expect=4)
        self.assertIn("1 live herdr agents of this run are at the limit of 1", refused.stdout)
        headless = self.start(work="U7")
        self.assertEqual(headless["launcher"], "headless")
        self.end(first)
        self.assertEqual(self.start(work="U6", role="implementer")["launcher"], "pane")

    def test_an_invalid_lanes_key_is_exit_2(self):
        for value, needle in (({"herdr_max_agents": 0}, "integer >= 1"),
                              ({"herdr_max_agents": True}, "integer >= 1"),
                              ({"herdr_max_agents": "4"}, "integer >= 1"),
                              ({"count": "all"}, "lanes.count"),
                              ({"max": 4}, "unknown lanes keys: max"),
                              ([], "lanes must be an object")):
            with self.subTest(value=value):
                self.overlay({"lanes": value})
                result = self.start(expect=2)
                self.assertIn(needle, result.stderr)
        self.assertEqual(self.herdr_calls(), [])

    # --- B. the dashboard tab -----------------------------------------------------------

    def test_without_herdr_the_dashboard_is_a_no_op(self):
        for flags in ((), ("--open",), ("--close",)):
            result = self.dashboard(*flags, herdr=False)
            self.assertIn("herdr is not available", result.stdout)
            self.assertIn("dashboard.py t1", result.stdout)
        self.assertEqual(self.herdr_calls(), [])
        self.assertFalse((self.run_dir / "lanes" / "_herdr.json").exists())

    def test_the_tab_is_created_once_with_its_geometry_and_labels(self):
        self.assertEqual(json.loads(self.dashboard().stdout), {"open": False})
        opened = json.loads(self.dashboard("--open").stdout)
        self.assertTrue(opened["opened"])
        again = json.loads(self.dashboard("--open").stdout)
        self.assertFalse(again["opened"])
        self.assertEqual(again["tab_id"], opened["tab_id"])
        calls = self.herdr_calls()
        tabs = [c for c in calls if c[:2] == ["tab", "create"]]
        self.assertEqual(len(tabs), 1)
        self.assertEqual(tabs[0][tabs[0].index("--label") + 1], "📊 t1")
        self.assertIn("--no-focus", tabs[0])
        right = next(c for c in calls if c[:2] == ["pane", "split"] and "right" in c)
        self.assertEqual(right[right.index("--ratio") + 1], "0.58")
        board = self.board()
        self.assertEqual(board["tab_id"], opened["tab_id"])
        self.assertEqual([p["kind"] for p in board["panes"]], ["status", "events", "lane"])
        model = self.herdr_model()["panes"]
        status, events, free = (p["pane_id"] for p in board["panes"])
        self.assertEqual(model[status]["label"], "📊 status")
        self.assertEqual(model[events]["label"], "🧾 events")
        self.assertEqual(model[free]["label"], "🤖 lanes · free")
        self.assertAlmostEqual(model[status]["rect"]["width"], 180 * 0.58)
        self.assertEqual(model[status]["rect"]["x"], 0)
        self.assertEqual(model[events]["rect"]["x"], 0)
        self.assertGreater(model[events]["rect"]["y"], model[status]["rect"]["y"])
        self.assertGreater(model[free]["rect"]["x"], 0)
        self.assertIn("view status --task t1", model[status]["ran"][0])
        self.assertIn("view events --task t1", model[events]["ran"][0])
        self.assertNotIn("ran", model[free])
        shown = json.loads(self.dashboard().stdout)
        self.assertTrue(shown["open"])

    def test_project_panes_run_in_their_zones(self):
        self.overlay({"dashboard": {"panes": [
            {"label": "📥 queue", "command": "echo queue", "zone": "status"},
            {"label": "🖥 worker log", "command": "tail -f worker.log", "zone": "events"},
            {"label": "🔭 pilot", "command": "echo pilot"}]}})
        self.dashboard("--open")
        board = self.board()
        model = self.herdr_model()["panes"]
        left = [p for p in board["panes"] if model[p["pane_id"]]["rect"]["x"] == 0]
        right = [p for p in board["panes"] if model[p["pane_id"]]["rect"]["x"] > 0]
        by_top = lambda panes: [p["label"] for p in sorted(
            panes, key=lambda p: model[p["pane_id"]]["rect"]["y"])]
        self.assertEqual(by_top(left), ["📊 status", "📥 queue", "🧾 events", "🖥 worker log"])
        self.assertEqual(by_top(right), ["🔭 pilot", "🤖 lanes · free"])
        heights = {round(model[p["pane_id"]]["rect"]["height"], 1) for p in left}
        self.assertEqual(heights, {10.0})
        commands = {p["label"]: model[p["pane_id"]].get("ran") for p in board["panes"]}
        self.assertEqual(commands["📥 queue"], ["echo queue"])
        self.assertEqual(commands["🖥 worker log"], ["tail -f worker.log"])
        self.assertEqual(commands["🔭 pilot"], ["echo pilot"])

    def test_an_invalid_dashboard_key_is_exit_2(self):
        for value, needle in (({"tabs": []}, "unknown dashboard keys: tabs"),
                              ({"panes": {}}, "dashboard.panes must be a list"),
                              ({"panes": [{"label": "x", "command": "y", "zone": "top"}]},
                               "zone must be one of"),
                              ({"panes": [{"label": "", "command": "y"}]}, "label"),
                              ({"panes": [{"label": "x", "command": "y", "size": 3}]},
                               "unknown keys: size")):
            with self.subTest(value=value):
                self.overlay({"dashboard": value})
                result = self.dashboard("--open", expect=2)
                self.assertIn(needle, result.stderr)
        self.assertEqual([c for c in self.herdr_calls() if c[:2] == ["tab", "create"]], [])

    def test_lanes_go_into_the_right_column_reusing_the_free_pane_first(self):
        self.dashboard("--open")
        free = self.board()["panes"][-1]["pane_id"]
        first = self.start()
        self.assertEqual(self.pane_of(first["dispatch"]), free)
        model = self.herdr_model()["panes"]
        self.assertEqual(model[free]["label"], "🤖 U9 · claude model-x")
        self.assertEqual(model[free]["ran"],
                         [f"cd -- {self.repo} && export CLAUDE_CODE_EFFORT_LEVEL=high"])
        second = self.start(work="U8", runtime="codex")
        pane = self.pane_of(second["dispatch"])
        split = [c for c in self.herdr_calls() if c[:2] == ["pane", "split"]][-1]
        self.assertEqual((split[2], split[split.index("--direction") + 1]), (free, "down"))
        model = self.herdr_model()["panes"]
        self.assertEqual(model[pane]["tab_id"], self.board()["tab_id"])
        self.assertGreater(model[pane]["rect"]["x"], 0)
        self.assertEqual(model[pane]["label"], "🤖 U8 · codex model-x")
        self.assertEqual(json.loads((self.run_dir / "lanes" / f"{second['dispatch']}.json")
                                    .read_text())["tab_id"], self.board()["tab_id"])
        third = self.start(work="U7")
        resizes = [c for c in self.herdr_calls() if c[:2] == ["pane", "resize"]]
        self.assertTrue(resizes, "the right column is balanced after a split")
        self.assertEqual(resizes[-1][resizes[-1].index("--pane") + 1], free)
        self.assertEqual(resizes[-1][resizes[-1].index("--direction") + 1], "up")
        lanes_now = [p["dispatch"] for p in self.board()["panes"] if p["kind"] == "lane"]
        self.assertEqual(lanes_now, [first["dispatch"], second["dispatch"], third["dispatch"]])
        self.assertFalse([c for c in self.herdr_calls()
                          if c[:2] == ["pane", "split"] and "--current" in c])

    def test_an_ended_lane_pane_is_reused_only_once_its_shell_is_idle(self):
        self.dashboard("--open")
        first = self.start()
        mine = self.pane_of(first["dispatch"])
        self.end(first, exit_agent=False)  # done, but the agent still holds the shell
        second = self.start(work="U8")
        self.assertNotEqual(self.pane_of(second["dispatch"]), mine)
        model = self.herdr_model()
        model["agents"] = [a for a in model["agents"] if a["name"] != first["pane"]]
        model["panes"][mine]["idle"] = True
        self.write_herdr(model)
        third = self.start(work="U7", runtime="codex")
        self.assertEqual(self.pane_of(third["dispatch"]), mine)
        prepared = self.herdr_model()["panes"][mine]["ran"][-1]
        self.assertEqual(prepared, f"unset CLAUDE_CODE_EFFORT_LEVEL && cd -- {self.repo}")
        typed = self.herdr_model()["panes"][mine]["typed"]  # reset before anything is run
        self.assertEqual(typed[0], "\x03")
        self.assertIn("stty sane", typed[1])
        calls = [c[:2] for c in self.herdr_calls() if c[2:3] == [mine]]
        self.assertLess(calls.index(["pane", "wait-output"]), len(calls) - 1 - calls[::-1].index(
            ["pane", "run"]))
        slot = next(p for p in self.board()["panes"] if p["pane_id"] == mine)
        self.assertEqual(slot["dispatch"], third["dispatch"])

    def test_a_pane_whose_prompt_does_not_come_back_is_not_reused(self):
        self.dashboard("--open")
        free = self.board()["panes"][-1]["pane_id"]
        model = self.herdr_model()
        model["panes"][free]["garbled"] = True
        self.write_herdr(model)
        first = self.start()
        self.assertNotEqual(self.pane_of(first["dispatch"]), free)
        self.assertNotIn("ran", self.herdr_model()["panes"][free])

    def test_a_live_lane_with_an_idle_shell_is_never_reused(self):
        self.dashboard("--open")
        first = self.start()
        model = self.herdr_model()
        model["panes"][self.pane_of(first["dispatch"])]["idle"] = True
        self.write_herdr(model)
        second = self.start(work="U8")
        self.assertNotEqual(self.pane_of(second["dispatch"]), self.pane_of(first["dispatch"]))

    def test_open_moves_the_runs_live_lanes_into_the_right_column(self):
        first = self.start()
        beside = self.pane_of(first["dispatch"])
        self.assertEqual(self.herdr_model()["panes"][beside]["tab_id"], "w1:t1")
        self.dashboard("--open")
        board = self.board()
        moves = [c for c in self.herdr_calls() if c[:2] == ["pane", "move"]]
        self.assertEqual([m[2] for m in moves], [beside])
        self.assertEqual(moves[0][moves[0].index("--tab") + 1], board["tab_id"])
        self.assertEqual([p["kind"] for p in board["panes"]], ["status", "events", "lane"])
        self.assertEqual(board["panes"][-1]["dispatch"], first["dispatch"])
        model = self.herdr_model()["panes"]
        self.assertEqual(model[beside]["tab_id"], board["tab_id"])
        self.assertGreater(model[beside]["rect"]["x"], 0)
        self.assertEqual(len([p for p in model.values() if p["tab_id"] == board["tab_id"]]), 3)
        self.assertEqual(json.loads((self.run_dir / "lanes" / f"{first['dispatch']}.json")
                                    .read_text())["tab_id"], board["tab_id"])
        self.assertIn("w1:p1", model)

    def test_close_closes_only_the_runs_own_dashboard_panes(self):
        self.dashboard("--open")
        live = self.start()
        done = self.start(work="U8")
        done_pane = self.end(done)
        board = self.board()
        own = {p["pane_id"] for p in board["panes"]}
        live_pane = self.pane_of(live["dispatch"])
        self.assertIn(done_pane, own)
        model = self.herdr_model()
        model["panes"]["w1:p99"] = {"tab_id": board["tab_id"], "idle": True,
                                    "rect": {"x": 0, "y": 0, "width": 1, "height": 1}}
        self.write_herdr(model)
        closed = json.loads(self.dashboard("--close").stdout)
        self.assertEqual(set(closed["closed"]), own - {live_pane})
        self.assertEqual(closed["kept"], [live_pane])
        self.assertFalse(closed["tab_closed"])
        for call in self.herdr_calls():
            if call[:2] in (["pane", "close"], ["tab", "close"]):
                self.assertIn(call[2], own - {live_pane})
        model = self.herdr_model()["panes"]
        self.assertIn("w1:p99", model)
        self.assertIn("w1:p1", model)
        self.assertIn(live_pane, model)
        self.assertNotIn("dashboard", self.record())

    def test_close_of_a_dashboard_that_is_all_ours_closes_the_tab(self):
        self.dashboard("--open")
        tab = self.board()["tab_id"]
        closed = json.loads(self.dashboard("--close").stdout)
        self.assertTrue(closed["tab_closed"])
        self.assertNotIn(tab, self.herdr_model()["tabs"])
        self.assertEqual(json.loads(self.dashboard("--close").stdout)["closed"], [])
        reopened = json.loads(self.dashboard("--open").stdout)
        self.assertTrue(reopened["opened"])
        self.assertNotEqual(reopened["tab_id"], tab)

    def test_after_close_lanes_are_placed_as_before(self):
        self.dashboard("--open")
        self.dashboard("--close")
        out = self.start()
        split = [c for c in self.herdr_calls() if c[:2] == ["pane", "split"]][-1]
        self.assertIn("--current", split)
        self.assertEqual(self.herdr_model()["panes"][self.pane_of(out["dispatch"])]["label"],
                         "🤖 U9 · claude model-x")

    # --- views ------------------------------------------------------------------------

    def event(self, kind, data, event_id=None):
        args = [sys.executable, str(TASK_STATE), "event", "t1", kind, "--data", json.dumps(data)]
        if event_id:
            args += ["--id", event_id]
        subprocess.run(args, cwd=self.repo, env=self.env, check=True, capture_output=True)

    def seed_events(self):
        self.event("dispatch.start", {"dispatch": "u1-implementer-a1", "role": "implementer",
                                      "runtime": "codex", "model": "m", "effort": "high",
                                      "launcher": "subagent", "work": "U1"})
        self.event("dispatch.end", {"dispatch": "u1-implementer-a1", "status": "done",
                                    "usage": {"tool_calls": 4, "duration_s": 90}})
        self.event("dispatch.start", {"dispatch": "u2-reviewer-b2", "role": "reviewer",
                                      "runtime": "claude", "model": "m", "effort": "high",
                                      "launcher": "subagent", "work": "U2"})
        self.event("gate.open", {"gate": "m1", "card": "gates/m1.md"})
        self.event("balance.pick", {"role": "implementer", "runtime": "codex",
                                    "reason": "larger runway"})
        units = self.run_dir / "units"
        units.mkdir(exist_ok=True)
        (units / "U1-closed").write_text("")
        (units / "U2-scope.json").write_text("{}")

    def test_the_status_view_summarizes_the_run(self):
        self.seed_events()
        result = self.run_script(VIEWS, "view", "status", "--task", "t1", "--once", herdr=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        text = result.stdout
        self.assertIn("📊 t1", text)
        self.assertIn("Units: 1 of 2 closed", text)
        self.assertIn("Lanes: 1 running · 0 blocked · 0 stale/idle · 1 ended", text)
        self.assertIn("u2-reviewer-b2", text)
        self.assertNotIn("u1-implementer-a1", text)
        self.assertIn("Gates: 1 pending — m1", text)
        self.assertIn("Tripwires: 0 open", text)
        self.assertIn("Balance: claude 50% (1 of 2) · codex 50% (1 of 2)", text)
        self.assertIn("implementer → codex (larger runway)", text)

    def test_the_status_view_shows_a_blocked_pane_from_herdr(self):
        out = self.start()
        model = self.herdr_model()
        for agent in model["agents"]:
            if agent["name"] == out["pane"]:
                agent["agent_status"] = "blocked"
        self.write_herdr(model)
        before = len(self.herdr_calls())
        result = self.run_script(VIEWS, "view", "status", "--task", "t1", "--once")
        self.assertIn("1 blocked", result.stdout)
        self.assertIn("⛔ " + out["dispatch"], result.stdout)
        self.assertEqual([c[:2] for c in self.herdr_calls()[before:]], [["agent", "list"]])

    def test_the_events_view_formats_the_tail(self):
        self.seed_events()
        result = self.run_script(VIEWS, "view", "events", "--task", "t1", "--once", herdr=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("5 events", lines[0])
        body = "\n".join(lines[1:])
        self.assertIn("▶ u1-implementer-a1  implementer · codex m high · subagent", body)
        self.assertIn("■ u1-implementer-a1  done · 1m 30s · 4 tool calls", body)
        self.assertIn("🚦 gate m1 open · gates/m1.md", body)
        self.assertIn("⚖ pick implementer → codex (larger runway)", body)

    # --- balancing ----------------------------------------------------------------------

    def test_balance_moves_give_a_chain_equal_heights(self):
        self.assertEqual(dashboard_herdr.balance_moves([40]), [])
        self.assertEqual(dashboard_herdr.balance_moves([20, 20]), [])
        self.assertEqual(dashboard_herdr.balance_moves([13, 13, 14]), [])
        ((index, direction, amount),) = dashboard_herdr.balance_moves([20, 10, 10])
        self.assertEqual((index, direction), (0, "up"))
        self.assertAlmostEqual(amount, 0.1667, places=3)
        moves = dashboard_herdr.balance_moves([10, 10, 5, 5])
        self.assertEqual([(i, d) for i, d, _ in moves], [(0, "up"), (1, "up")])
        moves = dashboard_herdr.balance_moves([5, 10, 15])
        self.assertEqual([(i, d) for i, d, _ in moves], [(0, "down"), (1, "down")])


if __name__ == "__main__":
    unittest.main()
