#!/usr/bin/env python3
"""Run the project's gates from their one copy, the overlay's gates.json.

Usage: gate.py [AREA ...] [--changed BASE] [--base REF] [--keep-going] [--dry-run]
               [--targeted] [--run-dir DIR] [--config FILE]
       gate.py --pin-ci WORKFLOW [--config FILE]

Overlay file `gates.json` (in $HARNESS_PROJECT, else <checkout>/harness/project; --config
names another file). Its top-level keys are areas, run in file order, except two reserved
keys:
  <area>    {"paths": [...], "steps": [...], "services"?: [...]}
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
  scope                 "full" or "targeted": overrides how the step's command is classified
                        for the load guard (below); old files without it keep working
  ci_form               read only by --pin-ci: how CI runs this step when its command
                        differs on purpose, a list of {"cwd", "cmd"} (CI commands that
                        together stand for the step) or {"cwd", "uses"} (an action prefix
                        run with that working-directory input)
Steps check the working tree as it is and write no sources; dependency installs are the
checkout's setup, not steps. Without gates.json there is nothing to run, and gate.py says
so with exit 2 rather than reporting a pass.

Optional services are {"name", "url_env", "start", "timeout"?: seconds}. Before selected
steps, probe the environment URL's host/port over TCP (default connect timeout 1 second).
Fail once with a credential-free endpoint and manual startup instruction; never read .env
or execute startup commands. --dry-run and --pin-ci never connect to services.

Without AREA or --changed every area runs. --changed adds the areas whose paths
changed since BASE: committed, staged, unstaged and untracked. Steps run through
quiet.py with logs under <checkout>/.harness/logs/; the run stops at the first
failure unless --keep-going. One line per step: PASS, FAIL (its digest above it) or
SKIP. Exit codes: 0 all passed, 1 a step failed, 2 usage or configuration error.

Load guard. Only full test suites wait for a busy machine. Each step's command is one of
  full      a whole suite: `go test ./...`, bare `pytest`/`unittest`/`npm test`/`jest`/
            `vitest run`, `cargo test`, `make test`, `rspec`, `dotnet test`, `gradle test`, tox
  targeted  one file, module, package or name filter (`pytest a_test.py`, `-k`, `--lf`,
            `unittest tests.test_x`, `go test ./pkg`, `-run`, `jest -t`, a single spec file,
            `cargo test name`, `--filter`). `{base}` substitution alone is not a test filter.
  check     anything else: lint, format, types, docs, metadata, unrecognised wrappers
An opaque wrapper that runs a whole suite declares "scope": "full". Before the run's first
full step gate.py asks load_guard.wait_for_capacity once: on an idle machine it returns at
once; on a busy one it waits (bounded) and says so. Its environment marks this process tree,
so nested heavy commands never wait again. Targeted and check steps never ask. --changed
picks areas, not test sets: a full-suite step of a changed area is still full. --targeted
declares the whole run narrow (an opaque wrapper given a narrow selection): nothing waits.
A broken guard fails open. --dry-run and --pin-ci neither wait nor measure.

Timings. Every executed step is measured from command start to exit, apart from any
guard wait (recorded separately as guard_wait). One JSON line per step (run id, time,
checkout, commit, area/step, cwd, resolved argv, scope, fingerprint, status, exit,
seconds, guard_wait, slower) goes to DIR/gate-timings.jsonl with --run-dir, else to the
checkout's bounded .harness/logs/gate-timings.jsonl. Passing runs also join a history in the
primary checkout's .harness/gate/timings.json, keyed by a fingerprint of area, step name,
cwd and the resolved argv ({base} and globs expanded), so a changed command starts afresh;
failures never join it. It keeps the last 20 runs per step for 30 days and 200 steps, under
an exclusive lock with atomic replacement. A passing step over 1.5x its median of at least 3
earlier runs gets a `NOTE` line: advisory, never a failure, including fast steps. A
history that cannot be read or written is a stderr warning, never a failed gate.

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
import contextlib
import glob
import hashlib
import json
import os
from pathlib import Path
import posixpath
import re
import shlex
import socket
import statistics
import sys
import tempfile
import time
from urllib.parse import urlsplit

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from project import OverlayError, load as load_overlay, overlay_dir
from quiet import default_log, digest, execute, slug
from task_state import StateError, git
from tripwire import glob_pattern

try:
    import fcntl
except ImportError:  # no flock (Windows): history writes stay atomic, not serialized
    fcntl = None
try:
    import load_guard
except ImportError:  # an older installed core without the guard: run unguarded
    load_guard = None

CONFIG = "gates.json"
RESERVED = {"comment", "ci_pin"}
STEP_KEYS = {"name", "cwd", "cmd", "expect_empty_stdout", "expand_globs", "ci_form", "scope"}
SCOPES = ("full", "targeted")
SLOWER = 1.5          # advisory note above this multiple of the rolling median
MIN_RUNS = 3          # successful runs before a median means anything
HISTORY_SAMPLES = 20  # successful runs kept per step fingerprint
HISTORY_DAYS = 30     # older samples expire
HISTORY_STEPS = 200   # fingerprints kept; the least recently run go first
RUN_LOG_BYTES = 1 << 20  # the default run log keeps its newer half past this size
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
        if not isinstance(spec, dict) or not {"paths", "steps"} <= set(spec) <= {"paths", "steps", "services"}:
            raise ConfigError(f"{place} needs 'paths', 'steps' and optional 'services'")
        services = spec.get("services", [])
        if not isinstance(services, list):
            raise ConfigError(f"{place}: services must be a list")
        for service in services:
            if not isinstance(service, dict) or not {"name", "url_env", "start"} <= set(service) <= {"name", "url_env", "start", "timeout"}:
                raise ConfigError(f"{place}: each service needs name, url_env, start and optional timeout")
            if any(not isinstance(service[key], str) or not service[key].strip()
                   or any(ord(c) < 32 for c in service[key]) for key in ("name", "url_env", "start")):
                raise ConfigError(f"{place}: service fields must be nonempty single-line text")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", service["url_env"]):
                raise ConfigError(f"{place}: url_env must be an environment variable name")
            timeout = service.get("timeout", 1)
            if type(timeout) not in (int, float) or not 0 < timeout <= 30:
                raise ConfigError(f"{place}: service timeout must be in (0, 30] seconds")
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
            if step.get("scope", "full") not in SCOPES:
                raise ConfigError(f"{place}/{name}: scope must be \"full\" or \"targeted\"")
    return areas, check_pin(data.get("ci_pin", {}), where)


def load(path: Path) -> tuple[dict, dict]:
    """(areas, ci_pin) from a gates file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    return parse(data, str(path))


def probe_services(areas: dict, selected: set[str]) -> str | None:
    """Bounded TCP liveness preflight; never echo URL credentials or run setup."""
    seen = set()
    for area, spec in areas.items():
        if area not in selected:
            continue
        for service in spec.get("services", []):
            key = (service["name"], service["url_env"])
            if key in seen:
                continue
            seen.add(key)
            name, env = key
            value = os.environ.get(env)
            recovery = f"start it with {service['start']}"
            if not value:
                return f"{name}: {env} is unset; {recovery}"
            try:
                url = urlsplit(value)
                port = url.port or {"postgres": 5432, "postgresql": 5432,
                                    "redis": 6379, "mysql": 3306,
                                    "http": 80, "https": 443}.get(url.scheme)
                host = url.hostname
                if not host or not port or any(ord(c) < 32 for c in host):
                    raise ValueError("missing endpoint")
            except ValueError:
                return f"{name}: {env} must be a service URL with host and port; {recovery}"
            endpoint = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
            try:
                with socket.create_connection((host, port), timeout=service.get("timeout", 1)):
                    pass
            except OSError:
                return f"{name} at {endpoint} is down; {recovery}"
    return None


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


PYTHON = re.compile(r"python[0-9.]*(\.exe)?$")
TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.(py|go)|[^/]*[._-](test|spec)\."
                       r"[cm]?[jt]sx?|[^/]*_spec\.rb)$")
LAUNCHERS = ({"uv", "poetry", "pipenv", "hatch", "pdm", "rye"}, {"run"})
RUNNER_VALUES = {
    "uv": {"--with", "--with-editable", "--with-requirements", "--python", "-p",
           "--project", "--directory", "--package", "--group", "--extra", "--no-group",
           "--index", "--default-index", "--index-url", "--extra-index-url", "--find-links",
           "--config-file", "--cache-dir", "--env-file", "--resolution", "--prerelease",
           "--exclude-newer", "--link-mode", "--keyring-provider", "--color", "--no-extra",
           "--only-group", "--python-platform", "--index-strategy", "--upgrade-package",
           "--fork-strategy", "--exclude-newer-package", "--no-sources-package",
           "--reinstall-package", "--config-setting", "--config-settings-package",
           "--no-build-isolation-package", "--no-build-package", "--no-binary-package",
           "--refresh-package", "--allow-insecure-host", "-w", "-i", "-f", "-P", "-C"},
    "poetry": {"--directory", "-C", "--project", "-P"},
    "pipenv": {"--python"}, "hatch": {"--env", "-e"},
    "pdm": {"--project", "-p"}, "rye": {"--pyproject"},
    "npx": {"--package", "-p", "--cache", "--userconfig", "--node-options"},
    "bunx": {"--package", "-p"}, "pnpm": {"--filter", "-F", "--dir", "-C"},
    "bundle": {"--gemfile"},
}


def runner_tail(args: list[str], runner: str) -> list[str]:
    """Consume launcher options only, leaving the executable and its flags intact."""
    index = 0
    valued = RUNNER_VALUES.get(runner, set())
    while index < len(args) and args[index].startswith("-"):
        flag = args[index]
        if flag == "--":
            return args[index + 1:]
        index += 2 if flag in valued else 1
    return args[index:]
JS_RUNNERS = {"jest", "vitest", "mocha", "ava", "playwright", "cypress", "karma", "tap"}
PACKAGE_MANAGERS = {"npm", "pnpm", "yarn", "bun"}


def has_flag(args: list[str], *flags: str) -> bool:
    return any(arg == flag or arg.startswith(flag + "=") for arg in args for flag in flags)


def flag_values(args: list[str], *flags: str) -> list[str]:
    """Values of separate, equals and attached short options, in argument order."""
    values = []
    for index, arg in enumerate(args):
        for flag in flags:
            if arg == flag and index + 1 < len(args):
                values.append(args[index + 1])
            elif arg.startswith(flag + "="):
                values.append(arg[len(flag) + 1:])
            elif len(flag) == 2 and arg.startswith(flag) and len(arg) > 2:
                values.append(arg[2:])
    return values


def positionals(args: list[str], valued: set[str] = frozenset()) -> list[str]:
    """Non-flag arguments; a flag in `valued` consumes the next argument."""
    found, skip = [], False
    for arg in args:
        if skip:
            skip = False
        elif arg.startswith("-"):
            skip = arg in valued
        else:
            found.append(arg)
    return found


# Flags whose value is the next argument, so it is never read as a test file or package.
VALUED = {
    "pytest": {"--maxfail", "-n", "--numprocesses", "-c", "--rootdir", "-p", "-m", "-o",
               "--override-ini", "--confcutdir", "--basetemp", "--junitxml", "--durations",
               "--cov", "--cov-report", "--tb", "-W", "--timeout", "--dist", "-r",
               "--import-mode", "--log-level"},
    "unittest": {"-s", "--start-directory", "-t", "--top-level-directory", "-p", "--pattern",
                 "-k"},
    "go": {"-tags", "-count", "-timeout", "-p", "-parallel", "-coverprofile", "-covermode",
           "-coverpkg", "-o", "-bench", "-benchtime", "-cpu", "-shuffle", "-exec", "-run",
           "-skip", "-mod", "-modfile", "-ldflags", "-gcflags", "-vet", "-C"},
    "js": {"-c", "--config", "--reporter", "--reporters", "-w", "--maxWorkers", "--project",
           "--environment", "--testTimeout", "--outputFile", "--shard", "--pool", "--dir",
           "--root", "-r", "--require", "--test-reporter", "--test-reporter-destination",
           "--test-concurrency", "--timeout", "--workers", "-j"},
}
JS_FILTERS = ("-t", "--testNamePattern", "--grep", "-g", "--filter", "--testPathPattern",
              "--findRelatedTests", "-o", "--onlyChanged", "--changed", "--changedSince",
              "--test-name-pattern", "--test-only", "--related")
NEUTRAL_FILTERS = {"", "*", ".", ".*", "^.*$"}
BOOLEAN_FILTERS = {"--lf", "--last-failed", "-o", "--onlyChanged", "--changed", "--test-only"}


def effective_filter(args: list[str], filters: tuple[str, ...]) -> bool:
    if has_flag(args, *(set(filters) & BOOLEAN_FILTERS)):
        return True
    values = flag_values(args, *(set(filters) - BOOLEAN_FILTERS))
    return bool(values) and values[-1].strip() not in NEUTRAL_FILTERS


def narrowed(args: list[str], filters: tuple[str, ...], valued: set[str] = frozenset()) -> bool:
    """A name filter, or a single concrete test file / node id (no glob, no directory)."""
    if effective_filter(args, filters):
        return True
    return any("::" in arg or (TEST_FILE.search(arg.split("::")[0]) and "*" not in arg)
               for arg in positionals(args, set(valued) | (set(filters) - BOOLEAN_FILTERS)))


def harness_command(name: str, rest: list[str]) -> str | None:
    """The harness's own heavy commands, so a gate step running one is admitted by the
    gate's guard (its child then never waits, and the wait stays out of step time)."""
    if name == "skill_evals.py":
        if has_flag(rest, "--help", "-h"):
            return "check"
        return "full" if positionals(rest)[:1] == ["run"] else "check"
    if name == "xreview.py":
        if has_flag(rest, "--dry-run", "-h", "--help"):
            return "check"
        return "targeted" if has_flag(rest, "--paths") else "full"
    return None


def classify(cmd: list[str]) -> str:
    """How heavy a gate command is, from its argv: "full" (a whole test suite: the load
    guard may hold it while the machine is busy), "targeted" (one file, module, package or
    name filter: never waits) or "check" (lint, format, types, docs, metadata, anything
    not recognised as a test runner: never waits). `{base}` proves nothing on its own: an
    opaque wrapper is check whatever its arguments, and declares "scope" when it runs a
    whole suite."""
    args = list(cmd)
    while args:  # peel launchers: env, uv run, npx, bundle exec, python -m
        head = Path(args[0]).name
        if head == "env":
            args = [a for a in args[1:] if "=" not in a or a.startswith("-")]
        elif head in LAUNCHERS[0] and runner_tail(args[1:], head)[:1] == ["run"]:
            args = runner_tail(runner_tail(args[1:], head)[1:], head)
        elif head in ("npx", "bunx"):
            args = runner_tail(args[1:], head)
        elif head in ("bundle", "pnpm") and runner_tail(args[1:], head)[:1] in (["exec"], ["dlx"]):
            args = runner_tail(runner_tail(args[1:], head)[1:], head)
        elif PYTHON.match(head) and len(args) > 2 and args[1] == "-m":
            args = args[2:]
        else:
            break
    if not args:
        return "check"
    head, rest = Path(args[0]).name, args[1:]
    if PYTHON.match(head) or head.endswith(".py"):
        if PYTHON.match(head):  # python [flags] script.py args; -c and -m X handled above
            if "-c" in rest:
                return "check"
            found = positionals(rest, {"-X", "-W"})
            if not found:
                return "check"
            head, rest = Path(found[0]).name, rest[rest.index(found[0]) + 1:]
        return harness_command(head, rest) or ("targeted" if TEST_FILE.search(head)
                                               else "check")
    if head in ("pytest", "py.test"):
        return "targeted" if narrowed(rest, ("-k", "--lf", "--last-failed", "--deselect",
                                             "--sw", "--stepwise"), VALUED["pytest"]) \
            else "full"
    if head == "unittest":
        keywords = flag_values(rest, "-k")
        filtered = bool(keywords) and all(value.strip() not in NEUTRAL_FILTERS for value in keywords)
        if rest[:1] == ["discover"]:
            if filtered:
                return "targeted"
            patterns = flag_values(rest, "-p", "--pattern")
            broad = {"*", "*.py", "test*.py", "test_*.py", "*_test.py", "*test*.py"}
            return "targeted" if patterns and patterns[-1] not in broad else "full"
        return "targeted" if filtered or positionals(rest, VALUED["unittest"]) \
            else "full"
    if head == "go" and rest[:1] == ["test"]:
        if effective_filter(rest, ("-run",)):
            return "targeted"
        packages = positionals(rest[1:], VALUED["go"])
        return "full" if not packages or any(p.endswith("...") for p in packages) \
            else "targeted"
    if head == "cargo" and rest[:1] in (["test"], ["nextest"]):
        return "targeted" if positionals(rest[1:] if rest[:1] == ["test"] else rest[2:]) \
            else "full"
    if head in PACKAGE_MANAGERS:
        script = rest[1:2] if rest[:1] == ["run"] else rest[:1]
        if not script or not script[0].startswith("test"):
            return "check"
        extra = rest[rest.index("--") + 1:] if "--" in rest else rest[1 + (rest[:1] == ["run"]):]
        return "targeted" if narrowed(extra, JS_FILTERS, VALUED["js"]) else "full"
    if head in JS_RUNNERS or (head == "node" and rest[:1] == ["--test"]):
        if head == "vitest" and rest[:1] in (["run"], ["watch"]):
            rest = rest[1:]  # a mode, not a test selection
        files = positionals(rest, VALUED["js"])
        return "targeted" if narrowed(rest, JS_FILTERS, VALUED["js"]) \
            or (head == "node" and len(files) == 1 and "*" not in files[0]
                and not files[0].endswith("/")) else "full"
    if head in ("rspec", "dotnet") and (head == "rspec" or rest[:1] == ["test"]):
        return "targeted" if narrowed(rest, ("--filter", "-e", "--example", "--tag"),
                                      {"-r", "--require", "-f", "--format", "-o", "--out",
                                       "-c", "--configuration", "-l", "--logger"}) else "full"
    if head in ("mvn", "mvnw", "gradle", "gradlew") and any(a.endswith("test") for a in rest):
        return "targeted" if any(a.startswith("-Dtest=") for a in rest) \
            or has_flag(rest, "--tests") else "full"
    if head in ("make", "just", "task", "tox", "nox"):
        targets = positionals(rest)
        if head in ("tox", "nox"):
            return "full"
        return "full" if any(t.startswith("test") for t in targets) else "check"
    return "check"


def step_scope(step: dict) -> str:
    """The step's declared "scope", else classify(cmd)."""
    return step.get("scope") or classify(step["cmd"])


def fingerprint(area: str, step: dict, cmd: list[str]) -> str:
    """Steps compare only with themselves: the same area, name, cwd and resolved argv."""
    key = json.dumps([area, step["name"], step["cwd"], cmd], separators=(",", ":"))
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def slowdown(samples: list[float], seconds: float) -> dict | None:
    """The advisory verdict on one successful run against its earlier successful runs."""
    if len(samples) < MIN_RUNS:
        return None
    median = statistics.median(samples)
    if median > 0 and seconds > SLOWER * median:
        return {"median": round(median, 3), "ratio": round(seconds / median, 1)
                if median else None, "runs": len(samples)}
    return None


@contextlib.contextmanager
def locked(path: Path):
    """An exclusive lock beside path, shared by every gate on the machine."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as handle:
        if fcntl:
            fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def replace_text(path: Path, text: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False,
                                     prefix=f".{path.name}.") as stream:
        stream.write(text)
    os.replace(stream.name, path)


def timing_home(root: Path) -> Path:
    """Timings live in the primary checkout, so every worktree of a lane shares them."""
    try:
        record = git("worktree", "list", "--porcelain", "-z", cwd=root).split("\0\0", 1)[0]
        first = record.split("\0")[0]
        if first.startswith("worktree ") and "bare" not in record.split("\0"):
            root = Path(first[len("worktree "):])
    except (StateError, OSError):
        pass
    return root / ".harness" / "gate"


def remember(root: Path, record: dict, now: float | None = None,
             home: Path | None = None) -> tuple[list[float], str]:
    """Add a run to the step's bounded history; return the earlier successful seconds of
    the same fingerprint and a warning (empty when none). Only passing runs join the
    baseline: a failure's time says nothing about how long the step takes."""
    now = time.time() if now is None else now
    path = (home or timing_home(root)) / "timings.json"
    warning = ""
    with locked(path):
        try:
            data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            steps = data.get("steps", {}) if isinstance(data, dict) else None
            if not isinstance(steps, dict):
                raise ValueError("not a timing history")
        except (ValueError, OSError) as exc:
            warning = f"timing history unreadable ({exc}); starting it anew at {path}"
            steps = {}
        horizon = now - HISTORY_DAYS * 86400
        kept = {}
        for key, entry in steps.items():
            samples = [s for s in (entry.get("samples", []) if isinstance(entry, dict) else [])
                       if isinstance(s, dict) and isinstance(s.get("seconds"), (int, float))
                       and isinstance(s.get("at"), (int, float)) and s["at"] >= horizon]
            if samples:
                kept[key] = {"label": entry.get("label", ""),
                             "samples": samples[-HISTORY_SAMPLES:]}
        earlier = [s["seconds"] for s in kept.get(record["fingerprint"], {}).get("samples", [])]
        if record["status"] == "pass":
            entry = kept.setdefault(record["fingerprint"], {"samples": []})
            entry["label"] = record["label"]
            entry["samples"] = (entry["samples"] + [{
                "seconds": round(record["seconds"], 3), "at": now, "run": record["run"],
                "commit": record["commit"]}])[-HISTORY_SAMPLES:]
        newest = sorted(kept, key=lambda key: kept[key]["samples"][-1]["at"], reverse=True)
        kept = {key: kept[key] for key in newest[:HISTORY_STEPS]}
        replace_text(path, json.dumps({"version": 1, "steps": kept}, indent=1) + "\n")
    return earlier, warning


def append_run(path: Path, record: dict, limit: int | None) -> None:
    """One JSON line per step; a bounded log keeps its newer half past limit bytes."""
    with locked(path):
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record) + "\n")
        if limit and path.stat().st_size > limit:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            replace_text(path, "".join(lines[len(lines) // 2:]))


class Timing:
    """One gate run's measurements: where they go and what they share."""

    def __init__(self, root: Path, run_dir: Path | None, targeted: bool = False):
        self.root = root
        self.targeted = targeted  # --targeted: the whole run is narrow, whatever the steps
        self.run_log = (run_dir / "gate-timings.jsonl") if run_dir else \
            root / ".harness" / "logs" / "gate-timings.jsonl"
        self.limit = None if run_dir else RUN_LOG_BYTES
        self.run = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{os.getpid()}"
        try:
            self.commit = git("rev-parse", "HEAD", cwd=root)
        except (StateError, OSError):
            self.commit = ""
        self.guard_wait = 0.0  # charged to the next step measured, then reset
        self.home = timing_home(root)

    def measure(self, area: str, step: dict, cmd: list[str], code: int, passed: bool,
                seconds: float) -> dict | None:
        """Record one step; return the advisory slowdown, never raise."""
        record = {
            "run": self.run, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "checkout": str(self.root), "commit": self.commit, "area": area,
            "step": step["name"], "label": f"{area}/{step['name']}", "cwd": step["cwd"],
            "argv": cmd,
            "scope": "targeted" if self.targeted and step_scope(step) == "full"
            else step_scope(step), "fingerprint": fingerprint(area, step, cmd),
            "status": "pass" if passed else "fail", "exit": code,
            "seconds": round(seconds, 3), "guard_wait": round(self.guard_wait, 3)}
        self.guard_wait = 0.0
        note = None
        try:
            earlier, warning = remember(self.root, record, home=self.home)
            if warning:
                print(f"gate: {warning}", file=sys.stderr, flush=True)
            note = slowdown(earlier, seconds) if passed else None
        except (OSError, ValueError, TypeError) as exc:
            print(f"gate: timing not recorded in shared history ({exc})", file=sys.stderr, flush=True)
        record["slower"] = note
        try:
            self.run_log.parent.mkdir(parents=True, exist_ok=True)
            append_run(self.run_log, record, self.limit)
        except (OSError, ValueError, TypeError) as exc:
            print(f"gate: per-run timing not recorded ({exc})", file=sys.stderr, flush=True)
        return note


@contextlib.contextmanager
def environment(env: dict | None):
    """os.environ is env for the block (quiet.execute's child inherits it), then restored."""
    if env is None:
        yield
        return
    saved = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    try:
        yield
    finally:
        os.environ.clear()
        os.environ.update(saved)


def run_step(area: str, step: dict, root: Path, base: str, timing: Timing | None = None,
             env: dict | None = None) -> bool:
    label = f"{area}/{step['name']}"
    started = time.monotonic()
    try:
        cmd = resolve(step, root, base)
    except StepError as exc:
        print(f"FAIL {label} ({exc})", flush=True)
        return False
    log = default_log(root, slug(["gate", area, step["name"]]))
    with environment(env):
        code = execute(cmd, log, cwd=root / step["cwd"])
    seconds = time.monotonic() - started
    passed = False
    if code:
        reason = f"exit={code}"
    elif step.get("expect_empty_stdout") and log.stat().st_size:
        reason = "unexpected output"
    else:
        passed = True
    note = timing.measure(area, step, cmd, code, passed, seconds) if timing else None
    if passed:
        print(f"PASS {label} {seconds:.1f}s", flush=True)
        if note:
            ratio = f"{note['ratio']}x its" if note["ratio"] else "above its"
            print(f"NOTE {label} took {seconds:.1f}s, {ratio} median {note['median']:.1f}s over "
                  f"{note['runs']} runs (advisory; a cache off, a new per-test service or a "
                  "busy machine?)", flush=True)
        return True
    # Indented, so a digest line can never be read as another step's summary.
    for line in digest(code, log, with_log=False).splitlines():
        print(f"  {line}")
    print(f"FAIL {label} ({reason}) {seconds:.1f}s log: {log}", flush=True)
    return False


def guard_settings(root: Path) -> None:
    """Fail fast (ConfigError, exit 2) on unusable guard settings in project.json or the
    environment, before any step runs; the guard's kill switch wins over them."""
    check = getattr(load_guard, "config", None)
    if check is None:
        return
    try:
        check(root, dict(os.environ))
    except ValueError as exc:
        raise ConfigError(f"load guard: {exc}") from None


def wait_for_guard(root: Path, run_dir: Path | None) -> tuple[float, dict | None]:
    """Ask the host load guard once, before the run's first full-suite step. Return the
    seconds spent waiting and the admitted environment for every later step: it marks this
    process tree, so nested heavy commands (skill_evals.py run, xreview.py) never wait
    again. Invalid settings are a ConfigError (exit 2); any other guard failure fails
    open, since missing telemetry must never block the user's check."""
    if load_guard is None:
        return 0.0, None
    started = time.monotonic()
    try:
        admitted = load_guard.wait_for_capacity(root, heavy=True, label="gate", run=run_dir,
                                                env=dict(os.environ))
    except ValueError as exc:
        raise ConfigError(f"load guard: {exc}") from None
    except Exception as exc:  # noqa: BLE001 - advisory machinery never fails a gate
        print(f"gate: load guard unavailable ({exc}); running now", file=sys.stderr, flush=True)
        return time.monotonic() - started, None
    waited = time.monotonic() - started
    if waited >= 0.1:
        print(f"gate: waited {waited:.1f}s for the load guard", flush=True)
    return waited, admitted


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
    parser.add_argument("--targeted", action="store_true",
                        help="the selected steps test a narrow set: never wait for the load guard")
    parser.add_argument("--run-dir", metavar="DIR",
                        help="also append each step's timing to DIR/gate-timings.jsonl")
    args = parser.parse_args()
    if args.pin_ci and (args.areas or args.changed or args.keep_going or args.dry_run
                        or args.run_dir or args.targeted):
        parser.error("--pin-ci takes no AREA, --changed, --keep-going, --dry-run, --targeted "
                     "or --run-dir")
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

    problem = probe_services(config, selected)
    if problem:
        print(f"FAIL {problem}", flush=True)
        return 1

    run_dir = Path(args.run_dir).resolve() if args.run_dir else None
    full = not args.targeted and any(step_scope(step) == "full" for _, step in steps)
    try:
        if full:
            guard_settings(root)
    except ConfigError as exc:
        print(f"gate: {exc}", file=sys.stderr)
        return 2
    timing = Timing(root, run_dir, args.targeted)
    guarded, admitted = not full, None
    passed = failed = skipped = 0
    for area, step in steps:
        if failed and not args.keep_going:
            print(f"SKIP {area}/{step['name']}")
            skipped += 1
            continue
        if not guarded and step_scope(step) == "full":
            guarded = True
            try:
                timing.guard_wait, admitted = wait_for_guard(root, run_dir)
            except ConfigError as exc:
                print(f"gate: {exc}", file=sys.stderr)
                return 2
        if run_step(area, step, root, base, timing, admitted):
            passed += 1
        else:
            failed += 1
    print(f"gate: {passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
