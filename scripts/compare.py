#!/usr/bin/env python3
"""Have two independent agents write the same approach memo or investigation, then compare them.

Usage: compare.py design --task T --input FILE --checkout DIR [--mode standard|deep]
                         [--candidate RUNTIME:PROFILE --candidate RUNTIME:PROFILE]
                         [--arbiter RUNTIME:PROFILE] [--resume COMPARE_ID]
                         [--timeout SEC] [--arbiter-timeout SEC] [--keep-snapshots]
       compare.py investigation --task T --input FILE --checkout DIR
                                [--candidate RUNTIME:PROFILE --candidate RUNTIME:PROFILE]
                                [--resume COMPARE_ID] [--timeout SEC] [--critique-timeout SEC]
                                [--keep-snapshots]
       compare.py status --task T [--compare COMPARE_ID]

design takes the frozen interview record (a Markdown file whose requirements table has an
Answer column; no table, or a row with an empty answer, is refused) and hashes it (sha256).
Both competitors get one identical brief: `prompts/compare-memo.md`, the input hash and the
record. Defaults: standard — claude:implementation and codex:implementation, arbiter
codex:critical-review; deep — claude:deep-reasoning and codex:deep-reasoning, arbiter
claude:escalation. Profiles come from `adapters/models.json` with the checkout's overlay
models.json merged over it, as xreview.py does. The arbiter must be neither competitor (not the
same runtime and profile, nor the same runtime, model and effort) and its profile must not rank
below either competitor's (mechanical < routine < implementation < critical-review <
escalation = deep-reasoning).

Events (all carry `compare` and `kind`; ids are deterministic, so a repeat is a skipped
duplicate and the first writer wins):
  compare.start   <id>.start         mode, input_hash, candidates [{label, runtime, profile,
                                     model, effort}], arbiter {runtime, profile, model, effort},
                                     capability equal|capability-unequal, capability_reason.
                                     Written before any launch: it reserves the round's four
                                     slots (two candidates, two arbiter passes); one round, no
                                     retry.
  compare.claim   <id>.claim.<slot>  the process that appends it launches the slot; slots are
                                     candidate-<label>, arbiter-1, arbiter-2; `claimed` is its
                                     time (epoch seconds)
  compare.launch  <id>.launch.<slot> dispatch (lanes.py), launcher, report, started; a failed
                                     launch has dispatch null and an error
  compare.seal    <id>.seal.<label>  sha256 of the memo, or null with `excluded` (the reason);
                                     `source` report | final message | null
  compare.pass    <id>.pass.<n>      one arbiter pass: order, letters, valid, the verdict
                                     mapped back to labels
  compare.result  <id>.result        stable, ranking or tie, unarbitrated,
                                     none_meets_mandatory, excluded, user_steered (per
                                     candidate, as in judgement.json)

Candidates start through `lanes.py start --compare-candidate --capability candidate --scratch
<private>/scratch` (a herdr pane under HERDR_ENV=1, else headless): lanes' candidate profile on
both vendors, read the source, run a shell, write only inside that scratch. The two arbiter
passes start through `lanes.py start --launcher headless` on their read-only profile. Every
worker gets a private directory `<dir>/private/<random>/`: a candidate writes its report into
its scratch (`<private>/scratch/report.md`), so it can write it as it goes; an arbiter or a
critic writes `<private>/report.md`. Nothing a worker writes is in a shared path until it is
sealed. Capability is `lanes.capability_profile(runtime, launcher, "candidate")` of both
candidates: `equal` when the two match. Both work in their own snapshot of the source; the
scratch-only writes are enforced by Claude Code's Bash sandbox and scoped Write/Edit rules on
Claude and by the workspace-write sandbox on Codex, whose writable roots are the snapshot (-C)
and the scratch (--add-dir); reads are not confined on either, so
independence rests on the brief. capability_reason and judgement.json's `capability_note` say so. Every event compare.py records (not a skipped duplicate)
regenerates the run's default dashboard through `dashboard.regenerate`, best effort: a failure
is a note on stderr. After both candidates end (or time out) each memo is copied to
`<dir>/candidate-<label>.md` and hashed. A missing or empty report falls back to the
candidate's final message (`<report>.last-message.md`, headless Codex, which lanes copies over
the report only on `done`), sealed with `source: final message` and marked "from the final
message" in comparison.md; a report lanes copied that way (its dispatch.end has
`report_source: "final_message"`) is sealed and marked the same. With neither, the memo is
excluded and cannot win.
With two memos the arbiter runs twice, labels anonymised as random letters and the order of pass
2 the reverse of pass 1; a failed mandatory requirement moves that memo below every memo without
one; passes with different winners give
`stable: false` ("arbiter unstable"). The passes' failed_mandatory (per memo) and
disagreements are merged in order without exact repeats: an item equal to an earlier one once
case, punctuation and whitespace are dropped is dropped, nothing fuzzier
(`merged_from_passes`: the valid passes merged). `failed_mandatory` stays a flat list per memo;
`failed_mandatory_by_row` groups it by the row an item starts with ("row N", "rows N",
"requirement N", any case; key "row N", ascending, "other" last for an item naming no row),
each distinct wording kept, and comparison.md has one bullet per memo and row, the wordings
joined by " / ". When every
arbitrated memo failed a mandatory requirement, `none_meets_mandatory` is true and the summary
says "No memo meets every mandatory requirement; the ranking orders imperfect options" before
the winner row. With fewer than two memos nothing is arbitrated
(`stable: null`, confidence low): a single memo is not a winner — `ranking` stays null, `tie`
false and it is listed in `unarbitrated`; the summary says "not arbitrated (one memo)".
Outputs in `<run>/compare/<id>/`: `comparison.md` (both
arbiter outputs verbatim, then a table), `judgement.json` (ranking or tie, none_meets_mandatory,
failed_mandatory, failed_mandatory_by_row, merged_from_passes, disagreements, confidence,
input_hash, stable, unarbitrated, capability, capability_reason, capability_note, excluded,
user_steered, notes). user_steered is per candidate: false when it ran headless (no way to
type into it), "unknown" in a herdr pane (the user could type into it; herdr neither blocks nor
attributes that input); comparison.md names the steerable candidates.

After compare.result, both commands remove the candidates' snapshots (`<scratch>/src`) through
`lanes.snapshot_cleanup` (this round's ended candidates whose snapshot lanes created; the
sealed memos and reports, in the compare folder, stay) unless --keep-snapshots. A snapshot
that is not removed, or a cleanup that fails, is a note on stderr, never a failed compare; the
printed object's `snapshots` is removed, kept or "not all removed".

--resume ID reuses the reservation, sealed memos and finished passes found in the events log
and launches only unclaimed slots; a slot another process claimed is awaited, never launched
again: for the claimer's whole launch window (claimed + LAUNCH_TIMEOUT, the timeout of its own
`lanes.py start`, + GRACE), and only after it is the slot sealed "claimed but never launched".
The input must hash to the reserved input_hash.

investigation takes the investigation brief (Markdown; refused when empty, or without a
`## Question` section or a first heading that states the question) and hashes it. Two
investigators, one per vendor (default claude:deep-reasoning and codex:deep-reasoning, role
investigator), get one identical brief: the compare framing, `roles/investigator.md` with its
report format, the input hash and the brief. Reports are sealed as memos are. Then each sealed
report is critiqued, headless and in parallel, by the OTHER vendor at critical-review (role
critic): the critique brief holds `prompts/compare-critique.md`, the full investigation brief
(the question and what is known), the other investigation's `**Answer:**` line alone (labelled
"the other investigation's answer, for comparison") and that one report; nothing else of the
critic's own vendor's report. A missing report is not critiqued and is excluded. The merge is
deterministic, no third model: `disagreements.md` (claims a critique accepts, claims in
dispute with the report's evidence and the critique's, the cheapest settling observation for
each), `judgement.json` (kind investigation, agreed, disputed, settles, answer_disputed
(null, unknown, unless both critiques are valid), valid_critiques, confidence — low when a
critique disputes the answer or says the two answers conflict
(`answers_conflict`), a report is missing, a critique gave no valid result, a report has no
`**Answer:**` line or states no confidence, else the lower of the two stated confidences —
input_hash, capability, capability_note, excluded, user_steered as design's, notes) and
`comparison.md` (both critiques verbatim). Its
events are design's with slots candidate-<label> and critique-<label>; compare.start carries
`critics` {report label: the critic}, compare.pass is one critique (`pass` critique-<label>),
compare.result carries answer_disputed, confidence, excluded and user_steered. --resume
reuses sealed reports and finished critiques.

Exit codes: 0 a result was written (stable or not), 1 a run failure, 2 bad input or
configuration (nothing reserved or launched).
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import time

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
import lanes
import task_state
from task_state import StateError
import xreview

HARNESS = Path(__file__).resolve().parents[1]
LANES = Path(__file__).resolve().with_name("lanes.py")
KIND = "design"
RANK = {"mechanical": 0, "routine": 1, "implementation": 2, "critical-review": 3,
        "escalation": 4, "deep-reasoning": 4}
DEFAULTS = {"standard": (("claude", "implementation"), ("codex", "implementation"),
                         ("codex", "critical-review")),
            "deep": (("claude", "deep-reasoning"), ("codex", "deep-reasoning"),
                     ("claude", "escalation"))}
CANDIDATE_TIMEOUT = 2700.0
ARBITER_TIMEOUT = 900.0
GRACE = 60.0  # beyond a worker's own timeout, and beyond a claim's launch window
LAUNCH_TIMEOUT = 120.0  # one `lanes.py start`; another process awaits a claim this long
PASSES = (1, 2)
LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
CONFIDENCE = ("low", "medium", "high")
EMPTY_ANSWERS = {"", "-", "--", "—", "–", "?", "tbd", "todo"}
JSON_BLOCK = re.compile(r"```json[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)
# A failed requirement's row: "row 4", "Rows 4 and 6", "requirement 4", "**Row 4**: ...".
ROW_REF = re.compile(r"^\W*(?:rows?|requirements?)\s*#?\s*(\d+)", re.IGNORECASE)
ROW_LEAD = re.compile(r"^\W*(?:rows?|requirements?)\s*#?\s*\d+\**\s*[:.\-\u2013\u2014]\s*",
                      re.IGNORECASE)
OTHER_ROW = "other"
NONE_MEETS = ("No memo meets every mandatory requirement; the ranking orders imperfect "
              "options.")
SEPARATOR = re.compile(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?")
# How each runtime holds a candidate to "writes only inside its scratch" (lanes.py
# candidate_flags): the same profile on paper, enforced differently.
ENFORCEMENT = {
    "claude": ("a Claude candidate works in its own snapshot of the source, its Write and Edit "
               "rules are scoped to the scratch and its Bash runs in Claude Code's sandbox "
               "(configured by lanes, fail-closed)"),
    "codex": ("a Codex candidate works in its own snapshot of the source and its workspace-write "
              "sandbox has two writable roots: that snapshot (-C) and the scratch (--add-dir)"),
}
# Reads are not confined on either runtime (lanes.CANDIDATE_READS).
READS = ("both can still read the source and the run directory by absolute path, so "
         "independence rests on the brief")

class InputError(ValueError):
    """Bad input or configuration: nothing is reserved or launched (exit 2)."""


class CompareError(Exception):
    """A run failure (exit 1)."""


# Input.

def cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body)]


def check_requirements(text: str) -> int:
    """The number of requirement rows; InputError without a table or with an empty answer."""
    lines = text.splitlines()
    for index, line in enumerate(lines[:-1]):
        if not line.lstrip().startswith("|") or not SEPARATOR.fullmatch(lines[index + 1].strip()):
            continue
        header = [cell.lower() for cell in cells(line)]
        if "answer" not in header:
            continue
        column = header.index("answer")
        rows = []
        for row in lines[index + 2:]:
            if not row.lstrip().startswith("|"):
                break
            rows.append(cells(row))
        if not rows:
            break
        for number, row in enumerate(rows, 1):
            answer = row[column] if column < len(row) else ""
            if answer.strip().lower() in EMPTY_ANSWERS:
                name = row[0] if row and row[0] and column != 0 else str(number)
                raise InputError(f"the requirements table has an empty answer in row {name}; "
                                 "finish the interview before comparing")
        return len(rows)
    raise InputError("the input has no requirements table (a Markdown table with an Answer "
                     "column); pass the frozen interview record")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# Profiles.

def resolve(spec: str, data: dict) -> dict:
    runtime, _, name = spec.partition(":")
    if runtime not in lanes.RUNTIMES or name not in RANK:
        raise InputError(f"{spec!r} is not RUNTIME:PROFILE (runtimes {', '.join(lanes.RUNTIMES)}; "
                         f"profiles {', '.join(RANK)})")
    entry = (data.get(runtime) or {}).get(name)
    if not isinstance(entry, dict):
        raise InputError(f"models.json has no {runtime}.{name} profile")
    model, effort = entry.get("model"), entry.get("effort")
    if not all(isinstance(v, str) and xreview.NAME.fullmatch(v) for v in (model, effort)):
        raise InputError(f"models.json: {runtime}.{name} needs a model and an effort")
    return {"runtime": runtime, "profile": name, "model": model, "effort": effort}


def agent(entry: dict) -> tuple[str, str, str]:
    return entry["runtime"], entry["model"], entry["effort"]


def configure(mode: str, overrides: list[str] | None, arbiter_spec: str | None,
              checkout: Path) -> tuple[list[dict], dict]:
    try:
        data, _ = xreview.models(checkout)
    except xreview.ReviewError as exc:
        raise InputError(str(exc)) from None
    first, second, default_arbiter = DEFAULTS[mode]
    if overrides and len(overrides) != 2:
        raise InputError("--candidate is given exactly twice, or not at all")
    specs = overrides or [f"{first[0]}:{first[1]}", f"{second[0]}:{second[1]}"]
    candidates = [resolve(spec, data) for spec in specs]
    arbiter = resolve(arbiter_spec or ":".join(default_arbiter), data)
    if agent(candidates[0]) == agent(candidates[1]):
        raise InputError("the two competitors are the same agent (runtime, model and effort)")
    same_runtime = candidates[0]["runtime"] == candidates[1]["runtime"]
    for entry in candidates:
        entry["label"] = (f"{entry['runtime']}-{entry['profile']}" if same_runtime
                          else entry["runtime"])
    if candidates[0]["label"] == candidates[1]["label"]:
        raise InputError("the two competitors need different labels")
    for entry in candidates:
        named = f"{entry['runtime']}:{entry['profile']}"
        if (arbiter["runtime"], arbiter["profile"]) == (entry["runtime"], entry["profile"]) \
                or agent(arbiter) == agent(entry):
            raise InputError(f"the arbiter {arbiter['runtime']}:{arbiter['profile']} "
                             f"({arbiter['model']}/{arbiter['effort']}) is the competitor {named}; "
                             "it must have written neither memo")
        if RANK[arbiter["profile"]] < RANK[entry["profile"]]:
            raise InputError(f"the arbiter profile {arbiter['profile']} is weaker than the "
                             f"competitor {named}")
    ordered = [{key: entry[key] for key in ("label", "runtime", "profile", "model", "effort")}
               for entry in candidates]
    return ordered, arbiter


def capability_note(candidates: list[dict]) -> str:
    """How the candidates' scratch-only writes are enforced, which differs by runtime."""
    runtimes = [runtime for runtime in ENFORCEMENT
                if runtime in {c["runtime"] for c in candidates}]
    sentence = "; ".join(ENFORCEMENT[runtime] for runtime in runtimes)
    reads = READS if len(runtimes) > 1 else READS.replace("both can", "it can")
    return (f"Enforcement differs by runtime: {sentence}; {reads}." if len(runtimes) > 1
            else f"Enforcement: {sentence}; {reads}.")


def capability(launchers: dict[str, str | None], candidates: list[dict]) -> tuple[str, str]:
    """`equal` when lanes gives both candidates the same capability profile, with the reason."""
    profiles = {}
    for c in candidates:
        launcher = launchers.get(c["label"])
        try:
            profiles[c["label"]] = (lanes.capability_profile(c["runtime"], launcher, "candidate")
                                    if launcher else None)
        except ValueError:  # a launcher lanes gives no candidate profile
            profiles[c["label"]] = None
    described = "; ".join(
        f"{label} ({launchers.get(label) or 'not launched'}): "
        + (", ".join(f"{k} {v}" for k, v in p.items()) if p else "unknown")
        for label, p in profiles.items())
    values = list(profiles.values())
    if all(values) and all(p == values[0] for p in values):
        return "equal", (f"{described}: lanes' candidate profile on both. "
                         f"{capability_note(candidates)}")
    return "capability-unequal", (f"{described}: the profiles differ or one is unknown (a "
                                  "candidate that was not launched has none).")


# Events.

class Run:
    def __init__(self, task: str):
        self.task = task
        try:
            self.primary, self.run = lanes.run_directory(task)
        except lanes.LaneError as exc:
            raise InputError(str(exc)) from None
        self.log = task_state.evidence_path(self.run / "events.jsonl", self.primary)

    def emit(self, kind: str, fields: dict, event_id: str) -> bool:
        """Record one event; a recorded one (not a skipped duplicate) regenerates the dashboard."""
        entry = task_state.event_record(kind, json.dumps(fields), event_id)
        recorded = task_state.append_event(self.log, entry)
        if recorded:
            regenerate_dashboard(self.run, self.primary)
        return recorded

    def events(self) -> list[dict]:
        events, _ = task_state.read_events(self.run)
        return events

    def folder(self, cid: str) -> Path:
        path = task_state.evidence_path(self.run / "compare" / cid, self.primary)
        path.mkdir(parents=True, exist_ok=True)
        return path


def regenerate_dashboard(run: Path, primary: Path) -> None:
    """Best effort after a transition: rewrite the run's default dashboard, never fail."""
    try:
        import dashboard  # imported late: a broken dashboard must not break a compare
        dashboard.regenerate(run, primary)
    except Exception as exc:  # noqa: BLE001
        print(f"compare: dashboard not regenerated: {exc}", file=sys.stderr)


def collect(events: list[dict], cid: str) -> dict:
    """The compare's events by role: start, claims, launches, seals, passes, result."""
    state = {"start": None, "claim": {}, "launch": {}, "seal": {}, "pass": {}, "result": None,
             "ends": {}}
    for event in events:
        kind = event.get("type")
        if kind == "dispatch.end":
            state["ends"].setdefault(event.get("dispatch"), event)
        if event.get("compare") != cid or not isinstance(kind, str):
            continue
        if kind == "compare.start" and state["start"] is None:
            state["start"] = event
        elif kind == "compare.result" and state["result"] is None:
            state["result"] = event
        elif kind in ("compare.claim", "compare.launch"):
            state[kind.split(".")[1]].setdefault(event.get("slot"), event)
        elif kind == "compare.seal":
            state["seal"].setdefault(event.get("label"), event)
        elif kind == "compare.pass":
            state["pass"].setdefault(event.get("pass"), event)
    return state


def write(path: Path, text: str) -> None:
    temp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


# Workers.

class Compare:
    kind = KIND
    candidate_role = "candidate"
    noun = "memo"

    def __init__(self, run: Run, cid: str, start: dict, checkout: Path, input_text: str,
                 timeout: float, arbiter_timeout: float):
        self.run, self.cid, self.start = run, cid, start
        self.checkout, self.input_text = checkout, input_text
        self.timeout, self.arbiter_timeout = timeout, arbiter_timeout
        self.folder = run.folder(cid)
        self.blocked: set[str] = set()

    def fields(self, **extra) -> dict:
        return {"compare": self.cid, "kind": self.kind, **extra}

    def state(self) -> dict:
        return collect(self.run.events(), self.cid)

    def launch(self, slot: str, entry: dict, role: str, brief: Path, timeout: float,
               candidate: bool) -> None:
        private = self.folder / "private" / secrets.token_hex(8)
        scratch = private / "scratch"
        scratch.mkdir(parents=True)
        # A candidate may write only its scratch, so its report lives there and it can write
        # the report as it goes; arbiters and critics report next to their scratch.
        report = scratch / "report.md" if candidate else private / "report.md"
        argv = [sys.executable, str(LANES), "start", "--task", self.run.task,
                "--work", f"{self.cid}-{slot}", "--role", role, "--runtime", entry["runtime"],
                "--model", entry["model"], "--effort", entry["effort"], "--brief", str(brief),
                "--report", str(report), "--checkout", str(self.checkout),
                "--timeout", str(timeout)]
        # Candidates get lanes' candidate profile: read the source, a shell, write only the
        # private scratch. Arbiters and critics keep the read-only headless profile.
        argv += (["--compare-candidate", "--capability", "candidate", "--scratch", str(scratch)]
                 if candidate else ["--launcher", "headless"])
        fields = self.fields(slot=slot, dispatch=None, launcher=None, report=str(report),
                             started=time.time())
        try:
            proc = subprocess.run(argv, capture_output=True, text=True,
                                  timeout=LAUNCH_TIMEOUT)
            out = json.loads(proc.stdout) if proc.returncode == 0 else None
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            proc, out = None, None
            fields["error"] = str(exc)[-500:]
        if out and out.get("dispatch"):
            fields.update(dispatch=out["dispatch"], launcher=out.get("launcher"))
        elif "error" not in fields:
            fields["error"] = ((proc.stderr or proc.stdout) if proc else "")[-500:].strip() \
                or f"lanes.py start exited {proc.returncode if proc else '?'}"
        self.run.emit("compare.launch", fields, f"{self.cid}.launch.{slot}")

    def launch_of(self, slot: str) -> dict | None:
        """The slot's launch event. Another process's claim is awaited for its whole launch
        window (claimed + LAUNCH_TIMEOUT + GRACE), so a slot is never sealed as "claimed but
        never launched" while its claimer may still launch it; a claim without its time
        (older events) gets the full window from now."""
        deadline = None
        while True:
            state = self.state()
            if slot in state["launch"]:
                return state["launch"][slot]
            claim = state["claim"].get(slot)
            if claim is None:
                return None
            if deadline is None:
                claimed = claim.get("claimed")
                claimed = float(claimed) if isinstance(claimed, (int, float)) else time.time()
                deadline = claimed + LAUNCH_TIMEOUT + GRACE
            if time.time() >= deadline:
                return None
            time.sleep(0.2)

    def lanes(self, *args: str, timeout: float = 120) -> tuple[int, dict | None]:
        try:
            proc = subprocess.run([sys.executable, str(LANES), *args, "--task", self.run.task],
                                  capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired):
            return 1, None
        try:
            return proc.returncode, json.loads(proc.stdout)
        except ValueError:
            return proc.returncode, None

    def finish(self, launch: dict | None, timeout: float) -> str:
        """Wait for the slot's dispatch to end; cancel it past its time. Its end status."""
        if not launch or not launch.get("dispatch"):
            return "not launched"
        dispatch = launch["dispatch"]
        deadline = float(launch.get("started") or time.time()) + timeout + GRACE
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                self.lanes("cancel", "--dispatch", dispatch)
                code, report = self.lanes("result", "--dispatch", dispatch)
                return (report or {}).get("status") or "timeout"
            code, report = self.lanes("wait", "--dispatch", dispatch,
                                      "--timeout", str(min(remaining, 30.0)),
                                      timeout=min(remaining, 30.0) + 60)
            state = (report or {}).get("state")
            if code == 0 and state:
                return state
            if code == lanes.EXIT_BLOCKED and dispatch not in self.blocked:
                self.blocked.add(dispatch)
                print(f"compare: {launch['slot']} ({dispatch}) is blocked on the user's answer "
                      f"in herdr pane {report.get('pane') if report else '?'}; answer it there",
                      file=sys.stderr)
            if code not in (0, 1, lanes.EXIT_BLOCKED) or report is None:
                return "failed"  # lanes.py could not report on the dispatch
            if code == lanes.EXIT_BLOCKED:
                time.sleep(2)

    # Candidates.

    def candidate_brief(self) -> Path:
        path = self.folder / "brief-candidate.md"
        if not path.is_file():
            template = (HARNESS / "prompts" / "compare-memo.md").read_text(encoding="utf-8")
            write(path, template.rstrip("\n") + "\n\n## Interview record\n\n"
                  f"Input sha256: {self.start['input_hash']}\n\n"
                  + self.input_text.rstrip("\n") + "\n")
        return path

    def candidates(self) -> dict[str, dict]:
        entries = self.start["candidates"]
        state = self.state()
        pending = [c for c in entries if c["label"] not in state["seal"]]
        if pending:
            brief = self.candidate_brief()
            for entry in pending:
                slot = f"candidate-{entry['label']}"
                if self.run.emit("compare.claim", self.fields(slot=slot, pid=os.getpid(),
                                                              claimed=time.time()),
                                 f"{self.cid}.claim.{slot}"):
                    self.launch(slot, entry, self.candidate_role, brief, self.timeout,
                                candidate=True)
            for entry in pending:
                slot = f"candidate-{entry['label']}"
                launch = self.launch_of(slot)
                status = self.finish(launch, self.timeout)
                self.seal(entry["label"], launch, status, self.report_source(launch))
        return self.state()["seal"]

    def report_source(self, launch: dict | None) -> str | None:
        """The report_source lanes recorded in the dispatch's end ("final_message" when it
        copied the final message over the report), None without one."""
        if not launch or not launch.get("dispatch"):
            return None
        end = (self.state().get("ends") or {}).get(launch["dispatch"]) or {}
        return end.get("report_source")

    def seal(self, label: str, launch: dict | None, status: str,
             report_source: str | None = None) -> None:
        """Seal the candidate's report; when it is missing or empty, its final message
        (`<report>.last-message.md`, headless Codex) instead, marked `source: final message`.
        A report lanes copied from the final message (report_source "final_message") is
        marked the same."""
        text, source = "", None
        if launch and launch.get("report"):
            for path, origin in ((Path(launch["report"]), "report"),
                                 (Path(f"{launch['report']}.last-message.md"), "final message")):
                try:
                    text = path.read_text(encoding="utf-8")
                except (OSError, UnicodeDecodeError):
                    text = ""
                if text.strip():
                    source = ("final message" if origin == "report"
                              and report_source == "final_message" else origin)
                    break
        fields = self.fields(label=label, status=status, sha256=None, excluded=None,
                             source=source)
        if text.strip():
            write(self.folder / f"candidate-{label}.md", text)
            fields["sha256"] = sha256_bytes(text.encode("utf-8"))
        elif launch is None:
            fields["excluded"] = "claimed but never launched"
        elif not launch.get("dispatch"):
            fields["excluded"] = f"launch failed: {launch.get('error')}"
        else:
            fields["excluded"] = f"no {self.noun} or an empty one (dispatch {status})"
        self.run.emit("compare.seal", fields, f"{self.cid}.seal.{label}")

    def memo(self, label: str, seal: dict) -> str:
        path = self.folder / f"candidate-{label}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CompareError(f"sealed {self.noun} {path} is unreadable: {exc}") from None
        if sha256_bytes(text.encode("utf-8")) != seal["sha256"]:
            raise CompareError(f"sealed {self.noun} {path} changed after it was sealed")
        return text

    # Arbiter.

    def order(self, labels: list[str]) -> list[str]:
        """Pass 1's order: fixed by the compare id, so every process agrees on it."""
        return sorted(labels, key=lambda label: sha256_bytes(f"{self.cid}:{label}".encode()))

    def arbiter_brief(self, number: int, letters: dict[str, str], memos: dict[str, str]) -> Path:
        template = (HARNESS / "prompts" / "compare-arbiter.md").read_text(encoding="utf-8")
        parts = [template.rstrip("\n"), "", "## Interview record", "",
                 f"Input sha256: {self.start['input_hash']}", "", self.input_text.rstrip("\n"), ""]
        for letter, label in letters.items():
            parts += [f"## Memo {letter}", memos[label].rstrip("\n"), ""]
        parts += ["## End of memos", ""]
        path = self.folder / f"brief-arbiter-{number}.md"
        write(path, "\n".join(parts))
        return path

    def passes(self, memos: dict[str, str]) -> dict[int, dict]:
        first = self.order(list(memos))
        orders = {1: first, 2: list(reversed(first))}
        state = self.state()
        pending = [n for n in PASSES if n not in state["pass"]]
        claims = {}
        for number in pending:
            slot = f"arbiter-{number}"
            picked = secrets.SystemRandom().sample(LETTERS, len(orders[number]))
            letters = dict(zip(picked, orders[number]))
            if self.run.emit("compare.claim", self.fields(slot=slot, pid=os.getpid(),
                                                          claimed=time.time(), letters=letters),
                             f"{self.cid}.claim.{slot}"):
                brief = self.arbiter_brief(number, letters, memos)
                self.launch(slot, self.start["arbiter"], "arbiter", brief, self.arbiter_timeout,
                            candidate=False)
            claims[number] = self.state()["claim"].get(slot)
        for number in pending:
            slot = f"arbiter-{number}"
            launch = self.launch_of(slot)
            status = self.finish(launch, self.arbiter_timeout)
            letters = (claims.get(number) or {}).get("letters") or {}
            self.record_pass(number, orders[number], letters, launch, status)
        return self.state()["pass"]

    def record_pass(self, number: int, order: list[str], letters: dict[str, str],
                    launch: dict | None, status: str) -> None:
        text = ""
        if launch and launch.get("report"):
            try:
                text = Path(launch["report"]).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
        fields = self.fields(**{"pass": number}, order=order, letters=letters, status=status,
                             sha256=None, valid=False, error=None, ranking=None, tie=False,
                             failed_mandatory={}, disagreements=[], confidence=None)
        if text.strip():
            write(self.folder / f"arbiter-{number}.md", text)
            fields["sha256"] = sha256_bytes(text.encode("utf-8"))
            try:
                fields.update(parse_verdict(text, letters), valid=True)
            except ValueError as exc:
                fields["error"] = str(exc)
        else:
            fields["error"] = f"no output (dispatch {status})"
        self.run.emit("compare.pass", fields, f"{self.cid}.pass.{number}")


def parse_verdict(text: str, letters: dict[str, str]) -> dict:
    """The last fenced JSON block, checked and mapped from letters back to labels."""
    blocks = JSON_BLOCK.findall(text)
    if not blocks:
        raise ValueError("no fenced JSON block")
    try:
        data = json.loads(blocks[-1])
    except ValueError as exc:
        raise ValueError(f"the JSON block does not parse: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("the JSON block is not an object")
    tie = data.get("tie") is True
    ranking = data.get("ranking")
    if not tie:
        if not isinstance(ranking, list) or sorted(map(str, ranking)) != sorted(letters):
            raise ValueError(f"ranking {ranking!r} does not list each memo once")
    failed = data.get("failed_mandatory") or {}
    if not isinstance(failed, dict) or any(
            key not in letters or not isinstance(value, list) for key, value in failed.items()):
        raise ValueError("failed_mandatory must map memo letters to lists")
    disagreements = data.get("disagreements") or []
    if not isinstance(disagreements, list):
        raise ValueError("disagreements must be a list")
    confidence = data.get("confidence")
    if confidence not in CONFIDENCE:
        raise ValueError(f"confidence {confidence!r} is not high, medium or low")

    def named(sentence) -> str:
        return re.sub(r"\bMemo ([A-Z])\b",
                      lambda m: f"Memo {letters.get(m.group(1), m.group(1))}", str(sentence))

    mapped_failed = {letters[k]: [str(item) for item in v] for k, v in failed.items() if v}
    if tie:
        labels = list(letters.values())
        passing = [label for label in labels if label not in mapped_failed]
        ranked = None
        if passing and len(passing) < len(labels):  # a tie cannot outweigh a failed requirement
            ranked, tie = passing + [label for label in labels if label in mapped_failed], False
    else:
        ranked = [letters[letter] for letter in ranking]
        ranked = ([label for label in ranked if label not in mapped_failed]
                  + [label for label in ranked if label in mapped_failed])
    return {"ranking": ranked, "tie": tie, "failed_mandatory": mapped_failed,
            "disagreements": [named(item) for item in disagreements], "confidence": confidence}


def words_of(item: str) -> str:
    """An item normalised for the repeat check: case, punctuation and whitespace dropped."""
    return " ".join(re.sub(r"[^\w\s]|_", " ", str(item)).casefold().split())


def without_repeats(items: list[str]) -> list[str]:
    """Items in order, minus exact repeats once normalised (words_of): the first one stays.
    Nothing fuzzier: paraphrases stay, and so do claims that differ only in word order."""
    seen: set[str] = set()
    out = []
    for item in items:
        norm = words_of(item)
        if norm not in seen:
            seen.add(norm)
            out.append(item)
    return out


def row_of(item: str) -> str:
    """The requirement row a failed-requirement item names ("row N"), else OTHER_ROW."""
    match = ROW_REF.match(str(item))
    return f"row {int(match.group(1))}" if match else OTHER_ROW


def by_row(items: list[str]) -> dict[str, list[str]]:
    """Items grouped by row, rows in ascending order and OTHER_ROW last; each row's distinct
    wordings in their order."""
    groups: dict[str, list[str]] = {}
    for item in items:
        groups.setdefault(row_of(item), []).append(item)
    order = sorted((key for key in groups if key != OTHER_ROW), key=lambda key: int(key[4:]))
    return {key: groups[key] for key in order + ([OTHER_ROW] if OTHER_ROW in groups else [])}


def wording(item: str) -> str:
    """An item without its leading "row N:" (kept when more follows the number, as in
    "rows 4 and 6: ...")."""
    return ROW_LEAD.sub("", str(item), count=1) or str(item)


def sealed_from(seal: dict) -> str:
    """The memo cell's suffix for a memo sealed from the candidate's final message."""
    return " (from the final message)" if seal.get("source") == "final message" else ""


def winner(entry: dict) -> str | None:
    if not entry.get("valid"):
        return None
    return "tie" if entry.get("tie") else (entry.get("ranking") or [None])[0]


# Results.

def steering(launchers: dict[str, str | None], labels: list[str]) -> dict[str, bool | str]:
    """Whether the user could have steered each candidate: False for one not in a herdr pane
    (headless, or never launched: there is nothing to type into), "unknown" for a pane — the
    user could type into it, and herdr neither blocks nor attributes that input."""
    return {label: "unknown" if launchers.get(label) == "pane" else False for label in labels}


def steering_lines(judgement: dict) -> list[str]:
    """comparison.md's lines naming the candidates the user could and could not steer."""
    steered = judgement.get("user_steered") or {}
    closed = [label for label, value in steered.items() if value is False]
    open_ = [label for label, value in steered.items() if value is not False]
    out = []
    if closed:
        out.append(f"Not steerable (headless, no way to type into it): {', '.join(closed)}")
    if open_:
        out.append("Steerable (herdr pane, the user could type into it; not tracked): "
                   f"{', '.join(open_)}")
    return [line for item in out for line in (item, "")]


def conclude(compare: Compare, seals: dict, passes: dict, launchers: dict[str, str]) -> dict:
    start = compare.start
    labels = [c["label"] for c in start["candidates"]]
    excluded = [label for label in labels if not (seals.get(label) or {}).get("sha256")]
    valid = [label for label in labels if label not in excluded]
    notes = []
    cap, cap_reason = capability(launchers, start["candidates"])
    if cap != start.get("capability"):
        notes.append(f"capability was reserved as {start.get('capability')}, the launchers "
                     f"gave {cap}")
    failed: dict[str, list[str]] = {}
    disagreements: list[str] = []
    unarbitrated: list[str] = []
    merged = 0
    if len(valid) < 2:
        # One surviving memo is never checked against the rubric: it is not a winner.
        unarbitrated = valid
        verdict = {"ranking": None, "tie": False, "stable": None, "confidence": "low"}
        notes.append(f"{len(valid)} memo(s) sealed: nothing to arbitrate")
    else:
        entries = [passes.get(n) or {} for n in PASSES]
        merged = sum(1 for entry in entries if entry.get("valid"))
        for entry in entries:
            for label, items in (entry.get("failed_mandatory") or {}).items():
                failed.setdefault(label, []).extend(items)
            disagreements += entry.get("disagreements") or []
        failed = {label: without_repeats(items) for label, items in failed.items()}
        # (the flat lists stay as they were for older readers; the grouped shape is by row)
        disagreements = without_repeats(disagreements)
        winners = [winner(entry) for entry in entries]
        for number, entry in zip(PASSES, entries):
            if not entry.get("valid"):
                notes.append(f"arbiter pass {number} gave no valid verdict: "
                             f"{entry.get('error') or 'not recorded'}")
        stable = None not in winners and winners[0] == winners[1]
        if stable:
            first = entries[0]
            confidence = min((e["confidence"] for e in entries), key=CONFIDENCE.index)
            verdict = {"ranking": None if first["tie"] else first["ranking"], "tie": first["tie"],
                       "stable": True, "confidence": confidence}
        else:
            verdict = {"ranking": None, "tie": False, "stable": False, "confidence": "low"}
            if None not in winners:
                disagreements.append(f"arbiter unstable: pass 1 put {winners[0]} first, pass 2 "
                                     f"{winners[1]}")
    steered = steering(launchers, labels)
    # Every arbitrated memo failed a mandatory row: the ranking orders imperfect options.
    none_meets = len(valid) >= 2 and all(failed.get(label) for label in valid)
    judgement = {"compare": compare.cid, "kind": KIND, **verdict, "unarbitrated": unarbitrated,
                 "none_meets_mandatory": none_meets, "failed_mandatory": failed,
                 "failed_mandatory_by_row": {label: by_row(items)
                                             for label, items in failed.items()},
                 "merged_from_passes": merged,
                 "disagreements": disagreements, "input_hash": start["input_hash"],
                 "capability": cap, "capability_reason": cap_reason,
                 "capability_note": capability_note(start["candidates"]), "excluded": excluded,
                 "excluded_reasons": {label: seals[label].get("excluded") if label in seals
                                      else "not sealed" for label in excluded},
                 "user_steered": steered, "notes": notes}
    write(compare.folder / "judgement.json", json.dumps(judgement, indent=1) + "\n")
    write(compare.folder / "comparison.md", render(compare, judgement, seals, passes, launchers))
    result = {"stable": judgement["stable"], "excluded": excluded}
    result.update({"tie": True} if judgement["tie"] else {"ranking": judgement["ranking"]})
    result["unarbitrated"] = unarbitrated
    result["none_meets_mandatory"] = none_meets
    result["user_steered"] = steered
    compare.run.emit("compare.result", compare.fields(**result), f"{compare.cid}.result")
    return judgement


def render(compare: Compare, judgement: dict, seals: dict, passes: dict,
           launchers: dict[str, str]) -> str:
    start = compare.start
    out = [f"# Design compare {compare.cid}", "",
           f"Mode {start['mode']}; input sha256 `{start['input_hash']}`; capability "
           f"**{judgement['capability']}** — {judgement['capability_reason']}", "",
           "| Candidate | Runtime | Profile | Model | Effort | Launcher | Memo |",
           "|---|---|---|---|---|---|---|"]
    for c in start["candidates"]:
        seal = seals.get(c["label"]) or {}
        memo = (f"`candidate-{c['label']}.md` sha256 `{seal['sha256'][:12]}`"
                f"{sealed_from(seal)}" if seal.get("sha256")
                else f"excluded: {seal.get('excluded') or 'not sealed'}")
        out.append(f"| {c['label']} | {c['runtime']} | {c['profile']} | {c['model']} | "
                   f"{c['effort']} | {launchers.get(c['label']) or 'unknown'} | {memo} |")
    arbiter = start["arbiter"]
    out += [""] + steering_lines(judgement)
    out += [f"Arbiter: {arbiter['runtime']}:{arbiter['profile']} ({arbiter['model']}, "
            f"{arbiter['effort']}), two passes, order swapped.", ""]
    for number in PASSES:
        entry = passes.get(number)
        out.append(f"## Arbiter pass {number}")
        out.append("")
        if not entry:
            out += ["Not run.", ""]
            continue
        letters = ", ".join(f"{k} = {v}" for k, v in (entry.get("letters") or {}).items())
        out += [f"Letters: {letters or 'unknown'}.", ""]
        path = compare.folder / f"arbiter-{number}.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        out += ["Output, verbatim:", "", text.rstrip("\n") if text.strip() else "(no output)", ""]
    rows = [("winner", [winner(passes.get(n) or {}) or "none" for n in PASSES]),
            ("confidence", [(passes.get(n) or {}).get("confidence") or "none" for n in PASSES])]
    final_winner = ("tie" if judgement["tie"] else
                    judgement["ranking"][0] if judgement["ranking"] else
                    "not arbitrated (one memo)" if judgement.get("unarbitrated") else "none")
    out += ["## Summary", ""]
    if judgement.get("none_meets_mandatory"):
        out += [f"**{NONE_MEETS}**", ""]
    out += ["| | Pass 1 | Pass 2 | Final |", "|---|---|---|---|"]
    out.append(f"| winner | {rows[0][1][0]} | {rows[0][1][1]} | {final_winner} |")
    out.append(f"| confidence | {rows[1][1][0]} | {rows[1][1][1]} | {judgement['confidence']} |")
    stable = judgement["stable"]
    out.append(f"| stable | | | {'yes' if stable else 'not arbitrated' if stable is None else 'no — arbiter unstable'} |")
    out.append("")
    if stable is False:
        out += ["**arbiter unstable**: the two passes disagree or one gave no verdict; read both "
                "outputs above before choosing.", ""]
    if judgement.get("unarbitrated"):
        out += [f"**not arbitrated (one memo)**: {', '.join(judgement['unarbitrated'])} is the "
                "only sealed memo; nothing checked it against the rubric, so it is not a winner.",
                ""]
    failed_rows = [f"{label}, {row}: {' / '.join(wording(item) for item in items)}"
                   for label, rows in judgement["failed_mandatory_by_row"].items()
                   for row, items in rows.items()]
    for title, items in (("Failed mandatory requirements", failed_rows),
                         ("Disagreements", judgement["disagreements"]),
                         ("Excluded", [f"{k}: {v}" for k, v in judgement["excluded_reasons"].items()]),
                         ("Notes", judgement["notes"])):
        if items:
            out += [f"### {title}", ""] + [f"- {item}" for item in items] + [""]
    return "\n".join(out)


# Investigation.

INVESTIGATION = "investigation"
INVESTIGATORS = (("claude", "deep-reasoning"), ("codex", "deep-reasoning"))
CRITIC_PROFILE = "critical-review"
CRITIQUE_TIMEOUT = 900.0
HEADING = re.compile(r"^(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
ANSWER = re.compile(r"^[ \t]*\*\*Answer:?\*\*:?[ \t]*(?P<answer>.*?)[ \t]*$", re.MULTILINE)
STATED_CONFIDENCE = re.compile(r"\*\*Confidence:?\*\*:?\s*\**\s*(high|medium|low)\b", re.IGNORECASE)
INVESTIGATOR_BRIEF = """# Investigation

You are one of two independent investigators. Each of you gets this same brief and the same
investigation brief below, in a fresh context, and answers its question. Afterwards each report
is critiqued by a reviewer who did not write it. You will not see the other report, and you must
not look for it: read only the source checkout and the brief, and never open other files under
the run's `compare/` directory.

## How to work

- The source checkout is read-only for you: never edit, create, stage or delete files in it.
- Your writable scratch directory is the directory that holds your report: write nothing
  outside it. Use it for reproductions and experiments.
- Write your report to the report path given at the end, and write it as you go: save the
  question, what is known and your hypotheses early and extend them, so a timeout still leaves
  usable evidence.
- Do not ask questions. Where the brief is unclear, state the assumption you made.
- Use exactly the report format below, including the `**Answer:**` and `**Confidence:**` line.

"""


def question_of(text: str) -> str:
    """The brief's question: its `## Question` section, or a first heading that states it."""
    if not text.strip():
        raise InputError("the investigation brief is empty; write the question and what is known")
    lines = text.splitlines()
    headings, fenced = [], False
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = None if fenced else HEADING.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    for position, (index, level, title) in enumerate(headings):
        if title.rstrip(":").strip().lower() != "question":
            continue
        end = next((i for i, lvl, _ in headings[position + 1:] if lvl <= level), len(lines))
        body = "\n".join(lines[index + 1:end]).strip()
        if not body:
            raise InputError("the brief's ## Question section is empty; state the question")
        return body
    if headings:
        title = headings[0][2]
        stated = re.match(r"(?i)question\s*:\s*(\S.*)$", title)
        if stated:
            return stated.group(1).strip()
        if title.endswith("?"):
            return title
    raise InputError("the brief has no ## Question section, and its first heading does not state "
                     "a question; add one")


def configure_investigation(overrides: list[str] | None,
                            checkout: Path) -> tuple[list[dict], dict[str, dict]]:
    """The two investigators and, per report label, the critic: the other vendor's runtime."""
    try:
        data, _ = xreview.models(checkout)
    except xreview.ReviewError as exc:
        raise InputError(str(exc)) from None
    if overrides and len(overrides) != 2:
        raise InputError("--candidate is given exactly twice, or not at all")
    specs = overrides or [f"{runtime}:{profile}" for runtime, profile in INVESTIGATORS]
    candidates = [resolve(spec, data) for spec in specs]
    if candidates[0]["runtime"] == candidates[1]["runtime"]:
        raise InputError("the investigators must be one per vendor (one claude, one codex): each "
                         "report is critiqued by the other vendor")
    ordered = [{"label": entry["runtime"], **entry} for entry in candidates]
    ordered = [{key: entry[key] for key in ("label", "runtime", "profile", "model", "effort")}
               for entry in ordered]
    critics = {}
    for entry, other in ((ordered[0], ordered[1]), (ordered[1], ordered[0])):
        critics[entry["label"]] = resolve(f"{other['runtime']}:{CRITIC_PROFILE}", data)
    return ordered, critics


def clean(value) -> str:
    return " ".join(str(value).split())


def parse_critique(text: str) -> dict:
    """The last fenced JSON block of a critique, checked."""
    blocks = JSON_BLOCK.findall(text)
    if not blocks:
        raise ValueError("no fenced JSON block")
    try:
        data = json.loads(blocks[-1])
    except ValueError as exc:
        raise ValueError(f"the JSON block does not parse: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("the JSON block is not an object")
    agreed, disputed, settles = (data.get(key) or [] for key in ("agreed", "disputed", "settles"))
    if not isinstance(agreed, list) or not all(isinstance(item, str) for item in agreed):
        raise ValueError("agreed must be a list of claims")
    if not isinstance(disputed, list) or not all(
            isinstance(item, dict) and isinstance(item.get("claim"), str) and item["claim"].strip()
            for item in disputed):
        raise ValueError("disputed must be a list of {claim, evidence_for, evidence_against}")
    if not isinstance(settles, list) or not all(
            isinstance(item, dict) and isinstance(item.get("claim"), str)
            and isinstance(item.get("observation"), str) for item in settles):
        raise ValueError("settles must be a list of {claim, observation}")
    if not isinstance(data.get("answer_disputed"), bool):
        raise ValueError("answer_disputed must be true or false")
    conflict = data.get("answers_conflict", False)  # not stated: no conflict claimed
    if not isinstance(conflict, bool):
        raise ValueError("answers_conflict must be true or false")
    return {"agreed": [clean(item) for item in agreed if item.strip()],
            "disputed": [{"claim": clean(item["claim"]),
                          "evidence_for": clean(item.get("evidence_for") or "(not given)"),
                          "evidence_against": clean(item.get("evidence_against") or "(not given)")}
                         for item in disputed],
            "settles": [{"claim": clean(item["claim"]), "observation": clean(item["observation"])}
                        for item in settles if item["observation"].strip()],
            "answer_disputed": data["answer_disputed"], "answers_conflict": conflict}


def answer_of(report: str) -> str | None:
    """The report's `**Answer:**` line, or None when it has none or an empty one."""
    match = ANSWER.search(report)
    if not match:
        return None
    answer = match.group("answer").split("**Confidence", 1)[0].strip(" \t·*-—–")
    return match.group(0).strip() if answer.casefold() not in EMPTY_ANSWERS else None


def stated_confidence(report: str) -> str | None:
    match = STATED_CONFIDENCE.search(report)
    return match.group(1).lower() if match else None


class Investigation(Compare):
    kind = INVESTIGATION
    candidate_role = "investigator"
    noun = "report"

    def __init__(self, run: Run, cid: str, start: dict, checkout: Path, input_text: str,
                 question: str, timeout: float, critique_timeout: float):
        super().__init__(run, cid, start, checkout, input_text, timeout, critique_timeout)
        self.question = question

    def candidate_brief(self) -> Path:
        path = self.folder / "brief-candidate.md"
        if not path.is_file():
            role = (HARNESS / "roles" / "investigator.md").read_text(encoding="utf-8")
            write(path, INVESTIGATOR_BRIEF + role.rstrip("\n") + "\n\n## Investigation brief\n\n"
                  f"Input sha256: {self.start['input_hash']}\n\n"
                  + self.input_text.rstrip("\n") + "\n")
        return path

    def critique_brief(self, label: str, report: str, other: str | None) -> Path:
        """The critique of `label`'s report: the template, the investigation brief, the other
        investigation's `**Answer:**` line only (`other`: its report, None when missing), and
        the report under review."""
        template = (HARNESS / "prompts" / "compare-critique.md").read_text(encoding="utf-8")
        if other is None:
            answer = "(none: the other investigation's report is missing)"
        else:
            answer = answer_of(other) or "(none: the other report has no **Answer:** line)"
        parts = [template.rstrip("\n"), "",
                 "## Investigation brief (the question and what is known)", "",
                 f"Input sha256: {self.start['input_hash']}", "", self.input_text.rstrip("\n"),
                 "", "## The other investigation's answer, for comparison", "", answer, "",
                 "## Report under review", "", report.rstrip("\n"), "", "## End of report", ""]
        path = self.folder / f"brief-critique-{label}.md"
        write(path, "\n".join(parts))
        return path

    def critiques(self, reports: dict[str, str]) -> dict:
        """One critique of each sealed report, by the other vendor; finished ones are reused."""
        labels = [c["label"] for c in self.start["candidates"] if c["label"] in reports]
        state = self.state()
        pending = [label for label in labels if f"critique-{label}" not in state["pass"]]
        for label in pending:
            slot = f"critique-{label}"
            if self.run.emit("compare.claim", self.fields(slot=slot, pid=os.getpid(),
                                                          claimed=time.time()),
                             f"{self.cid}.claim.{slot}"):
                others = [c["label"] for c in self.start["candidates"] if c["label"] != label]
                brief = self.critique_brief(label, reports[label],
                                            next((reports[o] for o in others if o in reports),
                                                 None))
                self.launch(slot, self.start["critics"][label], "critic", brief,
                            self.arbiter_timeout, candidate=False)
        for label in pending:
            slot = f"critique-{label}"
            launch = self.launch_of(slot)
            status = self.finish(launch, self.arbiter_timeout)
            self.record_critique(label, launch, status)
        return self.state()["pass"]

    def record_critique(self, label: str, launch: dict | None, status: str) -> None:
        text = ""
        if launch and launch.get("report"):
            try:
                text = Path(launch["report"]).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                text = ""
        critic = self.start["critics"][label]
        fields = self.fields(**{"pass": f"critique-{label}"}, report=label,
                             critic=critic["runtime"], status=status, sha256=None, valid=False,
                             error=None, agreed=[], disputed=[], settles=[],
                             answer_disputed=None, answers_conflict=None)
        if text.strip():
            write(self.folder / f"critique-{label}.md", text)
            fields["sha256"] = sha256_bytes(text.encode("utf-8"))
            try:
                fields.update(parse_critique(text), valid=True)
            except ValueError as exc:
                fields["error"] = str(exc)
        elif launch is None:
            fields["error"] = "claimed but never launched"
        elif not launch.get("dispatch"):
            fields["error"] = f"launch failed: {launch.get('error')}"
        else:
            fields["error"] = f"no output (dispatch {status})"
        self.run.emit("compare.pass", fields, f"{self.cid}.pass.critique-{label}")


def same_claim(first: str, second: str) -> bool:
    return clean(first).casefold() == clean(second).casefold()


def conclude_investigation(compare: Investigation, seals: dict, reports: dict[str, str],
                           passes: dict, launchers: dict[str, str]) -> dict:
    """Merge the two critiques into the disagreement map; no third model is asked."""
    start = compare.start
    labels = [c["label"] for c in start["candidates"]]
    excluded = [label for label in labels if label not in reports]
    notes = []
    cap, cap_reason = capability(launchers, start["candidates"])
    if cap != start.get("capability"):
        notes.append(f"capability was reserved as {start.get('capability')}, the launchers "
                     f"gave {cap}")
    agreed, disputed, settles, verdicts, disputed_by = [], [], [], [], []
    for label in labels:
        if label in excluded:
            notes.append(f"{label}'s report is missing: nothing of it was critiqued")
            continue
        entry = passes.get(f"critique-{label}") or {}
        critic = start["critics"][label]["runtime"]
        if not entry.get("valid"):
            notes.append(f"the critique of {label}'s report gave no valid result: "
                         f"{entry.get('error') or 'not recorded'}")
            continue
        conflict = entry.get("answers_conflict") is True
        verdicts.append(bool(entry["answer_disputed"]) or conflict)
        if entry["answer_disputed"]:
            disputed_by.append(f"the {critic} critique of {label}'s report")
        elif conflict:
            disputed_by.append(f"the {critic} critique of {label}'s report (the two answers "
                               "conflict)")
        agreed += [{"claim": claim, "report": label, "critic": critic}
                   for claim in entry["agreed"]]
        own = [{**item, "report": label, "critic": critic} for item in entry["settles"]]
        settles += own
        for item in entry["disputed"]:
            settle = next((s["observation"] for s in own if same_claim(s["claim"], item["claim"])),
                          None)
            disputed.append({**item, "report": label, "critic": critic, "settle": settle})
    # Whether the answer is disputed is known only when both critiques are valid: one
    # critique that accepts the answer says nothing about the report nobody critiqued.
    answer_disputed = any(verdicts) if len(verdicts) >= 2 else None
    stated = {label: stated_confidence(text) for label, text in reports.items()}
    unanswered = [label for label in labels
                  if label in reports and answer_of(reports[label]) is None]
    if any(verdicts):
        confidence, why = "low", "a critique disputes the answer"
    elif excluded:
        confidence, why = "low", f"report missing: {', '.join(excluded)}"
    elif len(verdicts) < len(labels):
        confidence, why = "low", "a critique gave no valid result"
    elif unanswered:
        confidence, why = "low", f"no **Answer:** line in the report of {', '.join(unanswered)}"
    elif None in stated.values():
        confidence, why = "low", "a report states no confidence"
    else:
        confidence = min(stated.values(), key=CONFIDENCE.index)
        why = "the lower of the two reports' stated confidences"
    steered = steering(launchers, labels)
    judgement = {"compare": compare.cid, "kind": INVESTIGATION, "agreed": agreed,
                 "disputed": disputed, "settles": settles, "answer_disputed": answer_disputed,
                 "answer_disputed_by": disputed_by, "valid_critiques": len(verdicts),
                 "confidence": confidence,
                 "confidence_reason": why, "report_confidence": stated,
                 "input_hash": start["input_hash"], "capability": cap,
                 "capability_reason": cap_reason,
                 "capability_note": capability_note(start["candidates"]), "excluded": excluded,
                 "excluded_reasons": {label: (seals.get(label) or {}).get("excluded")
                                      or "not sealed" for label in excluded},
                 "user_steered": steered, "notes": notes}
    write(compare.folder / "judgement.json", json.dumps(judgement, indent=1) + "\n")
    write(compare.folder / "disagreements.md", render_map(compare, judgement))
    write(compare.folder / "comparison.md",
          render_investigation(compare, judgement, seals, passes, launchers))
    result = {"answer_disputed": answer_disputed, "confidence": confidence, "excluded": excluded}
    result["user_steered"] = steered
    compare.run.emit("compare.result", compare.fields(**result), f"{compare.cid}.result")
    return judgement


def answer_line(judgement: dict) -> str:
    disputed = judgement["answer_disputed"]
    if disputed:
        return (f"**The answer is disputed** by {' and '.join(judgement['answer_disputed_by'])}: "
                "confidence stays low until the settling observations below are made.")
    if disputed is None:
        valid = judgement.get("valid_critiques", 0)
        line = (f"Whether the answer is disputed is unknown: only {valid} valid critique(s), "
                "both are needed.")
        if judgement["answer_disputed_by"]:
            line += (f" It is disputed by {' and '.join(judgement['answer_disputed_by'])}; "
                     "confidence stays low until the settling observations below are made.")
        return line
    return "Neither critique disputes the answer itself."


def render_map(compare: Investigation, judgement: dict) -> str:
    out = [f"# Disagreement map {compare.cid}", "", f"Question: {clean(compare.question)}", "",
           f"Input sha256 `{judgement['input_hash']}`; confidence **{judgement['confidence']}** "
           f"({judgement['confidence_reason']}).", "", answer_line(judgement), "",
           "## Agreed claims", "",
           "Claims of a report that the other vendor's critique accepts.", ""]
    out += [f"- {item['claim']} (in {item['report']}'s report, accepted by the {item['critic']} "
            "critique)" for item in judgement["agreed"]] or ["- none"]
    out += ["", "## Disputed claims", ""]
    if not judgement["disputed"]:
        out += ["- none", ""]
    for number, item in enumerate(judgement["disputed"], 1):
        out += [f"### {number}. {item['claim']}", "",
                f"- For ({item['report']}'s report): {item['evidence_for']}",
                f"- Against ({item['critic']} critique): {item['evidence_against']}",
                f"- Cheapest settling observation: {item['settle'] or 'none named'}", ""]
    unmatched = [s for s in judgement["settles"]
                 if not any(same_claim(s["claim"], d["claim"]) and s["report"] == d["report"]
                            for d in judgement["disputed"])]
    if unmatched:
        out += ["## Other settling observations", ""]
        out += [f"- {s['claim']}: {s['observation']} ({s['critic']} critique)" for s in unmatched]
        out.append("")
    for title, items in (("Excluded", [f"{k}: {v}" for k, v in
                                       judgement["excluded_reasons"].items()]),
                         ("Notes", judgement["notes"])):
        if items:
            out += [f"## {title}", ""] + [f"- {item}" for item in items] + [""]
    out += ["Both critiques, verbatim: `comparison.md`.", ""]
    return "\n".join(out)


def render_investigation(compare: Investigation, judgement: dict, seals: dict, passes: dict,
                         launchers: dict[str, str]) -> str:
    start = compare.start
    out = [f"# Investigation compare {compare.cid}", "",
           f"Input sha256 `{start['input_hash']}`; capability **{judgement['capability']}** — "
           f"{judgement['capability_reason']}", "",
           "| Investigator | Runtime | Profile | Model | Effort | Launcher | Report "
           "| Stated confidence |",
           "|---|---|---|---|---|---|---|---|"]
    for c in start["candidates"]:
        seal = seals.get(c["label"]) or {}
        report = (f"`candidate-{c['label']}.md` sha256 `{seal['sha256'][:12]}`{sealed_from(seal)}"
                  if seal.get("sha256") else f"excluded: {seal.get('excluded') or 'not sealed'}")
        stated = judgement["report_confidence"].get(c["label"]) or "unknown"
        out.append(f"| {c['label']} | {c['runtime']} | {c['profile']} | {c['model']} | "
                   f"{c['effort']} | {launchers.get(c['label']) or 'unknown'} | {report} | "
                   f"{stated} |")
    out.append("")
    out += steering_lines(judgement)
    for c in start["candidates"]:
        label = c["label"]
        critic = start["critics"][label]
        out += [f"## Critique of {label}'s report", "",
                f"By {critic['runtime']}:{critic['profile']} ({critic['model']}, "
                f"{critic['effort']}).", ""]
        entry = passes.get(f"critique-{label}")
        if label in judgement["excluded"]:
            out += ["Not run: the report is missing.", ""]
            continue
        if not entry:
            out += ["Not run.", ""]
            continue
        path = compare.folder / f"critique-{label}.md"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        out += ["Output, verbatim:", "", text.rstrip("\n") if text.strip() else "(no output)", ""]
        if not entry.get("valid"):
            out += [f"No valid result: {entry.get('error')}.", ""]
    disputed = judgement["answer_disputed"]
    out += ["## Summary", "", "| | Value |", "|---|---|",
            "| answer disputed | "
            f"{'yes' if disputed else 'unknown' if disputed is None else 'no'} |",
            f"| confidence | {judgement['confidence']} ({judgement['confidence_reason']}) |",
            f"| agreed claims | {len(judgement['agreed'])} |",
            f"| disputed claims | {len(judgement['disputed'])} |",
            f"| excluded | {', '.join(judgement['excluded']) or 'none'} |", "",
            "The merged map (agreed and disputed claims, both sides' evidence, settling "
            "observations): `disagreements.md`.", ""]
    if judgement["notes"]:
        out += ["### Notes", ""] + [f"- {note}" for note in judgement["notes"]] + [""]
    return "\n".join(out)


# Commands.

def remove_snapshots(run: Run, cid: str) -> list[str]:
    """After the result: remove the round's candidate snapshots through lanes' snapshot
    cleanup (the sealed memos and reports stay). Best effort: a snapshot that is not removed,
    or a cleanup that fails, is a note, never a failed compare."""
    launches = collect(run.events(), cid)["launch"]
    ours = {launch.get("dispatch") for slot, launch in launches.items()
            if str(slot).startswith("candidate-") and launch.get("dispatch")}
    try:
        rows = lanes.snapshot_cleanup(run.run, dispatches=sorted(ours))
    except Exception as exc:  # noqa: BLE001 - cleanup must never fail the compare
        return [f"snapshots not removed: {exc}"]
    notes = [f"snapshot of {row.get('dispatch')} not removed: {row.get('reason')}"
             for row in rows if row.get("dispatch") in ours and not row.get("removed")
             and row.get("reason") != "already removed"]
    if any(row.get("removed") for row in rows):
        regenerate_dashboard(run.run, run.primary)
    return notes


def snapshots_after(run: Run, cid: str, keep: bool) -> str:
    """Clean up after the result unless --keep-snapshots; notes go to stderr."""
    if keep:
        return "kept"
    notes = remove_snapshots(run, cid)
    for note in notes:
        print(f"compare: {note}", file=sys.stderr)
    return "not all removed" if notes else "removed"


def launchers_of(state: dict, candidates: list[dict]) -> dict[str, str]:
    return {c["label"]: (state["launch"].get(f"candidate-{c['label']}") or {}).get("launcher")
            for c in candidates}


def read_input(path: str) -> tuple[bytes, str]:
    try:
        raw = Path(path).read_bytes()
        return raw, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise InputError(f"cannot read --input {path}: {exc}") from None


def checkout_of(path: str) -> Path:
    checkout = Path(path).resolve()
    if not checkout.is_dir():
        raise InputError(f"--checkout {path} is not a directory")
    return checkout


def resumed(run: Run, resume: str, kind: str, input_hash: str) -> tuple[str, dict]:
    """The reserved compare to continue; InputError for an unknown id, kind or input."""
    cid = task_state.identifier(resume)
    start = collect(run.events(), cid)["start"]
    if start is None:
        raise InputError(f"no compare {cid} in task {run.task}")
    if start.get("kind") != kind:
        raise InputError(f"compare {cid} is a {start.get('kind')} compare, not {kind}")
    if start.get("input_hash") != input_hash:
        raise InputError(f"the input differs from the one compare {cid} was reserved for "
                         f"(sha256 {start.get('input_hash')})")
    return cid, start


def reserve(run: Run, fields: dict) -> tuple[str, dict]:
    """Append the compare.start that reserves every slot of the round, before any launch."""
    cid = f"cmp-{secrets.token_hex(4)}"
    if not run.emit("compare.start", {"compare": cid, **fields}, f"{cid}.start"):
        raise CompareError(f"compare id {cid} is already taken; run again")
    return cid, collect(run.events(), cid)["start"]


def planned_capability(role: str, candidates: list[dict]) -> tuple[str, str]:
    herdr_on = os.environ.get("HERDR_ENV") == "1"
    planned = {c["label"]: lanes.decide(role, c["runtime"], "claude", 0, herdr=herdr_on,
                                        compare_candidate=True)["launcher"]
               for c in candidates}
    return capability(planned, candidates)


def design(args) -> int:
    for name in ("timeout", "arbiter_timeout"):
        if getattr(args, name) <= 0:
            raise InputError(f"--{name.replace('_', '-')} must be positive")
    raw, input_text = read_input(args.input)
    check_requirements(input_text)
    input_hash = sha256_bytes(raw)
    checkout = checkout_of(args.checkout)
    task_state.identifier(args.task)
    run = Run(args.task)
    if args.resume:
        cid, start = resumed(run, args.resume, KIND, input_hash)
    else:
        candidates, arbiter = configure(args.mode, args.candidate, args.arbiter, checkout)
        cap, reason = planned_capability("candidate", candidates)
        cid, start = reserve(run, {"kind": KIND, "mode": args.mode, "input_hash": input_hash,
                                   "candidates": candidates, "arbiter": arbiter,
                                   "capability": cap, "capability_reason": reason})
    compare = Compare(run, cid, start, checkout, input_text, args.timeout, args.arbiter_timeout)
    copy = compare.folder / "input.md"
    if not copy.is_file():
        write(copy, input_text)
    seals = compare.candidates()
    memos = {label: compare.memo(label, seal) for label, seal in seals.items()
             if seal.get("sha256")}
    passes = compare.passes(memos) if len(memos) >= 2 else {}
    state = compare.state()
    judgement = conclude(compare, state["seal"], passes, launchers_of(state, start["candidates"]))
    snapshots = snapshots_after(run, cid, args.keep_snapshots)
    print(json.dumps({"compare": cid, "dir": str(compare.folder), "stable": judgement["stable"],
                      "ranking": judgement["ranking"], "tie": judgement["tie"],
                      "unarbitrated": judgement["unarbitrated"],
                      "excluded": judgement["excluded"], "snapshots": snapshots,
                      "judgement": str(compare.folder / "judgement.json"),
                      "comparison": str(compare.folder / "comparison.md")}))
    return 0


def investigation(args) -> int:
    for name in ("timeout", "critique_timeout"):
        if getattr(args, name) <= 0:
            raise InputError(f"--{name.replace('_', '-')} must be positive")
    raw, input_text = read_input(args.input)
    question = question_of(input_text)
    input_hash = sha256_bytes(raw)
    checkout = checkout_of(args.checkout)
    task_state.identifier(args.task)
    run = Run(args.task)
    if args.resume:
        cid, start = resumed(run, args.resume, INVESTIGATION, input_hash)
    else:
        candidates, critics = configure_investigation(args.candidate, checkout)
        cap, reason = planned_capability("investigator", candidates)
        cid, start = reserve(run, {"kind": INVESTIGATION, "input_hash": input_hash,
                                   "candidates": candidates, "critics": critics,
                                   "capability": cap, "capability_reason": reason})
    compare = Investigation(run, cid, start, checkout, input_text, question, args.timeout,
                            args.critique_timeout)
    copy = compare.folder / "input.md"
    if not copy.is_file():
        write(copy, input_text)
    seals = compare.candidates()
    reports = {label: compare.memo(label, seal) for label, seal in seals.items()
               if seal.get("sha256")}
    critiques = compare.critiques(reports)
    state = compare.state()
    judgement = conclude_investigation(compare, state["seal"], reports, critiques,
                                       launchers_of(state, start["candidates"]))
    snapshots = snapshots_after(run, cid, args.keep_snapshots)
    print(json.dumps({"compare": cid, "dir": str(compare.folder),
                      "answer_disputed": judgement["answer_disputed"],
                      "confidence": judgement["confidence"], "excluded": judgement["excluded"],
                      "snapshots": snapshots,
                      "judgement": str(compare.folder / "judgement.json"),
                      "disagreements": str(compare.folder / "disagreements.md"),
                      "comparison": str(compare.folder / "comparison.md")}))
    return 0


def summary(events: list[dict], cid: str) -> dict:
    state = collect(events, cid)
    start = state["start"]
    candidates = []
    for c in start.get("candidates") or []:
        slot = f"candidate-{c['label']}"
        seal, launch = state["seal"].get(c["label"]), state["launch"].get(slot)
        if seal:
            current = "sealed" if seal.get("sha256") else "excluded"
        elif launch:
            current = "ended" if launch.get("dispatch") in state["ends"] else "running"
        else:
            current = "claimed" if slot in state["claim"] else "pending"
        candidates.append({"label": c["label"], "state": current,
                           "dispatch": (launch or {}).get("dispatch"),
                           "sha256": (seal or {}).get("sha256"),
                           "excluded": (seal or {}).get("excluded")})
    investigation = start.get("kind") == INVESTIGATION
    slots = ([(f"critique-{c['label']}", f"critique-{c['label']}") for c in candidates]
             if investigation else [(number, f"arbiter-{number}") for number in PASSES])
    passes = []
    for key, slot in slots:
        entry, launch = state["pass"].get(key), state["launch"].get(slot)
        current = ("done" if entry and entry.get("valid") else "invalid" if entry
                   else "running" if launch else "claimed" if slot in state["claim"]
                   else "pending")
        item = {"pass": key, "state": current, "dispatch": (launch or {}).get("dispatch")}
        if investigation:
            item["answer_disputed"] = (entry or {}).get("answer_disputed")
        else:
            item["winner"] = winner(entry or {})
        passes.append(item)
    result = state["result"]
    if result:
        phase = "done"
    elif candidates and all(c["state"] in ("sealed", "excluded") for c in candidates):
        phase = "critiques" if investigation else "arbiter"
    elif any(c["state"] != "pending" for c in candidates):
        phase = "candidates"
    else:
        phase = "reserved"
    out = {"compare": cid, "kind": start.get("kind"), "mode": start.get("mode"),
           "input_hash": start.get("input_hash"), "capability": start.get("capability"),
           "phase": phase, "candidates": candidates, "passes": passes,
           "stable": (result or {}).get("stable"), "excluded": (result or {}).get("excluded")}
    if result:
        out.update({key: result[key] for key in ("ranking", "tie", "unarbitrated",
                                                 "answer_disputed", "confidence",
                                                 "user_steered", "note")
                    if key in result})
    return out


def status(args) -> int:
    run = Run(args.task)
    events = run.events()
    ids = list(dict.fromkeys(e.get("compare") for e in events
                             if e.get("type") == "compare.start" and isinstance(e.get("compare"), str)))
    if args.compare:
        if args.compare not in ids:
            raise InputError(f"no compare {args.compare} in task {args.task}")
        print(json.dumps(summary(events, args.compare)))
    else:
        print(json.dumps([summary(events, cid) for cid in ids]))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    sub = commands.add_parser("design", help="two approach memos, then two arbiter passes")
    sub.add_argument("--task", required=True)
    sub.add_argument("--input", required=True, help="the frozen interview record (Markdown)")
    sub.add_argument("--checkout", required=True, help="the source checkout the memos are about")
    sub.add_argument("--mode", choices=tuple(DEFAULTS), default="standard")
    sub.add_argument("--candidate", action="append", metavar="RUNTIME:PROFILE",
                     help="override the competitors; given exactly twice")
    sub.add_argument("--arbiter", metavar="RUNTIME:PROFILE", help="override the arbiter")
    sub.add_argument("--resume", metavar="COMPARE_ID", help="continue a reserved compare")
    sub.add_argument("--timeout", type=float, default=CANDIDATE_TIMEOUT,
                     help=f"seconds per competitor (default {CANDIDATE_TIMEOUT:.0f})")
    sub.add_argument("--arbiter-timeout", type=float, default=ARBITER_TIMEOUT,
                     help=f"seconds per arbiter pass (default {ARBITER_TIMEOUT:.0f})")
    sub.add_argument("--keep-snapshots", action="store_true",
                     help="keep the candidates' snapshots after the result")
    sub = commands.add_parser("investigation",
                              help="two investigation reports, then a cross-critique of each")
    sub.add_argument("--task", required=True)
    sub.add_argument("--input", required=True, help="the investigation brief (Markdown)")
    sub.add_argument("--checkout", required=True, help="the source checkout to investigate")
    sub.add_argument("--candidate", action="append", metavar="RUNTIME:PROFILE",
                     help="override the investigators; given exactly twice, one per vendor")
    sub.add_argument("--resume", metavar="COMPARE_ID", help="continue a reserved compare")
    sub.add_argument("--timeout", type=float, default=CANDIDATE_TIMEOUT,
                     help=f"seconds per investigator (default {CANDIDATE_TIMEOUT:.0f})")
    sub.add_argument("--critique-timeout", type=float, default=CRITIQUE_TIMEOUT,
                     help=f"seconds per critique (default {CRITIQUE_TIMEOUT:.0f})")
    sub.add_argument("--keep-snapshots", action="store_true",
                     help="keep the investigators' snapshots after the result")
    sub = commands.add_parser("status", help="the state of the run's compares")
    sub.add_argument("--task", required=True)
    sub.add_argument("--compare", metavar="COMPARE_ID")
    args = parser.parse_args()
    try:
        command = {"design": design, "investigation": investigation, "status": status}
        return command[args.command](args)
    except (InputError, StateError) as exc:
        print(f"compare: {exc}", file=sys.stderr)
        return 2
    except (CompareError, lanes.LaneError, OSError) as exc:
        print(f"compare: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
