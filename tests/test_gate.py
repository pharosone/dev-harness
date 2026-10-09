"""quiet.py keeps a command's full output on disk and shows only what a reader needs;
gate.py runs the one copy of the gate commands (the overlay's gates.json) through it, and
--pin-ci checks that copy against the CI workflow.

The project cases run on the fixture project in tests/fixture (harness/project/gates.json and
.github/workflows/ci.yml)."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

CORE = Path(__file__).resolve().parents[1]
QUIET = CORE / "scripts" / "quiet.py"
GATE = CORE / "scripts" / "gate.py"
FIXTURE = Path(__file__).resolve().parent / "fixture"
OVERLAY = FIXTURE / "harness" / "project"
WORKFLOW = FIXTURE / ".github" / "workflows" / "ci.yml"
PY = sys.executable

sys.path.insert(0, str(CORE / "scripts"))
import gate  # noqa: E402


def hermetic_env():
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("GIT_") and k != "HARNESS_PROJECT"}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return env


class Repo:
    """A throwaway Git checkout with one baseline commit, optionally holding the fixture
    overlay and workflow."""

    def __init__(self, test, overlay=False, workflow=False):
        temp = tempfile.TemporaryDirectory()
        test.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "repo"
        self.root.mkdir()
        self.env = hermetic_env()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Harness Test")
        self.git("config", "user.email", "harness@example.invalid")
        self.git("config", "commit.gpgsign", "false")
        self.write(".gitignore", ".harness/\n.marks/\n")
        for name in ("app/main.py", "ui/page.tsx", "static/a.test.mjs", "static/b.test.mjs",
                     "deploy/chart/values.yaml", "harness/project/rules/x.md", "docs/guide.md",
                     "AGENTS.md"):
            self.write(name, f"// {name}\n")
        if overlay:
            shutil.copytree(OVERLAY, self.root / "harness" / "project", dirs_exist_ok=True)
        if workflow:
            self.write(".github/workflows/ci.yml", WORKFLOW.read_text(encoding="utf-8"))
        self.git("add", ".")
        self.git("commit", "-qm", "baseline")
        self.marks = self.root / ".marks"
        self.marks.mkdir()

    def git(self, *args):
        return subprocess.check_output(["git", *args], cwd=self.root, env=self.env, text=True)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path


def run(script, *args, cwd, env=None):
    return subprocess.run([PY, str(script), *args], cwd=cwd, env=env or hermetic_env(),
                          capture_output=True, text=True, encoding="utf-8")


def program(source):
    """argv for a trivial Python step."""
    return [PY, "-c", source]


class QuietTest(unittest.TestCase):
    def setUp(self):
        self.repo = Repo(self)
        self.log = self.repo.root.parent / "out.log"

    def quiet(self, *cmd, log=True):
        extra = ["--log", str(self.log)] if log else []
        return run(QUIET, *extra, "--", *cmd, cwd=self.repo.root)

    def sections(self, stdout):
        """Split failure-mode output into its named blocks."""
        lines = stdout.splitlines()
        self.assertRegex(lines[0], r"^exit=\d+$")
        self.assertTrue(lines[-1].startswith("log: "), stdout)
        blocks, current = {}, None
        for line in lines[1:-1]:
            if line in ("-- failures --", "-- last 30 lines --"):
                current = line
                blocks[current] = []
            else:
                self.assertIsNotNone(current, f"line outside a block: {line!r}\n{stdout}")
                blocks[current].append(line)
        return blocks

    def test_success_prints_only_exit_the_last_ten_lines_and_the_log(self):
        result = self.quiet(*program("for i in range(50): print(f'line {i}')"))
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = ["exit=0", *[f"line {i}" for i in range(40, 50)], f"log: {self.log}"]
        self.assertEqual(result.stdout.splitlines(), expected)
        self.assertEqual(self.log.read_text().splitlines(), [f"line {i}" for i in range(50)])

    def test_success_with_short_output_prints_all_of_it(self):
        result = self.quiet(*program("print('one'); print('two')"))
        self.assertEqual(result.stdout.splitlines(), ["exit=0", "one", "two", f"log: {self.log}"])

    def test_exit_code_is_preserved(self):
        result = self.quiet(*program("import sys; print('bye'); sys.exit(3)"))
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.splitlines()[0], "exit=3")
        self.assertIn("bye", result.stdout)

    def test_missing_command_exits_127(self):
        result = self.quiet("definitely-not-a-command-4f9e")
        self.assertEqual(result.returncode, 127)
        self.assertEqual(result.stdout.splitlines()[0], "exit=127")
        self.assertIn("definitely-not-a-command-4f9e", self.log.read_text())

    def test_command_runs_without_a_shell(self):
        result = self.quiet(*program("import sys; print(sys.argv[1:])"), "$HOME", "a;b", "*")
        self.assertIn("['$HOME', 'a;b', '*']", result.stdout)

    def test_stdout_and_stderr_land_in_one_log_in_order(self):
        source = ("import sys\n"
                  "print('first', flush=True)\n"
                  "print('Error: second', file=sys.stderr, flush=True)\n"
                  "print('third', flush=True)\n"
                  "sys.exit(1)\n")
        result = self.quiet(*program(source))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.log.read_text().splitlines(), ["first", "Error: second", "third"])
        self.assertIn("Error: second", result.stdout)

    def test_failure_lines_come_with_five_following_lines_and_the_tail(self):
        lines = [f"noise {i}" for i in range(100)]
        lines[20] = "--- FAIL: TestBroken (0.01s)"
        for k in range(1, 7):
            lines[20 + k] = f"detail {k}"
        source = f"import sys\nprint({json.dumps(chr(10).join(lines))})\nsys.exit(1)\n"
        result = self.quiet(*program(source))
        self.assertEqual(result.returncode, 1)
        blocks = self.sections(result.stdout)
        failures = blocks["-- failures --"]
        self.assertEqual(failures, ["--- FAIL: TestBroken (0.01s)",
                                    *[f"detail {k}" for k in range(1, 6)]])
        self.assertEqual(blocks["-- last 30 lines --"], [f"noise {i}" for i in range(70, 100)])
        self.assertNotIn("detail 6", result.stdout)
        self.assertNotIn("noise 10\n", result.stdout)

    def test_every_failure_marker_is_extracted_and_look_alikes_are_not(self):
        markers = ["--- FAIL: TestA (0.00s)", "FAIL\tgithub.com/x/pkg\t0.2s", "panic: nil map",
                   "ERROR: test_x (module.Case)", "Error: boom", "error: linker failed",
                   "✗ static rewrite", "✖ node test failed", "   × web/a.test.ts > case",
                   "    --- FAIL: TestA/sub (0.00s)", " FAIL  src/a.test.ts", "Tests FAILED here"]
        look_alikes = ["Errors: 0 found", "the FAILSAFE path", "no error: none", "PASS ok"]
        lines = []
        for text in markers + look_alikes:
            lines += [text] + [f"pad {len(lines)}-{k}" for k in range(8)]
        lines += [f"tail {i}" for i in range(30)]
        source = f"import sys\nprint({json.dumps(chr(10).join(lines))})\nsys.exit(2)\n"
        failures = self.sections(self.quiet(*program(source)).stdout)["-- failures --"]
        for text in markers:
            self.assertIn(text, failures)
        for text in look_alikes:
            self.assertNotIn(text, failures)

    def test_failure_block_is_capped_at_one_hundred_lines(self):
        source = ("import sys\n"
                  "for i in range(500): print(f'FAIL case {i}')\n"
                  "for i in range(40): print(f'tail {i}')\n"
                  "sys.exit(1)\n")
        result = self.quiet(*program(source))
        failures = self.sections(result.stdout)["-- failures --"]
        shown = [line for line in failures if line.startswith("FAIL case")]
        self.assertEqual(len(shown), 100)
        self.assertEqual(shown[0], "FAIL case 0")
        self.assertLessEqual(len(failures), 101)
        self.assertIn("FAIL case 499", self.log.read_text())

    def test_failure_without_markers_shows_the_tail_only(self):
        source = "import sys\nfor i in range(60): print(f'plain {i}')\nsys.exit(4)\n"
        blocks = self.sections(self.quiet(*program(source)).stdout)
        self.assertNotIn("-- failures --", blocks)
        self.assertEqual(blocks["-- last 30 lines --"], [f"plain {i}" for i in range(30, 60)])

    def test_default_log_lives_under_the_checkout_harness_logs(self):
        sub = self.repo.root / "app"
        first = run(QUIET, "--", *program("print('hi')"), cwd=sub)
        second = run(QUIET, "--", *program("print('hi')"), cwd=sub)
        paths = []
        for result in (first, second):
            self.assertEqual(result.returncode, 0, result.stderr)
            path = Path(result.stdout.splitlines()[-1].removeprefix("log: "))
            self.assertEqual(path.parent, self.repo.root / ".harness" / "logs")
            self.assertRegex(path.name, r"^\d{8}T\d{6}Z-[a-z0-9][a-z0-9-]*\.log$")
            self.assertIn("python", path.name)
            self.assertEqual(path.read_text(), "hi\n")
            paths.append(path)
        self.assertNotEqual(paths[0], paths[1], "a second run must not overwrite the first log")

    def test_missing_separator_is_a_usage_error(self):
        result = run(QUIET, "--log", str(self.log), cwd=self.repo.root)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())


class GateTest(unittest.TestCase):
    """gate.py with an explicit --config: selection, run order, digests, flags."""

    def setUp(self):
        self.repo = Repo(self)
        self.config = self.repo.root.parent / "gates.json"

    def mark_step(self, area, name, cwd=".", fail=None, extra=""):
        """A step that leaves .marks/<area>-<name>, prints noise, and optionally fails."""
        source = (f"import pathlib, sys\n"
                  f"pathlib.Path({str(self.repo.marks / f'{area}-{name}')!r}).touch()\n"
                  f"print('noise from {area}/{name}')\n{extra}")
        if fail is not None:
            source += f"print('--- FAIL: Test{name}')\nsys.exit({fail})\n"
        return {"name": name, "cwd": cwd, "cmd": program(source)}

    def configure(self, areas):
        paths = {"app": ["app/"], "ui": ["ui/"], "static": ["static/"],
                 "harness": ["harness/", ".claude/", ".codex/", ".agents/", "AGENTS.md"]}
        config = {area: {"paths": paths[area], "steps": steps} for area, steps in areas.items()}
        self.config.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def standard(self):
        self.configure({area: [self.mark_step(area, "one"), self.mark_step(area, "two")]
                        for area in ("app", "ui", "static", "harness")})

    def gate(self, *args):
        return run(GATE, "--config", str(self.config), *args, cwd=self.repo.root)

    def marked(self):
        return sorted(p.name for p in self.repo.marks.iterdir())

    def summary(self, result):
        return [line for line in result.stdout.splitlines()
                if re.match(r"^(PASS|FAIL|SKIP) ", line)]

    def test_all_areas_run_in_order_with_one_line_per_step(self):
        self.standard()
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = self.summary(result)
        self.assertEqual([line.split()[:2] for line in lines],
                         [["PASS", f"{a}/{s}"] for a in ("app", "ui", "static", "harness")
                          for s in ("one", "two")])
        self.assertNotIn("noise from", result.stdout)
        self.assertEqual(len(self.marked()), 8)
        self.assertIn("gate: 8 passed, 0 failed, 0 skipped", result.stdout)

    def test_first_failure_stops_the_run_and_shows_its_digest(self):
        self.configure({"app": [self.mark_step("app", "vet"),
                                self.mark_step("app", "test", fail=3),
                                self.mark_step("app", "after")],
                        "ui": [self.mark_step("ui", "build")]})
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        lines = self.summary(result)
        self.assertEqual([line.split()[:2] for line in lines],
                         [["PASS", "app/vet"], ["FAIL", "app/test"],
                          ["SKIP", "app/after"], ["SKIP", "ui/build"]])
        self.assertIn("exit=3", lines[1])
        log = Path(lines[1].split("log: ", 1)[1])
        self.assertEqual(log.parent, self.repo.root / ".harness" / "logs")
        self.assertIn("--- FAIL: Testtest", log.read_text())
        self.assertIn("--- FAIL: Testtest", result.stdout)
        self.assertEqual(self.marked(), ["app-test", "app-vet"])
        self.assertIn("gate: 1 passed, 1 failed, 2 skipped", result.stdout)

    def test_keep_going_runs_every_step_and_still_fails(self):
        self.configure({"app": [self.mark_step("app", "test", fail=1),
                                self.mark_step("app", "after")],
                        "ui": [self.mark_step("ui", "build", fail=2)]})
        result = self.gate("--keep-going")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([line.split()[:2] for line in self.summary(result)],
                         [["FAIL", "app/test"], ["PASS", "app/after"], ["FAIL", "ui/build"]])
        self.assertEqual(self.marked(), ["app-after", "app-test", "ui-build"])

    def test_expect_empty_stdout_fails_a_step_that_prints(self):
        noisy = {"name": "fmt", "cwd": "app", "expect_empty_stdout": True,
                 "cmd": program("print('main.py')")}
        quiet = {"name": "fmt-clean", "cwd": "app", "expect_empty_stdout": True,
                 "cmd": program("pass")}
        self.configure({"app": [quiet, noisy]})
        result = self.gate("--keep-going")
        self.assertEqual(result.returncode, 1)
        lines = self.summary(result)
        self.assertEqual([line.split()[:2] for line in lines],
                         [["PASS", "app/fmt-clean"], ["FAIL", "app/fmt"]])
        self.assertIn("main.py", result.stdout)

    def test_named_areas_run_alone_and_unknown_areas_are_errors(self):
        self.standard()
        result = self.gate("ui")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marked(), ["ui-one", "ui-two"])
        unknown = self.gate("mobile")
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("mobile", unknown.stderr)

    def test_changed_picks_areas_by_path_prefix(self):
        self.standard()
        self.repo.write("ui/page.tsx", "// edited\n")
        self.repo.write("static/c.test.mjs", "// untracked\n")
        result = self.gate("--changed", "HEAD")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marked(), ["static-one", "static-two", "ui-one", "ui-two"])

    def test_changed_maps_agent_files_to_the_harness_area(self):
        self.standard()
        self.repo.write("AGENTS.md", "# edited\n")
        self.repo.write(".codex/agents/x.toml", "x = 1\n")
        self.gate("--changed", "HEAD")
        self.assertEqual(self.marked(), ["harness-one", "harness-two"])

    def test_changed_with_committed_changes_and_no_gated_paths(self):
        self.standard()
        base = self.repo.git("rev-parse", "HEAD").strip()
        self.repo.write("app/main.py", "// committed\n")
        self.repo.git("commit", "-qam", "app change")
        self.repo.write("deploy/chart/values.yaml", "x: 1\n")
        result = self.gate("--changed", base)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marked(), ["app-one", "app-two"])
        for mark in self.repo.marks.iterdir():
            mark.unlink()
        self.repo.git("checkout", "-q", "--", "deploy/chart/values.yaml")
        self.repo.write("docs/guide.md", "# docs only\n")
        nothing = self.gate("--changed", "HEAD")
        self.assertEqual(nothing.returncode, 0, nothing.stdout + nothing.stderr)
        self.assertEqual(self.marked(), [])
        self.assertIn("no gated paths changed", nothing.stdout)

    def test_changed_matches_glob_paths(self):
        step = lambda area: [self.mark_step(area, "one")]
        config = {"app": {"paths": ["app/", "**/AGENTS.md", "docs/*/x.md"],
                          "steps": step("app")},
                  "ui": {"paths": ["ui/"], "steps": step("ui")},
                  "harness": {"paths": ["**"], "steps": step("harness")}}
        self.config.write_text(json.dumps(config), encoding="utf-8")
        for name, expected in (("ui/AGENTS.md", ["app-one", "harness-one", "ui-one"]),
                               ("docs/agents/x.md", ["app-one", "harness-one"]),
                               ("docs/agents/deep/x.md", ["harness-one"]),
                               ("README.md", ["harness-one"])):
            with self.subTest(name=name):
                self.repo.write(name, "# changed\n")
                result = self.gate("--changed", "HEAD")
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(self.marked(), expected)
                self.assertNotIn("no gated paths changed", result.stdout)
                self.repo.git("reset", "-q", "--hard")
                self.repo.git("clean", "-qfd")
                for mark in self.repo.marks.iterdir():
                    mark.unlink()

    def test_step_runs_in_its_repo_relative_cwd(self):
        out = self.repo.root.parent / "cwd.txt"
        step = {"name": "where", "cwd": "app",
                "cmd": program(f"import os, pathlib; pathlib.Path({str(out)!r})"
                               ".write_text(os.getcwd())")}
        self.configure({"app": [step]})
        result = run(GATE, "--config", str(self.config), cwd=self.repo.root / "ui")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(Path(out.read_text()).resolve(), self.repo.root / "app")

    def test_base_placeholder_defaults_to_origin_main(self):
        out = self.repo.root.parent / "args.txt"
        step = {"name": "drift", "cwd": ".",
                "cmd": program(f"import sys, pathlib; pathlib.Path({str(out)!r})"
                               ".write_text(' '.join(sys.argv[1:]))") + ["--base", "{base}"]}
        self.configure({"harness": [step]})
        self.gate()
        self.assertEqual(out.read_text(), "--base origin/main")
        self.gate("--base", "abc123")
        self.assertEqual(out.read_text(), "--base abc123")

    def test_expand_globs_is_opt_in_and_relative_to_the_step_cwd(self):
        out = self.repo.root.parent / "argv.json"
        # The source itself holds `[1:]`: only arguments with `*` or `?` are patterns.
        dump = (f"import json, sys, pathlib; pathlib.Path({str(out)!r})"
                ".write_text(json.dumps(sys.argv[1:]))")
        expanded = {"name": "node", "cwd": ".", "expand_globs": True,
                    "cmd": program(dump) + ["--test", "static/*.test.mjs"]}
        literal = {"name": "unittest", "cwd": ".", "cmd": program(dump) + ["-p", "*.test.mjs"]}
        self.configure({"static": [expanded]})
        self.assertEqual(self.gate().returncode, 0)
        self.assertEqual(json.loads(out.read_text()),
                         ["--test", "static/a.test.mjs", "static/b.test.mjs"])
        self.configure({"static": [literal]})
        self.gate()
        self.assertEqual(json.loads(out.read_text()), ["-p", "*.test.mjs"])

    def test_glob_without_matches_fails_the_step(self):
        step = {"name": "node", "cwd": ".", "expand_globs": True,
                "cmd": program("pass") + ["static/none/*.test.mjs"]}
        self.configure({"static": [step]})
        result = self.gate()
        self.assertEqual(result.returncode, 1)
        self.assertIn("static/none/*.test.mjs", result.stdout)

    def test_invalid_config_is_an_error(self):
        for bad in ({"app": {"paths": ["app/"], "steps": [{"name": "x", "cwd": "../up",
                                                           "cmd": ["true"]}]}},
                    {"app": {"paths": ["app/"], "steps": [{"name": "x", "cwd": ".",
                                                           "cmd": "make test"}]}},
                    {"app": [{"name": "x", "cwd": ".", "cmd": ["true"]}]},
                    {"comment": "no areas"},
                    {}):
            self.config.write_text(json.dumps(bad), encoding="utf-8")
            result = self.gate()
            self.assertEqual(result.returncode, 2, f"{bad}\n{result.stdout}{result.stderr}")

    def test_dry_run_prints_resolved_commands_without_running(self):
        self.standard()
        result = self.gate("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.marked(), [])
        self.assertIn("app/one [.]", result.stdout)


class OverlayGatesTest(unittest.TestCase):
    """Without --config, gates.json is the overlay's; the core carries no gates of its own."""

    def area_lines(self, stdout):
        return re.findall(r"^([a-z0-9_-]+/\S+) \[([^\]]*)\] (.*)$", stdout, re.M)

    def test_the_core_ships_no_gates(self):
        self.assertFalse((CORE / "gates.json").exists(),
                         "a project's gate commands belong in its overlay, not in the core")

    def test_default_config_is_the_overlay_gates_json(self):
        repo = Repo(self, overlay=True)
        result = run(GATE, "--dry-run", cwd=repo.root / "ui")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        lines = self.area_lines(result.stdout)
        self.assertEqual([label for label, _, _ in lines],
                         ["app/fmt", "app/unit", "app/codegen", "app/lint", "ui/test",
                          "ui/build", "harness/unittest", "drift/docs"])
        self.assertIn(("app/unit", "app", "python3 -m unittest discover -s tests --failfast"),
                      lines)
        self.assertIn(("drift/docs", ".",
                       "python3 harness/core/scripts/check_docs_drift.py --base origin/main"),
                      lines)
        self.assertNotIn("ci_pin", result.stdout)
        self.assertNotIn("comment", result.stdout)

    def test_harness_project_names_another_overlay(self):
        repo = Repo(self)
        custom = repo.root / "config" / "harness" / "gates.json"
        custom.parent.mkdir(parents=True)
        custom.write_text(json.dumps({"only": {"paths": ["**"], "steps": [
            {"name": "one", "cwd": ".", "cmd": ["true"]}]}}), encoding="utf-8")
        env = {**hermetic_env(), "HARNESS_PROJECT": "config/harness"}
        result = run(GATE, "--dry-run", cwd=repo.root, env=env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.area_lines(result.stdout), [("only/one", ".", "true")])

    def test_explicit_config_wins_over_the_overlay(self):
        repo = Repo(self, overlay=True)
        config = repo.root.parent / "other.json"
        config.write_text(json.dumps({"x": {"paths": ["**"], "steps": [
            {"name": "y", "cwd": ".", "cmd": ["true"]}]}}), encoding="utf-8")
        result = run(GATE, "--config", str(config), "--dry-run", cwd=repo.root)
        self.assertEqual(self.area_lines(result.stdout), [("x/y", ".", "true")])

    def test_without_gates_json_nothing_passes(self):
        repo = Repo(self)
        for args in ((), ("--changed", "HEAD"), ("--dry-run",), ("app",)):
            with self.subTest(args=args):
                result = run(GATE, *args, cwd=repo.root)
                self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
                self.assertIn("no gates.json", result.stderr)
                self.assertIn(str(repo.root / "harness" / "project"), result.stderr)
                self.assertNotIn("passed", result.stdout)

    def test_unusable_overlay_gates_json_is_an_error(self):
        repo = Repo(self)
        path = repo.write("harness/project/gates.json", "[]")
        result = run(GATE, "--dry-run", cwd=repo.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("gates.json", result.stderr)
        path.write_text("{oops", encoding="utf-8")
        result = run(GATE, "--dry-run", cwd=repo.root)
        self.assertEqual(result.returncode, 2)
        self.assertIn("gates.json", result.stderr)


class FixtureAreaSelectionTest(unittest.TestCase):
    """--changed with the fixture project's gates.json picks every area that reads a path."""

    # path -> areas besides drift, which every change selects.
    CASES = {
        "app/items/service.py": {"app"},
        "docs/agents/config.md": {"app"},
        "AGENTS.md": {"app", "harness"},
        "ui/page.tsx": {"ui"},
        "app/static/site.css": {"app", "ui"},
        ".github/workflows/ci.yml": {"app", "ui", "harness"},
        "CLAUDE.md": {"harness"},
        "harness/project/rules/x.md": {"harness"},
        "harness/core/scripts/x.py": {"harness"},
        ".claude/agents/x.md": {"harness"},
        "docs/guide.md": set(),
        "deploy/chart/values.yaml": set(),
    }

    def setUp(self):
        self.repo = Repo(self, overlay=True)

    def selected(self):
        result = run(GATE, "--changed", "HEAD", "--dry-run", cwd=self.repo.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        areas = {m.group(1) for m in re.finditer(r"^([a-z0-9-]+)/\S+ \[", result.stdout, re.M)}
        return areas, result.stdout

    def test_each_path_selects_the_areas_that_read_it(self):
        for name, expected in self.CASES.items():
            with self.subTest(path=name):
                self.repo.write(name, "changed\n")
                areas, stdout = self.selected()
                self.assertEqual(areas, expected | {"drift"}, stdout)
                self.assertNotIn("no gated paths changed", stdout)
                self.repo.git("reset", "-q", "--hard")
                self.repo.git("clean", "-qfd")

    def test_no_gated_paths_only_without_changes(self):
        areas, stdout = self.selected()
        self.assertEqual(areas, set())
        self.assertIn("no gated paths changed", stdout)


class WorkflowReaderTest(unittest.TestCase):
    """The pin is only as strict as gate.py's workflow reader: it sees executed commands only."""

    WORKFLOW = """\
on: push
jobs:
  a:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - name: echo only
        run: echo make test -j1
      - name: block
        working-directory: app
        env:
          X: "1"
        run: |
          # make lint
          unformatted="$(fmt -l .)"
          make test \\
            -j1
      - run: cd cli && make vet
      - run: make build || true
      - run: make test | tee out.txt
      - continue-on-error: true
        run: npm test
      - if: false
        run: npm run lint
      - shell: bash {0}
        run: npm run build
      - run: |
          set +e
          node --test x.mjs
      - name: folded
        run: >-
          docker run --rm
          image:tag
      - run: "python3 -m unittest"  # trailing comment
      - uses: example/lint-action@v1
        with:
          working-directory: app
  b:
    if: github.event_name == 'push'
    runs-on: ubuntu-latest
    steps:
      - run: npm run build
"""

    def test_only_executed_commands_count(self):
        gating = [(job, cwd, argv) for job, cwd, argv, ok in gate.ci_commands(self.WORKFLOW)
                  if ok]
        self.assertEqual(gating, [
            ("a", ".", ["echo", "make", "test", "-j1"]),
            ("a", "app", ["fmt", "-l", "."]),
            ("a", "app", ["make", "test", "-j1"]),
            ("a", "cli", ["make", "vet"]),
            ("a", ".", ["tee", "out.txt"]),
            ("a", ".", ["docker", "run", "--rm", "image:tag"]),
            ("a", ".", ["python3", "-m", "unittest"]),
        ])
        ungated = {" ".join(argv) for _, _, argv, ok in gate.ci_commands(self.WORKFLOW) if not ok}
        self.assertEqual(ungated, {"make build", "true", "make test", "npm test", "npm run lint",
                                   "npm run build", "set +e", "node --test x.mjs"})

    def commands(self, run, job_extra="", top=""):
        workflow = (f"{top}jobs:\n  a:\n    runs-on: ubuntu-latest\n{job_extra}"
                    "    steps:\n      - run: |\n"
                    + "".join(f"          {line}\n" for line in run.splitlines()))
        return [(cwd, " ".join(argv), ok) for _, cwd, argv, ok in gate.ci_commands(workflow)]

    def test_every_command_of_a_line_counts(self):
        self.assertEqual(self.commands("npm test; ./extra-check"),
                         [(".", "npm test", True), (".", "./extra-check", True)])

    def test_a_command_before_and_gates_only_at_the_end_of_the_script(self):
        # bash -e does not stop on a failure before `&&`; only the script's last status counts
        self.assertEqual(self.commands("pytest && echo passed\ntrue"),
                         [(".", "pytest", False), (".", "echo passed", False), (".", "true", True)])
        self.assertEqual(self.commands("true\nmake a && make b"),
                         [(".", "true", True), (".", "make a", True), (".", "make b", True)])

    def test_a_command_that_may_be_skipped_does_not_gate(self):
        # after `||` it runs only when the left side failed; after `&&` it is skipped when the
        # left side failed, which stops the script only in its last statement
        self.assertEqual(self.commands("true || npm test\ntrue"),
                         [(".", "true", False), (".", "npm test", False), (".", "true", True)])
        self.assertEqual(self.commands("false && npm test\ntrue"),
                         [(".", "false", False), (".", "npm test", False), (".", "true", True)])
        # a missing directory makes `cd` fail and skips the command outside the last list
        self.assertEqual(self.commands("cd app && npm test\ntrue"),
                         [("app", "npm test", False), ("app", "true", True)])
        self.assertEqual(self.commands("true\ncd app && npm test"),
                         [(".", "true", True), ("app", "npm test", True)])

    def test_a_command_after_a_background_job_still_gates(self):
        self.assertEqual(self.commands("npm run dev & npm test\ntrue"),
                         [(".", "npm run dev", False), (".", "npm test", True), (".", "true", True)])

    def test_defaults_apply_wherever_they_are_declared(self):
        workflow = ("jobs:\n  a:\n    runs-on: ubuntu-latest\n    steps:\n"
                    "      - run: npm test\n"
                    "    defaults:\n      run:\n        working-directory: other\n"
                    "defaults:\n  run:\n    shell: bash\n")
        self.assertEqual([(cwd, " ".join(argv), ok) for _, cwd, argv, ok
                          in gate.ci_commands(workflow)], [("other", "npm test", True)])

    def test_defaults_set_the_working_directory_and_shell(self):
        top = "defaults:\n  run:\n    working-directory: app\n"
        self.assertEqual(self.commands("npm test", top=top), [("app", "npm test", True)])
        job = "    defaults:\n      run:\n        working-directory: ui\n"
        self.assertEqual(self.commands("npm test", job_extra=job, top=top),
                         [("ui", "npm test", True)])
        shell = "    defaults:\n      run:\n        shell: pwsh\n"
        self.assertEqual(self.commands("npm test", job_extra=shell), [(".", "npm test", False)])

    def test_echo_does_not_run_the_command_it_names(self):
        self.assertTrue(gate.starts_with(["echo", "make", "test"], ["echo"]))
        self.assertFalse(gate.starts_with(["echo", "make", "test"], ["make", "test"]))
        self.assertTrue(gate.starts_with(["x", "--base", "$B"], ["x", "--base", "{base}"]))

    def test_actions_and_their_inputs_are_read(self):
        steps = gate.workflow_steps(self.WORKFLOW)
        lint = [s for s in steps if s["keys"].get("uses", "").startswith("example/")]
        self.assertEqual(len(lint), 1)
        self.assertEqual(lint[0]["with"], {"working-directory": "app"})
        self.assertEqual(steps[0]["with"], {"fetch-depth": "0"})
        self.assertEqual([s["job"] for s in steps].count("b"), 1)


class CiPinTest(unittest.TestCase):
    """gate.py --pin-ci WORKFLOW: gates.json is the local copy of the CI gates, step for step.

    A project test calls it (or gate.pin_ci) on its own workflow; here it runs on the fixture
    project's workflow and gates."""

    def setUp(self):
        self.repo = Repo(self, overlay=True, workflow=True)
        self.workflow = self.repo.root / ".github" / "workflows" / "ci.yml"
        self.gates = self.repo.root / "harness" / "project" / "gates.json"

    def pin(self, *args, cwd=None):
        return run(GATE, "--pin-ci", ".github/workflows/ci.yml", *args, cwd=cwd or self.repo.root)

    def edit_workflow(self, old, new):
        text = self.workflow.read_text(encoding="utf-8")
        self.assertIn(old, text)
        self.workflow.write_text(text.replace(old, new, 1), encoding="utf-8")

    def edit_gates(self, change):
        data = json.loads(self.gates.read_text(encoding="utf-8"))
        change(data)
        self.gates.write_text(json.dumps(data), encoding="utf-8")

    def assertPinned(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("pin: ", result.stdout)

    def assertProblem(self, result, *needles):
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        problems = [line for line in result.stdout.splitlines() if line.startswith("pin: ")]
        self.assertTrue(problems, result.stdout)
        joined = "\n".join(problems)
        for needle in needles:
            self.assertIn(needle, joined)
        return problems

    def test_the_fixture_workflow_is_pinned(self):
        self.assertPinned(self.pin())
        self.assertPinned(self.pin(cwd=self.repo.root / "app"))  # the path is checkout-relative

    def test_project_tests_can_call_the_function(self):
        self.assertEqual(gate.pin_ci(self.gates, self.workflow), [])
        self.edit_workflow("      - run: npm run build\n        working-directory: ui\n", "")
        problems = gate.pin_ci(self.gates, self.workflow)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("ui/build", problems[0])

    def test_a_gate_step_ci_never_runs_is_a_problem(self):
        self.edit_workflow("      - run: npm run build\n        working-directory: ui\n", "")
        self.assertProblem(self.pin(), "ui/build", "npm run build", "ui")

    def test_a_gate_step_ci_runs_without_gating_is_a_problem(self):
        for old, new in (("run: npm run build", "run: npm run build || true"),
                         ("      - run: npm run build", "      - continue-on-error: true\n"
                                                        "        run: npm run build"),
                         ("      - run: npm run build", "      - if: false\n"
                                                        "        run: npm run build")):
            with self.subTest(new=new):
                self.repo.git("checkout", "-q", "--", ".github/workflows/ci.yml")
                self.edit_workflow(old, new)
                self.assertProblem(self.pin(), "ui/build")

    def test_a_gate_step_in_another_directory_is_a_problem(self):
        self.edit_workflow("      - run: npm test\n        working-directory: ui\n",
                           "      - run: cd ui/sub && npm test\n")
        self.assertProblem(self.pin(), "ui/test")

    def test_a_ci_command_no_gate_step_runs_is_a_problem(self):
        self.edit_workflow("        run: python3 -m unittest discover -s tests\n",
                           "        run: |\n          python3 -m unittest discover -s tests\n"
                           "          python3 tools/extra.py\n")
        self.assertProblem(self.pin(), "python3 tools/extra.py", "app")

    def test_declared_setup_is_bound_to_its_directory(self):
        self.edit_workflow("      - run: npm ci\n        working-directory: ui\n",
                           "      - run: npm ci\n        working-directory: ui\n"
                           "      - run: npm ci\n        working-directory: app\n")
        self.assertProblem(self.pin(), "npm ci", "app")

    def test_ci_form_stands_for_a_step_whose_ci_command_differs(self):
        def drop(data):
            del data["app"]["steps"][1]["ci_form"]
        self.edit_gates(drop)
        self.assertProblem(self.pin(), "app/unit", "--failfast")

    def test_every_command_of_a_ci_form_must_run(self):
        self.edit_workflow("          git diff --exit-code -- generated\n", "")
        self.assertProblem(self.pin(), "app/codegen", "git diff --exit-code -- generated")

    def test_an_action_form_needs_the_action_in_its_directory(self):
        self.edit_workflow("          working-directory: app\n", "          working-directory: ui\n")
        self.assertProblem(self.pin(), "app/lint", "example/lint-action@")

    def test_an_undeclared_action_is_a_problem(self):
        self.edit_workflow("      - uses: actions/setup-node@v4\n",
                           "      - uses: example/cache@v2\n      - uses: actions/setup-node@v4\n")
        self.assertProblem(self.pin(), "example/cache@v2")

    def test_jobs_are_gated_or_declared_ci_only(self):
        self.edit_workflow("  secrets:\n", "  docs:\n    runs-on: ubuntu-latest\n    steps:\n"
                                           "      - run: echo docs\n\n  secrets:\n")
        self.assertProblem(self.pin(), "docs")
        self.repo.git("checkout", "-q", "--", ".github/workflows/ci.yml")
        self.edit_workflow("  secrets:\n", "  scan:\n")
        self.assertProblem(self.pin(), "secrets", "scan")

    def test_pin_errors_are_usage_errors(self):
        self.workflow.unlink()
        result = self.pin()
        self.assertEqual(result.returncode, 2)
        self.assertIn("ci.yml", result.stderr)
        self.repo.git("checkout", "-q", "--", ".github/workflows/ci.yml")
        for args in (("app",), ("--changed", "HEAD")):
            with self.subTest(args=args):
                self.assertEqual(self.pin(*args).returncode, 2)

    def test_malformed_pin_keys_are_configuration_errors(self):
        for change in (lambda d: d["app"]["steps"][1].update(ci_form="python3 -m unittest"),
                       lambda d: d["app"]["steps"][1].update(ci_form=[{"cwd": "app"}]),
                       lambda d: d["app"]["steps"][1].update(
                           ci_form=[{"cwd": "app", "cmd": ["x"], "uses": "y@"}]),
                       lambda d: d["ci_pin"].update(setup=[{"cmd": "echo", "why": "x"}]),
                       lambda d: d["ci_pin"].update(setup=[{"cmd": ["echo"]}]),
                       lambda d: d["ci_pin"].update(setup_actions="actions/checkout@"),
                       lambda d: d["ci_pin"].update(ci_only_jobs=["secrets"]),
                       lambda d: d["ci_pin"].update(extra=1)):
            with self.subTest():
                self.repo.git("checkout", "-q", "--", "harness/project/gates.json")
                self.edit_gates(change)
                for args in (("--pin-ci", ".github/workflows/ci.yml"), ("--dry-run",)):
                    result = run(GATE, *args, cwd=self.repo.root)
                    self.assertEqual(result.returncode, 2, f"{args}\n{result.stdout}")


if __name__ == "__main__":
    unittest.main()
