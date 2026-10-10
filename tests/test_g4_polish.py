"""MF6: polish found by a real compare run (g4-demo, cmp-1a1e7732).

1. When every arbitrated memo has a failed mandatory requirement, judgement.json and
   compare.result carry `none_meets_mandatory: true` and comparison.md's summary says so
   before the winner row; with at least one memo passing (or nothing arbitrated) it is false.
2. The two arbiter passes' `failed_mandatory` (per label) and `disagreements` are merged
   without exact repeats (after normalising whitespace, case and punctuation), within and
   across passes, in order; nothing fuzzier, so paraphrases and claims with the memo names
   swapped stay. `failed_mandatory` stays flat (test_compare pins it);
   `failed_mandatory_by_row` groups each memo's items by "row N" ("rows N", "requirement N",
   any case), "other" for the rest, and comparison.md has one bullet per memo and row, the
   wordings joined by " / ". judgement.json records `merged_from_passes`.
3. lanes, headless usage: a Claude stream's input_tokens include the cache reads and cache
   writes of its result usage (comparable with Codex, whose input_tokens include cached ones);
   `cached_input_tokens` is recorded when the stream gives it.

No real claude, codex or herdr is called: compare's units on in-memory stubs, a fake `claude`
on PATH for the headless lane.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(CORE / "scripts"))
import compare  # noqa: E402
import lanes  # noqa: E402
import test_qa_compare as qa  # noqa: E402  (module import: its TestCases are not re-run)

LANES = CORE / "scripts" / "lanes.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
NONE_MEETS = ("No memo meets every mandatory requirement; the ranking orders imperfect options")
SEALS = {"claude": {"sha256": "a" * 64}, "codex": {"sha256": "b" * 64}}


def entry(failed=None, disagreements=None, ranking=("codex", "claude"), valid=True):
    return {"valid": valid, "ranking": list(ranking), "tie": False,
            "failed_mandatory": failed or {}, "disagreements": disagreements or [],
            "confidence": "medium"}


class ConcludeCase(qa.FolderCase):
    def conclude(self, passes, seals=None):
        obj = qa.stub(compare.Compare, self.folder)
        judgement = compare.conclude(obj, seals or SEALS, passes, qa.HEADLESS)
        result = self.emitted(obj, "compare.result")[0]
        summary = (self.folder / "comparison.md").read_text(encoding="utf-8")
        return judgement, result, summary

    def summary_section(self, summary):
        return summary[summary.index("## Summary"):]


# --- 1. none of the memos meets every mandatory requirement -------------------------------

class NoneMeetsMandatoryTest(ConcludeCase):
    def test_every_memo_failing_is_flagged_and_said_before_the_winner_row(self):
        both = {"codex": ["row 8: timing unproven"], "claude": ["row 6: no event hook"]}
        judgement, result, summary = self.conclude({1: entry(both), 2: entry(both)})
        self.assertIs(judgement["none_meets_mandatory"], True)
        self.assertIs(result["none_meets_mandatory"], True)
        on_disk = json.loads((self.folder / "judgement.json").read_text(encoding="utf-8"))
        self.assertIs(on_disk["none_meets_mandatory"], True)
        section = self.summary_section(summary)
        self.assertIn(NONE_MEETS, section)
        self.assertLess(section.index(NONE_MEETS), section.index("| winner |"))
        # The ranking still orders the options; the flag qualifies it, it does not drop it.
        self.assertEqual(judgement["ranking"], ["codex", "claude"])

    def test_failures_split_across_the_passes_still_count_for_every_memo(self):
        judgement, result, summary = self.conclude(
            {1: entry({"claude": ["row 6: no event hook"]}),
             2: entry({"codex": ["row 8: timing unproven"]})})
        self.assertIs(judgement["none_meets_mandatory"], True)
        self.assertIs(result["none_meets_mandatory"], True)
        self.assertIn(NONE_MEETS, self.summary_section(summary))

    def test_one_memo_passing_is_not_flagged(self):
        failed = {"claude": ["row 6: no event hook"]}
        judgement, result, summary = self.conclude({1: entry(failed), 2: entry(failed)})
        self.assertIs(judgement["none_meets_mandatory"], False)
        self.assertIs(result["none_meets_mandatory"], False)
        self.assertNotIn(NONE_MEETS, summary)

    def test_no_failures_is_not_flagged(self):
        judgement, result, summary = self.conclude({1: entry(), 2: entry()})
        self.assertIs(judgement["none_meets_mandatory"], False)
        self.assertIs(result["none_meets_mandatory"], False)
        self.assertNotIn(NONE_MEETS, summary)

    def test_a_single_unarbitrated_memo_is_not_flagged(self):
        seals = {"claude": {"sha256": "a" * 64},
                 "codex": {"sha256": None, "excluded": "no memo or an empty one"}}
        judgement, result, summary = self.conclude({}, seals=seals)
        self.assertIs(judgement["none_meets_mandatory"], False)
        self.assertIs(result["none_meets_mandatory"], False)
        self.assertNotIn(NONE_MEETS, summary)


# --- 2. merging the two passes without repeats ---------------------------------------------

BASE = "row 4 generation age does not establish idleness"
NEAR = "row 4: generation age does not establish idleness, run-wide"  # a paraphrase: kept


class MergePassesTest(ConcludeCase):
    def test_failed_mandatory_drops_only_exact_repeats_per_label_in_order(self):
        pass1 = {"claude": ["Row 6: leaves the event CLI without regeneration.", BASE],
                 "codex": ["row 8: timing unproven"]}
        pass2 = {"claude": ["row 6 leaves the   EVENT cli without regeneration", NEAR,
                            "Row 4. Generation age does not establish idleness!",
                            "row 7: a sentinel can expose partial content"],
                 "codex": ["Row 8 — timing unproven!", BASE]}
        judgement, _, summary = self.conclude({1: entry(pass1), 2: entry(pass2)})
        self.assertEqual(judgement["failed_mandatory"], {
            "claude": ["Row 6: leaves the event CLI without regeneration.", BASE, NEAR,
                       "row 7: a sentinel can expose partial content"],
            # Merging is per label: the same sentence under another memo is not a repeat.
            "codex": ["row 8: timing unproven", BASE]})
        self.assertEqual(judgement["merged_from_passes"], 2)
        self.assertEqual(summary.count("timing unproven"), 1)

    def test_failed_mandatory_is_grouped_by_row_with_each_wording(self):
        pass1 = {"claude": ["Row 6: leaves the event CLI without regeneration.", BASE,
                            "No acceptance in Safari."],
                 "codex": ["requirement 8: timing unproven"]}
        pass2 = {"claude": ["Rows 7 and 8: partial content can show", NEAR,
                            "row 6 — the direct event CLI is not regenerated",
                            "**Row 10**: no bound on rendering"],
                 "codex": ["ROW 8: lock waiting is unbounded"]}
        judgement, _, summary = self.conclude({1: entry(pass1), 2: entry(pass2)})
        grouped = judgement["failed_mandatory_by_row"]
        self.assertEqual(list(grouped["claude"].items()), [
            ("row 4", [BASE, NEAR]),
            ("row 6", ["Row 6: leaves the event CLI without regeneration.",
                       "row 6 — the direct event CLI is not regenerated"]),
            ("row 7", ["Rows 7 and 8: partial content can show"]),
            ("row 10", ["**Row 10**: no bound on rendering"]),
            ("other", ["No acceptance in Safari."])])
        self.assertEqual(list(grouped["codex"].items()), [
            ("row 8", ["requirement 8: timing unproven", "ROW 8: lock waiting is unbounded"])])
        on_disk = json.loads((self.folder / "judgement.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["failed_mandatory_by_row"], grouped)
        section = summary[summary.index("### Failed mandatory requirements"):]
        bullets = [line for line in section.splitlines()
                   if line.startswith(("- claude, ", "- codex, "))]
        self.assertEqual(len(bullets), 6)  # one per memo and row
        self.assertIn("- claude, row 6: leaves the event CLI without regeneration. / the direct "
                      "event CLI is not regenerated", bullets)
        self.assertIn("- codex, row 8: timing unproven / lock waiting is unbounded", bullets)
        self.assertIn("- claude, row 7: Rows 7 and 8: partial content can show", bullets)
        self.assertIn("- claude, other: No acceptance in Safari.", bullets)

    def test_disagreements_drop_only_exact_repeats_in_order(self):
        first = "Memo codex adds regeneration after direct CLI events; Memo claude does not."
        swapped = "Memo claude adds regeneration after direct CLI events; Memo codex does not."
        pass1 = ["Memo claude reloads the whole document.", first, BASE]
        pass2 = ["memo claude reloads the whole document", NEAR, swapped,
                 "Memo codex derives stale status from snapshot activity.", first]
        judgement, _, summary = self.conclude({1: entry(disagreements=pass1),
                                               2: entry(disagreements=pass2)})
        # A paraphrase and the opposite claim (memo names swapped) are not repeats.
        self.assertEqual(judgement["disagreements"], [
            "Memo claude reloads the whole document.", first, BASE, NEAR, swapped,
            "Memo codex derives stale status from snapshot activity."])
        self.assertEqual(judgement["merged_from_passes"], 2)
        self.assertEqual(summary.count("reloads the whole document"), 1)

    def test_repeats_within_one_pass_are_dropped_too(self):
        pass1 = {"claude": [BASE, "ROW 4: generation age, does not establish idleness"]}
        judgement, _, _ = self.conclude({1: entry(pass1, ["A b.", "a  B"]), 2: entry()})
        self.assertEqual(judgement["failed_mandatory"], {"claude": [BASE]})
        self.assertEqual(judgement["failed_mandatory_by_row"], {"claude": {"row 4": [BASE]}})
        self.assertEqual(judgement["disagreements"], ["A b."])

    def test_merged_from_passes_counts_the_valid_passes(self):
        judgement, _, _ = self.conclude(
            {1: entry({"claude": [BASE]}), 2: {"valid": False, "error": "no fenced JSON block"}})
        self.assertEqual(judgement["merged_from_passes"], 1)
        self.assertEqual(judgement["failed_mandatory"], {"claude": [BASE]})
        seals = {"claude": {"sha256": "a" * 64},
                 "codex": {"sha256": None, "excluded": "no memo or an empty one"}}
        judgement, _, _ = self.conclude({}, seals=seals)
        self.assertEqual(judgement["merged_from_passes"], 0)
        self.assertEqual(judgement["failed_mandatory_by_row"], {})


# --- 3. headless Claude usage counts cached input -------------------------------------------

def feed(*events):
    usage = lanes.Usage()
    for event in events:
        usage.feed(json.dumps(event).encode("utf-8") + b"\n")
    return usage.summary(1.0)


class UsageTest(unittest.TestCase):
    def test_claude_input_tokens_include_cache_reads_and_writes(self):
        summary = feed({"type": "assistant", "message": {"content": []}},
                       {"type": "result", "duration_ms": 2000,
                        "usage": {"input_tokens": 24, "cache_read_input_tokens": 1_000_000,
                                  "cache_creation_input_tokens": 90_000, "output_tokens": 7}})
        self.assertEqual(summary["input_tokens"], 1_090_024)
        self.assertEqual(summary["cached_input_tokens"], 1_000_000)
        self.assertEqual(summary["output_tokens"], 7)

    def test_each_part_counts_when_present(self):
        summary = feed({"type": "result", "usage": {"cache_read_input_tokens": 500,
                                                    "output_tokens": 1}})
        self.assertEqual(summary["input_tokens"], 500)
        self.assertEqual(summary["cached_input_tokens"], 500)
        summary = feed({"type": "result", "usage": {"input_tokens": 3,
                                                    "cache_creation_input_tokens": 40}})
        self.assertEqual(summary["input_tokens"], 43)
        self.assertNotIn("cached_input_tokens", summary)

    def test_a_stream_without_cache_fields_has_no_cached_key(self):
        summary = feed({"type": "result", "duration_ms": 4000,
                        "usage": {"input_tokens": 100, "output_tokens": 20}})
        self.assertEqual(summary, {"tool_calls": 0, "input_tokens": 100, "output_tokens": 20,
                                   "duration_s": 4.0})

    def test_codex_input_tokens_stay_and_its_cached_tokens_are_recorded(self):
        summary = feed({"type": "turn.completed",
                        "usage": {"input_tokens": 1_100_000, "cached_input_tokens": 900_000,
                                  "output_tokens": 30}})
        self.assertEqual(summary["input_tokens"], 1_100_000)
        self.assertEqual(summary["cached_input_tokens"], 900_000)


FAKE_CLAUDE = """#!{python}
import json, sys
sys.stdin.read()
print(json.dumps({{"type": "assistant", "message": {{"content": []}}}}), flush=True)
print(json.dumps({{"type": "result", "subtype": "success", "result": "report", "duration_ms": 10,
                  "usage": {{"input_tokens": 66, "cache_read_input_tokens": 1000000,
                            "cache_creation_input_tokens": 120000, "output_tokens": 9}}}}),
      flush=True)
"""


class HeadlessStreamTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo, self.bin = self.temp / "repo", self.temp / "bin"
        for directory in (self.repo, self.bin):
            directory.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
        fake.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "HERDR_", "CLAUDE_CODE_EFFORT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}")
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "Harness Test"),
                     ("config", "user.email", "harness@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.check_call(["git", *args], cwd=self.repo, env=self.env)
        (self.repo / "README").write_text("x\n")
        subprocess.check_call(["git", "add", "."], cwd=self.repo, env=self.env)
        subprocess.check_call(["git", "commit", "-qm", "baseline"], cwd=self.repo, env=self.env)
        subprocess.run([sys.executable, str(TASK_STATE), "init", "t1"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        self.brief = self.temp / "brief.md"
        self.brief.write_text("Do the unit.\n")

    def lanes(self, *args):
        result = subprocess.run([sys.executable, str(LANES), *args], cwd=self.repo, env=self.env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_the_dispatch_end_counts_cached_input_from_a_claude_stream(self):
        out = self.lanes("start", "--task", "t1", "--work", "U9", "--role", "reviewer",
                         "--runtime", "claude", "--model", "model-x", "--effort", "high",
                         "--brief", str(self.brief), "--report", str(self.temp / "r.md"),
                         "--checkout", str(self.repo), "--minutes", "5",
                         "--orchestrator", "claude", "--launcher", "headless")
        self.lanes("wait", "--task", "t1", "--dispatch", out["dispatch"], "--timeout", "30")
        events = [json.loads(line) for line in
                  (self.repo / ".harness/tasks/t1/events.jsonl").read_text().splitlines()]
        [end] = [e for e in events if e["type"] == "dispatch.end"
                 and e["dispatch"] == out["dispatch"]]
        self.assertEqual(end["usage"]["input_tokens"], 66 + 1_000_000 + 120_000)
        self.assertEqual(end["usage"]["cached_input_tokens"], 1_000_000)
        self.assertEqual(end["usage"]["output_tokens"], 9)


if __name__ == "__main__":
    unittest.main()
