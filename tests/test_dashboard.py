"""The run dashboard: one self-contained page that shows a run and never changes it."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
DASHBOARD = SCRIPTS / "dashboard.py"
TASK_STATE = SCRIPTS / "task_state.py"
TRIPWIRE = SCRIPTS / "tripwire.py"
NOW = "2026-10-09T12:00:00Z"


def section(page: str, name: str) -> str:
    match = re.search(rf'<section id="{re.escape(name)}">(.*?)</section>', page, re.DOTALL)
    if not match:
        raise AssertionError(f"section {name!r} missing")
    return match.group(1)


def row(page: str, row_id: str) -> str:
    match = re.search(rf'<tr id="{re.escape(row_id)}"[^>]*>(.*?)</tr>', page, re.DOTALL)
    if not match:
        raise AssertionError(f"row {row_id!r} missing")
    return match.group(1)


def cell(row_html: str, column: str) -> str:
    match = re.search(rf'<td class="{re.escape(column)}">(.*?)</td>', row_html, re.DOTALL)
    if not match:
        raise AssertionError(f"cell {column!r} missing in {row_html!r}")
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


def snapshot(root: Path, skip: set[Path]) -> dict:
    state = {}
    for path in sorted(root.rglob("*")):
        if path in skip:
            continue
        info = path.lstat()
        content = b"" if path.is_dir() or path.is_symlink() else path.read_bytes()
        state[str(path.relative_to(root))] = (info.st_mtime_ns, info.st_mode,
                                              hashlib.sha256(content).hexdigest())
    return state


class DashboardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "repo"
        self.root.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        (self.root / ".gitignore").write_text(".harness/\nred.txt\n")
        (self.root / "spec.md").write_text("# Approved spec\n")
        (self.root / "tests").mkdir()
        (self.root / "tests/test_unit.py").write_text("# frozen test\n")
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.ok(self.script(TASK_STATE, "init", "feature", "--spec", "spec.md"))
        self.run_dir = self.root / ".harness/tasks/feature"
        self.events = []

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env, text=True)

    def script(self, path, *args):
        return subprocess.run([sys.executable, str(path), *args], cwd=self.root, env=self.env,
                              capture_output=True, text=True)

    def ok(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def event(self, event_type, at, **fields):
        self.events.append({"v": 1, "id": f"e{len(self.events)}", "at": at, "type": event_type, **fields})

    def write_events(self, tail=""):
        lines = "".join(json.dumps(e) + "\n" for e in self.events)
        (self.run_dir / "events.jsonl").write_text(lines + tail)

    def write(self, relative, text):
        path = self.run_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def render(self, *args):
        output = self.ok(self.script(DASHBOARD, "feature", "--now", NOW, *args))
        return Path(output).read_text(encoding="utf-8")

    def start(self, dispatch, at, work="U1", **fields):
        base = {"dispatch": dispatch, "role": "implementer", "runtime": "claude",
                "model": "model-a", "effort": "high", "launcher": "subagent", "work": work,
                "report": f"reports/{work}.md", "pane": None}
        base.update(fields)
        self.event("dispatch.start", at, **base)

    def end(self, dispatch, at, status="done", **usage):
        values = {"tool_calls": None, "input_tokens": None, "output_tokens": None,
                  "duration_s": None}
        values.update(usage)
        self.event("dispatch.end", at, dispatch=dispatch, status=status, usage=values)

    # --- contract -------------------------------------------------------------------------

    def test_a_missing_run_exits_one(self):
        result = self.script(DASHBOARD, "nothing-here")
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)

    def test_default_output_is_the_run_dashboard_and_its_path_is_printed(self):
        self.write_events()
        result = self.script(DASHBOARD, "feature", "--now", NOW)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = (self.run_dir / "dashboard.html").resolve()
        self.assertEqual(Path(result.stdout.strip()).resolve(), expected)
        page = expected.read_text(encoding="utf-8")
        self.assertIn("<!doctype html>", page.lower())
        self.assertIn('name="viewport"', page)
        self.assertIn("prefers-color-scheme: dark", page)

    def test_out_outside_a_harness_directory_is_refused(self):
        outside = Path(self.temp.name) / "elsewhere" / "dashboard.html"
        outside.parent.mkdir()
        result = self.script(DASHBOARD, "feature", "--now", NOW, "--out", str(outside))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(outside.exists())
        sneaky = self.root / ".harness" / ".." / "dashboard.html"
        result = self.script(DASHBOARD, "feature", "--now", NOW, "--out", str(sneaky))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "dashboard.html").exists())

    def test_out_inside_harness_links_reports_relative_to_the_page(self):
        self.write("reports/U1.md", "report\n")
        out = self.root / ".harness" / "board.html"
        page = self.render("--out", str(out))
        self.assertTrue(out.is_file())
        self.assertIn('href="tasks/feature/reports/U1.md"', page)

    # --- first screen ---------------------------------------------------------------------

    def test_unknown_usage_is_shown_as_unknown_never_zero(self):
        self.start("d1", "2026-10-09T10:00:00Z")
        self.end("d1", "2026-10-09T10:30:00Z", tool_calls=None, input_tokens=1200,
                 output_tokens=None, duration_s=None)
        self.start("d2", "2026-10-09T10:40:00Z")
        self.end("d2", "2026-10-09T11:00:00Z", tool_calls=12, input_tokens=None,
                 output_tokens=300, duration_s=600)
        self.start("d3", "2026-10-09T10:00:00Z", work="U2")
        self.end("d3", "2026-10-09T10:10:00Z")
        self.write_events()
        page = self.render()
        u1, u2 = row(page, "spend-U1"), row(page, "spend-U2")
        self.assertEqual(cell(u1, "dispatches"), "2")
        self.assertEqual(cell(u1, "tool_calls"), "12 + unknown")
        self.assertEqual(cell(u1, "input_tokens"), "1200 + unknown")
        self.assertEqual(cell(u1, "output_tokens"), "300 + unknown")
        self.assertIn("unknown", cell(u1, "duration"))
        for column in ("tool_calls", "input_tokens", "output_tokens", "duration"):
            self.assertEqual(cell(u2, column), "unknown", column)

    def test_running_and_stale_dispatches_by_now_and_stale_minutes(self):
        self.start("fresh", "2026-10-09T11:55:00Z", model="model-b", effort="xhigh",
                   launcher="pane", pane="unit-u1", runtime="codex", role="investigator")
        self.start("quiet", "2026-10-09T11:00:00Z")
        self.start("lane", "2026-10-09T10:00:00Z", work="U2", launcher="headless")
        self.start("done", "2026-10-09T09:00:00Z")
        self.end("done", "2026-10-09T09:30:00Z", status="failed", tool_calls=3)
        self.write_events()
        self.write("lanes/lane.json", json.dumps({"last_activity": "2026-10-09T11:58:00Z"}))
        page = self.render("--stale-minutes", "30")
        running = section(page, "running")
        fresh = row(running, "dispatch-fresh")
        self.assertEqual(cell(fresh, "state"), "running")
        for value in ("investigator", "codex", "model-b", "xhigh", "pane", "unit-u1",
                      "2026-10-09T11:55:00Z"):
            self.assertIn(value, fresh)
        quiet = row(running, "dispatch-quiet")
        self.assertEqual(cell(quiet, "state"), "liveness unknown")
        self.assertNotIn("failed", quiet)
        self.assertEqual(cell(row(running, "dispatch-lane"), "state"), "running")
        self.assertNotIn("dispatch-done", running)
        finished = section(page, "finished")
        self.assertEqual(cell(row(finished, "dispatch-done"), "state"), "failed")

        page = self.render("--stale-minutes", "90")
        self.assertEqual(cell(row(section(page, "running"), "dispatch-quiet"), "state"), "running")

    def test_pending_and_answered_gates(self):
        self.write("gates/G1.md", "card one\n")
        self.write("gates/G4.md", "card four\n")
        self.event("gate.open", "2026-10-09T09:00:00Z", gate="G1", card="gates/G1.md")
        self.event("gate.answer", "2026-10-09T09:10:00Z", gate="G1", answer="approve",
                   source="looks right, go")
        self.event("gate.open", "2026-10-09T09:20:00Z", gate="G2", card="gates/G2.md")
        self.write_events()
        page = self.render()
        pending, answered = section(page, "pending-gates"), section(page, "answered-gates")
        self.assertIn("G4", pending)
        self.assertIn("G2", pending)
        self.assertNotIn("G1", pending)
        self.assertIn("G1", answered)
        self.assertIn("approve", answered)
        self.assertIn("looks right, go", answered)
        self.assertIn('href="gates/G4.md"', pending)

    def test_tripwires_shown_without_recording(self):
        red = self.root / "red.txt"
        red.write_text("FAILED (failures=1)\n")
        self.ok(self.script(TRIPWIRE, "scope", "feature", "U1", "--base", "HEAD", "tests/**"))
        self.ok(self.script(TASK_STATE, "freeze", "feature", "U1", "--red-log", "red.txt",
                            "tests/test_unit.py"))
        (self.root / "tests/test_unit.py").write_text("# weakened\n")
        counter = self.run_dir / "units/U1-trips.json"
        counter.write_text(json.dumps({"verify_failures": 2}) + "\n")
        before = counter.read_bytes(), counter.stat().st_mtime_ns
        page = self.render()
        self.assertEqual((counter.read_bytes(), counter.stat().st_mtime_ns), before)
        trips = section(page, "tripwires")
        self.assertIn("U1", trips)
        self.assertIn("Frozen tests were changed in 2 dispatches", trips)
        self.assertEqual(json.loads(counter.read_text())["verify_failures"], 2)

    def test_compare_status(self):
        self.event("compare.start", "2026-10-09T09:00:00Z", compare="c1", kind="design")
        self.event("compare.start", "2026-10-09T09:05:00Z", compare="c2", kind="investigation")
        self.event("compare.result", "2026-10-09T09:40:00Z", compare="c1", confidence="medium",
                   stable=False)
        self.write_events()
        compare = section(self.render(), "compare")
        self.assertIn("c1", compare)
        self.assertIn("medium", compare)
        self.assertIn("c2", compare)
        self.assertIn("running", row(compare, "compare-c2"))

    # --- robustness -----------------------------------------------------------------------

    def test_legacy_run_without_events_renders_with_unknowns(self):
        (self.run_dir / "units").mkdir(exist_ok=True)
        self.write("units/U1-spend.jsonl", json.dumps({"tool_uses": 9, "duration_ms": 120000}) + "\n")
        self.write("units/U2-tests.json", json.dumps({"files": {}}) + "\n")
        self.write("gates/G1.md", "legacy card\n")
        page = self.render()
        self.assertIn("no events log", page)
        u1 = row(page, "spend-U1")
        self.assertEqual(cell(u1, "tool_calls"), "9")
        self.assertEqual(cell(u1, "input_tokens"), "unknown")
        self.assertEqual(cell(u1, "output_tokens"), "unknown")
        self.assertEqual(cell(row(page, "spend-U2"), "tool_calls"), "unknown")
        self.assertIn("G1", page)
        self.assertNotIn("legacy card", page)

    def test_a_partial_line_renders_and_is_counted(self):
        self.start("d1", "2026-10-09T11:50:00Z")
        self.write_events(tail='{"v": 1, "id": "cut", "type": "dispatch.st')
        page = self.render()
        self.assertRegex(page, r'<span id="unreadable-count">1</span>')
        self.assertIn("dispatch-d1", section(page, "running"))

    def test_a_tests_only_unit_appears(self):
        self.write("units/U9-tests.json", json.dumps({"files": {}}) + "\n")
        self.write_events()
        self.assertIn("spend-U9", self.render())

    # --- safety ---------------------------------------------------------------------------

    def test_no_external_references(self):
        self.start("d1", "2026-10-09T11:50:00Z", report="https://example.invalid/report.md",
                   pane="http://example.invalid/")
        self.start("d2", "2026-10-09T11:50:00Z", report="../../../outside.md")
        self.event("gate.answer", "2026-10-09T09:10:00Z", gate="G1", answer="ok",
                   source='<script src="https://example.invalid/x.js"></script>')
        self.write_events()
        self.write("reports/U1.md", "x\n")
        page = self.render()
        self.assertNotRegex(page.lower(), r"<script|<link|<img|<iframe|<object|<embed|url\(|@import")
        self.assertIn("&lt;script", page)
        for tag in re.findall(r"<[^>]+>", page):
            self.assertNotRegex(tag.lower(), r"https?:|//|\bsrc\s*=|\baction\s*=", tag)
        for href in re.findall(r'href="([^"]*)"', page):
            self.assertNotRegex(href, r"^/|:|(^|/)\.\.(/|$)", href)

    def test_report_text_is_never_embedded(self):
        self.write("reports/U1.md", "REPORT-BODY-SENTINEL\n")
        self.write("gates/G1.md", "GATE-CARD-SENTINEL\n")
        with (self.run_dir / "run.md").open("a") as stream:
            stream.write("RUN-NARRATIVE-SENTINEL\n")
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("OUTSIDE-SENTINEL\n")
        (self.run_dir / "reports/linked.md").symlink_to(outside)
        self.start("d1", "2026-10-09T11:50:00Z", report="reports/U1.md")
        self.write_events()
        page = self.render()
        for sentinel in ("REPORT-BODY", "GATE-CARD", "RUN-NARRATIVE", "OUTSIDE"):
            self.assertNotIn(f"{sentinel}-SENTINEL", page)
        reports = section(page, "reports")
        self.assertIn('href="reports/U1.md"', reports)
        self.assertIn('href="run.md"', reports)
        self.assertNotIn('href="reports/linked.md"', page)

    def test_secret_looking_values_in_event_fields_are_redacted(self):
        token = "sk-" + "ant-" + "A1b2C3d4" * 6
        github = "gh" + "p_" + "Z9" * 20
        self.start("d1", "2026-10-09T11:50:00Z", model=token)
        self.event("gate.answer", "2026-10-09T09:10:00Z", gate="G1", answer="ok",
                   source=f"use {github} please")
        self.write_events()
        page = self.render()
        self.assertNotIn(token, page)
        self.assertNotIn(github, page)
        self.assertIn("[redacted]", section(page, "running"))
        self.assertIn("[redacted]", section(page, "answered-gates"))

    def test_values_are_html_escaped(self):
        self.start("d1", "2026-10-09T11:50:00Z", role="<b>boss</b>")
        self.write_events()
        page = self.render()
        self.assertNotIn("<b>boss</b>", page)
        self.assertIn("&lt;b&gt;boss&lt;/b&gt;", page)

    def test_nothing_in_the_run_directory_changes_except_the_output(self):
        self.write("gates/G1.md", "card\n")
        self.write("units/U1-spend.jsonl", json.dumps({"tool_uses": 4}) + "\n")
        self.write("lanes/d1.json", json.dumps({"last_activity": "2026-10-09T11:59:00Z"}))
        self.start("d1", "2026-10-09T11:50:00Z")
        self.write_events(tail="not json\n")
        output = self.run_dir / "dashboard.html"
        before = snapshot(self.run_dir, {output})
        self.render()
        self.render()
        self.assertEqual(snapshot(self.run_dir, {output}), before)


class RegenerateLockTest(unittest.TestCase):
    """A compare and its detached lane supervisors regenerate one page; a regeneration that
    read older events must not write after a newer one, so reading and writing hold a lock."""

    @unittest.skipIf(sys.platform == "win32", "fcntl locks are POSIX only")
    def test_render_and_write_happen_under_one_exclusive_lock(self):
        import fcntl
        from unittest import mock
        sys.path.insert(0, str(SCRIPTS))
        import dashboard
        with tempfile.TemporaryDirectory() as temp:
            primary = Path(temp).resolve()
            run = primary / ".harness" / "tasks" / "t1"
            run.mkdir(parents=True)
            held = []

            def render(*args):
                with open(run / ".dashboard-lock") as other:
                    try:
                        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        held.append(False)
                    except BlockingIOError:
                        held.append(True)
                return "<html></html>"

            with mock.patch.object(dashboard, "render", render):
                self.assertEqual(dashboard.regenerate(run, primary), run / "dashboard.html")
            self.assertEqual(held, [True])
            self.assertEqual((run / "dashboard.html").read_text(), "<html></html>")
            # The lock file is not one of the run's files the page lists.
            page = dashboard.render(run, primary, run / "dashboard.html",
                                    dashboard.datetime.now(dashboard.timezone.utc),
                                    dashboard.timedelta(minutes=30))
            self.assertNotIn("dashboard-lock", page)


if __name__ == "__main__":
    unittest.main()
