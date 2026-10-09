#!/usr/bin/env python3
"""Run the project's gates from their one copy, the overlay's gates.json.

Usage: gate.py [AREA ...] [--changed BASE] [--base REF] [--keep-going] [--dry-run]
               [--config FILE]
       gate.py --pin-ci WORKFLOW [--config FILE]

Overlay file `gates.json` (in $HARNESS_PROJECT, else <checkout>/harness/project; --config
names another file). Its top-level keys are areas, run in file order, except two reserved
keys:
  <area>    {"paths": [...], "steps": [...]}
  comment   free text, ignored
  ci_pin    read only by --pin-ci (below)
A path ending in `/` is a directory prefix, a path holding `*` or `?` a glob
(tripwire.py's: `*` within a directory, `**` across, so `**` is every path), any other
path an exact file. An area's paths are every input of its steps that CI reads, not only
its own directory. A step is
  name                  unique within the area, no `/`
  cwd                   relative to the checkout
  cmd                   argv, run without a shell; `{base}` in an argument becomes --base
                        (default: the --changed value, else origin/main)
  expect_empty_stdout   exit 0 with any output still fails (a formatter's list mode)
  expand_globs          arguments holding `*` or `?` are expanded relative to cwd; no
                        match fails the step
  ci_form               read only by --pin-ci: how CI runs this step when its command
                        differs on purpose, a list of {"cwd", "cmd"} (CI commands that
                        together stand for the step) or {"cwd", "uses"} (an action prefix
                        run with that working-directory input)
Steps check the working tree as it is and write no sources; dependency installs are the
checkout's setup, not steps. Without gates.json there is nothing to run, and gate.py says
so with exit 2 rather than reporting a pass.

Without AREA or --changed every area runs. --changed adds the areas whose paths
changed since BASE: committed, staged, unstaged and untracked. Steps run through
quiet.py with logs under <checkout>/.harness/logs/; the run stops at the first
failure unless --keep-going. One line per step: PASS, FAIL (its digest above it) or
SKIP. Exit codes: 0 all passed, 1 a step failed, 2 usage or configuration error.

--pin-ci WORKFLOW (a GitHub workflow, relative to the checkout) runs nothing: it checks
that gates.json is the local copy of the CI gates, step for step. Every gate step must run
in CI as a gating command (enabled step and job, not followed by `||`, `|` or `&`) in its
cwd, verbatim or with arguments appended, unless its ci_form says how CI runs it; every
command CI runs must be a gate step or declared setup; every job must run a gate step or
be declared CI-only. `ci_pin` declares the exceptions:
  setup           [{"cmd": [argv prefix], "cwd": dir (omitted: any), "why": text}]
  setup_actions   ["actions/checkout@", ...]: `uses:` prefixes that gate nothing
  ci_only_jobs    {"job": "why it has no local gate"}
One `pin: <problem>` line per problem; exit 0 pinned, 1 problems, 2 usage or
configuration error. Project tests can call pin_ci(gates_path, workflow_path) instead,
which returns the problems as a list. The reader understands the subset of workflow YAML
CI files use (block mappings, `- ` step lists, plain and quoted scalars, `|`/`>` blocks);
it checks that each command runs, not what its arguments are.
"""

import argparse
import glob
import json
from pathlib import Path
import posixpath
import re
import shlex
import sys
import time

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from project import OverlayError, load as load_overlay, overlay_dir
from quiet import default_log, digest, execute, slug
from task_state import StateError, git
from tripwire import glob_pattern

CONFIG = "gates.json"
RESERVED = {"comment", "ci_pin"}
STEP_KEYS = {"name", "cwd", "cmd", "expect_empty_stdout", "expand_globs", "ci_form"}
PIN_KEYS = {"setup", "setup_actions", "ci_only_jobs"}


class ConfigError(Exception):
    """gates.json is malformed or names an unknown area."""


class StepError(Exception):
    """A step cannot be resolved into a command."""


def relative_dir(value) -> bool:
    return isinstance(value, str) and bool(value) and not Path(value).is_absolute() \
        and ".." not in Path(value).parts


def argv(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(a, str) for a in value)


def check_ci_form(forms, where: str) -> None:
    if not isinstance(forms, list) or not forms:
        raise ConfigError(f"{where}: ci_form must be a non-empty list")
    for form in forms:
        ok = isinstance(form, dict) and relative_dir(form.get("cwd")) and (
            (set(form) == {"cwd", "cmd"} and argv(form["cmd"]))
            or (set(form) == {"cwd", "uses"} and isinstance(form["uses"], str)
                and bool(form["uses"])))
        if not ok:
            raise ConfigError(f"{where}: each ci_form entry is {{\"cwd\", \"cmd\": [argv]}} "
                              "or {\"cwd\", \"uses\": \"action prefix\"}")


def check_pin(pin, where: str) -> dict:
    if not isinstance(pin, dict) or set(pin) - PIN_KEYS:
        raise ConfigError(f"{where}: ci_pin must be an object with keys from "
                          + ", ".join(sorted(PIN_KEYS)))
    setup = pin.get("setup", [])
    if not isinstance(setup, list) or not all(
            isinstance(entry, dict) and {"cmd", "why"} <= set(entry) <= {"cmd", "cwd", "why"}
            and argv(entry["cmd"]) and isinstance(entry["why"], str) and entry["why"]
            and ("cwd" not in entry or relative_dir(entry["cwd"])) for entry in setup):
        raise ConfigError(f"{where}: ci_pin.setup is a list of "
                          "{\"cmd\": [argv prefix], \"cwd\"?: dir, \"why\": text}")
    actions = pin.get("setup_actions", [])
    if not isinstance(actions, list) or not all(isinstance(a, str) and a for a in actions):
        raise ConfigError(f"{where}: ci_pin.setup_actions is a list of `uses:` prefixes")
    jobs = pin.get("ci_only_jobs", {})
    if not isinstance(jobs, dict) or not all(isinstance(v, str) and v for v in jobs.values()):
        raise ConfigError(f"{where}: ci_pin.ci_only_jobs maps a job to why it has no gate")
    return {"setup": setup, "setup_actions": actions, "ci_only_jobs": jobs}


def parse(data, where: str) -> tuple[dict, dict]:
    """(areas, ci_pin) of a gates.json object."""
    if not isinstance(data, dict):
        raise ConfigError(f"{where}: expected an object of areas")
    if not isinstance(data.get("comment", ""), str):
        raise ConfigError(f"{where}: comment must be text")
    areas = {area: spec for area, spec in data.items() if area not in RESERVED}
    if not areas:
        raise ConfigError(f"{where}: expected an object of areas")
    for area, spec in areas.items():
        place = f"{where}: area {area!r}"
        if not isinstance(spec, dict) or set(spec) != {"paths", "steps"}:
            raise ConfigError(f"{place} must be an object with exactly 'paths' and 'steps'")
        if not isinstance(spec["paths"], list) or not all(
                isinstance(p, str) and p for p in spec["paths"]):
            raise ConfigError(f"{place}: 'paths' must be a list of non-empty strings")
        if not isinstance(spec["steps"], list) or not spec["steps"]:
            raise ConfigError(f"{place}: 'steps' must be a non-empty list")
        names = set()
        for step in spec["steps"]:
            if not isinstance(step, dict) or not {"name", "cwd", "cmd"} <= set(step) <= STEP_KEYS:
                raise ConfigError(f"{place}: each step needs name, cwd, cmd; allowed keys: "
                                  + ", ".join(sorted(STEP_KEYS)))
            name, cwd, cmd = step["name"], step["cwd"], step["cmd"]
            if not isinstance(name, str) or not name or "/" in name or name in names:
                raise ConfigError(f"{place}: step names must be unique non-empty strings")
            names.add(name)
            if not isinstance(cwd, str) or Path(cwd).is_absolute() or ".." in Path(cwd).parts:
                raise ConfigError(f"{place}/{name}: cwd must be checkout-relative")
            if not argv(cmd):
                raise ConfigError(f"{place}/{name}: cmd must be a non-empty argv list")
            for flag in ("expect_empty_stdout", "expand_globs"):
                if not isinstance(step.get(flag, False), bool):
                    raise ConfigError(f"{place}/{name}: {flag} must be true or false")
            if "ci_form" in step:
                check_ci_form(step["ci_form"], f"{place}/{name}")
    return areas, check_pin(data.get("ci_pin", {}), where)


def load(path: Path) -> tuple[dict, dict]:
    """(areas, ci_pin) from a gates file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return parse(data, str(path))


def project_config(root: Path) -> tuple[dict, dict]:
    """(areas, ci_pin) of the checkout's overlay; a missing gates.json is an error."""
    path = overlay_dir(root) / CONFIG
    if not path.is_file():
        raise ConfigError(f"no {CONFIG} in {path.parent}: the project defines no gates "
                          "(add one to the overlay, or pass --config)")
    try:
        data = load_overlay(root, CONFIG, {})
    except OverlayError as exc:
        raise ConfigError(str(exc)) from None
    return parse(data, str(path))


def changed_files(root: Path, base: str) -> set[str]:
    # --no-renames: a file moved out of an area still triggers that area.
    tracked = git("diff", "--name-only", "--no-renames", "-z", base, "--", cwd=root).split("\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z", cwd=root).split("\0")
    return {name for name in (*tracked, *untracked) if name}


def path_test(path: str):
    if "*" in path or "?" in path:
        return glob_pattern(path).match
    if path.endswith("/"):
        return lambda name: name.startswith(path)
    return lambda name: name == path


def touches(paths: list[str], files: set[str]) -> bool:
    tests = [path_test(path) for path in paths]
    return any(test(name) for test in tests for name in files)


def resolve(step: dict, root: Path, base: str) -> list[str]:
    cmd = [arg.replace("{base}", base) for arg in step["cmd"]]
    if not step.get("expand_globs"):
        return cmd
    expanded = []
    for arg in cmd:
        if "*" in arg or "?" in arg:
            matches = sorted(glob.glob(arg, root_dir=root / step["cwd"]))
            if not matches:
                raise StepError(f"no files match {arg}")
            expanded += matches
        else:
            expanded.append(arg)
    return expanded


def run_step(area: str, step: dict, root: Path, base: str) -> bool:
    label = f"{area}/{step['name']}"
    started = time.monotonic()
    try:
        cmd = resolve(step, root, base)
    except StepError as exc:
        print(f"FAIL {label} ({exc})", flush=True)
        return False
    log = default_log(root, slug(["gate", area, step["name"]]))
    code = execute(cmd, log, cwd=root / step["cwd"])
    seconds = time.monotonic() - started
    if code:
        reason = f"exit={code}"
    elif step.get("expect_empty_stdout") and log.stat().st_size:
        reason = "unexpected output"
    else:
        print(f"PASS {label} {seconds:.1f}s", flush=True)
        return True
    # Indented, so a digest line can never be read as another step's summary.
    for line in digest(code, log, with_log=False).splitlines():
        print(f"  {line}")
    print(f"FAIL {label} ({reason}) {seconds:.1f}s log: {log}", flush=True)
    return False


# --- A reader for the subset of GitHub-workflow YAML that CI files use ----------------------
# Block mappings, `- ` step lists, plain/quoted scalars and `|`/`>` block scalars. It exists
# so the pin checks the commands CI executes, not words that appear in the file.

SHELL_OPS = {"&&", "||", ";", "|", "&", "|&"}
NON_GATING_OPS = {"||", "|", "&", "|&"}  # the command's exit code does not fail the step
ASSIGN_SUB = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\$\((.*)\)$", re.S)


def indent_of(line):
    return len(line) - len(line.lstrip(" "))


def scalar(value):
    value = value.strip()
    if value[:1] in ("'", '"') and value.find(value[0], 1) > 0:
        return value[1:value.find(value[0], 1)]
    return re.split(r"\s+#", value, maxsplit=1)[0].strip()


def block_scalar(lines, i, parent, style):
    body = []
    while i < len(lines) and (not lines[i].strip() or indent_of(lines[i]) > parent):
        body.append(lines[i])
        i += 1
    while body and not body[-1].strip():
        body.pop()
    width = min((indent_of(line) for line in body if line.strip()), default=0)
    body = [line[width:] for line in body]
    if style.startswith(">"):
        paragraphs, current = [], []
        for line in body + [""]:
            if line.strip():
                current.append(line.strip())
            elif current:
                paragraphs.append(" ".join(current))
                current = []
        return i, "\n".join(paragraphs)
    return i, "\n".join(body)


def children(lines, i, parent):
    """`key: value` pairs nested under a mapping key at indent `parent`."""
    found = {}
    while i < len(lines) and (not lines[i].strip() or indent_of(lines[i]) > parent):
        key, sep, value = lines[i].strip().partition(":")
        if sep and not key.startswith("#"):
            found[key.strip()] = scalar(value)
        i += 1
    return i, found


def run_defaults(lines, i, parent):
    """`defaults: run:` keys (working-directory, shell) of a mapping at indent `parent`."""
    found = {}
    while i < len(lines) and (not lines[i].strip() or indent_of(lines[i]) > parent):
        if lines[i].strip() == "run:":
            i, found = children(lines, i + 1, indent_of(lines[i]))
            continue
        i += 1
    return i, {k: v for k, v in found.items() if k in ("working-directory", "shell")}


def workflow_steps(text):
    """[{"job", "job_keys", "keys", "with"}] for every step under jobs.<job>.steps."""
    lines = text.splitlines()
    steps, in_jobs, job, job_keys, dash, step = [], False, None, {}, None, None
    workflow_defaults, job_defaults = {}, {}  # applied after the pass: defaults may come last
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent, body = indent_of(line), line.strip()
        if indent == 0:
            in_jobs, job, step = body == "jobs:", None, None
            if body == "defaults:":
                i, workflow_defaults = run_defaults(lines, i, 0)
            continue
        if not in_jobs:
            continue
        if indent == 2:
            job, job_keys, dash, step = body.rstrip(":").strip(), {}, None, None
            continue
        if job is None:
            continue
        if indent == 4 and body == "defaults:":
            i, found = run_defaults(lines, i, 4)
            job_defaults[job] = {**job_defaults.get(job, {}), **found}
            step = None
            continue
        if indent == 4 and not body.startswith("- "):
            key, _, value = body.partition(":")
            job_keys[key.strip()] = scalar(value)
            step = None
            continue
        if body.startswith("- ") and (dash is None or indent == dash):
            dash = indent
            step = {"job": job, "job_keys": job_keys, "keys": {}, "with": {}}
            steps.append(step)
            indent, body = indent + 2, body[2:].strip()
        if step is None or indent != dash + 2:
            continue
        key, _, value = body.partition(":")
        key, value = key.strip(), value.strip()
        if key == "run" and value[:1] in ("|", ">"):
            i, step["keys"]["run"] = block_scalar(lines, i, indent, value)
        elif key == "with" and not value:
            i, step["with"] = children(lines, i, indent)
        elif not value:
            i, _ = children(lines, i, indent)
            step["keys"][key] = ""
        else:
            step["keys"][key] = scalar(value)
    for step in steps:
        step["defaults"] = {**workflow_defaults, **job_defaults.get(step["job"], {})}
    return steps


def disabled(keys):
    return "if" in keys or scalar(keys.get("continue-on-error", "false")) != "false"


SHELL_KEYWORDS = {"then", "else", "do", "done", "esac", "{", "}"}


def gates(k, ops, last_line):
    """Whether a failure of segment k of a line fails the step under `bash -e`.

    The list around k runs between `;` separators. A segment that may be skipped (after `||`,
    or after `&&`, `cd` included: a missing directory skips the rest) or whose failure the shell
    ignores (before `||`, `|`, `&`, or before `&&`) gates only where that cannot hide a
    failure: in the script's last list made of `&&` alone, whose status is the step's. A
    background job (`&`) does not affect the command after it.
    """
    start = max((i + 1 for i, op in enumerate(ops[:k]) if op == ";"), default=0)
    end = next((i for i in range(k, len(ops)) if ops[i] == ";"), len(ops))
    before, after = ops[start:k], ops[k:end]
    final_and_list = last_line and end == len(ops) and all(op == "&&" for op in ops[start:end])
    if after[:1] and after[0] in NON_GATING_OPS:
        return False
    if "||" in before:
        return False
    if "&&" in before:
        return final_and_list
    if after[:1] == ["&&"]:
        return final_and_list
    return True


def step_commands(step):
    """(cwd, argv, gating) for every command of a step's `run:`, in order.

    `cd DIR` moves the cwd for the rest of the script, as in the shell; `NAME="$(cmd)"` runs
    cmd; leading `then`/`else`/`do` are skipped. A command is gating when its step and job are
    enabled, the shell is bash (`bash -e` on the runner), `set +e` is absent, and a failure
    stops the script: not followed by `||`, `|` or `&`, and before `&&` only in the script's
    last statement (bash -e ignores a failure before `&&` elsewhere).
    """
    script = step["keys"].get("run")
    if script is None:
        return []
    keys, defaults = step["keys"], step.get("defaults", {})
    cwd = posixpath.normpath(keys.get("working-directory")
                             or defaults.get("working-directory") or ".")
    shell = keys.get("shell") or defaults.get("shell") or "bash"
    enabled = not disabled(keys) and not disabled(step["job_keys"]) and shell == "bash"
    lines = []
    for line in script.replace("\\\n", " ").splitlines():
        lexer = shlex.shlex(line, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
        if tokens:
            lines.append(tokens)
    found, here = [], cwd
    for number, tokens in enumerate(lines):
        last_line = number == len(lines) - 1
        segments, ops, current = [], [], []
        for token in tokens:
            if token in SHELL_OPS:
                segments.append(current)
                ops.append(token)
                current = []
            else:
                current.append(token)
        segments.append(current)
        cleaned = []
        for argv_ in segments:
            while argv_ and argv_[0] in SHELL_KEYWORDS:
                argv_ = argv_[1:]
            cleaned.append(argv_)
        is_cd = [len(a) == 2 and a[0] == "cd" for a in cleaned]
        for k, argv_ in enumerate(cleaned):
            if not argv_:
                continue
            if is_cd[k]:
                here = posixpath.normpath(posixpath.join(here, argv_[1]))
                continue
            if len(argv_) == 1 and ASSIGN_SUB.match(argv_[0]):
                argv_ = shlex.split(ASSIGN_SUB.match(argv_[0]).group(1))
            if argv_[:2] == ["set", "+e"]:
                enabled = False
            found.append((here, argv_, gates(k, ops, last_line)))
    return [(cwd, argv_, gating and enabled) for cwd, argv_, gating in found]


def ci_commands(text):
    """(job, cwd, argv, gating) for every command a workflow's run steps execute."""
    return [(step["job"], cwd, argv_, gating) for step in workflow_steps(text)
            for cwd, argv_, gating in step_commands(step)]


def starts_with(argv_, expected):
    """argv begins with expected; `{base}` in expected stands for any one argument."""
    return len(argv_) >= len(expected) and all(
        e == "{base}" or e == a for e, a in zip(expected, argv_))


# --- The pin -------------------------------------------------------------------------------

def ci_forms(step: dict) -> list[dict]:
    """How CI runs a gate step: its ci_form, else its own command in its own cwd."""
    forms = step.get("ci_form") or [{"cwd": step["cwd"], "cmd": step["cmd"]}]
    return [{**form, "cwd": posixpath.normpath(form["cwd"])} for form in forms]


def pin_problems(areas: dict, pin: dict, text: str, name: str = "the workflow") -> list[str]:
    """Why gates.json is not the local copy of a workflow's gates; [] when it is."""
    commands, steps = ci_commands(text), workflow_steps(text)
    labelled = [(f"{area}/{step['name']}", form) for area, spec in areas.items()
                for step in spec["steps"] for form in ci_forms(step)]
    problems = []

    def runs(form, cwd, argv_):
        return "cmd" in form and form["cwd"] == cwd and starts_with(argv_, form["cmd"])

    def action_step(form, step):
        return (step["keys"].get("uses", "").startswith(form["uses"])
                and posixpath.normpath(step["with"].get("working-directory", ".")) == form["cwd"]
                and not disabled(step["keys"]) and not disabled(step["job_keys"]))

    for label, form in labelled:
        if "uses" in form:
            if not any(action_step(form, step) for step in steps):
                problems.append(f"{label}: {name} has no enabled {form['uses']} step with "
                                f"working-directory {form['cwd']}")
        elif not any(ok and runs(form, cwd, argv_) for _, cwd, argv_, ok in commands):
            ungated = sorted({job for job, cwd, argv_, _ in commands if runs(form, cwd, argv_)})
            problems.append(f"{label}: {name} never runs `{shlex.join(form['cmd'])}` in "
                            f"{form['cwd']} as a gating command"
                            + (f" (it runs without gating in job {', '.join(ungated)})"
                               if ungated else ""))
    setup = pin["setup"]
    for job, cwd, argv_, _ in commands:
        covered = any(runs(form, cwd, argv_) for _, form in labelled) or any(
            entry.get("cwd") in (None, cwd) and starts_with(argv_, entry["cmd"])
            for entry in setup)
        if not covered:
            problems.append(f"job {job} runs `{shlex.join(argv_)}` in {cwd}, which no gate "
                            "step runs: add it to gates.json or declare it in ci_pin.setup")
    actions = [form["uses"] for _, form in labelled if "uses" in form]
    for step in steps:
        uses = step["keys"].get("uses")
        if uses and not uses.startswith((*pin["setup_actions"], *actions)):
            problems.append(f"job {step['job']} uses {uses}, which is neither a gate step's "
                            "action nor in ci_pin.setup_actions")
    jobs = list(dict.fromkeys(step["job"] for step in steps))
    gated = {job for job, cwd, argv_, ok in commands
             if ok and any(runs(form, cwd, argv_) for _, form in labelled)}
    gated |= {step["job"] for step in steps for _, form in labelled
              if "uses" in form and action_step(form, step)}
    ci_only = pin["ci_only_jobs"]
    for job in jobs:
        if job not in gated and job not in ci_only:
            problems.append(f"job {job} runs no gate step: gate it or declare it in "
                            "ci_pin.ci_only_jobs")
    for job in ci_only:
        if job not in jobs:
            problems.append(f"ci_pin.ci_only_jobs names {job}, which {name} does not have")
        elif job in gated:
            problems.append(f"ci_pin.ci_only_jobs names {job}, which runs gate steps")
    return problems


def pin_ci(gates: Path | str, workflow: Path | str) -> list[str]:
    """The pin problems of a gates file against a workflow file; [] when pinned."""
    areas, pin = load(Path(gates))
    path = Path(workflow)
    return pin_problems(areas, pin, path.read_text(encoding="utf-8"), path.name)


def pin_main(args, root: Path) -> int:
    workflow = Path(args.pin_ci)
    workflow = workflow if workflow.is_absolute() else root / workflow
    try:
        areas, pin = load(Path(args.config)) if args.config else project_config(root)
        text = workflow.read_text(encoding="utf-8")
    except (ConfigError, OSError, ValueError) as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return 2
    problems = pin_problems(areas, pin, text, args.pin_ci)
    for problem in problems:
        print(f"pin: {problem}")
    if problems:
        print(f"gate: {len(problems)} pin problem{'s' if len(problems) > 1 else ''} in "
              f"{args.pin_ci}")
        return 1
    print(f"gate: {args.pin_ci} runs every gate step and nothing else")
    return 0


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("areas", nargs="*", metavar="AREA")
    parser.add_argument("--changed", metavar="BASE", help="add areas changed since BASE")
    parser.add_argument("--base", help="value of {base} (default: --changed, else origin/main)")
    parser.add_argument("--keep-going", action="store_true", help="run every step")
    parser.add_argument("--dry-run", action="store_true", help="print commands, run nothing")
    parser.add_argument("--config", help=f"gates file (default: the overlay's {CONFIG})")
    parser.add_argument("--pin-ci", metavar="WORKFLOW",
                        help="check gates.json against a CI workflow; runs nothing")
    args = parser.parse_args()
    if args.pin_ci and (args.areas or args.changed or args.keep_going or args.dry_run):
        parser.error("--pin-ci takes no AREA, --changed, --keep-going or --dry-run")
    try:
        root = Path(git("rev-parse", "--show-toplevel")).resolve()
        if args.pin_ci:
            return pin_main(args, root)
        config, _ = load(Path(args.config)) if args.config else project_config(root)
        unknown = [area for area in args.areas if area not in config]
        if unknown:
            raise ConfigError(f"unknown area: {', '.join(unknown)} (known: {', '.join(config)})")
        selected = set(args.areas)
        if args.changed:
            files = changed_files(root, args.changed)
            selected |= {area for area, spec in config.items() if touches(spec["paths"], files)}
            if not selected:
                print(f"gate: no gated paths changed since {args.changed}")
                return 0
        elif not selected:
            selected = set(config)
        base = args.base or args.changed or "origin/main"
    except (ConfigError, StateError, OSError, ValueError) as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return 2

    steps = [(area, step) for area in config if area in selected for step in config[area]["steps"]]
    if args.dry_run:
        failed = False
        for area, step in steps:
            prefix = f"{area}/{step['name']} [{step['cwd']}]"
            try:
                print(f"{prefix} {shlex.join(resolve(step, root, base))}")
            except StepError as exc:
                print(f"{prefix} error: {exc}")
                failed = True
        return 1 if failed else 0

    passed = failed = skipped = 0
    for area, step in steps:
        if failed and not args.keep_going:
            print(f"SKIP {area}/{step['name']}")
            skipped += 1
        elif run_step(area, step, root, base):
            passed += 1
        else:
            failed += 1
    print(f"gate: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
