"""Observe a run's lanes and product; emit new conditions for its orchestrator to act on.

watch.py --task T [--once] [--every S] [--quiet-repeat S] [--ack DISPATCH]
Defaults: poll every 30 seconds; repeat an open condition after 600 seconds. Cleared
conditions re-arm immediately. State is <run>/lanes/_watch.json; emitted lines are
also locked `watch` events. This observer never answers dialogs or stops processes.

project.json -> watch: {checks: [{name, command, every: 120}], frozen_minutes: 15,
stop_command: "product's own stop command"}. Commands run in the product checkout
through its shell, with a 60-second timeout; stdout lines are anomalies, nonzero or
timeout emits CHECK <name> failed. stop_command is for the orchestrator, not executed
here. Without herdr, only persisted dispatch observations and product checks apply.
Lane conditions: done only once the dispatch ended with a report written since its start, or
a pane's agent is idle/done with one (a report written while it works, or progress notes in
<report>.progress.md, are not done; a vanished agent or process is exited until its end);
ended-no-report; exited; needs-input <dialog>; needs-input flag (lanes.flag_raised: the
canonical <run>/lanes/<work>.flag.md, an open tripwire flag of the unit, or <report>.flag.md
written since the start; the text is never read; removing or resolving it clears it);
needs-input hook-trust (Codex's startup review of new or changed hooks, with the one key the
user presses: t, Trust all and continue); frozen N only for a working agent whose screen
(spinner glyph and elapsed timer of its working-indicator line normalized, so one stuck tool
call still freezes) and checkout files (tracked and untracked, not ignored; .git and .harness/
excluded) have not changed for frozen_minutes. Activity that cannot be read (no checkout
recorded, git failing, more than ACTIVITY_MAX_FILES files or ACTIVITY_SECONDS of scanning)
never proves a freeze; it is said once per dispatch as "LANE <work> activity-unknown (<why>;
frozen cannot be detected)" and never repeated. A failed
screen/state read keeps unanswered dialogs but clears freeze evidence until fresh reads.
Native Agent-tool subagent events use the same canonical/tripwire request channels.
--ack closes a pending done/ended-no-report alert permanently for that dispatch after
acceptance/recovery. New work uses a new dispatch id. Poll errors are logged and retried;
--once exits 1 on a poll failure, invalid settings/usage exit 2, successful commands exit 0.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager, suppress
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

import lanes
import load_guard
import project
import task_state

LAUNCH_GRACE = 300  # a bounded startup window, including pane placement and registration
# checkout_activity's bound: past either, the checkout's activity is unknown (no freeze).
ACTIVITY_MAX_FILES = 50_000
ACTIVITY_SECONDS = 5.0


def log_error(run: Path, primary: Path, scope: str, exc: Exception) -> None:
    message = f"{scope} failed ({type(exc).__name__}); retrying"
    print(f"watch: {message}", file=sys.stderr, flush=True)
    try:
        event = task_state.event_record("watch.error", json.dumps({"scope": scope,
                                        "error": type(exc).__name__}), None)
        task_state.append_event(task_state.evidence_path(run / "events.jsonl", primary), event)
    except Exception:  # logging failure must not terminate the observer either
        pass


@contextmanager
def watch_state(run: Path, primary: Path):
    folder = task_state.evidence_path(run / "lanes", primary)
    folder.mkdir(exist_ok=True)
    path = task_state.evidence_path(folder / "_watch.json", primary)
    lock = task_state.evidence_path(folder / "_watch.lock", primary)
    with lock.open("a") as stream:
        lanes.locked(stream, True)
        memory = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(memory, dict):
            raise ValueError("watch state must be an object")
        yield memory
        temp = task_state.evidence_path(folder / "_watch.tmp", primary)
        temp.write_text(json.dumps(memory, indent=1) + "\n", encoding="utf-8")
        temp.replace(path)


def acknowledge(run: Path, primary: Path, dispatches: list[str]) -> None:
    """Explicit terminal-lane acceptance/recovery; never hides live input or freeze alerts."""
    changed = False
    with watch_state(run, primary) as memory:
        pending = memory.get("open", {})
        acknowledged = memory.setdefault("acknowledged", {})
        dispatches = list(dict.fromkeys(dispatches))
        for dispatch in dispatches:
            task_state.identifier(dispatch)
            if dispatch not in acknowledged and not any(
                    c.get("dispatch") == dispatch and c.get("condition") in ("done", "ended-no-report")
                    for c in pending.values()):
                raise ValueError(f"{dispatch}: only a pending terminal done/ended-no-report alert can be acknowledged")
        for dispatch in dispatches:
            if dispatch in acknowledged:
                continue
            condition = next(c["condition"] for c in pending.values() if c.get("dispatch") == dispatch
                             and c.get("condition") in ("done", "ended-no-report"))
            acknowledged[dispatch] = {"at": time.time(), "condition": condition}
            pending = {k: c for k, c in pending.items() if not (
                c.get("dispatch") == dispatch and c.get("condition") in ("done", "ended-no-report"))}
            event = task_state.event_record("watch.ack", json.dumps({"dispatch": dispatch,
                                           "condition": condition}), f"watch.ack.{dispatch}")
            task_state.append_event(task_state.evidence_path(run / "events.jsonl", primary), event)
            changed = True
        memory["open"] = pending
    if changed:
        lanes.regenerate_dashboard(run, primary)

def safe_text(value: str) -> str:
    # Share the dashboard's existing secret-value redaction; never persist screen text.
    import dashboard
    return dashboard.redact(value)


def positive(value, where: str, *, allow_zero=False) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero)):
        raise ValueError(f"{where} must be a finite number {'>= 0' if allow_zero else '> 0'}")
    return value


def settings(root: Path) -> dict:
    section = project.load(root, "project.json", {}).get("watch", {})
    if not isinstance(section, dict):
        raise ValueError("project.json: watch must be an object")
    unknown = section.keys() - {"checks", "frozen_minutes", "stop_command"}
    if unknown:
        raise ValueError(f"project.json: unknown watch keys: {', '.join(sorted(unknown))}")
    result = {"checks": [], "frozen_minutes": 15, **section}
    positive(result["frozen_minutes"], "watch.frozen_minutes")
    if "stop_command" in result and (not isinstance(result["stop_command"], str)
                                     or not result["stop_command"].strip()):
        raise ValueError("watch.stop_command must be a nonempty string")
    if not isinstance(result["checks"], list):
        raise ValueError("watch.checks must be a list")
    names = set()
    checks = []
    for check in result["checks"]:
        if not isinstance(check, dict) or check.keys() - {"name", "command", "every"}:
            raise ValueError("watch.checks entries accept only name, command, every")
        name, command = check.get("name"), check.get("command")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            raise ValueError("watch.checks.name must be a single word (letters, digits, _, ., -)")
        if name in names:
            raise ValueError(f"watch.checks: duplicate name {name}")
        if not isinstance(command, str) or not command.strip():
            raise ValueError(f"watch.checks.{name}.command must be a nonempty string")
        every = positive(check.get("every", 120), f"watch.checks.{name}.every")
        names.add(name)
        checks.append({"name": name, "command": command, "every": every})
    result["checks"] = checks
    return result


# Describe the condition rather than copying the screen (which can contain secrets).
# Codex's startup review of new or changed hooks (after every harness update the guard's hash
# changes); `t` is its "Trust all and continue". Only the user presses it.
HOOK_TRUST = "hook-trust"
HOOK_TRUST_ACTION = "the user presses t (Trust all and continue) in {pane}"
PROMPTS = [
    (r"\bhooks? needs? review\b|\bhooks? (?:is|are) new or changed\b|trust all and continue",
     HOOK_TRUST),
    (r"\b[1-9]\d*\s+questions?\b|⇧\s*←\s*to answer|\benter\s+submit\b", "question dialog"),
    (r"trust this (?:folder|workspace|repository)|\bt trust all\b|hook.{0,100}(?:needs review|review before)|needs review before it can run", "trust/hook review"),
    (r"do you want to proceed|allow (?:command|once|this)|permission (?:required|prompt)|approve.{0,30}(?:command|permission)|\(y/n\)", "permission prompt"),
]


def needs_input(screen: str) -> str | None:
    screen = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", screen)
    return next((label for pattern, label in PROMPTS
                 if re.search(pattern, screen, re.I)), None)


def working_indicator(screen: str) -> bool:
    """An agent status line, never prose such as 'working tree' or 'Working directory'."""
    return bool(re.search(r"(?im)^\s*(?:[•●✻✽✶✢✳⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏]\s*)?"
                          r"Working(?:\s*\([^\n]*\)|…|\.\.\.)?\s*$", screen))


# A working-indicator line, then its parenthesis where the elapsed timer ticks: "Working",
# bare or after any spinner glyph ("• Working (14m 3s • esc to interrupt)", Codex), or any
# other capitalized status verb only after a Claude or braille spinner glyph ("✻ Cogitating…
# (59s · …)"). Ordinary output such as "Result (14m 3s)" or a "•"/"*" bullet is not one.
WORKING_GLYPH = "[•◦●○·✢✳✶✻✽✦✧\u2800-\u28ff]"
VERB_GLYPH = "[·✢✳✶✻✽✦✧\u2800-\u28ff]"
STATUS_LINE = re.compile(rf"^(\s*)(?:{WORKING_GLYPH}?(\s*Working(?:…|\.\.\.)?\s*\()"
                         rf"|{VERB_GLYPH}(\s*[A-Z][\w-]*(?:…|\.\.\.)?\s*\())(.*)$")
ELAPSED = re.compile(r"\b(?:\d+h\s*)?(?:\d+m\s*)?\d+[hms]\b")


def screen_digest(screen: str) -> str:
    """The screen's hash for freeze detection, with each working-indicator line's spinner
    glyph and elapsed timer normalized: they tick while a lane is stuck in one tool call.
    Everything else counts, a token counter in the same parenthesis included."""
    lines = []
    for line in screen.splitlines():
        match = STATUS_LINE.match(line)
        if match:
            head = match[2] or match[3]
            line = f"{match[1]}<spinner>{head}{ELAPSED.sub('<elapsed>', match[4])}"
        lines.append(line)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()


def checkout_activity(state: dict) -> str | None:
    """activity's fingerprint alone."""
    return activity(state)[0]


def activity(state: dict) -> tuple[str | None, str | None]:
    """A fingerprint of what the lane changes, and why it is unknown when it is None.

    The fingerprint covers HEAD and every file of the checkout that git tracks or would track
    (`ls-files --cached --others --exclude-standard`: path, mtime, size, so a file rewritten
    with the same content counts), never .git or the run state under .harness/ (the watcher
    writes there), plus the report and its progress notes. Ignored files (build output,
    node_modules, .venv) are not scanned. None, which proves no freeze, for a state without a
    checkout, a checkout git cannot list, or one past ACTIVITY_MAX_FILES files or
    ACTIVITY_SECONDS of scanning."""
    stamps = [lanes.report_stamp(Path(name)) for name in (
        [state["report"], lanes.progress_path(state["report"])] if state.get("report") else [])]
    folder = state.get("workdir") or state.get("checkout")
    if not folder:
        return None, "no checkout recorded"
    began = time.monotonic()
    slow = f"scanning took over {ACTIVITY_SECONDS:g} s"
    try:
        head = subprocess.run(["git", "rev-parse", "-q", "--verify", "HEAD"], cwd=folder,
                              capture_output=True, text=True, timeout=ACTIVITY_SECONDS).stdout.strip()
        remaining = ACTIVITY_SECONDS - (time.monotonic() - began)
        if remaining <= 0:
            return None, slow
        done = subprocess.run(["git", "ls-files", "-z", "--cached", "--others",
                               "--exclude-standard", f"--exclude={lanes.RUN_STATE}"], cwd=folder,
                              capture_output=True, text=True, timeout=remaining)
    except subprocess.TimeoutExpired:
        return None, slow
    except OSError:
        return None, "git cannot list the checkout"
    if done.returncode:
        return None, "git cannot list the checkout"
    names = sorted(set(done.stdout.split("\0")) - {""})
    if len(names) > ACTIVITY_MAX_FILES:
        return None, f"more than {ACTIVITY_MAX_FILES} files to scan"
    files = []
    for name in names:
        if Path(name).parts[:1] in ((lanes.RUN_STATE,), (".git",)):
            continue
        if time.monotonic() - began > ACTIVITY_SECONDS:
            return None, slow
        files.append([name, lanes.report_stamp(Path(folder) / name)])
    return hashlib.sha256(json.dumps([head, files, stamps]).encode()).hexdigest(), None


def herdr_call(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["herdr", *args], text=True, capture_output=True, timeout=10)


def live_agents() -> list[dict] | None:
    if os.environ.get("HERDR_ENV") != "1" or not shutil.which("herdr"):
        return None
    try:
        done = herdr_call("agent", "list")
        if done.returncode:
            raise ValueError("herdr agent list failed")
        agents = json.loads(done.stdout)["result"]["agents"]
        if not isinstance(agents, list):
            raise ValueError("herdr agent list is not a list")
        return agents
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        print("watch: herdr unavailable; using dispatch state", file=sys.stderr)
        return None


def lane_conditions(run: Path, memory: dict, config: dict, now: float,
                    agents: list[dict] | None, primary: Path, waiting=()) -> dict:
    events, _ = task_state.read_events(run)
    ends = {e.get("dispatch") for e in events if e.get("type") == "dispatch.end"}
    conditions = {}
    screens = memory.setdefault("screens", {})
    present = set()
    for path in lanes.dispatch_states(run):
        try:
            state = lanes.load_state(task_state.evidence_path(path, primary))
            if not isinstance(state, dict) or not isinstance(state.get("dispatch"), str):
                raise ValueError("invalid dispatch state")
        except (ValueError, OSError, task_state.StateError) as exc:
            log_error(run, primary, f"lane {path.stem}", exc)
            present.add(path.stem)
            screens.pop(path.stem, None)
            conditions.update({k: dict(c) for k, c in memory.get("open", {}).items()
                               if c.get("dispatch") == path.stem and c.get("condition") != "frozen"})
            continue
        dispatch = state["dispatch"]
        present.add(dispatch)
        work = safe_text(" ".join(str(state.get("work") or dispatch).split()))

        def add(kind, detail=""):
            key = f"lane:{dispatch}:{kind}"
            if kind == "needs-input":
                key += ":flag" if detail == "flag" else f":{detail}"
            conditions[key] = {
                "line": f"LANE {work} {kind}" + (f" {detail}" if detail else ""),
                "dispatch": dispatch, "work": work, "condition": kind}

        acknowledged = dispatch in memory.get("acknowledged", {})
        fresh = bool(state.get("report")) and lanes.report_fresh(state)
        if not acknowledged and lanes.flag_raised(state, run):
            add("needs-input", "flag")  # the lane's sanctioned question; its text is not read

        def finished():
            if not acknowledged:
                add("done" if fresh else "ended-no-report")
            screens.pop(dispatch, None)

        if dispatch in ends or state.get("state") in lanes.ENDED:
            finished()
            continue
        status = state.get("agent_status") or state.get("state")
        screen = None
        if state.get("launcher") == "pane" and agents is not None:
            agent = next((a for a in agents if (state.get("pane_name")
                         and a.get("name") == state["pane_name"])
                         or (not state.get("pane_name") and a.get("pane_id") == state.get("pane_id"))), None)
            if not agent or not lanes.same_agent(state, agent) or not agent.get("agent"):
                started = lanes.epoch(state.get("started"))
                launching = state.get("launch_pending") is True or (
                    "launch_pending" not in state and not state.get("pane_id"))
                if launching:
                    if state.get("launch_pid"):
                        if lanes.alive(state["launch_pid"]):
                            continue
                    elif started is not None and now < started + LAUNCH_GRACE:
                        continue  # compatibility for states written before launch_pid existed
                add("exited")  # a report alone is no end: lanes status ends it
                screens.pop(dispatch, None)
                continue
            status = agent.get("agent_status")
            try:
                done = herdr_call("agent", "read", state.get("pane_name") or state["pane_id"],
                                  "--source", "visible", "--lines", "60")
                if done.returncode == 0:
                    screen = done.stdout
            except (OSError, subprocess.TimeoutExpired):
                pass  # no observation is not proof of a frozen screen
        elif status == "gone" or (state.get("launcher") == "headless"
                                   and state.get("pid") and not lanes.alive(state["pid"])):
            add("exited")
            continue
        if fresh and state.get("launcher") == "pane" and status in ("idle", "done"):
            finished()  # the report is final only once the agent ended its turn
            continue
        working = status == "working" or (status != "blocked" and working_indicator(screen or ""))
        prompt = needs_input(screen or "") if not working else None
        if prompt or status == "blocked":
            prior_input = next(((k, c) for k, c in memory.get("open", {}).items()
                                if c.get("dispatch") == dispatch and not k.endswith(":flag")
                                and c.get("condition") == "needs-input"), None)
            if screen is None and prior_input:
                conditions[prior_input[0]] = dict(prior_input[1])
            elif prompt == HOOK_TRUST:
                pane = state.get("pane_name") or state.get("pane_id")
                add("needs-input", f"{HOOK_TRUST}: {HOOK_TRUST_ACTION.format(pane=pane)}")
            else:
                add("needs-input", prompt or "agent_status: blocked")
        if any((state.get('pane_id') and job.get('pane_id') == state['pane_id'])
               or any(pid and load_guard.descendant(job['pid'], pid)
                      for pid in (state.get('pid'), state.get('child_pid')))
               for job in waiting):
            # A live bounded capacity wait is known activity. Drop the old freeze
            # clock; when the wait clears, start a fresh clock from the next read.
            screens.pop(dispatch, None)
            continue
        if screen is not None:
            # Frozen: a working agent whose screen (screen_digest) and checkout (activity)
            # have not changed for frozen_minutes. Activity that cannot be read proves
            # nothing; it is said once per dispatch, so the missing detection is visible.
            digest = screen_digest(screen)
            files, why = activity(state) if working else (None, None)
            noted = memory.setdefault("activity_unknown", [])
            if working and files is None and dispatch not in noted:
                add("activity-unknown", f"({why}; frozen cannot be detected)")
                noted.append(dispatch)
            previous = screens.get(dispatch, {})
            if (not working or files is None
                    or [previous.get("hash"), previous.get("files")] != [digest, files]):
                previous = {"hash": digest, "files": files, "since": now}
            screens[dispatch] = previous
            minutes = max(0, now - previous["since"]) / 60
            if working and files is not None and minutes >= config["frozen_minutes"]:
                add("frozen", str(int(minutes)))
        else:
            # Unknown screen evidence cannot prove a freeze. Preserve unanswered
            # dialogs until resolved, but restart the freeze clock after fresh reads.
            screens.pop(dispatch, None)
            keep = "needs-input" if status not in ("working", "running") else None
            for key, condition in memory.get("open", {}).items():
                if (condition.get("dispatch") == dispatch and condition.get("condition") == keep
                        and not key.endswith(":flag")
                        and not any(c.get("dispatch") == dispatch and not k.endswith(":flag")
                                    for k, c in conditions.items())):
                    conditions[key] = dict(condition)
    # Native Agent-tool dispatches have events rather than lanes/*.json states.
    # Their file request channel must be visible on both runtimes too.
    for event in events:
        dispatch = event.get("dispatch")
        if (event.get("type") != "dispatch.start" or event.get("launcher") != "subagent"
                or not isinstance(dispatch, str) or dispatch in present
                or dispatch in memory.get("acknowledged", {})):
            continue
        if lanes.flag_raised(event, run):
            work = safe_text(" ".join(str(event.get("work") or dispatch).split()))
            conditions[f"lane:{dispatch}:needs-input:flag"] = {
                "line": f"LANE {work} needs-input flag", "dispatch": dispatch,
                "work": work, "condition": "needs-input"}
    memory["screens"] = {d: s for d, s in screens.items() if d in present}
    memory["activity_unknown"] = [d for d in memory.get("activity_unknown", []) if d in present]
    return conditions


def check_command(command: str, root: Path) -> tuple[str, int]:
    child = subprocess.Popen(command, shell=True, cwd=root, stdout=subprocess.PIPE,
                             stdin=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             text=True, start_new_session=True)
    try:
        try:
            output, _ = child.communicate(timeout=60)
        except subprocess.TimeoutExpired:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            try:
                output, _ = child.communicate(timeout=2)
            except subprocess.TimeoutExpired as exc:
                output = exc.output or ""
                if isinstance(output, bytes):
                    output = output.decode("utf-8", errors="replace")
                child.stdout.close()  # detached descendants cannot hold this poll's pipe open
                with suppress(subprocess.TimeoutExpired):
                    child.wait(timeout=2)
            return output, 1
        return output, child.returncode
    finally:
        child.stdout.close()
        if child.poll() is None:
            with suppress(OSError):
                os.killpg(child.pid, signal.SIGKILL)
            with suppress(subprocess.TimeoutExpired):
                child.wait(timeout=2)


def anomaly(line: str) -> dict:
    """Optional JSON {key, message} separates stable identity from changing counters."""
    try:
        value = json.loads(line)
    except ValueError:
        value = None
    if (isinstance(value, dict) and isinstance(value.get("key"), str) and value["key"].strip()
            and isinstance(value.get("message"), str) and value["message"].strip()):
        identity, message = safe_text(value["key"]), safe_text(value["message"])
    else:
        identity = message = safe_text(line)
    return {"key": hashlib.sha256(identity.encode()).hexdigest(), "message": " ".join(message.split())}


def product_conditions(root: Path, memory: dict, config: dict, now: float) -> dict:
    stored = memory.setdefault("checks", {})
    conditions = {}
    names = set()
    for check in config["checks"]:
        name = check["name"]
        names.add(name)
        prior = stored.get(name, {})
        command_hash = hashlib.sha256(check["command"].encode()).hexdigest()
        if (prior.get("command_hash") != command_hash or "at" not in prior
                or now < prior["at"] or now - prior["at"] >= check["every"]):
            output, code = check_command(check["command"], root)
            lines = {a["key"]: a for a in (anomaly(line.strip()) for line in output.splitlines() if line.strip())}
            lines = list(lines.values())
            prior = {"at": now, "command_hash": command_hash, "lines": lines, "failed": code != 0}
            stored[name] = prior
        for line in prior["lines"]:
            entry = line if isinstance(line, dict) else anomaly(line)  # pre-keyed-state compatibility
            key = entry["key"]
            conditions[f"check:{name}:{key}"] = {
                "line": f"CHECK {name} {entry['message']}", "check": name, "condition": "anomaly"}
        if prior["failed"]:
            conditions[f"check:{name}:failed"] = {
                "line": f"CHECK {name} failed", "check": name, "condition": "failed"}
    memory["checks"] = {n: s for n, s in stored.items() if n in names}
    return conditions


def poll(run: Path, primary: Path, root: Path, config: dict, quiet_repeat=600,
         *, now: float | None = None) -> list[str]:
    now = time.time() if now is None else now
    with watch_state(run, primary) as memory:
        waiting = load_guard.waiting_jobs()
        current = lane_conditions(run, memory, config, now, live_agents(), primary, waiting)
        for job in waiting:
            current[f"load:{job['id']}"] = {
                'line': f"LOAD {job['label']} waiting (pid {job['pid']})",
                'condition': 'waiting', 'job': job['id']}
        current.update(product_conditions(root, memory, config, now))
        previous = memory.get("open", {})
        emitted = []
        for key, condition in current.items():
            last = previous.get(key, {}).get("emitted_at")
            due = last is None or now < last or now - last >= quiet_repeat
            condition["emitted_at"] = now if due else last
            if due:
                event = task_state.event_record("watch", json.dumps({
                    **condition, "key": key, "repeat": last is not None}), None)
                task_state.append_event(task_state.evidence_path(run / "events.jsonl", primary), event)
                emitted.append(condition["line"])
        changed = current.keys() != previous.keys() or bool(emitted)
        memory.update(open=current, observed_at=now)
    if changed:
        lanes.regenerate_dashboard(run, primary)
    return emitted


def summary(run: Path) -> str:
    path = run / "lanes" / "_watch.json"
    if not path.is_file():
        return "Watch: not armed"
    try:
        conditions = json.loads(path.read_text())["open"]
        waiting = sum(c.get('condition') == 'waiting' for c in conditions.values())
        count = len(conditions) - waiting
    except (OSError, ValueError, KeyError, TypeError):
        return "Watch: state unreadable"
    status = f"Watch: 🚨 {count} open" if count else "Watch: ✅ quiet"
    return status + (f"; {waiting} waiting" if waiting else '')


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--every", type=float, default=30)
    parser.add_argument("--quiet-repeat", type=float, default=600)
    parser.add_argument("--ack", action="append", metavar="DISPATCH",
                        help="acknowledge terminal lane acceptance/recovery; repeatable; exits without polling")
    args = parser.parse_args(argv)
    try:
        positive(args.every, "--every")
        positive(args.quiet_repeat, "--quiet-repeat", allow_zero=True)
        primary, root = task_state.checkouts()
        run = task_state.locate(primary, args.task)
        if not run.is_dir():
            raise ValueError(f"Run does not exist: {run}; initialize it first")
        config = settings(root)
        if args.ack:
            acknowledge(run, primary, args.ack)
            return 0
        while True:
            try:
                output = poll(run, primary, root, config, args.quiet_repeat)
            except Exception as exc:
                log_error(run, primary, "poll", exc)
                if args.once:
                    return 1
            else:
                for line in output:
                    print(line, flush=True)
                if args.once:
                    return 0
            time.sleep(args.every)
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, KeyError, TypeError, project.OverlayError,
            task_state.StateError, lanes.LaneError) as exc:
        print(f"watch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
