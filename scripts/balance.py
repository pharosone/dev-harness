#!/usr/bin/env python3
"""Balance work between Claude and Codex by how fast each vendor's usage limits burn.

  balance.py usage [--task T] [--json]
  balance.py usage set --vendor claude|codex --window NAME --used F --resets-at EPOCH
                       [--window-minutes N]
  balance.py observe --from LOG
  balance.py pick --task T --profile P --role R [--not-runtime claude|codex] [--now EPOCH]

usage prints, per vendor, {"status": "known"|"unknown", "windows": [{name, used (0-1),
window_minutes, resets_at (epoch s), observed_at (epoch s), source}]} (--json: one JSON object
keyed by vendor; else one line per window). Sources:
- Claude: the last `rate_limit_event` of a stream-json log (`rate_limit_info.unifiedWindows`,
  else its top-level rateLimitType / utilization / resetsAt). Candidates are the run's
  `<run>/lanes/*.log` (with --task; observed_at is the log's mtime) and the observations that
  `observe --from LOG` appended to `<state>/claude-observations.jsonl` (observed_at is LOG's
  mtime), so lanes or the orchestrator can feed it; the newest observation wins. A lanes-log
  observation not yet in that file is appended to it, so another task sees it too. An
  unreadable file or log is no observation.
- Codex: the newest session file under `$CODEX_HOME/sessions/**/*.jsonl` (default ~/.codex) that
  holds `rate_limits`, its last such line: `primary` and `secondary` when not null,
  used_percent / 100; observed_at is the earlier of that line's `timestamp` and the file's
  mtime (a later line without rate_limits does not make it newer). A window of 300 minutes is named
  five_hour, of 10080 minutes seven_day, any other keeps its key (primary / secondary).
- `usage set` writes a manual override of one window to `<state>/vendor-usage.json`; it wins
  over that window's observation until a strictly newer observation arrives (source "manual").
  A window length comes from --window-minutes, else five_hour 300 and seven_day 10080 minutes;
  any other name without it is exit 2. Claude windows without a length use the same table.
`<state>` is `$XDG_STATE_HOME/dev-harness`, default `~/.local/state/dev-harness`. No data is
"unknown", never a guess. observe exits 1 when LOG holds no rate_limit_event.

pick prints {runtime, model, effort, reason, shares, runway, usage_status} and is importable as
pick(task, profile, role, not_runtime=None, now=None) (run from inside the checkout). Usage is
`usage --task T` at --now (default: the clock); a window whose resets_at has passed is dropped,
and a vendor with no current window is unknown. Runway of a window = (1 - used) /
max(1 - elapsed, 0.01), elapsed = 1 - (resets_at - now) / (window_minutes * 60) clamped to
[0, 1]; a vendor's runway is its smallest window's (a window without a length is checked only
against stop_at); unknown usage is runway 1.0 and the reason says "usage unknown". A vendor is
excluded when any window has used >= stop_at. A window whose runway is < 1 once min_elapsed of
it has passed would run out before the reset: that is soft, never a reason for exit 6 by
itself; of two vendors left, one that would and one that would not, the latter is picked, ahead
of the minimum share. Of the vendors left (minus --not-runtime): none is exit 6, "both vendors
are near their limits; ask the user" (or, with --not-runtime, that the one left is), still JSON
on stdout with runtime null and no event; one is picked; of two, one below min_share of the run's
dispatches (`dispatch.start` events counted by runtime, compare candidates — `work` starting
with "cmp-" — not counted; no dispatches yet, no share rule) is picked, else the larger runway
wins, but the runtime of the run's previous `balance.pick` stays unless the other's runway is
larger by more than hysteresis. shares is {vendor: fraction or null when there are no
dispatches}, runway {vendor: number}, usage_status {vendor: known|unknown}. model and effort come
from profile P of the merged profiles (adapters/models.json with the overlay's models.json
deep-merged over it, as xreview.py merges them); P must exist on both runtimes, else exit 2.
Each pick appends a `balance.pick` event {runtime, model, effort, profile, role, reason,
runway, shares, not_runtime} to the run's events log.

Overlay `project.json` (in $HARNESS_PROJECT, else <checkout>/harness/project), optional key
`balance`: {"min_share": 0.3, "stop_at": 0.85, "hysteresis": 0.15, "min_elapsed": 0.25} —
numbers; min_share, stop_at and min_elapsed in [0, 1] (stop_at above 0), hysteresis >= 0; any
other key or value is exit 2.

Exit codes: 0 done, 1 nothing observed (observe), 2 bad arguments or configuration (an
uninitialized task, a bad overlay, a missing profile), 6 both vendors near their limits.
"""

import argparse
import contextlib
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # Windows: state files are written without a lock.
    fcntl = None

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
import project
import task_state
from task_state import StateError
import xreview

RUNTIMES = ("claude", "codex")
WINDOW_MINUTES = {"five_hour": 300, "seven_day": 10080}
DEFAULTS = {"min_share": 0.3, "stop_at": 0.85, "hysteresis": 0.15, "min_elapsed": 0.25}
NEUTRAL_RUNWAY = 1.0
COMPARE_PREFIX = "cmp-"
LIMITS_EXIT = 6


class BalanceError(Exception):
    """Bad arguments or configuration (exit 2)."""


# Where the machine-wide state and Codex's sessions live.

def state_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "dev-harness"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or str(Path.home() / ".codex"))


@contextlib.contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path.with_name(path.name + ".lock"), "a") as lock:
        if fcntl is not None:
            fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(lock, fcntl.LOCK_UN)


def number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def window(name: str, used: float, minutes, resets_at, observed_at: float, source: str) -> dict:
    return {"name": name, "used": round(float(used), 6), "window_minutes": minutes,
            "resets_at": int(resets_at), "observed_at": round(observed_at, 3), "source": source}


# Claude: rate_limit_event lines of a stream-json log.

def last_rate_limit_info(path: Path) -> dict | None:
    info = None
    try:
        text = path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        if "rate_limit_event" not in line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if (isinstance(value, dict) and value.get("type") == "rate_limit_event"
                and isinstance(value.get("rate_limit_info"), dict)):
            info = value["rate_limit_info"]
    return info


def claude_windows(info: dict, observed_at: float, source: str) -> list[dict]:
    unified = info.get("unifiedWindows")
    if isinstance(unified, dict) and unified:
        entries = list(unified.items())
    else:
        entries = [(info.get("rateLimitType"), info)]
    windows = []
    for name, entry in entries:
        if not (isinstance(name, str) and isinstance(entry, dict)):
            continue
        used, resets = entry.get("utilization"), entry.get("resetsAt")
        if not (number(used) and number(resets)):
            continue
        minutes = entry.get("windowMinutes")
        minutes = minutes if number(minutes) and minutes > 0 else WINDOW_MINUTES.get(name)
        windows.append(window(name, used, minutes, resets, observed_at, source))
    return windows


def observations_path() -> Path:
    return state_dir() / "claude-observations.jsonl"


def recorded_observations() -> list[dict]:
    """The machine-wide observation records; none when the file is missing or unreadable."""
    try:
        text = observations_path().read_bytes().decode("utf-8", errors="replace")
    except OSError:  # missing or unreadable: no observation, never a crash
        return []
    records = []
    for line in text.splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if (isinstance(value, dict) and number(value.get("observed_at"))
                and isinstance(value.get("rate_limit_info"), dict)):
            records.append(value)
    return records


def record_observation(observed: float, source: str, info: dict) -> None:
    record = {"observed_at": observed, "source": source, "rate_limit_info": info}
    path = observations_path()
    with locked(path), path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")


def claude_observations(run: Path | None) -> list[tuple[float, list[dict]]]:
    """The run's lanes logs and the machine-wide records; a lanes-log observation not yet
    recorded is recorded (best effort), so another task sees it."""
    records = recorded_observations()
    known = {(str(r.get("source")), float(r["observed_at"])) for r in records}
    found = []
    if run is not None and (run / "lanes").is_dir():
        for log in sorted((run / "lanes").glob("*.log")):
            info = last_rate_limit_info(log)
            try:
                observed = log.stat().st_mtime
            except OSError:
                continue
            if info is None:
                continue
            windows = claude_windows(info, observed, str(log))
            found.append((observed, windows))
            if windows and (str(log), observed) not in known:
                with contextlib.suppress(OSError):
                    record_observation(observed, str(log), info)
                known.add((str(log), observed))
    path = observations_path()
    for value in records:
        observed = float(value["observed_at"])
        found.append((observed, claude_windows(value["rate_limit_info"], observed,
                                               str(value.get("source", path)))))
    return [entry for entry in found if entry[1]]


def observe(log: Path) -> list[dict]:
    """Append the last rate_limit_event of LOG to the observations; [] when it holds none."""
    info = last_rate_limit_info(log)
    if info is None:
        return []
    observed = log.stat().st_mtime
    windows = claude_windows(info, observed, str(log.resolve()))
    if not windows:
        return []
    record_observation(observed, str(log.resolve()), info)
    return windows


# Codex: rate_limits in the newest session file.

def find_key(value, key: str):
    if isinstance(value, dict):
        if isinstance(value.get(key), dict):
            return value[key]
        children = value.values()
    elif isinstance(value, list):
        children = value
    else:
        return None
    for child in children:
        found = find_key(child, key)
        if found is not None:
            return found
    return None


def codex_windows(limits: dict, observed_at: float, source: str) -> list[dict]:
    windows = []
    for key in ("primary", "secondary"):
        entry = limits.get(key)
        if not isinstance(entry, dict):
            continue
        used, minutes, resets = (entry.get("used_percent"), entry.get("window_minutes"),
                                 entry.get("resets_at"))
        if not (number(used) and number(resets)):
            continue
        minutes = minutes if number(minutes) and minutes > 0 else None
        name = next((known for known, length in WINDOW_MINUTES.items() if length == minutes),
                    key)
        windows.append(window(name, used / 100, minutes, resets, observed_at, source))
    return windows


def line_time(value, default: float) -> float:
    """The epoch of a session line's ISO `timestamp`; default without a readable one."""
    stamp = value.get("timestamp") if isinstance(value, dict) else None
    if not isinstance(stamp, str):
        return default
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


def codex_observation() -> tuple[float, list[dict]] | None:
    sessions = codex_home() / "sessions"
    if not sessions.is_dir():
        return None
    files = []
    for path in sessions.rglob("*.jsonl"):
        with contextlib.suppress(OSError):
            files.append((path.stat().st_mtime, path))
    for observed, path in sorted(files, reverse=True):
        try:
            lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if "rate_limits" not in line:
                continue
            try:
                value = json.loads(line)
            except ValueError:
                continue
            limits = find_key(value, "rate_limits")
            at = min(observed, line_time(value, observed))
            windows = codex_windows(limits, at, str(path)) if limits else []
            if windows:
                return at, windows
    return None


# Manual overrides and the merged usage.

def overrides_path() -> Path:
    return state_dir() / "vendor-usage.json"


def read_overrides() -> dict:
    path = overrides_path()
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def set_override(vendor: str, name: str, used: float, resets_at: int,
                 minutes: int | None = None) -> dict:
    if vendor not in RUNTIMES:
        raise BalanceError(f"unknown vendor {vendor!r}; expected claude or codex")
    if not name or not name.replace("_", "").replace("-", "").isalnum():
        raise BalanceError(f"bad window name {name!r}")
    if not 0 <= used <= 1:
        raise BalanceError(f"--used must be between 0 and 1, got {used}")
    minutes = minutes if minutes is not None else WINDOW_MINUTES.get(name)
    if minutes is None or minutes <= 0:
        raise BalanceError(f"window {name!r} has no known length; pass --window-minutes")
    entry = {"used": used, "window_minutes": minutes, "resets_at": resets_at,
             "observed_at": time.time()}
    path = overrides_path()
    with locked(path):
        data = read_overrides()
        vendor_entries = data.get(vendor) if isinstance(data.get(vendor), dict) else {}
        data[vendor] = {**vendor_entries, name: entry}
        with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False,
                                         encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.replace(stream.name, path)
    return entry


def merged_windows(observation: tuple[float, list[dict]] | None, manual: dict) -> list[dict]:
    windows = list(observation[1]) if observation else []
    for name, entry in sorted(manual.items()):
        if not (isinstance(entry, dict) and all(number(entry.get(key)) for key in
                                                 ("used", "window_minutes", "resets_at",
                                                  "observed_at"))):
            continue
        index = next((i for i, w in enumerate(windows) if w["name"] == name), None)
        if index is not None and windows[index]["observed_at"] > entry["observed_at"]:
            continue  # a strictly newer observation wins over the override
        record = window(name, entry["used"], int(entry["window_minutes"]), entry["resets_at"],
                        float(entry["observed_at"]), "manual")
        if index is None:
            windows.append(record)
        else:
            windows[index] = record
    return windows


def usage(run: Path | None = None) -> dict:
    """{vendor: {"status", "windows"}} from the observations and manual overrides."""
    claude = claude_observations(run)
    sources = {"claude": max(claude, key=lambda entry: entry[0]) if claude else None,
               "codex": codex_observation()}
    manual = read_overrides()
    report = {}
    for vendor in RUNTIMES:
        entries = manual.get(vendor) if isinstance(manual.get(vendor), dict) else {}
        windows = merged_windows(sources[vendor], entries)
        report[vendor] = {"status": "known" if windows else "unknown", "windows": windows}
    return report


def run_directory(task: str, initialized: bool) -> tuple[Path, Path, Path]:
    """The primary checkout, the current checkout and the run directory of task."""
    primary, current = task_state.checkouts()
    run = task_state.locate(primary, task)
    marker = task_state.evidence_path(run / "run.md", primary)
    if initialized and not marker.is_file():
        raise BalanceError(f"Task is not initialized; run task_state.py init first: {task}")
    return primary, current, run


# The pick.

def runway(used: float, resets_at: float, window_minutes: float, now: float) -> float:
    return (1 - used) / max(1 - elapsed_fraction(resets_at, window_minutes, now), 0.01)


def settings(root: Path) -> dict:
    try:
        overlay = project.load(root, "project.json", {})
    except project.OverlayError as exc:
        raise BalanceError(f"Cannot use {exc}") from None
    values = overlay.get("balance", {})
    if not isinstance(values, dict):
        raise BalanceError("project.json: balance must be an object")
    unknown = sorted(set(values) - set(DEFAULTS))
    if unknown:
        raise BalanceError(f"project.json: unknown balance keys: {', '.join(unknown)}")
    result = {**DEFAULTS, **values}
    for key, value in result.items():
        if not number(value):
            raise BalanceError(f"project.json: balance.{key} must be a number, got {value!r}")
    if not 0 <= result["min_share"] <= 1:
        raise BalanceError("project.json: balance.min_share must be within [0, 1]")
    if not 0 < result["stop_at"] <= 1:
        raise BalanceError("project.json: balance.stop_at must be within (0, 1]")
    if result["hysteresis"] < 0:
        raise BalanceError("project.json: balance.hysteresis must not be negative")
    if not 0 <= result["min_elapsed"] <= 1:
        raise BalanceError("project.json: balance.min_elapsed must be within [0, 1]")
    return result


def profiles(root: Path, name: str) -> dict:
    try:
        data, path = xreview.models(root)
    except xreview.ReviewError as exc:
        raise BalanceError(str(exc)) from None
    result = {}
    for runtime in RUNTIMES:
        entry = data.get(runtime, {}).get(name) if isinstance(data.get(runtime), dict) else None
        if not (isinstance(entry, dict) and isinstance(entry.get("model"), str)
                and isinstance(entry.get("effort"), str)):
            raise BalanceError(f"profile {name!r} is missing on {runtime} in {path}; a pick "
                               "needs the same profile on both vendors")
        result[runtime] = {"model": entry["model"], "effort": entry["effort"]}
    return result


def elapsed_fraction(resets_at: float, window_minutes: float, now: float) -> float:
    return min(max(1 - (resets_at - now) / (window_minutes * 60), 0.0), 1.0)


def assess(vendor: dict, now: float, config: dict) -> tuple[str, float, list[str], list[str]]:
    """(status, runway, why hard-excluded, why projected to run out) of one vendor at now."""
    current = [w for w in vendor["windows"] if w["resets_at"] > now]
    if not current:
        return "unknown", NEUTRAL_RUNWAY, [], []
    hard, soft, runways = [], [], []
    for item in current:
        if item["used"] >= config["stop_at"]:
            hard.append(f"{item['name']} used {item['used']:.2f} >= stop_at "
                        f"{config['stop_at']:.2f}")
        if item["window_minutes"]:
            value = runway(item["used"], item["resets_at"], item["window_minutes"], now)
            runways.append(value)
            if value < 1 and elapsed_fraction(item["resets_at"], item["window_minutes"],
                                              now) >= config["min_elapsed"]:
                soft.append(f"{item['name']} runway {value:.2f} < 1, it would run out before "
                            "the reset")
    return "known", min(runways) if runways else NEUTRAL_RUNWAY, hard, soft


def dispatch_shares(events: list[dict]) -> tuple[dict, int]:
    counts = dict.fromkeys(RUNTIMES, 0)
    for event in events:
        work = event.get("work")
        if (event.get("type") == "dispatch.start" and event.get("runtime") in counts
                and not (isinstance(work, str) and work.startswith(COMPARE_PREFIX))):
            counts[event["runtime"]] += 1
    total = sum(counts.values())
    shares = {v: (round(counts[v] / total, 4) if total else None) for v in RUNTIMES}
    return shares, total


def previous_pick(events: list[dict]) -> str | None:
    for event in reversed(events):
        if event.get("type") == "balance.pick" and event.get("runtime") in RUNTIMES:
            return event["runtime"]
    return None


def decide(status: dict, runways: dict, why: dict, soft: dict, shares: dict, total: int,
           previous: str | None, not_runtime: str | None, config: dict) -> tuple[str | None, str]:
    candidates = [v for v in RUNTIMES if v != not_runtime]
    eligible = [v for v in candidates if not why[v]]
    if not eligible:
        if not_runtime:
            left = candidates[0]
            return None, (f"{left} is near its limits ({'; '.join(why[left])}) and "
                          f"--not-runtime {not_runtime} rules out the other vendor; ask the user")
        details = "; ".join(f"{v}: {', '.join(why[v])}" for v in RUNTIMES)
        return None, f"both vendors are near their limits; ask the user ({details})"
    if len(eligible) == 1:
        chosen = eligible[0]
        other = next(v for v in RUNTIMES if v != chosen)
        if other == not_runtime:
            return chosen, f"{chosen}: --not-runtime {not_runtime}"
        return chosen, f"{chosen}: {other} excluded ({', '.join(why[other])})"
    steady = [v for v in eligible if not soft[v]]
    if len(steady) == 1:
        chosen = steady[0]
        other = next(v for v in eligible if v != chosen)
        return chosen, f"{chosen}: {other} is burning too fast ({', '.join(soft[other])})"
    below = [v for v in steady if total and shares[v] < config["min_share"]]
    if below:
        chosen = min(below, key=lambda v: shares[v])
        return chosen, (f"{chosen}: its share {shares[chosen]:.2f} of {total} dispatches is "
                        f"below min_share {config['min_share']:.2f}")
    order = sorted(eligible, key=lambda v: (-runways[v], v != previous,
                                            shares[v] if shares[v] is not None else 0,
                                            RUNTIMES.index(v)))
    best = order[0]
    if previous in eligible and previous != best:
        gap = runways[best] - runways[previous]
        if gap <= config["hysteresis"]:
            return previous, (f"{previous}: stays with the previous pick, {best}'s runway "
                              f"{runways[best]:.2f} leads by {gap:.2f} <= hysteresis "
                              f"{config['hysteresis']:.2f}")
    other = next(v for v in eligible if v != best)
    reason = (f"{best}: larger runway {runways[best]:.2f} against {other}'s "
              f"{runways[other]:.2f}")
    if not steady:
        reason += ("; both would run out before their resets ("
                   + "; ".join(soft[best] + soft[other]) + ")")
    return best, reason


def pick(task: str, profile: str, role: str, not_runtime: str | None = None,
         now: float | None = None, *, brief: str | None = None,
         tier: int | None = None, router_weights: dict | str | None = None) -> dict:
    """The pick for one worker; runtime is None when no vendor can take it (exit 6)."""
    if not_runtime is not None and not_runtime not in RUNTIMES:
        raise BalanceError(f"unknown runtime {not_runtime!r}; expected claude or codex")
    if not role:
        raise BalanceError("--role must not be empty")
    now = time.time() if now is None else now
    primary, current, run = run_directory(task, initialized=True)
    config = settings(current)
    models = profiles(current, profile)
    report = usage(run)
    status, runways, why, soft = {}, {}, {}, {}
    for vendor in RUNTIMES:
        status[vendor], runways[vendor], why[vendor], soft[vendor] = assess(
            report[vendor], now, config)
    events, _ = task_state.read_events(run)
    shares, total = dispatch_shares(events)
    chosen, reason = decide(status, runways, why, soft, shares, total,
                            previous_pick(events), not_runtime, config)
    unknown = [v for v in RUNTIMES if status[v] == "unknown"]
    if unknown:
        reason += "; " + ", ".join(unknown) + " usage unknown, treated as neutral (runway 1.0)"
    result = {"runtime": chosen,
              "model": models[chosen]["model"] if chosen else None,
              "effort": models[chosen]["effort"] if chosen else None,
              "reason": reason + ".",
              "shares": shares,
              "runway": {v: round(runways[v], 4) for v in RUNTIMES},
              "usage_status": status}
    import router_shadow
    shadow = router_shadow.evaluate(current, run, primary,
        actual={key: result[key] for key in ("runtime", "model", "effort")},
        profile=profile, role=role, tier=tier, brief=brief, weights=router_weights,
        usage=report, now=now, not_runtime=not_runtime, origin="balance.pick")
    if chosen:
        fields = {"runtime": chosen, "model": result["model"], "effort": result["effort"],
                  "profile": profile, "role": role, "reason": result["reason"],
                  "runway": result["runway"], "shares": shares, "not_runtime": not_runtime}
        entry = task_state.event_record("balance.pick", json.dumps(fields), None)
        task_state.append_event(task_state.evidence_path(run / "events.jsonl", primary), entry)
    if shadow is not None:
        result["shadow"] = shadow
        router_shadow.append(run, primary, shadow)
    if chosen or shadow is not None:
        regenerate_dashboard(run, primary)
    return result


def regenerate_dashboard(run: Path, primary: Path) -> None:
    """Best effort after a pick: rewrite the run's default dashboard, never fail."""
    try:
        import dashboard  # imported late: a broken dashboard must not break a pick
        dashboard.regenerate(run, primary)
    except Exception as exc:  # noqa: BLE001
        print(f"balance: dashboard not regenerated: {exc}", file=sys.stderr)


# CLI

def stamp(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def print_usage(report: dict) -> None:
    for vendor in RUNTIMES:
        windows = report[vendor]["windows"]
        if not windows:
            print(f"{vendor}: unknown (no observation, no manual override)")
        for item in windows:
            length = f"{item['window_minutes']} min" if item["window_minutes"] else "length unknown"
            print(f"{vendor} {item['name']}: used {item['used']:.0%} of {length}, resets "
                  f"{stamp(item['resets_at'])}, observed {stamp(item['observed_at'])} "
                  f"({item['source']})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    usage_parser = commands.add_parser("usage")
    usage_parser.add_argument("--task")
    usage_parser.add_argument("--json", action="store_true")
    actions = usage_parser.add_subparsers(dest="action")
    setter = actions.add_parser("set")
    setter.add_argument("--vendor", required=True, choices=RUNTIMES)
    setter.add_argument("--window", required=True)
    setter.add_argument("--used", required=True, type=float)
    setter.add_argument("--resets-at", required=True, type=int)
    setter.add_argument("--window-minutes", type=int)
    observer = commands.add_parser("observe")
    observer.add_argument("--from", dest="log", required=True)
    picker = commands.add_parser("pick")
    picker.add_argument("--task", required=True)
    picker.add_argument("--profile", required=True)
    picker.add_argument("--role", required=True)
    picker.add_argument("--not-runtime", choices=RUNTIMES)
    picker.add_argument("--now", type=float)
    picker.add_argument("--brief", help="compact JSON task capsule or Markdown Goal/Contract sections")
    picker.add_argument("--tier", type=int, choices=range(4), help="risk tier for shadow scoring")
    picker.add_argument("--router-weights", help="explicit JSON trait weights; no classifier call")
    args = parser.parse_args(argv)
    try:
        if args.command == "usage" and args.action == "set":
            entry = set_override(args.vendor, args.window, args.used, args.resets_at,
                                 args.window_minutes)
            print(json.dumps({"vendor": args.vendor, "window": args.window, **entry}))
            return 0
        if args.command == "usage":
            run = run_directory(args.task, initialized=False)[2] if args.task else None
            if run is not None and not run.is_dir():
                raise BalanceError(f"No such task: {args.task}")
            report = usage(run)
            if args.json:
                print(json.dumps(report, indent=2))
            else:
                print_usage(report)
            return 0
        if args.command == "observe":
            log = Path(args.log)
            if not log.is_file():
                raise BalanceError(f"No such log: {log}")
            windows = observe(log)
            if not windows:
                print(f"balance: no rate_limit_event in {log}; nothing recorded", file=sys.stderr)
                return 1
            print(json.dumps({"vendor": "claude", "windows": windows}))
            return 0
        result = pick(args.task, args.profile, args.role, args.not_runtime, args.now,
                      brief=args.brief, tier=args.tier, router_weights=args.router_weights)
        print(json.dumps(result, indent=2))
        if result["runtime"] is None:
            print(f"balance: {result['reason']}", file=sys.stderr)
            return LIMITS_EXIT
        return 0
    except (BalanceError, StateError, OSError) as exc:
        print(f"balance: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
