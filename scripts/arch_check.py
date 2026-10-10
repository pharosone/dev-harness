#!/usr/bin/env python3
"""Architecture conformance check. Exit 0: clean, 1: findings, 2: configuration or usage error.

Usage: arch_check.py [--base REF | --all] [--format text|json]

  --base REF   check the files changed against REF: committed since it, staged, unstaged and
               untracked (a rename counts both paths; deleted files are skipped)
  --all        check every tracked and untracked (not ignored) file
  neither      --base the merge base of HEAD with origin/main, else with main
  --format     text (default): one finding per line, then a summary line, on stdout;
               json: {"findings": [{"path", "line", "rule", "message"}]}, line null when the
               finding is about the file as a whole

The declared architecture is the project's: overlay file `architecture.json` (in
$HARNESS_PROJECT, else <checkout>/harness/project). Without the file the check is off: exit 0
and a note saying so, before any base is resolved (a checkout without the file never fails). An unusable file is an error naming the problem, never clean. Keys, all
optional:
  layers     [{"name", "paths": [globs], "may_depend_on": [layer names]}]: a file belongs to
             the layer whose paths match it; a file matching two layers is an error naming
             both. may_depend_on (default []) names the other layers it may import.
  imports    [{"files": glob, "pattern": regex, "resolve": {import prefix: path prefix}}]:
             how a file's dependencies read in its language. `pattern` (Python regex,
             multi-line mode) has a named group `target`; every match in a file matching
             `files` is one dependency at the line where `target` starts. A target starting
             with "." is relative to the importing file's directory: a path ("./x", "../x")
             is joined to it; dotted Python form ("..pkg.mod") climbs one package per dot
             after the first. Any other target takes the longest matching `resolve` prefix,
             replaced by its checkout-relative path prefix; when the prefix ends in ".", the
             rest is dotted and its dots become "/". A target with no matching prefix (a
             standard or third-party library) is ignored. A resolved path belongs to the layer
             that matches it, or that matches a file directly inside it (a package directory),
             else to the layer of the first file on disk it names that is in a layer, tried in
             order: <path> with the importing file's extension, <path>.*, <path>/__init__.py,
             <path>/index with that extension, <path>/index.* (so `app.data.store` finds
             src/data/store.py); one in no layer is ignored.
  placement  [{"kind", "files": glob, "allowed": [globs], "claim"}]: a file matching
             `files` must match one of `allowed`.
  naming     [{"files": glob, "pattern": regex, "claim"}]: the basename of a file matching
             `files` must match `pattern` in full.
  roots      [globs]: every file matching a root belongs to some layer.
  comment    free text, ignored
Globs are tripwire.py's on checkout-relative paths: `*` stays inside one directory, `**`
crosses directories. Findings, by rule:
  dependency  path:line — <A> may not depend on <B> (<target>)
  layer       path — not in any layer of architecture.json
  placement   path — <claim>
  naming      path — <claim>
  unreadable  path — unreadable, dependencies not checked (a file an imports rule covers that
              is not UTF-8 or cannot be read)
"""

import argparse
import json
from pathlib import Path, PurePosixPath
import posixpath
import re
import subprocess
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from check_docs_drift import CheckError, changes, default_base, git, repository, resolve_base
from project import OverlayError, load as load_overlay, overlay_dir
from tripwire import glob_pattern

NAME = "architecture.json"
KEYS = {"comment", "layers", "imports", "placement", "naming", "roots"}
UNASSIGNED = f"not in any layer of {NAME}"
UNREADABLE = "unreadable, dependencies not checked"


class ConfigError(Exception):
    """The declared architecture cannot be used; a check against it would mean nothing."""


# --- architecture.json ----------------------------------------------------------------------

def text(value, where):
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where} must be a non-empty string")
    return value


def texts(value, where):
    if not isinstance(value, list) or not value:
        raise ConfigError(f"{where} must be a non-empty list of strings")
    return [text(item, f"{where}[{index}]") for index, item in enumerate(value)]


def regex(value, where, flags=0):
    try:
        return re.compile(text(value, where), flags)
    except re.error as error:
        raise ConfigError(f"{where} is not a valid regex: {error}") from None


def entries(config, key, required, optional=()):
    """The objects listed under `key`, each with exactly the required and optional keys."""
    value = config.get(key, [])
    if not isinstance(value, list):
        raise ConfigError(f"{key} must be a list of objects")
    for index, entry in enumerate(value):
        where = f"{key}[{index}]"
        if not isinstance(entry, dict):
            raise ConfigError(f"{where} must be an object")
        unknown = sorted(set(entry) - set(required) - set(optional))
        if unknown:
            raise ConfigError(f"{where}: unknown key {', '.join(unknown)}")
        missing = [name for name in required if name not in entry]
        if missing:
            raise ConfigError(f"{where}: missing {', '.join(missing)}")
        yield where, entry


def parse(config):
    unknown = sorted(set(config) - KEYS)
    if unknown:
        raise ConfigError(f"unknown key {', '.join(unknown)} (known: {', '.join(sorted(KEYS))})")
    layers = {}
    for where, entry in entries(config, "layers", ("name", "paths"), ("may_depend_on",)):
        name = text(entry["name"], f"{where}.name")
        if name in layers:
            raise ConfigError(f"{where}: layer name {name!r} appears twice")
        allowed = entry.get("may_depend_on", [])
        if not isinstance(allowed, list):
            raise ConfigError(f"{where}.may_depend_on must be a list of layer names")
        layers[name] = {
            "paths": [glob_pattern(glob) for glob in texts(entry["paths"], f"{where}.paths")],
            "may_depend_on": {text(item, f"{where}.may_depend_on[{index}]")
                              for index, item in enumerate(allowed)},
        }
    for name, layer in layers.items():
        ghosts = sorted(layer["may_depend_on"] - set(layers))
        if ghosts:
            raise ConfigError(f"layer {name!r} may_depend_on unknown layer {', '.join(ghosts)}")
    imports = []
    for where, entry in entries(config, "imports", ("files", "pattern"), ("resolve",)):
        pattern = regex(entry["pattern"], f"{where}.pattern", re.MULTILINE)
        if "target" not in pattern.groupindex:
            raise ConfigError(f"{where}.pattern has no named group (?P<target>...)")
        resolve = entry.get("resolve", {})
        if not isinstance(resolve, dict) or not all(
                isinstance(key, str) and key and isinstance(value, str)
                for key, value in resolve.items()):
            raise ConfigError(f"{where}.resolve must map import prefixes to path prefixes")
        imports.append({"files": glob_pattern(text(entry["files"], f"{where}.files")),
                        "pattern": pattern,
                        "resolve": sorted(resolve.items(), key=lambda item: -len(item[0]))})
    placement = [{"files": glob_pattern(text(entry["files"], f"{where}.files")),
                  "allowed": [glob_pattern(glob) for glob in texts(entry["allowed"],
                                                                   f"{where}.allowed")],
                  "claim": text(entry["claim"], f"{where}.claim"),
                  "kind": text(entry["kind"], f"{where}.kind")}
                 for where, entry in entries(config, "placement",
                                             ("kind", "files", "allowed", "claim"))]
    naming = [{"files": glob_pattern(text(entry["files"], f"{where}.files")),
               "pattern": regex(entry["pattern"], f"{where}.pattern"),
               "claim": text(entry["claim"], f"{where}.claim")}
              for where, entry in entries(config, "naming", ("files", "pattern", "claim"))]
    roots = config.get("roots", [])
    if not isinstance(roots, list):
        raise ConfigError("roots must be a list of globs")
    roots = [glob_pattern(text(glob, f"roots[{index}]")) for index, glob in enumerate(roots)]
    return {"layers": layers, "imports": imports, "placement": placement, "naming": naming,
            "roots": roots}


# --- the checks -----------------------------------------------------------------------------

def matches(patterns, path):
    return any(pattern.match(path) for pattern in patterns)


def layer_of(architecture, path):
    owners = [name for name, layer in architecture["layers"].items()
              if matches(layer["paths"], path)]
    if len(owners) > 1:
        raise ConfigError(f"{path} matches layers {' and '.join(owners)}; a file belongs to one")
    return owners[0] if owners else None


def on_disk(root, path, importer):
    """The files an extensionless import may name, in order: `<path>` with the importing
    file's extension, `<path>.*`, then `<path>/__init__.py`, `<path>/index` with that
    extension and `<path>/index.*`; only those that exist in the working tree."""
    suffix = PurePosixPath(importer).suffix

    def named(directory, stem, first):
        """`<stem>.*` files in `directory`, the one with extension `first` leading."""
        if not directory.is_dir():
            return []
        names = sorted(item.name for item in directory.iterdir() if item.is_file()
                       and item.name.startswith(f"{stem}.") and len(item.name) > len(stem) + 1)
        lead = [f"{stem}{first}"] if first and f"{stem}{first}" in names else []
        return lead + [name for name in names if name not in lead]

    target = root / path
    parent = posixpath.dirname(path)
    found = [posixpath.join(parent, name) for name in named(target.parent, target.name, suffix)]
    package = ["__init__.py"] if (target / "__init__.py").is_file() else []
    found += [f"{path}/{name}" for name in package + named(target, "index", suffix)]
    return found


def target_layer(architecture, path, root=None, importer=""):
    """The layer of a resolved import: the path itself, else a package directory's file, else
    the first file on disk the extensionless import names that belongs to a layer."""
    layer = layer_of(architecture, path) or layer_of(architecture, f"{path}/_")
    if layer or root is None:
        return layer
    for candidate in on_disk(root, path, importer):
        layer = layer_of(architecture, candidate)
        if layer:
            return layer
    return None


def resolve(rule, importer, target):
    """The checkout-relative path a target names, or None for an external library."""
    if target.startswith("."):
        directory = posixpath.dirname(importer)
        if "/" in target:  # a path: "./x", "../x"
            joined = posixpath.join(directory, target)
        else:  # dotted: ".", "..", ".mod", "..pkg.mod" (n dots climb n - 1 packages)
            dots = len(target) - len(target.lstrip("."))
            parts = [directory, *[".."] * (dots - 1), target[dots:].replace(".", "/")]
            joined = "/".join(part for part in parts if part)
        path = posixpath.normpath(joined or ".")
        return None if path == ".." or path.startswith("../") else path
    for prefix, replacement in rule["resolve"]:
        if target.startswith(prefix):
            rest = target[len(prefix):]
            if prefix.endswith("."):
                rest = rest.replace(".", "/")
            return posixpath.normpath(replacement + rest) if replacement + rest else None
    return None


def dependencies(root, architecture, path, layer):
    rules = [rule for rule in architecture["imports"] if rule["files"].match(path)]
    if not rules:
        return
    try:
        source = (root / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        # Never clean by default: a file whose dependencies were not read is a finding.
        yield finding(path, None, "unreadable", UNREADABLE)
        return
    for rule in rules:
        for match in rule["pattern"].finditer(source):
            target = match.group("target")
            if not target:
                continue
            resolved = resolve(rule, path, target)
            other = resolved and target_layer(architecture, resolved, root, path)
            if other and other != layer and other not in architecture["layers"][layer][
                    "may_depend_on"]:
                line = source.count("\n", 0, match.start("target")) + 1
                yield finding(path, line, "dependency",
                              f"{layer} may not depend on {other} ({target})")


def finding(path, line, rule, message):
    return {"path": path, "line": line, "rule": rule, "message": message}


def check_file(root, architecture, path):
    found = []
    layer = layer_of(architecture, path)
    if layer is None and matches(architecture["roots"], path):
        found.append(finding(path, None, "layer", UNASSIGNED))
    for rule in architecture["placement"]:
        if rule["files"].match(path) and not matches(rule["allowed"], path):
            found.append(finding(path, None, "placement", rule["claim"]))
    for rule in architecture["naming"]:
        if rule["files"].match(path) and not rule["pattern"].fullmatch(PurePosixPath(path).name):
            found.append(finding(path, None, "naming", rule["claim"]))
    if layer is not None:
        found.extend(dependencies(root, architecture, path, layer))
    return found


def files(root, base):
    """The checkout-relative files to check that exist in the working tree."""
    if base is None:
        listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
        names = set(filter(None, listed.split("\0")))
    else:
        names = changes(root, base)[1]
    return sorted(name for name in names if (root / name).is_file())


def off_note(root):
    """The note saying the check is off when the overlay declares no architecture, else None."""
    overlay = overlay_dir(root)
    if (overlay / NAME).is_file():
        return None
    shown = overlay.relative_to(root).as_posix() if overlay.is_relative_to(root) else overlay
    return f"architecture check is off: no {NAME} in {shown}"


def check(root, base):
    """The sorted findings on the files to check (base None: every file), and their count."""
    path = overlay_dir(root) / NAME
    try:
        architecture = parse(load_overlay(root, NAME, {}))
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from None
    checked = files(root, base)
    found = []
    for name in checked:
        found.extend(check_file(root, architecture, name))
    found.sort(key=lambda item: (item["path"], item["line"] or 0, item["rule"], item["message"]))
    return found, len(checked)


def plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


def render(found, checked):
    lines = [f"{item['path']}{'' if item['line'] is None else ':' + str(item['line'])}"
             f" — {item['message']}" for item in found]
    if found:
        lines.append(f"architecture check: {plural(len(found), 'finding')} "
                     f"in {plural(checked, 'file')} checked")
    else:
        lines.append(f"architecture check: clean, {plural(checked, 'file')} checked")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--base", help="Git revision to compare against")
    scope.add_argument("--all", action="store_true", help="check every file")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args()
    try:
        root = repository(Path.cwd()).resolve()
        note = off_note(root)
        if note is None:
            base = None
            if not args.all:
                base = resolve_base(root, args.base) if args.base else default_base(root)
            found, checked = check(root, base)
    except (ConfigError, OverlayError, CheckError, OSError, subprocess.SubprocessError) as error:
        print(f"Architecture check failed: {error}", file=sys.stderr)
        return 2
    if note is not None:
        print(json.dumps({"findings": [], "note": note}) if args.format == "json" else note)
        return 0
    if args.format == "json":
        print(json.dumps({"findings": found}, sort_keys=True))
    else:
        print(render(found, checked))
    return int(bool(found))

if __name__ == "__main__":
    sys.exit(main())
