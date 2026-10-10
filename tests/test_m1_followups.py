"""Milestone-1 follow-ups: background dispatch links, end status, writer tools, repo tabs, budget.

The real claude, codex and herdr are never called: the fakes of test_lanes_launch come first
on PATH and record their argv, and the tripwire hook is fed JSON payloads on stdin.
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_lanes_launch as launch  # noqa: E402
import test_tripwire as wire  # noqa: E402

WRITER_TOOLS = "Bash,Read,Edit,Write,Grep,Glob"
CLAUDE_HEAD = ["claude", "-p", "--output-format", "stream-json", "--verbose", "--model", "model-x"]


class DispatchLinkTest(unittest.TestCase):
    """A background Claude dispatch is matched to its start through the agent id."""

    setUp = wire.TripwireTest.setUp
    git = wire.TripwireTest.git
    run_script = wire.TripwireTest.run_script
    tripwire = wire.TripwireTest.tripwire
    ok = wire.TripwireTest.ok
    agent_hook = wire.TripwireTest.agent_hook
    events = wire.TripwireTest.events
    of_type = wire.TripwireTest.of_type

    def launch_in_background(self):
        self.agent_hook("PreToolUse", background=True)
        for _ in range(2):
            out = self.agent_hook("PostToolUse", status="async_launched", background=True)
            self.assertEqual(out.stdout, "")

    def fields(self, event):
        return {k: v for k, v in event.items() if k not in ("v", "id", "at", "type")}

    def test_a_background_launch_links_its_agent_id_once(self):
        self.launch_in_background()
        [link] = self.of_type("dispatch.link")
        self.assertEqual(link["id"], "toolu_01:link")
        self.assertEqual(self.fields(link), {"dispatch": "toolu_01", "agent_id": "a1b2"})
        self.assertEqual(len(self.of_type("dispatch.start")), 1)
        self.assertEqual(self.of_type("dispatch.end"), [])

    def test_a_launch_without_an_agent_id_writes_no_link(self):
        self.agent_hook("PostToolUse", background=True,
                        response={"status": "async_launched", "isAsync": True})
        self.assertEqual(len(self.of_type("dispatch.start")), 1)
        self.assertEqual(self.of_type("dispatch.link"), [])

    def test_spend_with_the_agent_id_ends_the_original_dispatch_once(self):
        self.launch_in_background()
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "9", "--dispatch", "a1b2"))
        [end] = self.of_type("dispatch.end")
        self.assertEqual((end["id"], end["dispatch"], end["status"]),
                         ("toolu_01:end", "toolu_01", "done"))
        self.assertEqual(end["usage"]["tool_calls"], 9)
        # The same dispatch named by either id is one end and one spend line.
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "9", "--dispatch", "a1b2"))
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "9",
                              "--dispatch", "toolu_01"))
        self.assertEqual(len(self.of_type("dispatch.end")), 1)
        ledger = (self.units / "01-spend.jsonl").read_text().splitlines()
        self.assertEqual(len(ledger), 1)

    def test_spend_with_the_dispatch_id_still_ends_it(self):
        self.launch_in_background()
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "4",
                              "--dispatch", "toolu_01", "--status", "timeout"))
        [end] = self.of_type("dispatch.end")
        self.assertEqual((end["id"], end["dispatch"], end["status"]),
                         ("toolu_01:end", "toolu_01", "timeout"))

    def test_spend_status_sets_the_end_status(self):
        for status in ("done", "failed", "timeout", "cancelled"):
            self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "1",
                                  "--dispatch", f"agent-{status}", "--status", status))
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "1",
                              "--dispatch", "agent-default"))
        ends = {e["dispatch"]: e["status"] for e in self.of_type("dispatch.end")}
        self.assertEqual(ends, {"agent-done": "done", "agent-failed": "failed",
                                "agent-timeout": "timeout", "agent-cancelled": "cancelled",
                                "agent-default": "done"})

    def test_an_agent_id_resolves_for_a_failed_end_too(self):
        self.launch_in_background()
        self.ok(self.tripwire("spend", "feature", "01", "--tool-uses", "2", "--dispatch", "a1b2",
                              "--status", "failed"))
        [end] = self.of_type("dispatch.end")
        self.assertEqual((end["dispatch"], end["status"]), ("toolu_01", "failed"))

    def test_a_bad_status_or_a_status_without_a_dispatch_is_refused(self):
        bad = self.tripwire("spend", "feature", "01", "--tool-uses", "1", "--dispatch", "x",
                            "--status", "finished")
        self.assertEqual(bad.returncode, 2)
        alone = self.tripwire("spend", "feature", "01", "--tool-uses", "1", "--status", "failed")
        self.assertEqual(alone.returncode, 2, alone.stdout + alone.stderr)
        self.assertEqual(self.events(), [])
        self.assertFalse((self.units / "01-spend.jsonl").exists())


class LanesFollowupTest(unittest.TestCase):
    """Writer tools, budget and the tab of another repository, on fake CLIs."""

    setUp = launch.LaunchTest.setUp
    cleanup = launch.LaunchTest.cleanup
    git = launch.LaunchTest.git
    herdr_env = launch.LaunchTest.herdr_env
    lanes = launch.LaunchTest.lanes
    wait = launch.LaunchTest.wait
    calls = launch.LaunchTest.calls
    herdr_calls = launch.LaunchTest.herdr_calls
    events = launch.LaunchTest.events

    def begin(self, *extra, runtime="claude", role="reviewer", work="U9", checkout=None,
              env=None, expect=0):
        result = self.lanes(
            "start", "--task", "t1", "--work", work, "--role", role, "--runtime", runtime,
            "--model", "model-x", "--effort", "high", "--brief", str(self.brief),
            "--report", str(self.temp / "reports" / f"{work}-{role}.md"),
            "--checkout", str(checkout or self.repo), "--minutes", "5",
            "--orchestrator", "claude", *extra, **(env or {}))
        self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        if expect:
            return result
        out = json.loads(result.stdout)
        self.started.append(out["dispatch"])
        return out

    def argv_of(self, out):
        self.wait(out["dispatch"])
        return next(c["argv"] for c in self.calls()
                    if c["argv"][0] in ("claude", "codex") and self.report_of(out) in c["prompt"])

    def report_of(self, out):
        events = [json.loads(line) for line in (self.run_dir / "events.jsonl").read_text()
                  .splitlines() if line.strip()]
        return next(e["report"] for e in events
                    if e["type"] == "dispatch.start" and e["dispatch"] == out["dispatch"])

    def repository(self, name):
        path = self.temp / name
        (path / "server").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True, env=self.env)
        return path

    # --- headless Claude writers ------------------------------------------------------

    def test_headless_claude_writers_get_accept_edits_and_the_writer_tools(self):
        for work, role in (("U1", "implementer"), ("U2", "simplifier")):
            out = self.begin("--launcher", "headless", role=role, work=work)
            self.assertEqual(self.argv_of(out), CLAUDE_HEAD + [
                "--permission-mode", "acceptEdits", "--allowedTools", WRITER_TOOLS])

    def test_headless_claude_reviewers_keep_their_read_only_set(self):
        out = self.begin("--launcher", "headless", role="reviewer")
        self.assertEqual(self.argv_of(out), CLAUDE_HEAD)

    # --- budget -----------------------------------------------------------------------

    def test_the_budget_goes_to_headless_claude_and_is_enforced(self):
        out = self.begin("--launcher", "headless", "--max-budget-usd", "2.5")
        self.assertIs(out["budget_enforced"], True)
        self.assertEqual(self.argv_of(out), CLAUDE_HEAD + ["--max-budget-usd", "2.5"])
        writer = self.begin("--launcher", "headless", "--max-budget-usd", "3", role="implementer",
                            work="U2")
        self.assertIs(writer["budget_enforced"], True)
        self.assertEqual(self.argv_of(writer), CLAUDE_HEAD + [
            "--permission-mode", "acceptEdits", "--allowedTools", WRITER_TOOLS,
            "--max-budget-usd", "3"])

    def test_codex_ignores_the_budget_and_says_so(self):
        out = self.begin("--launcher", "headless", "--max-budget-usd", "2.5", runtime="codex")
        self.assertIs(out["budget_enforced"], False)
        argv = self.argv_of(out)
        self.assertEqual(argv[:3], ["codex", "exec", "--json"])
        self.assertFalse([a for a in argv if "budget" in a])

    def test_panes_ignore_the_budget_and_say_so(self):
        for work, runtime in (("U1", "claude"), ("U2", "codex")):
            out = self.begin("--launcher", "pane", "--max-budget-usd", "2.5", runtime=runtime,
                             work=work, env=self.herdr_env())
            self.assertEqual(out["launcher"], "pane")
            self.assertIs(out["budget_enforced"], False)
        for call in self.herdr_calls():
            self.assertFalse([a for a in call if "budget" in a], call)
        self.assertEqual(self.calls(), [])

    def test_a_budget_that_is_not_positive_is_refused(self):
        for value in ("0", "-1", "lots"):
            result = self.begin("--launcher", "headless", "--max-budget-usd", value, expect=2)
            self.assertIn("budget", result.stderr)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events(), [])

    # --- herdr: another repository ----------------------------------------------------

    def test_another_repository_gets_its_own_tab_reused_for_its_later_panes(self):
        billing = self.repository("billing-svc")
        first = self.begin(checkout=billing, env=self.herdr_env())
        self.assertEqual(first["launcher"], "pane")
        self.assertIn("another repository", first["reason"])
        calls = self.herdr_calls()
        self.assertFalse([c for c in calls if c[:2] == ["pane", "split"]])
        [tab] = [c for c in calls if c[:2] == ["tab", "create"]]
        self.assertEqual(tab[tab.index("--label") + 1], "billing-svc")
        self.assertEqual(tab[tab.index("--cwd") + 1], str(billing))
        self.assertIn("--no-focus", tab)
        [started] = [c for c in calls if c[:2] == ["agent", "start"]]
        root = started[started.index("--pane") + 1]

        # A later pane of the same repository, from a subdirectory: the same tab.
        before = len(calls)
        second = self.begin(work="U8", checkout=billing / "server", env=self.herdr_env())
        later = self.herdr_calls()[before:]
        self.assertFalse([c for c in later if c[:2] == ["tab", "create"]])
        [split] = [c for c in later if c[:2] == ["pane", "split"]]
        self.assertEqual(split[2], root)
        self.assertEqual(split[split.index("--cwd") + 1], str(billing / "server"))
        self.assertIn("--no-focus", split)
        [started] = [c for c in later if c[:2] == ["agent", "start"]]
        self.assertNotEqual(started[started.index("--pane") + 1], root)
        self.assertNotEqual(first["pane"], second["pane"])

    def test_same_repository_placement_is_unchanged_beside_another_repository(self):
        billing = self.repository("billing-svc")
        self.begin(checkout=billing, env=self.herdr_env())
        before = len(self.herdr_calls())
        self.begin("--launcher", "pane", work="U8", env=self.herdr_env())
        here = self.herdr_calls()[before:]
        [split] = [c for c in here if c[:2] == ["pane", "split"]]
        self.assertEqual(split[2:4], ["--current", "--direction"])
        self.assertEqual(split[split.index("--cwd") + 1], str(self.repo))
        self.assertFalse([c for c in here if c[:2] == ["tab", "create"]])
        before = len(self.herdr_calls())
        self.begin("--launcher", "pane", work="U7", env=self.herdr_env())
        [tab] = [c for c in self.herdr_calls()[before:] if c[:2] == ["tab", "create"]]
        self.assertEqual(tab[tab.index("--label") + 1], "t1")
        # Three run panes are live: end one, so the 4th opens under the 3-agent limit.
        u8 = next(d for d in self.started if d.startswith("u8-"))
        self.lanes("cancel", "--task", "t1", "--dispatch", u8, **self.herdr_env())
        ledger = self.repository("ledger")
        before = len(self.herdr_calls())
        self.begin(work="U6", checkout=ledger, env=self.herdr_env())
        [tab] = [c for c in self.herdr_calls()[before:] if c[:2] == ["tab", "create"]]
        self.assertEqual(tab[tab.index("--label") + 1], "ledger")
        labels = [c[c.index("--label") + 1] for c in self.herdr_calls() if c[:2] == ["tab", "create"]]
        self.assertEqual(labels, ["billing-svc", "t1", "ledger"])


if __name__ == "__main__":
    unittest.main()
