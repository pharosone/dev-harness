#!/usr/bin/env python3
"""Classify a diff into a process tier by its paths and its size (the overlay's tiers.json).

Usage: tier.py --base REF [--json]

Overlay file `tiers.json` (in $HARNESS_PROJECT, else <checkout>/harness/project):
  tier3_override, tier0_override, tier3, tier2, tier1, tier0   lists of globs, each optional
                                                               (missing = empty)
  limits        {"tier0_lines": N, "tier1_lines": N}, non-negative integers, required
  comment       free text, ignored
Without tiers.json nothing is classified lower than the default: every changed path is
tier 2 with the rule "no tiers.json" and the size decides nothing.

The diff is the checkout against REF: committed, staged, unstaged and untracked
changes; a rename counts as its old and its new path. Each path takes the tier of the
first list in tiers.json it matches, checked in the order tier3_override,
tier0_override, tier3, tier2, tier1, tier0; a path no list matches is tier 2
("unclassified"). tier3_override is for what is Tier 3 wherever it lives (agent
instructions, build files), so a tier-0 override for data under a Tier-3 directory
cannot lower it. Globs are those of tripwire.py: `*` stays inside one directory, `**`
crosses directories.

The size is added + deleted lines from `git diff --numstat REF` plus the lines of
untracked files (binary files count 0): above tier1_lines it is tier 2, above
tier0_lines tier 1. The diff's tier is the strictest of its paths and its size.
Reasons are what decided it: every path of that tier and, when it reaches that tier
too, the size. Text output is `tier=<n>`, `lines=<n>`, then one line per reason;
--json prints {"tier", "lines", "reasons": [{"path", "tier", "rule"}]}, where the size
reason has path null. Exit codes: 0 classified, 2 error (an unusable tiers.json too).
"""

import argparse
import json
from pathlib import Path
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from project import OverlayError, load as load_overlay, overlay_dir
from task_state import StateError, git
from tripwire import glob_pattern

TABLE = "tiers.json"
ORDER = (("tier3_override", 3), ("tier0_override", 0), ("tier3", 3), ("tier2", 2),
         ("tier1", 1), ("tier0", 0))
LIMITS = ("tier0_lines", "tier1_lines")
NO_TABLE = "no tiers.json"


def parse(data: dict, where: str) -> tuple[list, dict]:
    """(rules, limits) from a tiers.json object; rules are (glob, pattern, tier) in order."""
    known = {key for key, _ in ORDER} | {"limits", "comment"}
    if set(data) - known:
        raise StateError(f"{where}: unknown keys {sorted(set(data) - known)}")
    rules = []
    for key, tier in ORDER:
        globs = data.get(key, [])
        if not isinstance(globs, list) or not all(isinstance(g, str) and g for g in globs):
            raise StateError(f"{where}: {key} must be a list of globs")
        rules += [(glob, glob_pattern(glob), tier) for glob in globs]
    limits = data.get("limits")
    if not isinstance(limits, dict) or set(limits) != set(LIMITS) or not all(
            type(limits[k]) is int and limits[k] >= 0 for k in LIMITS):
        raise StateError(f"{where}: limits must hold non-negative {' and '.join(LIMITS)}")
    return rules, limits


def load(root: Path) -> tuple[list, dict] | None:
    """The checkout's tier table, or None when its overlay has none."""
    path = overlay_dir(root) / TABLE
    if not path.is_file():
        return None
    try:
        data = load_overlay(root, TABLE, {})
    except OverlayError as exc:
        raise StateError(str(exc)) from None
    return parse(data, str(path))


def classify(name: str, rules: list) -> tuple[int, str]:
    for glob, pattern, tier in rules:
        if pattern.match(name):
            return tier, glob
    return 2, "unclassified"


def file_lines(path: Path) -> int:
    if path.is_symlink():
        return 1  # git counts a link as its one-line target
    if not path.is_file():
        return 0
    data = path.read_bytes()
    if b"\0" in data[:8000]:
        return 0  # binary, as --numstat reports it
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def changes(root: Path, base: str) -> dict[str, int]:
    try:
        git("rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", cwd=root)
    except StateError:
        raise StateError(f"Unknown base revision: {base}") from None
    files = {}
    numstat = git("diff", "--numstat", "--no-renames", "-z", base, "--", cwd=root)
    for record in filter(None, numstat.split("\0")):
        added, deleted, name = record.split("\t", 2)
        files[name] = sum(0 if n == "-" else int(n) for n in (added, deleted))
    untracked = git("ls-files", "--others", "--exclude-standard", "-z", cwd=root)
    for name in filter(None, untracked.split("\0")):
        files[name] = file_lines(root / name)
    return files


def assess(files: dict[str, int], rules: list, limits: dict) -> dict:
    lines = sum(files.values())
    size_tier = 2 if lines > limits["tier1_lines"] else 1 if lines > limits["tier0_lines"] else 0
    classified = {name: classify(name, rules) for name in sorted(files)}
    tier = max([size_tier, *(t for t, _ in classified.values())])
    reasons = [{"path": name, "tier": t, "rule": rule}
               for name, (t, rule) in classified.items() if t == tier]
    if size_tier and size_tier == tier:
        limit = "tier1_lines" if size_tier == 2 else "tier0_lines"
        reasons.append({"path": None, "tier": tier,
                        "rule": f"{lines} lines > {limit} {limits[limit]}"})
    return {"tier": tier, "lines": lines, "reasons": reasons}


def unconfigured(files: dict[str, int]) -> dict:
    """Without a tier table: every changed path is tier 2, an empty diff tier 0."""
    return {"tier": 2 if files else 0, "lines": sum(files.values()),
            "reasons": [{"path": name, "tier": 2, "rule": NO_TABLE} for name in sorted(files)]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="revision the diff is taken against")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args()
    try:
        root = Path(git("rev-parse", "--show-toplevel")).resolve()
        table = load(root)
        files = changes(root, args.base)
        report = assess(files, *table) if table else unconfigured(files)
    except (StateError, OSError, ValueError) as exc:
        print(f"tier: {exc}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report))
        return 0
    print(f"tier={report['tier']}")
    print(f"lines={report['lines']}")
    for reason in report["reasons"]:
        if reason["path"] is None:
            print(f"size: {reason['rule']}")
        else:
            print(f"{reason['path']}: tier {reason['tier']} ({reason['rule']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
