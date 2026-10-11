#!/usr/bin/env python3
"""Keep task evidence in the primary checkout and freeze tests by Git blob hash.

File arguments are relative to the current checkout root, even from a subdirectory.
Run directories are shared across linked worktrees; verification always hashes the
files in the current checkout, so an implementation cannot inherit another lane's
successful verification. `freeze` also requires a red log (`--red-log FILE`, resolved
against the current directory) that shows a failing test; its path and sha256 are kept
in the manifest as evidence and are not checked by `verify`.

Repeating `freeze` preserves and archives the old manifest when Python changes are
AST-equivalent (including removal of unused imports), or setup-only with byte-identical
assertions and hash-verified setup-error evidence before the first recorded green run.
Setup repairs require an innermost traceback in the exact frozen setup function.
Only pure, unobserved local bindings can change automatically. Fixtures, returned
values, shared attributes/globals, calls and construction helpers remain frozen:
their effect on assertion operands cannot be proved safe by this static check.
Missing non-collecting stdlib imports may be added only for such local setup bindings.
`green TASK UNIT --green-log FILE` verifies the frozen files and records that first pass;
call it immediately after the first successful test command. `verify` alone is not a pass.
At the initial freeze, `--brief FILE` pins exact brief lines of the form
`task-state append-only: tests/test_tools.py:EXPECTED` (qualified local names work too,
e.g. `TestTools.test_tools.expected`). Only trailing literal elements may be added to
the named list/tuple/set assignment; all other AST nodes must remain unchanged.
Every automatic refreeze stores an exclusive byte-for-byte archive and an amendment
record with its hash and reason, also printed on stderr. Other edits need coordinator
approval; there is no CLI flag that bypasses this check. Legacy manifests lacking source
snapshots cannot be automatically amended. Mutations use a separate flock and atomic
replacement; automatic refreezing is unavailable without fcntl.

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
import ast
import base64
from collections import Counter
from contextlib import contextmanager
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
    r"--- FAIL|^FAIL|FAILED|^ERROR(?:\s|:)|undefined:|cannot find"
    r"|^\s*(?:raise\s+\w*Error\b|(?:E[ \t]+)?[A-Za-z0-9_.]*(?:Error|UndefinedColumn):)"
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


def load_manifest(manifest: Path) -> dict:
    data = json.loads(manifest.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    if not isinstance(data, dict) or data.get("version") != 1:
        raise StateError(f"Malformed or unsupported test manifest: {manifest}")
    files = data.get("files")
    if not isinstance(files, dict) or not files:
        raise StateError(f"Test manifest has no files: {manifest}")
    for name, expected in files.items():
        if not isinstance(expected, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected):
            raise StateError(f"Invalid test fingerprint: {name}")
    if "sources" in data and (not isinstance(data["sources"], dict)
                              or any(not isinstance(value, str) for value in data["sources"].values())):
        raise StateError("Malformed saved source snapshots")
    if "amendments" in data and (not isinstance(data["amendments"], list)
                                 or any(not isinstance(record, dict) for record in data["amendments"])):
        raise StateError("Malformed amendment history")
    if "append_only" in data:
        policy = data["append_only"]
        if not isinstance(policy, dict) or not isinstance(policy.get("targets"), dict):
            raise StateError("Malformed append-only authorization")
        if (not isinstance(policy.get("path"), str)
                or not isinstance(policy.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", policy["sha256"])):
            raise StateError("Malformed append-only brief evidence")
        for name, targets in policy["targets"].items():
            if (name not in files or not isinstance(targets, list) or not targets
                    or any(not isinstance(target, str) for target in targets)):
                raise StateError("Malformed append-only targets")
    return data


def source_tree(source: bytes) -> ast.Module:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise StateError(f"Automatic amendments require valid Python: {exc}") from None


def tree_key(tree: ast.AST) -> str:
    return ast.dump(tree, include_attributes=False)


def import_keys(tree: ast.AST) -> Counter:
    return Counter((type(node).__name__, getattr(node, "module", None),
                    getattr(node, "level", None), alias.name, alias.asname)
                   for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
                   for alias in node.names)


NON_COLLECTION_IMPORTS = {"os", "sys", "pathlib", "math", "json", "re", "datetime", "time",
                          "collections", "itertools", "functools", "typing", "tempfile",
                          "hashlib", "base64", "io", "copy", "shutil", "subprocess", "socket"}


def without_unused_imports(tree: ast.Module) -> ast.Module:
    # Deliberately overestimate usage, including names in other scopes and strings.
    # Reflection and star imports make absence of a Name insufficient evidence.
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    strings = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and isinstance(node.value, str)]
    dynamic = bool(names & {"globals", "locals", "eval", "exec", "vars", "__all__"})
    dynamic |= any(isinstance(node, ast.ImportFrom) and any(a.name == "*" for a in node.names)
                   for node in ast.walk(tree))
    # Imported objects can be collected through globals by unittest/pytest even
    # without a Name use in this file. Restrict from-import cleanup to these
    # known non-collection stdlib modules; unknown bindings need approval.
    safe_from = NON_COLLECTION_IMPORTS

    class Strip(ast.NodeTransformer):
        def visit_Import(self, node):
            if dynamic:
                return node
            node.names = [alias for alias in node.names
                          if alias.name.split(".")[0] not in safe_from
                          or used(alias.asname or alias.name.split(".")[0])]
            return node if node.names else None

        def visit_ImportFrom(self, node):
            if dynamic or node.level or node.module not in safe_from:
                return node
            node.names = [alias for alias in node.names if used(alias.asname or alias.name)]
            return node if node.names else None

    def used(name):
        return (name in names or name.startswith(("test_", "fixture_"))
                or any(re.search(r"\b" + re.escape(name) + r"\b", value) for value in strings))

    return Strip().visit(tree)


def ast_equivalent(old: bytes, new: bytes) -> bool:
    before, after = source_tree(old), source_tree(new)
    # Removing an unused import is allowed; introducing an import is not.
    if import_keys(after) - import_keys(before):
        return False
    return tree_key(without_unused_imports(before)) == tree_key(without_unused_imports(after))


def literal_assignments(tree: ast.AST) -> dict[str, list[ast.AST]]:
    found = {}

    def walk(node, scope=()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            scope = (*scope, node.name)
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
        elif isinstance(node, ast.AnnAssign):
            target = node.target
        if isinstance(target, ast.Name):
            found.setdefault(".".join((*scope, target.id)), []).append(node)
        for child in ast.iter_child_nodes(node):
            walk(child, scope)

    walk(tree)
    return found


def named_literal(tree: ast.AST, name: str) -> ast.AST:
    assignments = literal_assignments(tree).get(name, [])
    if len(assignments) != 1:
        raise StateError(f"Append-only target must name one assignment: {name}")
    assignment = assignments[0]
    if not isinstance(assignment.value, (ast.List, ast.Tuple, ast.Set)):
        raise StateError(f"Append-only target is not a list/tuple/set literal: {name}")
    try:
        ast.literal_eval(assignment.value)
    except (ValueError, TypeError):
        raise StateError(f"Append-only target contains nonliteral elements: {name}") from None
    return assignment


def brief_policy(current: Path, name: str, sources: dict[str, bytes]) -> dict:
    path = checkout_file(current, name)
    raw = path.read_bytes()
    targets = {}
    for line in raw.decode("utf-8").splitlines():
        if not line.startswith("task-state append-only:"):
            continue
        declaration = line.removeprefix("task-state append-only:").strip()
        file, separator, target = declaration.rpartition(":")
        if not separator or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", target):
            raise StateError(f"Malformed append-only declaration: {line}")
        file = checkout_file(current, file).relative_to(current).as_posix()
        if file not in sources:
            raise StateError(f"Append-only declaration is outside frozen files: {file}")
        named_literal(source_tree(sources[file]), target)
        if target in targets.setdefault(file, []):
            raise StateError(f"Duplicate append-only declaration: {file}:{target}")
        targets[file].append(target)
    if not targets:
        raise StateError("Brief has no task-state append-only declarations")
    return {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(), "targets": targets}


def append_only(old: bytes, new: bytes, targets: list[str]) -> bool:
    before, after = source_tree(old), source_tree(new)
    changed = False
    for name in targets:
        previous, revised = named_literal(before, name), named_literal(after, name)
        left, right = previous.value, revised.value
        if type(left) is not type(right) or len(right.elts) < len(left.elts):
            return False
        if [tree_key(e) for e in left.elts] != [tree_key(e) for e in right.elts[:len(left.elts)]]:
            return False
        changed |= len(right.elts) > len(left.elts)
        revised.value = left
    return changed and tree_key(before) == tree_key(after)


def assertion_bytes(source: bytes, tree: ast.Module) -> list[bytes]:
    lines = source.splitlines(keepends=True)
    protected = []

    def assertion_call(node):
        if not isinstance(node, ast.Call):
            return False
        function = node.func
        return ((isinstance(function, ast.Attribute)
                 and (function.attr.startswith("assert") or function.attr in {"raises", "fail"}))
                or (isinstance(function, ast.Name)
                    and (function.id.startswith("assert") or function.id in {"raises", "fail"})))

    for node in ast.walk(tree):
        is_assertion = isinstance(node, ast.Assert) or assertion_call(node)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            is_assertion |= any(assertion_call(item.context_expr) for item in node.items)
        if is_assertion:
            protected.append((node.lineno, b"".join(lines[node.lineno - 1:node.end_lineno])))
    return [value for _, value in sorted(protected)]


SETUP_NAMES = {"setUp", "setUpClass", "setup", "setup_method", "setup_function", "setup_module"}


def is_setup_function(node: ast.AST) -> bool:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return False
    decorators = [d.func if isinstance(d, ast.Call) else d for d in node.decorator_list]
    return node.name in SETUP_NAMES or any(
        isinstance(d, ast.Attribute) and isinstance(d.value, ast.Name)
        and d.value.id == "pytest" and d.attr == "fixture" for d in decorators)


def construction_helpers(tree: ast.Module) -> dict[str, ast.AST]:
    candidates = [node for node in tree.body
                  if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and re.fullmatch(r"(?:Fake|Stub)\w*", node.name)]
    counts = Counter(node.name for node in candidates)
    return {node.name: node for node in candidates if counts[node.name] == 1}


TEST_CONTROLS = {"skip", "skipTest", "SkipTest", "skipif", "skipIf", "skipUnless",
                 "xfail", "importorskip"}


def has_test_controls(node: ast.AST, aliases: set[str]) -> bool:
    """Do not mask setup bodies that can skip/xfail collected contracts.

    Protect their entire body, including guards around existing control calls.
    """
    controls = TEST_CONTROLS | aliases
    return any((isinstance(child, ast.Name) and child.id in controls)
               or (isinstance(child, ast.Attribute) and child.attr in TEST_CONTROLS)
               or (isinstance(child, ast.Constant) and isinstance(child.value, str)
                   and child.value in TEST_CONTROLS)
               for child in ast.walk(node))


def pure_local_setup(tree: ast.Module, node: ast.AST) -> bool:
    """Prove a deliberately small subset cannot supply or mutate test operands.

    No Python call/property/operator is assumed pure. Reject fixtures even when
    their return is implicit (autouse fixtures may mutate shared state). Leave
    opaque construction helpers frozen rather than guessing their dependencies.
    """
    if not is_setup_function(node) or node.name not in SETUP_NAMES or node.decorator_list:
        return False
    local = set()
    allowed = (ast.Constant, ast.Name, ast.List, ast.Tuple, ast.Set, ast.Dict,
               ast.Load, ast.Store)
    for statement in node.body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant):
            continue  # docstring / inert literal
        if not isinstance(statement, ast.Assign):
            return False
        if not all(isinstance(target, ast.Name) for target in statement.targets):
            return False
        if any(not isinstance(child, allowed) for child in ast.walk(statement.value)):
            return False
        if (not isinstance(statement.value, ast.Name)
                and any(isinstance(child, ast.Name) for child in ast.walk(statement.value))):
            return False  # containers must not hash user-defined objects
        local.update(target.id for target in statement.targets)
    # Conservative across scopes: any same-named use outside this setup keeps
    # the whole body frozen, including indirect assertion aliases in test code.
    inside = {id(child) for child in ast.walk(node)}
    return not any(isinstance(child, ast.Name) and child.id in local
                   and id(child) not in inside for child in ast.walk(tree))


def without_setup(tree: ast.Module, eligible: set[str]) -> ast.Module:
    aliases = {alias.asname or alias.name for child in ast.walk(tree)
               if isinstance(child, ast.ImportFrom) for alias in child.names
               if alias.name in TEST_CONTROLS}

    class Strip(ast.NodeTransformer):
        def visit_Module(self, node):
            # Imports are setup; statements elsewhere remain part of the contract.
            body = []
            for child in node.body:
                if isinstance(child, (ast.Import, ast.ImportFrom)):
                    continue
                body.append(self.visit(child))
            node.body = body
            return node

        def visit_FunctionDef(self, node):
            if (node.name in eligible and pure_local_setup(tree, node)
                    and not has_test_controls(node, aliases)):
                node.body = [ast.Pass()]
                return node
            return self.generic_visit(node)

        visit_AsyncFunctionDef = visit_FunctionDef

    return Strip().visit(tree)


def setup_imports_safe(before: ast.Module, after: ast.Module, eligible: set[str]) -> bool:
    """Setup may add missing bindings, but must retain possibly collected imports.

    Unknown existing imports stay exact. A new import may not overwrite an
    existing definition/binding and hide a collected contract from the loader.
    """
    def imports(tree, protected=False):
        keys = Counter()
        for node in tree.body:
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            for alias in node.names:
                unsafe = ((alias.name.split('.')[0] not in NON_COLLECTION_IMPORTS)
                          if isinstance(node, ast.Import) else
                          (node.level or node.module not in NON_COLLECTION_IMPORTS))
                if not protected or unsafe:
                    keys[(type(node).__name__, getattr(node, 'module', None),
                          getattr(node, 'level', None), alias.name, alias.asname)] += 1
        return keys

    old, new = imports(before), imports(after)
    if old - new:
        return False
    safe_nodes = [node for node in ast.walk(before)
                  if is_setup_function(node) and node.name in eligible
                  and pure_local_setup(before, node)]
    safe_reads = {id(child) for node in safe_nodes for child in ast.walk(node)}
    for kind, module, level, name, alias in new - old:
        binding = alias or (name.split('.')[0] if kind == 'Import' else name)
        if (level or (name not in NON_COLLECTION_IMPORTS if kind == 'Import'
                      else module not in NON_COLLECTION_IMPORTS)):
            return False
        reads = [child for child in ast.walk(before)
                 if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
                 and child.id == binding]
        if not reads or any(id(child) not in safe_reads for child in reads):
            return False
    bound = {node.name for node in ast.walk(before)
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
    bound.update(node.id for node in ast.walk(before)
                 if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store))
    bound.update(alias or (name.split('.')[0] if kind == 'Import' else name)
                 for kind, _, _, name, alias in old)
    return not any(name == '*' or (alias or (name.split('.')[0] if kind == 'Import' else name)) in bound
                   for kind, _, _, name, alias in new - old)


def setup_only(old: bytes, new: bytes, eligible: set[str]) -> bool:
    before, after = source_tree(old), source_tree(new)
    # An assertion must not be moved into/out of a fixture or rewritten there.
    return (setup_imports_safe(before, after, eligible)
            and assertion_bytes(old, before) == assertion_bytes(new, after)
            and tree_key(without_setup(before, eligible)) == tree_key(without_setup(after, eligible)))


def verified_setup_error(evidence: dict, source: bytes, filename: str) -> set[str] | None:
    """Return uniquely traceback-implicated setup names, or None for unsafe evidence."""
    if not isinstance(evidence, dict) or not isinstance(evidence.get("path"), str):
        return None
    path = Path(evidence["path"])
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != evidence.get("sha256"):
        return None
    text = raw.decode("utf-8", errors="replace")
    if re.search(r"\bAssertionError\b|^FAIL:|^FAILURES\b|^--- FAIL"
                 r"|^FAILED\s+(?!\(errors=)|\bfailures=[1-9]\d*"
                 r"|\b[1-9]\d* failed\b|^\s*(?:E\s+)?(?:assert\b|self\.assert)",
                 text, re.MULTILINE):
        return None
    errors = list(re.finditer(r"^[ \t]*(?:E[ \t]+)?([A-Za-z0-9_.]*(?:Error|UndefinedColumn)):[ \t]*(.*)",
                              text, re.MULTILINE))
    if not errors:
        return None
    tree = source_tree(source)
    setup_nodes = [node for node in ast.walk(tree) if is_setup_function(node)]
    counts = Counter(node.name for node in setup_nodes)
    eligible = set()
    previous_end = 0
    for error in errors:
        kind = error.group(1).rsplit(".", 1)[-1]
        prefix = text[previous_end:error.start()]
        previous_end = error.end()
        locations = list(re.finditer(
            r'File "([^"\n]+)", line (\d+), in ([^\s\n]+)'
            r'|^([^\n]+\.py):(\d+): in ([^\s\n]+)', prefix, re.MULTILINE))
        if not locations or kind not in {"ImportError", "ModuleNotFoundError", "NameError",
                                         "UndefinedColumn", "AttributeError", "TypeError"}:
            return None
        location = locations[-1]
        file, line, function = location.groups()[:3] if location.group(1) else location.groups()[3:]
        if file != filename and not file.endswith("/" + filename):
            return None
        implicated = [node for node in setup_nodes if counts[node.name] == 1
                      and node.name == function and node.lineno <= int(line) <= node.end_lineno]
        if len(implicated) != 1:
            return None
        eligible.add(implicated[0].name)
    return eligible or None


def saved_sources(data: dict) -> dict[str, bytes]:
    saved = data.get("sources")
    if not isinstance(saved, dict) or set(saved) != set(data["files"]):
        raise StateError("No saved source baseline; coordinator approval is required")
    sources = {}
    for name, expected in data["files"].items():
        raw = base64.b64decode(saved[name], validate=True)
        algorithm = "sha1" if len(expected) == 40 else "sha256"
        actual = hashlib.new(algorithm, f"blob {len(raw)}\0".encode() + raw).hexdigest()
        if actual != expected:
            raise StateError(f"Saved baseline does not match frozen hash: {name}")
        sources[name] = raw
    return sources


@contextmanager
def manifest_lock(manifest: Path):
    manifest.parent.mkdir(parents=True, exist_ok=True)
    lock = manifest.with_suffix(".lock")
    if lock.is_symlink():
        raise StateError(f"Symlinked evidence path is not allowed: {lock}")
    with lock.open("a+b") as stream:
        if fcntl is not None:
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(stream, fcntl.LOCK_UN)


def replace_manifest(manifest: Path, data: dict) -> None:
    temporary = manifest.with_name(f".{manifest.name}-{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest)
    finally:
        temporary.unlink(missing_ok=True)


def freeze(manifest: Path, current: Path, names: list[str], red_log: str,
           brief: str | None = None) -> None:
    with manifest_lock(manifest):
        evidence = red_evidence(red_log)
        files = fingerprints(current, names)
        sources = {name: checkout_file(current, name).read_bytes() for name in files}
        # Hash the same bytes that will become the durable source baseline.
        encoded = {name: base64.b64encode(raw).decode("ascii") for name, raw in sources.items()}
        fresh = {"version": 1, "files": files, "sources": encoded,
                 "red_log": evidence, "amendment_version": 1}
        saved_sources(fresh)
        if not manifest.exists():
            if brief is not None:
                fresh["append_only"] = brief_policy(current, brief, sources)
            with manifest.open("x", encoding="utf-8") as stream:
                json.dump(fresh, stream, indent=2, sort_keys=True)
                stream.write("\n")
            return
        if fcntl is None:
            raise StateError("Automatic amendments require file locking; coordinator approval is required")
        if brief is not None:
            raise StateError("Append-only authorization must be pinned at the first freeze")
        data = load_manifest(manifest)
        if set(files) != set(data["files"]):
            raise StateError("Frozen file set changed; coordinator approval is required")
        baseline = saved_sources(data)
        changed = [name for name in files if files[name] != data["files"][name]]
        if not changed:
            raise StateError(f"Tests are already frozen; refusing to overwrite: {manifest}")
        reasons = set()
        for name in changed:
            if not name.endswith(".py"):
                raise StateError(f"Automatic amendments require Python tests: {name}")
            old, new = baseline[name], sources[name]
            if ast_equivalent(old, new):
                reasons.add("ast-equivalent")
                continue
            setup_helpers = (verified_setup_error(data.get("red_log"), old, name)
                             if data.get("amendment_version") == 1 and "first_green" not in data
                             else None)
            if setup_helpers is not None and setup_only(old, new, setup_helpers):
                reasons.add("setup-only")
            elif (data.get("append_only", {}).get("targets", {}).get(name)
                  and append_only(old, new, data["append_only"]["targets"][name])):
                reasons.add("append-only")
            else:
                raise StateError(f"Test change needs coordinator approval: {name}")
        reason = "+".join(sorted(reasons))
        original = manifest.read_bytes()
        archive = manifest.with_name(f"{manifest.stem}-archive-{uuid.uuid4().hex}.json")
        with archive.open("xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        data.update(files=files, sources=encoded, red_log=evidence)
        data.setdefault("amendments", []).append({
            "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "reason": reason, "files": changed, "archive": archive.name,
            "archive_sha256": hashlib.sha256(original).hexdigest(),
        })
        replace_manifest(manifest, data)
        print(f"task-state: auto-refreeze {reason}; archived {archive}", file=sys.stderr)


def record_green(manifest: Path, current: Path, green_log: str) -> None:
    with manifest_lock(manifest):
        verify(manifest, current)
        path = Path(green_log).resolve()
        raw = path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        passing = re.search(r"\b[1-9]\d* passed\b|^ok\s+\S+|^PASS\s*$", text, re.MULTILINE)
        failed = RED_EVIDENCE.search(text) or re.search(r"\b[1-9]\d* (?:failed|errors?)\b", text)
        # unittest's bare OK also covers empty and entirely skipped suites.
        summary = re.search(r"\bRan (\d+) tests?\b", text)
        skipped = re.search(r"\bskipped=(\d+)\b", text)
        expected = re.search(r"\bexpected failures=(\d+)\b", text)
        if summary:
            executed = int(summary[1]) - (int(skipped[1]) if skipped else 0) \
                - (int(expected[1]) if expected else 0)
            passing = executed > 0 and re.search(r"^OK(?:\s*\(.*\))?\s*$", text, re.MULTILINE)
        if not passing or failed:
            raise StateError("Green log must show a passing test run with no failures")
        data = load_manifest(manifest)
        if "first_green" not in data:
            data["first_green"] = {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                                   "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
            replace_manifest(manifest, data)


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
    files = load_manifest(manifest)["files"]
    failures = []
    for name, expected in files.items():
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
    for command in ("locate", "init", "freeze", "verify", "green", "import-legacy", "event"):
        sub = commands.add_parser(command)
        sub.add_argument("task")
        if command == "event":
            sub.add_argument("type", help="event type, e.g. dispatch.start")
            sub.add_argument("--data", required=True, help="the event's fields, a JSON object")
            sub.add_argument("--id", help="idempotency key; defaults to a new uuid4 hex")
        if command == "init":
            sub.add_argument("--spec", help="the approved spec; omit it for an investigation or "
                             "a discovery that has no spec yet")
        if command in ("freeze", "verify", "green", "import-legacy"):
            sub.add_argument("unit")
        if command == "freeze":
            sub.add_argument("--red-log", required=True, help="log of the failing test run")
            sub.add_argument("--brief", help="approved brief with append-only declarations; first freeze only")
        if command == "green":
            sub.add_argument("--green-log", required=True, help="log of the first passing test run")
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
        elif args.command in ("freeze", "verify", "green", "import-legacy"):
            manifest = manifest_path(run, primary, args.unit)
            legacy = evidence_path(manifest.with_suffix(".sha"), primary)
            if legacy.exists() and (
                args.command == "freeze" or (args.command in ("verify", "green") and not manifest.exists())
            ):
                raise StateError(
                    f"Legacy test evidence exists: {legacy}. Run "
                    f"task_state.py import-legacy {args.task} {args.unit} FILE... "
                    "with every original test file in its saved hash order."
                )
            if args.command == "freeze":
                freeze(manifest, current, args.files, args.red_log, args.brief)
            elif args.command == "import-legacy":
                import_legacy(manifest, legacy, current, args.files)
            elif args.command == "green":
                record_green(manifest, current, args.green_log)
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
