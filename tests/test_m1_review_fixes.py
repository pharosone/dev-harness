"""Milestone-1 review fixes: stale reports, startup-blocked panes, cancel ownership, the agent
limit lock, other-repository tabs, tripwire duplicates and dashboard regeneration and links.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch come first
on PATH (herdr behind a wrapper that can fail a start with agent_not_ready, hand out agent
ids and slow `agent list` down), and the tripwire hook is fed JSON payloads on stdin.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_dashboard as dash  # noqa: E402
import test_lanes_launch as launch  # noqa: E402
import test_tripwire as wire  # noqa: E402

HERDR_WRAPPER = """#!{python}
import json, os, subprocess, sys, time
args = sys.argv[1:]
state_path = os.environ["FAKE_HERDR_STATE"]
done = subprocess.run([os.environ["FAKE_HERDR_BASE"], *args], capture_output=True, text=True)
def load():
    with open(state_path, encoding="utf-8") as stream:
        return json.load(stream)
def save(state):
    with open(state_path, "w", encoding="utf-8") as stream:
        json.dump(state, stream)
if done.returncode == 0 and args[:2] == ["agent", "start"]:
    state = load()
    agent = next(a for a in state["agents"] if a.get("name") == args[2])
    if os.environ.get("FAKE_HERDR_AGENT_ID") == "1":
        agent["agent_id"] = "agt-%d" % len(state["agents"])
    if os.environ.get("FAKE_HERDR_NOT_READY") == "1":
        agent["agent_status"] = "blocked"
        save(state)
        print(json.dumps({{"error": {{"code": "agent_not_ready", "message": "not ready"}}}}),
              file=sys.stderr)
        sys.exit(1)
    save(state)
    print(json.dumps({{"id": "cli", "result": {{"agent": agent}}}}))
    sys.exit(0)
if args[:2] == ["agent", "list"]:
    time.sleep(float(os.environ.get("FAKE_HERDR_LIST_SLEEP", "0")))
sys.stdout.write(done.stdout)
sys.stderr.write(done.stderr)
sys.exit(done.returncode)
"""


class LanesReviewFixTest(unittest.TestCase):
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

    def setUp(self):
        self.setUp_launch()
        base = self.bin / "herdr-base"
        (self.bin / "herdr").rename(base)
        wrapper = self.bin / "herdr"
        wrapper.write_text(HERDR_WRAPPER.format(python=sys.executable))
        wrapper.chmod(0o755)
        self.env["FAKE_HERDR_BASE"] = str(base)

    def begin(self, *extra, runtime="claude", role="reviewer", work="U9", checkout=None,
              env=None, expect=0):
        result = self.lanes(
            "start", "--task", "t1", "--work", work, "--role", role, "--runtime", runtime,
            "--model", "model-x", "--effort", "high", "--brief", str(self.brief),
            "--report", str(self.report_path(work, role)),
            "--checkout", str(checkout or self.repo), "--minutes", "5",
            "--orchestrator", "claude", *extra, **(env or {}))
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        if expect:
            return result
        out = json.loads(result.stdout)
        self.started.append(out["dispatch"])
        return out

    def report_path(self, work="U9", role="reviewer"):
        return self.temp / "reports" / f"{work}-{role}.md"

    def agents(self):
        return json.loads(self.herdr_state.read_text())["agents"]

    def edit_agent(self, name, **fields):
        state = json.loads(self.herdr_state.read_text())
        for agent in state["agents"]:
            if agent.get("name") == name:
                agent.update(fields)
        self.herdr_state.write_text(json.dumps(state))

    def of(self, kind, since=0):
        return [c for c in self.herdr_calls()[since:] if c[:2] == kind]

    def page(self):
        return (self.run_dir / "dashboard.html").read_text(encoding="utf-8")

    # --- 1. a report from an earlier run ----------------------------------------------

    def test_a_report_from_an_earlier_run_does_not_suppress_the_result_fallback(self):
        report = self.report_path()
        report.parent.mkdir(parents=True)
        report.write_text("yesterday's report\n")
        old = time.time() - 86400
        os.utime(report, (old, old))
        out = self.begin("--launcher", "headless")
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        self.assertEqual(report.read_text(), "fake claude report\n")
        result = json.loads(self.lanes("result", "--task", "t1", "--dispatch",
                                       out["dispatch"]).stdout)
        self.assertEqual((result["status"], result["report"]), ("done", str(report)))

    def test_a_pane_report_counts_only_once_written_after_the_start(self):
        report = self.report_path()
        report.parent.mkdir(parents=True)
        report.write_text("written a moment before the start\n")
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        self.set_agent(out["pane"], "idle")
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "running")
        self.assertEqual([e["type"] for e in self.events(out["dispatch"])], ["dispatch.start"])
        report.write_text("the worker's own report, longer than the old one\n")
        self.assertEqual(self.wait(out["dispatch"], env=self.herdr_env())["state"], "done")

    # --- 2. a pane blocked at startup -------------------------------------------------

    def test_an_agent_not_ready_at_start_is_blocked_then_gets_its_brief_exactly_once(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env(FAKE_HERDR_NOT_READY="1"))
        self.assertIs(self.state_file(out["dispatch"])["prompt_pending"], True)
        self.assertEqual(self.of(["agent", "prompt"]), [])
        blocked = self.status(out["dispatch"], env=self.herdr_env())
        self.assertEqual(blocked["state"], "blocked")
        self.assertIn("user", blocked["reason"])
        self.assertEqual(self.wait(out["dispatch"], env=self.herdr_env(), expect=5)["state"],
                         "blocked")
        self.assertEqual(self.of(["agent", "prompt"]), [])

        self.set_agent(out["pane"], "idle")  # the user cleared the startup approval
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "running")
        [prompt] = self.of(["agent", "prompt"])
        self.assertEqual(prompt[2], out["pane"])
        self.assertIn(str(self.brief), prompt[3])
        self.assertIn(str(self.report_path()), prompt[3])
        self.assertIs(self.state_file(out["dispatch"])["prompt_pending"], False)

        self.set_agent(out["pane"], "idle")
        self.assertEqual(self.status(out["dispatch"], env=self.herdr_env())["state"], "running")
        result = self.lanes("wait", "--task", "t1", "--dispatch", out["dispatch"], "--timeout",
                            "0.5", **self.herdr_env())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertEqual(len(self.of(["agent", "prompt"])), 1)

        self.report_path().write_text("pane report\n")
        self.set_agent(out["pane"], "idle")
        self.assertEqual(self.wait(out["dispatch"], env=self.herdr_env())["state"], "done")
        self.assertEqual(len(self.of(["agent", "prompt"])), 1)

    # --- 3. cancel checks it is still the run's own agent -----------------------------

    def test_cancel_refuses_when_the_name_now_belongs_to_another_pane(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        self.edit_agent(out["pane"], pane_id="w1:p77")
        before = len(self.herdr_calls())
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"],
                            **self.herdr_env())
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn(f"agent {out['pane']} is no longer the pane this run created",
                      result.stderr)
        self.assertEqual(self.of(["agent", "send-keys"], before), [])
        self.assertEqual([e["type"] for e in self.events(out["dispatch"])], ["dispatch.start"])
        self.assertIn(out["pane"], {a.get("name") for a in self.agents()})

    def test_cancel_checks_the_agent_id_when_herdr_gives_one(self):
        env = self.herdr_env(FAKE_HERDR_AGENT_ID="1")
        out = self.begin("--launcher", "pane", env=env)
        recorded = self.state_file(out["dispatch"])["agent_id"]
        self.assertTrue(recorded)
        self.edit_agent(out["pane"], agent_id="agt-other")
        before = len(self.herdr_calls())
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"], **env)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("is no longer the pane this run created", result.stderr)
        self.assertEqual(self.of(["agent", "send-keys"], before), [])

        self.edit_agent(out["pane"], agent_id=recorded)
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"], **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)["state"], "cancelled")
        [keys] = self.of(["agent", "send-keys"], before)
        self.assertEqual(keys[2], out["pane"])

    # --- 4. the agent limit holds under concurrent starts -----------------------------

    def test_two_concurrent_starts_at_two_agents_launch_only_one(self):
        busy = [{"name": f"busy{i}", "agent": "claude", "pane_id": f"w1:p{20 + i}",
                 "agent_status": "working", "workspace_id": "w1"} for i in range(2)]
        self.herdr_state.write_text(json.dumps({"agents": busy}))
        env = {**self.env, **self.herdr_env(FAKE_HERDR_LIST_SLEEP="1.0")}
        procs = [subprocess.Popen(
            [sys.executable, str(launch.LANES), "start", "--task", "t1", "--work", work,
             "--role", "implementer", "--runtime", "claude", "--model", "model-x", "--effort",
             "high", "--brief", str(self.brief), "--report", str(self.report_path(work)),
             "--checkout", str(self.repo), "--launcher", "pane"],
            cwd=self.repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for work in ("U1", "U2")]
        results = [(p.wait(timeout=60), *p.communicate()) for p in procs]
        for code, stdout, _ in results:
            if code == 0:
                self.started.append(json.loads(stdout)["dispatch"])
        self.assertEqual(sorted(code for code, _, _ in results), [0, 4], results)
        self.assertEqual(len(self.agents()), 3)
        self.assertEqual(len(self.of(["agent", "start"])), 1)

    # --- 5. the run's first pane in another repository --------------------------------

    def test_the_first_pane_in_another_repository_goes_to_its_tab(self):
        other = self.temp / "ledger-svc"
        (other / "src").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=other, check=True, env=self.env)
        relative = os.path.relpath(other / "src", self.repo)
        out = self.begin("--launcher", "pane", checkout=relative, env=self.herdr_env())
        self.assertEqual(out["launcher"], "pane")
        self.assertEqual(self.of(["pane", "split"]), [])
        [tab] = self.of(["tab", "create"])
        self.assertEqual(tab[tab.index("--label") + 1], "ledger-svc")
        self.assertEqual(self.state_file(out["dispatch"])["other_repo"], str(other.resolve()))

    # --- 7. the dashboard follows lane transitions ------------------------------------

    def test_a_pane_start_and_end_regenerate_the_dashboard(self):
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        running = dash.section(self.page(), "running")
        self.assertIn(f'id="dispatch-{out["dispatch"]}"', running)
        self.report_path().write_text("pane report\n")
        self.set_agent(out["pane"], "idle")
        self.assertEqual(self.wait(out["dispatch"], env=self.herdr_env())["state"], "done")
        page = self.page()
        self.assertNotIn(f'id="dispatch-{out["dispatch"]}"', dash.section(page, "running"))
        finished = dash.row(dash.section(page, "finished"), f"dispatch-{out['dispatch']}")
        self.assertEqual(dash.cell(finished, "state"), "done")

    def test_a_headless_end_regenerates_the_dashboard(self):
        out = self.begin("--launcher", "headless")
        self.assertEqual(self.wait(out["dispatch"])["state"], "done")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if f'id="dispatch-{out["dispatch"]}"' in dash.section(self.page(), "finished"):
                break
            time.sleep(0.1)
        finished = dash.row(dash.section(self.page(), "finished"), f"dispatch-{out['dispatch']}")
        self.assertEqual(dash.cell(finished, "state"), "done")

    def test_a_failed_regeneration_never_fails_the_start(self):
        (self.run_dir / "dashboard.html").mkdir()
        out = self.begin("--launcher", "pane", env=self.herdr_env())
        self.assertEqual(out["launcher"], "pane")
        self.assertEqual([e["type"] for e in self.events(out["dispatch"])], ["dispatch.start"])
        result = self.lanes("cancel", "--task", "t1", "--dispatch", out["dispatch"],
                            **self.herdr_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("dashboard", result.stderr)
        self.assertEqual([e["type"] for e in self.events(out["dispatch"])],
                         ["dispatch.start", "dispatch.end"])

    # --- 8. absolute report paths link -----------------------------------------------

    def test_a_report_inside_the_run_links_relative_on_the_dashboard(self):
        report = self.run_dir / "reports" / "U9.md"
        result = self.lanes(
            "start", "--task", "t1", "--work", "U9", "--role", "reviewer", "--runtime", "claude",
            "--model", "model-x", "--effort", "high", "--brief", str(self.brief), "--report",
            str(report), "--checkout", str(self.repo), "--launcher", "pane", **self.herdr_env())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        out = json.loads(result.stdout)
        self.started.append(out["dispatch"])
        [start] = [e for e in self.events(out["dispatch"]) if e["type"] == "dispatch.start"]
        self.assertEqual(start["report"], str(report.resolve()))
        report.write_text("report\n")
        self.set_agent(out["pane"], "idle")
        self.wait(out["dispatch"], env=self.herdr_env())
        finished = dash.row(dash.section(self.page(), "finished"), f"dispatch-{out['dispatch']}")
        self.assertIn('href="reports/U9.md"', finished)
        self.assertEqual(dash.cell(finished, "report"), "reports/U9.md")


class TripwireReviewFixTest(unittest.TestCase):
    setUp = wire.TripwireTest.setUp
    git = wire.TripwireTest.git
    run_script = wire.TripwireTest.run_script
    tripwire = wire.TripwireTest.tripwire
    ok = wire.TripwireTest.ok
    agent_hook = wire.TripwireTest.agent_hook
    events = wire.TripwireTest.events
    of_type = wire.TripwireTest.of_type

    def freeze_and_weaken(self):
        red = self.units.parent / "01-red.txt"
        red.write_text("--- FAIL: TestCSV\n")
        self.ok(self.run_script(wire.TASK_STATE, "freeze", "feature", "01", "--red-log",
                                str(red), "tests/csv_test.go", cwd=self.worktree))
        self.ok(self.tripwire("scope", "feature", "01", "--base", "HEAD", "server/export/**",
                              "tests/**", cwd=self.worktree))
        (self.worktree / "tests/csv_test.go").write_text("// weakened\n")

    def counter(self):
        return json.loads((self.units / "01-trips.json").read_text())

    def page(self):
        return (self.root / ".harness/tasks/feature/dashboard.html").read_text(encoding="utf-8")

    # --- 6. one dispatch counts once --------------------------------------------------

    def test_a_duplicate_completion_counts_one_verify_failure(self):
        self.freeze_and_weaken()
        for _ in range(2):
            out = self.agent_hook("PostToolUse")
            self.assertNotIn("block", out.stdout)
        self.assertEqual(self.counter()["verify_failures"], 1)
        out = self.agent_hook("PostToolUse", tool_use_id="toolu_02")
        self.assertEqual(json.loads(out.stdout)["decision"], "block")
        self.assertIn("Frozen tests were changed in 2 dispatches", out.stdout)
        self.assertEqual(self.counter()["verify_failures"], 2)

    # --- 7. the hook regenerates the dashboard ----------------------------------------

    def test_the_hook_regenerates_the_dashboard_on_start_and_end(self):
        self.assertEqual(self.agent_hook("PreToolUse").stdout, "")
        self.assertIn('id="dispatch-toolu_01"', dash.section(self.page(), "running"))
        self.agent_hook("PostToolUse")
        page = self.page()
        self.assertNotIn('id="dispatch-toolu_01"', dash.section(page, "running"))
        self.assertIn('id="dispatch-toolu_01"', dash.section(page, "finished"))

    def test_a_failed_regeneration_never_fails_the_hook(self):
        (self.root / ".harness/tasks/feature/dashboard.html").mkdir()
        result = self.agent_hook("PreToolUse")
        self.assertEqual(result.stdout, "")
        self.assertIn("dashboard", result.stderr)
        self.assertEqual(len(self.of_type("dispatch.start")), 1)


class DashboardReviewFixTest(unittest.TestCase):
    setUp = dash.DashboardTest.setUp
    git = dash.DashboardTest.git
    script = dash.DashboardTest.script
    ok = dash.DashboardTest.ok
    event = dash.DashboardTest.event
    write_events = dash.DashboardTest.write_events
    write = dash.DashboardTest.write
    render = dash.DashboardTest.render
    start = dash.DashboardTest.start
    end = dash.DashboardTest.end

    # --- 8. absolute report paths -----------------------------------------------------

    def test_an_absolute_report_inside_the_run_links_relative(self):
        self.write("reports/U1.md", "report one\n")
        self.write("reports/U2.md", "report two\n")
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("outside\n")
        run = self.run_dir.resolve()
        self.start("d1", "2026-10-09T11:50:00Z", report=str(run / "reports/U1.md"))
        self.end("d1", "2026-10-09T11:55:00Z")
        self.start("d2", "2026-10-09T11:56:00Z", work="U2", report=str(run / "reports/U2.md"))
        self.start("d3", "2026-10-09T11:56:00Z", work="U3", report=str(outside.resolve()))
        self.write_events()
        page = self.render()
        finished = dash.row(dash.section(page, "finished"), "dispatch-d1")
        self.assertIn('href="reports/U1.md"', finished)
        self.assertEqual(dash.cell(finished, "report"), "reports/U1.md")
        running = dash.row(dash.section(page, "running"), "dispatch-d2")
        self.assertIn('href="reports/U2.md"', running)
        self.assertEqual(dash.cell(running, "report"), "reports/U2.md")
        self.assertNotIn("href", dash.row(dash.section(page, "running"), "dispatch-d3"))
        for href in dash.re.findall(r'href="([^"]*)"', page):
            self.assertNotRegex(href, r"^/|:", href)

    def test_a_relative_report_shows_its_run_relative_path(self):
        self.write("reports/U1.md", "report\n")
        self.start("d1", "2026-10-09T11:50:00Z", report="reports/U1.md")
        self.write_events()
        running = dash.row(dash.section(self.render(), "running"), "dispatch-d1")
        self.assertEqual(dash.cell(running, "report"), "reports/U1.md")

    # --- 9. gate cards ----------------------------------------------------------------

    def test_a_symlinked_gate_card_is_listed_but_not_linked(self):
        outside = Path(self.temp.name) / "outside-card.md"
        outside.write_text("outside card\n")
        (self.run_dir / "gates").mkdir()
        (self.run_dir / "gates/G1.md").symlink_to(outside)
        self.write("gates/G2.md", "real card\n")
        self.write_events()
        pending = dash.section(self.render(), "pending-gates")
        self.assertIn("gate-G1", pending)
        self.assertNotIn('href="gates/G1.md"', pending)
        self.assertIn('href="gates/G2.md"', pending)

    # --- 10. legacy runs ---------------------------------------------------------------

    def legacy_run(self):
        legacy = self.root / ".claude/tasks/oldtask"
        legacy.mkdir(parents=True)
        (legacy / "run.md").write_text("# oldtask\n")
        return legacy

    def test_a_legacy_run_writes_its_default_output_under_harness(self):
        legacy = self.legacy_run()
        output = Path(self.ok(self.script(dash.DASHBOARD, "oldtask", "--now", dash.NOW)))
        expected = self.root / ".harness/dashboards/oldtask.html"
        self.assertEqual(output.resolve(), expected.resolve())
        self.assertTrue(expected.is_file())
        self.assertFalse((legacy / "dashboard.html").exists())
        self.assertIn("../../.claude/tasks/oldtask/run.md", expected.read_text(encoding="utf-8"))

    def test_a_legacy_run_still_refuses_out_outside_harness(self):
        legacy = self.legacy_run()
        result = self.script(dash.DASHBOARD, "oldtask", "--now", dash.NOW, "--out",
                             str(legacy / "dashboard.html"))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse((legacy / "dashboard.html").exists())


if __name__ == "__main__":
    unittest.main()
