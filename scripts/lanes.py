#!/usr/bin/env python3
"""Decide how a worker is started: an in-session subagent, a headless CLI or a herdr pane.

Usage: lanes.py decide --role R --runtime claude|codex --orchestrator claude|codex --minutes N
                       [--needs-user] [--outlives-turn] [--repo PATH] [--tier N]
                       [--compare-candidate]

decide prints one JSON object {"launcher": "subagent"|"headless"|"pane", "reason": "<one
sentence>", "herdr": true|false}; "herdr" says whether herdr is available (HERDR_ENV=1).
The rule, in order:
  1. The lane is pane-worthy if any of these hold, and the reason names every one that does:
     the user will watch or steer it (--minutes over 20, so 20 is not and 21 is; --tier 3 with
     a writer role, implementer or simplifier; --compare-candidate), --needs-user (answers or
     approvals mid-way), --outlives-turn (the work must outlive the orchestrator's turn),
     --repo inside another Git top-level than the current checkout.
  2. A pane-worthy lane is `pane` under HERDR_ENV=1, else `headless` with a reason that starts
     "no herdr: " and says why it would be a pane.
  3. Otherwise a runtime other than the orchestrator's is `headless` and the same runtime is
     `subagent` (short, report-only, the result is needed before the next step).
--repo is resolved against the current directory; a relative path is fine. A linked worktree
of the same repository is not another repository: the two are compared by their resolved
`git rev-parse --git-common-dir`, not by their top-levels. Exit codes: 0 decided, 2 bad
arguments (an unknown runtime, negative minutes, a tier outside 0-3, a --repo that is not a
directory or not inside a Git repository).

Launchers (the run directory is `task_state.py locate TASK` of the current checkout):

  lanes.py start --task T --work W --role R --runtime claude|codex --model M --effort E
                 --brief FILE --report PATH --checkout DIR [--minutes N] [--needs-user]
                 [--outlives-turn] [--tier N] [--compare-candidate]
                 [--orchestrator claude|codex] [--launcher headless|pane] [--timeout SEC]
                 [--max-budget-usd N] [--capability candidate --scratch DIR]
  lanes.py status --task T [--dispatch D] [--stale-minutes N]
  lanes.py wait --task T --dispatch D [--timeout SEC] [--stale-minutes N]
  lanes.py result --task T --dispatch D
  lanes.py cancel --task T --dispatch D
  lanes.py snapshot-cleanup --task T [--dispatch D ...]
  lanes.py dashboard --task T [--open | --close]

start decides by the rule above with --checkout as --repo (unless --launcher is given); its
orchestrator is --orchestrator, else $HARNESS_ORCHESTRATOR (an unknown runtime there is exit 2),
else claude, and the printed object adds "orchestrator" whenever it did not come from the flag,
so an assumed claude is visible. start
records `dispatch.start` {dispatch, role, runtime, model, effort, launcher, work, report,
pane}, prints {"dispatch", "launcher", "reason", "pane"} and returns at once. With
--max-budget-usd N the printed object adds "budget_enforced": only headless Claude takes the
limit (`--max-budget-usd N`, true); Codex, panes and subagents ignore it (false). Every dispatch
ends with exactly one `dispatch.end` {dispatch, status done|failed|timeout|cancelled, usage};
its event id is "<dispatch>.end", so a second writer is a skipped duplicate. Events keep the
report path as given (absolute). After each event it writes, lanes regenerates the run's
default dashboard (dashboard.py) best effort: a failure is a note on stderr, never an error.
A report counts as the worker's only when it was written after the start: created, or its
mtime or size changed against the stamp the state file keeps (`report_before`).

--capability candidate --scratch DIR (the same keywords on the importable start) gives one
profile on both vendors, whatever the role: a shell and a private snapshot of the source, writes
only inside DIR. DIR is created if missing; it must not contain the checkout, and it must lie
outside the checkout's repository or under its .harness/ (git-ignored run state), else exit 2.
DIR/src is the snapshot and must not exist yet. The snapshot is an independent clone of the
checkout's repository: `git clone --no-hardlinks --depth 1` (a full clone if that fails) of HEAD,
detached, with no remote and no alternates, so nothing in it reaches the source's objects, refs
or path. Then the working tree's changes are applied (`git diff HEAD --binary`) and the untracked,
non-ignored files are copied, except .harness/. The clone leaves a submodule empty, so the
working-tree files of every submodule initialized in the source (nested ones included; tracked
and untracked non-ignored, never its .git) are copied into the same path; nothing is fetched,
and a submodule the source has not initialized stays empty. The candidate reads exactly what the
checkout holds now; its working directory is the snapshot's copy of --checkout, so a relative write
changes the copy and never the source. The snapshot carries the project's tracked hook config,
so the guard still loads.
--capability implies --compare-candidate for the decision, so a candidate is never handed back
as a subagent.
- Headless Claude: `--permission-mode dontAsk --allowedTools Read Grep Glob Bash
  Write(//DIR/**) Edit(//DIR/**) --add-dir DIR --setting-sources project,local --settings
  <sandbox>`. dontAsk denies every other tool. The sandbox is Claude Code's Bash sandbox,
  {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false, "filesystem":
  {"denyWrite": [...]}}: shell commands write only in the snapshot, DIR and the per-user temp
  directory, so even an absolute path into the source is refused by the OS. There is no
  unsandboxed retry, and Claude does not start where the sandbox cannot run. Hooks run outside
  the sandbox, as before. --setting-sources project,local loads no user settings, so a
  `sandbox.filesystem.allowWrite` in ~/.claude/settings.json cannot reopen the source; the
  project's settings are the snapshot's (the guard hooks). denyWrite lists the source checkout,
  its repository's Git directory, the primary checkout and the run directory, each whole, or,
  when DIR lies inside one, every entry beside the path down to DIR (write_denials; entries
  created there later are not covered). Managed (admin) settings still apply and cannot be
  excluded: an allowWrite there widens the candidate too, only denyWrite still holds.
- Headless Codex: `--sandbox workspace-write -C <snapshot> --add-dir DIR
  --skip-git-repo-check` and the guard override; --add-dir makes DIR, where the report lives,
  writable beside the snapshot. Its last message goes to `<report>.last-message.md`
  (removed at the start). It is copied over the report only when the run ends done and the
  report is not fresh, so a report the candidate wrote as it went is kept; then the state
  file and the dispatch.end record `report_source: "final_message"`, which compare's seal
  reads; so does a Claude candidate's result written over a report that is not fresh. Other
  Codex workers write the last message to the report itself.
- Pane: the same flags go through `herdr agent start ... --`, and the pane opens in the
  snapshot. Interactive codex has no --skip-git-repo-check, and the clone is a repository
  anyway.
Every candidate process, headless or in a pane, gets HARNESS_CANDIDATE_SNAPSHOT=1
(CANDIDATE_SNAPSHOT_ENV) in its environment: the drift Stop hook (runtime_hook.py) skips only
there, and only when the snapshot has no main base.

The brief (and the pane's prompt) adds one line, "The source checkout is CHECKOUT; you work in
SNAPSHOT ...; write only inside DIR.". It starts with CANDIDATE_BRIEF_PREFIX, which compare
strips by. The printed object and `dispatch.start` add "capability",
"scratch", "snapshot" and "snapshot_base" (the commit the snapshot starts from). Without
--capability nothing changes.

The snapshot outlives the dispatch, including a cancel, as evidence.
`lanes.py snapshot-cleanup --task T` removes the snapshots of the run's ended candidates and
prints one row per candidate {dispatch, snapshot, removed, reason}; each --dispatch D (and the
importable snapshot_cleanup(run, dispatches)) limits it to those dispatches, so a compare
removes only its own round's, and a named dispatch without a snapshot gets a row with snapshot
null. It removes only a directory
the dispatch's state records as DIR/src that also holds lanes' marker for that dispatch
(.git/dev-harness-snapshot). Anything else is left in place, as is a candidate that has not
ended.

capability_profile(runtime, launcher, capability, role=...) describes what a worker gets as
{read_source, shell, writes} so callers can tell whether two candidates are equal
(write_roots=True adds "write_roots", where each runtime really writes: Claude the scratch by
its Edit rule and Bash the snapshot and the scratch in the sandbox, Codex the snapshot by -C
and the scratch by --add-dir, each also a temp directory); shell and writes are "as-approved"
for a default pane, where the user approves each action. A candidate's writes are confined,
but its reads are not confined: the source and the run directory, a rival's in-progress
report included, stay readable by absolute path on both runtimes. Reading the source is
intended. capability_profile(..., reads=True) adds "reads": "source and run directory
(independence rests on the brief)" for a candidate, so compare can report it.

- subagent: not launched here; the decision is printed with dispatch null and exit 3, and the
  orchestrator dispatches it through its own Agent tool.
- headless: a detached supervisor runs `claude -p --output-format stream-json --verbose` or
  `codex exec --json ... --output-last-message REPORT` (with the guard override that
  xreview.py gives the Codex reviewer), the brief plus "write your report to REPORT" on stdin,
  in --checkout (a candidate: in its snapshot). Writers (implementer, simplifier) get
  `--permission-mode acceptEdits --allowedTools Bash,Read,Edit,Write,Grep,Glob` / `--sandbox
  workspace-write`, others the default / `--sandbox read-only`; the checkout's own hooks (the
  guard) still apply. The stream goes to
  `<run>/lanes/<dispatch>.log`; `<run>/lanes/<dispatch>.json` keeps pid, child_pid, started
  and last_activity (the time of the latest stream line). The timeout (default 2700 s) stops
  the CLI's process group and ends `timeout`; exit 0 ends `done`, any other `failed`. Usage
  (tool_calls, input_tokens, output_tokens, duration_s) comes from the stream, else null;
  input_tokens count cached input on both runtimes (Claude: input_tokens +
  cache_read_input_tokens + cache_creation_input_tokens of the result, each when present;
  Codex's input_tokens already include it), and `cached_input_tokens` (Claude's cache reads,
  Codex's cached_input_tokens) is added only when the stream gives it. A
  Claude run that wrote no report since its start gets its final result text as the report
  (a report left by an earlier run is replaced).
- pane (HERDR_ENV=1 only): at most `herdr_max_agents` (default 3) live herdr agents, counted
  by the overlay's `project.json → lanes` {"herdr_max_agents": N (an integer >= 1), "count":
  "workspace" | "run"}: "workspace" (the default) counts every agent in the caller's workspace
  ($HERDR_WORKSPACE_ID, else all of `herdr agent list`), ones the run did not start included;
  "run" counts only the live agents this run started. Any other key or value is exit 2.
  The count and the launch run under an exclusive lock on one file per herdr workspace,
  `dev-harness-herdr-<workspace>.lock` ($HERDR_WORKSPACE_ID, else "default") in $XDG_RUNTIME_DIR
  (else the system temp directory), so concurrent starts of any runs in the workspace cannot
  both pass the limit.
  At the limit report-only work (not a writer, no --needs-user) runs headless instead and other
  work is refused with exit 4 and "queued: ..." so the orchestrator retries later. When the
  run has a dashboard tab (`lanes.py dashboard --open`, dashboard_herdr.py), a pane goes into
  its right column: a lane pane of the run whose dispatch ended and whose shell is idle again
  is reused first (cd and the worker's environment are typed into that shell), else the
  column's last pane is split down and the column's heights are balanced. Without a dashboard
  the first live pane of the run splits the caller's pane (`--no-focus`); a second live pane
  opens one tab labelled with the task, and later ones split the run's last pane there. Every
  lane pane is labelled "🤖 <work> · <runtime> <model>". A pane whose
  --checkout is in another repository (decide's other-repo signal) goes into that repository's
  own tab instead, labelled with its top-level directory's name, opened once per run and split
  for its later panes; those panes do not count for the placement above. Then
  `herdr agent start NAME --kind RUNTIME --pane ID -- <model/effort args; Codex adds the guard>`
  (Claude's effort goes in the pane's environment; the agent id herdr returns, if any, is kept)
  and `herdr agent prompt NAME "<read the brief, write the report>"`. A start that returns
  `agent_not_ready` records `prompt_pending`: status shows `blocked` until the agent is idle,
  then status (or wait) delivers the brief exactly once. Every status of a pane that reaches
herdr keeps what it saw in `<run>/lanes/<dispatch>.json` under the state lock: agent_status
(herdr's, "gone" for an agent herdr no longer knows), state (lanes') and observed_at, so the
dashboard can show a pane herdr blocked after its start; a status that changes the kept
state regenerates the dashboard (best effort, as after an event). Panes the run did not create are never
  closed or reused; cancel first checks that NAME still resolves to the recorded pane (and agent
  id, when both are known), else exits 1 with "agent NAME is no longer the pane this run
  created" and sends nothing; then it sends ctrl+c to the run's own agent and leaves its pane
  open. Usage from a pane is null.

dashboard opens (--open), closes (--close) or describes the run's herdr dashboard tab; the tab,
its geometry, views and the overlay's `project.json → dashboard` are dashboard_herdr.py's.
Without herdr (HERDR_ENV unset) it says so and exits 0; dashboard.py's page is the fallback.

status prints one object per dispatch (a list without --dispatch): {dispatch, launcher, state,
last_activity, pane, report, reason}, state one of running, stale, idle, done, failed, timeout,
cancelled, blocked, unknown. Headless liveness comes from the supervisor pid and the stream:
no line for --stale-minutes (default 15) is `stale` (STALE_MINUTES, which the dashboard
shares), "liveness unknown", never failed; a supervisor gone without an end is ended `failed`.
A pane reads `herdr agent get`: working is running, idle or done with a report written since
the start ends `done`, idle or done without one after status has seen it working is `idle`
("waiting: the agent ended its turn without a report"; an agent never seen working stays
running, as it may not have started its turn; wait keeps polling it), an agent that is gone
ends `done` with a report and `failed` without one, and blocked is surfaced without automatic
input. The orchestrator reads the dialog, answers questions it can decide from the spec and
earlier authorizations, never answers trust/permission reviews, and escalates material decisions.
wait polls status: exit 0 ended, 5 blocked (read the dialog and apply that rule), 1 still running at its
timeout. result prints {dispatch, status, report, usage} (exit 1 while running). cancel ends
`cancelled` and stops the worker; it never delivers a pending brief (it clears `prompt_pending`
under the state lock first) (headless: SIGTERM, then SIGKILL, to the supervisor's and the
CLI's process groups). Other exit codes: 1 a run or launch failure, 2 bad arguments.
"""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

try:
    import fcntl
except ImportError:  # Windows: state files are written without a lock.
    fcntl = None

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
import project
import task_state
from task_state import StateError, git
import xreview

RUNTIMES = ("claude", "codex")
ORCHESTRATOR_ENV = "HARNESS_ORCHESTRATOR"  # start's orchestrator when --orchestrator is absent
WRITERS = ("implementer", "simplifier")
WRITER_TOOLS = "Bash,Read,Edit,Write,Grep,Glob"
CAPABILITIES = ("candidate",)
CANDIDATE_TOOLS = ("Read", "Grep", "Glob", "Bash")
# Claude Code's Bash sandbox for a candidate: shell writes only in the cwd (the snapshot), the
# --add-dir scratch and the per-user temp directory; no unsandboxed retry, and no start at all
# where the sandbox cannot run (fail closed). Given with --settings, a repository's own
# settings cannot loosen it; sandbox_settings adds the denyWrite list of one candidate.
SANDBOX = {"enabled": True, "failIfUnavailable": True, "allowUnsandboxedCommands": False}
# The settings a candidate Claude loads besides --settings and managed settings: never the
# user's own (~/.claude/settings.json), whose sandbox allowWrite would reopen the source; the
# project's (the snapshot's) carry the guard hooks.
CANDIDATE_SETTING_SOURCES = "project,local"
# What a candidate can read: its writes are confined, its reads are not (the source and the
# run directory, a rival's report included, are readable by absolute path).
CANDIDATE_READS = "source and run directory (independence rests on the brief)"
# Where a candidate's runtime lets it write (candidate_flags); the snapshot is <scratch>/src.
CANDIDATE_WRITE_ROOTS = {
    "claude": ("scratch (Edit(//<scratch>/**) for the file tools)",
               "snapshot and scratch for Bash (sandbox: the working directory and --add-dir)",
               "per-user temp directory (Bash)"),
    "codex": ("snapshot (workspace-write -C <scratch>/src)", "scratch (--add-dir)",
              "temp directory (workspace-write)"),
}
# Set to "1" in every candidate's environment; runtime_hook.py exempts only such a snapshot
# without a base from the drift check.
CANDIDATE_SNAPSHOT_ENV = "HARNESS_CANDIDATE_SNAPSHOT"
# The candidate line of a brief starts with this; compare strips the line by it.
CANDIDATE_BRIEF_PREFIX = "The source checkout is "
SNAPSHOT_DIR = "src"  # the snapshot is <scratch>/src
SNAPSHOT_MARKER = "dev-harness-snapshot"  # <snapshot>/.git/<marker> holds the dispatch id
RUN_STATE = ".harness"  # git-ignored run state: never copied into a snapshot
LAUNCHERS = ("headless", "pane")
WATCH_MINUTES = 20
BLOCKED_REASON = ("read the dialog; answer questions you can decide from the spec and earlier authorizations; "
                  "never answer trust/permission reviews; escalate material decisions to the user")
TIERS = range(4)


def pane_signals(role: str, minutes: int, *, needs_user: bool, outlives_turn: bool,
                 other_repo: str | None, tier: int | None, compare_candidate: bool) -> list[str]:
    """Why the lane deserves a pane, as clauses of one sentence; empty when it does not."""
    signals = []
    if minutes > WATCH_MINUTES:
        signals.append(f"expected {minutes} min is over the {WATCH_MINUTES} min the user "
                       "watches or steers")
    if tier == 3 and role in WRITERS:
        signals.append(f"a tier-3 {role} is a writer the user watches or steers")
    if compare_candidate:
        signals.append("a compare candidate is watched by the user")
    if needs_user:
        signals.append("it needs the user's answers or approvals mid-way")
    if outlives_turn:
        signals.append("it must outlive the orchestrator's turn")
    if other_repo:
        signals.append(f"it works in another repository ({other_repo})")
    return signals


def decide(role: str, runtime: str, orchestrator: str, minutes: int, *, herdr: bool,
           needs_user: bool = False, outlives_turn: bool = False, other_repo: str | None = None,
           tier: int | None = None, compare_candidate: bool = False) -> dict:
    """The launcher for one worker by the lane rule; ValueError on a bad argument.

    other_repo is the other Git top-level the worker runs in (None for this checkout) and herdr
    whether herdr is available; resolving either is the caller's job (see other_repository).
    """
    if not isinstance(role, str) or not role:
        raise ValueError("role must be a non-empty name")
    for name, value in (("runtime", runtime), ("orchestrator", orchestrator)):
        if value not in RUNTIMES:
            raise ValueError(f"unknown {name} {value!r}; expected one of {', '.join(RUNTIMES)}")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
        raise ValueError(f"minutes must be a non-negative integer, got {minutes!r}")
    if tier is not None and (isinstance(tier, bool) or tier not in TIERS):
        raise ValueError(f"tier must be 0-3, got {tier!r}")
    signals = pane_signals(role, minutes, needs_user=needs_user, outlives_turn=outlives_turn,
                           other_repo=other_repo, tier=tier, compare_candidate=compare_candidate)
    if signals:
        why = "; ".join(signals)
        if herdr:
            return {"launcher": "pane", "reason": f"herdr pane: {why}.", "herdr": True}
        return {"launcher": "headless", "reason": f"no herdr: {why}, so it runs headless.",
                "herdr": False}
    if runtime != orchestrator:
        reason = (f"headless: a {runtime} worker under a {orchestrator} orchestrator, "
                  "short and report-only.")
        return {"launcher": "headless", "reason": reason, "herdr": herdr}
    reason = f"subagent: same runtime as the orchestrator ({runtime}), short and report-only."
    return {"launcher": "subagent", "reason": reason, "herdr": herdr}


def toplevel(path: Path) -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=path)).resolve()


def common_dir(path: Path) -> Path:
    """The repository every worktree of path shares (its resolved Git common directory)."""
    found = Path(git("rev-parse", "--path-format=absolute", "--git-common-dir", cwd=path))
    return (found if found.is_absolute() else path / found).resolve()


def other_repository(repo: str | None, cwd: Path) -> str | None:
    """The Git top-level of repo when it is another repository than cwd's, else None.

    A linked worktree of the same repository shares its Git common directory, so it is not
    another repository even though its top-level differs."""
    if repo is None:
        return None
    path = (cwd / repo).resolve()
    if not path.is_dir():
        raise ValueError(f"--repo {repo} is not a directory")
    try:
        theirs = toplevel(path)
    except StateError as exc:
        raise ValueError(f"--repo {repo} is not inside a Git repository: {exc}") from exc
    try:
        toplevel(cwd)
        ours = common_dir(cwd)
    except StateError as exc:
        raise ValueError(f"the current directory is not inside a Git repository: {exc}") from exc
    return None if common_dir(path) == ours else str(theirs)


# --- launchers ---------------------------------------------------------------------------

HERDR_LIMIT = 3
# The overlay's `project.json → lanes`: each key given replaces its default.
LANE_SETTINGS = {"herdr_max_agents": HERDR_LIMIT, "count": "workspace"}
COUNTS = ("workspace", "run")
DEFAULT_TIMEOUT = 45 * 60
STALE_MINUTES = 15  # status's stale window; dashboard.py shares it
ENDED = ("done", "failed", "timeout", "cancelled")
TOOL_ITEMS = ("command_execution", "file_change", "mcp_tool_call", "web_search")
EXIT_SUBAGENT, EXIT_QUEUED, EXIT_BLOCKED = 3, 4, 5
IDLE_REASON = "waiting: the agent ended its turn without a report"


class LaneError(Exception):
    """A run, launch or herdr failure (exit 1)."""


class Queued(Exception):
    """Pane work over the herdr agent limit; the orchestrator retries later (exit 4)."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def slug(text: str, limit: int = 32) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "-", text.lower()).strip("-_")
    value = value if value[:1].isalpha() else f"w-{value}"
    return value[:limit].rstrip("-_")


def run_directory(task: str) -> tuple[Path, Path]:
    """The primary checkout and the initialized run directory of task."""
    primary, _ = task_state.checkouts()
    run = task_state.locate(primary, task)
    if not task_state.evidence_path(run / "run.md", primary).is_file():
        raise LaneError(f"Task is not initialized; run task_state.py init first: {task}")
    return primary, run


def lane_settings(cwd: Path) -> dict:
    """The overlay's `project.json → lanes` of cwd's checkout over LANE_SETTINGS; ValueError
    (exit 2) on an unusable overlay, an unknown key or an invalid value. Outside a checkout
    there is no overlay, so the defaults hold."""
    try:
        root = project.repo_root(cwd)
    except project.OverlayError:
        return dict(LANE_SETTINGS)
    try:
        values = project.load(root, "project.json", {}).get("lanes", {})
    except project.OverlayError as exc:
        raise ValueError(f"cannot use {exc}") from None
    if not isinstance(values, dict):
        raise ValueError("project.json: lanes must be an object")
    unknown = sorted(set(values) - set(LANE_SETTINGS))
    if unknown:
        raise ValueError(f"project.json: unknown lanes keys: {', '.join(unknown)}; expected "
                         f"{', '.join(LANE_SETTINGS)}")
    result = {**LANE_SETTINGS, **values}
    limit = result["herdr_max_agents"]
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError("project.json: lanes.herdr_max_agents must be an integer >= 1, got "
                         f"{limit!r}")
    if result["count"] not in COUNTS:
        raise ValueError(f"project.json: lanes.count must be one of {', '.join(COUNTS)}, got "
                         f"{result['count']!r}")
    return result


def lanes_dir(run: Path, primary: Path) -> Path:
    path = task_state.evidence_path(run / "lanes", primary)
    path.mkdir(exist_ok=True)
    return path


# State files: written whole at start, then updated field by field under a lock.

def locked(stream, exclusive: bool) -> None:
    if fcntl is not None:
        fcntl.flock(stream, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)


def load_state(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        locked(stream, False)
        return json.load(stream)


def rewrite(stream, data: dict) -> None:
    stream.seek(0)
    stream.truncate()
    json.dump(data, stream, indent=1)
    stream.flush()


def update_state(path: Path, **fields) -> dict:
    """Merge fields into the state file under its lock; the state as it was before."""
    with path.open("r+", encoding="utf-8") as stream:
        locked(stream, True)
        data = json.load(stream)
        before = dict(data)
        data.update(fields)
        rewrite(stream, data)
    return before


def claim(path: Path, field: str) -> bool:
    """Turn a true field false under the lock; True only for the one caller that did."""
    with path.open("r+", encoding="utf-8") as stream:
        locked(stream, True)
        data = json.load(stream)
        if not data.get(field):
            return False
        data[field] = False
        rewrite(stream, data)
        return True


def herdr_lock_path() -> Path:
    """The machine-wide lock file of the caller's herdr workspace, shared by every run."""
    workspace = re.sub(r"[^A-Za-z0-9_.-]", "_", os.environ.get("HERDR_WORKSPACE_ID") or "default")
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    folder = Path(runtime) if runtime and Path(runtime).is_dir() else Path(tempfile.gettempdir())
    return folder / f"dev-harness-herdr-{workspace}.lock"


@contextlib.contextmanager
def herdr_lock():
    """Serialize counting herdr agents and launching a pane among all starts in the workspace:
    the agent limit is per workspace, so a lock per run would let two runs both pass it."""
    with herdr_lock_path().open("a", encoding="utf-8") as stream:
        locked(stream, True)  # released when the file closes
        yield


def state_path(run: Path, primary: Path, dispatch: str) -> Path:
    task_state.identifier(dispatch)
    path = task_state.evidence_path(run / "lanes" / f"{dispatch}.json", primary)
    if not path.is_file():
        raise LaneError(f"No such dispatch in {run.name}: {dispatch}")
    return path


def dispatch_states(run: Path) -> list[Path]:
    folder = run / "lanes"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.json") if not p.name.startswith("_"))


# Events: one start, then exactly one end with the deterministic id "<dispatch>.end".

def record(state: dict, kind: str, fields: dict, event_id: str | None = None) -> None:
    primary, run = Path(state["primary"]), Path(state["run"])
    entry = task_state.event_record(kind, json.dumps(fields), event_id)
    if task_state.append_event(task_state.evidence_path(run / "events.jsonl", primary), entry):
        regenerate_dashboard(run, primary)


def regenerate_dashboard(run: Path, primary: Path) -> None:
    """Best effort after a transition: rewrite the run's default dashboard, never fail."""
    try:
        import dashboard  # imported late: a broken dashboard must not break the lanes
        dashboard.regenerate(run, primary)
    except Exception as exc:  # noqa: BLE001
        print(f"lanes: dashboard not regenerated: {exc}", file=sys.stderr)


def end_of(state: dict) -> dict | None:
    events, _ = task_state.read_events(Path(state["run"]))
    return next((e for e in events if e.get("type") == "dispatch.end"
                 and e.get("dispatch") == state["dispatch"]), None)


def finish(state: dict, status: str, usage: dict | None = None,
           report_source: str | None = None) -> dict:
    """Write the dispatch's one end; an end already written wins and is returned.
    report_source, when given, says where the report came from ("final_message")."""
    fields = {"dispatch": state["dispatch"], "status": status, "usage": usage}
    if report_source:
        fields["report_source"] = report_source
    record(state, "dispatch.end", fields, event_id=f"{state['dispatch']}.end")
    return end_of(state)


# Capability profiles.

def capability_profile(runtime: str, launcher: str, capability: str | None = None, *,
                       role: str | None = None, reads: bool = False,
                       write_roots: bool = False) -> dict:
    """What a worker started by lanes gets, {read_source, shell, writes}, comparable across
    workers; ValueError for a runtime, launcher or capability lanes does not launch.

    reads=True adds "reads", what the worker can read, for a caller to report: a candidate's
    is CANDIDATE_READS, any other worker's is not confined by lanes either. write_roots=True
    adds "write_roots", where the runtime actually lets it write: a candidate's per runtime
    (CANDIDATE_WRITE_ROOTS; all inside the scratch but the temp directory), any other worker's
    restates its writes ([], ["checkout"] or "as-approved")."""
    profile = base_profile(runtime, launcher, capability, role)
    if reads:
        profile["reads"] = (CANDIDATE_READS if capability == "candidate"
                            else "not confined by lanes")
    if write_roots:
        writes = profile["writes"]
        profile["write_roots"] = (list(CANDIDATE_WRITE_ROOTS[runtime]) if capability == "candidate"
                                  else writes if writes == "as-approved"
                                  else [] if writes == "none" else [writes])
    return profile


def base_profile(runtime: str, launcher: str, capability: str | None,
                 role: str | None) -> dict:
    if runtime not in RUNTIMES:
        raise ValueError(f"unknown runtime {runtime!r}; expected one of {', '.join(RUNTIMES)}")
    if launcher not in LAUNCHERS:
        raise ValueError(f"lanes launches no {launcher!r} worker; expected one of "
                         f"{', '.join(LAUNCHERS)}")
    if capability is not None and capability not in CAPABILITIES:
        raise ValueError(f"unknown capability {capability!r}; expected one of "
                         f"{', '.join(CAPABILITIES)}")
    if capability == "candidate":
        return {"read_source": True, "shell": True, "writes": "scratch"}
    if launcher == "pane":  # interactive: the user approves each shell command and write
        return {"read_source": True, "shell": "as-approved", "writes": "as-approved"}
    if role in WRITERS:  # acceptEdits with Bash / the workspace-write sandbox on the checkout
        return {"read_source": True, "shell": True, "writes": "checkout"}
    # Claude's default -p mode runs no shell; the read-only Codex sandbox runs commands.
    return {"read_source": True, "shell": runtime == "codex", "writes": "none"}


def candidate_flags(runtime: str, scratch: str, snapshot: str, *, headless: bool,
                    deny_write: list[str] = ()) -> list[str]:
    """The candidate profile's flags: a shell and the snapshot, writes only inside scratch.

    deny_write (write_denials) goes into Claude's sandbox settings as denyWrite."""
    if runtime == "codex":
        # --add-dir makes the scratch, where the report lives, writable beside the snapshot.
        # Only `codex exec` takes --skip-git-repo-check; the snapshot is a clone, so the
        # interactive codex of a pane finds a repository anyway.
        return ["--sandbox", "workspace-write", "-C", snapshot, "--add-dir", scratch,
                *(["--skip-git-repo-check"] if headless else [])]
    # `//` makes a permission rule's path absolute; dontAsk denies whatever is not listed; the
    # sandbox confines Bash, which the permission rules do not.
    return ["--permission-mode", "dontAsk", "--allowedTools", *CANDIDATE_TOOLS,
            f"Write(/{scratch}/**)", f"Edit(/{scratch}/**)", "--add-dir", scratch,
            "--setting-sources", CANDIDATE_SETTING_SOURCES,
            "--settings", sandbox_settings(deny_write)]


def sandbox_settings(deny_write: list[str]) -> str:
    """The candidate's --settings JSON: the fail-closed sandbox and its denyWrite paths."""
    return json.dumps({"sandbox": {**SANDBOX, "filesystem": {"denyWrite": list(deny_write)}}},
                      separators=(",", ":"))


def write_denials(scratch: Path, roots: list[Path]) -> list[str]:
    """The paths a candidate's sandbox denies writing: each root (the source checkout, its
    repository, the run directory) whole, or, when the scratch lies inside it, every entry
    beside the path down to the scratch (a denyWrite holds inside an allowed directory, so an
    ancestor of the scratch cannot be denied). Entries created later beside that path are not
    covered. Symlinks are skipped (a deny would follow them); a path inside another is dropped.
    """
    scratch = Path(scratch).resolve()
    found: list[Path] = []
    for root in (Path(r).resolve() for r in roots):
        if root == scratch or scratch in root.parents:
            continue  # inside the scratch: the candidate's own
        if root not in scratch.parents:
            found.append(root)
            continue
        node = root
        for part in scratch.relative_to(root).parts:
            with contextlib.suppress(OSError):
                found += [child for child in sorted(node.iterdir())
                          if child.name != part and not child.is_symlink()]
            node = node / part
    unique = list(dict.fromkeys(found))
    return [str(p) for p in unique if not any(q in p.parents for q in unique)]


def candidate_denials(state: dict) -> list[str]:
    """write_denials of a candidate: its source checkout, that repository's Git directory, the
    primary checkout and the run directory."""
    checkout = Path(state["checkout"])
    roots = [toplevel(checkout), common_dir(checkout), Path(state["primary"]),
             Path(state["run"])]
    return write_denials(Path(state["scratch"]), roots)


def candidate_note(state: dict) -> str:
    """The sentence a candidate's brief and prompt carry, empty for other workers."""
    if state.get("capability") != "candidate":
        return ""
    return (f"{CANDIDATE_BRIEF_PREFIX}{state['checkout']}; you work in {state['snapshot']}, a "
            "private copy of it as it is now (your working directory): read and change the "
            f"copy, change nothing in the source; write only inside {state['scratch']}.")


def last_message(state: dict) -> Path:
    """Where headless Codex writes its last message: beside a candidate's report, so a report
    the candidate wrote as it went is never overwritten; other workers write the report."""
    if state.get("capability") == "candidate":
        return Path(f"{state['report']}.last-message.md")
    return Path(state["report"])


def workdir(state: dict) -> Path:
    """Where the worker runs: the snapshot's copy of --checkout for a candidate."""
    return Path(state.get("workdir") or state["checkout"])


def run_git(args: list[str], cwd: Path, data: bytes | None = None) -> bytes:
    done = subprocess.run(["git", *args], cwd=cwd, input=data, capture_output=True)
    if done.returncode:
        raise LaneError(f"git {args[0]}: {os.fsdecode(done.stderr).strip() or done.returncode}")
    return done.stdout


def take_snapshot(checkout: Path, snapshot: Path, dispatch: str) -> tuple[str, Path]:
    """Copy the checkout's repository as it is now into snapshot, an independent clone.

    HEAD is cloned without hardlinks, alternates or a remote (so nothing in it reaches the
    source's objects, refs or path), the working-tree diff against HEAD is applied and the
    untracked files that are not ignored are copied (run state excluded). Returns the base
    commit and the worker's directory in the snapshot; LaneError (the partial copy removed)
    on a failure."""
    top = toplevel(checkout)
    try:
        base = git("rev-parse", "--verify", "HEAD^{commit}", cwd=top)
    except StateError:
        raise LaneError(f"the checkout {checkout} has no commit to snapshot") from None
    diff = run_git(["diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv", "--no-color"],
                   top)
    untracked = [os.fsdecode(name) for name in
                 run_git(["ls-files", "--others", "--exclude-standard", "-z"], top).split(b"\0")
                 if name]
    modules = submodule_files(top)
    try:
        for depth in (["--depth", "1"], []):
            shutil.rmtree(snapshot, ignore_errors=True)
            try:
                run_git(["-c", "protocol.file.allow=always", "clone", "-q", "--no-hardlinks",
                         *depth, top.as_uri(), str(snapshot)], top.parent)
                run_git(["checkout", "-q", "--detach", base], snapshot)
                break
            except LaneError:
                if not depth:
                    raise
        run_git(["remote", "remove", "origin"], snapshot)
        if (snapshot / ".git" / "objects" / "info" / "alternates").exists():
            raise LaneError("the snapshot clone borrows the source's objects")
        if diff:
            run_git(["apply", "--whitespace=nowarn", "-"], snapshot, diff)
        for name in untracked:
            if Path(name).parts[0] == RUN_STATE:
                continue
            source, target = top / name, snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name.endswith("/") and source.is_dir() and not source.is_symlink():
                shutil.copytree(source, target, symlinks=True)  # a nested repository
            elif source.is_symlink() or source.is_file():
                shutil.copy2(source, target, follow_symlinks=False)
        for name in modules:
            source, target = top / name, snapshot / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
        (snapshot / ".git" / SNAPSHOT_MARKER).write_text(dispatch + "\n", encoding="utf-8")
    except (LaneError, OSError) as exc:
        shutil.rmtree(snapshot, ignore_errors=True)
        raise LaneError(f"cannot snapshot {top}: {exc}") from None
    return base, snapshot / checkout.relative_to(top)


def submodule_files(top: Path) -> list[str]:
    """The working-tree files of top's initialized submodules, nested ones included, relative
    to top: tracked and untracked non-ignored, never a .git and never run state. A clone leaves
    a submodule empty, so these are copied in; nothing is fetched, and a submodule that is not
    initialized in the source stays empty in the snapshot too."""
    listing = run_git(["submodule", "--quiet", "foreach", "--recursive",
                       'printf "%s\\0" "$displaypath"'], top)
    files: dict[str, None] = {}
    for module in (os.fsdecode(m) for m in listing.split(b"\0") if m):
        if Path(module).parts[0] == RUN_STATE:
            continue
        names = run_git(["ls-files", "--cached", "--others", "--exclude-standard", "-z"],
                        top / module)
        for name in (os.fsdecode(n) for n in names.split(b"\0") if n):
            path = top / module / name
            if ".git" in Path(name).parts or path.is_dir() and not path.is_symlink():
                continue  # a nested submodule is listed by its own entry
            if path.is_symlink() or path.is_file():
                files[f"{module}/{name}"] = None
    return list(files)


def snapshot_cleanup(run: Path, dispatches: list[str] | None = None) -> list[dict]:
    """Remove the snapshots of the run's ended candidates (only those of dispatches, when
    given); only a directory lanes recorded and marked for that dispatch is removed, never a
    running candidate's. A filtered dispatch with no recorded snapshot gets a row too."""
    wanted = None if dispatches is None else list(dict.fromkeys(dispatches))
    rows, seen = [], set()
    for path in dispatch_states(run):
        state = load_state(path)
        if not state.get("snapshot") or wanted is not None and state["dispatch"] not in wanted:
            continue
        seen.add(state["dispatch"])
        snap = Path(state["snapshot"])
        row = {"dispatch": state["dispatch"], "snapshot": str(snap), "removed": False}
        marker = snap / ".git" / SNAPSHOT_MARKER
        if end_of(state) is None:
            row["reason"] = "the dispatch has not ended"
        elif not snap.exists() and not snap.is_symlink():
            row["reason"] = "already removed"
        elif (snap.is_symlink() or snap.name != SNAPSHOT_DIR
              or snap.parent != Path(state.get("scratch") or "")
              or not marker.is_file() or marker.is_symlink()
              or marker.read_text(encoding="utf-8").strip() != state["dispatch"]):
            row["reason"] = "not a snapshot lanes created for this dispatch; left in place"
        else:
            try:
                shutil.rmtree(snap)
            except OSError as exc:
                row["reason"] = f"could not remove it: {exc}"
            else:
                update_state(path, snapshot_removed=now())
                row.update(removed=True, reason="removed")
        rows.append(row)
    rows += [{"dispatch": d, "snapshot": None, "removed": False,
              "reason": "no snapshot recorded for this dispatch in the run"}
             for d in wanted or () if d not in seen]
    return rows


# Worker command lines.

def worker_flags(runtime: str, model: str, effort: str, checkout: Path,
                 report: Path) -> tuple[list[str], dict]:
    """Model and effort flags (and env), reusing xreview's command and its Codex guard."""
    argv, env = xreview.command(runtime, model, effort, checkout, report)
    if runtime == "codex":
        hooks = next(arg for arg in argv if arg.startswith("hooks.PreToolUse="))
        return ["-m", model, "-c", f"model_reasoning_effort={effort}", "-c", hooks], env
    return ["--model", model], env


def worker_env(state: dict, env: dict) -> dict:
    """The worker's extra environment: a candidate's adds CANDIDATE_SNAPSHOT_ENV=1."""
    if state.get("capability") == "candidate":
        return {**env, CANDIDATE_SNAPSHOT_ENV: "1"}
    return env


def headless_command(state: dict) -> tuple[list[str], dict]:
    checkout, report = Path(state["checkout"]), Path(state["report"])
    flags, env = worker_flags(state["runtime"], state["model"], state["effort"], checkout, report)
    env = worker_env(state, env)
    writer = state["role"] in WRITERS
    candidate = state.get("capability") == "candidate"
    if state["runtime"] == "codex":
        sandbox = (candidate_flags("codex", state["scratch"], str(workdir(state)), headless=True)
                   if candidate else
                   ["--sandbox", "workspace-write" if writer else "read-only", "-C",
                    str(checkout)])
        return ["codex", "exec", "--json", *sandbox[:2], *flags, *sandbox[2:],
                "--output-last-message", str(last_message(state)), "-"], env
    if candidate:
        permission = candidate_flags("claude", state["scratch"], str(workdir(state)),
                                     headless=True, deny_write=candidate_denials(state))
    else:
        permission = (["--permission-mode", "acceptEdits", "--allowedTools", WRITER_TOOLS]
                      if writer else [])
    budget = (["--max-budget-usd", format(state["max_budget_usd"], "g")]
              if state.get("max_budget_usd") is not None else [])
    return ["claude", "-p", "--output-format", "stream-json", "--verbose", *flags,
            *permission, *budget], env


class Usage:
    """Usage read from a Claude stream-json or Codex --json stream; null fields stay null."""

    def __init__(self):
        self.seen = False
        self.tool_calls = 0
        self.input_tokens = self.output_tokens = self.duration_s = None
        self.cached_input_tokens = None
        self.result = None

    def add(self, key: str, value) -> None:
        if isinstance(value, int) and not isinstance(value, bool):
            setattr(self, key, (getattr(self, key) or 0) + value)

    def feed(self, line: bytes) -> None:
        try:
            event = json.loads(line)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "assistant":
            self.seen = True
            content = (event.get("message") or {}).get("content") or []
            self.tool_calls += sum(1 for item in content
                                   if isinstance(item, dict) and item.get("type") == "tool_use")
        elif kind == "result":
            self.seen = True
            usage = event.get("usage") or {}
            # Claude's input_tokens exclude cached input; Codex's include it. Count it here so
            # the two runtimes' tokens in are comparable.
            for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
                self.add("input_tokens", usage.get(key))
            self.add("cached_input_tokens", usage.get("cache_read_input_tokens"))
            self.add("output_tokens", usage.get("output_tokens"))
            if isinstance(event.get("duration_ms"), (int, float)):
                self.duration_s = event["duration_ms"] / 1000
            if isinstance(event.get("result"), str):
                self.result = event["result"]
        elif kind == "item.completed":
            self.seen = True
            if (event.get("item") or {}).get("type") in TOOL_ITEMS:
                self.tool_calls += 1
        elif kind == "turn.completed":
            self.seen = True
            usage = event.get("usage") or {}
            self.add("input_tokens", usage.get("input_tokens"))
            self.add("cached_input_tokens", usage.get("cached_input_tokens"))
            self.add("output_tokens", usage.get("output_tokens"))

    def summary(self, elapsed: float) -> dict | None:
        if not self.seen:
            return None
        duration = self.duration_s if self.duration_s is not None else round(elapsed, 1)
        out = {"tool_calls": self.tool_calls, "input_tokens": self.input_tokens,
               "output_tokens": self.output_tokens, "duration_s": duration}
        if self.cached_input_tokens is not None:
            out["cached_input_tokens"] = self.cached_input_tokens
        return out


def kill_group(pid: int | None, sig: int) -> None:
    if not pid:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError):
        pass


def alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:  # our own exited child is a zombie until reaped
        return os.waitpid(pid, os.WNOHANG) == (0, 0)
    except ChildProcessError:
        return True


def stop(pids: list[int | None], grace: float = 2.0) -> None:
    for pid in pids:
        kill_group(pid, signal.SIGTERM)
    deadline = time.monotonic() + grace
    while any(alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    for pid in pids:
        if alive(pid):
            kill_group(pid, signal.SIGKILL)


def supervise(path: Path) -> int:
    """The detached supervisor of one headless dispatch: run, log, time out, end."""
    state = load_state(path)
    argv, env = headless_command(state)
    began = time.monotonic()
    usage = Usage()
    last = last_message(state)
    if state["runtime"] == "codex" and last != Path(state["report"]):
        last.unlink(missing_ok=True)  # an earlier run's last message is not this run's
    with open(state["log"], "ab") as log:
        try:
            proc = subprocess.Popen(argv, cwd=workdir(state), env={**os.environ, **env},
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=log, start_new_session=True)
        except OSError as exc:
            log.write(f"lanes: cannot start {argv[0]}: {exc}\n".encode())
            finish(state, "failed")
            return 1
        update_state(path, child_pid=proc.pid)

        def feed_stdin():
            try:
                proc.stdin.write(state["prompt"].encode("utf-8"))
                proc.stdin.close()
            except OSError:
                pass

        def read_stdout():
            last = 0.0
            for line in proc.stdout:
                log.write(line)
                log.flush()
                usage.feed(line)
                if time.monotonic() - last >= 0.5:
                    last = time.monotonic()
                    update_state(path, last_activity=now())
            update_state(path, last_activity=now())

        threads = [threading.Thread(target=feed_stdin, daemon=True),
                   threading.Thread(target=read_stdout, daemon=True)]
        for thread in threads:
            thread.start()
        timed_out = False
        while proc.poll() is None:
            if time.monotonic() - began > state["timeout"]:
                timed_out = True
                stop([proc.pid])
                break
            time.sleep(0.1)
        proc.wait()
        threads[1].join(timeout=5)
    if timed_out:
        status = "timeout"
    else:
        status = "done" if proc.returncode == 0 else "failed"
    report = Path(state["report"])
    source = None
    if status == "done" and not report_fresh(state):
        if state["runtime"] == "claude" and usage.result:
            report.write_text(usage.result.rstrip("\n") + "\n", encoding="utf-8")
            if state.get("capability") == "candidate":
                source = "final_message"
        elif state["runtime"] == "codex" and last != report and last.is_file():
            shutil.copyfile(last, report)
            source = "final_message"
        if source:
            update_state(path, report_source=source)
    finish(state, status, usage.summary(time.monotonic() - began), report_source=source)
    return 0


# herdr.

def herdr(*args: str) -> dict:
    try:
        done = subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaneError(f"herdr {' '.join(args[:2])}: {exc}") from None
    if done.returncode:
        try:
            error = json.loads(done.stderr).get("error") or {}
        except ValueError:
            error = {}
        failure = LaneError(f"herdr {' '.join(args[:2])}: "
                            f"{error.get('message') or done.stderr.strip() or done.returncode}")
        failure.code = error.get("code")
        raise failure
    try:
        return (json.loads(done.stdout) if done.stdout.strip() else {}).get("result") or {}
    except ValueError:
        return {}


def herdr_agents() -> list[dict]:
    agents = herdr("agent", "list").get("agents") or []
    workspace = os.environ.get("HERDR_WORKSPACE_ID")
    return [a for a in agents if not workspace or a.get("workspace_id") == workspace]


def live_panes(run: Path) -> list[dict]:
    states = [load_state(path) for path in dispatch_states(run)]
    return [s for s in states if s.get("launcher") == "pane" and s.get("pane_id")
            and end_of(s) is None]


def place_pane(run: Path, primary: Path, task: str, checkout: Path, env: dict,
               other_repo: str | None = None) -> tuple[str, str | None]:
    """A new pane for the run: beside the caller first, then in one tab named after the task;
    a pane in another repository goes into that repository's own tab."""
    extra = [arg for key, value in env.items() for arg in ("--env", f"{key}={value}")]
    tabs = task_state.evidence_path(run / "lanes" / "_herdr.json", primary)
    known = json.loads(tabs.read_text(encoding="utf-8")) if tabs.is_file() else {}
    repos = known.setdefault("repos", {})
    tab, label = (repos.setdefault(other_repo, {}), Path(other_repo).name) if other_repo \
        else (known, task)
    if not other_repo and not [s for s in live_panes(run) if not s.get("other_repo")]:
        made = herdr("pane", "split", "--current", "--direction", "right", "--cwd", str(checkout),
                     "--no-focus", *extra)
        return made["pane"]["pane_id"], None
    if not tab.get("tab_id"):
        made = herdr("tab", "create", "--cwd", str(checkout), "--label", label, "--no-focus",
                     *extra)
        tab.update(tab_id=made["tab"]["tab_id"], last_pane=made["root_pane"]["pane_id"])
    else:
        made = herdr("pane", "split", tab["last_pane"], "--direction", "down", "--cwd",
                     str(checkout), "--no-focus", *extra)
        tab["last_pane"] = made["pane"]["pane_id"]
    tabs.write_text(json.dumps(known), encoding="utf-8")
    return tab["last_pane"], tab["tab_id"]


def lane_label(state: dict) -> str:
    """The readable label of a lane's pane: "🤖 <work> · <runtime> <model>"."""
    return f"🤖 {state['work']} · {state['runtime']} {state['model']}"


def agent_name(work: str, role: str, taken: set[str]) -> str:
    """A herdr agent name for the dispatch that no live agent has."""
    base = slug(f"{work}-{role}", 28)
    name, number = base, 2
    while name in taken:
        name, number = f"{base}-{number}", number + 1
    return name


def pane_launch(state: dict, path: Path, run: Path, primary: Path) -> None:
    checkout, report = Path(state["checkout"]), Path(state["report"])
    flags, env = worker_flags(state["runtime"], state["model"], state["effort"], checkout, report)
    env = worker_env(state, env)
    if state.get("capability") == "candidate":
        flags += candidate_flags(state["runtime"], state["scratch"], str(workdir(state)),
                                 headless=False, deny_write=candidate_denials(state))
    name = state["pane_name"]
    placed = None
    if not state.get("other_repo"):
        import dashboard_herdr  # imported late: it imports this module
        placed = dashboard_herdr.lane_pane(state, run, primary, workdir(state), env)
    pane, tab = placed or place_pane(run, primary, state["task"], workdir(state), env,
                                     state.get("other_repo"))
    update_state(path, pane_id=pane, tab_id=tab)
    state.update(pane_id=pane, tab_id=tab)
    with contextlib.suppress(LaneError):  # a label is a convenience, never a failed launch
        herdr("pane", "rename", pane, lane_label(state))
    try:
        started = herdr("agent", "start", name, "--kind", state["runtime"], "--pane", pane, "--",
                        *flags)
    except LaneError as exc:
        if getattr(exc, "code", None) == "agent_not_ready":
            # Blocked at startup: status surfaces the dialog for the orchestrator's decision;
            # trust/permission reviews stay with the user; the brief goes out once idle.
            update_state(path, prompt_pending=True)
            return
        raise
    agent = started.get("agent", started)
    update_state(path, agent_id=agent.get("agent_id"))
    send_prompt(state)


def send_prompt(state: dict) -> None:
    note = candidate_note(state)
    herdr("agent", "prompt", state["pane_name"], f"Read the brief at {state['brief']} and follow "
          f"it. Write your full report to {state['report']}." + (f" {note}" if note else ""))


def same_agent(state: dict, agent: dict) -> bool:
    """The agent herdr resolves by name is still the one this run started in its pane."""
    if state.get("pane_id") and agent.get("pane_id") != state["pane_id"]:
        return False
    return not (state.get("agent_id") and agent.get("agent_id")
                and agent["agent_id"] != state["agent_id"])


def not_ours(state: dict) -> str:
    return f"agent {state['pane_name']} is no longer the pane this run created"


def start(args, *, capability: str | None = None, scratch: str | None = None) -> int:
    """`lanes.py start`; capability and scratch, when given, override the args' own."""
    herdr_on = os.environ.get("HERDR_ENV") == "1"
    brief, checkout = Path(args.brief).resolve(), Path(args.checkout).resolve()
    if not brief.is_file():
        raise ValueError(f"--brief {args.brief} is not a file")
    if not checkout.is_dir():
        raise ValueError(f"--checkout {args.checkout} is not a directory")
    capability = capability if capability is not None else getattr(args, "capability", None)
    scratch = scratch if scratch is not None else getattr(args, "scratch", None)
    scratch_dir = checked_scratch(capability, scratch, checkout)
    if args.timeout <= 0:
        raise ValueError("--timeout must be positive")
    if args.max_budget_usd is not None and not args.max_budget_usd > 0:
        raise ValueError("--max-budget-usd must be positive")
    task_state.identifier(args.task)
    settings = lane_settings(Path.cwd())
    orchestrator, echo = start_orchestrator(getattr(args, "orchestrator", None))
    if args.launcher:
        if args.launcher == "pane" and not herdr_on:
            raise ValueError("--launcher pane needs herdr (HERDR_ENV=1)")
        launcher, reason = args.launcher, f"{args.launcher}: chosen by --launcher."
    else:
        decision = decide(args.role, args.runtime, orchestrator, args.minutes, herdr=herdr_on,
                          needs_user=args.needs_user, outlives_turn=args.outlives_turn,
                          other_repo=other_repository(str(checkout), Path.cwd()), tier=args.tier,
                          compare_candidate=args.compare_candidate or capability is not None)
        launcher, reason = decision["launcher"], decision["reason"]
    budget = {} if args.max_budget_usd is None else {"budget_enforced": False}
    budget.update(echo)  # printed beside the budget, never recorded
    if launcher == "subagent":
        shadow = shadow_for_args(args, "lanes.start")
        extra = {"shadow": shadow} if shadow is not None else {}
        print(json.dumps({"dispatch": None, "launcher": launcher, "reason": reason, "pane": None,
                          **budget, **extra}))
        return EXIT_SUBAGENT
    primary, run = run_directory(args.task)
    # Classify outside the Herdr launch lock. This never alters args or launcher.
    shadow = shadow_for_args(args, "lanes.start", primary=primary, run=run, emit=False)
    profile = {}
    if scratch_dir is not None:
        scratch_dir.mkdir(parents=True, exist_ok=True)
        profile = {"capability": capability, "scratch": str(scratch_dir)}
    try:
        with herdr_lock() if launcher == "pane" else contextlib.nullcontext():
            return launch(args, launcher, reason, budget, primary, run, brief, checkout, profile,
                          settings, shadow=shadow)
    except (Queued, LaneError, OSError):
        if shadow is not None:
            # Keep classification cost/evidence even if capacity/snapshot setup prevented a launch.
            # If launch already wrote the same decision ID, append is an idempotent no-op.
            import router_shadow
            router_shadow.append(run, primary, {**shadow, "launch_not_completed": True})
            regenerate_dashboard(run, primary)
        raise


def shadow_for_args(args, origin: str, *, primary: Path | None = None,
                    run: Path | None = None, emit: bool = True) -> dict | None:
    """Optional advisory data. Old commands without shadow configuration are unchanged."""
    import router_shadow
    try:
        root = project.repo_root(Path.cwd())
        try:
            configured_mode = router_shadow.mode(root)
        except Exception:
            configured_mode = "shadow"  # evaluate records the configuration failure safely.
        if configured_mode == "off":
            return None
        if run is None:
            if not getattr(args, "task", None):
                return {"mode": "shadow", "status": "skipped", "recommendation": None,
                        "reason": "shadow event needs --task; actual launcher retained"}
            primary, run = run_directory(args.task)
        actual = {"runtime": args.runtime, "model": getattr(args, "model", None),
                  "effort": getattr(args, "effort", None)}
        profile = getattr(args, "profile", None)
        if origin == "lanes.decide" and profile and not actual["model"]:
            import xreview
            models, _ = xreview.models(root)
            static = models.get(args.runtime, {}).get(profile, {})
            actual.update(model=static.get("model"), effort=static.get("effort"))
        event = router_shadow.evaluate(root, run, primary, actual=actual, profile=profile,
            role=args.role, tier=getattr(args, "tier", None), brief=getattr(args, "brief", None),
            weights=getattr(args, "router_weights", None), origin=origin,
            parent_attempt_id=getattr(args, "parent_attempt_id", None),
            root_attempt_id=getattr(args, "root_attempt_id", None),
            linked_decision_id=getattr(args, "router_decision_id", None))
        if emit:
            router_shadow.append(run, primary, event)
            regenerate_dashboard(run, primary)
        return event
    except Exception as exc:
        # No exception in an advisory path is allowed to mutate the actual launch.
        print(f"lanes: shadow skipped ({type(exc).__name__}); actual launcher retained",
              file=sys.stderr)
        return {"mode": "shadow", "status": "skipped", "recommendation": None,
                "reason": f"{type(exc).__name__}; shadow skipped"}


def start_orchestrator(flag: str | None) -> tuple[str, dict]:
    """start's orchestrator: --orchestrator, else $HARNESS_ORCHESTRATOR, else claude; with the
    {"orchestrator": ...} to print when it did not come from the flag. ValueError on an unknown
    runtime in the environment."""
    if flag:
        return flag, {}
    value = os.environ.get(ORCHESTRATOR_ENV)
    if value:
        if value not in RUNTIMES:
            raise ValueError(f"${ORCHESTRATOR_ENV}={value!r} is not one of {', '.join(RUNTIMES)}")
        return value, {"orchestrator": value}
    return "claude", {"orchestrator": "claude"}


def checked_scratch(capability: str | None, scratch: str | None, checkout: Path) -> Path | None:
    """The resolved scratch directory of a capability profile; ValueError on a bad pair."""
    if capability is None:
        if scratch is not None:
            raise ValueError("--scratch needs --capability candidate")
        return None
    if capability not in CAPABILITIES:
        raise ValueError(f"--capability must be one of {', '.join(CAPABILITIES)}, "
                         f"got {capability!r}")
    if not scratch:
        raise ValueError(f"--capability {capability} needs --scratch DIR")
    path = Path(scratch).resolve()
    if path.exists() and not path.is_dir():
        raise ValueError(f"--scratch {scratch} is not a directory")
    if path == checkout or path in checkout.parents:
        raise ValueError(f"--scratch {scratch} contains the source checkout {checkout}; the "
                         "candidate may write only outside it")
    try:
        top = toplevel(checkout)
    except StateError as exc:
        raise ValueError(f"--checkout {checkout} is not inside a Git repository, so a candidate "
                         f"has no snapshot: {exc}") from None
    if (path == top or top in path.parents) and (top / RUN_STATE) not in path.parents:
        raise ValueError(f"--scratch {scratch} is inside the source repository {top}; put it "
                         f"outside, or under {top / RUN_STATE}")
    if (path / SNAPSHOT_DIR).exists() or (path / SNAPSHOT_DIR).is_symlink():
        raise ValueError(f"--scratch {scratch} already holds a snapshot "
                         f"({path / SNAPSHOT_DIR}); a candidate never reuses one")
    return path


def launch(args, launcher: str, reason: str, budget: dict, primary: Path, run: Path,
           brief: Path, checkout: Path, profile: dict, settings: dict | None = None,
           *, shadow: dict | None = None) -> int:
    """Count, record and launch one dispatch (a pane under the workspace's herdr lock)."""
    name = None
    if launcher == "pane":
        settings = settings or LANE_SETTINGS
        agents = herdr_agents()
        mine = {s.get("pane_name") for s in live_panes(run)}
        name = agent_name(args.work, args.role,
                          {a.get("name") for a in agents if a.get("name")} | mine)
        counted = (agents if settings["count"] == "workspace"
                   else [a for a in agents if a.get("name") in mine])
        limit, scope = settings["herdr_max_agents"], (
            "" if settings["count"] == "workspace" else " of this run")
        if len(counted) >= limit:
            if args.role in WRITERS or args.needs_user:
                raise Queued(f"queued: {len(counted)} live herdr agents{scope} are at the limit "
                             f"of {limit}; retry when one ends.")
            launcher = "headless"
            reason = (f"headless: {len(counted)} live herdr agents{scope} are at the limit of "
                      f"{limit} and the work is report-only.")
    enforced = launcher == "headless" and args.runtime == "claude"
    if "budget_enforced" in budget:
        budget["budget_enforced"] = enforced
    folder = lanes_dir(run, primary)
    dispatch = slug(f"{args.work}-{args.role}", 40) + "-" + uuid.uuid4().hex[:6]
    shadow_fields = {}
    if shadow is not None and shadow.get("decision_id"):
        import router_shadow
        shadow = {**shadow, "dispatch": dispatch, "attempt_id": dispatch}
        if not shadow.get("parent_attempt_id"):
            shadow["root_attempt_id"] = shadow.get("root_attempt_id") or dispatch
        shadow_fields = router_shadow.dispatch_fields(shadow)
    where = None
    if profile:
        snapshot = Path(profile["scratch"]) / SNAPSHOT_DIR
        base, where = take_snapshot(checkout, snapshot, dispatch)
        profile = {**profile, "snapshot": str(snapshot), "snapshot_base": base}
    report = Path(args.report).resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    stamp = now()
    state = {"dispatch": dispatch, "task": args.task, "run": str(run), "primary": str(primary),
             "launcher": launcher, "reason": reason, "role": args.role, "runtime": args.runtime,
             "model": args.model, "effort": args.effort, "work": args.work,
             "report": str(report), "brief": str(brief), "checkout": str(checkout),
             "started": stamp, "last_activity": stamp, "timeout": args.timeout,
             "log": str(folder / f"{dispatch}.log"), "pid": None, "child_pid": None,
             "pane_id": None, "tab_id": None, "pane_name": name if launcher == "pane" else None,
             "agent_id": None, "prompt_pending": False, "report_before": report_stamp(report),
             "launch_pending": launcher == "pane",
             "launch_pid": os.getpid() if launcher == "pane" else None,
             "other_repo": other_repository(str(checkout), Path.cwd())
             if launcher == "pane" else None,
             "max_budget_usd": args.max_budget_usd if enforced else None,
             "capability": profile.get("capability"), "scratch": profile.get("scratch"),
             "snapshot": profile.get("snapshot"), "snapshot_base": profile.get("snapshot_base"),
             "workdir": str(where) if where else None,
             "prompt": brief.read_text(encoding="utf-8")
             + f"\n\nWrite your full report to {report}.\n", **shadow_fields}
    if profile:
        state["prompt"] += candidate_note(state) + "\n"
    path = task_state.evidence_path(folder / f"{dispatch}.json", primary)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(state, stream, indent=1)
    record(state, "dispatch.start", {
        "dispatch": dispatch, "role": args.role, "runtime": args.runtime, "model": args.model,
        "effort": args.effort, "launcher": launcher, "work": args.work, "report": str(report),
        "pane": state["pane_name"], **profile, **shadow_fields})
    if shadow is not None and shadow.get("decision_id"):
        router_shadow.append(run, primary, shadow)
        regenerate_dashboard(run, primary)
    try:
        if launcher == "headless":
            with open(state["log"], "ab") as log:
                supervisor = [sys.executable, str(Path(__file__).resolve()), "supervise", str(path)]
                proc = subprocess.Popen(supervisor, cwd=workdir(state), stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=log,
                                        start_new_session=True)
            update_state(path, pid=proc.pid)
        else:
            pane_launch(state, path, run, primary)
            update_state(path, launch_pending=False)
    except (LaneError, OSError) as exc:
        finish(state, "failed")
        raise LaneError(f"launch failed, dispatch {dispatch} ended failed: {exc}") from None
    print(json.dumps({"dispatch": dispatch, "launcher": launcher, "reason": reason,
                      "pane": state["pane_name"], **budget, **profile,
                      **({"shadow": shadow} if shadow is not None else {})}))
    return 0


def report_stamp(report: Path) -> list[int] | None:
    """The report file's [mtime_ns, size], None when there is no such file."""
    try:
        info = report.stat()
    except OSError:
        return None
    return [info.st_mtime_ns, info.st_size] if report.is_file() else None


def report_fresh(state: dict) -> bool:
    """A report written since the dispatch started: created, or its mtime or size changed."""
    current = report_stamp(Path(state["report"]))
    if current is None:
        return False
    if "report_before" not in state:  # a dispatch recorded before the stamp was kept
        return current[0] / 1e9 >= epoch(state["started"]) - 1
    return current != state["report_before"]


def status_of(path: Path, stale_minutes: float) -> dict:
    """One dispatch's status; a pane's observation is kept in its state file (observe)."""
    seen: dict = {}
    report = observed_status(path, stale_minutes, seen)
    if "agent_status" in seen:
        before = update_state(path, agent_status=seen["agent_status"], state=report["state"],
                              observed_at=now())
        if before.get("state") != report["state"]:
            state = load_state(path)
            regenerate_dashboard(Path(state["run"]), Path(state["primary"]))
    return report


def observed_status(path: Path, stale_minutes: float, seen: dict) -> dict:
    """status_of without the write: seen["agent_status"] is set once herdr reports the pane
    ("gone" when herdr no longer knows the agent)."""
    state = load_state(path)
    out = {"dispatch": state["dispatch"], "launcher": state["launcher"], "state": "unknown",
           "last_activity": state.get("last_activity"), "pane": state.get("pane_name"),
           "report": state["report"], "reason": None}
    end = end_of(state)
    if end is not None:
        return {**out, "state": end.get("status", "unknown")}
    if state["launcher"] == "headless":
        if not state.get("pid"):
            return {**out, "reason": "the supervisor has not recorded its pid yet"}
        if alive(state["pid"]):
            quiet = time.time() - (epoch(state.get("last_activity")) or time.time())
            if quiet > stale_minutes * 60:
                return {**out, "state": "stale",
                        "reason": f"no stream activity for {quiet / 60:.1f} min: liveness unknown"}
            return {**out, "state": "running"}
        stop([state.get("child_pid")])
        end = finish(state, "failed")
        return {**out, "state": end["status"], "reason": "the supervisor exited without an end"}
    try:
        agent = herdr("agent", "get", state["pane_name"])
    except LaneError as exc:
        if getattr(exc, "code", None) != "agent_not_found":
            return {**out, "reason": str(exc)}
        seen["agent_status"] = "gone"
        end = finish(state, "done" if report_fresh(state) else "failed")
        return {**out, "state": end["status"], "reason": "the herdr agent is gone"}
    agent = agent.get("agent", agent)
    current = agent.get("agent_status")
    seen["agent_status"] = current
    if state.get("prompt_pending"):
        if not same_agent(state, agent):
            return {**out, "reason": not_ours(state)}
        if current not in ("idle", "done"):
            return {**out, "state": "blocked",
                    "reason": "the agent has not finished starting; " + BLOCKED_REASON
                              + " (the brief is delivered once the agent is idle)"}
        if claim(path, "prompt_pending"):
            try:
                send_prompt(state)
            except LaneError as exc:
                update_state(path, prompt_pending=True)
                return {**out, "reason": f"the brief could not be delivered yet: {exc}"}
            if agent.get("agent_id") and not state.get("agent_id"):
                update_state(path, agent_id=agent["agent_id"])
        return {**out, "state": "running", "reason": "the brief was delivered to the ready agent"}
    if current == "blocked":
        return {**out, "state": "blocked",
                "reason": BLOCKED_REASON}
    if current in ("idle", "done") and report_fresh(state):
        return {**out, "state": finish(state, "done")["status"]}
    if current == "working" and not state.get("seen_working"):
        update_state(path, seen_working=True)
    if current in ("idle", "done") and state.get("seen_working"):
        return {**out, "state": "idle", "reason": IDLE_REASON}
    if current in ("idle", "done", "working"):
        return {**out, "state": "running", "reason": f"herdr agent {current}"}
    return {**out, "reason": f"herdr agent {current or 'unknown'}"}


def wait(path: Path, timeout: float, stale_minutes: float) -> tuple[int, dict]:
    deadline = time.monotonic() + timeout
    while True:
        report = status_of(path, stale_minutes)
        if report["state"] in ENDED:
            return 0, report
        if report["state"] == "blocked":
            return EXIT_BLOCKED, report
        if time.monotonic() >= deadline:
            return 1, report
        time.sleep(0.2)


def cancel(path: Path) -> dict:
    state = load_state(path)
    # A brief still pending must never go out on the way to stopping the agent: take the flag
    # under the state lock before status_of (which delivers it to a ready agent) can.
    pending = claim(path, "prompt_pending")
    report = status_of(path, STALE_MINUTES)
    if report["state"] in ENDED:
        return report
    if state["launcher"] == "headless":
        finish(state, "cancelled")
        stop([state.get("pid"), state.get("child_pid")])
    else:
        try:
            agent = herdr("agent", "get", state["pane_name"])
        except LaneError as exc:
            if getattr(exc, "code", None) != "agent_not_found":
                raise
            agent = None
        if agent is not None:
            if not same_agent(state, agent.get("agent", agent)):
                if pending:
                    update_state(path, prompt_pending=True)
                raise LaneError(f"{not_ours(state)}; nothing was sent")
            try:  # stop the run's own agent; its pane stays open
                herdr("agent", "send-keys", state["pane_name"], "ctrl+c", "ctrl+c")
            except LaneError:
                pass
        finish(state, "cancelled")
    return status_of(path, STALE_MINUTES)


def add_decision_flags(sub, required: bool) -> None:
    sub.add_argument("--role", required=True, help="the worker's role, e.g. implementer")
    sub.add_argument("--runtime", required=True, choices=RUNTIMES, help="the worker's CLI")
    sub.add_argument("--orchestrator", required=required, choices=RUNTIMES,
                     help="the runtime of the session that starts it" + (
                         "" if required else f" (default ${ORCHESTRATOR_ENV}, else claude)"))
    sub.add_argument("--minutes", required=required, default=0, type=int,
                     help="expected duration")
    sub.add_argument("--needs-user", action="store_true",
                     help="the worker needs the user's answers or approvals mid-way")
    sub.add_argument("--outlives-turn", action="store_true",
                     help="the work must keep running after the orchestrator's turn")
    sub.add_argument("--tier", type=int, help="the unit's process tier, 0-3")
    sub.add_argument("--compare-candidate", action="store_true",
                     help="the worker is a compare candidate")
    sub.add_argument("--profile", help="grade used only by the optional shadow router")
    sub.add_argument("--router-weights", help="explicit JSON trait weights; no classifier call")
    sub.add_argument("--router-decision-id", help="link a prior balance/decide shadow record")
    sub.add_argument("--parent-attempt-id", help="explicit preceding dispatch for a fix-up")
    sub.add_argument("--root-attempt-id", help="root dispatch of the same acceptance chain")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("decide", help="print the launcher for one worker")
    add_decision_flags(sub, required=True)
    sub.add_argument("--repo", help="the checkout the worker runs in (default: this one)")
    sub.add_argument("--task", help="initialized run for shadow evidence")
    sub.add_argument("--brief", help="compact task capsule or Markdown Goal/Contract sections")
    sub = commands.add_parser("start", help="decide, launch and record one worker")
    add_decision_flags(sub, required=False)
    sub.add_argument("--task", required=True)
    sub.add_argument("--work", required=True, help="the unit or candidate")
    sub.add_argument("--model", required=True)
    sub.add_argument("--effort", required=True)
    sub.add_argument("--brief", required=True, help="the file the worker reads")
    sub.add_argument("--report", required=True, help="where the worker writes its report")
    sub.add_argument("--checkout", required=True, help="the directory the worker runs in")
    sub.add_argument("--launcher", choices=("headless", "pane"), help="skip the decision")
    sub.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                     help=f"headless limit in seconds (default {DEFAULT_TIMEOUT})")
    sub.add_argument("--max-budget-usd", type=float,
                     help="spend limit, passed to headless Claude only")
    sub.add_argument("--capability", choices=CAPABILITIES,
                     help="one profile on both vendors: read the source, a shell, writes only "
                          "in --scratch")
    sub.add_argument("--scratch", help="the only directory a candidate writes (created)")
    for name in ("status", "wait", "result", "cancel"):
        sub = commands.add_parser(name)
        sub.add_argument("--task", required=True)
        sub.add_argument("--dispatch", required=name != "status")
        if name in ("status", "wait"):
            sub.add_argument("--stale-minutes", type=float, default=STALE_MINUTES,
                             help="minutes without stream activity before a headless "
                                  f"dispatch is stale (default {STALE_MINUTES})")
        if name == "wait":
            sub.add_argument("--timeout", type=float, default=3600.0, help="seconds to wait")
    sub = commands.add_parser("snapshot-cleanup",
                              help="remove the snapshots of the run's ended candidates")
    sub.add_argument("--task", required=True)
    sub.add_argument("--dispatch", action="append", dest="dispatches", metavar="D",
                     help="only this dispatch's snapshot (repeatable; default: every candidate)")
    sub = commands.add_parser("dashboard", help="the run's herdr dashboard tab")
    sub.add_argument("--task", required=True)
    action = sub.add_mutually_exclusive_group()
    action.add_argument("--open", action="store_true", help="open it (once per run)")
    action.add_argument("--close", action="store_true",
                        help="close the run's own dashboard panes (a live lane's pane stays)")
    sub = commands.add_parser("supervise", help="internal: the headless supervisor")
    sub.add_argument("state")
    args = parser.parse_args()
    try:
        if args.command == "decide":
            other = other_repository(args.repo, Path.cwd())
            report = decide(args.role, args.runtime, args.orchestrator, args.minutes,
                            herdr=os.environ.get("HERDR_ENV") == "1", needs_user=args.needs_user,
                            outlives_turn=args.outlives_turn, other_repo=other, tier=args.tier,
                            compare_candidate=args.compare_candidate)
            shadow = shadow_for_args(args, "lanes.decide")
            if shadow is not None:
                report["shadow"] = shadow
            print(json.dumps(report))
            return 0
        if args.command == "start":
            return start(args)
        if args.command == "supervise":
            return supervise(Path(args.state))
        if args.command == "dashboard":
            import dashboard_herdr  # imported late: it imports this module
            return dashboard_herdr.command(args.task, "open" if args.open
                                           else "close" if args.close else "show")
        primary, run = run_directory(args.task)
        if args.command == "snapshot-cleanup":
            print(json.dumps(snapshot_cleanup(run, args.dispatches)))
            return 0
        if args.command == "status" and not args.dispatch:
            print(json.dumps([status_of(p, args.stale_minutes) for p in dispatch_states(run)]))
            return 0
        path = state_path(run, primary, args.dispatch)
        if args.command == "status":
            print(json.dumps(status_of(path, args.stale_minutes)))
            return 0
        if args.command == "wait":
            code, report = wait(path, args.timeout, args.stale_minutes)
            print(json.dumps(report))
            return code
        if args.command == "cancel":
            print(json.dumps(cancel(path)))
            return 0
        report = status_of(path, STALE_MINUTES)
        end = end_of(load_state(path))
        print(json.dumps({"dispatch": report["dispatch"], "status": report["state"],
                          "report": report["report"], "usage": end.get("usage") if end else None}))
        return 0 if end else 1
    except Queued as exc:
        print(str(exc))
        return EXIT_QUEUED
    except ValueError as exc:
        print(f"lanes: {exc}", file=sys.stderr)
        return 2
    except (LaneError, StateError, OSError) as exc:
        print(f"lanes: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
