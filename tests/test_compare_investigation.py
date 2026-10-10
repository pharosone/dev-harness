"""`compare.py investigation` against fake `claude` and `codex` on PATH.

The real CLIs are never called. The fakes come first on PATH, record their argv and prompt, and
either write an investigation report (an investigator brief) or a critique ending in a fenced
JSON block (a critique brief, the one with a "## Report under review" section). Investigators and
critiques start through lanes.py, so the events log also carries their dispatch.start / end.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
COMPARE = CORE / "scripts" / "compare.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
MODELS = json.loads((CORE / "adapters" / "models.json").read_text(encoding="utf-8"))

FAKE_CLI = r"""#!{python}
import json, os, sys, time
name = os.path.basename(sys.argv[0])
prompt = sys.stdin.read()
role = "critic" if "## Report under review" in prompt else "investigator"
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"name": name, "role": role, "argv": sys.argv[1:], "prompt": prompt}) + "\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")) if role == "investigator" else 0)
def finish(text):
    if name == "claude":
        print(json.dumps({"type": "assistant", "message": {"content": []}}), flush=True)
        print(json.dumps({"type": "result", "result": text, "duration_ms": 10,
                          "usage": {"input_tokens": 1, "output_tokens": 1}}), flush=True)
    else:
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1,
                                                                "output_tokens": 1}}), flush=True)
        if text:
            out = sys.argv[sys.argv.index("--output-last-message") + 1]
            with open(out, "w", encoding="utf-8") as stream:
                stream.write(text)
if os.environ.get(f"FAKE_FAIL_{role}_{name}") == "1":
    sys.exit(1)
if role == "investigator":
    default = (f"# Investigation: the cache\n\n**Answer:** the key misses the locale ({name}) · "
               "**Confidence:** high — reproduced\n")
    finish(os.environ.get("FAKE_REPORT_" + name, default))
    sys.exit(0)
verdict = {"agreed": [f"agreed by {name}: the cache key is built once"],
           "disputed": [{"claim": f"disputed by {name}: the locale is the cause",
                         "evidence_for": f"the report's log line ({name} reviewed)",
                         "evidence_against": f"a rerun by {name} without the locale still misses"}],
           "settles": [{"claim": f"disputed by {name}: the locale is the cause",
                        "observation": f"run the cache test with a fixed locale ({name})"}],
           "answer_disputed": os.environ.get("FAKE_ANSWER_DISPUTED_" + name) == "1"}
finish(f"Critique by the reviewer {name}: the evidence is thin.\n\n```json\n"
       + json.dumps(verdict) + "\n```\n")
"""

BRIEF = """# Investigation brief

## Question

Why does the cache miss on the second request?

## Known

The cache test fails one time in ten.
"""

REPORT_CLAUDE = ("# Investigation: the cache\n\n**Answer:** ALPHA the key omits the locale · "
                 "**Confidence:** high — reproduced twice\n\n## Evidence\nALPHA log line\n")
REPORT_CODEX = ("# Investigation: the cache\n\n**Answer:** BETA the clock skews the TTL · "
                "**Confidence:** medium — one run\n\n## Evidence\nBETA timing trace\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# The line lanes appends to a candidate-profile brief names that candidate's own scratch.
CANDIDATE_NOTE_PREFIX = "The source checkout is "


def without_report_line(prompt: str) -> str:
    return "\n".join(line for line in prompt.splitlines()
                     if "Write your full report to" not in line
                     and not line.startswith(CANDIDATE_NOTE_PREFIX))


class CompareInvestigationTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp = Path(temp.name).resolve()
        self.repo = self.temp / "repo"
        self.bin = self.temp / "bin"
        self.project = self.temp / "project"
        for directory in (self.repo, self.bin, self.project):
            directory.mkdir()
        self.log = self.temp / "calls.jsonl"
        for name in ("claude", "codex"):
            fake = self.bin / name
            fake.write_text(FAKE_CLI.replace("{python}", sys.executable))
            fake.chmod(0o755)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "FAKE_", "HERDR_", "CLAUDE_CODE_EFFORT",
                                         "HARNESS_PROJECT"))}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                        PATH=f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}",
                        FAKE_LOG=str(self.log), HARNESS_PROJECT=str(self.project))
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
        self.input = self.temp / "brief.md"
        self.input.write_text(BRIEF, encoding="utf-8")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True)

    def compare(self, *args, expect=0, **env):
        result = subprocess.run([sys.executable, str(COMPARE), *args], cwd=self.repo,
                                env={**self.env, **env}, capture_output=True, text=True,
                                timeout=120)
        if expect is not None:
            self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def investigate(self, *extra, expect=0, **env):
        result = self.compare("investigation", "--task", "t1", "--input", str(self.input),
                              "--checkout", str(self.repo), "--timeout", "30",
                              "--critique-timeout", "30", *extra, expect=expect, **env)
        return json.loads(result.stdout) if expect == 0 else result

    def run_two_reports(self, *extra, **env):
        return self.investigate(*extra, FAKE_REPORT_claude=REPORT_CLAUDE,
                                FAKE_REPORT_codex=REPORT_CODEX, **env)

    def events(self):
        path = self.run_dir / "events.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]

    def calls(self, role=None):
        if not self.log.is_file():
            return []
        calls = [json.loads(line) for line in self.log.read_text().splitlines() if line.strip()]
        return [c for c in calls if role is None or c["role"] == role]

    def folder(self, compare_id):
        return self.run_dir / "compare" / compare_id

    def judgement(self, compare_id):
        return json.loads((self.folder(compare_id) / "judgement.json").read_text())

    def result_event(self):
        results = [e for e in self.events() if e["type"] == "compare.result"]
        self.assertEqual(len(results), 1)
        return results[0]

    def assert_nothing_started(self):
        self.assertEqual(self.calls(), [])
        self.assertEqual([e for e in self.events() if e["type"].startswith(("compare.", "dispatch."))],
                         [])

    # Input and configuration are checked before anything is reserved or launched.

    def test_empty_brief_is_refused(self):
        self.input.write_text("  \n\n")
        result = self.investigate(expect=2)
        self.assertIn("empty", result.stderr)
        self.assert_nothing_started()

    def test_brief_without_a_question_is_refused(self):
        self.input.write_text("# Notes\n\nThe cache is slow sometimes.\n")
        result = self.investigate(expect=2)
        self.assertIn("Question", result.stderr)
        self.assert_nothing_started()
        self.input.write_text("# Notes\n\n## Question\n\n## Known\n\nIt is slow.\n")
        result = self.investigate(expect=2)
        self.assertIn("Question", result.stderr)
        self.assert_nothing_started()

    def test_first_heading_may_state_the_question(self):
        self.input.write_text("# Why does the cache miss on the second request?\n\nIt is slow.\n")
        out = self.investigate()
        self.assertEqual(len(self.calls("investigator")), 2)
        for call in self.calls("critic"):
            self.assertIn("Why does the cache miss on the second request?", call["prompt"])
        self.assertEqual(self.judgement(out["compare"])["kind"], "investigation")

    def test_investigators_of_one_vendor_are_refused(self):
        result = self.investigate("--candidate", "claude:deep-reasoning",
                                  "--candidate", "claude:escalation", expect=2)
        self.assertIn("vendor", result.stderr)
        self.assert_nothing_started()

    # A full round.

    def test_round_isolates_investigators_and_crosses_the_critiques(self):
        out = self.run_two_reports()
        cid = out["compare"]
        events = self.events()
        types = [e["type"] for e in events]
        self.assertEqual(types.count("compare.start"), 1)
        first_dispatch = min(i for i, t in enumerate(types) if t == "dispatch.start")
        self.assertLess(types.index("compare.start"), first_dispatch)
        start = events[types.index("compare.start")]
        self.assertEqual(start["kind"], "investigation")
        self.assertEqual(start["input_hash"], sha256(self.input))
        self.assertEqual(start["candidates"], [
            {"label": "claude", "runtime": "claude", "profile": "deep-reasoning",
             **MODELS["claude"]["deep-reasoning"]},
            {"label": "codex", "runtime": "codex", "profile": "deep-reasoning",
             **MODELS["codex"]["deep-reasoning"]}])
        for event in events:
            if event["type"].startswith("compare."):
                self.assertEqual(event["compare"], cid, event)
                self.assertEqual(event["kind"], "investigation", event)

        # Investigators: identical briefs with the report format, never the other report.
        investigators = self.calls("investigator")
        self.assertEqual(sorted(c["name"] for c in investigators), ["claude", "codex"])
        briefs = [without_report_line(c["prompt"]) for c in investigators]
        self.assertEqual(briefs[0], briefs[1])
        self.assertIn(sha256(self.input), briefs[0])
        self.assertIn("Why does the cache miss on the second request?", briefs[0])
        self.assertIn("The cache test fails one time in ten.", briefs[0])
        self.assertIn("## Hypotheses considered", briefs[0])
        self.assertIn("**Confidence:**", briefs[0])
        for call in investigators:
            self.assertNotIn("ALPHA", call["prompt"])
            self.assertNotIn("BETA", call["prompt"])
        dispatches = [e for e in events if e["type"] == "dispatch.start"]
        roles = sorted(e["role"] for e in dispatches)
        self.assertEqual(roles, ["critic", "critic", "investigator", "investigator"])

        folder = self.folder(cid)
        self.assertEqual((folder / "candidate-claude.md").read_text(), REPORT_CLAUDE)
        self.assertEqual((folder / "candidate-codex.md").read_text(), REPORT_CODEX)

        # Critiques: the other vendor reviews each report, seeing only that one report.
        critics = {c["name"]: c for c in self.calls("critic")}
        self.assertEqual(sorted(critics), ["claude", "codex"])
        self.assertEqual(len(self.calls("critic")), 2)
        # Of the critic's own vendor's report it sees only the **Answer:** line, labelled as
        # the other investigation's answer, for comparison.
        other = "## The other investigation's answer, for comparison"
        self.assertIn("ALPHA log line", critics["codex"]["prompt"])
        self.assertNotIn("BETA timing trace", critics["codex"]["prompt"])
        self.assertEqual(critics["codex"]["prompt"].count("BETA"), 1)
        self.assertIn(f"{other}\n\n" + REPORT_CODEX.splitlines()[2] + "\n",
                      critics["codex"]["prompt"])
        self.assertIn("BETA timing trace", critics["claude"]["prompt"])
        self.assertNotIn("ALPHA log line", critics["claude"]["prompt"])
        self.assertEqual(critics["claude"]["prompt"].count("ALPHA"), 1)
        self.assertIn(f"{other}\n\n" + REPORT_CLAUDE.splitlines()[2] + "\n",
                      critics["claude"]["prompt"])
        for call in critics.values():
            self.assertIn("Why does the cache miss on the second request?", call["prompt"])
            self.assertIn("answer_disputed", call["prompt"])
        for event in dispatches:
            if event["role"] == "critic":
                self.assertEqual(event["launcher"], "headless")
                expected = MODELS[event["runtime"]]["critical-review"]
                self.assertEqual((event["model"], event["effort"]),
                                 (expected["model"], expected["effort"]))

        # The deterministic merge.
        verdict = self.judgement(cid)
        self.assertEqual(verdict["kind"], "investigation")
        self.assertEqual(verdict["input_hash"], sha256(self.input))
        self.assertEqual(verdict["capability"], "equal")
        self.assertEqual(verdict["excluded"], [])
        self.assertFalse(verdict["answer_disputed"])
        self.assertEqual(verdict["confidence"], "medium")  # the lower of high and medium
        agreed = json.dumps(verdict["agreed"])
        self.assertIn("agreed by claude: the cache key is built once", agreed)
        self.assertIn("agreed by codex: the cache key is built once", agreed)
        disputed = {d["claim"]: d for d in verdict["disputed"]}
        self.assertEqual(sorted(disputed), ["disputed by claude: the locale is the cause",
                                            "disputed by codex: the locale is the cause"])
        for name, item in (("claude", disputed["disputed by claude: the locale is the cause"]),
                           ("codex", disputed["disputed by codex: the locale is the cause"])):
            self.assertEqual(item["evidence_for"], f"the report's log line ({name} reviewed)")
            self.assertEqual(item["evidence_against"],
                             f"a rerun by {name} without the locale still misses")
        self.assertEqual(len(verdict["settles"]), 2)

        mapping = (folder / "disagreements.md").read_text()
        for name in ("claude", "codex"):
            self.assertIn(f"agreed by {name}: the cache key is built once", mapping)
            self.assertIn(f"disputed by {name}: the locale is the cause", mapping)
            self.assertIn(f"the report's log line ({name} reviewed)", mapping)
            self.assertIn(f"a rerun by {name} without the locale still misses", mapping)
            self.assertIn(f"run the cache test with a fixed locale ({name})", mapping)

        comparison = (folder / "comparison.md").read_text()
        critiques = sorted(folder.glob("critique-*.md"))
        self.assertEqual(len(critiques), 2)
        for path in critiques:
            self.assertIn(path.read_text().strip(), comparison)

        result = self.result_event()
        self.assertEqual(result["answer_disputed"], False)
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["excluded"], [])

    def test_a_disputed_answer_lowers_confidence_to_low(self):
        out = self.investigate(FAKE_ANSWER_DISPUTED_codex="1")
        verdict = self.judgement(out["compare"])
        self.assertTrue(verdict["answer_disputed"])
        self.assertEqual(verdict["confidence"], "low")
        result = self.result_event()
        self.assertTrue(result["answer_disputed"])
        self.assertEqual(result["confidence"], "low")
        self.assertIn("answer is disputed",
                      (self.folder(out["compare"]) / "disagreements.md").read_text())

    def test_a_missing_report_is_not_critiqued_and_lowers_confidence(self):
        out = self.investigate(FAKE_FAIL_investigator_codex="1", FAKE_REPORT_claude=REPORT_CLAUDE)
        cid = out["compare"]
        critics = self.calls("critic")
        self.assertEqual([c["name"] for c in critics], ["codex"])  # codex reviews claude's report
        self.assertIn("ALPHA log line", critics[0]["prompt"])
        verdict = self.judgement(cid)
        self.assertEqual(verdict["excluded"], ["codex"])
        self.assertEqual(verdict["confidence"], "low")
        self.assertFalse((self.folder(cid) / "candidate-codex.md").exists())
        result = self.result_event()
        self.assertEqual(result["excluded"], ["codex"])
        self.assertEqual(result["confidence"], "low")

    # Resume and concurrency.

    def test_resume_reuses_sealed_reports_and_finished_critiques(self):
        out = self.run_two_reports()
        cid = out["compare"]
        before = self.judgement(cid)
        self.assertEqual(len(self.calls()), 4)
        self.investigate("--resume", cid)
        self.assertEqual(len(self.calls()), 4)
        self.assertEqual(self.judgement(cid), before)

        # Drop the critique phase as a crash before it would have left the log.
        path = self.run_dir / "events.jsonl"
        kept = []
        for line in path.read_text().splitlines():
            event = json.loads(line)
            critique_phase = event["type"] in ("compare.pass", "compare.result") or (
                event["type"] in ("compare.claim", "compare.launch")
                and str(event.get("slot", "")).startswith("critique"))
            if not critique_phase:
                kept.append(line)
        path.write_text("\n".join(kept) + "\n")
        folder = self.folder(cid)
        for name in ("judgement.json", "comparison.md", "disagreements.md"):
            (folder / name).unlink(missing_ok=True)
        for stale in folder.glob("critique-*.md"):
            stale.unlink()

        self.investigate("--resume", cid, FAKE_REPORT_claude="CHANGED", FAKE_REPORT_codex="CHANGED")
        calls = self.calls()
        self.assertEqual(len(calls), 6)
        self.assertEqual([c["role"] for c in calls[4:]], ["critic", "critic"])
        for call in calls[4:]:
            self.assertNotIn("CHANGED", call["prompt"])
        self.assertEqual(self.judgement(cid)["confidence"], "medium")

    def test_resume_with_a_different_brief_or_kind_is_refused(self):
        out = self.investigate()
        self.input.write_text(BRIEF.replace("second request", "third request"))
        result = self.investigate("--resume", out["compare"], expect=2)
        self.assertIn("input", result.stderr)
        result = self.investigate("--resume", "cmp-unknown", expect=2)
        self.assertIn("cmp-unknown", result.stderr)

    def test_concurrent_resumes_launch_each_slot_once(self):
        cid = "cmp-race02"
        start = {"compare": cid, "kind": "investigation", "input_hash": sha256(self.input),
                 "candidates": [{"label": "claude", "runtime": "claude",
                                 "profile": "deep-reasoning", **MODELS["claude"]["deep-reasoning"]},
                                {"label": "codex", "runtime": "codex",
                                 "profile": "deep-reasoning", **MODELS["codex"]["deep-reasoning"]}],
                 "critics": {"claude": {"runtime": "codex", "profile": "critical-review",
                                        **MODELS["codex"]["critical-review"]},
                             "codex": {"runtime": "claude", "profile": "critical-review",
                                       **MODELS["claude"]["critical-review"]}},
                 "capability": "capability-unequal"}
        subprocess.run([sys.executable, str(TASK_STATE), "event", "t1", "compare.start",
                        "--data", json.dumps(start), "--id", f"{cid}.start"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        argv = [sys.executable, str(COMPARE), "investigation", "--task", "t1", "--input",
                str(self.input), "--checkout", str(self.repo), "--resume", cid,
                "--timeout", "30", "--critique-timeout", "30"]
        env = {**self.env, "FAKE_SLEEP": "1", "FAKE_REPORT_claude": REPORT_CLAUDE,
               "FAKE_REPORT_codex": REPORT_CODEX}
        procs = [subprocess.Popen(argv, cwd=self.repo, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [proc.communicate(timeout=120) for proc in procs]
        for proc, (stdout, stderr) in zip(procs, outputs):
            self.assertEqual(proc.returncode, 0, stdout + stderr)
        self.assertEqual(sorted(c["name"] for c in self.calls("investigator")), ["claude", "codex"])
        self.assertEqual(sorted(c["name"] for c in self.calls("critic")), ["claude", "codex"])
        events = self.events()
        self.assertEqual(sum(e["type"] == "compare.start" for e in events), 1)
        self.assertEqual(sum(e["type"] == "compare.result" for e in events), 1)
        self.assertEqual(sum(e["type"] == "dispatch.start" for e in events), 4)
        self.assertEqual(self.judgement(cid)["confidence"], "medium")

    def test_status_reports_an_investigation(self):
        out = self.investigate()
        cid = out["compare"]
        one = json.loads(self.compare("status", "--task", "t1", "--compare", cid).stdout)
        self.assertEqual(one["kind"], "investigation")
        self.assertEqual(one["phase"], "done")
        self.assertTrue(all(c["state"] == "sealed" for c in one["candidates"]))
        self.assertEqual(one["confidence"], "high")


if __name__ == "__main__":
    unittest.main()
