#!/usr/bin/env python3
"""Install, inspect, update and contribute back the vendored harness core.

A product repository vendors the core with `git subtree --squash` at a prefix (default
`harness/core`) and pins it in the overlay's project.json:

  "core": {"repo": "<url>", "ref": "<tag>", "prefix": "harness/core"}

Run inside the product checkout:

  install --repo URL --ref TAG [--prefix P]  first `git subtree add`; writes the pin
  status [--json] [--strict]                 pin, latest upstream tag, local edits in the core
  pull [REF]                                 `git subtree pull` (default: latest tag) in a
                                             scratch worktree, render and run the harness tests
                                             there; the branch fast-forwards only when green
  propose --branch NAME [--title T] [--feedback-file F] [--yes]
                                             the core edits as a branch and pull request

Exit codes: 0 done or clean; 1 needs your action (edits inside the core, a conflicted or red
update); 2 refused or failed (dirty tree, no pin, Git or network error).

Nothing is ever reset. Only `propose --yes` publishes, and it pushes a temporary clone of the
core repository, never the product repository. `status --strict` is the project CI check: the
core must match its pin (no latest-tag lookup); local changes go upstream (`propose`) or
into the overlay. Pin values reach Git as arguments and may not start with "-".
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import project  # noqa: E402

DEFAULT_PREFIX = "harness/core"
VERSION = re.compile(r"v?(\d+(?:\.\d+)*)")
TAIL_LINES = 30


class SyncError(Exception):
    """A refusal or a failed step; the message says what was left as it was."""


# --- processes ----------------------------------------------------------------------------

def tail(text: str, lines: int = TAIL_LINES) -> str:
    return "\n".join(text.strip().splitlines()[-lines:])


def run(args: list[str], cwd: Path, check: bool = True,
        env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env)
    except OSError as exc:
        raise SyncError(f"cannot run {args[0]}: {exc}") from None
    if check and result.returncode:
        raise SyncError(f"{shlex.join(args)} failed:\n{tail(result.stdout + result.stderr)}")
    return result


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return run(["git", *args], root, check)


def value(root: Path, *args: str) -> str:
    return git(root, *args).stdout.strip()


def display(command: list[str]) -> str:
    shown = ["python3", *command[1:]] if command and command[0] == sys.executable else command
    return shlex.join(shown)


# --- pin ----------------------------------------------------------------------------------

def normalize_prefix(raw: str) -> str:
    path = PurePosixPath(raw.strip().strip("/"))
    if not raw.strip() or path.is_absolute() or ".." in path.parts or path.as_posix() == ".":
        raise SyncError(f"invalid core prefix: {raw!r} (expected a relative path)")
    return path.as_posix()


def argument(name: str, raw: str) -> str:
    """A repository URL, ref or branch that reaches Git as a positional argument."""
    if not raw.strip() or raw.startswith("-"):
        raise SyncError(f"invalid {name}: {raw!r}")
    return raw


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def config_path(root: Path) -> Path:
    return project.overlay_dir(root) / "project.json"


def read_pin(root: Path) -> dict[str, str]:
    core = project.load(root, "project.json", {}).get("core")
    shown = relative(root, config_path(root))
    if not isinstance(core, dict):
        raise SyncError(f"no core pin in {shown}; run `harness_sync.py install` first")
    for key in ("repo", "ref"):
        if not isinstance(core.get(key), str):
            raise SyncError(f"{shown}: core.{key} must be a string")
        argument(f"core.{key} in {shown}", core[key])
    prefix = core.get("prefix", DEFAULT_PREFIX)
    if not isinstance(prefix, str):
        raise SyncError(f"{shown}: core.prefix must be a string")
    return {"repo": core["repo"], "ref": core["ref"], "prefix": normalize_prefix(prefix)}


def write_pin(root: Path, **updates: str) -> Path:
    path = config_path(root)
    data = project.load(root, "project.json", {})
    core = data.get("core") if isinstance(data.get("core"), dict) else {}
    data["core"] = {**core, **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --- repository state ---------------------------------------------------------------------

def under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def porcelain(root: Path, *paths: str) -> list[tuple[str, str]]:
    """(XY, path) for every changed or untracked file, rename sources skipped."""
    raw = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all",
              "--", *paths).stdout
    items = raw.split("\0")
    entries = []
    index = 0
    while index < len(items):
        item = items[index]
        index += 1
        if len(item) < 4:
            continue
        code, path = item[:2], item[3:]
        entries.append((code, path))
        if code[0] in "RC":
            index += 1  # the next field is the rename or copy source
    return entries


def in_merge(root: Path) -> bool:
    return git(root, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0


def refuse_dirty(root: Path, prefix: str) -> None:
    """Tracked changes anywhere, untracked files inside the core, or a merge in progress."""
    blocked = [path for code, path in porcelain(root) if code != "??" or under(path, prefix)]
    if in_merge(root):
        blocked.insert(0, "(a merge is in progress)")
    if blocked:
        listed = "\n  ".join(blocked[:20]) + ("\n  …" if len(blocked) > 20 else "")
        raise SyncError("the working tree has uncommitted changes; commit or stash them "
                        f"first:\n  {listed}")


def has_head(root: Path) -> bool:
    return git(root, "rev-parse", "-q", "--verify", "HEAD", check=False).returncode == 0


def prefix_tree(root: Path, prefix: str) -> str:
    result = git(root, "rev-parse", "-q", "--verify", f"HEAD:{prefix}", check=False)
    if result.returncode:
        raise SyncError(f"{prefix} is not in HEAD; run `harness_sync.py install` first")
    return result.stdout.strip()


# --- upstream -----------------------------------------------------------------------------

def latest_tag(tags: list[str]) -> str | None:
    """The highest plain version tag (v1.2.3 or 1.2); anything else is ignored."""
    versions = []
    for tag in tags:
        match = VERSION.fullmatch(tag)
        if match:
            versions.append((tuple(int(part) for part in match.group(1).split(".")), tag))
    return max(versions)[1] if versions else None


def remote_tags(root: Path, repo: str) -> list[str]:
    listing = value(root, "ls-remote", "--tags", "--refs", repo)
    return [line.split("\trefs/tags/", 1)[1] for line in listing.splitlines()
            if "\trefs/tags/" in line]


def repo_slug(url: str) -> str:
    """owner/name of a GitHub-style URL, scp-like address or path (its last two parts)."""
    trimmed = url.strip().rstrip("/")
    trimmed = trimmed[:-4] if trimmed.endswith(".git") else trimmed
    parts = [part for part in re.split(r"[/:]", trimmed) if part]
    if len(parts) < 2:
        raise SyncError(f"cannot derive owner/name from {url!r}; pass --gh-repo OWNER/NAME")
    return f"{parts[-2]}/{parts[-1]}"


def pinned_tree(root: Path, repo: str, ref: str) -> str:
    """Fetch the pinned ref into a temporary ref and return its tree; the ref is removed."""
    temp = f"refs/harness-sync/fetch-{os.getpid()}"
    try:
        git(root, "fetch", "--quiet", "--no-tags", "--no-write-fetch-head", repo,
            f"+{ref}:{temp}")
        return value(root, "rev-parse", "--verify", f"{temp}^{{tree}}")
    finally:
        git(root, "update-ref", "-d", temp, check=False)


def tree_diff(root: Path, old: str, new: str) -> list[tuple[str, str]]:
    raw = git(root, "diff", "--name-status", "--no-renames", "-z", old, new).stdout
    fields = [field for field in raw.split("\0") if field]
    return list(zip(fields[0::2], fields[1::2]))


# --- status -------------------------------------------------------------------------------

def inspect(root: Path, pin: dict[str, str], with_latest: bool) -> dict:
    prefix = pin["prefix"]
    head = prefix_tree(root, prefix)
    pinned = pinned_tree(root, pin["repo"], pin["ref"])
    changes = [{"path": path, "status": status, "committed": True}
               for status, path in (tree_diff(root, pinned, head) if pinned != head else [])]
    for code, path in porcelain(root, prefix):
        changes.append({"path": path[len(prefix) + 1:] if under(path, prefix) else path,
                        "status": code.strip(), "committed": False})
    report = {"repo": pin["repo"], "prefix": prefix, "pinned": pin["ref"], "latest": None,
              "update_available": False, "local_changes": changes, "clean": not changes}
    if with_latest:
        try:
            report["latest"] = latest_tag(remote_tags(root, pin["repo"]))
        except SyncError as exc:
            report["latest_error"] = tail(str(exc), 3)
        report["update_available"] = (report["latest"] is not None
                                      and report["latest"] != pin["ref"])
    return report


def change_lines(changes: list[dict]) -> list[str]:
    return [f"  {change['status']:<2} {change['path']}"
            + ("" if change["committed"] else "  (uncommitted)") for change in changes]


def cmd_status(root: Path, args: argparse.Namespace) -> int:
    pin = read_pin(root)
    prefix = pin["prefix"]
    report = inspect(root, pin, with_latest=not args.strict)
    tool = f"python3 {prefix}/scripts/harness_sync.py"
    overlay = relative(root, project.overlay_dir(root))
    if args.json:
        print(json.dumps(report, indent=2))
    elif not args.strict:
        print(f"core     {prefix}  from {pin['repo']}")
        print(f"pinned   {pin['ref']}")
        if report["latest"] is None:
            print("latest   unknown" + (f" ({report['latest_error']})"
                                         if "latest_error" in report else " (no version tags)"))
        elif report["update_available"]:
            print(f"latest   {report['latest']}  (update: {tool} pull {report['latest']})")
        else:
            print(f"latest   {report['latest']}  (up to date)")
        if report["clean"]:
            print(f"local    clean: {prefix} matches {pin['ref']}")
        else:
            print(f"local    {len(report['local_changes'])} path(s) differ from {pin['ref']}:")
            print("\n".join(change_lines(report["local_changes"])))
            print(f"hint: upstream them with `{tool} propose --branch NAME`, or move "
                  f"project-specific changes into the overlay ({overlay}/)")
    if report["clean"]:
        if args.strict and not args.json:
            print(f"{prefix} matches its pin {pin['ref']}.")
        return 0
    if args.strict:
        print(f"harness-sync: {prefix} differs from its pinned ref {pin['ref']} "
              f"({len(report['local_changes'])} path(s)):", file=sys.stderr)
        print("\n".join(change_lines(report["local_changes"])), file=sys.stderr)
        print(f"The core is vendored and must match its pin. Upstream the change with "
              f"`{tool} propose --branch NAME` and pull the release that contains it, or move "
              f"it into the overlay ({overlay}/).", file=sys.stderr)
    return 1


# --- install ------------------------------------------------------------------------------

def cmd_install(root: Path, args: argparse.Namespace) -> int:
    prefix = normalize_prefix(args.prefix)
    argument("--repo", args.repo)
    argument("--ref", args.ref)
    if not has_head(root):
        raise SyncError("the repository has no commits yet; commit something first")
    refuse_dirty(root, prefix)
    exists = git(root, "cat-file", "-e", f"HEAD:{prefix}", check=False).returncode == 0
    if exists or (root / prefix).exists():
        raise SyncError(f"{prefix} already exists; update it with `pull`")
    result = git(root, "subtree", "add", f"--prefix={prefix}", "-m",
                 f"Add harness core {args.ref} at {prefix}", args.repo, args.ref, "--squash",
                 check=False)
    if result.returncode:
        raise SyncError(f"git subtree add failed; nothing was pinned:\n"
                        f"{tail(result.stdout + result.stderr)}")
    path = write_pin(root, repo=args.repo, ref=args.ref, prefix=prefix)
    shown = relative(root, path)
    print(f"Installed {args.repo} {args.ref} at {prefix} "
          f"(commit {value(root, 'rev-parse', '--short', 'HEAD')}).")
    print(f"Pinned in {shown} (not committed).")
    print("Next steps:")
    print(f"  git add {shlex.quote(shown)} && git commit -m 'chore(harness): pin core {args.ref}'")
    for _, command, _ in check_commands(root, prefix):
        print(f"  {display(command)}")
    print(f"  python3 {prefix}/scripts/harness_sync.py status")
    return 0


# --- pull ---------------------------------------------------------------------------------

def check_commands(root: Path, prefix: str) -> list[tuple[str, list[str], Path]]:
    """(name, command, path that must exist) for the checks after an update."""
    unit = [sys.executable, "-m", "unittest", "discover", "-p", "test_*.py", "-s"]
    commands = [
        ("render adapters", [sys.executable, f"{prefix}/scripts/render_adapters.py",
                             "--root", str(root)], root / prefix / "scripts/render_adapters.py"),
        ("core tests", [*unit, f"{prefix}/tests"], root / prefix / "tests"),
    ]
    overlay_tests = project.overlay_dir(root) / "tests"
    if overlay_tests.is_dir():
        commands.append(("project tests", [*unit, relative(root, overlay_tests)], overlay_tests))
    return commands


def run_checks(root: Path, prefix: str) -> list[tuple[str, str, bool, str]]:
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    results = []
    for name, command, needed in check_commands(root, prefix):
        if not needed.exists():
            results.append((name, display(command), False, f"missing: {relative(root, needed)}"))
            continue
        result = run(command, root, check=False, env=env)
        results.append((name, display(command), result.returncode == 0,
                        tail(result.stdout + result.stderr)))
    return results


def subtree_pull(checkout: Path, prefix: str, repo: str, ref: str):
    return git(checkout, "subtree", "pull", f"--prefix={prefix}", "-m",
               f"Update harness core to {ref}", repo, ref, "--squash", check=False)


def report_conflict(root: Path, pin: dict[str, str], prefix: str, ref: str) -> int:
    shown = relative(root, config_path(root))
    conflicts = [path for path in
                 git(root, "diff", "--name-only", "-z", "--diff-filter=U").stdout.split("\0")
                 if path]
    print(f"harness-sync: updating {prefix} {pin['ref']} → {ref} stopped on conflicts:",
          file=sys.stderr)
    print("\n".join(f"  {path}" for path in conflicts) or "  (see git status)", file=sys.stderr)
    print("Nothing was reset. Either resolve them, `git add` the files and `git commit`, "
          f"then set core.ref to {ref} in {shown} and run the checks:", file=sys.stderr)
    for _, command, _ in check_commands(root, prefix):
        print(f"  {display(command)}", file=sys.stderr)
    print("or abandon the update: git merge --abort", file=sys.stderr)
    return 1


def cmd_pull(root: Path, args: argparse.Namespace) -> int:
    """Merge and check the update in a scratch worktree; the branch moves only when green."""
    pin = read_pin(root)
    prefix = pin["prefix"]
    prefix_tree(root, prefix)
    refuse_dirty(root, prefix)
    ref = argument("REF", args.ref) if args.ref else latest_tag(remote_tags(root, pin["repo"]))
    if not ref:
        raise SyncError(f"{pin['repo']} has no version tags; name the REF to pull")
    shown = relative(root, config_path(root))
    overlay = project.overlay_dir(root)
    if not overlay.is_relative_to(root):
        raise SyncError(f"the overlay {overlay} is outside {root}; pull checks a copy of the "
                        "checkout, so HARNESS_PROJECT must point inside it")
    # Name the overlay relative to the checkout so the scratch copy resolves its own overlay.
    os.environ["HARNESS_PROJECT"] = overlay.relative_to(root).as_posix()
    before = value(root, "rev-parse", "HEAD")
    holder = Path(tempfile.mkdtemp(prefix="harness-sync-"))
    scratch = holder / "update"
    git(root, "worktree", "add", "--quiet", "--detach", str(scratch), "HEAD")
    try:
        result = subtree_pull(scratch, prefix, pin["repo"], ref)
        if result.returncode:
            conflicted = in_merge(scratch)
            git(scratch, "merge", "--abort", check=False)
            if not conflicted:
                raise SyncError(f"git subtree pull failed; nothing changed:\n"
                                f"{tail(result.stdout + result.stderr)}")
            conflict = True
        else:
            conflict = False
            merged = value(scratch, "rev-parse", "HEAD")
            if merged != before:
                write_pin(scratch, ref=ref)
                results = run_checks(scratch, prefix)
    finally:
        git(root, "worktree", "remove", "--force", str(scratch), check=False)
        shutil.rmtree(holder, ignore_errors=True)
        git(root, "worktree", "prune", check=False)
    if conflict:
        # Redo the merge here, where the user can resolve it; nothing gets committed.
        if subtree_pull(root, prefix, pin["repo"], ref).returncode and in_merge(root):
            return report_conflict(root, pin, prefix, ref)
        raise SyncError("the update conflicted in the scratch worktree but not here; "
                        "check `git status` before retrying")
    if merged == before:
        if pin["ref"] != ref:
            write_pin(root, ref=ref)
        print(f"{prefix} is already at {ref}; nothing to update.")
        return 0
    for name, command, passed, output in results:
        print(f"{'ok  ' if passed else 'FAIL'}  {name}: {command}")
        if not passed:
            print("\n".join(f"      {line}" for line in output.splitlines()))
    if not all(passed for _, _, passed, _ in results):
        print(f"harness-sync: the update of {prefix} to {ref} fails its checks (above), so "
              f"nothing changed: HEAD is still {before[:12]} and core.ref {pin['ref']}. Fix the "
              f"overlay or the core (upstream with `propose`) and pull again; the tested merge "
              f"was {merged[:12]}.", file=sys.stderr)
        return 1
    git(root, "merge", "--ff-only", "--quiet", merged)
    write_pin(root, ref=ref)
    name, command, _ = check_commands(root, prefix)[0]
    rendered = run(command, root, check=False, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    print(f"{'ok  ' if rendered.returncode == 0 else 'FAIL'}  {name} here: {display(command)}")
    print(f"Updated {prefix} {pin['ref']} → {ref} (merge {merged[:7]}); core.ref set in {shown}.")
    if rendered.returncode:
        print(tail(rendered.stdout + rendered.stderr), file=sys.stderr)
        return 1
    print(f"Review `git status`, then commit {shown} and the regenerated adapters.")
    return 0


# --- propose ------------------------------------------------------------------------------

def last_squash(root: Path, prefix: str) -> str:
    """The newest `git subtree --squash` commit for the prefix: its tree is the upstream tree."""
    raw = git(root, "log", "--grep=^git-subtree-dir:", "--format=%H%x00%B%x01", "HEAD").stdout
    for entry in raw.split("\x01"):
        sha, _, body = entry.strip().partition("\0")
        lines = body.splitlines()
        if not sha or not lines or not lines[0].startswith("Squashed '"):
            continue
        dirs = [line.split(":", 1)[1].strip().rstrip("/") for line in lines
                if line.startswith("git-subtree-dir:")]
        if prefix in dirs:
            return sha
    raise SyncError(f"no `git subtree --squash` commit for {prefix} in HEAD; propose needs a "
                    "core added with `harness_sync.py install`")


def identity(root: Path) -> list[str]:
    flags = []
    for key in ("user.name", "user.email"):
        result = git(root, "config", "--get", key, check=False)
        if result.returncode == 0 and result.stdout.strip():
            flags += ["-c", f"{key}={result.stdout.strip()}"]
    return flags


def cmd_propose(root: Path, args: argparse.Namespace) -> int:
    if args.feedback_file:
        import feedback
        try:
            draft_meta, draft = feedback.read(Path(args.feedback_file))
            if draft_meta["status"] != "draft":
                raise SyncError("feedback attachment must have draft status; nothing published")
            draft = feedback.sanitize(draft, root)
        except feedback.FeedbackError as exc:
            raise SyncError(str(exc)) from None
        except (project.OverlayError, feedback.task_state.StateError, OSError,
                ValueError, KeyError, TypeError):
            raise SyncError("invalid feedback attachment or privacy settings; nothing published") from None
    pin = read_pin(root)
    prefix = pin["prefix"]
    branch = argument("--branch", args.branch)
    if git(root, "check-ref-format", "--branch", branch, check=False).returncode:
        raise SyncError(f"invalid branch name: {branch!r}")
    head = prefix_tree(root, prefix)
    pending = [path for _, path in porcelain(root, prefix)]
    if pending:
        raise SyncError("uncommitted changes inside the core are not proposed; commit or "
                        "stash them first:\n  " + "\n  ".join(pending[:20]))
    squash = last_squash(root, prefix)
    base = value(root, "rev-parse", f"{squash}^{{tree}}")
    if base == head:
        print(f"Nothing to propose: {prefix} matches the last sync ({pin['ref']}).")
        return 0
    files = tree_diff(root, base, head)
    commits = [line for line in git(root, "log", "--no-merges", "--format=%s",
                                    f"{squash}..HEAD", "--", prefix).stdout.splitlines() if line]
    title = args.title or (commits[0] if commits else "Update the harness core")
    slug = args.gh_repo or repo_slug(pin["repo"])
    patch = subprocess.run(
        ["git", "diff", "--binary", "--full-index", "--no-color", "--no-ext-diff",
         "--no-textconv", "--src-prefix=a/", "--dst-prefix=b/", base, head],
        cwd=root, capture_output=True)
    if patch.returncode:
        raise SyncError(f"git diff failed: {os.fsdecode(patch.stderr).strip()}")
    if args.yes and not shutil.which("gh"):
        raise SyncError("gh is not on PATH; install it, or run without --yes for the commands")

    work = Path(tempfile.mkdtemp(prefix="harness-propose-"))
    clone = work / "core"
    body_file = work / "PR_BODY.md"
    try:
        git(work, "clone", "--quiet", pin["repo"], str(clone))
        if git(clone, "rev-parse", "-q", "--verify", f"refs/remotes/origin/{branch}",
               check=False).returncode == 0:
            raise SyncError(f"branch {branch} already exists in {pin['repo']}; choose another")
        upstream = git(clone, "rev-parse", "-q", "--verify", f"{pin['ref']}^{{tree}}",
                       check=False)
        if upstream.returncode:
            raise SyncError(f"{pin['ref']} is not in {pin['repo']}")
        if upstream.stdout.strip() != base:
            raise SyncError(f"core.ref {pin['ref']} is not the tree {prefix} was last synced "
                            f"from (squash {squash[:12]}); fix core.ref or pull first")
        git(clone, "checkout", "--quiet", "-b", branch, f"{pin['ref']}^{{commit}}")
        (work / "core.patch").write_bytes(patch.stdout)
        git(clone, "apply", "--index", "--whitespace=nowarn", str(work / "core.patch"))
        body = "\n".join([
            "Proposed from a downstream project with `harness_sync.py propose`.",
            "",
            f"Base: {pin['ref']} ({value(clone, 'rev-parse', '--short', 'HEAD')})",
            "",
            "Commits:",
            *[f"- {subject}" for subject in commits or ["(no commit subjects found)"]],
            "",
            "Files:",
            *[f"- {status} {path}" for status, path in files],
            "",
        ])
        if args.feedback_file:
            # A feedback attachment is local evidence, never a link to a product path.
            try:
                body += "\n[Reviewed feedback draft](#harness-feedback-draft)\n\n## Harness feedback draft\n\n" + draft
                body = feedback.sanitize(body, root)
                title = feedback.sanitize(title, root)
            except feedback.FeedbackError as exc:
                raise SyncError(str(exc)) from None
            except (project.OverlayError, feedback.task_state.StateError, OSError,
                    ValueError, KeyError, TypeError):
                raise SyncError("invalid feedback attachment or privacy settings; nothing published") from None
        body_file.write_text(body, encoding="utf-8")
        (work / "COMMIT_MSG").write_text(f"{title}\n\n{body}", encoding="utf-8")
        git(clone, *identity(root), "commit", "--quiet", "--no-verify",
            "-F", str(work / "COMMIT_MSG"))
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise

    push = ["git", "-C", str(clone), "push", "origin", branch]
    pr = ["gh", "pr", "create", "--repo", slug, "--base", args.base, "--head", branch,
          "--title", title, "--body-file", str(body_file)]
    print(f"Prepared branch {branch} of {pin['repo']} on {pin['ref']}: "
          f"{len(files)} file(s), {len(commits)} commit(s) squashed into one.")
    print(f"Clone: {clone}")
    if args.feedback_file:
        print(f"Reviewed PR title: {title}\n\n{body}", flush=True)
    if not args.yes:
        print("Nothing was published. Review the branch, then run:")
        print(f"  {shlex.join(push)}")
        print(f"  {shlex.join(pr)}")
        print("or rerun with --yes to run both.")
        return 0
    result = run(push, work, check=False)
    if result.returncode:
        raise SyncError(f"push failed; nothing was published (clone kept at {clone}):\n"
                        f"{tail(result.stdout + result.stderr)}")
    result = run(pr, clone, check=False)
    if result.returncode:
        raise SyncError(f"branch {branch} was pushed but `gh pr create` failed (clone kept at "
                        f"{clone}); retry:\n  {shlex.join(pr)}\n"
                        f"{tail(result.stdout + result.stderr)}")
    print(f"Pushed {branch} to {pin['repo']} and opened the pull request:")
    print(result.stdout.strip())
    if args.feedback_file:
        url = result.stdout.strip()
        if not re.fullmatch(r"https://github\.com/pharosone/dev-harness/pull/\d+", url):
            raise SyncError("PR opened but its URL was unexpected; verify it before updating the feedback draft")
        feedback.write(Path(args.feedback_file),
                       {**draft_meta, "status": "sent", "offered": True, "url": url},
                       draft)
    shutil.rmtree(work, ignore_errors=True)
    return 0


# --- entry point --------------------------------------------------------------------------

def parser() -> argparse.ArgumentParser:
    main = argparse.ArgumentParser(description=__doc__,
                                   formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = main.add_subparsers(dest="command", required=True)
    install = commands.add_parser("install", help="add the core as a squashed subtree")
    install.add_argument("--repo", required=True, help="core repository URL")
    install.add_argument("--ref", required=True, help="tag (or ref) to install")
    install.add_argument("--prefix", default=DEFAULT_PREFIX)
    status = commands.add_parser("status", help="pin, latest tag and local edits in the core")
    status.add_argument("--json", action="store_true")
    status.add_argument("--strict", action="store_true",
                        help="CI check: fail when the core differs from its pin")
    pull = commands.add_parser("pull", help="update the core, re-render, run the tests")
    pull.add_argument("ref", nargs="?", help="ref to pull (default: latest version tag)")
    propose = commands.add_parser("propose", help="the core's local edits as a PR upstream")
    propose.add_argument("--branch", required=True)
    propose.add_argument("--title")
    propose.add_argument("--feedback-file", help="attach a reviewed feedback draft; sanitize PR text")
    propose.add_argument("--base", default="main", help="PR base branch (default: main)")
    propose.add_argument("--gh-repo", help="OWNER/NAME for gh (default: from the core URL)")
    propose.add_argument("--yes", action="store_true", help="push the branch and open the PR")
    return main


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    handlers = {"install": cmd_install, "status": cmd_status, "pull": cmd_pull,
                "propose": cmd_propose}
    try:
        root = project.repo_root()
        return handlers[args.command](root, args)
    except (SyncError, project.OverlayError, OSError) as exc:
        print(f"harness-sync: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
