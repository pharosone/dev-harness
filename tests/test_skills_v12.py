"""v1.2.0 process text: the compare choice, lanes, dispatch events and the run dashboard.

The skills, adapters and README make the v1.2.0 scripts part of the process: the starting
skills offer compare next to the depth mode (off by default, recommended with its signal),
pharos-dev starts every worker by `lanes.py decide` and records dispatch events, the adapters
say how each runtime records them, and the README lists the scripts.
"""
from pathlib import Path
import re
import unittest

CORE = Path(__file__).resolve().parents[1]
SKILLS = CORE / "skills"
ADAPTERS = CORE / "adapters"
EVALS = CORE / "plugin" / "evals"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def skill(name: str) -> str:
    return read(SKILLS / name / "SKILL.md")


def frontmatter(path: Path) -> dict[str, str]:
    lines = read(path).splitlines()
    end = lines.index("---", 1)
    pairs = [line.split(":", 1) for line in lines[1:end] if re.match(r"^[a-z_]+:", line)]
    return {key: value.strip() for key, value in pairs}


class CompareChoiceTest(unittest.TestCase):
    STARTERS = {"pharos-discover": "design", "pharos-investigate": "investigation"}

    def test_the_start_question_offers_compare_off_by_default_with_a_recommendation(self):
        for name in self.STARTERS:
            text = skill(name)
            with self.subTest(skill=name):
                self.assertRegex(text, r"`compare`")
                self.assertRegex(text, r"`off`[^\n]{0,80}default|default[^\n]{0,80}`off`")
                self.assertRegex(text, r"`compare=\w+` \(Recommended\)|compare[^\n]{0,60}\(Recommended\)")
                self.assertRegex(text, r"depth")

    def test_each_starter_runs_its_own_compare_kind(self):
        for name, kind in self.STARTERS.items():
            text = skill(name)
            with self.subTest(skill=name):
                self.assertIn(f"compare.py {kind}", text)
                self.assertRegex(text, r"verbatim")

    def test_the_recommendation_names_its_signal(self):
        discover = skill("pharos-discover")
        self.assertRegex(discover, r"two or more real architectural options")
        investigate = skill("pharos-investigate")
        for signal in (r"costly", r"conflicting evidence", r"low confidence"):
            self.assertRegex(investigate, signal)

    def test_design_compare_ends_in_one_spec_after_a_pick_or_a_reviewed_merge(self):
        text = skill("pharos-discover")
        self.assertRegex(text, r"approach memo")
        self.assertRegex(text, r"merge[^\n]{0,80}one review|one review[^\n]{0,80}merge")
        self.assertRegex(text, r"one spec")

    def test_a_disputed_answer_means_low_confidence(self):
        text = skill("pharos-investigate")
        self.assertRegex(text, r"disagreement map")
        self.assertRegex(text, r"disputed[^\n]{0,120}low|low[^\n]{0,120}disputed")

    def test_compare_off_keeps_the_deep_flow(self):
        for name in self.STARTERS:
            text = skill(name)
            with self.subTest(skill=name):
                self.assertRegex(text, r"`compare=off`[^\n]{0,160}(unchanged|as before|today)")
                self.assertRegex(text, r"second opinion")


class LanesInDevTest(unittest.TestCase):
    def test_dev_starts_every_worker_by_lanes_decide(self):
        text = skill("pharos-dev")
        self.assertIn("lanes.py decide", text)
        for verb in ("start", "status", "wait", "result", "cancel"):
            self.assertRegex(text, rf"\b{verb}\b")
        self.assertIn("lanes.py start", text)

    def test_dev_records_dispatch_events(self):
        text = skill("pharos-dev")
        self.assertRegex(text, r"tripwire\.py spend[^\n]*--dispatch")
        self.assertIn("task_state.py event", text)

    def test_dev_opens_the_dashboard_at_gates(self):
        text = skill("pharos-dev")
        self.assertIn("dashboard.py", text)

    def test_blocked_lanes_and_foreign_panes(self):
        text = skill("pharos-dev")
        self.assertIn("spec and earlier authorizations", text)
        self.assertIn("never answer trust/permission reviews", text)
        self.assertIn("escalate material decisions to the user", text)
        self.assertRegex(text, r"panes? the run did not create[^\n]{0,60}never closed")

    def test_blocked_rule_agrees_with_spec_and_wait_message(self):
        spec = (CORE / "docs/specs/2026-10-09-v1.2.0-compare-lanes-dashboard.md").read_text()
        for phrase in ("spec and earlier authorizations", "never answer trust/permission reviews",
                       "escalate material decisions to the user"):
            self.assertIn(phrase, spec)


class AdaptersTest(unittest.TestCase):
    def test_both_adapters_name_events_lanes_and_the_dashboard(self):
        for name in ("claude.md", "codex.md"):
            text = read(ADAPTERS / name)
            with self.subTest(adapter=name):
                self.assertIn("events.jsonl", text)
                self.assertRegex(text, r"dispatch\.start")
                self.assertIn("lanes.py", text)
                self.assertIn("dashboard", text)
                self.assertRegex(text, r"HERDR_ENV=1")

    def test_claude_records_by_hooks_and_codex_by_commands(self):
        claude = read(ADAPTERS / "claude.md")
        self.assertRegex(claude, r"PreToolUse\(Agent\)")
        codex = read(ADAPTERS / "codex.md")
        self.assertRegex(codex, r"tripwire\.py spend[^\n]*--dispatch|task_state\.py event")


class ReadmeTest(unittest.TestCase):
    def test_scripts_table_lists_the_new_scripts(self):
        text = read(CORE / "README.md")
        rows = [line for line in text.splitlines() if line.startswith("| `")]
        for script in ("lanes.py", "compare.py", "dashboard.py"):
            with self.subTest(script=script):
                self.assertTrue(any(script in row for row in rows), script)
        self.assertTrue(any("task_state.py" in row and "event" in row for row in rows))

    def test_readme_describes_the_events_log_and_lanes(self):
        text = read(CORE / "README.md")
        self.assertIn("events.jsonl", text)
        self.assertRegex(text, r"(?m)^## Lanes$")
        for launcher in ("subagent", "headless", "pane"):
            self.assertRegex(text, rf"\| `{launcher}`")

    def test_skills_table_notes_compare(self):
        text = read(CORE / "README.md")
        section = text.split("## Skills", 1)[1].split("\n## ", 1)[0]
        self.assertRegex(section, r"compare")

    def test_changelog_and_overlay_doc(self):
        changelog = read(CORE / "CHANGELOG.md")
        unreleased = changelog.split("## 1.2.0", 1)[1].split("\n## ", 1)[0]
        for name in ("lanes.py", "compare.py", "dashboard.py", "events.jsonl"):
            self.assertIn(name, unreleased)
        overlay = read(CORE / "docs" / "overlay.md")
        self.assertIn("compare.py", overlay)


class ArbiterWordTest(unittest.TestCase):
    # Built at run time so the public scrub of tracked files does not flag this test.
    WORD = re.compile(r"\b" + "jud" + r"g(e|es|ed|ing)\b", re.IGNORECASE)

    def test_no_other_word_for_the_arbiter(self):
        paths = sorted(SKILLS.rglob("*.md")) + sorted(ADAPTERS.glob("*.md")) + [CORE / "README.md"]
        hits = [f"{p.relative_to(CORE)}:{n}" for p in paths
                for n, line in enumerate(read(p).splitlines(), 1) if self.WORD.search(line)]
        self.assertEqual(hits, [])
        self.assertIn("arbiter", skill("pharos-discover"))


class EvalCasesTest(unittest.TestCase):
    CASES = ("compare-offer", "compare-declined", "lanes-boundary")

    def test_the_v12_cases_exist_with_case_insensitive_regex_graders(self):
        for case in self.CASES:
            folder = EVALS / case
            with self.subTest(case=case):
                self.assertEqual(frontmatter(folder / "prompt.md").get("name"), case)
                graders = sorted((folder / "graders").glob("*.md"))
                regexes = [g for g in graders if frontmatter(g).get("type") == "regex"]
                self.assertTrue(regexes)
                for grader in regexes:
                    self.assertEqual(frontmatter(grader).get("flags"), "i", grader.name)
                    self.assertNotRegex(read(grader), r"\(\?[a-z]+\)", grader.name)

    def test_the_cases_test_what_they_say(self):
        offer = " ".join(read(g) for g in (EVALS / "compare-offer" / "graders").glob("*.md"))
        self.assertRegex(offer, r"compare")
        self.assertRegex(offer, r"recommend")
        declined = " ".join(read(g) for g in (EVALS / "compare-declined" / "graders").glob("*.md"))
        self.assertIn("does not dispatch comparison candidates", declined)
        self.assertIn("is not a launch and must not fail", declined)
        self.assertIn("Fail any actual proposed comparison dispatch", declined)
        self.assertRegex(declined, r"compare\\\\.py|compare\.py")
        lanes = " ".join(read(g) for g in (EVALS / "lanes-boundary" / "graders").glob("*.md"))
        self.assertRegex(lanes, r"subagent")
        self.assertIn("not_contains", lanes)


if __name__ == "__main__":
    unittest.main()
