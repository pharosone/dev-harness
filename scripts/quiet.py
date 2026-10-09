#!/usr/bin/env python3
"""Run one command with its full output on disk and print only what a reader needs.

Usage: quiet.py [--log FILE] -- CMD ARGS...

CMD runs without a shell; its stdout and stderr go, interleaved, to one log file
(default `<checkout>/.harness/logs/<UTC timestamp>-<slug of CMD>.log`, where the
checkout is the Git top level of the current directory, or the directory itself
outside Git; a second run in the same second gets its own file).

Printed: `exit=<code>`; on failure the failure lines found before the tail, each with
up to five following lines (at most 100 lines), then the last 30 lines; on success
only the last 10 lines; then `log: <path>`. The exit code is CMD's own: 127 when it
is not found, 126 when it cannot be executed, 128+N when signal N killed it.
"""

import argparse
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
import re
import subprocess
import sys

FAILURE = re.compile(r"^\s*(?:--- FAIL|FAIL\b|panic:|ERROR\b|Error:|error:|✗|✖|×)")
CONTEXT = 5        # lines kept after each failure line
FAILURE_CAP = 100  # failure lines shown before the tail
FAILURE_TAIL = 30
SUCCESS_TAIL = 10


def slug(words: list[str]) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", "-".join(words).lower()).strip("-")
    return text[:48].rstrip("-") or "cmd"


def checkout_root(directory: Path) -> Path:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=directory,
                            capture_output=True, text=True)
    if result.returncode:
        return directory.resolve()
    return Path(result.stdout.strip()).resolve()


def default_log(root: Path, label: str) -> Path:
    """Create and return a fresh log file; never reuse an earlier run's file."""
    logs = root / ".harness" / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for n in count(1):
        path = logs / (f"{stamp}-{label}.log" if n == 1 else f"{stamp}-{label}-{n}.log")
        try:
            path.open("x").close()
            return path
        except FileExistsError:
            continue


def execute(cmd: list[str], log: Path, cwd: Path | None = None) -> int:
    """Run cmd with stdout and stderr in the log; return its shell-style exit code."""
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("wb") as out:
        try:
            code = subprocess.run(cmd, cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                  stderr=subprocess.STDOUT).returncode
        except FileNotFoundError as exc:
            out.write(f"quiet: not found: {exc.filename or cmd[0]}\n".encode())
            return 127
        except OSError as exc:
            out.write(f"quiet: cannot execute {cmd[0]}: {exc.strerror or exc}\n".encode())
            return 126
    return 128 - code if code < 0 else code


def is_failure(line: str) -> bool:
    return bool(FAILURE.match(line)) or " FAIL " in line or "FAILED" in line


def failure_lines(lines: list[str]) -> list[str]:
    """Failure lines with their context, overlapping windows merged, capped."""
    shown, end = [], 0
    for i, line in enumerate(lines):
        if not is_failure(line):
            continue
        for j in range(max(i, end), min(i + CONTEXT + 1, len(lines))):
            if len(shown) == FAILURE_CAP:
                shown.append(f"-- failures truncated at {FAILURE_CAP} lines; the log has all --")
                return shown
            shown.append(lines[j])
        end = max(end, i + CONTEXT + 1)
    return shown


def digest(code: int, log: Path, with_log: bool = True) -> str:
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    out = [f"exit={code}"]
    if code == 0:
        out += lines[-SUCCESS_TAIL:]
    else:
        # The tail is printed anyway, so failures are looked for only before it.
        start = max(0, len(lines) - FAILURE_TAIL)
        failures = failure_lines(lines[:start])
        if failures:
            out += ["-- failures --", *failures]
        out += [f"-- last {FAILURE_TAIL} lines --", *lines[start:]]
    if with_log:
        out.append(f"log: {log}")
    return "\n".join(out)


def main() -> int:
    sys.stdout.reconfigure(errors="replace")
    argv = sys.argv[1:]
    usage = "usage: quiet.py [--log FILE] -- CMD ARGS..."
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        print(usage, file=sys.stderr)
        return 2
    split = argv.index("--")
    parser = argparse.ArgumentParser(usage=usage, description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log", help="log file (default: under <checkout>/.harness/logs/)")
    args = parser.parse_args(argv[:split])
    cmd = argv[split + 1:]
    try:
        log = (Path(args.log).absolute() if args.log else
               default_log(checkout_root(Path.cwd()), slug([Path(cmd[0]).name, *cmd[1:]])))
        code = execute(cmd, log)
        print(digest(code, log))
    except OSError as exc:
        print(f"quiet: {exc}", file=sys.stderr)
        return 2
    return code


if __name__ == "__main__":
    sys.exit(main())
