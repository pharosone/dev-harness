#!/usr/bin/env python3
"""Stop a unit's lane on deterministic evidence that its agent went off course.

Commands (task and unit as in task_state.py; state lives in the run directory's units/):
  scope TASK UNIT --base REF [--turn-budget N] GLOB...  declare the paths a unit may change
  spend TASK UNIT --tool-uses N [...]                   record one finished dispatch
  flag  TASK UNIT REASON                                 an agent cannot reconcile spec and tests
  check TASK UNIT [--record]                             exit 1 and print every tripwire
  report TASK                                           spend and status per unit (Markdown)
  resolve TASK UNIT DECISION                            archive the flag with the orchestrator's decision
  close TASK UNIT                                       the unit is done: stop checking it
  hook                                                   Claude Code PostToolUse(Agent) adapter

Globs are checkout-relative: `*` stays inside one directory, `**` crosses directories.
Exit codes: 0 clean, 1 tripped, 2 error.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
from task_state import StateError, checkouts, evidence_path, git, identifier, locate, verify

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


def check(run, primary, unit, record: bool) -> tuple[list[str], list[str]]:
    """Return (tripwires, notes)."""
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
            if record:
                state["verify_failures"] += 1
                write_json(counter, state)
            notes.append(f"Frozen tests changed: {exc}")
        if state["verify_failures"] >= 2:
            trips.append(f"Frozen tests were changed in {state['verify_failures']} dispatches")

    flag = unit_file(run, primary, unit, "flag.md")
    if flag.is_file():
        trips.append("Agent raised a flag: " + flag.read_text(encoding="utf-8").strip()[:500])

    ledger = unit_file(run, primary, unit, "spend.jsonl")
    if ledger.is_file():
        uses = [json.loads(line).get("tool_uses") or 0
                for line in ledger.read_text(encoding="utf-8").splitlines() if line]
        if uses and uses[-1] > DISPATCH_TOOL_LIMIT:
            trips.append(f"Last dispatch used {uses[-1]} tool calls (limit {DISPATCH_TOOL_LIMIT})")
        budget = declared.get("turn_budget") if declared else None
        if budget and sum(uses) > 2 * budget:
            trips.append(f"Unit used {sum(uses)} tool calls, over twice its budget of {budget}")
    return trips, notes


def report(run, primary) -> list[str]:
    """One Markdown row per unit with evidence: dispatches, tool calls, models, status."""
    units = sorted({p.name.rsplit("-", 1)[0] for p in (run / "units").glob("*-*")
                    if p.name.endswith(("-spend.jsonl", "-scope.json", "-flag.md", "-closed"))})
    rows = ["| Unit | Dispatches | Tool calls | Models | Status |", "|---|---|---|---|---|"]
    total_dispatches = total_uses = 0
    for unit in units:
        ledger = unit_file(run, primary, unit, "spend.jsonl")
        entries = ([json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines() if line]
                   if ledger.is_file() else [])
        uses = sum(e.get("tool_uses") or 0 for e in entries)
        models = ", ".join(sorted({e["model"] for e in entries if e.get("model")}))
        trips, notes = check(run, primary, unit, record=False)
        status = "TRIPPED" if trips else "closed" if "unit is closed" in notes else "ok"
        rows.append(f"| {unit} | {len(entries)} | {uses} | {models} | {status} |")
        total_dispatches += len(entries)
        total_uses += uses
    rows.append(f"| total | {total_dispatches} | {total_uses} | | |")
    return rows


def hook() -> int:
    """PostToolUse(Agent): record the dispatch, return tripwires to the orchestrator."""
    try:
        event = json.load(sys.stdin)
        tool_input, response = event.get("tool_input") or {}, event.get("tool_response") or {}
        match = TAG.search(f"{tool_input.get('description', '')}\n{tool_input.get('prompt', '')}")
        if not match or response.get("status") != "completed":
            return 0
        task, unit = match.groups()
        os.chdir(event.get("cwd") or ".")
        primary, _ = checkouts()
        run = locate(primary, task)
        if not (run / "run.md").is_file():
            return 0
        spend(run, primary, unit, {
            "tool_uses": response.get("totalToolUseCount"),
            "duration_ms": response.get("totalDurationMs") or event.get("duration_ms"),
            "final_request_tokens": response.get("totalTokens"),
            "model": response.get("resolvedModel"),
            "agent": tool_input.get("subagent_type"),
        })
        trips, _ = check(run, primary, unit, record=True)
    except (StateError, OSError, ValueError, KeyError) as exc:
        # A broken tripwire must not break the session; say so on stderr only.
        print(f"tripwire: {exc}", file=sys.stderr)
        return 0
    if trips:
        reason = (f"Tripwire for {task}/{unit}:\n- " + "\n- ".join(trips) +
                  "\nStop this lane: write its gate card and ask the user before any further "
                  "dispatch for this unit (pharos-dev, Taste gates).")
        print(json.dumps({"decision": "block", "reason": reason,
                          "hookSpecificOutput": {"hookEventName": "PostToolUse",
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
            spend(run, primary, args.unit,
                  {"tool_uses": args.tool_uses, "model": args.model, "agent": args.agent})
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
