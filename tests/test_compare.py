"""`compare.py design` and `compare.py status` against fake `claude` and `codex` on PATH.

The real CLIs are never called: the fakes come first on PATH, record their argv and prompt,
and either write a memo (a competitor brief) or a fenced JSON verdict (an arbiter brief, the
one with "## Memo X" sections). Candidates and arbiter passes start through lanes.py, so the
events log also carries their dispatch.start / dispatch.end.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
COMPARE = CORE / "scripts" / "compare.py"
TASK_STATE = CORE / "scripts" / "task_state.py"
MODELS = json.loads((CORE / "adapters" / "models.json").read_text(encoding="utf-8"))

FAKE_CLI = r"""#!{python}
import json, os, re, sys, time
name = os.path.basename(sys.argv[0])
prompt = sys.stdin.read()
memos = re.findall(r"^## Memo ([A-Z])\n(.*?)(?=^## Memo [A-Z]\n|^## End of memos\n)", prompt,
                   re.MULTILINE | re.DOTALL)
role = "arbiter" if memos else "candidate"
with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps({"name": name, "role": role, "argv": sys.argv[1:], "prompt": prompt}) + "\n")
time.sleep(float(os.environ.get("FAKE_SLEEP", "0")) if role == "candidate" else 0)
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
if role == "candidate":
    if os.environ.get("FAKE_FAIL_" + name) == "1":
        sys.exit(1)
    finish(os.environ.get("FAKE_MEMO_" + name, "A plain memo.\n"))
    sys.exit(0)
mode = os.environ.get("FAKE_ARBITER", "strong")
if mode == "fail":
    sys.exit(1)
letters = [letter for letter, _ in memos]
strong = [letter for letter, body in memos if "STRONG" in body]
if mode == "first":
    ranking = letters
else:
    ranking = strong + [letter for letter in letters if letter not in strong]
failed = {}
if mode == "strong_failed":
    failed = {letter: ["R1 is not covered"] for letter in strong}
verdict = {"ranking": ranking, "failed_mandatory": failed,
           "disagreements": ["the memos differ on retries"], "confidence": "high"}
finish("Reasoning of the arbiter, order " + ",".join(letters) + ".\n\n```json\n"
       + json.dumps(verdict) + "\n```\n")
"""

INTERVIEW = """# Feature interview

## Requirements interview

| # | Item | Question | Answer | Source |
|---|------|----------|--------|--------|
| 1 | Goal | Who uses it? | The operator of a run | user |
| 2 | Limits | Timeouts? | 45 min per producer | user |
"""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# The line lanes appends to a candidate-profile brief names that candidate's own scratch.
CANDIDATE_NOTE_PREFIX = "The source checkout is "


def without_report_line(prompt: str) -> str:
    return "\n".join(line for line in prompt.splitlines()
                     if "Write your full report to" not in line
                     and not line.startswith(CANDIDATE_NOTE_PREFIX))


class CompareTest(unittest.TestCase):
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
        self.input = self.temp / "interview.md"
        self.input.write_text(INTERVIEW, encoding="utf-8")

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.repo, env=self.env, text=True)

    def compare(self, *args, expect=0, **env):
        result = subprocess.run([sys.executable, str(COMPARE), *args], cwd=self.repo,
                                env={**self.env, **env}, capture_output=True, text=True,
                                timeout=120)
        if expect is not None:
            self.assertEqual(result.returncode, expect, result.stdout + result.stderr)
        return result

    def design(self, *extra, expect=0, **env):
        result = self.compare("design", "--task", "t1", "--input", str(self.input),
                              "--checkout", str(self.repo), "--timeout", "30",
                              "--arbiter-timeout", "30", *extra, expect=expect, **env)
        return json.loads(result.stdout) if expect == 0 else result

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

    def judgement(self, compare_id):
        return json.loads((self.run_dir / "compare" / compare_id / "judgement.json").read_text())

    @staticmethod
    def memo_order(prompt):
        return re.findall(r"^## Memo ([A-Z])\n(.*?)(?=^## Memo [A-Z]\n|^## End of memos\n)",
                          prompt, re.MULTILINE | re.DOTALL)

    # Input and configuration are checked before anything is reserved or launched.

    def assert_nothing_started(self):
        self.assertEqual(self.calls(), [])
        self.assertEqual([e for e in self.events() if e["type"].startswith(("compare.", "dispatch."))],
                         [])

    def test_input_without_requirements_table_is_refused(self):
        self.input.write_text("# Notes\n\nWe should build it.\n")
        result = self.design(expect=2)
        self.assertIn("requirements table", result.stderr)
        self.assert_nothing_started()

    def test_row_with_empty_answer_is_refused(self):
        self.input.write_text(INTERVIEW.replace("45 min per producer", "  "))
        result = self.design(expect=2)
        self.assertIn("empty answer", result.stderr)
        self.assertIn("2", result.stderr)
        self.assert_nothing_started()

    def test_arbiter_that_is_a_competitor_is_refused(self):
        result = self.design("--arbiter", "claude:implementation", expect=2)
        self.assertIn("arbiter", result.stderr)
        self.assert_nothing_started()

    def test_arbiter_with_a_competitors_model_and_effort_is_refused(self):
        same = MODELS["codex"]["implementation"]
        (self.project / "models.json").write_text(json.dumps({"codex": {"critical-review": same}}))
        result = self.design(expect=2)
        self.assertIn("arbiter", result.stderr)
        self.assert_nothing_started()

    def test_weaker_arbiter_is_refused(self):
        result = self.design("--arbiter", "codex:routine", expect=2)
        self.assertIn("weaker", result.stderr)
        self.assert_nothing_started()
        result = self.design("--mode", "deep", "--arbiter", "codex:critical-review", expect=2)
        self.assertIn("weaker", result.stderr)
        self.assert_nothing_started()

    # A full standard round.

    def test_standard_round_reserves_first_isolates_candidates_and_swaps_the_order(self):
        out = self.design(FAKE_MEMO_claude="Plan alpha: one queue.\n",
                          FAKE_MEMO_codex="Plan beta: two queues. STRONG\n")
        cid = out["compare"]
        events = self.events()
        types = [e["type"] for e in events]
        self.assertIn("compare.start", types)
        first_dispatch = min(i for i, t in enumerate(types) if t == "dispatch.start")
        self.assertLess(types.index("compare.start"), first_dispatch)
        self.assertEqual(types.count("compare.start"), 1)
        start = events[types.index("compare.start")]
        self.assertEqual(start["compare"], cid)
        self.assertEqual(start["kind"], "design")
        self.assertEqual(start["mode"], "standard")
        self.assertEqual(start["input_hash"], sha256(self.input))
        expected = [{"label": "claude", "runtime": "claude", "profile": "implementation",
                     **MODELS["claude"]["implementation"]},
                    {"label": "codex", "runtime": "codex", "profile": "implementation",
                     **MODELS["codex"]["implementation"]}]
        self.assertEqual(start["candidates"], expected)
        self.assertEqual(start["arbiter"], {"runtime": "codex", "profile": "critical-review",
                                            **MODELS["codex"]["critical-review"]})
        self.assertEqual(start["capability"], "equal")
        for event in events:
            if event["type"].startswith("compare."):
                self.assertEqual(event["compare"], cid, event)
                self.assertEqual(event["kind"], "design", event)

        candidates = self.calls("candidate")
        self.assertEqual(sorted(c["name"] for c in candidates), ["claude", "codex"])
        briefs = [without_report_line(c["prompt"]) for c in candidates]
        self.assertEqual(briefs[0], briefs[1])
        self.assertIn(sha256(self.input), briefs[0])
        self.assertIn("45 min per producer", briefs[0])
        for call in candidates:
            self.assertNotIn("Plan alpha", call["prompt"])
            self.assertNotIn("Plan beta", call["prompt"])

        folder = self.run_dir / "compare" / cid
        self.assertEqual((folder / "candidate-claude.md").read_text(), "Plan alpha: one queue.\n")
        self.assertEqual((folder / "candidate-codex.md").read_text(),
                         "Plan beta: two queues. STRONG\n")

        passes = self.calls("arbiter")
        self.assertEqual(len(passes), 2)
        self.assertTrue(all(c["name"] == "codex" for c in passes))
        orders = [self.memo_order(c["prompt"]) for c in passes]
        bodies = [[body.strip() for _, body in order] for order in orders]
        self.assertEqual(sorted(bodies[0]), ["Plan alpha: one queue.", "Plan beta: two queues. STRONG"])
        self.assertEqual(bodies[1], list(reversed(bodies[0])))
        for call in passes:
            visible = without_report_line(call["prompt"])
            self.assertNotIn("claude", visible)
            self.assertNotIn("codex", visible)

        verdict = self.judgement(cid)
        self.assertEqual(verdict["ranking"], ["codex", "claude"])
        self.assertFalse(verdict["tie"])
        self.assertTrue(verdict["stable"])
        self.assertEqual(verdict["input_hash"], sha256(self.input))
        self.assertEqual(verdict["capability"], "equal")
        self.assertEqual(verdict["excluded"], [])
        self.assertEqual(verdict["confidence"], "high")
        self.assertIn("the memos differ on retries", verdict["disagreements"])
        self.assertEqual(verdict["failed_mandatory"], {})

        comparison = (folder / "comparison.md").read_text()
        for pass_out in sorted(folder.glob("arbiter-*.md")):
            self.assertIn(pass_out.read_text().strip(), comparison)
        self.assertEqual(len(list(folder.glob("arbiter-*.md"))), 2)
        self.assertIn("**equal**", comparison)
        self.assertIn("|", comparison)

        results = [e for e in events if e["type"] == "compare.result"]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["stable"], True)
        self.assertEqual(results[0]["ranking"], ["codex", "claude"])
        self.assertEqual(results[0]["excluded"], [])
        self.assertEqual(results[0]["user_steered"], {"claude": False, "codex": False})

    def test_flipped_verdict_is_unstable(self):
        out = self.design(FAKE_ARBITER="first", FAKE_MEMO_claude="Plan alpha.\n",
                          FAKE_MEMO_codex="Plan beta.\n")
        verdict = self.judgement(out["compare"])
        self.assertFalse(verdict["stable"])
        self.assertEqual(verdict["confidence"], "low")
        comparison = (self.run_dir / "compare" / out["compare"] / "comparison.md").read_text()
        self.assertIn("arbiter unstable", comparison)
        result = [e for e in self.events() if e["type"] == "compare.result"][0]
        self.assertFalse(result["stable"])

    def test_failed_mandatory_requirement_cannot_be_outweighed(self):
        out = self.design(FAKE_ARBITER="strong_failed", FAKE_MEMO_claude="Plan alpha.\n",
                          FAKE_MEMO_codex="Plan beta. STRONG\n")
        verdict = self.judgement(out["compare"])
        self.assertEqual(verdict["ranking"], ["claude", "codex"])
        self.assertTrue(verdict["stable"])
        self.assertEqual(verdict["failed_mandatory"], {"codex": ["R1 is not covered"]})

    def test_missing_candidate_cannot_win(self):
        out = self.design(FAKE_FAIL_codex="1", FAKE_MEMO_claude="Plan alpha.\n")
        verdict = self.judgement(out["compare"])
        self.assertEqual(verdict["excluded"], ["codex"])
        self.assertIsNone(verdict["ranking"])  # one memo is not a winner (QA m5)
        self.assertEqual(verdict["unarbitrated"], ["claude"])
        self.assertEqual(verdict["confidence"], "low")
        self.assertEqual(self.calls("arbiter"), [])  # one memo: nothing to arbitrate
        result = [e for e in self.events() if e["type"] == "compare.result"][0]
        self.assertEqual(result["excluded"], ["codex"])
        self.assertNotIn("codex", result.get("ranking") or [])

    def test_empty_memo_cannot_win(self):
        out = self.design(FAKE_MEMO_claude="", FAKE_MEMO_codex="Plan beta.\n")
        verdict = self.judgement(out["compare"])
        self.assertEqual(verdict["excluded"], ["claude"])
        self.assertNotIn("claude", verdict["ranking"] or [])

    # Resume.

    def test_resume_reuses_sealed_candidates_and_launches_only_what_is_missing(self):
        out = self.design(FAKE_MEMO_claude="Plan alpha.\n", FAKE_MEMO_codex="Plan beta. STRONG\n")
        cid = out["compare"]
        before = self.judgement(cid)
        self.assertEqual(len(self.calls()), 4)
        self.design("--resume", cid)
        self.assertEqual(len(self.calls()), 4)
        self.assertEqual(self.judgement(cid), before)

        # Drop the arbiter phase as a crash before it would have left the log.
        path = self.run_dir / "events.jsonl"
        kept = []
        for line in path.read_text().splitlines():
            event = json.loads(line)
            arbiter_phase = event["type"] in ("compare.pass", "compare.result") or (
                event["type"] in ("compare.claim", "compare.launch")
                and str(event.get("slot", "")).startswith("arbiter"))
            if not arbiter_phase:
                kept.append(line)
        path.write_text("\n".join(kept) + "\n")
        folder = self.run_dir / "compare" / cid
        for name in ("judgement.json", "comparison.md", "arbiter-1.md", "arbiter-2.md"):
            (folder / name).unlink(missing_ok=True)

        self.design("--resume", cid, FAKE_MEMO_claude="CHANGED", FAKE_MEMO_codex="CHANGED")
        calls = self.calls()
        self.assertEqual(len(calls), 6)
        self.assertEqual([c["role"] for c in calls[4:]], ["arbiter", "arbiter"])
        for call in calls[4:]:
            self.assertIn("Plan alpha.", call["prompt"])
            self.assertNotIn("CHANGED", call["prompt"])
        self.assertEqual(self.judgement(cid)["ranking"], ["codex", "claude"])

    def test_resume_with_a_different_input_is_refused(self):
        out = self.design()
        self.input.write_text(INTERVIEW.replace("The operator of a run", "Someone else"))
        result = self.design("--resume", out["compare"], expect=2)
        self.assertIn("input", result.stderr)

    def test_resume_of_an_unknown_compare_is_refused(self):
        result = self.design("--resume", "cmp-unknown", expect=2)
        self.assertIn("cmp-unknown", result.stderr)

    def test_concurrent_resumes_launch_each_role_once(self):
        cid = "cmp-race01"
        start = {"compare": cid, "kind": "design", "mode": "standard",
                 "input_hash": sha256(self.input),
                 "candidates": [{"label": "claude", "runtime": "claude",
                                 "profile": "implementation", **MODELS["claude"]["implementation"]},
                                {"label": "codex", "runtime": "codex",
                                 "profile": "implementation", **MODELS["codex"]["implementation"]}],
                 "arbiter": {"runtime": "codex", "profile": "critical-review",
                             **MODELS["codex"]["critical-review"]},
                 "capability": "capability-unequal"}
        subprocess.run([sys.executable, str(TASK_STATE), "event", "t1", "compare.start",
                        "--data", json.dumps(start), "--id", f"{cid}.start"], cwd=self.repo,
                       env=self.env, check=True, capture_output=True)
        argv = [sys.executable, str(COMPARE), "design", "--task", "t1", "--input",
                str(self.input), "--checkout", str(self.repo), "--resume", cid,
                "--timeout", "30", "--arbiter-timeout", "30"]
        env = {**self.env, "FAKE_SLEEP": "1", "FAKE_MEMO_codex": "Plan beta. STRONG\n"}
        procs = [subprocess.Popen(argv, cwd=self.repo, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for _ in range(2)]
        outputs = [proc.communicate(timeout=120) for proc in procs]
        for proc, (stdout, stderr) in zip(procs, outputs):
            self.assertEqual(proc.returncode, 0, stdout + stderr)
        candidates = self.calls("candidate")
        self.assertEqual(sorted(c["name"] for c in candidates), ["claude", "codex"])
        self.assertEqual(len(self.calls("arbiter")), 2)
        events = self.events()
        self.assertEqual(sum(e["type"] == "compare.start" for e in events), 1)
        self.assertEqual(sum(e["type"] == "compare.result" for e in events), 1)
        self.assertEqual(sum(e["type"] == "dispatch.start" for e in events), 4)
        self.assertEqual(self.judgement(cid)["ranking"], ["codex", "claude"])

    # Deep mode and status.

    def test_deep_mode_uses_the_deep_profiles(self):
        out = self.design("--mode", "deep", FAKE_MEMO_claude="Plan alpha. STRONG\n")
        start = [e for e in self.events() if e["type"] == "compare.start"][0]
        self.assertEqual(start["mode"], "deep")
        self.assertEqual([c["profile"] for c in start["candidates"]],
                         ["deep-reasoning", "deep-reasoning"])
        self.assertEqual(start["arbiter"], {"runtime": "claude", "profile": "escalation",
                                            **MODELS["claude"]["escalation"]})
        passes = self.calls("arbiter")
        self.assertEqual([c["name"] for c in passes], ["claude", "claude"])
        self.assertEqual(self.judgement(out["compare"])["ranking"], ["claude", "codex"])

    def test_status_reports_the_compare(self):
        out = self.design(FAKE_MEMO_codex="Plan beta. STRONG\n")
        cid = out["compare"]
        one = json.loads(self.compare("status", "--task", "t1", "--compare", cid).stdout)
        self.assertEqual(one["compare"], cid)
        self.assertEqual(one["kind"], "design")
        self.assertEqual(one["phase"], "done")
        self.assertEqual(one["stable"], True)
        self.assertEqual(sorted(c["label"] for c in one["candidates"]), ["claude", "codex"])
        self.assertTrue(all(c["state"] == "sealed" for c in one["candidates"]))
        listed = json.loads(self.compare("status", "--task", "t1").stdout)
        self.assertEqual([c["compare"] for c in listed], [cid])
        self.compare("status", "--task", "t1", "--compare", "cmp-nope", expect=2)


if __name__ == "__main__":
    unittest.main()
