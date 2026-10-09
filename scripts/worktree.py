#!/usr/bin/env python3
"""Create and remove lane worktrees under the primary checkout's .claude/worktrees/.

  add NAME [--base REF] [--branch BRANCH] [--deps]
      New branch BRANCH (default NAME) at REF (default HEAD of the current checkout)
      in <primary>/.claude/worktrees/NAME; --deps then runs `npm ci` in every directory
      of the lane overlay's project.json "worktree": {"node_dirs": ["web", ...]}
      (checkout-relative, in order; project.py finds the overlay in the new lane)
      through quiet.py (digest on stderr per directory, logs under
      <primary>/.harness/logs/). --web is a deprecated alias of --deps.
      Only the lane's absolute path goes to stdout.
  remove NAME
      Refuses while the lane has staged, unstaged or untracked changes (ignored
      files such as node_modules do not count), then `git worktree remove`.
      The branch is kept.

The primary checkout is the first `git worktree list` entry, as in task_state.py, so a
lane made from inside another lane lands next to the others. Exit codes: 0 done,
1 refused or failed (also --deps with no node_dirs, an invalid entry or a directory missing
from the lane), or npm's own code from the first failing `npm ci`; the lane is kept and
the other directories are still installed.
"""

import argparse
from pathlib import Path
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
import project
from quiet import default_log, digest, execute, slug
from task_state import StateError, checkouts, git, identifier


def lane_path(primary: Path, name: str) -> Path:
    try:
        identifier(name)
    except StateError:
        raise StateError(f"Invalid lane name {name!r}: use letters, digits, '.', '_' "
                         "and '-', starting with a letter or digit") from None
    return primary / ".claude" / "worktrees" / name


def has_ref(ref: str) -> bool:
    try:
        git("rev-parse", "--verify", "--quiet", ref)
        return True
    except StateError:
        return False


def registered(primary: Path) -> set[Path]:
    records = git("worktree", "list", "--porcelain", "-z", cwd=primary).split("\0")
    return {Path(r[len("worktree "):]).resolve() for r in records if r.startswith("worktree ")}


def node_dirs(lane: Path) -> list[str]:
    """The lane overlay's worktree.node_dirs: checkout-relative directories, in order."""
    try:
        config = project.load(lane, "project.json", {}).get("worktree", {})
    except (project.OverlayError, OSError, ValueError) as exc:
        raise StateError(str(exc)) from None
    dirs = config.get("node_dirs", []) if isinstance(config, dict) else None
    if not isinstance(dirs, list) or not all(isinstance(d, str) and d.strip() for d in dirs):
        raise StateError("worktree.node_dirs must be a list of directory names")
    for folder in dirs:
        if Path(folder).is_absolute() or ".." in Path(folder).parts:
            raise StateError(f"worktree.node_dirs entry {folder!r} is outside the lane")
    if not dirs:
        raise StateError("the overlay lists no worktree.node_dirs")
    return dirs


def install(primary: Path, lane: Path, name: str) -> int:
    """`npm ci` in every node directory of the lane; the first failing exit code."""
    try:
        dirs = node_dirs(lane)
    except StateError as exc:
        print(f"worktree: {exc}; dependencies not installed", file=sys.stderr)
        return 1
    code = 0
    for folder in dirs:
        if not (lane / folder).is_dir():
            print(f"worktree: no {folder}/ in {lane}; npm ci not run there", file=sys.stderr)
            code = code or 1
            continue
        log = default_log(primary, slug(["worktree", name, folder, "npm", "ci"]))
        result = execute(["npm", "ci"], log, cwd=lane / folder)
        print(digest(result, log), file=sys.stderr)
        if result:
            print(f"worktree: npm ci in {folder}/ failed (exit {result}); the lane is kept",
                  file=sys.stderr)
            code = code or result
    return code


def add(primary: Path, name: str, base: str, branch: str | None, deps: bool) -> int:
    path = lane_path(primary, name)
    branch = branch or name
    if path.exists() or path.is_symlink():
        raise StateError(f"Lane already exists: {path}")
    if has_ref(f"refs/heads/{branch}"):
        raise StateError(f"Branch already exists: {branch}")
    if not has_ref(f"{base}^{{commit}}"):
        raise StateError(f"Unknown base revision: {base}")
    commit = git("rev-parse", "--verify", f"{base}^{{commit}}")
    git("worktree", "add", "-b", branch, str(path), commit, cwd=primary)
    path = path.resolve()
    code = install(primary, path, name) if deps else 0
    print(path)
    return code


def remove(primary: Path, current: Path, name: str) -> int:
    path = lane_path(primary, name)
    if not path.exists() or path.resolve() not in registered(primary):
        raise StateError(f"No lane worktree named {name}: {path}")
    path = path.resolve()
    if current == path:
        raise StateError(f"Refusing to remove the checkout you are in: {path}")
    status = git("status", "--porcelain", "--untracked-files=all", cwd=path)
    if status:
        print(f"worktree: {path} has uncommitted or untracked changes; commit or discard "
              f"them first:\n{status}", file=sys.stderr)
        return 1
    git("worktree", "remove", str(path), cwd=primary)
    print(f"removed {path} (branch kept)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("add", help="create a lane worktree")
    create.add_argument("name")
    create.add_argument("--base", default="HEAD", help="start revision (default HEAD)")
    create.add_argument("--branch", help="new branch name (default NAME)")
    create.add_argument("--deps", action="store_true",
                        help="run npm ci in every worktree.node_dirs entry of the overlay")
    create.add_argument("--web", action="store_true", help="deprecated alias of --deps")
    commands.add_parser("remove", help="remove a clean lane worktree").add_argument("name")
    args = parser.parse_args()
    try:
        primary, current = checkouts()
        if args.command == "add":
            if args.web:
                print("worktree: --web is deprecated; use --deps (npm ci in every "
                      "worktree.node_dirs entry)", file=sys.stderr)
            return add(primary, args.name, args.base, args.branch, args.deps or args.web)
        return remove(primary, current, args.name)
    except (StateError, OSError) as exc:
        print(f"worktree: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
