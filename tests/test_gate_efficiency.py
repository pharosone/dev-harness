"""gate.py's efficiency side: the host load guard before full-suite steps and the per-step
wall-time history with its advisory slowdown note.

The guard is replaced by a stub that records calls and can simulate a loaded machine, so the
cases measure gate.py's integration (when it asks, what it records), not the guard's sensors;
load_guard.py has its own tests."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / "scripts"))
import gate  # noqa: E402

try:
    import load_guard as REAL_GUARD  # noqa: E402
except ImportError:
    REAL_GUARD = None

PY = sys.executable


def hermetic_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


class FakeGuard:
    """load_guard's interface: wait_for_capacity(...) -> the environment for heavy work."""

    def __init__(self, wait=0.0):
        self.wait = wait
        self.calls = []

    def wait_for_capacity(self, root, *, heavy=True, label="", run=None, env=None):
        self.calls.append({"root": Path(root), "heavy": heavy, "label": label, "run": run,
                           "at": time.monotonic()})
        if heavy and self.wait:
            time.sleep(self.wait)
        environment = dict(os.environ if env is None else env)
        environment["FAKE_GUARD_ANCESTOR"] = "gate"
        return environment


def step(name, source="pass", **extra):
    return {"name": name, "cwd": ".", "cmd": [PY, "-c", source], **extra}


def touch(path):
    return f"import pathlib, time; pathlib.Path({str(path)!r}).write_text(str(time.monotonic()))"


class GateCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.root.mkdir()
        self.env = mock.patch.dict(os.environ, hermetic_env(), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        for args in (("init", "-q", "-b", "main"), ("config", "user.name", "T"),
                     ("config", "user.email", "t@example.invalid"),
                     ("config", "commit.gpgsign", "false")):
            subprocess.run(["git", *args], cwd=self.root, check=True)
        (self.root / ".gitignore").write_text(".harness/\nmarks/\n")
        (self.root / "app").mkdir()
        (self.root / "app" / "x.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=self.root, check=True)
        subprocess.run(["git", "commit", "-qm", "base"], cwd=self.root, check=True)
        self.marks = self.root / "marks"
        self.marks.mkdir()
        self.config = self.root.parent / "gates.json"
        self.guard = FakeGuard()
        patch = mock.patch.object(gate, "load_guard", self.guard)
        patch.start()
        self.addCleanup(patch.stop)

    def configure(self, steps, area="app"):
        self.config.write_text(json.dumps({area: {"paths": ["app/"], "steps": steps}}))

    def gate(self, *args):
        """main() in-process, so the stub guard is the one gate.py calls."""
        out = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")  # main() reconfigures stdout
        err = io.StringIO()
        cwd = os.getcwd()
        os.chdir(self.root)
        try:
            with mock.patch.object(sys, "argv", ["gate.py", "--config", str(self.config), *args]), \
                    contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = gate.main()
        finally:
            os.chdir(cwd)
        out.flush()
        return types.SimpleNamespace(returncode=code,
                                     stdout=out.buffer.getvalue().decode("utf-8"),
                                     stderr=err.getvalue())

    def history(self):
        return json.loads((self.root / ".harness" / "gate" / "timings.json").read_text())

    def run_records(self, run_dir):
        return [json.loads(line) for line in
                (Path(run_dir) / "gate-timings.jsonl").read_text().splitlines()]


class ScopeTest(unittest.TestCase):
    FULL = [
        ["go", "test", "./...", "-count=1"], ["go", "test", "-race", "./..."], ["go", "test"],
        ["go", "test", "./...", "-run", ""], ["go", "test", "./...", "-run=.*"],
        ["pytest", "-k", ""], ["jest", "-t", ".*"],
        ["python3", "-m", "unittest", "discover", "-k", "*"],
        ["python3", "-m", "unittest", "discover", "-k", ""],
        ["pytest"], ["pytest", "tests/"], ["python3", "-m", "pytest", "-q"],
        ["uv", "run", "pytest", "-x"], ["python3", "-m", "unittest"],
        ["python3", "-m", "unittest", "discover", "-s", "tests"], ["npm", "test"],
        ["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        ["python3", "-m", "unittest", "discover", "--pattern=test*.py"],
        ["python3", "-m", "unittest", "discover", "-ptest_*.py"],
        ["pnpm", "run", "test"], ["npx", "vitest", "run"], ["jest", "--ci"],
        ["node", "--test", "static/*.test.mjs"], ["cargo", "test"], ["make", "test"],
        ["bundle", "exec", "rspec"], ["dotnet", "test"], ["./gradlew", "test"], ["tox"],
        # a flag's value is not a test file, a package or a selection
        ["pytest", "--maxfail", "1"], ["pytest", "-p", "test_plugin.py"],
        ["pytest", "-c", "ci_test.py", "tests/"],
        ["python3", "-m", "unittest", "discover", "-s", "tests", "-v"],
        ["python3", "-m", "unittest", "-v"], ["go", "test", "-count", "1", "./..."],
        ["go", "test", "-tags", "integration", "./..."], ["npm", "test", "--", "--run"],
        ["vitest", "--run"], ["vitest", "run", "--reporter", "dot"],
        ["npx", "jest", "--config", "jest.config.test.js"],
        ["node", "--test", "--test-reporter", "spec", "tests/"],
        # the harness's own heavy commands, admitted by the gate so the child never waits
        ["python3", "scripts/skill_evals.py", "run", "--skill", "pharos-dev",
         "--base", "origin/main"],
        ["python3", "harness/core/scripts/xreview.py", "--base", "origin/main",
         "--writer", "claude", "--out", "r.md"],
        ["./scripts/skill_evals.py", "run", "--skill", "x", "--base", "y"],
    ]
    TARGETED = [
        ["go", "test", "./internal/billing"], ["go", "test", "./...", "-run", "TestRefund"],
        ["go", "test", "-run=TestX", "./..."], ["pytest", "tests/test_api.py"],
        ["pytest", "tests/test_api.py::test_refund"], ["pytest", "-k", "refund"],
        ["python3", "-m", "pytest", "--lf"], ["python3", "-m", "unittest", "tests.test_gate"],
        ["python3", "-m", "unittest", "tests/test_gate.py"],
        ["python3", "-m", "unittest", "-k", "refund"],
        ["python3", "-m", "unittest", "discover", "-p", "test_gate*.py"],
        ["python3", "-m", "unittest", "discover", "-ptest_gate.py"],
        ["python3", "-m", "unittest", "discover", "--pattern=test_gate.py"],
        ["python3", "tests/test_gate.py"], ["npm", "test", "--", "src/a.test.ts"],
        ["npx", "jest", "-t", "refund"], ["vitest", "run", "src/cart.spec.tsx"],
        ["yarn", "test", "--", "--findRelatedTests", "src/cart.ts"],
        ["node", "--test", "static/a.test.mjs"], ["cargo", "test", "refund"],
        ["rspec", "spec/models/user_spec.rb"], ["dotnet", "test", "--filter", "Refund"],
        ["./gradlew", "test", "--tests", "*Refund*"], ["jest", "--changedSince={base}"],
        ["python3", "scripts/xreview.py", "--base", "origin/main", "--writer", "claude",
         "--out", "r.md", "--paths", "scripts/"],
    ]
    CHECKS = [
        ["ruff", "check", "."], ["go", "vet", "./..."], ["gofmt", "-l", "."], ["tsc", "--noEmit"],
        ["npx", "eslint", "."], ["npm", "run", "lint"], ["git", "diff", "--check"],
        ["python3", "scripts/check_docs_drift.py"], ["python3", "-c", "pass"],
        ["make", "lint"], ["mypy", "src"], ["./scripts/opaque-wrapper.sh"],
        # {base} alone proves nothing about an opaque wrapper or a linter
        ["ruff", "check", "--diff", "{base}"], ["./scripts/run-tests.sh", "{base}"],
        ["python3", "scripts/skill_evals.py", "check", "--base", "origin/main"],
        ["python3", "scripts/skill_evals.py", "report"],
        ["python3", "scripts/xreview.py", "--base", "x", "--writer", "claude", "--out", "r",
         "--dry-run"],
    ]

    def test_full_suites_targeted_runs_and_checks_are_told_apart(self):
        for scope, commands in (("full", self.FULL), ("targeted", self.TARGETED),
                                ("check", self.CHECKS)):
            for cmd in commands:
                with self.subTest(cmd=cmd):
                    self.assertEqual(gate.classify(cmd), scope)

    def test_a_declared_scope_wins_both_ways(self):
        self.assertEqual(gate.step_scope(step("t", scope="full")), "full")  # opaque wrapper
        self.assertEqual(gate.step_scope({"name": "s", "cwd": ".", "scope": "targeted",
                                          "cmd": ["go", "test", "./..."]}), "targeted")
        self.assertEqual(gate.step_scope({"name": "s", "cwd": ".", "scope": "full",
                                          "cmd": ["tool", "--since={base}"]}), "full")
        self.assertEqual(gate.step_scope({"name": "s", "cwd": ".", "scope": "targeted",
                                          "cmd": ["./opaque-suite.sh"]}), "targeted")

    def test_scope_is_validated_and_old_configs_still_parse(self):
        areas, _ = gate.parse({"app": {"paths": ["app/"], "steps": [step("t")]}}, "x")
        self.assertNotIn("scope", areas["app"]["steps"][0])
        gate.parse({"app": {"paths": ["app/"], "steps": [step("t", scope="full")]}}, "x")
        for bad in ("all", "check", ""):
            with self.assertRaisesRegex(gate.ConfigError, "scope"):
                gate.parse({"app": {"paths": ["app/"], "steps": [step("t", scope=bad)]}}, "x")


def suite(name, source="pass", **extra):
    """A step that stands for a whole test suite."""
    return step(name, source, scope="full", **extra)


class GuardIntegrationTest(GateCase):
    def test_explicit_default_discovery_pattern_still_admits_a_full_suite(self):
        tests = self.root / "tests"
        tests.mkdir()
        (tests / "test_one.py").write_text("import unittest\nclass T(unittest.TestCase):\n"
                                         "    def test_one(self): self.assertTrue(True)\n")
        item = {"name": "full", "cwd": ".", "cmd": [PY, "-m", "unittest", "discover",
                "-s", "tests", "-p", "test_*.py"]}
        self.configure([item])
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.guard.calls), 1)
        self.assertTrue(self.guard.calls[0]["heavy"])

    def test_idle_machine_starts_at_once_and_asks_the_guard_once(self):
        self.configure([suite("one"), suite("two")])
        started = time.monotonic()
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(len(self.guard.calls), 1)
        call = self.guard.calls[0]
        self.assertEqual((call["root"], call["heavy"], call["label"]), (self.root, True, "gate"))
        self.assertNotIn("waited", result.stdout)

    def test_loaded_machine_waits_then_runs_and_the_wait_is_not_step_time(self):
        self.guard.wait = 0.6
        run_dir = self.root.parent / "run"
        self.configure([suite("one")])
        result = self.gate("--run-dir", str(run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        record, = self.run_records(run_dir)
        self.assertGreaterEqual(record["guard_wait"], 0.6)
        self.assertLess(record["seconds"], 0.6)
        self.assertIn("waited 0.6s for the load guard", result.stdout)
        sample, = self.history()["steps"][record["fingerprint"]]["samples"]
        self.assertLess(sample["seconds"], 0.6)

    def test_targeted_runs_and_checks_never_ask_the_guard(self):
        self.configure([step("lint"), step("declared", scope="targeted"),
                        {"name": "diff", "cwd": ".", "cmd": [PY, "-c", "pass", "{base}"]},
                        {"name": "file", "cwd": ".",
                         "cmd": [PY, "-m", "unittest", "tests/test_none.py"]}])
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_none.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_nothing(self):\n        pass\n")
        result = self.gate("--base", "HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.guard.calls, [])

    def test_changed_selection_waits_only_for_full_suites(self):
        (self.root / "app" / "x.py").write_text("x = 2\n")
        (self.root / "tests").mkdir()
        (self.root / "tests" / "test_none.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_nothing(self):\n        pass\n")
        self.configure([step("lint"),
                        {"name": "file", "cwd": ".", "cmd": [PY, "tests/test_none.py"]},
                        {"name": "module", "cwd": ".",
                         "cmd": [PY, "-m", "unittest", "-k", "nothing", "tests/test_none.py"]}])
        result = self.gate("--changed", "HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"gate: 3 passed")
        self.assertEqual(self.guard.calls, [])
        self.configure([step("lint"), suite("suite")])
        self.assertEqual(self.gate("--changed", "HEAD").returncode, 0)
        self.assertEqual(len(self.guard.calls), 1)

    def test_targeted_flag_runs_an_opaque_full_step_without_waiting(self):
        run_dir = self.root.parent / "run"
        self.configure([suite("wrapper")])
        result = self.gate("--targeted", "--run-dir", str(run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.guard.calls, [])
        self.assertEqual(self.run_records(run_dir)[0]["scope"], "targeted")

    def test_the_guard_is_asked_just_before_the_first_full_step(self):
        first, second = self.marks / "lint", self.marks / "suite"
        self.configure([step("lint", touch(first)), suite("suite", touch(second)),
                        suite("more")])
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.guard.calls), 1)
        asked = self.guard.calls[0]["at"]
        self.assertLess(float(first.read_text()), asked)
        self.assertGreater(float(second.read_text()), asked)

    def test_steps_run_with_the_guard_environment(self):
        seen = self.marks / "env"
        self.configure([suite("suite", f"import os, pathlib; pathlib.Path({str(seen)!r})"
                                       ".write_text(os.environ.get('FAKE_GUARD_ANCESTOR', ''))")])
        self.assertEqual(self.gate().returncode, 0)
        self.assertEqual(seen.read_text(), "gate")
        self.assertNotIn("FAKE_GUARD_ANCESTOR", os.environ)

    def test_run_step_takes_an_environment_for_reuse(self):
        seen = self.marks / "env"
        item = step("one", f"import os, pathlib; pathlib.Path({str(seen)!r})"
                           ".write_text(os.environ.get('ONLY_FOR_THE_STEP', ''))")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(gate.run_step("app", item, self.root, "origin/main",
                                          env={**os.environ, "ONLY_FOR_THE_STEP": "yes"}))
        self.assertEqual(seen.read_text(), "yes")
        self.assertNotIn("ONLY_FOR_THE_STEP", os.environ)

    def test_a_broken_guard_fails_open(self):
        def broken(*args, **kwargs):
            raise RuntimeError("sensor exploded")
        self.configure([suite("one")])
        with mock.patch.object(self.guard, "wait_for_capacity", broken):
            result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("load guard unavailable (sensor exploded)", result.stderr)

    def test_dry_run_and_pin_ci_ask_no_guard_and_measure_nothing(self):
        self.configure([suite("one")])
        result = self.gate("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.guard.calls, [])
        self.assertFalse((self.root / ".harness" / "gate").exists())
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.gate("--pin-ci", "ci.yml", "--targeted")

    def test_run_step_keeps_its_caller_api(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(gate.run_step("app", suite("one"), self.root, "origin/main"))
            self.assertFalse(gate.run_step("app", suite("bad", "raise SystemExit(3)"),
                                           self.root, "origin/main"))
        self.assertEqual(self.guard.calls, [])


@unittest.skipUnless(REAL_GUARD is not None, "load_guard not installed")
class RealGuardTest(GateCase):
    """The shipped load_guard behind gate.py, with a fake load source."""

    def setUp(self):
        super().setUp()
        mock.patch.object(gate, "load_guard", REAL_GUARD).start()
        self.addCleanup(mock.patch.stopall)
        os.environ.update(HARNESS_LOAD_BACKOFF_SECONDS="0.1", HARNESS_LOAD_MAX_WAIT_SECONDS="5",
                          HARNESS_LOAD_PER_CORE="1.5",
                          HARNESS_LOAD_GUARD_STATE_DIR=str(self.root.parent / "registry"))
        os.environ.pop("HARNESS_LOAD_GUARD", None)
        os.environ.pop(REAL_GUARD.ANCESTOR, None)
        self.samples = []

    def load(self, *loads):
        """Each sample pops the next load; the last one repeats."""
        queue = list(loads)

        def sample(*args, **kwargs):
            self.samples.append(time.monotonic())
            value = queue.pop(0) if len(queue) > 1 else queue[0]
            return REAL_GUARD.Capacity(value, 4, True)
        mock.patch.object(REAL_GUARD, "sample_capacity", sample).start()

    def test_idle_machine_runs_at_once(self):
        self.load(1.0)
        self.configure([suite("one")])
        started = time.monotonic()
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(self.samples), 1)
        self.assertNotIn("machine busy", result.stderr)

    def test_loaded_machine_waits_then_proceeds_with_a_marked_environment(self):
        self.load(30.0, 30.0, 30.0, 1.0)
        seen = self.marks / "env"
        self.configure([suite("one", f"import os, pathlib; pathlib.Path({str(seen)!r})"
                                     ".write_text(os.environ.get('HARNESS_LOAD_GUARD_ANCESTOR',"
                                     " ''))")])
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("machine busy (load 30 / 4 cores), waiting up to 5 s", result.stderr)
        self.assertEqual(len(self.samples), 4)
        self.assertRegex(result.stdout, r"gate: waited 0\.\ds for the load guard")
        self.assertIn('"pid"', seen.read_text())

    def test_a_nested_harness_command_is_admitted_once_by_the_gate(self):
        """skill_evals.py run under the gate: the parent asks once (idle here); the child,
        on a machine its own settings call busy, still never waits, so no guard wait hides
        in the step's measured seconds."""
        self.load(0.0)  # the parent sees an idle machine
        child = self.root / "tools" / "skill_evals.py"
        child.parent.mkdir()
        child.write_text(
            "import sys, time\n"
            f"sys.path.insert(0, {str(CORE / 'scripts')!r})\n"
            "import load_guard\n"
            "started = time.monotonic()\n"
            "load_guard.wait_for_capacity('.', label='skill-evals')\n"
            "print(f'child waited {time.monotonic() - started:.1f}s')\n")
        os.environ["HARNESS_LOAD_PER_CORE"] = "0.0001"  # the real machine is always 'busy'
        os.environ["HARNESS_LOAD_MAX_WAIT_SECONDS"] = "3"
        run_dir = self.root.parent / "run"
        self.configure([{"name": "evals", "cwd": ".",
                         "cmd": [PY, "tools/skill_evals.py", "run", "--skill", "x"]}])
        self.assertEqual(gate.step_scope(json.loads(self.config.read_text())["app"]["steps"][0]),
                         "full")
        result = self.gate("--run-dir", str(run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(self.samples), 1)  # asked once, at the parent
        record, = self.run_records(run_dir)
        self.assertLess(record["seconds"], 2.5)
        log = next((self.root / ".harness" / "logs").glob("*gate-app-evals.log")).read_text()
        self.assertIn("child waited 0.0s", log)
        self.assertNotIn("machine busy", log)
        self.assertNotIn(REAL_GUARD.ANCESTOR, os.environ)  # admitted env scoped to the step

    def test_invalid_guard_settings_are_a_configuration_error(self):
        self.load(1.0)
        ran = self.marks / "ran"
        self.configure([step("lint", touch(ran)), suite("one")])
        os.environ["HARNESS_LOAD_PER_CORE"] = "lots"
        result = self.gate()
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("gate: load guard: HARNESS_LOAD_PER_CORE", result.stderr)
        self.assertFalse(ran.exists())  # checked before any step runs

    def test_the_kill_switch_wins_over_invalid_settings(self):
        self.load(30.0)
        self.configure([suite("one")])
        os.environ.update(HARNESS_LOAD_PER_CORE="lots", HARNESS_LOAD_GUARD="0")
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.samples, [])

    def test_invalid_settings_do_not_matter_to_a_run_without_full_suites(self):
        self.configure([step("lint")])
        os.environ["HARNESS_LOAD_PER_CORE"] = "lots"
        self.assertEqual(self.gate().returncode, 0)
        self.assertEqual(self.gate("--targeted").returncode, 0)

    def test_targeted_run_does_not_sample_the_machine(self):
        self.load(30.0)
        self.configure([step("lint"), step("one", scope="targeted")])
        self.assertEqual(self.gate().returncode, 0)
        self.assertEqual(self.samples, [])


class TimingTest(GateCase):
    def seed(self, fingerprint, seconds, *, age_days=0, label="app/one"):
        at = time.time() - age_days * 86400
        path = self.root / ".harness" / "gate" / "timings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads(path.read_text()) if path.exists() else {"version": 1, "steps": {}}
        entry = data["steps"].setdefault(fingerprint, {"label": label, "samples": []})
        entry["samples"] += [{"seconds": s, "at": at, "run": "seed"} for s in seconds]
        path.write_text(json.dumps(data))

    def fingerprint(self, item, area="app", base="origin/main"):
        return gate.fingerprint(area, item, gate.resolve(item, self.root, base))

    def test_a_step_slower_than_one_and_a_half_medians_gets_an_advisory_note(self):
        slow = step("one", "import time; time.sleep(1.3)")
        self.configure([slow])
        self.seed(self.fingerprint(slow), [0.1, 0.1, 0.2])
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertRegex(result.stdout, r"PASS app/one 1\.\ds")
        self.assertRegex(result.stdout,
                         r"NOTE app/one took 1\.\ds, \d+\.\dx its median 0\.1s over 3 runs")
        self.assertIn("advisory", result.stdout)
        self.assertRegex(result.stdout, r"gate: 1 passed, 0 failed, 0 skipped")

    def test_verdict_threshold_needs_history_and_a_real_gap(self):
        self.assertIsNone(gate.slowdown([1.0, 1.0], 9.0))           # too few samples
        self.assertIsNone(gate.slowdown([2.0, 2.0, 2.0], 3.0))      # exactly 1.5x
        self.assertIsNotNone(gate.slowdown([2.0, 2.0, 2.0], 3.2))
        self.assertIsNone(gate.slowdown([0.0, 0.0, 0.0], 0.4))      # zero median, tiny step
        self.assertIsNotNone(gate.slowdown([0.1, 0.1, 0.1], 0.5))   # fast steps also regress
        self.assertIsNone(gate.slowdown([0.1, 0.1, 0.1], 0.15))    # exactly 1.5x
        self.assertIsNotNone(gate.slowdown([0.1, 0.1, 0.1], 0.151))
        self.assertEqual(gate.slowdown([1.0, 5.0, 2.0, 3.0], 9.0)["median"], 2.5)

    def test_a_changed_command_cwd_or_base_starts_a_fresh_history(self):
        original = step("one")
        same = self.fingerprint(original)
        self.assertEqual(same, self.fingerprint(dict(original)))
        self.assertNotEqual(same, self.fingerprint(step("one", "pass  # changed")))
        self.assertNotEqual(same, self.fingerprint({**original, "cwd": "app"}))
        self.assertNotEqual(same, self.fingerprint(original, area="ui"))
        based = step("one", "pass", ) | {"cmd": [PY, "-c", "pass", "{base}"]}
        self.assertNotEqual(self.fingerprint(based, base="main"),
                            self.fingerprint(based, base="origin/main"))
        slow = step("one", "import time; time.sleep(1.3)")
        self.configure([slow])
        self.seed(same, [0.1, 0.1, 0.1])
        result = self.gate()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("NOTE", result.stdout)

    def test_failed_steps_are_recorded_but_never_join_the_baseline(self):
        run_dir = self.root.parent / "run"
        bad = step("one", "raise SystemExit(4)")
        self.configure([bad])
        result = self.gate("--run-dir", str(run_dir))
        self.assertEqual(result.returncode, 1)
        record, = self.run_records(run_dir)
        self.assertEqual((record["status"], record["exit"]), ("fail", 4))
        steps = self.history()["steps"] if (self.root / ".harness/gate/timings.json").exists() \
            else {}
        self.assertEqual(steps.get(record["fingerprint"], {}).get("samples", []), [])

    def test_unexpected_output_is_a_failure_for_the_baseline_too(self):
        noisy = step("fmt", "print('x.go')", expect_empty_stdout=True)
        self.configure([noisy])
        self.assertEqual(self.gate().returncode, 1)
        path = self.root / ".harness/gate/timings.json"
        samples = json.loads(path.read_text())["steps"].get(self.fingerprint(noisy), {}) \
            .get("samples", []) if path.exists() else []
        self.assertEqual(samples, [])

    def test_history_is_bounded_and_stale_samples_expire(self):
        fresh = step("one")
        fp = self.fingerprint(fresh)
        self.seed(fp, [1.0] * (gate.HISTORY_SAMPLES + 5))
        self.seed("stale-only", [1.0, 1.0], age_days=gate.HISTORY_DAYS + 1, label="app/old")
        for i in range(gate.HISTORY_STEPS + 3):
            self.seed(f"other-{i:03d}", [1.0], age_days=1, label=f"app/o{i}")
        self.configure([fresh])
        self.assertEqual(self.gate().returncode, 0)
        steps = self.history()["steps"]
        self.assertEqual(len(steps[fp]["samples"]), gate.HISTORY_SAMPLES)
        self.assertNotIn("stale-only", steps)
        self.assertLessEqual(len(steps), gate.HISTORY_STEPS)
        self.assertIn(fp, steps)  # the step just measured is the newest, never evicted

    def test_a_zero_second_step_is_recorded_without_a_note(self):
        quick = step("one")
        self.configure([quick])
        self.seed(self.fingerprint(quick), [0.0, 0.0, 0.0])
        result = self.gate()
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("NOTE", result.stdout)
        self.assertEqual(len(self.history()["steps"][self.fingerprint(quick)]["samples"]), 4)

    def test_unreadable_history_never_fails_a_green_gate(self):
        path = self.root / ".harness" / "gate" / "timings.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json")
        self.configure([step("one")])
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("timing history unreadable", result.stderr)
        self.assertEqual(len(self.history()["steps"]), 1)

    def test_unwritable_history_never_fails_a_green_gate(self):
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "gate").write_text("a file where the directory goes")
        self.configure([step("one")])
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("timing not recorded", result.stderr)

    def test_unwritable_history_does_not_drop_writable_run_evidence(self):
        (self.root / ".harness").mkdir()
        (self.root / ".harness" / "gate").write_text("not a directory")
        run_dir = self.root.parent / "run"
        self.configure([step("one")])
        result = self.gate("--run-dir", str(run_dir))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("timing not recorded", result.stderr)
        record, = self.run_records(run_dir)
        self.assertEqual(record["status"], "pass")
        self.assertGreaterEqual(record["seconds"], 0)
        self.assertIn("guard_wait", record)
        self.assertIsNone(record["slower"])

    def test_run_records_carry_their_provenance(self):
        run_dir = self.root.parent / "run"
        self.configure([step("one")])
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.root,
                                       text=True).strip()
        self.assertEqual(self.gate("--run-dir", str(run_dir)).returncode, 0)
        record, = self.run_records(run_dir)
        self.assertEqual(record["label"], "app/one")
        self.assertEqual((record["area"], record["step"], record["cwd"]), ("app", "one", "."))
        self.assertEqual(record["argv"], [PY, "-c", "pass"])
        self.assertEqual(record["scope"], "check")
        self.assertEqual(record["commit"], head)
        self.assertEqual(record["checkout"], str(self.root))
        self.assertEqual(record["status"], "pass")
        self.assertTrue(record["run"])
        self.assertIsInstance(record["at"], str)
        sample = self.history()["steps"][record["fingerprint"]]["samples"][0]
        self.assertEqual(sample["run"], record["run"])
        self.assertEqual(sample["commit"], head)

    def test_concurrent_gates_lose_no_samples(self):
        writer = (
            "import sys, pathlib\n"
            f"sys.path.insert(0, {str(CORE / 'scripts')!r})\n"
            "import gate\n"
            "root = pathlib.Path(sys.argv[1]); worker = sys.argv[2]\n"
            "for i in range(5):\n"
            "    gate.remember(root, {'fingerprint': f'fp-{worker}', 'label': f'app/{worker}',\n"
            "                         'seconds': 1.0 + i, 'run': worker, 'commit': 'c',\n"
            "                         'status': 'pass'})\n")
        workers = [subprocess.Popen([PY, "-c", writer, str(self.root), str(n)])
                   for n in range(8)]
        self.assertEqual([w.wait(timeout=60) for w in workers], [0] * 8)
        steps = self.history()["steps"]
        self.assertEqual(sorted(steps), [f"fp-{n}" for n in range(8)])
        self.assertTrue(all(len(entry["samples"]) == 5 for entry in steps.values()))


if __name__ == "__main__":
    unittest.main()
