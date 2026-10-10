"""Vendor balancing wired into the process: the dashboard's Vendors section and the docs.

The dashboard reads usage through balance.py (fixture lanes logs, $CODEX_HOME, $XDG_STATE_HOME;
the user's real ~/.codex and ~/.claude are never read), shows every window, each vendor's
runway and share of the run's dispatches, and the latest `balance.pick` per role. Unknown stays
unknown; a usage read that fails leaves the page whole. pharos-dev picks writers and reviewers
through `balance.py pick`; the overlay doc, README and CHANGELOG describe it.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

CORE = Path(__file__).resolve().parents[1]
SCRIPTS = CORE / "scripts"
DASHBOARD = SCRIPTS / "dashboard.py"
TASK_STATE = SCRIPTS / "task_state.py"
sys.path.insert(0, str(SCRIPTS))
import balance  # noqa: E402
import dashboard  # noqa: E402

NOW_ISO = "2026-10-09T12:00:00Z"
NOW = int(datetime(2026, 10, 9, 12, tzinfo=timezone.utc).timestamp())
FIVE_HOUR, SEVEN_DAY = 300, 10080


def resets_for(elapsed: float, minutes: int) -> int:
    return int(round(NOW + (1 - elapsed) * minutes * 60))


def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def write_lines(path: Path, lines: list, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def claude_event(five: tuple, seven: tuple) -> dict:
    return {"type": "rate_limit_event", "rate_limit_info": {
        "status": "allowed", "resetsAt": seven[1], "rateLimitType": "seven_day",
        "utilization": seven[0], "unifiedWindows": {
            "five_hour": {"utilization": five[0], "resetsAt": five[1]},
            "seven_day": {"utilization": seven[0], "resetsAt": seven[1]}}}}


def codex_line(primary: tuple, secondary: tuple | None) -> dict:
    def window(value):
        return None if value is None else {"used_percent": value[0], "window_minutes": value[1],
                                           "resets_at": value[2]}
    return {"type": "event_msg", "payload": {"type": "token_count", "rate_limits": {
        "primary": window(primary), "secondary": window(secondary)}}}


class WiringTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo = self.temp / "repo"
        self.codex_home = self.temp / "codex"
        self.state_home = self.temp / "state"
        for directory in (self.repo, self.temp / "project", self.codex_home, self.state_home,
                          self.temp / "home"):
            directory.mkdir()
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "HERDR_", "HARNESS_", "CODEX_", "XDG_"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        HARNESS_PROJECT=str(self.temp / "project"),
                        CODEX_HOME=str(self.codex_home), XDG_STATE_HOME=str(self.state_home),
                        HOME=str(self.temp / "home"))
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Harness Test"),
                     ("config", "user.email", "harness@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.run(["git", *args], cwd=self.repo, env=self.env, check=True)
        (self.repo / ".gitignore").write_text(".harness/\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, env=self.env, check=True)
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=self.repo, env=self.env,
                       check=True)
        self.script(TASK_STATE, "init", "t1")
        self.run_dir = self.repo / ".harness" / "tasks" / "t1"

    def script(self, path: Path, *args) -> str:
        result = subprocess.run([sys.executable, str(path), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def event(self, kind: str, **fields):
        self.script(TASK_STATE, "event", "t1", kind, "--data", json.dumps(fields))

    def render(self) -> str:
        out = self.script(DASHBOARD, "t1", "--now", NOW_ISO)
        return Path(out).read_text(encoding="utf-8")

    def known_usage(self):
        # Claude: five_hour 40 % at half the window (runway 1.2), seven_day 20 % at half (1.6).
        write_lines(self.run_dir / "lanes" / "d1.log", [
            {"type": "system"},
            claude_event((0.4, resets_for(0.5, FIVE_HOUR)), (0.2, resets_for(0.5, SEVEN_DAY))),
            {"type": "result", "result": "ok"}], mtime=NOW - 600)
        # Codex: primary 50 % at a quarter of five hours (runway 0.67), secondary 10 % (1.8).
        write_lines(self.codex_home / "sessions" / "2026" / "10" / "09" / "rollout-x.jsonl", [
            {"type": "session_meta"},
            codex_line((50.0, FIVE_HOUR, resets_for(0.25, FIVE_HOUR)),
                       (10.0, SEVEN_DAY, resets_for(0.5, SEVEN_DAY)))], mtime=NOW - 300)

    def dispatches_and_picks(self):
        for index in range(2):
            self.event("dispatch.start", dispatch=f"c{index}", runtime="claude", work="U1")
        self.event("dispatch.start", dispatch="x0", runtime="codex", work="U2")
        self.event("dispatch.start", dispatch="m0", runtime="codex", work="cmp-design-a")
        self.event("balance.pick", runtime="claude", model="model-c", effort="medium",
                   profile="implementation", role="implementer",
                   reason="claude: OLDER-PICK-REASON.")
        self.event("balance.pick", runtime="codex", model="model-x", effort="high",
                   profile="critical-review", role="reviewer", not_runtime="claude",
                   reason="codex: --not-runtime claude.")
        self.event("balance.pick", runtime="codex", model="model-x", effort="high",
                   profile="implementation", role="implementer",
                   reason="codex: its share 0.25 of 4 dispatches is below min_share 0.30.")


class VendorsSectionTest(WiringTest):
    def test_each_window_with_used_reset_observed_and_source(self):
        self.known_usage()
        vendors = section(self.render(), "vendors")
        five = row(vendors, "window-claude-five_hour")
        self.assertEqual(cell(five, "window"), "five_hour")
        self.assertEqual(cell(five, "used"), "40%")
        self.assertEqual(cell(five, "resets"), stamp(resets_for(0.5, FIVE_HOUR)))
        self.assertEqual(cell(five, "observed"), stamp(NOW - 600))
        self.assertIn("d1.log", cell(five, "source"))
        self.assertEqual(cell(row(vendors, "window-claude-seven_day"), "used"), "20%")
        primary = row(vendors, "window-codex-five_hour")
        self.assertEqual(cell(primary, "used"), "50%")
        self.assertEqual(cell(primary, "resets"), stamp(resets_for(0.25, FIVE_HOUR)))
        self.assertEqual(cell(primary, "observed"), stamp(NOW - 300))
        self.assertIn("rollout-x.jsonl", cell(primary, "source"))
        self.assertEqual(cell(row(vendors, "window-codex-seven_day"), "used"), "10%")

    def test_runway_is_the_smallest_current_window(self):
        self.known_usage()
        vendors = section(self.render(), "vendors")
        claude, codex = row(vendors, "vendor-claude"), row(vendors, "vendor-codex")
        self.assertEqual(cell(claude, "status"), "known")
        self.assertEqual(cell(claude, "runway"), "1.20")
        self.assertEqual(cell(codex, "status"), "known")
        self.assertEqual(cell(codex, "runway"), "0.67")

    def test_share_counts_dispatches_by_runtime_without_compare_work(self):
        self.known_usage()
        self.dispatches_and_picks()
        vendors = section(self.render(), "vendors")
        self.assertEqual(cell(row(vendors, "vendor-claude"), "share"), "67% (2 of 3)")
        self.assertEqual(cell(row(vendors, "vendor-codex"), "share"), "33% (1 of 3)")

    def test_latest_pick_per_role_with_its_reason(self):
        self.dispatches_and_picks()
        vendors = section(self.render(), "vendors")
        implementer = row(vendors, "pick-implementer")
        self.assertEqual(cell(implementer, "runtime"), "codex")
        self.assertEqual(cell(implementer, "profile"), "implementation")
        self.assertIn("below min_share 0.30", cell(implementer, "reason"))
        self.assertNotIn("OLDER-PICK-REASON", vendors)
        reviewer = row(vendors, "pick-reviewer")
        self.assertEqual(cell(reviewer, "runtime"), "codex")
        self.assertIn("--not-runtime claude", cell(reviewer, "reason"))

    def test_unknown_usage_is_shown_as_unknown_never_zero(self):
        # Codex holds only windows whose reset has passed; Claude has no observation at all.
        write_lines(self.codex_home / "sessions" / "2026" / "10" / "08" / "rollout-old.jsonl",
                    [codex_line((70.0, FIVE_HOUR, NOW - 60), None)], mtime=NOW - 3600)
        vendors = section(self.render(), "vendors")
        for vendor in ("claude", "codex"):
            with self.subTest(vendor=vendor):
                vendor_row = row(vendors, f"vendor-{vendor}")
                self.assertEqual(cell(vendor_row, "status"), "unknown")
                self.assertEqual(cell(vendor_row, "runway"), "unknown")
                self.assertNotRegex(cell(vendor_row, "share"), r"^0")
                self.assertNotRegex(cell(vendor_row, "runway"), r"\d")
        self.assertNotIn("window-claude-", vendors)
        self.assertNotIn("usage unavailable", vendors)

    def test_a_usage_read_failure_leaves_the_page_whole(self):
        self.dispatches_and_picks()
        out = self.run_dir / "dashboard.html"
        now = datetime.fromtimestamp(NOW, timezone.utc)
        with mock.patch.object(balance, "usage", side_effect=OSError("state unreadable")):
            page = dashboard.render(self.run_dir, self.repo, out, now, timedelta(minutes=15))
        vendors = section(page, "vendors")
        self.assertIn("usage unavailable", vendors)
        self.assertIn("pick-implementer", vendors)
        for name in ("running", "pending-gates", "spend", "reports"):
            section(page, name)

    def test_the_section_stays_self_contained(self):
        self.known_usage()
        self.dispatches_and_picks()
        page = self.render()
        vendors = section(page, "vendors")
        self.assertNotRegex(vendors, r"https?://|<script|<img|<iframe|src=")
        self.assertNotRegex(page, r"<script|<link ")
        self.assertLess(page.index('<section id="running">'), page.index('<section id="vendors">'))


class ProcessTextTest(unittest.TestCase):
    @staticmethod
    def read(relative: str) -> str:
        return (CORE / relative).read_text(encoding="utf-8")

    def test_dev_skill_picks_writers_and_reviewers_through_balance(self):
        text = self.read("skills/pharos-dev/SKILL.md")
        self.assertIn("balance.py pick", text)
        self.assertRegex(text, r"balance\.py pick --task <task> --profile <profile> --role <role>")
        self.assertIn("--not-runtime", text)
        self.assertRegex(text, r"[Ee]xit 6[^\n]{0,80}ask the user")
        self.assertIn("balance.py observe", text)
        self.assertRegex(text, r"same profile on both vendors")
        self.assertRegex(text, r"reason[^\n]{0,80}run\.md|run\.md[^\n]{0,80}reason")

    def test_overlay_doc_documents_the_balance_keys(self):
        text = self.read("docs/overlay.md")
        match = re.search(r"(?ms)^### balance\b(.*?)(?=^##)", text)
        self.assertIsNotNone(match, "docs/overlay.md has no ### balance section")
        body = match.group(1)
        for key, default in (("min_share", "0.3"), ("stop_at", "0.85"), ("hysteresis", "0.15"),
                             ("min_elapsed", "0.25")):
            with self.subTest(key=key):
                self.assertRegex(body, rf"`{key}`[^\n]*\b{re.escape(default)}\b")
        self.assertIn("balance.py", text.split("## project.json", 1)[0])

    def test_readme_and_changelog_list_balance(self):
        rows = [line for line in self.read("README.md").splitlines() if line.startswith("| `")]
        self.assertTrue(any(line.startswith("| `balance.py") for line in rows))
        changelog = self.read("CHANGELOG.md")
        unreleased = changelog.split("## 1.2.0", 1)[1].split("\n## ", 1)[0]
        self.assertIn("Vendor balancing", unreleased)
        self.assertIn("balance.py", unreleased)


if __name__ == "__main__":
    unittest.main()
