#!/usr/bin/env python3
"""The run's live dashboard as one herdr tab: views on the left, the run's lane panes on the right.

  lanes.py dashboard --task T [--open | --close]
  dashboard_herdr.py view status|events --task T [--every SEC] [--once]

`lanes.py dashboard --open` opens one tab per run, labelled "📊 <task>", and records it in
`<run>/lanes/_herdr.json` under "dashboard" (tab_id and every pane it created, with its zone,
kind and label); a second --open while the tab exists only prints it. Geometry: the root pane
is split right at LEFT_SHARE (0.58), so the left column is that wide. The left column holds,
top to bottom and of equal height, `📊 status` (`view status`, every 30 s: units closed of
units known from units/ with the run.md spec, running/blocked/stale/idle lanes with herdr's
live agent status for panes, pending gates, open tripwires, each vendor's share of the run's
dispatches and the latest pick per role), the overlay's project panes of zone "status",
`🧾 events` (`view events`, every 10 s: a formatted tail of events.jsonl — dispatch start/end,
gate open/answer, balance picks, the rest as type and fields) and the project panes of zone
"events". The right column holds the project panes of zone "side", then the lane panes: a free
lane pane (`🤖 lanes · free`, an idle shell) when the run has no live pane lane, else the run's
live lane panes moved there from beside the caller or the task's tab (`herdr pane move`; the
free pane is then closed). Every pane is renamed to its label; views and project commands run
through `herdr pane run` in the current checkout.

Project panes come from the overlay: `project.json → dashboard` {"panes": [{"label": str,
"command": str, "zone": "status" | "events" | "side" (default "side")}]}. Any other key, a
missing or empty label or command, or another zone is exit 2.

lane_pane(state, ...) is lanes.py start's placement while the run has a dashboard (the caller
holds the herdr lock): a lane pane of the run whose dispatch ended (or the free one) and whose
shell is idle again (`pane process-info`: the shell is the foreground process group) is reused
— the worker's directory and environment are typed into that shell, and variables an earlier
lane exported there are unset — else the right column's last pane is split down. Then the
column's heights are balanced (`pane resize`, best effort). Panes the run did not create are
never closed, reused or moved.

`--close` closes only the panes the record lists, except a lane pane whose dispatch has not
ended (it stays, still the run's); the tab goes away with its last pane, so a pane someone else
added keeps it open. The record's "dashboard" entry is then removed. Without herdr (HERDR_ENV
is not 1) every action says so and exits 0; dashboard.py's HTML page stays the fallback.
Exit codes: 0 done, 1 a herdr or run failure, 2 bad arguments or an unusable overlay.
"""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sys
import time

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
import dashboard
import lanes
from lanes import LaneError, herdr
import project
import task_state
from task_state import StateError

LEFT_SHARE = 0.58
ZONES = ("status", "events", "side")
PANE_KEYS = ("label", "command", "zone")
STATUS_LABEL, EVENTS_LABEL, FREE_LABEL = "📊 status", "🧾 events", "🤖 lanes · free"
STATUS_EVERY, EVENTS_EVERY = 30.0, 10.0
BALANCE_STEP = 0.02  # a column split this close to its target is left alone
NO_HERDR = ("herdr is not available (HERDR_ENV is not 1): no dashboard tab; the run's page is "
            "`dashboard.py {task}`")
SCRIPT = Path(__file__).resolve()


def herdr_on() -> bool:
    return os.environ.get("HERDR_ENV") == "1"


# --- the overlay -------------------------------------------------------------------------

def project_panes(root: Path) -> list[dict]:
    """The overlay's `project.json → dashboard.panes`, each with its zone; ValueError (exit 2)
    on anything else."""
    try:
        section = project.load(root, "project.json", {}).get("dashboard", {})
    except project.OverlayError as exc:
        raise ValueError(f"cannot use {exc}") from None
    if not isinstance(section, dict):
        raise ValueError("project.json: dashboard must be an object")
    unknown = sorted(set(section) - {"panes"})
    if unknown:
        raise ValueError(f"project.json: unknown dashboard keys: {', '.join(unknown)}; "
                         "expected panes")
    panes = section.get("panes", [])
    if not isinstance(panes, list):
        raise ValueError("project.json: dashboard.panes must be a list")
    found = []
    for number, pane in enumerate(panes):
        where = f"project.json: dashboard.panes[{number}]"
        if not isinstance(pane, dict):
            raise ValueError(f"{where} must be an object")
        unknown = sorted(set(pane) - set(PANE_KEYS))
        if unknown:
            raise ValueError(f"{where}: unknown keys: {', '.join(unknown)}; expected "
                             f"{', '.join(PANE_KEYS)}")
        for key in ("label", "command"):
            if not isinstance(pane.get(key), str) or not pane[key].strip():
                raise ValueError(f"{where}.{key} must be a non-empty string")
        zone = pane.get("zone", "side")
        if zone not in ZONES:
            raise ValueError(f"{where}.zone must be one of {', '.join(ZONES)}, got {zone!r}")
        found.append({"label": pane["label"], "command": pane["command"], "zone": zone})
    return found


# --- the record --------------------------------------------------------------------------

def record_path(run: Path, primary: Path) -> Path:
    return task_state.evidence_path(lanes.lanes_dir(run, primary) / "_herdr.json", primary)


def load_record(path: Path) -> dict:
    try:
        known = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except ValueError:
        known = {}
    return known if isinstance(known, dict) else {}


def save_record(path: Path, known: dict) -> None:
    path.write_text(json.dumps(known), encoding="utf-8")


def tab_panes(tab: str) -> dict[str, dict]:
    """The panes herdr lists in tab, by id; empty when the tab is gone."""
    panes = herdr("pane", "list").get("panes") or []
    return {p["pane_id"]: p for p in panes if p.get("tab_id") == tab and p.get("pane_id")}


def ended(run: Path, dispatch: str | None) -> bool:
    """The dispatch has its end (or no state the run could still be waiting on)."""
    if not dispatch:
        return True
    try:
        state = lanes.load_state(run / "lanes" / f"{dispatch}.json")
    except (OSError, ValueError):
        return True
    return lanes.end_of(state) is not None


def shell_idle(pane: str) -> bool:
    """The pane's shell is back at its prompt: the shell is the foreground process group."""
    try:
        info = herdr("pane", "process-info", "--pane", pane).get("process_info") or {}
    except LaneError:
        return False
    shell = info.get("shell_pid")
    return bool(shell) and info.get("foreground_process_group_id") == shell


# --- geometry ----------------------------------------------------------------------------

def column(first: str, count: int, cwd: Path) -> list[str]:
    """first and count - 1 panes split down below it, each of equal height."""
    ids = [first]
    for k in range(1, count):
        ratio = 1 / (count - k + 1)  # the pane split keeps its share of what is left
        made = herdr("pane", "split", ids[-1], "--direction", "down", "--ratio", f"{ratio:.4f}",
                     "--cwd", str(cwd), "--no-focus")
        ids.append(made["pane"]["pane_id"])
    return ids


def balance_moves(heights: list[float]) -> list[tuple[int, str, float]]:
    """Resizes (index, "down" | "up", amount) that give a chain of panes split top to bottom
    equal heights: the split below pane k keeps 1/(n - k) of the height from k down. A split's
    ratio is relative to its own area, so each is set from the heights read before any move."""
    moves, count = [], len(heights)
    for k in range(count - 1):
        rest = sum(heights[k:])
        if rest <= 0:
            break
        delta = 1 / (count - k) - heights[k] / rest
        if abs(delta) >= BALANCE_STEP:
            moves.append((k, "down" if delta > 0 else "up", round(abs(delta), 4)))
    return moves


def balance(ids: list[str]) -> None:
    """Best effort: equal heights for the column ids (a chain split top to bottom)."""
    if len(ids) < 2:
        return
    try:
        layout = herdr("pane", "layout", "--pane", ids[0]).get("layout") or {}
        rects = {p["pane_id"]: p.get("rect") or {} for p in layout.get("panes") or []}
        heights = [float(rects[i]["height"]) for i in ids]
        for index, direction, amount in balance_moves(heights):
            herdr("pane", "resize", "--pane", ids[index], "--direction", direction,
                  "--amount", f"{amount:.4f}")
    except (LaneError, KeyError, TypeError, ValueError):
        pass


def right_column(board: dict, alive: dict[str, dict]) -> list[dict]:
    return [p for p in board["panes"] if p["zone"] in ("side", "lanes")
            and p["pane_id"] in alive]


def view_command(kind: str, task: str) -> str:
    return shlex.join([sys.executable, str(SCRIPT), "view", kind, "--task", task])


# --- open, close, show -------------------------------------------------------------------

def open_dashboard(task: str) -> dict:
    primary, run = lanes.run_directory(task)
    root = project.repo_root(Path.cwd())
    extra = project_panes(root)
    with lanes.herdr_lock():
        path = record_path(run, primary)
        known = load_record(path)
        board = known.get("dashboard")
        if board and tab_panes(board["tab_id"]):
            return {**summary(board), "opened": False}
        label = f"📊 {task}"
        made = herdr("tab", "create", "--cwd", str(root), "--label", label, "--no-focus")
        tab, status = made["tab"]["tab_id"], made["root_pane"]["pane_id"]
        board = {"tab_id": tab, "label": label, "panes": []}
        known["dashboard"] = board
        save_record(path, known)  # recorded at once: --close finds what a failure left
        right = herdr("pane", "split", status, "--direction", "right", "--ratio",
                      f"{LEFT_SHARE:.2f}", "--cwd", str(root), "--no-focus")["pane"]["pane_id"]
        left_specs = ([{"kind": "status", "zone": "status", "label": STATUS_LABEL,
                        "command": view_command("status", task)}]
                      + [{"kind": "project", **p} for p in extra if p["zone"] == "status"]
                      + [{"kind": "events", "zone": "events", "label": EVENTS_LABEL,
                          "command": view_command("events", task)}]
                      + [{"kind": "project", **p} for p in extra if p["zone"] == "events"])
        right_specs = ([{"kind": "project", **p} for p in extra if p["zone"] == "side"]
                       + [{"kind": "lane", "zone": "lanes", "label": FREE_LABEL,
                           "command": None, "dispatch": None, "env": []}])
        board["panes"] = [{**spec, "pane_id": pane} for spec, pane in
                          zip(left_specs, column(status, len(left_specs), root))]
        board["panes"] += [{**spec, "pane_id": pane} for spec, pane in
                           zip(right_specs, column(right, len(right_specs), root))]
        save_record(path, known)
        for pane in board["panes"]:
            herdr("pane", "rename", pane["pane_id"], pane["label"])
            if pane.get("command"):
                herdr("pane", "run", pane["pane_id"], pane["command"])
        adopt_live_lanes(run, known, board)
        save_record(path, known)
        balance([p["pane_id"] for p in right_column(board, tab_panes(tab))])
    return {**summary(board), "opened": True}


def adopt_live_lanes(run: Path, known: dict, board: dict) -> None:
    """Move the run's live pane lanes of this repository into the right column (they are the
    run's own); the free lane pane is closed once one moved in. A failed move leaves the lane
    where it was."""
    moved = 0
    for state in lanes.live_panes(run):
        if state.get("other_repo"):
            continue
        last = board["panes"][-1]["pane_id"]
        try:
            result = herdr("pane", "move", state["pane_id"], "--tab", board["tab_id"], "--split",
                           "down", "--target-pane", last, "--no-focus").get("move_result") or {}
        except LaneError:
            continue
        moved += 1
        board["panes"].append({"kind": "lane", "zone": "lanes", "label": lanes.lane_label(state),
                               "command": None, "dispatch": state["dispatch"], "env": [],
                               "pane_id": state["pane_id"]})
        with contextlib.suppress(OSError, ValueError):
            lanes.update_state(run / "lanes" / f"{state['dispatch']}.json",
                               tab_id=board["tab_id"])
        if state["pane_id"] == known.get("last_pane") or (
                result.get("closed_tab_id") and result["closed_tab_id"] == known.get("tab_id")):
            known.pop("tab_id", None)  # the task's tab lost its last pane or is gone
            known.pop("last_pane", None)
    free = next((p for p in board["panes"] if p["kind"] == "lane" and not p.get("dispatch")),
                None)
    if moved and free is not None:
        with contextlib.suppress(LaneError):
            herdr("pane", "close", free["pane_id"])
            board["panes"].remove(free)


def close_dashboard(task: str) -> dict:
    primary, run = lanes.run_directory(task)
    with lanes.herdr_lock():
        path = record_path(run, primary)
        known = load_record(path)
        board = known.get("dashboard")
        if not board:
            return {"closed": [], "kept": [], "tab_closed": False,
                    "reason": "the run has no dashboard tab"}
        alive = tab_panes(board["tab_id"])
        closed, kept = [], []
        for pane in board["panes"]:
            if pane["pane_id"] not in alive:
                continue
            if pane["kind"] == "lane" and not ended(run, pane.get("dispatch")):
                kept.append(pane["pane_id"])  # a live lane stays, still the run's
                continue
            with contextlib.suppress(LaneError):
                herdr("pane", "close", pane["pane_id"])
                closed.append(pane["pane_id"])
        tab_closed = not tab_panes(board["tab_id"])
        known.pop("dashboard")
        save_record(path, known)
    return {"tab_id": board["tab_id"], "closed": closed, "kept": kept, "tab_closed": tab_closed}


def summary(board: dict) -> dict:
    return {"tab_id": board["tab_id"], "label": board["label"],
            "panes": [{k: p.get(k) for k in ("pane_id", "zone", "kind", "label", "dispatch")
                       if k != "dispatch" or p["kind"] == "lane"} for p in board["panes"]]}


def show(task: str) -> dict:
    primary, run = lanes.run_directory(task)
    board = load_record(record_path(run, primary)).get("dashboard")
    if not board:
        return {"open": False}
    return {**summary(board), "open": bool(tab_panes(board["tab_id"]))}


# --- placement for lanes.py start --------------------------------------------------------

def lane_pane(state: dict, run: Path, primary: Path, cwd: Path,
              env: dict) -> tuple[str, str] | None:
    """A pane for a new lane in the dashboard's right column, or None when the run has no
    dashboard tab (the caller then places it as before). The caller holds the herdr lock."""
    if not herdr_on():
        return None
    path = record_path(run, primary)
    known = load_record(path)
    board = known.get("dashboard")
    if not board:
        return None
    alive = tab_panes(board["tab_id"])
    if not alive:  # the tab is gone: forget it
        known.pop("dashboard")
        save_record(path, known)
        return None
    for slot in [p for p in board["panes"] if p["kind"] == "lane"]:
        if slot["pane_id"] not in alive:
            board["panes"].remove(slot)
            continue
        if not ended(run, slot.get("dispatch")) or not shell_idle(slot["pane_id"]):
            continue
        try:
            prepare(slot["pane_id"], cwd, env, slot.get("env") or [])
        except LaneError:
            continue  # this shell cannot be prepared; try the next pane or split a new one
        slot.update(dispatch=state["dispatch"], label=lanes.lane_label(state), env=sorted(env))
        save_record(path, known)
        return slot["pane_id"], board["tab_id"]
    column_now = right_column(board, alive)
    extra = [arg for key, value in env.items() for arg in ("--env", f"{key}={value}")]
    if column_now:
        made = herdr("pane", "split", column_now[-1]["pane_id"], "--direction", "down", "--cwd",
                     str(cwd), "--no-focus", *extra)
    else:  # the right column was closed by hand: start it again beside the left one
        anchor = next(p["pane_id"] for p in board["panes"] if p["pane_id"] in alive) \
            if any(p["pane_id"] in alive for p in board["panes"]) else next(iter(alive))
        made = herdr("pane", "split", anchor, "--direction", "right", "--ratio",
                     f"{LEFT_SHARE:.2f}", "--cwd", str(cwd), "--no-focus", *extra)
    pane = made["pane"]["pane_id"]
    board["panes"].append({"kind": "lane", "zone": "lanes", "label": lanes.lane_label(state),
                           "command": None, "dispatch": state["dispatch"], "env": sorted(env),
                           "pane_id": pane})
    save_record(path, known)
    balance([p["pane_id"] for p in column_now] + [pane])
    return pane, board["tab_id"]


def prepare(pane: str, cwd: Path, env: dict, previous: list[str]) -> None:
    """Type the worker's directory and environment into a reused pane's idle shell; what an
    earlier lane exported there and this one does not set is unset first."""
    stale = [key for key in previous if key not in env and re.fullmatch(r"[A-Za-z_]\w*", key)]
    parts = [f"unset {' '.join(stale)}"] if stale else []
    parts.append(f"cd -- {shlex.quote(str(cwd))}")
    parts += [f"export {key}={shlex.quote(str(value))}" for key, value in sorted(env.items())]
    herdr("pane", "run", pane, " && ".join(parts))


# --- views -------------------------------------------------------------------------------

def clip(line: str, width: int) -> str:
    line = dashboard.redact(line)
    return line if len(line) <= width else line[: max(width - 1, 1)] + "…"


def hhmm(value) -> str:
    moment = dashboard.parse_time(value)
    return moment.astimezone(timezone.utc).strftime("%H:%M:%S") if moment else "--:--:--"


def live_agents() -> dict[str, dict] | None:
    """herdr's agents by name, read only; None when herdr cannot be asked."""
    if not herdr_on():
        return None
    try:
        return {a["name"]: a for a in herdr("agent", "list").get("agents") or []
                if a.get("name")}
    except LaneError:
        return None


def lane_rows(run: Path, events: list[dict], now: datetime,
              agents: dict | None) -> tuple[list[tuple[int, str]], int]:
    """(rank, line) per dispatch without an end, blocked first; and the number ended."""
    starts, ends = dashboard.dispatches(events)
    rows = []
    for key, start in starts.items():
        if key in ends:
            continue
        lane = dashboard.lane_state(run, start.get("dispatch"))
        since = dashboard.parse_time(start.get("at"))
        moments = [m for m in (since, dashboard.parse_time(lane.get("last_activity"))) if m]
        quiet = (now - max(moments)).total_seconds() if moments else None
        state = dashboard.lane_waiting(lane) or "running"
        launcher = start.get("launcher") or lane.get("launcher")
        if launcher == "pane" and agents is not None and not lane.get("prompt_pending"):
            agent = agents.get(lane.get("pane_name") or start.get("pane"))
            status = agent.get("agent_status") if agent else "gone"
            state = {"working": "running", "blocked": dashboard.BLOCKED, "idle": dashboard.IDLE,
                     "done": dashboard.IDLE}.get(status, f"herdr {status}")
        elif launcher == "headless" and state == "running" and (
                quiet is None or quiet > lanes.STALE_MINUTES * 60):
            state = "stale — liveness unknown"
        mark, rank = (("⛔", 0) if state == dashboard.BLOCKED else ("⚠", 1)
                      if state.startswith(("stale", "herdr", "idle")) else ("▶", 2))
        elapsed = dashboard.span((now - since).total_seconds()) if since and now >= since else "?"
        rows.append((rank, f"  {mark} {key}  {start.get('role') or '?'} · "
                           f"{start.get('runtime') or '?'} {start.get('model') or ''}  "
                           f"{launcher or '?'}  {elapsed}  {state}"))
    return sorted(rows, key=lambda row: row[0]), len([k for k in starts if k in ends])


def status_lines(run: Path, primary: Path, now: datetime, agents: dict | None) -> list[str]:
    events, unreadable = task_state.read_events(run)
    lines = [f"📊 {run.name} · {now.astimezone(timezone.utc):%H:%M:%S} UTC · "
             f"every {STATUS_EVERY:g} s"]
    ledger = run / "run.md"
    spec = None
    with contextlib.suppress(OSError):
        found = re.search(r"^- Spec: (.+)$", ledger.read_text(encoding="utf-8"), re.MULTILINE)
        spec = found.group(1).strip() if found else None
    if spec:
        lines.append(f"Spec: {spec}")
    units = dashboard.unit_names(run)
    closed = [u for u in units if (run / "units" / f"{u}-closed").exists()]
    marks = " ".join(f"{u}{' ✓' if u in closed else ''}" for u in units)
    lines.append(f"Units: {len(closed)} of {len(units)} closed" + (f" — {marks}" if units
                                                                    else ""))
    rows, ended_count = lane_rows(run, events, now, agents)
    counts = {"blocked": sum(1 for r, _ in rows if r == 0),
              "attention": sum(1 for r, _ in rows if r == 1),
              "running": sum(1 for r, _ in rows if r == 2)}
    lines.append(f"Lanes: {counts['running']} running · {counts['blocked']} blocked · "
                 f"{counts['attention']} stale/idle · {ended_count} ended"
                 + ("" if agents is not None else " (herdr not asked)"))
    lines += [line for _, line in rows]
    pending, _ = dashboard.gates(run, events, (run / "events.jsonl").is_file())
    lines.append(f"Gates: {len(pending)} pending" + (" — " + ", ".join(
        f"{g['gate']} (since {hhmm(g['since'])})" for g in pending) if pending else ""))
    trips = []
    if ledger.is_file():
        for unit, state in dashboard.tripwire_state(run, primary, units).items():
            if state["trips"]:
                trips.append(f"  🔥 {unit}: {'; '.join(str(t) for t in state['trips'])}")
    import watch
    lines.append(watch.summary(run))
    lines.append(f"Tripwires: {len(trips)} open")
    lines += trips
    lines += balance_lines(events)
    if unreadable:
        lines.append(f"({unreadable} unreadable line(s) in events.jsonl skipped)")
    return lines


def balance_lines(events: list[dict]) -> list[str]:
    try:
        import balance as vendors  # imported here: a broken balance.py must not stop the view
        shares, total = vendors.dispatch_shares(events)
    except Exception as exc:  # noqa: BLE001
        return [f"Balance: unavailable ({exc})"]
    if not total:
        line = "Balance: no dispatches yet"
    else:
        line = "Balance: " + " · ".join(
            f"{v} {shares[v]:.0%} ({round(shares[v] * total)} of {total})"
            for v in vendors.RUNTIMES if shares.get(v) is not None)
    picks = [f"  ⚖ {p['role']} → {p.get('runtime') or '?'}"
             + (f" ({p['reason']})" if p.get("reason") else "")
             for p in dashboard.latest_picks(events)]
    return [line, *picks]


def event_line(event: dict) -> str:
    kind = event.get("type") or "?"
    at = hhmm(event.get("at"))
    if kind == "dispatch.start":
        body = (f"▶ {event.get('dispatch')}  {event.get('role') or '?'} · "
                f"{event.get('runtime') or '?'} {event.get('model') or ''} "
                f"{event.get('effort') or ''} · {event.get('launcher') or '?'}"
                + (f" · {event['pane']}" if event.get("pane") else ""))
    elif kind == "dispatch.end":
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else {}
        took = (f" · {dashboard.span(usage['duration_s'])}"
                if isinstance(usage.get("duration_s"), (int, float)) else "")
        tools = (f" · {usage['tool_calls']} tool calls"
                 if isinstance(usage.get("tool_calls"), int) else "")
        body = f"■ {event.get('dispatch')}  {event.get('status') or '?'}{took}{tools}"
    elif kind == "watch":
        body = f"🚨 {event.get('line') or 'watch condition'}"
    elif kind == "gate.open":
        body = f"🚦 gate {event.get('gate')} open" + (f" · {event['card']}"
                                                      if event.get("card") else "")
    elif kind == "gate.answer":
        body = f"✅ gate {event.get('gate')}: {event.get('answer') or '?'}"
    elif kind == "balance.pick":
        body = (f"⚖ pick {event.get('role') or '?'} → {event.get('runtime') or '?'}"
                + (f" ({event['reason']})" if event.get("reason") else ""))
    else:
        fields = {k: v for k, v in event.items() if k not in task_state.EVENT_ENVELOPE}
        body = f"· {kind} " + " ".join(f"{k}={json.dumps(v, ensure_ascii=False)}"
                                       for k, v in fields.items())
    return f"{at} {body}"


def events_lines(run: Path, rows: int) -> list[str]:
    events, unreadable = task_state.read_events(run)
    head = f"🧾 {run.name} · events.jsonl · {len(events)} events · every {EVENTS_EVERY:g} s"
    if not events:
        return [head, "no events yet"]
    tail = [event_line(e) for e in events[-max(rows - 1, 1):]]
    return [head, *tail] + ([f"({unreadable} unreadable line(s) skipped)"] if unreadable else [])


def view(kind: str, task: str, every: float | None, once: bool) -> int:
    primary, run = lanes.run_directory(task)
    every = every or (STATUS_EVERY if kind == "status" else EVENTS_EVERY)
    while True:
        size = shutil.get_terminal_size((120, 40))
        try:
            if kind == "status":
                lines = status_lines(run, primary, datetime.now(timezone.utc), live_agents())
            else:
                lines = events_lines(run, size.lines - 1)
        except Exception as exc:  # noqa: BLE001 - the view shows a failure and keeps going
            lines = [f"{kind}: unavailable ({type(exc).__name__}: {exc})"]
        text = "\n".join(clip(line, size.columns) for line in lines)
        if once:
            print(text)
            return 0
        sys.stdout.write("\x1b[H\x1b[2J" + text + "\n")
        sys.stdout.flush()
        time.sleep(every)


# --- CLI ---------------------------------------------------------------------------------

def command(task: str, action: str) -> int:
    """`lanes.py dashboard`: action is open, close or show."""
    if not herdr_on():
        print(NO_HERDR.format(task=task))
        return 0
    try:
        task_state.identifier(task)
        result = {"open": open_dashboard, "close": close_dashboard, "show": show}[action](task)
    except (ValueError, project.OverlayError) as exc:
        print(f"lanes: {exc}", file=sys.stderr)
        return 2
    except (LaneError, StateError, OSError, KeyError) as exc:
        print(f"lanes: dashboard: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("view", help="a live view of the run (a dashboard pane runs it)")
    sub.add_argument("kind", choices=("status", "events"))
    sub.add_argument("--task", required=True)
    sub.add_argument("--every", type=float, help="seconds between refreshes")
    sub.add_argument("--once", action="store_true", help="print once and exit")
    args = parser.parse_args()
    if args.every is not None and args.every <= 0:
        print("dashboard_herdr: --every must be positive", file=sys.stderr)
        return 2
    try:
        return view(args.kind, args.task, args.every, args.once)
    except KeyboardInterrupt:
        return 130
    except (LaneError, StateError, OSError) as exc:
        print(f"dashboard_herdr: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
