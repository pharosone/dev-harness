#!/usr/bin/env python3
"""Stateless documentation-drift check. Exit 0: clean, 1: findings, 2: error.

Usage: check_docs_drift.py --base REF [--format text|json]

Checks branch changes against --base plus staged, unstaged and untracked work: a change
to a path that a document restates needs a change to that document in the same branch.
Runtime hooks import this module; session suppression belongs to their wrapper.

The mappings are the project's: overlay file `drift.json` (in $HARNESS_PROJECT, else
<checkout>/harness/project). Every key is optional; without the file only the
harness-log rule runs, with its defaults. An unusable file is an error, never clean.
  harness_log    {"path": file, "paths": [globs]}: a change to a path matching `paths`
                 needs a change to `path`, the project's harness history.
                   path   default <overlay>/HARNESS_LOG.md (harness/project/HARNESS_LOG.md)
                   paths  default: the overlay (<overlay>/**), harness/**, and the files
                          rendered from core and overlay (.claude/agents/**,
                          .claude/skills/**, .claude/rules/**, .claude/hooks/**,
                          .claude/settings.json, .agents/skills/**, .codex/agents/**,
                          .codex/hooks.json, .codex/config.toml); a list given here
                          replaces the default
                 Never counted: the log itself; the core this script belongs to (at
                 harness/core in a project; core changes go in the core's CHANGELOG, and
                 in the core's own repository the rule is off); untracked files under
                 .claude/ (personal agents and skills).
  watched        [{"paths": [globs], "doc": file, "claim": text}]: a changed path needs
                 `doc` changed (a config module and its env table).
  structural     [{"paths": [globs], "doc", "claim"}]: an added, deleted or renamed path
                 needs `doc` changed (a migration and the domain document).
  new_packages   [{"paths": [globs], "doc", "claim"}]: an added path whose directory had
                 no files at the base, a new package, needs `doc` changed (the repo map).
  mirrors        [{"paths": [file, file, ...], "claim"}]: hand-written mirrors of one
                 contract; a changed member needs every other member changed.
  registries     [{"registry": file, "files": [globs], "exclude": [globs], "route": regex,
                 "claim"}]: an added line matching `route` in a changed file matching
                 `files` and not `exclude` (optional) needs `registry` changed; off while
                 `registry` does not exist (nothing to register into yet).
  comment        free text, ignored
Globs are tripwire.py's: `*` stays inside one directory, `**` crosses directories.
Documents and mirror members are exact checkout-relative paths. A finding is
{"document", "claim", "paths"}; --format json prints {"findings": [...]}, text output
goes to stderr.
"""

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from project import OverlayError, core_root, load as load_overlay, overlay_dir
from tripwire import glob_pattern

MAPPINGS = "drift.json"
LOG_NAME = "HARNESS_LOG.md"
LOG_CLAIM = "the harness change log"
DEFAULT_OVERLAY = "harness/project"
DEFAULT_CORE = "harness/core"
RENDERED = (
    ".claude/agents/**", ".claude/skills/**", ".claude/rules/**", ".claude/hooks/**",
    ".claude/settings.json", ".agents/skills/**", ".codex/agents/**", ".codex/hooks.json",
    ".codex/config.toml",
)
PERSONAL = ".claude/"  # untracked files here are a person's own agents and skills
DOC_RULES = ("watched", "structural", "new_packages")
KEYS = {"comment", "harness_log", "mirrors", "registries", *DOC_RULES}


class CheckError(Exception):
    """An incomplete check must never be reported as clean."""


def git(cwd, *args):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="surrogateescape"
    )
    if result.returncode:
        raise CheckError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def repository(cwd):
    return Path(git(cwd, "rev-parse", "--show-toplevel").strip())


def resolve_base(root, base):
    return git(root, "rev-parse", "--verify", "--end-of-options", f"{base}^{{commit}}").strip()


def default_base(root):
    """Hooks use the branch merge base; CI/CLI always provide their base explicitly."""
    for ref in ("refs/remotes/origin/main", "refs/heads/main"):
        # Missing preferred refs are expected; every other Git failure is fatal.
        result = subprocess.run(
            ["git", "show-ref", "--verify", "--quiet", ref], cwd=root,
            capture_output=True, text=True,
        )
        if result.returncode == 1:
            continue
        if result.returncode:
            raise CheckError(f"git show-ref {ref}: {result.stderr.strip()}")
        return git(root, "merge-base", "HEAD", ref).strip()
    raise CheckError("No origin/main or main base exists; run the CLI with --base REV.")


def inside(root, path):
    """`path` relative to the checkout as a POSIX string ("." for the root), else None."""
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return None


def under(path, prefix):
    return prefix == "." or path == prefix or path.startswith(prefix + "/")


def matches(patterns, path):
    return any(pattern.match(path) for pattern in patterns)


# --- drift.json ----------------------------------------------------------------------------

def text_value(value, where):
    if not isinstance(value, str) or not value:
        raise CheckError(f"{where} must be a non-empty string")
    return value


def file_value(value, where):
    if text_value(value, where) != value.strip() or any(c in value for c in "*?[") \
            or value.startswith("/") or ".." in value.split("/"):
        raise CheckError(f"{where} must be a checkout-relative file path, not a glob")
    return value


def glob_list(value, where):
    if not isinstance(value, list) or not value or not all(
            isinstance(glob, str) and glob for glob in value):
        raise CheckError(f"{where} must be a non-empty list of globs")
    return [glob_pattern(glob) for glob in value]


def rule_list(data, key, where, required, optional=()):
    items = data.get(key, [])
    if not isinstance(items, list):
        raise CheckError(f"{where}: {key} must be a list")
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not set(required) <= set(item) <= {*required, *optional}:
            raise CheckError(f"{where}: {key}[{index}] needs {', '.join(required)}"
                             + (f" (optional: {', '.join(optional)})" if optional else ""))
    return [(f"{where}: {key}[{index}]", item) for index, item in enumerate(items)]


def mappings(root):
    """The checkout's mappings from its overlay drift.json, with the harness-log defaults."""
    path = overlay_dir(root) / MAPPINGS
    try:
        data = load_overlay(root, MAPPINGS, {})
    except OverlayError as error:
        raise CheckError(str(error)) from None
    where = str(path)
    if set(data) - KEYS:
        raise CheckError(f"{where}: unknown keys {sorted(set(data) - KEYS)} "
                         f"(known: {', '.join(sorted(KEYS))})")
    if not isinstance(data.get("comment", ""), str):
        raise CheckError(f"{where}: comment must be text")

    overlay = inside(root, overlay_dir(root)) or DEFAULT_OVERLAY
    log = data.get("harness_log", {})
    if not isinstance(log, dict) or set(log) - {"path", "paths"}:
        raise CheckError(f"{where}: harness_log is an object with optional path and paths")
    default_paths = ([] if overlay == "." else [f"{overlay}/**"]) + ["harness/**", *RENDERED]
    result = {
        "log": file_value(log["path"], f"{where}: harness_log.path") if "path" in log
        else (LOG_NAME if overlay == "." else f"{overlay}/{LOG_NAME}"),
        "harness": glob_list(log.get("paths", default_paths), f"{where}: harness_log.paths"),
        # The running core, wherever this script lives; a core outside the checkout (its own
        # tests, a core run against another repository) stands for the vendored location.
        "core": inside(root, core_root()) or DEFAULT_CORE,
    }
    for key in DOC_RULES:
        result[key] = [(glob_list(item["paths"], f"{place}.paths"),
                        file_value(item["doc"], f"{place}.doc"),
                        text_value(item["claim"], f"{place}.claim"))
                       for place, item in rule_list(data, key, where, ("paths", "doc", "claim"))]
    result["mirrors"] = []
    for place, item in rule_list(data, "mirrors", where, ("paths", "claim")):
        members = item["paths"]
        if not isinstance(members, list) or len(set(members)) < 2:
            raise CheckError(f"{place}.paths must list two or more files")
        result["mirrors"].append(([file_value(m, f"{place}.paths") for m in members],
                                  text_value(item["claim"], f"{place}.claim")))
    result["registries"] = []
    for place, item in rule_list(data, "registries", where,
                                 ("registry", "files", "route", "claim"), ("exclude",)):
        try:
            route = re.compile(text_value(item["route"], f"{place}.route"))
        except re.error as error:
            raise CheckError(f"{place}.route is not a regular expression: {error}") from None
        result["registries"].append((
            file_value(item["registry"], f"{place}.registry"),
            glob_list(item["files"], f"{place}.files"),
            glob_list(item["exclude"], f"{place}.exclude") if "exclude" in item else [],
            route,
            text_value(item["claim"], f"{place}.claim"),
        ))
    return result


# --- The check -----------------------------------------------------------------------------

def changes(root, base):
    structural, touched, untracked = set(), set(), set()
    # Disable rename detection here to retain both the removed and added paths.
    entries = git(root, "diff", "--name-status", "-z", "--no-renames", base, "HEAD").split("\0")
    for status, path in zip(entries[0::2], entries[1::2]):
        touched.add(path)
        if status in ("A", "D"):
            structural.add(path)
    entries = iter(git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all").split("\0"))
    for entry in entries:
        if not entry:
            continue
        status, path = entry[:2], entry[3:]
        touched.add(path)
        if any(kind in status for kind in "ADR?"):
            structural.add(path)
        if status == "??":
            untracked.add(path)
        if "R" in status or "C" in status:
            original = next(entries)
            touched.add(original)
            if "R" in status:
                structural.add(original)
    return structural, touched, untracked


def added_lines(root, paths, *revs):
    path, header = None, False
    specs = [f":(literal){name}" for name in paths]
    diff = git(root, "-c", "core.quotePath=false", "diff", "-U0", "--no-color",
               "--no-ext-diff", "--no-textconv", *revs, "--", *specs)
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, header = None, True
        elif line.startswith("@@"):
            header = False
        elif header and line.startswith("+++ b/"):
            path = line[6:].split("\t", 1)[0]
        elif not header and path and line.startswith("+"):
            yield path, line[1:]


def check(root, base):
    rules = mappings(root)
    base = resolve_base(root, base)
    structural, touched, untracked = changes(root, base)
    findings = {}

    def add(doc, claim, paths):
        if doc not in touched and paths:
            findings.setdefault((doc, claim), set()).update(paths)

    for patterns, doc, claim in rules["structural"]:
        add(doc, claim, [p for p in structural if matches(patterns, p)])
    for patterns, doc, claim in rules["new_packages"]:
        add(doc, claim, [p for p in structural if matches(patterns, p) and not git(
            root, "ls-tree", "--name-only", base, str(Path(p).parent) + "/").strip()])
    harness = [p for p in touched if p != rules["log"] and matches(rules["harness"], p)
               and not under(p, rules["core"])
               and not (p in untracked and p.startswith(PERSONAL))]
    add(rules["log"], LOG_CLAIM, harness)
    for patterns, doc, claim in rules["watched"]:
        add(doc, claim, [p for p in touched if matches(patterns, p)])
    for members, claim in rules["mirrors"]:
        for member in members:
            if member in touched:
                for other in members:
                    if other != member:
                        add(other, claim, [member])
    for registry, files, exclude, route, claim in rules["registries"]:
        # No registry yet: nothing to register into; its coverage test takes over once it lands.
        if not (root / registry).exists() or registry in touched:
            continue
        sources = sorted(p for p in touched if matches(files, p) and not matches(exclude, p))
        if not sources:
            continue
        lines = list(added_lines(root, sources, base, "HEAD")) + list(
            added_lines(root, sources, "HEAD"))
        for path in sorted(untracked.intersection(sources)):
            lines.extend((path, line)
                         for line in (root / path).read_text(errors="replace").splitlines())
        add(registry, claim,
            [path for path, text in lines if path in sources and route.search(text)])
    return [{"document": doc, "claim": claim, "paths": sorted(paths)}
            for (doc, claim), paths in sorted(findings.items())]


def format_findings(findings):
    lines = ["Docs may be stale — this branch changes what they restate:"]
    for finding in findings:
        shown = ", ".join(finding["paths"][:4])
        lines.append(f"  {finding['document']} ({finding['claim']}) <- {shown}")
    lines.append("Check each one against the code and update it, or explain why it still holds. "
                 "Verify claims before writing them; do not commit unless asked.")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="Git revision to compare against")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        findings = check(repository(Path.cwd()), args.base)
    except (CheckError, OSError, ValueError) as error:
        print(f"Documentation drift check failed: {error}", file=sys.stderr)
        return 2
    if args.format == "json":
        print(json.dumps({"findings": findings}, sort_keys=True))
    elif findings:
        print(format_findings(findings), file=sys.stderr)
    return int(bool(findings))


if __name__ == "__main__":
    sys.exit(main())
