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
import project
import task_state

LAUNCH_GRACE = 300  # a bounded startup window, including pane placement and registration


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
PROMPTS = [
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
                    agents: list[dict] | None, primary: Path) -> dict:
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
                               if c.get("dispatch") == path.stem})
            continue
        dispatch = state["dispatch"]
        present.add(dispatch)
        work = safe_text(" ".join(str(state.get("work") or dispatch).split()))

        def add(kind, detail=""):
            key = f"lane:{dispatch}:{kind}"
            if kind == "needs-input":
                key += f":{detail}"
            conditions[key] = {
                "line": f"LANE {work} {kind}" + (f" {detail}" if detail else ""),
                "dispatch": dispatch, "work": work, "condition": kind}

        if state.get("report") and lanes.report_fresh(state):
            if dispatch not in memory.get("acknowledged", {}):
                add("done")
            screens.pop(dispatch, None)
            continue
        if dispatch in ends or state.get("state") in ("done", "failed", "timeout", "cancelled"):
            if dispatch not in memory.get("acknowledged", {}):
                add("ended-no-report")
            screens.pop(dispatch, None)
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
                add("exited")
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
        working = status == "working" or (status != "blocked" and working_indicator(screen or ""))
        prompt = needs_input(screen or "") if not working else None
        if prompt or status == "blocked":
            prior_input = next(((k, c) for k, c in memory.get("open", {}).items()
                                if c.get("dispatch") == dispatch
                                and c.get("condition") == "needs-input"), None)
            if screen is None and prior_input:
                conditions[prior_input[0]] = dict(prior_input[1])
            else:
                add("needs-input", prompt or "agent_status: blocked")
        if screen is not None:
            digest = hashlib.sha256(screen.encode()).hexdigest()
            previous = screens.get(dispatch, {})
            if previous.get("hash") != digest or working:
                previous = {"hash": digest, "since": now}
            screens[dispatch] = previous
            minutes = max(0, now - previous["since"]) / 60
            if not working and not prompt and status != "blocked" and minutes >= config["frozen_minutes"]:
                add("frozen", str(int(minutes)))
        else:
            # A failed read or unavailable Herdr is not evidence of recovery. Preserve
            # screen-derived alerts, but require a fresh observation for new freezes.
            screens.pop(dispatch, None)
            if status not in ("working", "running"):
                for key, condition in memory.get("open", {}).items():
                    if (condition.get("dispatch") == dispatch
                            and condition.get("condition") in ("needs-input", "frozen")
                            and not any(c.get("dispatch") == dispatch for c in conditions.values())):
                        conditions[key] = dict(condition)
    memory["screens"] = {d: s for d, s in screens.items() if d in present}
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
        current = lane_conditions(run, memory, config, now, live_agents(), primary)
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
        count = len(json.loads(path.read_text())["open"])
    except (OSError, ValueError, KeyError, TypeError):
        return "Watch: state unreadable"
    return f"Watch: 🚨 {count} open" if count else "Watch: ✅ quiet"


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
