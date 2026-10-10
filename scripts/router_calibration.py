#!/usr/bin/env python3
"""Append explicit shadow-attempt evidence and report coverage, never route or fit models.

The 1.4 review-ledger contract is read directly without importing its implementation.
Process completion and xreview quota reservations are never acceptance evidence.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import unicodedata

try:
    import fcntl
except ImportError:
    fcntl = None

sys.dont_write_bytecode = True
import task_state
from xreview import SECRET_VALUE

LABELS = ("accepted", "rejected", "fixup", "censored", "incident")
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}\Z")
HASH = re.compile(r"[a-fA-F0-9]{64}\Z")
TUPLE_KEYS = ("runtime", "model", "effort")
LINK_KEYS = ("decision_id", "attempt_id", "root_attempt_id", "parent_attempt_id",
             "profile", "tier", "policy_hash", "data_hash")


class CalibrationError(ValueError):
    """Unverifiable evidence or an inconsistent append."""


def identity(value, name):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise CalibrationError(f"{name} must be a bounded identifier")
    return value


def tuple_value(value):
    if not isinstance(value, dict) or set(value) != set(TUPLE_KEYS):
        raise CalibrationError("tuple needs exactly runtime, model, effort")
    return {key: identity(value[key], key) for key in TUPLE_KEYS}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(",", ":")).encode()).hexdigest()


def _semantic(value):
    value = {k: v for k, v in value.items() if k != "at"}
    if isinstance(value.get("review"), dict):
        value["review"] = {k: v for k, v in value["review"].items() if k != "ledger_sha256"}
    return value


def run_path(run):
    path = Path(run)
    if path.is_symlink():
        raise CalibrationError("run must not be a symlink")
    path = path.resolve(strict=True)
    if not path.is_dir():
        raise CalibrationError("run must be a directory")
    return path


def read_ledger(path):
    """Read a regular file without following its final symlink; no report prose."""
    path = Path(path)
    if path.is_symlink():
        raise CalibrationError("ledger must not be a symlink")
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return [], 0, None
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise CalibrationError("ledger must be a regular file")
        raw = stream.read()
    events, bad = task_state.parse_events(raw.decode("utf-8", errors="replace"))
    return events, bad, {"path": str(path), "sha256": hashlib.sha256(raw).hexdigest(),
                         "bytes": len(raw), "records": len(events), "invalid_lines": bad}


def review_snapshot(run, reference):
    """Verify the computed verdict of an exact 1.4 review attempt, not a reservation."""
    required = {"attempt_id", "milestone", "round", "report", "verdict"}
    if not isinstance(reference, dict) or set(reference) != required:
        raise CalibrationError("review needs attempt_id, milestone, round, report, verdict")
    identity(reference["attempt_id"], "review attempt_id")
    if any(not isinstance(reference[k], str) or not reference[k].strip()
           for k in ("milestone", "report")):
        raise CalibrationError("review milestone/report must be nonempty strings")
    if type(reference["round"]) is not int or reference["round"] not in (1, 2):
        raise CalibrationError("review round must be 1 or 2")
    if reference["verdict"] not in ("pass", "block", "needs_triage", "unknown"):
        raise CalibrationError("unknown review verdict")
    records, bad, source = read_ledger(run_path(run) / "review-ledger.jsonl")
    if bad or source is None:
        raise CalibrationError("authoritative 1.4 review ledger missing or invalid")
    if any(r.get("type") not in ("review", "review_result", "finding", "triage", "outcome") for r in records):
        raise CalibrationError("not an authoritative 1.4 review ledger")
    reviews = [(i, r) for i, r in enumerate(records) if r.get("type") == "review"
               and r.get("attempt_id") == reference["attempt_id"]]
    if len(reviews) != 1:
        raise CalibrationError("review attempt must exist exactly once")
    index, review = reviews[0]
    if type(review.get("round")) is not int:
        raise CalibrationError("invalid authoritative review round")
    for key in ("milestone", "round", "report"):
        if reference[key] != review.get(key):
            raise CalibrationError(f"review {key} identity mismatch")
    results = [(i, r) for i, r in enumerate(records) if r.get("type") == "review_result"
               and r.get("attempt_id") == reference["attempt_id"]]
    if not results or results[-1][0] <= index:
        raise CalibrationError("review reservation has no later result")
    result_index, result = results[-1]
    status = result.get("status")
    if status not in ("complete", "failed"):
        raise CalibrationError("review result has no terminal status")
    reason = result.get("reason")
    verdict = "unknown"
    if isinstance(reason, str):
        # Later text may say the reviewer's opposite verdict was overridden.
        match = re.match(r"^VERDICT: (pass|block) \(computed from matrix\)(?:;|$)", reason)
        if match:
            verdict = match[1]
        elif reason.startswith("VERDICT: needs triage"):
            verdict = "needs_triage"
    if verdict != reference["verdict"]:
        raise CalibrationError("computed review verdict identity mismatch")
    return {**reference, "status": status, "ledger_sha256": source["sha256"],
            "review_record_hash": digest(review), "result_record_hash": digest(result),
            "review_row": index + 1, "result_row": result_index + 1}


def _attempts(events):
    """Root's enriched start+shadow pair is sufficient; helper links are optional."""
    starts = {}
    for event in events:
        if event.get("type") == "dispatch.start" and isinstance(event.get("dispatch"), str):
            key = event["dispatch"]
            if key in starts and starts[key] != event:
                raise CalibrationError("conflicting dispatch starts")
            starts[key] = event
    attempts = {}
    for dispatch, start in starts.items():
        shadows = [e for e in events if e.get("type") == "router.shadow"
                   and e.get("dispatch") == dispatch]
        links = [e for e in events if e.get("type") == "router.attempt"
                 and e.get("dispatch") == dispatch]
        joined = {**start, **(shadows[-1] if shadows else {}), **(links[-1] if links else {})}
        key = joined.get("attempt_id") or dispatch
        if key in attempts:
            raise CalibrationError("attempt ID belongs to multiple dispatches")
        actual = {k: start.get(k) for k in TUPLE_KEYS}
        for evidence in shadows + links:
            if evidence.get("actual") != actual or (
                    "selected_tuple" in evidence and evidence["selected_tuple"] != actual):
                raise CalibrationError("shadow actual tuple contradicts dispatch start")
            for field in LINK_KEYS:
                if start.get(field) is not None and evidence.get(field) is not None and (
                        start[field] != evidence[field]):
                    raise CalibrationError(f"dispatch/shadow {field} mismatch")
        attempts[key] = {**joined, "dispatch": dispatch, "attempt_id": key, "actual": actual,
                         "root_attempt_id": joined.get("root_attempt_id") or key,
                         "parent_attempt_id": joined.get("parent_attempt_id"),
                         "linked": bool(shadows or links)}
    return attempts


def _validated_attempt(attempts, attempt_id):
    identity(attempt_id, "attempt_id")
    if attempt_id not in attempts:
        raise CalibrationError("unknown attempt/dispatch")
    attempt = attempts[attempt_id]
    if not attempt["linked"]:
        raise CalibrationError("attempt has no shadow-decision link")
    tuple_value(attempt["actual"])
    identity(attempt.get("decision_id"), "decision_id")
    identity(attempt.get("profile"), "profile")
    if type(attempt.get("tier")) is not int or attempt["tier"] not in range(4):
        raise CalibrationError("attempt tier must be 0 through 3")
    for field in ("policy_hash", "data_hash"):
        if not isinstance(attempt.get(field), str) or not HASH.fullmatch(attempt[field]):
            raise CalibrationError(f"{field} must be a SHA-256")
    root = identity(attempt["root_attempt_id"], "root_attempt_id")
    if root not in attempts:
        raise CalibrationError("root attempt does not exist")
    seen, node = set(), attempt_id
    while node is not None:
        if node in seen or node not in attempts:
            raise CalibrationError("attempt lineage is cyclic or missing")
        seen.add(node)
        parent = attempts[node].get("parent_attempt_id")
        if parent is None:
            if node != root:
                raise CalibrationError("root does not match the parent chain")
            break
        identity(parent, "parent_attempt_id")
        if attempts[node]["root_attempt_id"] != root:
            raise CalibrationError("inconsistent root lineage")
        node = parent
    return attempt


def _validate_end(events, attempt):
    ends = [e for e in events if e.get("type") == "dispatch.end" and e.get("dispatch") == attempt["dispatch"]]
    if len(ends) > 1:
        raise CalibrationError("ambiguous duplicate dispatch ends")
    if ends:
        for key in LINK_KEYS + ("actual", "selected_tuple"):
            expected = attempt["actual"] if key == "selected_tuple" else attempt.get(key)
            if ends[0].get(key) is not None and ends[0][key] != expected:
                raise CalibrationError("dispatch end contradicts attempt provenance")


def _append(run, kind, event_id, build):
    """Validate and append under the events writer's exclusive-file-lock semantics."""
    if fcntl is None:
        raise CalibrationError("safe append requires POSIX file locking")
    run = run_path(run)
    path = run / "events.jsonl"
    if path.is_symlink():
        raise CalibrationError("events ledger must not be a symlink")
    fd = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(fd, "r+b") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise CalibrationError("events ledger must be a regular file")
        fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            raw = stream.read()
            events, bad = task_state.parse_events(raw.decode("utf-8", errors="replace"))
            if bad:
                raise CalibrationError("cannot append calibration evidence to malformed events")
            fields = build(events)
            event_id = event_id or kind + ":" + digest(_semantic(fields))
            identity(event_id, "event_id")
            record = task_state.event_record(kind, json.dumps(fields, allow_nan=False), event_id)
            prior = [e for e in events if e.get("id") == event_id]
            if prior:
                if len(prior) != 1 or _semantic(prior[0]) != _semantic(record):
                    raise CalibrationError("event ID reused with conflicting evidence")
                return False
            stream.seek(0, os.SEEK_END)
            if raw and not raw.endswith(b"\n"):
                stream.write(b"\n")
            stream.write((json.dumps(record, allow_nan=False) + "\n").encode())
            stream.flush()
            return True
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def link_attempt(run, dispatch, decision_id, *, root_attempt_id=None,
                 parent_attempt_id=None, event_id=None):
    identity(dispatch, "dispatch")
    identity(decision_id, "decision_id")

    def build(events):
        attempts = _attempts(events)
        starts = [a for a in attempts.values() if a["dispatch"] == dispatch]
        if len(starts) != 1:
            raise CalibrationError("dispatch start does not exist")
        attempt = starts[0]
        shadows = [e for e in events if e.get("type") == "router.shadow"
                   and e.get("decision_id") == decision_id and e.get("dispatch") in (None, dispatch)]
        if not shadows:
            raise CalibrationError("shadow decision does not exist")
        shadow = next((e for e in reversed(shadows) if e.get("dispatch") == dispatch), shadows[-1])
        if shadow.get("actual") != attempt["actual"]:
            raise CalibrationError("decision actual tuple does not match dispatch")
        fields = {k: shadow.get(k) for k in LINK_KEYS}
        fields.update(dispatch=dispatch, decision_id=decision_id,
                      attempt_id=attempt["attempt_id"], actual=attempt["actual"],
                      selected_tuple=attempt["actual"])
        fields["root_attempt_id"] = root_attempt_id or attempt["root_attempt_id"]
        fields["parent_attempt_id"] = parent_attempt_id if parent_attempt_id is not None else attempt["parent_attempt_id"]
        declared = [e for e in events if e.get("dispatch") == dispatch
                    and e.get("type") in ("dispatch.start", "router.shadow", "router.attempt")]
        for key in LINK_KEYS:
            if any(e.get(key) is not None and fields[key] != e[key] for e in declared):
                raise CalibrationError(f"link {key} contradicts dispatch")
        candidate = {**attempt, **fields, "linked": True}
        _validated_attempt({**attempts, candidate["attempt_id"]: candidate}, candidate["attempt_id"])
        return fields

    return _append(run, "router.attempt", event_id, build)


def label_attempt(run, attempt_id, status, *, authority, criteria_id, revision,
                  review=None, event_id=None):
    if status not in LABELS:
        raise CalibrationError("invalid explicit label status")
    for value, name in ((authority, "authority"), (criteria_id, "criteria_id"), (revision, "revision")):
        identity(value, name)

    def build(events):
        attempt = _validated_attempt(_attempts(events), attempt_id)
        _validate_end(events, attempt)
        snapshot = review_snapshot(run, review) if review is not None else None
        if status == "accepted" and (
                (snapshot is None and attempt["tier"] >= 2)
                or (snapshot is not None and (snapshot["status"] != "complete" or snapshot["verdict"] != "pass"))):
            raise CalibrationError("tier 2-3 acceptance requires an authoritative computed complete pass; supplied reviews must pass")
        return {"attempt_id": attempt_id, "dispatch": attempt["dispatch"], "status": status,
                "authority": authority, "criteria_id": criteria_id, "revision": revision,
                "review": snapshot}

    return _append(run, "router.label", event_id, build)


def link_fixup(run, parent_attempt_id, child_attempt_id, *, event_id=None):
    def build(events):
        attempts = _attempts(events)
        _validated_attempt(attempts, parent_attempt_id)
        child = _validated_attempt(attempts, child_attempt_id)
        if child["parent_attempt_id"] != parent_attempt_id:
            raise CalibrationError("fixup needs the child's explicit matching parent link")
        return {"parent_attempt_id": parent_attempt_id, "child_attempt_id": child_attempt_id,
                "root_attempt_id": child["root_attempt_id"]}

    return _append(run, "router.fixup", event_id, build)


def safe_text(value, cap=300):
    """Neutralize terminal/bidi controls before displaying logged model output."""
    if value is None:
        return "unknown"
    value = str(value)
    if SECRET_VALUE.search(value) or "PRIVATE KEY" in value:
        return "[redacted]"
    value = "".join((f"\\u{ord(c):04x}" if unicodedata.category(c) in ("Cc", "Cf") else c)
                    for c in value)
    return value if len(value) <= cap else value[:cap] + "…"


def show_tuple(value):
    if not isinstance(value, dict):
        return "none"
    return " ".join(safe_text(value.get(k), 80) for k in TUPLE_KEYS)


def classifier_label(event):
    classifier = event.get("classifier")
    if classifier is None:
        return ""
    if isinstance(classifier, dict) and classifier.get("status") == "manual_weights":
        return "manual weights; no classifier call"
    if isinstance(classifier, dict) and classifier.get("status") == "disabled":
        return "classifier disabled"
    return "UNVALIDATED classifier"


def shadow_text(event):
    recommendation = event.get("recommendation")
    chosen = "would pick " + show_tuple(recommendation) if isinstance(recommendation, dict) else "skipped"
    label = classifier_label(event)
    classifier = f" [{label}]" if label else ""
    return (f"⚖ shadow: {chosen}{classifier}; actual {show_tuple(event.get('actual'))}; "
            f"{safe_text(event.get('status'), 80)} because {safe_text(event.get('reason'))}")


def shadow_details(event):
    details = []
    for key in ("assumptions", "missing_data"):
        values = event.get(key)
        if values:
            details.append(key.replace("_", " ") + ": " + safe_text(values))
    details.append("decision " + safe_text(event.get("decision_id"), 160)
                   + "; dispatch " + safe_text(event.get("dispatch"), 160)
                   + "; attempt " + safe_text(event.get("attempt_id"), 160))
    details.append("policy " + safe_text(event.get("policy_hash"), 64)
                   + "; data " + safe_text(event.get("data_hash"), 64))
    return details


def _numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def calibration_report(run):
    """Coverage and descriptive sums only. No inference from process status or prose."""
    run = run_path(run)
    events, bad, provenance = read_ledger(run / "events.jsonl")
    reviews, review_bad, review_source = read_ledger(run / "review-ledger.jsonl")
    warnings = []
    if provenance is None:
        warnings.append("events ledger missing; counts are observed coverage, not historical activity")
    if bad or review_bad:
        warnings.append("malformed ledger lines excluded; outcome coverage is uncertain")
    ids = Counter(e["id"] for e in events if isinstance(e.get("id"), str))
    duplicate_ids = sum(n - 1 for n in ids.values())
    unique, seen = [], set()
    for e in events:
        key = e.get("id")
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        unique.append(e)
    if duplicate_ids:
        warnings.append("duplicate event IDs excluded; inspect conflicting copies before fitting")
    try:
        attempts = _attempts(unique)
    except CalibrationError:
        attempts = {}
        warnings.append("inconsistent dispatch/shadow identities; linked eligibility withheld")
    valid = {}
    for key in attempts:
        try:
            valid[key] = _validated_attempt(attempts, key)
        except CalibrationError:
            pass
    ends = {e.get("dispatch"): e for e in unique if e.get("type") == "dispatch.end"}
    end_counts = Counter(e.get("dispatch") for e in unique if e.get("type") == "dispatch.end")
    duplicate_ends = sum(n - 1 for n in end_counts.values())
    for key, attempt in list(valid.items()):
        end = ends.get(attempt["dispatch"])
        try:
            _validate_end(unique, attempt)
        except CalibrationError:
            valid.pop(key)
            warnings.append("ambiguous end identity/metadata excludes an attempt from outcome eligibility")
    labels, invalid_labels = defaultdict(list), 0
    for e in unique:
        if e.get("type") != "router.label":
            continue
        try:
            attempt = valid.get(e.get("attempt_id"))
            if attempt is None:
                raise CalibrationError("attempt provenance is incomplete or inconsistent")
            if e.get("status") not in LABELS:
                raise CalibrationError("invalid status")
            for key in ("authority", "criteria_id", "revision"):
                identity(e.get(key), key)
            snapshot = e.get("review")
            if snapshot is not None:
                current = review_snapshot(run, {k: snapshot[k] for k in
                                              ("attempt_id", "milestone", "round", "report", "verdict")})
                if any(current[k] != snapshot.get(k) for k in
                       ("review_record_hash", "result_record_hash", "status")):
                    raise CalibrationError("review snapshot changed")
            if e["status"] == "accepted" and (
                    (snapshot is None and attempt["tier"] >= 2)
                    or (snapshot is not None and (snapshot.get("verdict") != "pass" or snapshot.get("status") != "complete"))):
                raise CalibrationError("unverified acceptance")
            labels[e["attempt_id"]].append(e)
        except (CalibrationError, KeyError, TypeError):
            invalid_labels += 1
    statuses = Counter(e["status"] for values in labels.values() for e in values)
    terminal = {key: values[-1]["status"] for key, values in labels.items()}
    observed = {key: next((e["status"] for e in values if e["status"] in ("accepted", "rejected", "fixup")), None)
                for key, values in labels.items()}
    eligible = sum(value is not None for value in observed.values())
    groups = defaultdict(lambda: {"started_n": 0, "linked_n": 0, "ended_n": 0,
                                  "outcome_labeled_n": 0, "accepted_n": 0, "censored_n": 0})
    metrics = {key: {"observed_n": 0, "explicit_zero_n": 0, "sum_observed": None}
               for key in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
                           "duration_s", "cost_usd", "charged_cost_usd")}
    serving_statuses = Counter()
    serving_objects_n = resolved_model_n = resolved_effort_n = 0
    for key, attempt in attempts.items():
        group = groups[tuple(str(attempt["actual"].get(k) or "unknown") for k in TUPLE_KEYS)]
        group["started_n"] += 1
        group["linked_n"] += key in valid
        end = ends.get(attempt["dispatch"])
        group["ended_n"] += end is not None
        group["outcome_labeled_n"] += key in labels
        group["accepted_n"] += terminal.get(key) == "accepted"
        group["censored_n"] += end is None or terminal.get(key) == "censored"
        usage = end.get("usage") if end and isinstance(end.get("usage"), dict) else {}
        serving = end.get("serving_identity") if end else None
        if isinstance(serving, dict):
            serving_objects_n += 1
            status = serving.get("status")
            serving_statuses[status if status in ("single_observed", "mixed", "unverified") else "invalid"] += 1
            observed_models = serving.get("models_observed")
            if (status == "single_observed" and isinstance(observed_models, list)
                    and len(observed_models) == 1 and isinstance(observed_models[0], str)
                    and serving.get("resolved_model") == observed_models[0]):
                resolved_model_n += 1
            if isinstance(serving.get("resolved_effort"), str) and serving.get("effort_status") == "observed":
                resolved_effort_n += 1
        for field, row in metrics.items():
            value = usage.get(field)
            if _numeric(value):
                row["observed_n"] += 1
                row["explicit_zero_n"] += value == 0
                row["sum_observed"] = (row["sum_observed"] or 0) + value
    warnings += ["process done/cancelled and xreview reservations are not accepted/rejected labels",
                 "tuple groups are requested launch tuples, not resolved or calibrated serving tuples",
                 "single stream model observations do not prove canonical identity or exclude safeguard fallback; requested effort is not resolved effort",
                 "input_tokens already includes cached input in lanes telemetry; never add it again",
                 "reasoning/output/cache-write accounting and quota conversion may be incomplete",
                 "no fitted failure probability, rework regression, confidence interval or cost reserve"]
    if invalid_labels:
        warnings.append("invalid or stale review-linked labels excluded")
    operator_acceptance_n = sum(e["status"] == "accepted" and e.get("review") is None
                                for values in labels.values() for e in values)
    if operator_acceptance_n:
        warnings.append("tier 0-1 operator acceptance is explicit authority/criteria/revision evidence, not a panel pass")
    fixups = 0
    for e in unique:
        if e.get("type") == "router.fixup":
            child = valid.get(e.get("child_attempt_id"))
            if (child and e.get("parent_attempt_id") in valid
                    and child["parent_attempt_id"] == e.get("parent_attempt_id")
                    and child["root_attempt_id"] == e.get("root_attempt_id")):
                fixups += 1
    return {"schema_version": 1, "as_of": datetime.now(timezone.utc).isoformat(),
            "provenance": [p for p in (provenance, review_source) if p],
            "events_n": len(events), "duplicate_event_ids_n": duplicate_ids,
            "duplicate_dispatch_ends_n": duplicate_ends,
            "invalid_lines_n": bad + review_bad, "dispatch_starts_n": len(attempts),
            "linked_attempts_n": len(valid), "start_only_n": sum(a["dispatch"] not in ends for a in attempts.values()),
            "end_only_n": len(set(ends) - {a["dispatch"] for a in attempts.values()}),
            "process_status_counts": dict(Counter(e.get("status", "unknown") for e in ends.values())),
            "label_event_counts": dict(statuses), "outcome_labeled_attempts_n": len(labels),
            "eligible_outcome_n": eligible, "invalid_label_n": invalid_labels,
            "initial_explicit_outcome_counts": dict(Counter(v for v in observed.values() if v is not None)),
            "operator_accepted_labels_n": operator_acceptance_n,
            "review_reservations_n": sum(e.get("type") == "review" for e in reviews),
            "review_results_n": sum(e.get("type") == "review_result" for e in reviews),
            "review_linked_labels_n": sum(e.get("review") is not None for values in labels.values() for e in values),
            "fixup_links_n": fixups, "usage": metrics,
            "serving_identity": {"objects_n": serving_objects_n,
                                 "status_counts": dict(serving_statuses),
                                 "single_stream_model_n": resolved_model_n,
                                 "observed_effort_n": resolved_effort_n,
                                 "proven_serving_tuple_n": 0},
            "tuple_basis": "requested_launch",
            "tuple_coverage": [{**dict(zip(TUPLE_KEYS, key)), **value} for key, value in sorted(groups.items())],
            "estimates": {"failure_probability": None, "conditional_rework_cost": None,
                          "expected_total_cost": None, "confidence_interval": None, "reserve": None},
            "fit_ready": False, "warnings": warnings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("link", "label", "fixup", "report"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--run", type=Path, required=True)
        if name != "report":
            cmd.add_argument("--event-id")
        if name == "link":
            cmd.add_argument("--dispatch", required=True)
            cmd.add_argument("--decision-id", required=True)
            cmd.add_argument("--root-attempt-id")
            cmd.add_argument("--parent-attempt-id")
        elif name == "label":
            for field in ("attempt-id", "authority", "criteria-id", "revision"):
                cmd.add_argument("--" + field, required=True)
            cmd.add_argument("--status", choices=LABELS, required=True)
            cmd.add_argument("--review", type=json.loads)
        elif name == "fixup":
            cmd.add_argument("--parent-attempt-id", required=True)
            cmd.add_argument("--child-attempt-id", required=True)
        else:
            cmd.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "link":
            changed = link_attempt(args.run, args.dispatch, args.decision_id,
                                   root_attempt_id=args.root_attempt_id, parent_attempt_id=args.parent_attempt_id,
                                   event_id=args.event_id)
        elif args.command == "label":
            changed = label_attempt(args.run, args.attempt_id, args.status, authority=args.authority,
                                    criteria_id=args.criteria_id, revision=args.revision,
                                    review=args.review, event_id=args.event_id)
        elif args.command == "fixup":
            changed = link_fixup(args.run, args.parent_attempt_id, args.child_attempt_id, event_id=args.event_id)
        else:
            report = calibration_report(args.run)
            if args.json:
                print(json.dumps(report, indent=2, allow_nan=False))
            else:
                print(f"Linked attempts: {report['linked_attempts_n']}; explicit outcome N={report['eligible_outcome_n']}; fit ready: false")
                for warning in report["warnings"]:
                    print("- " + warning)
            return 0
        print("appended" if changed else "duplicate")
        return 0
    except (CalibrationError, OSError, ValueError, TypeError) as exc:
        print(f"router_calibration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
