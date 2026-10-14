#!/usr/bin/env python3
"""Render one run as a self-contained local HTML page.

  dashboard.py TASK [--out PATH] [--stale-minutes N] [--now ISO]

Writes `<run>/dashboard.html` (a legacy run under `.claude/tasks/<task>` writes
`<primary>/.harness/dashboards/<task>.html` instead; `--out`, which must lie inside a
`.harness/` directory and end in `.html`, overrides either), prints its path and exits 0; a run that does not exist exits 1, a refused
`--out` exits 2. The page reads `events.jsonl`, `run.md`, `units/`, `gates/` and, when
present, `lanes/<dispatch>.json` (`last_activity`; `prompt_pending`, `agent_status` and `state`
for a lane waiting on the user); nothing in the run directory is written except the output
file. Tripwires come from `tripwire.check(..., record=False)`.

First screen: how many dispatches are blocked waiting for the user, running dispatches (a
dispatch.start without its dispatch.end; a lane with `prompt_pending` or a blocked status is
"blocked — waiting for the user", an idle one "idle — waiting"; one with no activity newer
than `--stale-minutes` is "liveness unknown", never failed), pending gates (`gates/<g>.md` or
a `gate.open` without a `gate.answer`), tripwires (including a "Frozen tests changed" note
below the trip threshold) and compare status (a compare without a result whose own events and
launched dispatches show no activity within the stale window is "stale"). Then vendors, read
through balance.py's importable `usage(run)`, `runway` and `dispatch_shares`: per vendor its
status, runway (the smallest current window's) and share of the run's dispatches, every usage
window (name, used, resets at, observed at, source), and the latest `balance.pick` of each role
with its reason; no current window is "unknown", never 0, and a failure to read usage shows
"usage unavailable" while the rest of the page renders. Then spend per unit
(dispatches, tool calls, tokens in, tokens out, duration, each in its own unit; a missing count
stays unknown, never 0): every dispatch event, an end without a start included, plus every
`units/<unit>-spend.jsonl` line no dispatch event accounts for (matched by its `dispatch` id).
Then finished dispatches, answered gates and the run's files, linked by relative path and never
embedded: top-level files, reports/ and gates/ first, then the rest, at most 500 with the
number left out; candidate snapshots (`compare/<id>/private/`) and `.git` are never listed. A report or gate card is linked
only when it is a real file inside the run reached without a symlink; a report named by an
absolute path inside the run is linked and shown by its run-relative path. A run without
events.jsonl renders from its files with every missing fact shown as unknown (a unit with no
ledger line has an unknown dispatch count).

regenerate(run, primary) rewrites the default output best effort (lanes.py and the tripwire
hook call it after each event they write); a failure is a note on stderr, never an error.
Concurrent regenerations (a compare and its detached lane supervisors) hold an exclusive
`fcntl.flock` on `.dashboard-lock` beside the page while they read the run and write it, so
the last page written reflects the latest events, never an older read (unlocked on Windows).

Safety: the page has no scripts and no external references; every value is HTML-escaped,
and a string that looks like a secret (the patterns of xreview.py) is shown as [redacted].
"""

import argparse
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote

try:
    import fcntl
except ImportError:  # Windows: regenerations are not serialized.
    fcntl = None

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from task_state import StateError, checkouts, locate, read_events
from tripwire import check as tripwire_check, ledger_entries
from xreview import SECRET_VALUE
import router_calibration

UNKNOWN = "unknown"
BLOCKED = "blocked — waiting for the user"
IDLE = "idle — waiting"
DEFAULT_STALE_MINUTES = 15
TEXT_CAP = 300
FILE_CAP = 500
SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
UNIT_SUFFIXES = ("-spend.jsonl", "-scope.json", "-tests.json", "-tests.sha", "-flag.md",
                 "-flag-resolved.md", "-closed", "-red.txt", "-trips.json", "-gate.txt")
USAGE = (("tool_calls", "Tool calls"), ("input_tokens", "Tokens in"),
         ("output_tokens", "Tokens out"), ("duration_s", "Duration"))


class RefusedError(Exception):
    """The output path is not allowed (exit 2)."""


# --- values -----------------------------------------------------------------------------

def redact(value: str) -> str:
    return "[redacted]" if SECRET_VALUE.search(value) or "PRIVATE KEY" in value else value


def text(value, missing: str = UNKNOWN) -> str:
    """The one way a value reaches the page: redacted, capped, escaped."""
    if value is None:
        return missing
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, (list, tuple)):
        value = ", ".join(str(v) for v in value if not isinstance(v, (dict, list)))
    elif isinstance(value, dict):
        value = json.dumps(value, sort_keys=True)
    value = redact(str(value))
    if len(value) > TEXT_CAP:
        value = value[:TEXT_CAP] + "…"
    return html.escape(value, quote=True)


def parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def stamp(moment: datetime | None) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else UNKNOWN


def span(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {seconds:02d}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes:02d}m"


def count(value) -> float | None:
    """A recorded non-negative number, or None when it is missing or not a number."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return value


def total(values: list, seconds: bool = False) -> str:
    """Sum of known values; any missing one makes it '<sum> + unknown', all missing 'unknown'."""
    known = [v for v in values if v is not None]
    if not known:
        return UNKNOWN
    shown = span(sum(known)) if seconds else str(int(sum(known)))
    return f"{shown} + {UNKNOWN}" if len(known) < len(values) else shown


# --- reading the run --------------------------------------------------------------------

def inside(path: Path, run: Path) -> bool:
    """A real file within the run directory, reached without any symlink."""
    try:
        relative = path.relative_to(run)
    except ValueError:
        return False
    if ".." in relative.parts:
        return False
    current = run
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return current.is_file() and current.resolve().is_relative_to(run.resolve())


def run_file(run: Path, name) -> Path | None:
    """A file named by an event field, run-relative or absolute, when it is a real file inside
    the run."""
    if not isinstance(name, str) or not name:
        return None
    path = Path(name)
    if path.is_absolute():
        return path if inside(path, run) else None
    if name.startswith(("/", "\\")) or ":" in name:
        return None
    path = run / name
    return path if inside(path, run) else None


def lane_state(run: Path, dispatch) -> dict:
    """`lanes/<dispatch>.json` as an object; {} when it is absent, unreadable or not ours."""
    if not isinstance(dispatch, str) or not SAFE_NAME.fullmatch(dispatch):
        return {}
    path = run / "lanes" / f"{dispatch}.json"
    if not inside(path, run):
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return state if isinstance(state, dict) else {}


def lane_activity(run: Path, dispatch) -> datetime | None:
    return parse_time(lane_state(run, dispatch).get("last_activity"))


def lane_waiting(state: dict) -> str | None:
    """What a lane waits on, from the fields lanes keeps: a brief still pending at an agent
    that has not finished starting (`prompt_pending`), or a last seen herdr `agent_status` /
    lanes `state` of blocked or idle. None when it is not known to wait."""
    seen = {state.get("agent_status"), state.get("state")}
    if state.get("prompt_pending") is True or "blocked" in seen:
        return BLOCKED
    if "idle" in seen:
        return IDLE
    return None


def unit_names(run: Path) -> list[str]:
    names = set()
    units = run / "units"
    if units.is_dir() and not units.is_symlink():
        for path in units.iterdir():
            for suffix in UNIT_SUFFIXES:
                if path.name.endswith(suffix):
                    unit = path.name[: -len(suffix)]
                    if SAFE_NAME.fullmatch(unit):
                        names.add(unit)
                    break
    return sorted(names)


def dispatches(events: list[dict]) -> tuple[dict, dict]:
    starts, ends = {}, {}
    for event in events:
        kind, key = event.get("type"), event.get("dispatch")
        if not isinstance(key, str):
            key = f"event {event.get('id')}"
        if kind == "dispatch.start":
            starts.setdefault(key, event)
        elif kind == "dispatch.end":
            ends.setdefault(key, event)
    return starts, ends


def usage_of(end: dict | None) -> dict:
    usage = end.get("usage") if end else None
    usage = usage if isinstance(usage, dict) else {}
    return {key: count(usage.get(key)) for key, _ in USAGE}


def empty_row() -> dict:
    return {"dispatches": 0, **{k: [] for k, _ in USAGE}}


def spend_rows(run: Path, events: list[dict], has_log: bool, units: list[str]) -> list[dict]:
    """Per unit: every dispatch event (a start, or an end without one) plus every ledger line
    no dispatch event accounts for (matched by its `dispatch` id; a line without one is its own
    dispatch). Without an events log a unit with no ledger line has an unknown count."""
    rows = {}
    ledgers = {unit: ledger_entries(run / "units" / f"{unit}-spend.jsonl")[0] for unit in units}
    logged = set()
    if has_log:
        starts, ends = dispatches(events)
        ledger_unit = {e["dispatch"]: unit for unit, entries in ledgers.items() for e in entries
                       if isinstance(e.get("dispatch"), str)}
        for key in [*starts, *(k for k in ends if k not in starts)]:
            work = (starts.get(key) or {}).get("work") or (ends.get(key) or {}).get("work")
            unit = (work if isinstance(work, str) and work else ledger_unit.get(key, UNKNOWN))
            row = rows.setdefault(unit, empty_row())
            row["dispatches"] += 1
            for name, value in usage_of(ends.get(key)).items():
                row[name].append(value)
        logged = set(starts) | set(ends)
    for unit, entries in ledgers.items():
        row = rows.setdefault(unit, empty_row())
        for entry in entries:
            if isinstance(entry.get("dispatch"), str) and entry["dispatch"] in logged:
                continue
            row["dispatches"] += 1
            row["tool_calls"].append(count(entry.get("tool_uses")))
            row["input_tokens"].append(None)  # the ledger never split tokens by direction
            row["output_tokens"].append(None)
            ms = count(entry.get("duration_ms"))
            row["duration_s"].append(ms / 1000 if ms is not None else None)
    result = []
    for unit in sorted(rows):
        row = rows[unit]
        # Without a log, untagged dispatches leave no trace: none recorded is not zero.
        shown = row["dispatches"] if has_log or row["dispatches"] else UNKNOWN
        result.append({"unit": unit, **row, "dispatches": shown})
    return result


def tripwire_state(run: Path, primary: Path, units: list[str]) -> dict:
    state = {}
    for unit in units:
        try:
            trips, notes = tripwire_check(run, primary, unit, record=False)
        except (StateError, OSError, ValueError, KeyError, TypeError) as exc:
            state[unit] = {"status": UNKNOWN, "trips": [], "notes": [f"not checked: {exc}"]}
            continue
        status = ("tripped" if trips else "closed" if "unit is closed" in notes
                  else "unchecked" if any(n.startswith("Scope checkout is gone") for n in notes)
                  else "ok")
        changed = any(n.startswith("Frozen tests changed") for n in notes)
        if changed and not trips:
            status += f" — frozen tests changed ({counted_dispatches(run, unit)})"
        state[unit] = {"status": status, "trips": trips, "notes": notes, "changed": changed}
    return state


def counted_dispatches(run: Path, unit: str) -> str:
    """How many dispatches the tripwire has counted as changing frozen tests (read only)."""
    path = run / "units" / f"{unit}-trips.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("verify_failures")
    except (OSError, ValueError, AttributeError):
        value = None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return "dispatches unknown"
    if value == 0:
        return "not yet counted in a dispatch"
    return f"{value} dispatch{'es' if value != 1 else ''}"


def gates(run: Path, events: list[dict], has_log: bool) -> tuple[list[dict], list[dict]]:
    cards = {}
    folder = run / "gates"
    if folder.is_dir() and not folder.is_symlink():
        for path in sorted(folder.glob("*.md")):
            cards[path.stem] = path if inside(path, run) else None  # listed, linked only if real
    opened, answered = {}, {}
    for event in events:
        gate = event.get("gate")
        if not isinstance(gate, str):
            continue
        if event.get("type") == "gate.open":
            opened.setdefault(gate, event)
        elif event.get("type") == "gate.answer":
            answered.setdefault(gate, event)
    pending = []
    for gate in sorted(set(cards) | set(opened)):
        if gate in answered:
            continue
        card = cards.get(gate)
        if card is None:
            card = run_file(run, (opened.get(gate) or {}).get("card"))
        state = "pending" if has_log else "answer unknown (no events log)"
        pending.append({"gate": gate, "card": card, "since": (opened.get(gate) or {}).get("at"),
                        "state": state})
    done = []
    for gate, event in answered.items():
        done.append({"gate": gate, "card": cards.get(gate) or run_file(run, event.get("card")),
                     "answer": event.get("answer"), "source": event.get("source"),
                     "at": event.get("at")})
    return pending, done


def compares(events: list[dict]) -> list[dict]:
    """Each compare's start, result, every event time it wrote and the dispatches it launched."""
    found = {}
    for event in events:
        kind = event.get("type")
        if not isinstance(kind, str) or not kind.startswith("compare."):
            continue
        key = event.get("compare")
        if kind not in ("compare.start", "compare.result") and not (isinstance(key, str) and key):
            continue
        key = key if isinstance(key, str) and key else f"event {event.get('id')}"
        entry = found.setdefault(key, {"compare": key, "start": None, "result": None,
                                       "moments": [], "dispatches": []})
        entry["moments"].append(parse_time(event.get("at")))
        if kind == "compare.launch" and isinstance(event.get("dispatch"), str):
            entry["dispatches"].append(event["dispatch"])
        slot = {"compare.start": "start", "compare.result": "result"}.get(kind)
        if slot and entry[slot] is None:
            entry[slot] = event
    return list(found.values())


def latest_picks(events: list[dict]) -> list[dict]:
    """The last `balance.pick` event of each role, by role name."""
    picks = {}
    for event in events:
        if event.get("type") == "balance.pick" and isinstance(event.get("role"), str):
            picks[event["role"]] = event
    return [picks[role] for role in sorted(picks)]


def vendor_state(run: Path, events: list[dict], now: datetime) -> dict:
    """Per vendor its windows, status, runway and share of dispatches, read through balance.py.

    Status and runway follow the pick: a window whose reset has passed does not count, a vendor
    without a current window is unknown, its runway the smallest current window's. Shares are
    balance.py's own count. `error` is set when balance.py or the usage could not be read; the
    shares survive a failed usage read."""
    state = {"vendors": {}, "error": None}
    try:
        import balance  # imported here: a broken balance.py must not break the page
        shares, total = balance.dispatch_shares(events)
    except Exception as exc:  # noqa: BLE001
        state["error"] = f"balance.py could not be loaded: {exc}"
        return state
    for vendor in balance.RUNTIMES:
        share = shares.get(vendor)
        if not total:
            shown = "no dispatches yet"
        elif share is None:
            shown = UNKNOWN
        else:
            shown = f"{share:.0%} ({round(share * total)} of {total})"
        state["vendors"][vendor] = {"windows": [], "status": "unavailable", "runway": None,
                                    "share": shown}
    try:
        moment = now.timestamp()
        report = balance.usage(run)
        for vendor in balance.RUNTIMES:
            windows = report[vendor]["windows"]
            current = [w for w in windows if w["resets_at"] > moment]
            runways = [balance.runway(w["used"], w["resets_at"], w["window_minutes"], moment)
                       for w in current if w["window_minutes"]]
            state["vendors"][vendor].update(
                windows=[{**w, "passed": w["resets_at"] <= moment} for w in windows],
                status="known" if current else UNKNOWN,
                runway=min(runways) if runways else None)
    except Exception as exc:  # noqa: BLE001 - any failure to read usage is shown, never raised
        state["error"] = f"{type(exc).__name__}: {exc}"
        for vendor in state["vendors"].values():
            vendor.update(windows=[], status="unavailable", runway=None)
    return state


def epoch_stamp(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return UNKNOWN
    return stamp(datetime.fromtimestamp(value, timezone.utc))


def usage_source(run: Path, source) -> str:
    """A source inside the run by its run-relative path, else as recorded."""
    if isinstance(source, str) and Path(source).is_absolute():
        try:
            return Path(source).relative_to(run).as_posix()
        except ValueError:
            pass
    return source


def compare_activity(run: Path, entry: dict, starts: dict, ends: dict) -> datetime | None:
    """The latest sign of life of a compare: its own events, and the start, end and lanes
    activity of every dispatch it launched."""
    moments = [m for m in entry["moments"] if m]
    for dispatch in entry["dispatches"]:
        moments += [parse_time((starts.get(dispatch) or {}).get("at")),
                    parse_time((ends.get(dispatch) or {}).get("at")),
                    lane_activity(run, dispatch)]
    moments = [m for m in moments if m]
    return max(moments) if moments else None


FIRST_FOLDERS = ("reports", "gates")


def hidden_folder(run: Path, folder: Path) -> bool:
    """A candidate snapshot (`compare/<id>/private/`) or any `.git`: never listed."""
    parts = folder.relative_to(run).parts
    return (parts[-1:] == (".git",)
            or (len(parts) == 3 and parts[0] == "compare" and parts[2] == "private"))


def run_files(run: Path, skip: Path) -> tuple[list[tuple[str, bool]], int]:
    """Run-relative paths and whether each may be linked (real file, no symlink on the way),
    capped at FILE_CAP, and how many more there were. Top-level files, reports/ and gates/ come
    first, then the rest in path order; candidate snapshots and .git directories are skipped."""
    first, rest = [], []
    for folder, subfolders, files in os.walk(run, followlinks=False):
        here = Path(folder)
        subfolders[:] = sorted(name for name in subfolders
                               if not hidden_folder(run, here / name))
        relative = here.relative_to(run).parts
        bucket = first if not relative or relative[0] in FIRST_FOLDERS else rest
        for name in subfolders:
            if (here / name).is_symlink():
                bucket.append(((here / name).relative_to(run).as_posix(), False))
        for name in sorted(files):
            path = here / name
            if path == skip or name.startswith(".dashboard-"):
                continue
            bucket.append((path.relative_to(run).as_posix(), inside(path, run)))
    order = {name: index for index, name in enumerate(FIRST_FOLDERS)}

    def rank(item):
        parts = item[0].split("/")
        return (order.get(parts[0], -1) if len(parts) > 1 else -1, item[0])

    listed = sorted(first, key=rank) + rest
    return listed[:FILE_CAP], max(0, len(listed) - FILE_CAP)


# --- rendering --------------------------------------------------------------------------

STYLE = """
:root { color-scheme: light dark; --bg: #ffffff; --fg: #1d2127; --muted: #5b6470;
  --line: #d9dde3; --panel: #f5f7f9; --warn: #8a5a00; --warn-bg: #fff4d6; --bad: #a3242c;
  --bad-bg: #fde8e9; --ok: #1e6b3a; --link: #1f5fbf; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #14171b; --fg: #e4e7eb; --muted: #9aa3ad; --line: #2c3239; --panel: #1b1f24;
    --warn: #f0c060; --warn-bg: #3a2e12; --bad: #f2a0a5; --bad-bg: #3d1a1d; --ok: #7fd69b;
    --link: #8db8ff; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg);
  font: 15px/1.45 -apple-system, "Segoe UI", system-ui, sans-serif; }
main { max-width: 1100px; margin: 0 auto; }
h1 { font-size: 1.35rem; margin: 0 0 4px; overflow-wrap: anywhere; }
h2 { font-size: 1.05rem; margin: 28px 0 8px; }
p, li { overflow-wrap: anywhere; }
.muted { color: var(--muted); }
.facts { display: flex; flex-wrap: wrap; gap: 6px 18px; margin: 8px 0 0; padding: 0;
  list-style: none; }
.scroll { overflow-x: auto; border: 1px solid var(--line); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--line);
  vertical-align: top; white-space: nowrap; }
td.wrap, td.reason, td.source { white-space: normal; overflow-wrap: anywhere; min-width: 12rem; }
h3 { font-size: 0.95rem; margin: 18px 0 8px; }
th { background: var(--panel); font-weight: 600; }
tr:last-child td { border-bottom: 0; }
.state-stale td.state, .pending { color: var(--warn); background: var(--warn-bg); }
.tripped { color: var(--bad); background: var(--bad-bg); }
.state-running td.state { color: var(--ok); }
a { color: var(--link); }
ul.files { columns: 2 18rem; padding-left: 1.2rem; }
@media (max-width: 600px) { body { padding: 16px; } ul.files { columns: 1; } }
"""


def table(headers: list[str], rows: list[str], empty: str) -> str:
    if not rows:
        return f'<p class="muted">{html.escape(empty)}</p>'
    head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
    return f'<div class="scroll"><table><thead><tr>{head}</tr></thead><tbody>' \
           + "".join(rows) + "</tbody></table></div>"


def link(path: Path | None, out_dir: Path, label: str | None = None) -> str:
    if path is None:
        return text(label) if label is not None else UNKNOWN
    relative = os.path.relpath(path, out_dir).replace(os.sep, "/")
    return f'<a href="{html.escape(quote(relative, safe="/._-~"), quote=True)}">' \
           f'{text(label if label is not None else path.name)}</a>'


def report_cell(report: Path | None, run: Path, out_dir: Path, named) -> str:
    """A report linked and shown by its run-relative path, else the name as recorded."""
    if report is None:
        return text(named)
    return link(report, out_dir, report.relative_to(run).as_posix())


def shadow_section(events: list[dict]) -> str:
    """Add read-only recommendations; absent shadow events leave the page unchanged."""
    rows = []
    for event in [e for e in events if e.get("type") == "router.shadow"][-20:]:
        label = router_calibration.classifier_label(event)
        badge = "<br><strong>" + text(label) + "</strong>" if label else ""
        details = "<br>".join(text(item) for item in router_calibration.shadow_details(event))
        recommendation = ("would pick " + router_calibration.show_tuple(event["recommendation"])
                          if isinstance(event.get("recommendation"), dict) else "skipped / no recommendation")
        rows.append(
            '<tr><td class="recommendation">⚖ shadow: ' + text(recommendation) + badge + '</td>'
            '<td class="actual">' + text(router_calibration.show_tuple(event.get("actual"))) + '</td>'
            '<td class="status">' + text(router_calibration.safe_text(event.get("status"))) + '</td>'
            '<td class="reason">because ' + text(router_calibration.safe_text(event.get("reason"))) + '</td>'
            '<td class="provenance">' + details + '</td></tr>')
    if not rows:
        return ""
    return ('<section id="router-shadow"><h2>Shadow recommendations</h2>'
            '<p class="muted">Static dispatch remains the actual choice. Assumptions are uncalibrated.</p>'
            + table(["Would pick", "Actual static tuple", "Status", "Reason", "Assumptions / provenance"], rows, "")
            + '</section>')


def render(run: Path, primary: Path, out: Path, now: datetime, stale: timedelta) -> str:
    out_dir = out.parent
    has_log = (run / "events.jsonl").is_file()
    events, unreadable = read_events(run)
    units = unit_names(run)
    starts, ends = dispatches(events)
    run_start = next((e for e in events if e.get("type") == "run.start"), {})

    # Running and stale dispatches.
    running, blocked = [], []
    for key, start in starts.items():
        if key in ends:
            continue
        since = parse_time(start.get("at"))
        lane = lane_state(run, start.get("dispatch"))
        moments = [m for m in (since, parse_time(lane.get("last_activity"))) if m]
        last = max(moments) if moments else None
        is_stale = last is None or now - last > stale
        waiting = lane_waiting(lane)
        if waiting == BLOCKED:
            blocked.append(key)
        state = waiting or ("liveness unknown" if is_stale else "running")
        quiet = f" (no activity for {span((now - last).total_seconds())})" if last and is_stale else ""
        runtime = span((now - since).total_seconds()) if since and now >= since else UNKNOWN
        report = run_file(run, start.get("report"))
        running.append(
            f'<tr id="dispatch-{text(key)}" class="state-{"stale" if is_stale or waiting else "running"}">'
            f'<td class="dispatch">{text(key)}</td><td class="role">{text(start.get("role"))}</td>'
            f'<td class="runtime">{text(start.get("runtime"))}</td>'
            f'<td class="model">{text(start.get("model"))}</td>'
            f'<td class="effort">{text(start.get("effort"))}</td>'
            f'<td class="launcher">{text(start.get("launcher"))}</td>'
            f'<td class="pane">{text(start.get("pane"), missing="none")}</td>'
            f'<td class="work">{text(start.get("work"))}</td>'
            f'<td class="since">{text(stamp(since))}</td><td class="elapsed">{runtime}</td>'
            f'<td class="activity">{text(stamp(last))}{text(quiet, missing="")}</td>'
            f'<td class="state">{state}</td>'
            f'<td class="report">{report_cell(report, run, out_dir, start.get("report"))}</td>'
            f'</tr>')

    pending, answered = gates(run, events, has_log)
    pending_rows = [
        f'<tr id="gate-{text(g["gate"])}" class="pending"><td class="gate">{text(g["gate"])}</td>'
        f'<td class="state">{text(g["state"])}</td><td class="since">{text(g["since"])}</td>'
        f'<td class="card">{link(g["card"], out_dir)}</td></tr>' for g in pending]
    answered_rows = [
        f'<tr id="gate-{text(g["gate"])}"><td class="gate">{text(g["gate"])}</td>'
        f'<td class="answer">{text(g["answer"])}</td><td class="wrap source">{text(g["source"])}</td>'
        f'<td class="at">{text(g["at"])}</td><td class="card">{link(g["card"], out_dir)}</td></tr>'
        for g in answered]

    trip_state = tripwire_state(run, primary, units) if (run / "run.md").is_file() else {
        unit: {"status": UNKNOWN, "trips": [], "notes": ["run.md missing"]} for unit in units}
    trip_rows = []
    for unit, state in trip_state.items():
        if not state["trips"] and state["status"] != UNKNOWN and not state.get("changed"):
            continue
        detail = state["trips"] or state["notes"]
        trip_rows.append(
            f'<tr id="trip-{text(unit)}" class="{"tripped" if state["trips"] else ""}">'
            f'<td class="unit">{text(unit)}</td><td class="status">{text(state["status"])}</td>'
            f'<td class="wrap detail">{"<br>".join(text(d) for d in detail)}</td></tr>')

    compare_rows = []
    for entry in compares(events):
        start, result = entry["start"] or {}, entry["result"]
        fields = {k: v for k, v in (result or {}).items()
                  if k not in ("v", "id", "at", "type", "compare")}
        summary = "; ".join(f"{text(k)}: {text(v)}" for k, v in sorted(fields.items()))
        state = "done" if result else "running"
        if not result:
            last = compare_activity(run, entry, starts, ends)
            if last is None or now - last > stale:
                state = "stale"
                quiet = (f"for {span((now - last).total_seconds())}" if last and now >= last
                         else "recorded")
                summary = text(f"no result and no activity {quiet}: whether it is still running "
                               "is unknown; compare.py --resume picks it up")
        compare_rows.append(
            f'<tr id="compare-{text(entry["compare"])}"{' class="pending"' if state == "stale" else ""}>'
            f'<td class="compare">{text(entry["compare"])}</td>'
            f'<td class="kind">{text(start.get("kind"))}</td>'
            f'<td class="state">{state}</td>'
            f'<td class="since">{text(start.get("at"))}</td>'
            f'<td class="wrap result">{summary or ("—" if result else "")}</td></tr>')

    vendors = vendor_state(run, events, now)
    vendor_rows, window_rows = [], []
    for vendor, info in vendors["vendors"].items():
        runway = f"{info['runway']:.2f}" if info["runway"] is not None else UNKNOWN
        vendor_rows.append(
            f'<tr id="vendor-{text(vendor)}"><td class="vendor">{text(vendor)}</td>'
            f'<td class="status">{text(info["status"])}</td>'
            f'<td class="runway">{text(runway)}</td>'
            f'<td class="share">{text(info["share"])}</td></tr>')
        for item in info["windows"]:
            used = count(item.get("used"))
            resets = epoch_stamp(item.get("resets_at"))
            window_rows.append(
                f'<tr id="window-{text(vendor)}-{text(item.get("name"))}">'
                f'<td class="vendor">{text(vendor)}</td>'
                f'<td class="window">{text(item.get("name"))}</td>'
                f'<td class="used">{text(f"{used:.0%}" if used is not None else None)}</td>'
                f'<td class="resets">{text(resets + (" (passed)" if item["passed"] else ""))}</td>'
                f'<td class="observed">{text(epoch_stamp(item.get("observed_at")))}</td>'
                f'<td class="source">{text(usage_source(run, item.get("source")))}</td></tr>')
    pick_rows = [
        f'<tr id="pick-{text(p["role"])}"><td class="role">{text(p["role"])}</td>'
        f'<td class="runtime">{text(p.get("runtime"))}</td>'
        f'<td class="model">{text(p.get("model"))}</td>'
        f'<td class="effort">{text(p.get("effort"))}</td>'
        f'<td class="profile">{text(p.get("profile"))}</td>'
        f'<td class="not_runtime">{text(p.get("not_runtime"), missing="—")}</td>'
        f'<td class="at">{text(p.get("at"))}</td>'
        f'<td class="reason">{text(p.get("reason"))}</td></tr>' for p in latest_picks(events)]
    usage_note = (f'<p id="usage-unavailable" class="pending">usage unavailable: '
                  f'{text(vendors["error"])}. Shares and picks below come from the events log.</p>'
                  if vendors["error"] else "")
    window_table = ("" if vendors["error"] else
                    table(["Vendor", "Window", "Used", "Resets at", "Observed at", "Source"],
                          window_rows, "No usage window observed for either vendor."))

    spend = []
    for row in spend_rows(run, events, has_log, units):
        status = trip_state.get(row["unit"], {}).get("status", "")
        spend.append(
            f'<tr id="spend-{text(row["unit"])}"><td class="unit">{text(row["unit"])}</td>'
            f'<td class="dispatches">{text(row["dispatches"])}</td>'
            f'<td class="tool_calls">{total(row["tool_calls"])}</td>'
            f'<td class="input_tokens">{total(row["input_tokens"])}</td>'
            f'<td class="output_tokens">{total(row["output_tokens"])}</td>'
            f'<td class="duration">{total(row["duration_s"], seconds=True)}</td>'
            f'<td class="status">{text(status or "—")}</td></tr>')

    finished = []
    for key, end in ends.items():
        start = starts.get(key, {})
        usage = usage_of(end)
        report = run_file(run, start.get("report"))
        finished.append(
            f'<tr id="dispatch-{text(key)}"><td class="dispatch">{text(key)}</td>'
            f'<td class="role">{text(start.get("role"))}</td>'
            f'<td class="runtime">{text(start.get("runtime"))}</td>'
            f'<td class="model">{text(start.get("model"))}</td>'
            f'<td class="launcher">{text(start.get("launcher"))}</td>'
            f'<td class="work">{text(start.get("work"))}</td>'
            f'<td class="state">{text(end.get("status"))}</td>'
            f'<td class="tool_calls">{total([usage["tool_calls"]])}</td>'
            f'<td class="input_tokens">{total([usage["input_tokens"]])}</td>'
            f'<td class="output_tokens">{total([usage["output_tokens"]])}</td>'
            f'<td class="duration">{total([usage["duration_s"]], seconds=True)}</td>'
            f'<td class="at">{text(end.get("at"))}</td>'
            f'<td class="report">{report_cell(report, run, out_dir, start.get("report"))}</td>'
            f'</tr>')

    files = []
    listed, not_listed = run_files(run, out)
    for relative, linkable in listed:
        if linkable:
            files.append(f"<li>{link(run / relative, out_dir, relative)}</li>")
        else:
            files.append(f'<li>{text(relative)} <span class="muted">(symlink, not linked)</span></li>')

    waiting_note = (f'<p id="waiting-for-user" class="pending">{len(blocked)} dispatch'
                    f'{"es" if len(blocked) != 1 else ""} blocked — waiting for the user: '
                    f'{", ".join(text(k) for k in blocked)}. Answer it in its pane.</p>'
                    if blocked else "")
    log_note = ("" if has_log else
                '<p class="pending">This run has no events log: no events log was kept, so '
                "dispatches, gate answers and usage not in its files are unknown.</p>")
    facts = "".join(
        f"<li><span class=\"muted\">{label}:</span> {text(run_start.get(key))}</li>"
        for key, label in (("workflow", "workflow"), ("depth", "depth"), ("compare", "compare"),
                           ("runtime_mode", "runtime mode")))
    import watch
    watch_summary = watch.summary(run)
    watch_events = [e for e in events if e.get("type") == "watch"]
    watch_rows = [f'<li>{text(e.get("at"))} — {text(e.get("line"))}</li>'
                  for e in watch_events[-50:]]
    dispatch_head = ["Dispatch", "Role", "Runtime", "Model", "Effort", "Launcher", "Pane", "Work",
                     "Since", "Running for", "Last activity", "State", "Report"]
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'">
<meta name="referrer" content="no-referrer">
<title>Run {text(run.name)}</title>
<style>{STYLE}</style>
</head>
<body>
<main>
<h1>Run {text(run.name)}</h1>
<p class="muted">Generated {text(stamp(now))}; stale after {int(stale.total_seconds() // 60)} min without activity.
Unreadable event lines skipped: <span id="unreadable-count">{unreadable}</span>.</p>
<ul class="facts">{facts}</ul>
{log_note}
{waiting_note}
<section id="watch">
<h2>{text(watch_summary)}</h2>
<p class="muted">Latest {min(len(watch_events), 50)} of {len(watch_events)} watch events; full history in events.jsonl.</p>
<ul>{''.join(watch_rows) or '<li>No watch conditions emitted.</li>'}</ul>
</section>
{shadow_section(events)}
<section id="running">
<h2>Running and stale dispatches</h2>
{table(dispatch_head, running, "No dispatch is running.")}
<p class="muted">"liveness unknown" means no activity was recorded within the stale window; it does not mean the dispatch failed.</p>
</section>
<section id="pending-gates">
<h2>Pending gates</h2>
{table(["Gate", "State", "Opened", "Card"], pending_rows, "No gate is waiting.")}
</section>
<section id="tripwires">
<h2>Tripwires</h2>
{table(["Unit", "Status", "Detail"], trip_rows, "No tripwire is set off.")}
</section>
<section id="compare">
<h2>Compare</h2>
{table(["Compare", "Kind", "State", "Started", "Result"], compare_rows, "No compare in this run.")}
</section>
<section id="vendors">
<h2>Vendors</h2>
{usage_note}
{table(["Vendor", "Usage", "Runway", "Share of dispatches"], vendor_rows, "Vendor state could not be read.")}
<p class="muted">Runway is the smallest current window's (1 − used) / (1 − elapsed): below 1 the vendor would run out before its reset. "unknown" means no current observation (none recorded, or every window has reset); a pick treats it as neutral. Shares count dispatch.start by runtime, compare candidates excluded.</p>
{window_table}
<h3>Latest pick per role</h3>
{table(["Role", "Runtime", "Model", "Effort", "Profile", "Not runtime", "At", "Reason"], pick_rows, "No balance.pick recorded.")}
</section>
<section id="spend">
<h2>Spend per unit</h2>
{table(["Unit", "Dispatches", "Tool calls", "Tokens in", "Tokens out", "Duration", "Tripwire"], spend, "No unit has evidence yet.")}
</section>
<section id="finished">
<h2>Finished dispatches</h2>
{table(["Dispatch", "Role", "Runtime", "Model", "Launcher", "Work", "State", "Tool calls", "Tokens in", "Tokens out", "Duration", "Ended", "Report"], finished, "No dispatch has finished.")}
</section>
<section id="answered-gates">
<h2>Answered gates</h2>
{table(["Gate", "Answer", "User's words", "Answered", "Card"], answered_rows, "No gate has been answered.")}
</section>
<section id="reports">
<h2>Files in the run</h2>
{"<ul class=\"files\">" + "".join(files) + "</ul>" if files else '<p class="muted">No files.</p>'}
{f'<p id="files-not-listed" class="pending">{not_listed} more files not listed (the list stops at {FILE_CAP}).</p>' if not_listed else ""}
<p class="muted">Candidate snapshots (the private folder of each compare) and .git directories are not listed.</p>
</section>
</main>
</body>
</html>
"""


# --- command line -----------------------------------------------------------------------

def default_output(run: Path, primary: Path) -> Path:
    """`<run>/dashboard.html`, or `<primary>/.harness/dashboards/<task>.html` for a legacy run."""
    try:
        parts = run.relative_to(primary).parts
    except ValueError:
        parts = run.parts
    if ".harness" in parts:
        return run / "dashboard.html"
    return primary / ".harness" / "dashboards" / f"{run.name}.html"


def output_path(run: Path, primary: Path, out: str | None) -> Path:
    if out is None:
        return default_output(run, primary)
    path = Path(out).absolute()
    resolved = path.resolve()
    if ".harness" not in resolved.parent.parts:
        raise RefusedError(f"--out must lie inside a .harness/ directory: {out}")
    if resolved.suffix != ".html":
        raise RefusedError(f"--out must be an .html file: {out}")
    if resolved.is_dir():
        raise RefusedError(f"--out is a directory: {out}")
    return resolved


def write_page(out: Path, page: str) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=out.parent, prefix=".dashboard-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(page)
        os.replace(temp, out)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def regenerate(run: Path, primary: Path) -> Path | None:
    """Rewrite the run's default dashboard now; best effort, a failure is a note on stderr."""
    try:
        run, primary = run.resolve(), primary.resolve()
        out = default_output(run, primary)
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out.parent / ".dashboard-lock", "a") as lock:
            if fcntl is not None:  # read and write as one step: no stale page wins a race
                fcntl.flock(lock, fcntl.LOCK_EX)
            page = render(run, primary, out, datetime.now(timezone.utc),
                          timedelta(minutes=DEFAULT_STALE_MINUTES))
            write_page(out, page)
        return out
    except Exception as exc:  # noqa: BLE001 - never fail the caller over its dashboard
        print(f"dashboard: not regenerated: {exc}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("task")
    parser.add_argument("--out", help="output file inside a .harness/ directory")
    parser.add_argument("--stale-minutes", type=float, default=DEFAULT_STALE_MINUTES,
                        help=f"minutes without activity before a dispatch shows as liveness "
                             f"unknown (default {DEFAULT_STALE_MINUTES})")
    parser.add_argument("--now", help="the current time, ISO 8601 (default: the clock)")
    args = parser.parse_args()
    try:
        now = datetime.now(timezone.utc)
        if args.now is not None:
            now = parse_time(args.now)
            if now is None:
                raise RefusedError(f"--now is not an ISO 8601 time: {args.now}")
        if args.stale_minutes < 0:
            raise RefusedError("--stale-minutes must not be negative")
        primary, _ = checkouts()
        run = locate(primary, args.task)
        if not run.is_dir():
            print(f"dashboard: no such run: {args.task}", file=sys.stderr)
            return 1
        out = output_path(run, primary, args.out)
        page = render(run.resolve(), primary, out, now, timedelta(minutes=args.stale_minutes))
        write_page(out, page)
        print(out)
        return 0
    except RefusedError as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 2
    except (StateError, OSError, ValueError) as exc:
        print(f"dashboard: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
