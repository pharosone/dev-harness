#!/usr/bin/env python3
"""Keep task evidence in the primary checkout and freeze tests by Git blob hash.

File arguments are relative to the current checkout root, even from a subdirectory.
Run directories are shared across linked worktrees; verification always hashes the
files in the current checkout, so an implementation cannot inherit another lane's
successful verification. `freeze` also requires a red log (`--red-log FILE`, resolved
against the current directory) that shows a failing test; its path and sha256 are kept
in the manifest as evidence and are not checked by `verify`.

`event TASK TYPE --data JSON [--id ID]` appends one line
`{"v": 1, "id", "at", "type", ...data}` to the run's `events.jsonl` and prints the id
(`id` defaults to a new uuid4 hex, `at` is UTC to the second). It is the only writer of
that log. The append holds an exclusive `fcntl.flock` on the events file; an id already
present is skipped (exit 0, prints `duplicate`), so a repeated hook delivery is harmless.
Windows has no `fcntl`: there the append is unlocked, and concurrent writers may race on
the duplicate check. `--data` must be a JSON object without the envelope keys and TYPE
must be dot-separated lower-case words (`dispatch.start`); either error exits 2.
`read_events(run)` returns the events in file order and the number of lines that are not
JSON objects (a write cut short); a later append starts on a fresh line after such a line.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

try:
    import fcntl
except ImportError:  # Windows: appends to the events log are not locked.
    fcntl = None


class StateError(Exception):
    """Invalid or unavailable durable task evidence."""


class EventDataError(StateError):
    """An event whose type or data is malformed (exit 2)."""


EVENT_TYPE = re.compile(r"[a-z]+(?:\.[a-z]+)*")
EVENT_ENVELOPE = ("v", "id", "at", "type")


# Output of a failing Go, Python, Node or TypeScript run; a passing log matches none of them.
RED_EVIDENCE = re.compile(
    # Error types count only where a failure prints them (line start, or "raise X"),
    # never inside a passing test's name such as test_raises_AssertionError ... ok.
    r"--- FAIL|^FAIL|FAILED|undefined:|cannot find|^\s*(?:raise\s+\w*Error\b|[A-Za-z_.]*Error:)"
    r"|: error\b|^error\b|Traceback"
    r"|✗|×|Tests? .*failed",
    re.MULTILINE,
)


def git(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True)
    if result.returncode:
        raise StateError(os.fsdecode(result.stderr).strip() or "Git command failed")
    return os.fsdecode(result.stdout).removesuffix("\n")


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", value):
        raise StateError(f"Invalid task or unit identifier: {value!r}")
    return value


def checkouts() -> tuple[Path, Path]:
    # The first porcelain record is the primary worktree, including when it is
    # bare. NUL framing preserves paths containing spaces, tabs and newlines.
    record = git("worktree", "list", "--porcelain", "-z").split("\0\0", 1)[0]
    fields = record.split("\0")
    if "bare" in fields:
        raise StateError(
            "The primary repository is bare and has no checkout for durable state. "
            "Use a non-bare primary clone with linked worktrees."
        )
    if not fields or not fields[0].startswith("worktree "):
        raise StateError("Could not locate the primary Git checkout")
    primary = Path(fields[0][len("worktree "):]).resolve()
    current = Path(git("rev-parse", "--show-toplevel")).resolve()
    return primary, current


def contained(path: Path, root: Path) -> Path:
    if not path.resolve().is_relative_to(root):
        raise StateError(f"Path escapes checkout: {path}")
    return path


def evidence_path(path: Path, primary: Path) -> Path:
    contained(path, primary)
    # Evidence must not alias a different task or follow symlinked manifests.
    for part in (path, *path.parents):
        if part == primary:
            break
        if part.is_symlink():
            raise StateError(f"Symlinked evidence path is not allowed: {part}")
    return path


def locate(primary: Path, task: str) -> Path:
    identifier(task)
    new = evidence_path(primary / ".harness" / "tasks" / task, primary)
    legacy = evidence_path(primary / ".claude" / "tasks" / task, primary)
    if new.exists() and legacy.exists():
        raise StateError(
            f"Both task directories exist: {new} and {legacy}. "
            "Reconcile their evidence explicitly before continuing."
        )
    selected = legacy if legacy.exists() else new
    if selected.exists() and not selected.is_dir():
        raise StateError(f"Task state is not a directory: {selected}")
    return selected


def checkout_file(current: Path, name: str) -> Path:
    relative = Path(name)
    if not name or relative.is_absolute() or ".." in relative.parts:
        raise StateError(f"Expected a checkout-relative file path: {name!r}")
    path = contained(current / relative, current)
    if not path.is_file():
        raise StateError(f"Missing or invalid file: {name}")
    return path


def initialize(run: Path, primary: Path, current: Path, spec: str | None) -> None:
    ledger = evidence_path(run / "run.md", primary)
    if ledger.exists():
        if not ledger.is_file():
            raise StateError(f"Run ledger is not a file: {ledger}")
        return
    spec_line = (checkout_file(current, spec).relative_to(current).as_posix() if spec
                 else "none yet (investigation or discovery)")
    base = git("rev-parse", "HEAD", cwd=current)
    run.mkdir(parents=True, exist_ok=True)
    try:
        with ledger.open("x", encoding="utf-8") as stream:
            stream.write(
                f"# Task: {run.name}\n\n"
                f"- Spec: {spec_line}\n"
                f"- Base HEAD: {base}\n"
                f"- Worktree: {current}\n"
                "- Runtime: pending\n"
                "- Model/profile: pending\n"
                "- Commits: pending\n\n"
                "## Progress and evidence\n\n"
                "Record unit status, commands, results, report paths and review status here.\n"
            )
    except FileExistsError:
        # A concurrent initialization must never replace the first ledger.
        pass


def manifest_path(run: Path, primary: Path, unit: str) -> Path:
    identifier(unit)
    ledger = evidence_path(run / "run.md", primary)
    if not ledger.is_file():
        raise StateError(f"Task is not initialized; run init first: {run.name}")
    return evidence_path(run / "units" / f"{unit}-tests.json", primary)


def fingerprint(current: Path, name: str) -> str:
    path = checkout_file(current, name)
    if path.stat().st_size == 0:
        raise StateError(f"Test file is empty: {name}")
    return git("hash-object", "--no-filters", "--", str(path), cwd=current)


def fingerprints(current: Path, names: list[str]) -> dict[str, str]:
    files = {}
    for name in names:
        normalized = checkout_file(current, name).relative_to(current).as_posix()
        if normalized in files:
            raise StateError(f"Duplicate test file: {normalized}")
        files[normalized] = fingerprint(current, normalized)
    if not files:
        raise StateError("At least one test file must be frozen")
    return files


def red_evidence(name: str) -> dict[str, str]:
    path = Path(name).resolve()
    if not name or not path.is_file():
        raise StateError(f"Missing red log: {name}")
    data = path.read_bytes()
    if not data.strip():
        raise StateError(f"Red log is empty: {name}")
    if not RED_EVIDENCE.search(data.decode("utf-8", errors="replace")):
        raise StateError(f"red log shows no failing test: {name}")
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest()}


def write_manifest(manifest: Path, files: dict[str, str], red_log: dict[str, str] | None = None) -> None:
    data = {"version": 1, "files": files}
    if red_log is not None:
        data["red_log"] = red_log
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def freeze(manifest: Path, current: Path, names: list[str], red_log: str) -> None:
    if manifest.exists():
        raise StateError(f"Tests are already frozen; refusing to overwrite: {manifest}")
    evidence = red_evidence(red_log)
    write_manifest(manifest, fingerprints(current, names), evidence)


def import_legacy(manifest: Path, legacy: Path, current: Path, names: list[str]) -> None:
    """Recover paths only when they match every original hash in its saved order."""
    if manifest.exists():
        raise StateError(f"Tests are already frozen; refusing to overwrite: {manifest}")
    hashes = legacy.read_text(encoding="utf-8").splitlines()
    if not hashes or any(not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", h) for h in hashes):
        raise StateError(f"Malformed or empty legacy test hashes: {legacy}")
    if len(hashes) != len(names):
        raise StateError(
            f"Legacy hash count ({len(hashes)}) differs from supplied files ({len(names)}); "
            "supply every original test file in its original order."
        )
    files = fingerprints(current, names)
    for (name, actual), expected in zip(files.items(), hashes, strict=True):
        if actual != expected:
            raise StateError(
                f"Legacy fingerprint mismatch: {name}. Restore the original tests "
                "and supply the original file order; do not regenerate the legacy hashes."
            )
    # Preserve the .sha baseline. JSON creation is exclusive, exactly as freeze.
    write_manifest(manifest, files)


def unique_keys(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise StateError(f"Duplicate manifest key: {key}")
        result[key] = value
    return result


def verify(manifest: Path, current: Path) -> None:
    data = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise StateError(f"Malformed or unsupported test manifest: {manifest}")
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise StateError(f"Test manifest has no files: {manifest}")
    failures = []
    for name, expected in files.items():
        if not isinstance(expected, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected):
            raise StateError(f"Invalid test fingerprint: {name}")
        try:
            if fingerprint(current, name) != expected:
                failures.append(f"Test file changed: {name}")
        except (StateError, OSError) as exc:
            failures.append(str(exc))
    if failures:
        raise StateError("\n".join(failures))


def event_record(kind: str, data: str, event_id: str | None) -> dict:
    if not EVENT_TYPE.fullmatch(kind):
        raise EventDataError(f"Invalid event type: {kind!r}")
    try:
        fields = json.loads(data)
    except ValueError as exc:
        raise EventDataError(f"--data is not JSON: {exc}") from None
    if not isinstance(fields, dict):
        raise EventDataError("--data must be a JSON object")
    reserved = [key for key in EVENT_ENVELOPE if key in fields]
    if reserved:
        raise EventDataError(f"--data must not set envelope keys: {', '.join(reserved)}")
    at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"v": 1, "id": event_id or uuid.uuid4().hex, "at": at, "type": kind, **fields}


def parse_events(text: str) -> tuple[list[dict], int]:
    events, unreadable = [], 0
    for line in text.split("\n"):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except ValueError:
            value = None
        if isinstance(value, dict):
            events.append(value)
        else:
            unreadable += 1
    return events, unreadable


def read_events(run: Path) -> tuple[list[dict], int]:
    """Events of a run in file order, and the count of lines that are not JSON objects."""
    path = Path(run) / "events.jsonl"
    if not path.is_file():
        return [], 0
    return parse_events(path.read_bytes().decode("utf-8", errors="replace"))


def append_event(path: Path, record: dict) -> bool:
    """Append under an exclusive lock; False when the id is already in the log."""
    line = json.dumps(record) + "\n"
    with path.open("a+b") as stream:
        if fcntl is not None:
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            stream.seek(0)
            existing = stream.read()
            events, _ = parse_events(existing.decode("utf-8", errors="replace"))
            if any(event.get("id") == record["id"] for event in events):
                return False
            # A write cut short leaves no newline; never glue the new event onto it.
            prefix = b"\n" if existing and not existing.endswith(b"\n") else b""
            stream.write(prefix + line.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            if fcntl is not None:
                fcntl.flock(stream, fcntl.LOCK_UN)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("locate", "init", "freeze", "verify", "import-legacy", "event"):
        sub = commands.add_parser(command)
        sub.add_argument("task")
        if command == "event":
            sub.add_argument("type", help="event type, e.g. dispatch.start")
            sub.add_argument("--data", required=True, help="the event's fields, a JSON object")
            sub.add_argument("--id", help="idempotency key; defaults to a new uuid4 hex")
        if command == "init":
            sub.add_argument("--spec", help="the approved spec; omit it for an investigation or "
                             "a discovery that has no spec yet")
        if command in ("freeze", "verify", "import-legacy"):
            sub.add_argument("unit")
        if command == "freeze":
            sub.add_argument("--red-log", required=True, help="log of the failing test run")
        if command in ("freeze", "import-legacy"):
            sub.add_argument("files", nargs="+")
    args = parser.parse_args()
    try:
        primary, current = checkouts()
        run = locate(primary, args.task)
        if args.command == "event":
            record = event_record(args.type, args.data, args.id)
            ledger = evidence_path(run / "run.md", primary)
            if not ledger.is_file():
                raise StateError(f"Task is not initialized; run init first: {run.name}")
            appended = append_event(evidence_path(run / "events.jsonl", primary), record)
            print(record["id"] if appended else "duplicate")
            return 0
        if args.command == "init":
            initialize(run, primary, current, args.spec)
        elif args.command in ("freeze", "verify", "import-legacy"):
            manifest = manifest_path(run, primary, args.unit)
            legacy = evidence_path(manifest.with_suffix(".sha"), primary)
            if legacy.exists() and (
                args.command == "freeze" or (args.command == "verify" and not manifest.exists())
            ):
                raise StateError(
                    f"Legacy test evidence exists: {legacy}. Run "
                    f"task_state.py import-legacy {args.task} {args.unit} FILE... "
                    "with every original test file in its saved hash order."
                )
            if args.command == "freeze":
                freeze(manifest, current, args.files, args.red_log)
            elif args.command == "import-legacy":
                import_legacy(manifest, legacy, current, args.files)
            else:
                verify(manifest, current)
            print(manifest)
            return 0
        print(run)
        return 0
    except (StateError, OSError, ValueError) as exc:
        print(f"task-state: {exc}", file=sys.stderr)
        return 2 if isinstance(exc, EventDataError) else 1


if __name__ == "__main__":
    sys.exit(main())
