"""Where the core and the project overlay live, and how core scripts read the overlay.

A product repository vendors the core at `harness/core` (git subtree) and keeps its own
layer at `harness/project`. Core scripts never hard-code project paths, hosts or commands:
they read them from the overlay, and fall back to an empty configuration.

  core_root()            the directory holding this core (scripts/, skills/, roles/ …)
  repo_root(cwd)         the Git checkout root the command runs in
  overlay_dir(root)      $HARNESS_PROJECT, else <root>/harness/project
  load(root, name, default)   one overlay JSON file (project.json, tiers.json …)

CLI: `python3 harness/core/scripts/project.py overlay` prints the overlay directory in use.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

CORE = Path(__file__).resolve().parents[1]


class OverlayError(Exception):
    """An overlay file exists but cannot be used."""


def core_root() -> Path:
    return CORE


def repo_root(cwd: Path | str | None = None) -> Path:
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                            capture_output=True, text=True)
    if result.returncode:
        raise OverlayError(result.stderr.strip() or "not inside a Git checkout")
    return Path(result.stdout.strip()).resolve()


def overlay_dir(root: Path) -> Path:
    override = os.environ.get("HARNESS_PROJECT")
    if override:
        path = Path(override)
        return (path if path.is_absolute() else root / path).resolve()
    return root / "harness" / "project"


def load(root: Path, name: str, default):
    path = overlay_dir(root) / name
    if not path.is_file():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise OverlayError(f"{path}: {exc}") from None
    if type(value) is not type(default):
        raise OverlayError(f"{path}: expected a JSON {type(default).__name__}")
    return value


def main(argv: list[str]) -> int:
    """`project.py overlay`: the overlay directory in use, relative to the checkout when inside
    it — what the skills read instead of assuming harness/project."""
    if argv != ["overlay"]:
        print("usage: project.py overlay", file=sys.stderr)
        return 2
    root = repo_root(Path.cwd())
    path = overlay_dir(root)
    print(path.relative_to(root).as_posix() if path.is_relative_to(root) else path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
