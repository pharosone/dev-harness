#!/usr/bin/env python3
"""Stop a unit's lane on deterministic evidence that its agent went off course.

Commands (task and unit as in task_state.py; state lives in the run directory's units/):
  scope TASK UNIT --base REF [--turn-budget N] GLOB...  declare the paths a unit may change
  spend TASK UNIT --tool-uses N [--dispatch ID [--status S]] [...]  record one finished dispatch
  flag  TASK UNIT REASON                                 an agent cannot reconcile spec and tests
  check TASK UNIT [--record]                             exit 1 and print every tripwire
  report TASK                                           spend and status per unit (Markdown)
  resolve TASK UNIT DECISION                            archive the flag with the orchestrator's decision
  close TASK UNIT                                       the unit is done: stop checking it
  hook                                                   Claude Code Pre/PostToolUse[Failure](Agent) adapter

Globs are checkout-relative: `*` stays inside one directory, `**` crosses directories.

Dispatch events go to the run's events.jsonl through task_state.py's writer, for dispatches
tagged `[pharos task=T unit=U]` only: `dispatch.start` (id `<dispatch>:start`) on PreToolUse
and on a background launch, `dispatch.end` (id `<dispatch>:end`, with the unit as `work`) on completion, on a
PostToolUseFailure (status `failed`, tool calls unknown, duration from `duration_ms`) and on
`spend --dispatch ID`. Every ledger line written with an end carries its `dispatch` id, so the
dashboard counts it once. The dispatch is the hook's tool_use_id, else a hash of session,
description and prompt. A background launch also writes `dispatch.link` {dispatch, agent_id}
(id `<dispatch>:link`) from the response's agentId, the id the orchestrator sees: `spend
--dispatch` takes either id and ends the original dispatch. `--status` (done, failed, timeout,
cancelled; default done) is the end's status. A repeated delivery is skipped by id, and so is
its spend line; a failed frozen-test verification counts once per dispatch id (the ids counted
are kept in `units/<unit>-trips.json`). After writing an event the hook (and `spend --dispatch`)
regenerates the run's default dashboard (dashboard.py) best effort: a failure is a note on
stderr, never a failed hook.
Claude's Agent `usage` covers the final request only, so dispatch tokens stay null.
Exit codes: 0 clean, 1 tripped, 2 error.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from task_state import (StateError, append_event, checkouts, event_record, evidence_path, git,
                        identifier, locate, read_events, verify)

END_STATUSES = ("done", "failed", "timeout", "cancelled")
DISPATCH_TOOL_LIMIT = 80  # pharos-dev: a unit that needs more is two units
TAG = re.compile(r"\[pharos task=([A-Za-z0-9][A-Za-z0-9_.-]*) unit=([A-Za-z0-9][A-Za-z0-9_.-]*)\]")


def unit_file(run: Path, primary: Path, unit: str, suffix: str) -> Path:
    identifier(unit)
    if not (run / "run.md").is_file():
        raise StateError(f"Task is not initialized; run task_state.py init first: {run.name}")
    return evidence_path(run / "units" / f"{unit}-{suffix}", primary)


def glob_pattern(glob: str) -> re.Pattern:
    parts = re.split(r"(\*\*/?|\*|\?)", glob)
    tokens = {"**/": "(?:.*/)?", "**": ".*", "*": "[^/]*", "?": "[^/]"}
    return re.compile("".join(tokens.get(p, re.escape(p)) for p in parts) + r"\Z")


def changed_files(checkout: Path, base: str) -> set[str]:
    tracked = git("diff", "--name-only", "-z", base, cwd=checkout).split("\0")
    untracked = git("ls-files", "--others", "--exclude-standard", "-z", cwd=checkout).split("\0")
    return {name for name in (*tracked, *untracked) if name}


def read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def scope(run, primary, current, unit, base, globs, budget) -> None:
    path = unit_file(run, primary, unit, "scope.json")
    unit_file(run, primary, unit, "closed").unlink(missing_ok=True)  # a new scope reopens the unit
    write_json(path, {"version": 1, "checkout": str(current), "globs": globs,
                      "base": git("rev-parse", "--verify", f"{base}^{{commit}}", cwd=current),
                      "turn_budget": budget})


def spend(run, primary, unit, entry: dict) -> None:
    path = unit_file(run, primary, unit, "spend.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **entry}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")


def record_event(run: Path, primary: Path, kind: str, event_id: str, fields: dict) -> bool:
    """Append one event through task_state's writer; False when its id is already logged."""
    if not (run / "run.md").is_file():
        raise StateError(f"Task is not initialized; run task_state.py init first: {run.name}")
    record = event_record(kind, json.dumps(fields), event_id)
    return append_event(evidence_path(run / "events.jsonl", primary), record)


def end_fields(dispatch: str, tool_calls, duration_ms=None, status: str = "done",
               work: str | None = None) -> dict:
    duration = duration_ms / 1000 if isinstance(duration_ms, (int, float)) else None
    return {"dispatch": dispatch, "status": status, "work": work,
            "usage": {"tool_calls": tool_calls if isinstance(tool_calls, int) else None,
                      "input_tokens": None, "output_tokens": None, "duration_s": duration}}


def linked_dispatch(run: Path, name: str) -> str:
    """The dispatch id for name: a logged dispatch, else the dispatch an agent id links to."""
    events, _ = read_events(run)
    if any(e.get("type") == "dispatch.start" and e.get("dispatch") == name for e in events):
        return name
    return next((str(e["dispatch"]) for e in events if e.get("type") == "dispatch.link"
                 and e.get("agent_id") == name and e.get("dispatch")), name)


def ledger_entries(path: Path) -> tuple[list[dict], int]:
    """The ledger's readable entries and how many lines could not be read (a write cut short)."""
    if not path.is_file():
        return [], 0
    entries, bad = [], 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            bad += 1
            continue
        if isinstance(entry, dict):
            entries.append(entry)
        else:
            bad += 1
    return entries, bad


def usage(known: int, unknown: int) -> str:
    """Tool calls as recorded: a dispatch without a count is unknown, never zero."""
    return f"{known} + unknown" if unknown else str(known)


def check(run, primary, unit, record: bool,
          dispatch: str | None = None) -> tuple[list[str], list[str]]:
    """Return (tripwires, notes). With record a failed verification counts once per dispatch:
    the trips file lists the dispatch ids already counted (a repeated delivery adds nothing)."""
    trips, notes = [], []
    flag = unit_file(run, primary, unit, "flag.md")
    if unit_file(run, primary, unit, "closed").is_file():
        # Closed stops the path, budget and test checks; a new flag still stops the lane.
        if flag.is_file():
            trips.append("Agent raised a flag: " + flag.read_text(encoding="utf-8").strip()[:500])
        return trips, ["unit is closed"]
    declared = read_json(unit_file(run, primary, unit, "scope.json"), None)
    if declared:
        checkout = Path(declared["checkout"])
        if checkout.is_dir():
            patterns = [glob_pattern(g) for g in declared["globs"]]
            outside = sorted(f for f in changed_files(checkout, declared["base"])
                             if not any(p.match(f) for p in patterns))
            if outside:
                trips.append("Unit diff leaves its declared paths: " + ", ".join(outside))
        else:
            notes.append(f"Scope checkout is gone, paths not checked: {checkout}")

    manifest = unit_file(run, primary, unit, "tests.json")
    if manifest.is_file():
        counter = unit_file(run, primary, unit, "trips.json")
        state = read_json(counter, {"verify_failures": 0})
        try:
            verify(manifest, Path(declared["checkout"]) if declared else checkouts()[1])
        except StateError as exc:
            counted = state.setdefault("counted", [])
            if record and (dispatch is None or dispatch not in counted):
                state["verify_failures"] += 1
                if dispatch is not None:
                    counted.append(dispatch)
                write_json(counter, state)
            notes.append(f"Frozen tests changed: {exc}")
        if state["verify_failures"] >= 2:
            trips.append(f"Frozen tests were changed in {state['verify_failures']} dispatches")

    flag = unit_file(run, primary, unit, "flag.md")
    if flag.is_file():
        trips.append("Agent raised a flag: " + flag.read_text(encoding="utf-8").strip()[:500])

    entries, _ = ledger_entries(unit_file(run, primary, unit, "spend.jsonl"))
    if entries:
        uses = [e.get("tool_uses") or 0 for e in entries]
        if uses and uses[-1] > DISPATCH_TOOL_LIMIT:
            trips.append(f"Last dispatch used {uses[-1]} tool calls (limit {DISPATCH_TOOL_LIMIT})")
        budget = declared.get("turn_budget") if declared else None
        if budget and sum(uses) > 2 * budget:
            trips.append(f"Unit used {sum(uses)} tool calls, over twice its budget of {budget}")
    return trips, notes


def report(run, primary) -> list[str]:
    """One Markdown row per unit with evidence: dispatches, tool calls, models, status."""
    units = sorted({p.name.rsplit("-", 1)[0] for p in (run / "units").glob("*-*")
                    if p.name.endswith(("-spend.jsonl", "-scope.json", "-tests.json", "-flag.md",
                                        "-closed"))})
    rows = ["| Unit | Dispatches | Tool calls | Models | Status |", "|---|---|---|---|---|"]
    total_dispatches = total_known = total_unknown = 0
    for unit in units:
        entries, bad = ledger_entries(unit_file(run, primary, unit, "spend.jsonl"))
        counts = [e.get("tool_uses") for e in entries]
        known = sum(c for c in counts if isinstance(c, int))
        unknown = sum(1 for c in counts if not isinstance(c, int))
        models = ", ".join(sorted({e["model"] for e in entries if e.get("model")}))
        trips, notes = check(run, primary, unit, record=False)
        status = ("TRIPPED" if trips else "closed" if "unit is closed" in notes
                  else "unchecked" if any(n.startswith("Scope checkout is gone") for n in notes)
                  else "ok")
        if bad:
            status += f", {bad} unreadable ledger line{'s' if bad > 1 else ''}"
        rows.append(f"| {unit} | {len(entries)} | {usage(known, unknown)} | {models} | {status} |")
        total_dispatches += len(entries)
        total_known += known
        total_unknown += unknown
    rows.append(f"| total | {total_dispatches} | {usage(total_known, total_unknown)} | | |")
    return rows


def regenerate_dashboard(run: Path, primary: Path) -> None:
    """Best effort after an event: rewrite the run's default dashboard; never fails the caller."""
    try:
        import dashboard  # imported here: dashboard imports this module
        dashboard.regenerate(run, primary)
    except Exception as exc:  # noqa: BLE001 - a dashboard must never break a hook
        print(f"tripwire: dashboard not regenerated: {exc}", file=sys.stderr)


def dispatch_id(event: dict, tool_input: dict) -> str:
    if event.get("tool_use_id"):
        return str(event["tool_use_id"])
    key = json.dumps([event.get("session_id"), tool_input.get("description"),
                      tool_input.get("prompt")])
    return "h" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]


def hook() -> int:
    """Pre/PostToolUse/PostToolUseFailure(Agent): log the dispatch, record its spend, return
    tripwires."""
    trips = []
    try:
        event = json.load(sys.stdin)
        tool_input, response = event.get("tool_input") or {}, event.get("tool_response") or {}
        match = TAG.search(f"{tool_input.get('description', '')}\n{tool_input.get('prompt', '')}")
        name = event.get("hook_event_name")
        phase = ("start" if name == "PreToolUse" else "failed" if name == "PostToolUseFailure"
                 else response.get("status"))
        if not match or phase not in ("start", "async_launched", "completed", "failed"):
            return 0
        task, unit = match.groups()
        os.chdir(event.get("cwd") or ".")
        primary, _ = checkouts()
        run = locate(primary, task)
        if not (run / "run.md").is_file():
            return 0
        dispatch = dispatch_id(event, tool_input)
        if phase in ("start", "async_launched"):
            wrote = record_event(run, primary, "dispatch.start", f"{dispatch}:start", {
                "dispatch": dispatch, "role": tool_input.get("subagent_type"), "runtime": "claude",
                "model": tool_input.get("model"), "effort": None, "launcher": "subagent",
                "work": unit, "report": None, "pane": None,
                "background": bool(tool_input.get("run_in_background"))})
            agent = response.get("agentId")
            if phase == "async_launched" and agent:
                wrote = record_event(run, primary, "dispatch.link", f"{dispatch}:link",
                                     {"dispatch": dispatch, "agent_id": str(agent)}) or wrote
            if wrote:
                regenerate_dashboard(run, primary)
            return 0
        duration = response.get("totalDurationMs") or event.get("duration_ms")
        status = "failed" if phase == "failed" else "done"
        wrote = record_event(run, primary, "dispatch.end", f"{dispatch}:end",
                             end_fields(dispatch, response.get("totalToolUseCount"), duration,
                                        status, work=unit))
        if wrote:
            spend(run, primary, unit, {
                "dispatch": dispatch, "status": status,
                "tool_uses": response.get("totalToolUseCount"),
                "duration_ms": duration,
                "final_request_tokens": response.get("totalTokens"),
                "model": response.get("resolvedModel"),
                "agent": tool_input.get("subagent_type"),
            })
        trips, _ = check(run, primary, unit, record=True, dispatch=dispatch)
        if wrote:
            regenerate_dashboard(run, primary)
    except (StateError, OSError, ValueError, KeyError, AttributeError, TypeError) as exc:
        # A broken tripwire must not break the session; say so on stderr only.
        print(f"tripwire: {exc}", file=sys.stderr)
        return 0
    if trips:
        reason = (f"Tripwire for {task}/{unit}:\n- " + "\n- ".join(trips) +
                  "\nStop this lane: write its gate card and ask the user before any further "
                  "dispatch for this unit (pharos-dev, Taste gates).")
        print(json.dumps({"decision": "block", "reason": reason,
                          "hookSpecificOutput": {"hookEventName": name,
                                                 "additionalContext": reason}}))
    return 0


def main() -> int:
    if sys.argv[1:2] == ["hook"]:
        return hook()
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("scope", "spend", "flag", "check", "report", "resolve", "close"):
        sub = commands.add_parser(name)
        sub.add_argument("task")
        if name != "report":
            sub.add_argument("unit")
    commands.choices["scope"].add_argument("--base", required=True)
    commands.choices["scope"].add_argument("--turn-budget", type=int)
    commands.choices["scope"].add_argument("globs", nargs="+")
    commands.choices["spend"].add_argument("--tool-uses", type=int, required=True)
    commands.choices["spend"].add_argument("--model")
    commands.choices["spend"].add_argument("--agent")
    commands.choices["spend"].add_argument(
        "--dispatch", help="also log dispatch.end for this dispatch or linked agent id")
    commands.choices["spend"].add_argument("--status", choices=END_STATUSES,
                                           help="the end's status with --dispatch (default done)")
    commands.choices["flag"].add_argument("reason")
    commands.choices["resolve"].add_argument("decision")
    commands.choices["check"].add_argument("--record", action="store_true")
    args = parser.parse_args()
    try:
        primary, current = checkouts()
        run = locate(primary, args.task)
        if args.command == "scope":
            scope(run, primary, current, args.unit, args.base, args.globs, args.turn_budget)
        elif args.command == "spend":
            unit_file(run, primary, args.unit, "spend.jsonl")
            if args.status and args.dispatch is None:
                raise ValueError("--status needs --dispatch")
            dispatch = args.dispatch and linked_dispatch(run, args.dispatch)
            if dispatch is None or record_event(
                    run, primary, "dispatch.end", f"{dispatch}:end",
                    end_fields(dispatch, args.tool_uses, status=args.status or "done",
                               work=args.unit)):
                spend(run, primary, args.unit,
                      {"tool_uses": args.tool_uses, "model": args.model, "agent": args.agent,
                       **({"dispatch": dispatch} if dispatch is not None else {})})
                if dispatch is not None:
                    regenerate_dashboard(run, primary)
        elif args.command == "resolve":
            flag = unit_file(run, primary, args.unit, "flag.md")
            if not flag.is_file():
                raise StateError(f"No open flag for unit {args.unit}")
            resolved = unit_file(run, primary, args.unit, "flag-resolved.md")
            with resolved.open("a", encoding="utf-8") as stream:
                stream.write(f"{flag.read_text(encoding='utf-8').strip()}\n\nDecision: "
                             f"{args.decision.strip()}\n\n")
            flag.unlink()
        elif args.command == "close":
            closed = unit_file(run, primary, args.unit, "closed")
            closed.parent.mkdir(parents=True, exist_ok=True)
            closed.write_text(datetime.now(timezone.utc).isoformat(timespec="seconds") + "\n",
                              encoding="utf-8")
        elif args.command == "report":
            print("\n".join(report(run, primary)))
        elif args.command == "flag":
            path = unit_file(run, primary, args.unit, "flag.md")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(args.reason.strip() + "\n", encoding="utf-8")
        else:
            trips, notes = check(run, primary, args.unit, args.record)
            for line in notes:
                print(f"note: {line}")
            for line in trips:
                print(f"TRIPWIRE: {line}")
            if trips:
                return 1
            print("closed" if "unit is closed" in notes else "ok")
        return 0
    except (StateError, OSError, ValueError) as exc:
        print(f"tripwire: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
