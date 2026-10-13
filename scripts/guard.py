#!/usr/bin/env python3
"""PreToolUse guard for Claude Code and Codex: deny or ask before a risky tool call runs.

Usage: guard.py --runtime claude|codex < hook-event.json

Shell commands (tool_input.command or Codex cmd, a string or an argv list; Codex workdir
sets the directory). Every part of a chain (; && || | & newlines, subshells), every
$(...) and `...` body (also inside double quotes), every `bash -c` / `sh -c` / `eval` /
here-string payload, the obvious `python -c` subprocess forms, and the command behind
wrappers (sudo, env [-S], nohup, nice, timeout, xargs, quiet.py --) is inspected:
  git push, send-pack, http-push, subtree push, an alias resolving to one (-c alias.*,
  configured aliases), a verb hidden in $... or fed by xargs
                                              ask on claude (the user approves), deny on codex
  git add|stage -A|--all|-u|--update (and abbreviations), whole-tree pathspecs (., :/, *,
  **, pathspec magic, only ./.., the repo root, $...), --pathspec-from-file
                                              deny: stage explicit paths
  git commit -a|--all|-am..., or with a whole-tree pathspec       deny: stage explicit paths
  git clean without -n, git stash -u|-a       deny: they remove untracked personal files
  helm/kubectl mutations (not kubectl rollout status|history), gh workflow run (except a
  file guard.allow_workflows lists), railway up|deploy|redeploy, fly|flyctl deploy;
  ssh/scp/sftp/rsync as a deploy user, to a deploy host, or to an IPv4 host named in a
  deploy host file (read at decision time); docker compose with a production compose
  file                                        deny: deploys go through CD
  file readers on .env / .env.* (not .env.example, any case, symlinks too; guard.env_exempt
  below), redirects to or from them, and tee/cp/mv/ln/dd/rm/truncate/sed -i on them    deny
  shell writes (> >> tee cp mv ln dd sed -i truncate) into a generated directory, and
  writes or removals of personal files                            deny
  writes or removals of the guard or its configuration (see below),
  git config core.worktree|core.hooksPath changes                 ask on claude, deny on codex
Files (Edit, Write, MultiEdit, NotebookEdit, Read, Grep path and glob; Codex apply_patch,
also free-form or via shell); names compare case-insensitively and through symlinks:
  .env / .env.* (not .env.example; guard.env_exempt below) edits and reads,
  settings.local.json edits                                                   deny
  edits under a generated directory                                           deny
  edits of existing untracked files (ignored ones too) under .claude/agents|skills,
  .codex/agents, .agents/skills (personal files)                              deny
  a test file whose resulting content gains skip markers (.skip(, xit(, t.Skip(...)  deny
  the guard itself (its path in the checkout that holds the core, located from this file,
  and harness/core/scripts/guard.py), the overlay project.json (harness/project/project.json
  and the overlay in use), .claude/settings.json, .codex/hooks.json, .codex/config.toml
                                              ask on claude, deny on codex

Project rules come from the "guard" object of the overlay's project.json (project.py:
$HARNESS_PROJECT, else <repo>/harness/project), read from the command's repository and
from the checkout that holds this core. Every key is optional and every list adds to the
core rules. Three keys loosen a core rule, so only the overlay of the repository they apply
to counts; a separate core checkout's overlay never loosens another repository's rules.
The sole permission opt-in is guard.push=allow: on Claude only, one literal
git push / git subtree push to verified non-force, non-default destinations may run without
a per-push confirmation. Default ask and Codex deny remain. Force/lease, mirror, deletions,
main/master/cached remote HEAD, hidden verbs, compound commands and uncertain routing ask.
The command repository must opt in; a separate core checkout cannot authorize its pushes.
Config edits still use the SELF rule. Missing cached remote HEAD or unreadable metadata
asks; use git remote set-head <remote> -a to initialize it (no network probes in the guard).
An overlay cannot remove any other core rule.
  push               "ask" (default) or "allow", owner-authorized Claude orchestrator only
  env_exempt         repository-relative globs of committed, secret-free env files, e.g.
                     ["web/.env.*"]; * stays within one path segment, no .. or **, the last
                     segment starts with .env. and never matches .env itself. A file is
                     exempt only in the command's repository, while that exact path is a
                     file in HEAD and one plain index entry (not staged-only, conflicted,
                     skip-worktree or assume-unchanged); the file a symlink resolves to must
                     be as well (and named, if it is an env file), a `..` after a symlink
                     must not lead elsewhere, and a path with * ? [ is never exempt; so a
                     staged or untracked .env.local or a symlink to .env stays denied
  allow_workflows    workflow files [OWNER/REPO:]FILE.yml that `gh workflow run` may dispatch
                     without the deploy rule, e.g. ["env-power.yml", "acme/ops:plan.yml"]
                     (the prefix must equal -R/--repo; none means no -R): Claude only, one
                     literal gh command with only -f/-F/--ref/-R options and no -F @file;
                     every other form keeps the deploy rule. Repository scripts are not
                     deploy commands to the guard, so vetted wrappers need no entry
  deploy_hosts       host names or IPv4 addresses, e.g. ["deploy.example.com"]: an
                     ssh/scp/sftp/rsync argument naming one, or a subdomain of a name, is a
                     deploy (names compare case-insensitively, label by label)
  deploy_users       remote users that make an ssh-family call a deploy (user@, -l USER,
                     -o User=USER); core: deploy, root
  deploy_host_files  repository-relative globs, or directories ending in / (read
                     recursively), whose IPv4 literals are deploy hosts, e.g.
                     ["infra/*.hosts"]; core: .github/workflows/*deploy*, deploy/
  prod_compose       basename globs of production compose files, e.g. ["compose.prod*"];
                     core: none
  generated          directories of generated output, e.g. [{"path": "gen/db/", "hint":
                     "run make gen instead of editing it by hand."}]; the path matches as
                     a run of path segments anywhere, any case; the deny reason is
                     "<path> is generated output; <hint>" (hint optional, one sentence)
No overlay means the core rules alone. An unreadable overlay, a wrong type, an invalid
entry or an unknown key prints a note to stderr and is skipped; the core rules still apply.

A decision prints PreToolUse hookSpecificOutput JSON; no decision prints nothing.
Always exits 0: unreadable input or an internal error prints a note to stderr and allows
the call, because a broken guard must not brick a session. A guardrail, not a boundary:
it matches clear forms and does not try to parse arbitrary shell.
"""

import argparse
from fnmatch import fnmatchcase
import functools
import json
import os
from pathlib import Path
import re
import posixpath
import shlex
import subprocess
import sys
from typing import NamedTuple

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
import project  # noqa: E402

ENV = ("deny", ".env files hold secrets, so reading or changing them is blocked; "
              "use .env.example or ask the user for the value.")
LOCAL_SETTINGS = ("deny", "Editing settings.local.json is blocked because it is the user's "
                         "personal configuration; ask the user to change it.")
PERSONAL = ("deny", "This is an existing untracked personal file, not part of the shared "
                   "harness; leave it alone or ask the user.")
SKIP = ("deny", "Skipping a test needs the user's decision; raise a tripwire flag "
               "(tripwire.py flag TASK UNIT REASON) instead of adding a skip.")
ADD = ("deny", "Broad staging (git add -A, -u or a whole-tree pathspec) is blocked; "
              "stage explicit paths.")
COMMIT_ALL = ("deny", "git commit -a or a whole-tree pathspec commits every tracked change; "
                     "stage explicit paths with git add and commit without them.")
CLEAN = ("deny", "git clean deletes untracked files, personal agents and skills among them; "
                "delete explicit paths or preview with git clean -n.")
STASH = ("deny", "git stash -u or -a moves untracked personal files into the stash every "
                "worktree shares; stash tracked changes only.")
DEPLOY = ("deny", "Deploy commands are blocked for agents because deploys go through CD.")
PUSH = {
    "claude": ("ask", "The user approves each git push, so confirm this push only if the user "
                      "asked for it."),
    "codex": ("deny", "git push is blocked for agents; leave the push to the user."),
}
GIT_DYNAMIC = {
    "claude": ("ask", "This git command hides its verb behind an alias, variable or "
                      "substitution and may push, so the user approves it; confirm only if "
                      "the user asked for it."),
    "codex": ("deny", "A git command whose verb hides behind an alias, variable or "
                      "substitution may push, so it is blocked for agents; spell the verb out."),
}
SELF = {
    "claude": ("ask", "This changes the guard or its configuration, so the user approves it; "
                      "confirm only if the user asked for this change."),
    "codex": ("deny", "Changing the guard or its configuration is blocked for agents; ask the "
                      "user to make this change."),
}

WRAPPERS = {  # wrapper -> its options that take a separate value
    "sudo": {"-u", "-g", "-h", "-p", "-C", "-U", "-r", "-t", "-T", "-D", "-R", "--user",
             "--group", "--host", "--prompt", "--close-from", "--other-user", "--role",
             "--type", "--command-timeout", "--chdir", "--chroot"},
    "env": {"-u", "--unset", "-C", "--chdir"},
    "nice": {"-n", "--adjustment"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "xargs": {"-I", "-L", "-n", "-P", "-s", "-d", "-E", "-a", "--replace", "--max-lines",
              "--max-args", "--max-procs", "--max-chars", "--delimiter", "--eof", "--arg-file",
              "--process-slot-var"},
    "exec": {"-a"},
    "time": {"-f", "-o", "--format", "--output"},
    "command": set(), "builtin": set(), "nohup": set(),
}
KEYWORDS = {"if", "then", "else", "elif", "while", "until", "do", "!", "{"}
SHELLS = {"bash", "sh", "zsh", "dash", "ksh", "csh", "tcsh", "fish"}
PYTHON = re.compile(r"python[\d.]*")
READERS = {"cat", "less", "more", "head", "tail", "bat", "grep", "egrep", "fgrep", "rg", "sed",
           "awk", "gawk", "source", ".", "tac", "nl", "xxd", "od", "strings", "base64", "cut",
           "sort", "diff"}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")

GIT_VALUED = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env",
              "--super-prefix", "--attr-source"}
GIT_COMMANDS = {  # builtins and external git-* commands: an alias cannot shadow them
    "add", "am", "annotate", "apply", "archive", "bisect", "blame", "branch", "bundle",
    "cat-file", "check-attr", "check-ignore", "check-ref-format", "checkout", "cherry",
    "cherry-pick", "clean", "clone", "commit", "commit-tree", "config", "count-objects",
    "describe", "diff", "diff-files", "diff-index", "diff-tree", "difftool", "fetch",
    "for-each-ref", "format-patch", "fsck", "gc", "grep", "hash-object", "help", "http-push",
    "init", "lfs", "log", "ls-files", "ls-remote", "ls-tree", "maintenance", "merge",
    "merge-base", "merge-file", "mergetool", "mktree", "mv", "name-rev", "notes", "prune",
    "pull", "push", "range-diff", "read-tree", "rebase", "reflog", "remote", "repack",
    "replace", "rerere", "reset", "restore", "rev-list", "rev-parse", "revert", "rm",
    "send-pack", "shortlog", "show", "show-branch", "show-ref", "sparse-checkout", "stage",
    "stash", "status", "submodule", "subtree", "switch", "symbolic-ref", "tag",
    "update-index", "update-ref", "var", "verify-commit", "version", "whatchanged",
    "worktree", "write-tree",
}
PUSH_VERBS = {"push", "send-pack", "http-push"}
BROAD_PATHSPECS = {".", "./", ":/", ":(top)", "*"}
COMMIT_VALUED = {"--message", "--file", "--author", "--date", "--reuse-message",
                 "--reedit-message", "--fixup", "--squash", "--template", "--trailer",
                 "--cleanup"}
CONFIG_VALUED = {"-f", "--file", "--blob", "--type", "--default", "--comment", "--value"}
CONFIG_CHANGES = {"--unset", "--unset-all", "--add", "--replace-all", "--rename-section",
                  "--remove-section"}
CONFIG_READS = {"--get", "--get-all", "--get-regexp", "--get-urlmatch", "--get-color",
                "--get-colorbool", "-l", "--list"}
GUARDED_CONFIG = {"core.worktree", "core.hookspath"}
HELM_VERBS = {"install", "upgrade", "uninstall", "rollback", "delete", "del", "un"}
HELM_VALUED = {"-n", "--namespace", "--kube-context", "--kubeconfig", "--registry-config",
               "--repository-config", "--repository-cache"}
KUBECTL_VERBS = {"apply", "delete", "patch", "replace", "create", "scale", "rollout", "edit",
                 "set", "label", "annotate"}
KUBECTL_VALUED = {"-n", "--namespace", "--context", "--kubeconfig", "--cluster", "--user",
                  "-s", "--server", "--token", "--as"}
DOCKER_VALUED = {"-c", "--context", "-H", "--host", "--config", "-l", "--log-level"}
GH_VALUED = {"-R", "--repo"}
FLY_VALUED = {"-t", "--access-token", "-a", "--app", "-c", "--config"}
REMOTE_SHELLS = {"ssh", "scp", "sftp", "rsync"}
IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?!\.?\d)")

# Core rules; an overlay's "guard" lists add to them (see the module docstring).
CORE_RULES = {
    "deploy_hosts": (),
    "deploy_users": ("deploy", "root"),
    "deploy_host_files": (".github/workflows/*deploy*", "deploy/"),
    "prod_compose": (),
    "generated": (),
    "env_exempt": (),  # loosens the .env rule: the command repository's overlay only
    "allow_workflows": (),  # loosens the deploy rule: the command repository's overlay only
}
LOOSENING = ("env_exempt", "allow_workflows")  # list keys read from the own overlay only
WORKFLOW_ENTRY = re.compile(r"(?:[\w.-]+/[\w.-]+:)?[\w.-]+\.ya?ml", re.ASCII)
GH_RUN_VALUED = {"-f", "--raw-field", "-F", "--field", "-r", "--ref", "-R", "--repo"}
GENERATED_HINT = "edit its source and regenerate it instead of changing it by hand."
GUARD_FILE = Path(__file__).resolve()
VENDORED_GUARD = "harness/core/scripts/guard.py"
DEFAULT_OVERLAY = "harness/project/project.json"
HOOK_CONFIGS = (".claude/settings.json", ".codex/hooks.json", ".codex/config.toml")

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
READ_TOOLS = {"Read": "file_path", "Grep": "path"}
PERSONAL_DIRS = ("/.claude/agents/", "/.claude/skills/", "/.codex/agents/", "/.agents/skills/")
TEST_FILES = ("*_test.go", "*.test.ts", "*.test.tsx", "*.spec.ts", "*.spec.tsx", "*.test.js",
              "*.test.jsx", "*.spec.js", "*.spec.jsx", "*.test.mjs", "test_*.py", "*_test.py")
SKIP_MARKERS = re.compile(
    r"\.skip\(|(?<![\w$.])x(?:it|describe)\(|\b(?:it|test)\.todo\(|\bt\.Skip(?:f|Now)?\("
    r"|@unittest\.skip|\bpytest\.mark\.skip|\.skipTest\(")
PATCH_PATH = re.compile(r"\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)")
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?\s*(?:'([^']*)'|\"([^\"]*)\"|\\?([^\s;&|()<>'\"]+))")
DATA_READERS = {"cat", "tee", "git", "gh", "wc", "sort", "grep", "egrep", "fgrep", "rg", "sed",
                "awk", "head", "tail", "jq", "tr", "base64", "diff", "patch", "apply_patch"}
PY_EXEC = re.compile(r"\bsubprocess\b|\bos\.(?:system|popen|exec\w*|spawn\w*)|\bpty\.spawn")
PY_STRING = re.compile(r"""'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)\"""")
MAX_DEPTH = 5
READ_CAP = 4 << 20


# Project rules

def repo_root(start: Path) -> Path | None:
    for folder in (start, *start.parents):
        if (folder / ".git").exists():
            return folder
    return None


GUARD_CHECKOUT = repo_root(project.core_root())  # the checkout holding this core


def guard_path() -> str:
    """This guard relative to the checkout holding the core: scripts/guard.py in the core
    repository itself, harness/core/scripts/guard.py where a project vendors it."""
    if GUARD_CHECKOUT is not None and GUARD_FILE.is_relative_to(GUARD_CHECKOUT):
        return GUARD_FILE.relative_to(GUARD_CHECKOUT).as_posix()
    return VENDORED_GUARD


# The guard and every module it imports: a crash in one disables the guard (hooks fail open).
GUARD_MODULES = ("guard.py", "project.py")
SELF_FILES = tuple(dict.fromkeys(f"/{name}".casefold() for name in (
    *(posixpath.join(posixpath.dirname(base), module) for base in (guard_path(), VENDORED_GUARD)
      for module in GUARD_MODULES),
    DEFAULT_OVERLAY, *HOOK_CONFIGS)))
VENDORED_CORE = "/harness/core/"
CORE = {
    "claude": ("ask", "harness/core is the vendored core, never edited in place, so the user "
                      "approves this; prefer the overlay or harness_sync.py propose upstream."),
    "codex": ("deny", "harness/core is the vendored core and is not edited in place; put the "
                      "change in the overlay or ask the user to propose it upstream."),
}


class Rules(NamedTuple):
    deploy_hosts: tuple[str, ...]
    deploy_users: tuple[str, ...]
    deploy_host_files: tuple[str, ...]
    prod_compose: tuple[str, ...]
    generated: tuple[tuple[str, tuple[str, str]], ...]  # (/segment/, deny finding)
    env_exempt: tuple[str, ...]  # from the repository's own overlay only
    allow_workflows: tuple[str, ...]  # from the repository's own overlay only
    overlays: tuple[Path, ...]  # the project.json files these rules were read from
    push: str  # owner opt-in from the command repository; never authorizes Codex


class Context(NamedTuple):
    runtime: str
    cwd: Path
    autonomous: bool = True  # only a literal, single git or gh command may use an opt-in


def note(message: str) -> None:
    print(f"guard: {message}", file=sys.stderr)


def overlay_entry(key: str, value):
    """One validated entry of an overlay list, or None."""
    if key == "generated":
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            return None
        path, hint = value["path"].strip(), value.get("hint", GENERATED_HINT)
        if not path.strip("/") or not isinstance(hint, str) or not hint.strip():
            return None
        hint = hint.strip() if hint.strip().endswith(".") else hint.strip() + "."
        return ("/" + path.strip("/").casefold() + "/",
                ("deny", f"{path} is generated output; {hint}"))
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if key in ("deploy_hosts", "deploy_users") and any(c.isspace() for c in value):
        return None
    if key == "deploy_host_files" and (os.path.isabs(value) or ".." in Path(value).parts):
        return None
    if key == "env_exempt" and not env_exempt_glob(value):
        return None
    if key == "allow_workflows" and not WORKFLOW_ENTRY.fullmatch(value):
        return None
    return value


def env_exempt_glob(value: str) -> bool:
    """A repository-relative glob of env files that can never name .env itself."""
    parts = value.split("/")
    last = parts[-1].casefold()
    return (not os.path.isabs(value) and "\\" not in value and "**" not in value
            and all(p not in ("", ".", "..") for p in parts)
            and last.startswith(".env.") and not fnmatchcase(".env", last))


OVERLAYS: dict[Path, dict] = {}
COMMAND_REPO: Path | None = None  # realpath of the command's repository, set by decide()


def overlay(root: Path) -> tuple[Path, dict]:
    """The project.json that configures a checkout and the rule lists it adds, read once
    per overlay directory; every problem is a note and is skipped."""
    folder = project.overlay_dir(root)
    path = folder / "project.json"
    if folder in OVERLAYS:
        return path, OVERLAYS[folder]
    found = OVERLAYS[folder] = {}
    try:
        section = project.load(root, "project.json", {}).get("guard", {})
    except project.OverlayError as exc:  # names the file already
        note(f"{exc}; using the core rules only")
        return path, found
    except (OSError, ValueError) as exc:
        note(f"{path}: {exc}; using the core rules only")
        return path, found
    if not isinstance(section, dict):
        note(f'{path}: "guard" is not an object; using the core rules only')
        return path, found
    for key, values in section.items():
        if key == "push":
            if isinstance(values, str) and values in ("ask", "allow"):
                found[key] = values
            else:
                note(f"{path}: invalid guard.push; expected ask or allow; using core push rule")
        elif key not in CORE_RULES:
            note(f"{path}: unknown key guard.{key} ignored")
        elif not isinstance(values, list):
            note(f"{path}: guard.{key} is not a list; ignored")
        else:
            found[key] = []
            for value in values:
                entry = overlay_entry(key, value)
                if entry is None:
                    note(f"{path}: invalid guard.{key} entry {json.dumps(value)} ignored")
                else:
                    found[key].append(entry)
    return path, found


@functools.lru_cache(maxsize=None)
def rules_at(root: Path | None) -> Rules:
    merged = {key: list(values) for key, values in CORE_RULES.items()}
    roots = {}
    for base in (root, GUARD_CHECKOUT):
        if base is not None:
            roots.setdefault(project.overlay_dir(base), base)
    files = []
    push = "ask"
    for base in roots.values():
        path, found = overlay(base)
        files.append(path)
        for key, values in found.items():
            if key == "push":
                if base == root:
                    push = values
            elif key in LOOSENING:  # loosens a core rule: the repository's own overlay only
                if base == root:
                    merged[key] = list(values)
            else:
                merged[key] += [value for value in values if value not in merged[key]]
    return Rules(**{key: tuple(values) for key, values in merged.items()},
                 overlays=tuple(files), push=push)


def rules(start: Path) -> Rules:
    """The core rules plus the overlays of start's repository and of the guard's checkout."""
    return rules_at(repo_root(start))


# Shell commands

def closing(text: str, start: int) -> int:
    """Index of the ) that closes a $( opened just before start, or len(text)."""
    depth, i, quote = 1, start, ""
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\" and quote == '"':
                i += 1
            elif c == quote:
                quote = ""
        elif c == "\\":
            i += 1
        elif c in "'\"":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if not depth:
                return i
        i += 1
    return len(text)


def substitutions(command: str) -> tuple[str, list[str]]:
    """Cut $(...) and `...` bodies out of a command, also inside double quotes but not
    inside single quotes: the command with each replaced by a $SUB word, and the bodies."""
    out, bodies, i, quote = [], [], 0, ""
    while i < len(command):
        c = command[i]
        if quote == "'":
            quote = "" if c == "'" else quote
            out.append(c)
            i += 1
        elif c == "\\":
            out.append(command[i:i + 2])
            i += 2
        elif c == "'" and not quote:
            quote = "'"
            out.append(c)
            i += 1
        elif c == '"':
            quote = "" if quote else '"'
            out.append(c)
            i += 1
        elif command.startswith("$(", i) or c == "`":
            start = i + (2 if c == "$" else 1)
            end = closing(command, start) if c == "$" else command.find("`", start)
            end = len(command) if end < 0 else end
            bodies.append(command[start:end])
            out.append("$SUB")
            i = end + 1
        else:
            out.append(c)
            i += 1
    return "".join(out), bodies


def heredocs(command: str) -> tuple[str, list[str]]:
    """Drop heredoc bodies that are data (every program on the line only reads data, so
    the body is not run); an unquoted one still runs its $(...) and `...` parts, which
    are returned separately. Other bodies stay in place and are inspected as commands."""
    lines, out, extra, i = command.split("\n"), [], [], 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        i += 1
        matches = list(HEREDOC.finditer(line))
        data = bool(matches) and all(program(s)[0] in DATA_READERS for s in segments(line))
        for match in matches:
            delimiter, body = next(g for g in match.groups() if g is not None), []
            while i < len(lines) and lines[i].strip() != delimiter:
                body.append(lines[i])
                i += 1
            if not data:
                out += body
            elif match.group(3) is not None:
                extra += substitutions("\n".join(body))[1]
            if i < len(lines):
                out.append(lines[i])
                i += 1
    return "\n".join(out), extra


def tokens(command: str) -> list[str]:
    text = re.sub(r"\\\r?\n", " ", command).replace("\n", " ; ")
    lexer = shlex.shlex(text, posix=True, punctuation_chars=True)
    lexer.whitespace_split, lexer.commenters = True, ""
    try:
        return list(lexer)
    except ValueError:
        # Unbalanced quotes: fall back to a rough split rather than letting the call through.
        return [t.strip("'\"") for t in re.findall(r"[;&|()<>]+|[^\s;&|()<>]+", text)]


def split_words(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except ValueError:
        return text.split()


def segments(command: str) -> list[list[str]]:
    """Split a command line into simple commands at ; & && || | ( ) and newlines."""
    result, current = [], []
    for token in tokens(command):
        if token and set(token) <= set(";&|()"):
            result.append(current)
            current = []
        else:
            current.append(token)
    return [s for s in (*result, current) if s]


def unwrap(name: str, args: list[str]) -> list[str]:
    """The wrapped command line of a wrapper's arguments."""
    valued, i = WRAPPERS[name], 0
    while i < len(args) and args[i].startswith("-"):
        arg = args[i]
        if arg == "--":
            i += 1
            break
        if name == "env" and arg.startswith(("-S", "--split-string")):
            if arg in ("-S", "--split-string"):
                value, rest = (args[i + 1] if i + 1 < len(args) else ""), args[i + 2:]
            else:
                value, rest = arg.split("=", 1)[1] if "=" in arg else arg[2:], args[i + 1:]
            return split_words(value) + rest
        i += 2 if arg in valued else 1
    return args[i + (name == "timeout"):]  # timeout's duration


def program(segment: list[str]) -> tuple[str, list[str], bool]:
    """Return the executed program and its arguments past assignments, keywords and
    wrappers, and whether xargs feeds it further arguments."""
    words, fed = segment, False
    while words:
        word, name = words[0], os.path.basename(words[0])
        if ASSIGNMENT.match(word) or word in KEYWORDS:
            words = words[1:]
        elif name in WRAPPERS:
            fed |= name == "xargs"
            words = unwrap(name, words[1:])
        elif name == "quiet.py" or (PYTHON.fullmatch(name) and len(words) > 1
                                    and os.path.basename(words[1]) == "quiet.py"):
            rest = words[1:] if name == "quiet.py" else words[2:]
            if "--" not in rest:
                return name, words[1:], fed
            words = rest[rest.index("--") + 1:]
        else:
            return name, words[1:], fed
    return "", [], fed


def subcommand(args: list[str], valued=frozenset()) -> tuple[str, list[str]]:
    """Split arguments into (first positional, arguments after it), skipping option values."""
    i = 0
    while i < len(args) and args[i].startswith("-"):
        i += 2 if args[i] in valued else 1
    return (args[i], args[i + 1:]) if i < len(args) else ("", [])


def positionals(args: list[str], valued=frozenset()) -> list[str]:
    out, i, only = [], 0, False
    while i < len(args):
        arg = args[i]
        if only or not arg.startswith("-") or arg == "-":
            out.append(arg)
        elif arg == "--":
            only = True
        elif arg in valued:
            i += 1
        i += 1
    return out


def shell_payload(args: list[str]) -> str | None:
    for i, arg in enumerate(args[:-1]):
        if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", arg) or arg == "<<<":
            return args[i + 1]
    return None


def python_findings(args: list[str], ctx: tuple[str, Path], depth: int) -> list:
    """`python -c CODE`: .env literals, and the strings and string lists it runs."""
    code = None
    for i, arg in enumerate(args):
        if not arg.startswith("-"):
            break
        if arg.startswith("-c"):
            code = arg[2:] or (args[i + 1] if i + 1 < len(args) else "")
            break
    if not code:
        return []
    strings = [a or b for a, b in PY_STRING.findall(code)]
    found = [ENV for s in strings if is_env_file(s) and env_path(s, ctx[1])]
    if PY_EXEC.search(code) and depth < MAX_DEPTH:
        commands = list(strings)
        for body in re.findall(r"\[([^\[\]]*)\]", code):
            words = [a or b for a, b in PY_STRING.findall(body)]
            if words:
                commands.append(shlex.join(words))
        for command in commands:
            found += shell_findings(command, ctx, depth + 1)
    return found


def abbrev(name: str, full: str, minimum: int) -> bool:
    """A long option as git accepts it: a prefix of full, at least minimum characters."""
    return len(name) >= minimum and full.startswith(name)


def broad_pathspec(arg: str, cwd: Path) -> bool:
    if arg in BROAD_PATHSPECS or arg.startswith(":") or "**" in arg or "$" in arg:
        return True
    parts = [p for p in arg.split("/") if p]
    if parts and all(p in (".", "..") for p in parts):
        return True
    if os.path.isabs(os.path.expanduser(arg)):
        root = repo_root(cwd)
        target = os.path.realpath(os.path.expanduser(arg))
        return root is not None and (os.path.realpath(root) + "/").startswith(
            target.rstrip("/") + "/")
    return False


def broad_add(args: list[str], cwd: Path) -> bool:
    paths_only = False
    for arg in args:
        if paths_only or not arg.startswith("-"):
            if broad_pathspec(arg, cwd):
                return True
        elif arg == "--":
            paths_only = True
        elif arg.startswith("--"):
            name = arg[2:].split("=", 1)[0]
            if (abbrev(name, "all", 1) or abbrev(name, "update", 1)
                    or abbrev(name, "no-ignore-removal", 11)
                    or abbrev(name, "pathspec-from-file", 10)):
                return True
        elif set(arg[1:]) & {"A", "u"}:
            return True
    return False


def commits_all(args: list[str], cwd: Path) -> bool:
    skip, paths_only = False, False
    for arg in args:
        if skip:
            skip = False
        elif paths_only or not arg.startswith("-"):
            if broad_pathspec(arg, cwd):
                return True
        elif arg == "--":
            paths_only = True
        elif arg.startswith("--"):
            name = arg[2:].split("=", 1)[0]
            if abbrev(name, "all", 1) or abbrev(name, "pathspec-from-file", 10):
                return True
            skip = "=" not in arg and arg in COMMIT_VALUED
        else:
            for position, flag in enumerate(arg[1:], 1):
                if flag == "a":
                    return True
                if flag in "mFCct":  # takes a value: the rest of the cluster or the next word
                    skip = position == len(arg) - 1
                    break
                if flag in "uS":  # optional value, attached only
                    break
    return False


def cleans(args: list[str]) -> bool:
    """git clean that is not a dry run."""
    for arg in args:
        if arg == "--":
            break
        if arg.startswith("--") and abbrev(arg[2:], "dry-run", 1):
            return False
        if arg.startswith("-") and not arg.startswith("--") and "n" in arg[1:]:
            return False
    return True


def stashes_untracked(args: list[str]) -> bool:
    if subcommand(args)[0] in {"list", "show", "pop", "apply", "drop", "clear", "branch",
                               "create", "store"}:
        return False
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg == "--":
            break
        elif arg.startswith("--"):
            name = arg[2:].split("=", 1)[0]
            if abbrev(name, "include-untracked", 3) or abbrev(name, "all", 1):
                return True
            skip = name == "message" and "=" not in arg
        elif arg.startswith("-"):
            for position, flag in enumerate(arg[1:], 1):
                if flag in "ua":
                    return True
                if flag == "m":
                    skip = position == len(arg) - 1
                    break
    return False


def configured_alias(cwd: Path, verb: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9][\w.-]*", verb) or not cwd.is_dir():
        return None
    try:
        result = subprocess.run(["git", "-C", str(cwd), "config", "--get", f"alias.{verb}"],
                                capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def alias_findings(value: str, rest: list[str], ctx: tuple[str, Path], depth: int) -> list:
    ctx = Context(ctx[0], ctx[1], False)
    if depth >= MAX_DEPTH:
        return [GIT_DYNAMIC[ctx[0]]]
    if value.startswith("!"):
        return shell_findings(f"{value[1:]} {shlex.join(rest)}", ctx, depth + 1)
    return git_findings(split_words(value) + rest, ctx, depth + 1)


def config_findings(args: list[str], ctx: tuple[str, Path], depth: int) -> list:
    """git config: guarded keys being changed, and aliases being defined."""
    words, changes, reads, i = [], False, False, 0
    while i < len(args):
        arg = args[i]
        changes |= arg in CONFIG_CHANGES
        reads |= arg in CONFIG_READS
        if not arg.startswith("-"):
            words.append(arg)
        i += 2 if arg in CONFIG_VALUED else 1
    if words and words[0] in {"set", "unset", "get", "list", "edit", "rename-section",
                              "remove-section"}:
        action = words.pop(0)
        changes |= action in {"set", "unset", "rename-section", "remove-section"}
        reads |= action in {"get", "list"}
    if not words or not (changes or (len(words) > 1 and not reads)):
        return []
    key = words[0].casefold()
    if key in GUARDED_CONFIG:
        return [SELF[ctx[0]]]
    if key.startswith("alias.") and len(words) > 1:
        return alias_findings(words[1], [], ctx, depth)
    return []


def git_read(cwd: Path, *args: str):
    """Bounded local metadata only. Unknown state cannot authorize a push."""
    try:
        result = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True,
                                text=True, timeout=1, stdin=subprocess.DEVNULL)
        if len(result.stdout) <= 65536 and result.returncode in (0, 1):
            return result.returncode, result.stdout.strip()
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return None


def ordinary_push(args: list[str], cwd: Path, *, subtree=False) -> bool:
    """Prove non-force destinations from literal argv and effective local routing."""
    flags = {"--quiet", "--verbose", "--porcelain", "--set-upstream", "--dry-run",
             "--thin", "--no-thin", "--progress", "--follow-tags", "--atomic", "--verify",
             "--ipv4", "--ipv6"}
    subtree_values = {"--prefix", "-P", "--annotate", "--onto", "--branch", "-b", "--message", "-m"}
    words, index, positional = [], 0, False
    while index < len(args):
        arg = args[index]
        index += 1
        if "$" in arg or "`" in arg or not arg:
            return False
        if not positional and arg == "--":
            positional = True
        elif not positional and arg.startswith("-"):
            name = arg.split("=", 1)[0]
            if subtree and name in subtree_values:
                if "=" not in arg:
                    if index == len(args):
                        return False
                    index += 1
            elif name in flags or (subtree and name in ("--debug", "--ignore-joins", "--rejoin", "--squash")):
                pass
            elif re.fullmatch(r"-[vqun46]+", arg) or (subtree and arg == "-d"):
                pass
            else:
                return False  # force, deletion, mirror, broad/unknown switches stay ask.
        elif subtree and arg == "push" and not words:
            pass
        else:
            words.append(arg)
    current = git_read(cwd, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = current[1] if current and current[0] == 0 else None
    raw = git_read(cwd, "config", "--null", "--get-regexp",
                   r"^(remote\..*\.(url|pushurl|push|mirror)|branch\..*\.(remote|merge|pushremote)|remote\.pushdefault|push\.default|url\..*\.(insteadof|pushinsteadof))$")
    if raw is None:
        return False
    config = {}
    for entry in raw[1].split("\0"):
        key, separator, value = entry.partition("\n")
        if separator:
            # Git section/variable names ignore case; remote and branch subsections do not.
            section, _, tail = key.partition(".")
            subsection, dot, variable = tail.rpartition(".")
            key = section.lower() + "." + (subsection + dot if dot else "") + variable.lower()
            config.setdefault(key, []).append(value)
    def last(key, default=None):
        return config.get(key, [default])[-1]
    remote = words.pop(0) if words else (last(f"branch.{branch}.pushremote") or
             last("remote.pushdefault") or last(f"branch.{branch}.remote") or "origin")
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", remote) \
            or f"remote.{remote}.url" not in config:
        return False
    if len(config[f"remote.{remote}.url"]) != 1 or f"remote.{remote}.pushurl" in config \
            or any(key.startswith("url.") for key in config):
        return False  # Cached fetch HEAD cannot prove a rewritten/separate push endpoint.
    if last(f"remote.{remote}.mirror", "false").lower() not in ("false", "no", "off", "0"):
        return False
    head = git_read(cwd, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
    if not head or head[0] != 0:
        return False  # No network/auth probes: initialize cached remote HEAD before opting in.
    prefix = f"refs/remotes/{remote}/"
    if not head[1].startswith(prefix):
        return False
    protected = {"main", "master", head[1][len(prefix):]}
    if subtree and len(words) != 1:
        return False
    if not words:
        words = config.get(f"remote.{remote}.push", [])
        if not words:
            mode = last("push.default", "simple")
            upstream = last(f"branch.{branch}.merge")
            if mode == "current" and branch:
                words = [branch]
            elif mode in ("simple", "upstream") and branch and upstream \
                    and last(f"branch.{branch}.remote") == remote:
                if mode == "simple" and upstream != "refs/heads/" + branch:
                    return False
                words = [upstream]
            else:
                return False
    for ref in words:
        if any(c in ref for c in "+*$`?") or not ref or ref.startswith(":"):
            return False
        source, colon, target = ref.partition(":")
        if colon and (not source or not target or ":" in target):
            return False
        target = target if colon else ref
        if not colon and config.get(f"remote.{remote}.push"):
            # Git may map an explicitly supplied source via configured push refspecs.
            return False
        if target in ("HEAD", "@"):
            if colon or not branch:
                return False
            target = branch
        elif any(c in target for c in "~^{}["):
            return False  # Other revision expressions cannot prove a destination.
        if target.startswith("refs/heads/"):
            target = target[len("refs/heads/"):]
        elif target.startswith("refs/"):
            continue  # Explicit tags/notes cannot change a default branch.
        if target in protected:
            return False
    return True


def git_findings(args: list[str], ctx: tuple[str, Path], depth: int = 0,
                 fed: bool = False) -> list:
    ctx = Context(*ctx)
    runtime, cwd = ctx[:2]
    autonomous = ctx.autonomous
    aliases, i = {}, 0
    while i < len(args) and args[i].startswith("-"):
        option, attached, value = args[i].partition("=") if args[i].startswith("--") else (
            args[i], "", "")
        if not attached and option in GIT_VALUED:
            value = args[i + 1] if i + 1 < len(args) else ""
            i += 1
        i += 1
        key, _, setting = value.partition("=")
        if option == "-C" and value:
            cwd = resolve(value, cwd)
        elif option in ("-c", "--config-env") and key.casefold().startswith("alias."):
            aliases[key[6:]] = setting if option == "-c" else None
        if option not in ("-C", "--no-pager"):
            autonomous = False  # Git configuration/directory overrides need approval.
    verb, rest = (args[i], args[i + 1:]) if i < len(args) else ("", [])
    ctx = Context(runtime, cwd, autonomous)
    if "$" in verb or (fed and verb not in GIT_COMMANDS):
        return [GIT_DYNAMIC[runtime]]
    if verb and verb not in GIT_COMMANDS:
        value = aliases[verb] if verb in aliases else configured_alias(cwd, verb)
        if verb in aliases and value is None:
            return [GIT_DYNAMIC[runtime]]
        return alias_findings(value, rest, ctx, depth) if value else []
    subtree_verb, _ = subcommand(rest, {"-P", "--prefix"}) if verb == "subtree" else ("", [])
    if verb == "push" or subtree_verb == "push":
        if runtime == "claude" and autonomous and not fed and rules(cwd).push == "allow" \
                and ordinary_push(rest, cwd, subtree=subtree_verb == "push"):
            return []
        return [PUSH[runtime]]
    if verb in PUSH_VERBS:
        return [PUSH[runtime]]
    if verb in ("add", "stage") and broad_add(rest, cwd):
        return [ADD]
    if verb == "commit" and commits_all(rest, cwd):
        return [COMMIT_ALL]
    if verb == "config":
        return config_findings(rest, ctx, depth)
    if verb == "clean" and cleans(rest):
        return [CLEAN]
    if verb == "stash" and stashes_untracked(rest):
        return [STASH]
    return []


def prod_compose(token: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatchcase(os.path.basename(piece), pattern)
               for piece in re.split(r"[=:,]", token) for pattern in patterns)


def host_files(root: Path, patterns: tuple[str, ...]) -> list[Path]:
    """The files a list of deploy host file patterns names under root."""
    files = []
    for pattern in patterns:
        try:
            if pattern.endswith("/"):
                for folder, dirs, names in os.walk(root / pattern):
                    dirs[:] = [d for d in dirs if d not in ("node_modules", ".git")]
                    files += [Path(folder) / name for name in names]
            else:
                files += root.glob(pattern)
        except (OSError, ValueError, NotImplementedError):
            continue
    return files


def deploy_hosts(cwd: Path, patterns: tuple[str, ...]) -> set[str]:
    """IPv4 literals (not loopback) in the deploy host files of the command's repository
    and of the guard's own checkout, read now."""
    hosts = set()
    for root in {repo_root(cwd), GUARD_CHECKOUT} - {None}:
        for path in host_files(root, patterns):
            try:
                if path.is_file() and path.stat().st_size <= READ_CAP:
                    hosts.update(IPV4.findall(path.read_text(errors="replace")))
            except OSError:
                continue
    return {host for host in hosts if not host.startswith(("127.", "0."))}


def host_pattern(host: str) -> re.Pattern:
    """An IPv4 address as a whole number run; a name, or any subdomain of it, label by label."""
    if IPV4.fullmatch(host):
        return re.compile(rf"(?<![\d.]){re.escape(host)}(?!\.?\d)")
    return re.compile(rf"(?i)(?<![\w-]){re.escape(host)}(?![\w-]|\.[\w-])")


def remote_deploy(args: list[str], cwd: Path) -> bool:
    found = rules(cwd)
    if any(f"{user}@" in arg for arg in args for user in found.deploy_users):
        return True
    users = "|".join(map(re.escape, found.deploy_users))
    for arg, following in zip(args, [*args[1:], ""]):
        if (arg == "-l" and following in found.deploy_users) or re.fullmatch(
                rf"(?i)(?:-o)?user=(?:{users})", arg):
            return True
    hosts = [*found.deploy_hosts, *deploy_hosts(cwd, found.deploy_host_files)]
    return any(host_pattern(host).search(arg) for arg in args for host in hosts)


def deploys(name: str, args: list[str], segment: list[str], cwd: Path) -> bool:
    if name == "helm":
        return subcommand(args, HELM_VALUED)[0] in HELM_VERBS
    if name == "kubectl":
        verb, rest = subcommand(args, KUBECTL_VALUED)
        if verb == "rollout":  # status and history only read
            return subcommand(rest, KUBECTL_VALUED)[0] not in ("status", "history")
        return verb in KUBECTL_VERBS
    if name in REMOTE_SHELLS:
        return remote_deploy(args, cwd)
    if name == "docker-compose" or (name == "docker"
                                    and subcommand(args, DOCKER_VALUED)[0] == "compose"):
        patterns = rules(cwd).prod_compose
        return any(prod_compose(token, patterns) for token in segment)
    if name == "gh":
        verb, rest = subcommand(args, GH_VALUED)
        return verb == "workflow" and subcommand(rest)[0] == "run"
    if name == "railway":
        return subcommand(args)[0] in {"up", "deploy", "redeploy"}
    if name in ("fly", "flyctl"):
        return subcommand(args, FLY_VALUED)[0] == "deploy"
    return False


def allowed_workflow(name: str, args: list[str], ctx: Context) -> bool:
    """`gh [-R REPO] workflow run FILE [-f|-F|--ref|-R VALUE]...` as one literal command on
    Claude, naming an entry of the command repository's guard.allow_workflows."""
    if name != "gh" or ctx.runtime != "claude" or not ctx.autonomous:
        return False
    repo, words, i = None, [], 0
    while i < len(args):
        flag, eq, value = args[i].partition("=")
        if args[i] in GH_RUN_VALUED and i + 1 < len(args):
            flag, value, i = args[i], args[i + 1], i + 2
        elif flag.startswith("--") and eq and flag in GH_RUN_VALUED:
            i += 1
        elif args[i].startswith("-"):
            return False  # --json, attached short values, anything unvetted
        else:
            words.append(args[i])
            i += 1
            continue
        if flag in ("-R", "--repo"):
            if repo is not None:
                return False
            repo = value
        if flag in ("-F", "--field") and value.partition("=")[2].startswith("@"):
            return False  # -F key=@path sends a file's content
    if len(words) != 3 or words[:2] != ["workflow", "run"]:
        return False
    entry = f"{repo}:{words[2]}" if repo is not None else words[2]
    return entry in rules(ctx.cwd).allow_workflows


SUBSTITUTE = re.compile(r"(?:^|[;\s{])s([^\w\s\\])((?:\\.|(?!\1).)*)\1((?:\\.|(?!\1).)*)\1")


def inplace_skip(name: str, args: list[str], cwd: Path) -> bool:
    """`sed -i` / `perl -pi` whose s/// replacement adds a skip marker to a test file."""
    if name == "sed":
        if not any(a.startswith(("-i", "--in-place")) for a in args):
            return False
    elif name == "perl":
        if not any(a.startswith("-") and not a.startswith("--") and "i" in a[1:] for a in args):
            return False
    else:
        return False
    scripts = [v for a, v in zip(args, [*args[1:], ""]) if a in ("-e", "--expression")]
    files = [a for a in positionals(args, {"-e", "--expression", "-f", "--file"}) if a]
    if not scripts and files:
        scripts, files = [files[0]], files[1:]
    text = lambda part: re.sub(r"\\[ntr]", " ", part)  # sed's \n / \t become separators
    added = any(len(SKIP_MARKERS.findall(text(m.group(3)))) > len(SKIP_MARKERS.findall(text(m.group(2))))
                for script in scripts for m in SUBSTITUTE.finditer(script))
    return added and any(is_test_file(resolve(f, cwd)) for f in files)


def is_redirect(token: str) -> bool:
    return bool(set(token) & set("<>")) and set(token) <= set("<>&|") and not token.startswith("<<")


def shell_targets(name: str, args: list[str], cwd: Path) -> list[tuple[str, str]]:
    """(path, read|write|remove) for the files a shell file command touches."""
    if name == "tee":
        return [(p, "write") for p in positionals(args)]
    if name == "truncate":
        return [(p, "write") for p in positionals(args, {"-s", "--size", "-r", "--reference"})]
    if name in ("rm", "unlink"):
        return [(p, "remove") for p in positionals(args)]
    if name == "dd":
        return [(a[3:], "write" if a.startswith("of=") else "read")
                for a in args if a.startswith(("of=", "if="))]
    if name == "sed" and any(a.startswith(("-i", "--in-place")) for a in args):
        return [(p, "write") for p in positionals(args, {"-e", "-f", "--expression", "--file"})
                if p]
    if name in ("cp", "mv", "ln"):
        paths = positionals(args, {"-t", "--target-directory", "-S", "--suffix"})
        target = next((v for a, v in zip(args, [*args[1:], ""]) if a in ("-t",
                       "--target-directory")), None)
        sources = paths if target else paths[:-1]
        target = target or (paths[-1] if len(paths) > 1 else None)
        kind = "remove" if name == "mv" else "read"
        found = [(s, kind) for s in sources]
        if target:
            found.append((target, "write"))
            if resolve(target, cwd).is_dir():
                found += [(os.path.join(target, os.path.basename(s.rstrip("/"))), "write")
                          for s in sources]
        return found
    return []


def segment_findings(segment: list[str], ctx: tuple[str, Path], depth: int) -> list:
    runtime, cwd = ctx[:2]
    found = []
    for token, target in zip(segment, segment[1:]):
        if is_redirect(token):
            found += target_findings(target, cwd, runtime, "write" if ">" in token else "read")
    name, args, fed = program(segment)
    payload = None
    if name in SHELLS:
        payload = shell_payload(args)
    elif name == "eval":
        payload = " ".join(args)
    if payload is not None and depth < MAX_DEPTH:
        found += shell_findings(payload, ctx, depth + 1)
    if PYTHON.fullmatch(name):
        found += python_findings(args, ctx, depth)
    if name == "git":
        found += git_findings(args, ctx, depth, fed)
    if deploys(name, args, segment, cwd) and not allowed_workflow(name, args, ctx):
        found.append(DEPLOY)
    if name in READERS and any(env_path(arg, cwd) for arg in args):
        found.append(ENV)
    if inplace_skip(name, args, cwd):
        found.append(SKIP)
    for target, kind in shell_targets(name, args, cwd):
        found += target_findings(target, cwd, runtime, kind)
    return found


def shell_findings(command: str, ctx: tuple[str, Path], depth: int = 0) -> list:
    ctx = Context(*ctx)
    runtime, cwd = ctx[:2]
    command, bodies = heredocs(command)
    outer, more = substitutions(command)
    bodies += more
    found = []
    parts = segments(outer)
    for segment in parts:
        literal = len(parts) == 1 and not bodies and Path(segment[0]).name in ("git", "gh")
        found += segment_findings(segment, Context(runtime, cwd, ctx.autonomous and literal), depth)
        if segment[0] == "cd" and len(segment) == 2:
            cwd = resolve(segment[1], cwd)
    if depth < MAX_DEPTH:
        for body in bodies:
            found += shell_findings(body, Context(runtime, cwd, False), depth + 1)
    return found


# Files

def is_env_file(path: str) -> bool:
    name = os.path.basename(path.rstrip("/")).casefold()
    if any(c in name for c in "*?[") and name.startswith("."):
        return (any(fnmatchcase(probe, name) for probe in (".env", ".env.local"))
                or re.split(r"[*?\[]", name, maxsplit=1)[0].startswith(".env."))
    return name == ".env" or (name.startswith(".env.") and name != ".env.example")


def real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def opened(raw: str, cwd: Path) -> Path | None:
    """The file the kernel opens for raw (unlike resolve(), symlinks resolve before `..`), or
    None when that cannot be told."""
    try:
        return real(cwd / os.path.expanduser(raw))
    except (OSError, ValueError):
        return None


def env_path(raw: str, cwd: Path) -> bool:
    """An .env name, or a path whose symlinks resolve to one, that guard.env_exempt does not
    exempt; an option is decided by its name alone, and a path with *, ? or [ is never
    exempt (the shell may expand it)."""
    if raw.startswith("-"):
        return is_env_file(raw)
    path, actual = resolve(raw, cwd), opened(raw, cwd)
    named = is_env_file(raw) or env_named(path, actual)
    if any(c in raw for c in "*?["):
        return named
    return named and not env_exempt(path, actual)


def env_named(path: Path, actual: Path | None) -> bool:
    try:
        return any(is_env_file(p.name) for p in (path, real(path), actual) if p is not None)
    except (OSError, ValueError):  # e.g. a NUL byte: no file has that name
        return is_env_file(path.name)


def env_blocked(path: Path, actual: Path | None) -> bool:
    return env_named(path, actual) and not env_exempt(path, actual)


def env_exempt(path: Path, actual: Path | None) -> bool:
    """A file of the command's repository that its overlay's guard.env_exempt globs name:
    the path and the file it resolves to are each committed and cleanly tracked as that
    exact file there, and each one with an env name matches a glob. actual is the file the
    kernel opens (opened()); unknown, or not the one path resolves to, exempts nothing. Any
    error means not exempt: a crash would fail open."""
    try:
        if actual is None or actual != real(path):
            return False
        for candidate in dict.fromkeys((path, real(path))):
            root = repo_root(candidate.parent)
            if root is None or real(root) != COMMAND_REPO or candidate.is_dir():
                return False
            name = candidate.relative_to(root).as_posix()
            parts = name.split("/")
            if is_env_file(candidate.name) and not any(
                    len(glob.split("/")) == len(parts)
                    and all(map(fnmatchcase, parts, glob.split("/")))
                    for glob in rules_at(root).env_exempt):
                return False
            if not committed(root, name):
                return False
        return True
    except Exception:  # never let the exemption crash the guard open
        return False


def committed(root: Path, name: str) -> bool:
    """name is exactly one file in HEAD and one plain index entry (stage 0, not skip-worktree
    or assume-unchanged): staging a file is not enough, and the index cannot hide edits."""
    entry = os.fsencode(name)
    index = git_status(str(root), "--literal-pathspecs", "ls-files", "-z", "-s", "-v",
                       "--error-unmatch", "--", name)
    head = git_status(str(root), "--literal-pathspecs", "ls-tree", "-z", "HEAD", "--", name)
    for result, kind in ((index, None), (head, b"blob")):
        if result is None or result.returncode != 0 or result.stdout.count(b"\0") != 1:
            return False
        meta, _, tail = result.stdout[:-1].partition(b"\t")
        fields = meta.split()
        if tail != entry or (fields[1:2] != [kind] if kind else (
                fields[:1] != [b"H"] or fields[3:4] != [b"0"])):
            return False
    return True


def posix(path: Path) -> str:
    return ("/" + path.as_posix() + "/").casefold()


def generated(path: Path) -> tuple[str, str] | None:
    """The deny finding of the generated directory a path lies in, if any."""
    for segment, finding in rules(path).generated:
        if any(segment in posix(p) for p in (path, real(path))):
            return finding
    return None


def is_self(path: Path) -> bool:
    """The guard, an overlay that configures it, or a hook configuration."""
    overlays = {posix(real(p)).rstrip("/") for p in rules(path).overlays}
    return any(name.endswith(SELF_FILES) or name in overlays
               for name in (posix(p).rstrip("/") for p in (path, real(path))))


def is_vendored_core(path: Path) -> bool:
    """A file of the core a project vendors at harness/core (any case)."""
    return any(VENDORED_CORE in posix(p).casefold() for p in (path, real(path)))


def is_local_settings(path: Path) -> bool:
    return any(p.name.casefold() == "settings.local.json" for p in (path, real(path)))


def is_test_file(path: Path) -> bool:
    return any(fnmatchcase(path.name, pattern) for pattern in TEST_FILES)


def git_status(folder: str, *args: str) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(["git", "-C", folder, *args], capture_output=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None


def personal_untracked(path: Path) -> bool:
    """An existing file under a personal directory that Git does not track (ignored or not)."""
    resolved = real(path)
    if not any(d in posix(resolved) for d in PERSONAL_DIRS) or not resolved.is_file():
        return False
    # 1 means "not tracked". Any other failure (no repository) allows.
    result = git_status(str(resolved.parent), "ls-files", "--error-unmatch", "--", resolved.name)
    return result is not None and result.returncode == 1


def personal_tree(path: Path) -> bool:
    """A directory that is, holds or sits in a personal directory with untracked files."""
    resolved = real(path)
    root = repo_root(resolved) if resolved.is_dir() else None
    if root is None:
        return False
    for folder in (root / d.strip("/") for d in PERSONAL_DIRS):
        if not folder.is_dir():
            continue
        if resolved == folder or folder in resolved.parents:
            base = resolved
        elif resolved in folder.parents:
            base = folder
        else:
            continue
        result = git_status(str(base), "ls-files", "--others", "-z", "--", ".")
        if result is not None and result.returncode == 0 and result.stdout:
            return True
    return False


def target_findings(raw: str, cwd: Path, runtime: str, kind: str) -> list:
    """Findings for a file a shell command reads, writes or removes."""
    if not raw or "$" in raw:
        return []
    if env_path(raw, cwd):
        return [ENV]
    path = resolve(raw, cwd)
    if kind == "read":
        return []
    found = []
    if is_local_settings(path):
        found.append(LOCAL_SETTINGS)
    finding = generated(path) if kind == "write" else None
    if finding:
        found.append(finding)
    if is_self(path):
        found.append(SELF[runtime])
    elif is_vendored_core(path):
        found.append(CORE[runtime])
    if personal_untracked(path) or (kind == "remove" and personal_tree(path)):
        found.append(PERSONAL)
    return found


def file_findings(path: Path, old: str, new: str, runtime: str, actual: Path | None) -> list:
    if env_blocked(path, actual):
        return [ENV]
    if is_local_settings(path):
        return [LOCAL_SETTINGS]
    finding = generated(path)
    if finding:
        return [finding]
    if personal_untracked(path):
        return [PERSONAL]
    found = ([SELF[runtime]] if is_self(path)
             else [CORE[runtime]] if is_vendored_core(path) else [])
    if is_test_file(path) and len(SKIP_MARKERS.findall(new)) > len(SKIP_MARKERS.findall(old)):
        found.append(SKIP)
    return found


def resolve(raw: str, cwd: Path) -> Path:
    return Path(os.path.normpath(cwd / os.path.expanduser(raw)))


def current_text(path: Path) -> str:
    try:
        if not path.is_file():  # not a FIFO or a device: reading one could hang the hook
            return ""
        with path.open("rb") as handle:
            return handle.read(READ_CAP).decode("utf-8", errors="replace")
    except OSError:
        return ""


def applied(current: str, edits: list[tuple[str, str, bool]]) -> tuple[str, str]:
    """(before, after) for a file: edits applied to its current text; an edit that does not
    apply contributes its own old and new text instead."""
    result, removed, added = current, [], []
    for old, new, every in edits:
        if old and old in result:
            result = result.replace(old, new) if every else result.replace(old, new, 1)
        else:
            removed.append(old)
            added.append(new)
    return "\n".join([current, *removed]), "\n".join([result, *added])


def patch_changes(text: str, cwd: Path) -> list[tuple[Path, str, str, Path | None]]:
    """(path, before, after, opened path) per file of an apply_patch body, hunks applied to
    the file."""
    files = []
    for line in text.splitlines():
        match = PATCH_PATH.fullmatch(line.strip())
        if match:
            raw = match.group(1).strip()
            path = resolve(raw, cwd)
            moved = line.strip().startswith("*** Move to") and files
            files.append((path, files[-1][1] if moved else path, [[[], []]], opened(raw, cwd)))
        elif files and line.startswith("@@"):
            files[-1][2].append([[], []])
        elif files and line and line[0] in "-+ ":
            old, new = files[-1][2][-1]
            if line[0] in "- ":
                old.append(line[1:])
            if line[0] in "+ ":
                new.append(line[1:])
    return [(path, *applied(current_text(base), [("\n".join(old), "\n".join(new), False)
                                                  for old, new in hunks if old or new]), actual)
            for path, base, hunks, actual in files]


def tool_changes(data: dict, cwd: Path) -> list[tuple[Path, str, str, Path | None]]:
    raw = data.get("file_path") or data.get("notebook_path")
    if not isinstance(raw, str) or not raw:
        return []
    path, actual = resolve(raw, cwd), opened(raw, cwd)
    current = current_text(path)
    if "content" in data:
        return [(path, current, str(data["content"]), actual)]
    edits = data.get("edits") if isinstance(data.get("edits"), list) else [data]
    edits = [(str(e.get("old_string") or ""), str(e.get("new_string") or ""),
              bool(e.get("replace_all"))) for e in edits if isinstance(e, dict)]
    return [(path, *applied(current, edits), actual)]


def changes_findings(changes: list[tuple[Path, str, str, Path | None]], runtime: str) -> list:
    return [f for path, old, new, actual in changes
            for f in file_findings(path, old, new, runtime, actual)]


# Decision

def decide(event: dict, runtime: str) -> tuple[str, str] | None:
    tool, data = event.get("tool_name"), event.get("tool_input")
    if isinstance(data, str) and (tool == "apply_patch" or "*** Begin Patch" in data):
        data = {"input": data}  # Codex free-form apply_patch
    if not isinstance(data, dict):
        return None
    cwd = Path(event["cwd"]) if isinstance(event.get("cwd"), str) else Path.cwd()
    if isinstance(data.get("workdir"), str) and data["workdir"]:
        # Codex changes into workdir like the kernel does: symlinks resolve before `..`.
        cwd = opened(data["workdir"], cwd) or resolve(data["workdir"], cwd)
    rules(cwd)  # read the overlays now, so a broken one is noted on every call
    global COMMAND_REPO
    root = repo_root(cwd)
    COMMAND_REPO = real(root) if root is not None else None
    findings = []
    command = data.get("command", data.get("cmd"))
    if isinstance(command, list) and all(isinstance(part, str) for part in command):
        command = shlex.join(command)
    if tool == "apply_patch":
        text = next((v for v in (command, data.get("input"), data.get("patch"))
                     if isinstance(v, str)), "")
        findings += changes_findings(patch_changes(text, cwd), runtime)
    elif isinstance(command, str):
        if "*** Begin Patch" in command:
            findings += changes_findings(patch_changes(command, cwd), runtime)
        findings += shell_findings(command, Context(runtime, cwd))
    if tool in EDIT_TOOLS:
        findings += changes_findings(tool_changes(data, cwd), runtime)
    raw = data.get(READ_TOOLS.get(tool, ""))
    if isinstance(raw, str) and env_path(raw, cwd):
        findings.append(ENV)
    if tool == "Grep" and isinstance(data.get("glob"), str) and is_env_file(data["glob"]):
        findings.append(ENV)
    for wanted in ("deny", "ask"):
        for decision, reason in findings:
            if decision == wanted:
                return decision, reason
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime", choices=("claude", "codex"), required=True)
    try:
        args = parser.parse_args()
    except SystemExit as exc:
        if exc.code:
            print("guard: bad arguments; allowing the call", file=sys.stderr)
        return 0
    try:
        event = json.load(sys.stdin)
        if not isinstance(event, dict):
            raise ValueError("hook event is not a JSON object")
        verdict = decide(event, args.runtime)
    except Exception as exc:  # fail open: a broken guard must not block every tool call
        print(f"guard: {type(exc).__name__}: {exc}; allowing the call", file=sys.stderr)
        return 0
    if verdict:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                                 "permissionDecision": verdict[0],
                                                 "permissionDecisionReason": verdict[1]}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
