#!/usr/bin/env python3
"""Send a diff to the other vendor's CLI for a report-only review.

  xreview.py --base REF --writer claude|codex --out REPORT [--tier N]
             [--spec FILE --section "Title, with comma" --section "Title B"]
             [--addressed FILE] [--run-dir RUN --milestone NAME --round 1|2]
             [--dry-run] [--timeout SEC]

The diff is `git diff REF` of the checkout containing the current directory, with
untracked files appended as new-file diffs. The prompt (`prompts/xreview.md`), the
profiles (`adapters/models.json`), the optional `scripts/tier.py` and the guard the Codex
reviewer carries (`scripts/guard.py`) belong to the core this script lives in, so a newer
harness can review an older lane. The context is written to `<REPORT>.context.md`; prompt
and context go to the reviewer on stdin. Claude-written code is reviewed by Codex and
Codex-written code by Claude, on `critical-review` for Tier 3 and `implementation` below.

Overlay file `models.json` (in $HARNESS_PROJECT, else <checkout>/harness/project, of the
reviewed checkout), optional: the same shape as the core's
{"<runtime>": {"<profile>": {"model", "effort"}}}, deep-merged over it, so a project can
change one model or effort, or add a profile, without restating the rest.
Review acceptance reads project.json.review (stage/invariants), recomputes labels and
verdicts, and records findings/backlog when --run-dir or --ledger supplies a run.

Exit codes: 0 pass, 1 block, 2 not reviewed (usage, configuration, nothing to review,
diff over the cap), 3 no verdict (timeout, reviewer failure, missing verdict),
4 needs orchestrator triage (unparsed/incomplete claims or uncertain prior identity).
Only exit 0 is an approval.
"""

import argparse
import json
import hashlib
import uuid
try:
    import fcntl
except ImportError:  # Windows: no advisory locks; the cap is checked without one
    fcntl = None
import os
from pathlib import Path
import re
import shlex
import shutil
import time
import signal
import subprocess
import sys

# Importing must not write __pycache__ into a read-only installed harness.
sys.dont_write_bytecode = True
import load_guard
from project import OverlayError, load as load_overlay, overlay_dir
from review_calibration import (review_config, parse_findings, compute, record_findings,
                                append_event, previous_blockers, finish_review, prior_ledger_findings)

HARNESS = Path(__file__).resolve().parents[1]
DIFF_CAP = 200 * 1024
REVIEWER = {"claude": "codex", "codex": "claude"}
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/\[\]-]*")
VERDICT = re.compile(r"[\s>*_`#-]*VERDICT:[\s*_`]*([A-Za-z]+)[\s*_`.]*")
HEADING = re.compile(r" {0,3}(#{1,6})[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*")
FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")


class ReviewError(Exception):
    """The review cannot run; nothing was sent to a reviewer."""


def git(root: Path, *args: str, ok: tuple[int, ...] = (0,)) -> bytes:
    result = subprocess.run(["git", *args], cwd=root, capture_output=True)
    if result.returncode not in ok:
        raise ReviewError(os.fsdecode(result.stderr).strip() or f"git {args[0]} failed")
    return result.stdout


def checkout() -> Path:
    return Path(os.fsdecode(git(Path.cwd(), "rev-parse", "--show-toplevel")).strip()).resolve()


def base_commit(root: Path, ref: str) -> str:
    if not ref or ref.startswith("-"):
        raise ReviewError(f"Unknown base ref: {ref}")
    result = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                            cwd=root, capture_output=True, text=True)
    if result.returncode:
        raise ReviewError(f"Unknown base ref: {ref}")
    return result.stdout.strip()


# What must never be sent to another vendor: secret-looking files, and secret-looking values
# anywhere in the context (the review is refused rather than redacted, so nothing leaks).
SECRET_FILE = re.compile(r"(^|/)([^/]*\.env(\.(?!(example|sample|template)$)[^/]+)?|[^/]*\.(pem|key|p12|pfx)|id_(rsa|ed25519|ecdsa)[^/]*"
                         r"|credentials[^/]*|[^/]*secrets?\.(json|ya?ml|toml))$", re.IGNORECASE)
SECRET_VALUE = re.compile(
    # A key header counts only with a base64 body on the next line (diff "+" prefix allowed):
    # test fixtures carry the header with a dummy body.
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----(?:\\n|\s)*[+-]?\s*[A-Za-z0-9+/=]{40,}|\bAKIA[0-9A-Z]{16}\b|\bsk-(?:or-v1-|ant-|proj-)?[A-Za-z0-9_-]{32,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bxox[abpr]-[A-Za-z0-9-]{10,}|\bAIza[0-9A-Za-z_-]{35}\b"
    r"|\bsk_live_[0-9A-Za-z]{24,}")


def secret_location(text: str) -> str | None:
    """`path:line` (from the nearest diff header) of the first secret-looking value."""
    path = "context"
    lines = text.splitlines()
    for number, line in enumerate(lines, 1):
        if line.startswith("+++ b/"):
            path = line[6:]
        # Two lines at once, so a key header is weighed together with its body line.
        if SECRET_VALUE.search(line + "\n" + (lines[number] if number < len(lines) else "")):
            if SECRET_VALUE.search(line) or "PRIVATE KEY" in line:
                return f"{path} (context line {number})"
    return None


def changes(root: Path, base: str, paths: tuple[str, ...] = ()) -> tuple[list[str], bytes, list[str]]:
    """Changed paths, a plain-text patch, and untracked files withheld as possible secrets.

    `paths` (git pathspecs) limit the review to one part of a change too large for one pass.
    """
    options = ("--no-ext-diff", "--no-textconv", "--no-color")
    tracked = git(root, "diff", "--name-only", "--no-renames", "-z", base, "--", *paths).split(b"\0")
    untracked = git(root, "ls-files", "--others", "--exclude-standard", "-z", "--", *paths).split(b"\0")
    # Tracked, deleted or untracked: a secret-looking file never enters the patch.
    skipped = sorted({os.fsdecode(n) for n in (*tracked, *untracked) if n and SECRET_FILE.search(os.fsdecode(n))})
    untracked = [n for n in untracked if n and os.fsdecode(n) not in skipped]
    exclude = [f":(exclude,literal){name}" for name in skipped]
    patch = [git(root, "diff", *options, base, "--", *(paths or (".",)), *exclude)]
    for name in filter(None, untracked):
        # --no-index exits 1 when the files differ, which is always the case here.
        patch.append(git(root, "diff", "--no-index", *options, "--", "/dev/null",
                         os.fsdecode(name), ok=(0, 1)))
    names = sorted({os.fsdecode(name) for name in (*tracked, *untracked) if name})
    return names, b"".join(patch), skipped


def tier_of(root: Path, ref: str, explicit: int | None) -> tuple[int, str, list[str]]:
    """Tier, its source and the Tier-3 lines tier.py gave as reasons."""
    if explicit is not None:
        return explicit, "--tier", []
    script = HARNESS / "scripts/tier.py"
    if not script.is_file():
        return 2, "default: no tier.py", []
    result = subprocess.run([sys.executable, str(script), "--base", ref, "--json"],
                            cwd=root, capture_output=True, text=True)
    try:
        data = json.loads(result.stdout) if not result.returncode else None
    except ValueError:
        data = None
    tier = data.get("tier") if isinstance(data, dict) else None
    if type(tier) is not int or not 0 <= tier <= 3:
        detail = result.stderr.strip() or result.stdout.strip()[:200]
        raise ReviewError(f"tier.py gave no tier (exit {result.returncode}: {detail}); pass --tier")
    reasons = data.get("reasons")
    lines = []
    for reason in reasons if isinstance(reasons, list) else []:
        if isinstance(reason, dict) and reason.get("tier") == 3 and reason.get("path"):
            lines.append(f"{reason['path']} — {reason.get('reason') or 'Tier 3'}")
        elif isinstance(reason, str) and tier == 3:
            lines.append(reason)
    return tier, "tier.py", lines


def merged(base: dict, override: dict) -> dict:
    """`override` deep-merged over `base`: nested objects merge, any other value replaces."""
    result = dict(base)
    for key, value in override.items():
        result[key] = (merged(result[key], value)
                       if isinstance(value, dict) and isinstance(result.get(key), dict) else value)
    return result


def models(root: Path) -> tuple[dict, str]:
    """The core's profiles with the reviewed checkout's overlay models.json over them."""
    path = HARNESS / "adapters/models.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReviewError(f"Cannot read {path}: {exc}") from None
    if not isinstance(data, dict):
        raise ReviewError(f"{path} must hold a JSON object")
    overlay = overlay_dir(root) / "models.json"
    if not overlay.is_file():
        return data, str(path)
    try:
        extra = load_overlay(root, "models.json", {})
    except OverlayError as exc:
        raise ReviewError(f"Cannot use {exc}") from None
    return merged(data, extra), f"{path} + {overlay}"


def profile(runtime: str, tier: int, single_vendor: bool = False,
            root: Path | None = None) -> tuple[str, str, str]:
    # Same vendor reviewing its own code: a different (stronger) profile at least.
    name = "critical-review" if tier == 3 or single_vendor else "implementation"
    data, path = models(root or checkout())
    profiles = data.get(runtime)
    if not isinstance(profiles, dict):
        raise ReviewError(f"{path} has no profiles for the {runtime} reviewer")
    entry = profiles.get(name)
    if not isinstance(entry, dict):
        known = ", ".join(sorted(profiles)) or "none"
        raise ReviewError(f"{path} has no '{name}' profile for {runtime} (has: {known})")
    model, effort = entry.get("model"), entry.get("effort")
    if not all(isinstance(value, str) and NAME.fullmatch(value) for value in (model, effort)):
        raise ReviewError(f"{path}: {runtime}.{name} needs a model and an effort")
    return name, model, effort


def spec_sections(text: str, titles: list[str]) -> str:
    """Each titled section up to the next heading of the same or a higher level."""
    lines = text.splitlines()
    heads, fence = [], None
    for index, line in enumerate(lines):
        marker = FENCE.match(line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence):
                fence = None
        elif fence is None and (head := HEADING.fullmatch(line)):
            heads.append((index, len(head.group(1)), " ".join(head.group(2).split()).casefold()))
    ranges = set()
    for wanted in titles:
        key = " ".join(wanted.split()).casefold()
        # An exact title wins; otherwise "Tiers" names "Tiers (`tiers.json`)".
        found = [h for h in heads if h[2] == key] or [
            h for h in heads if h[2].startswith(key) and not h[2][len(key)].isalnum()]
        if not found:
            raise ReviewError(f"Spec section not found: {wanted!r}")
        for index, level, _ in found:
            end = next((i for i, other, _ in heads if i > index and other <= level), len(lines))
            ranges.add((index, end))
    picked = []
    for start, end in sorted(ranges):
        if not picked or end > picked[-1][1]:  # skip a section nested in the previous one
            picked.append((start, end))
    return "\n\n".join("\n".join(lines[start:end]).strip("\n") for start, end in picked)


def read(path: str, what: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ReviewError(f"Cannot read {what} {path}: {exc}") from None


def block(title: str, body: str) -> str:
    if not body.endswith("\n"):
        body += "\n"
    return f"## {title}\n\n{body}\n"


def context(args, base: str, names: list[str], patch: str, tier: tuple[int, str, list[str]],
            reviewer: str, profile_name: str) -> str:
    level, source, tier3 = tier
    parts = [
        "# Cross-vendor review context\n\n"
        f"- Base: {args.base} ({base})\n"
        f"- Writer: {args.writer}; reviewer: {reviewer} on the {profile_name} profile"
        + (" (single-vendor: same vendor, stronger profile — not an independent vendor check)"
           if reviewer == args.writer else "") + "\n"
        f"- Tier: {level} ({source})\n\n",
        block("Changed files", "".join(f"- {name}\n" for name in names)),
    ]
    if tier3:
        parts.append(block("Tier-3 paths", "".join(f"- {line}\n" for line in tier3)))
    try:
        policy = review_config(checkout())
    except OverlayError as exc:
        raise ReviewError(str(exc)) from None
    parts.append(block("Review policy from overlay", json.dumps(policy, indent=2)))
    parts.append(block("Review round", str(args.round)))
    if args.spec:
        titles = list(getattr(args, "section", None) or [])
        titles += [title.strip() for title in (args.sections or "").split(",") if title.strip()]
        if not titles:
            raise ReviewError("--sections names no section")
        parts.append(block(f"Spec sections ({args.spec})",
                           spec_sections(read(args.spec, "spec"), titles)))
    if args.addressed:
        parts.append(block("Already addressed — do not repeat", read(args.addressed, "addressed findings")))
    run_dir = getattr(args, 'run_dir', None) or (Path(args.ledger).resolve().parent if getattr(args, 'ledger', None) else None)
    if args.round == 2 and run_dir:
        prior = [{key: finding.get(key) for key in ('id', 'location', 'defect', 'likelihood', 'impact',
                 'reachable_path', 'scenario', 'evidence', 'workaround', 'invariant')}
                 for finding in prior_ledger_findings(Path(run_dir), args.milestone)]
        if prior:
            parts.append(block('Prior blockers after orchestrator triage', json.dumps(prior, indent=2)))
    # A fence longer than any backtick run in the patch cannot be closed by the patch.
    fence = "`" * max(3, 1 + max(map(len, re.findall(r"`+", patch)), default=0))
    patch = patch.rstrip("\n")
    parts.append(block("Diff", f"{fence}diff\n{patch}\n{fence}"))
    return "".join(parts)


def command(reviewer: str, model: str, effort: str, root: Path, report: Path) -> tuple[list[str], dict]:
    if reviewer == "codex":
        # Project hooks do not load in an untrusted worktree: carry the guard explicitly.
        guard = "'" + str(HARNESS / "scripts/guard.py").replace("'", "'\\''") + "'"
        hooks = ('hooks.PreToolUse=[{matcher="Bash|apply_patch|Edit|Write",hooks=[{type="command",'
                 f'command="python3 {guard} --runtime codex || true",timeout=10}}]}}]')
        return ["codex", "exec", "--sandbox", "read-only", "-m", model,
                "-c", f"model_reasoning_effort={effort}", "-c", hooks, "-C", str(root),
                "--output-last-message", str(report), "-"], {}
    return ["claude", "-p", "--model", model, "--output-format", "text", "--permission-mode", "plan",
            "--allowedTools", "Read,Grep,Glob", "--disallowedTools", "Bash,Edit,Write,NotebookEdit"], \
        {"CLAUDE_CODE_EFFORT_LEVEL": effort}


def run(argv: list[str], env: dict, payload: str, root: Path, report: Path, timeout: float) -> int | None:
    """The reviewer's exit code, or None after a timeout."""
    report.unlink(missing_ok=True)  # a stale report must never pass for this round's verdict
    Path(f"{report}.calibration.md").unlink(missing_ok=True)
    capture = argv[0] == "claude"
    try:
        proc = subprocess.Popen(argv, cwd=root, env={**os.environ, **env}, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                                start_new_session=True)
    except OSError as exc:
        raise ReviewError(f"Cannot start {argv[0]}: {exc}") from None
    try:
        out, _ = proc.communicate(payload.encode("utf-8"), timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            if hasattr(os, "killpg"):
                os.killpg(proc.pid, signal.SIGKILL)  # the CLI's own children too
            else:
                proc.kill()
        except ProcessLookupError:
            pass
        proc.communicate()
        report.write_text(f"Reviewer {argv[0]} timed out after {timeout:g} s.\n\nVERDICT: timeout\n",
                          encoding="utf-8")
        return None
    if capture:
        report.write_bytes(out or b"")
    return proc.returncode


def write_verdict(report: Path, status: int, message: str, text: str) -> tuple[int, str]:
    Path(f"{report}.calibration.md").write_text(
        "# Computed review verdict\n\n" + message + "\n\n## Reviewer findings (verbatim)\n\n" + text,
        encoding="utf-8")
    return status, message


def verdict(report: Path, policy: dict | None = None, *, addressed: str = "",
            round_number: int = 1, run_dir: Path | None = None,
            milestone: str = "milestone") -> tuple[int, str]:
    try:
        text = report.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        text = ""
    lines = [line for line in text.splitlines() if "VERDICT:" in line]
    match = VERDICT.fullmatch(lines[-1]) if lines else None
    word = match.group(1).lower() if match else None
    if word not in ("pass", "block"):
        return write_verdict(report, 3, "no verdict — not an approval", text)
    policy = policy or {"stage": "pilot", "invariants": []}
    findings = parse_findings(text)
    if word == 'block':
        recognized = {finding['raw'] for finding in findings}
        for line in text.splitlines():
            raw = line.strip()
            # Only protocol scaffolding can be ignored. Any other unclassified prose
            # might contain the reason for block, even beside a valid Minor finding.
            scaffold = (not raw or VERDICT.fullmatch(line) or raw == 'No findings.'
                        or re.fullmatch(r"(?:#{1,6}\s+|\*\*)?(?:Findings|Cross-vendor review|Review)(?:\*\*)?:?", raw, re.I)
                        or FENCE.fullmatch(line))
            if raw not in recognized and not scaffold:
                opaque = parse_findings('- [Critical] Unparsed reviewer response')[0]
                opaque.update(raw=raw, location='', defect=raw, reviewer_rating='Unknown')
                findings.append(opaque)
    if word == 'block' and not findings:
        opaque = parse_findings('- [Critical] Unparsed reviewer response')[0]
        opaque['raw'] = text
        findings = [opaque]
    previous, uncertain = (previous_blockers(findings, addressed, policy, run_dir, milestone)
                           if round_number == 2 else (set(), []))
    results = [compute(f, policy, round_number=round_number, previous_ids=previous) for f in findings]
    if run_dir:
        record_findings(run_dir, findings, policy, milestone, round_number, previous)
    adjustments = []
    pending = []
    for finding, result in zip(findings, results):
        label = finding["reviewer_rating"]
        if result['reason'].startswith('unverified:') and (word == 'block' or label != 'Minor'):
            pending.append(f"{finding['location'] or 'unparsed finding'}: {result['reason']}")
        if label != result["label"]:
            direction = "downgraded" if result["label"] == "Minor" else "reclassified"
            adjustments.append(f"{label} {finding['location']} {direction} to {result['label']}: {result['reason']}")
    if pending or uncertain:
        return write_verdict(report, 4, "VERDICT: needs triage — not an approval; reviewer VERDICT: " + word
                             + "; " + "; ".join(pending + uncertain), text)
    status = int(any(result["verdict"] == "block" for result in results))
    message = "VERDICT: " + ("block" if status else "pass") + " (computed from matrix)"
    if word != ("block" if status else "pass"):
        message += f"; reviewer VERDICT: {word} overridden"
    if adjustments:
        message += "; " + "; ".join(adjustments)
    return write_verdict(report, status, message, text)


CRITICAL = ("critical-review", "escalation")


def cap_reached(text: str, cap: int, ledger: Path) -> None:
    """Refuse when the ledger already holds `cap` critical reviews; unreadable lines count."""
    used = 0
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            used += json.loads(line).get("profile") in CRITICAL
        except (ValueError, AttributeError):
            used += 1  # a damaged entry must not open a slot
    if used >= cap:
        raise ReviewError(f"critical-review cap reached ({used}/{cap} in {ledger}): "
                          "review per milestone, or ask the user for more")


def reserve(ledger: Path, entry: dict, cap: int | None, on_reserve=None) -> None:
    """Count and append under one exclusive lock, so concurrent reviews cannot pass the cap."""
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a+", encoding="utf-8") as stream:
        if fcntl:
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            if cap is not None:
                stream.seek(0)
                cap_reached(stream.read(), cap, ledger)
            if on_reserve:
                on_reserve()
            stream.seek(0, os.SEEK_END)
            stream.write(json.dumps(entry) + "\n")
            stream.flush()
        finally:
            if fcntl:
                fcntl.flock(stream, fcntl.LOCK_UN)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="Git ref the diff is taken against")
    parser.add_argument("--writer", required=True, choices=sorted(REVIEWER))
    parser.add_argument("--out", required=True, help="report path; the context goes next to it")
    parser.add_argument("--tier", type=int, choices=range(4))
    parser.add_argument("--spec")
    parser.add_argument("--sections", help="comma-separated heading titles of --spec (legacy)")
    parser.add_argument("--section", action="append", help="one exact heading title; repeat, commas preserved")
    parser.add_argument("--run-dir", type=Path, help="run directory for backlog and review-ledger.jsonl")
    parser.add_argument("--milestone", help="stable milestone identity; required with --run-dir or --ledger")
    parser.add_argument("--round", type=int, choices=(1, 2), help="review round; --addressed defaults to 2 unless explicitly --round 1")
    parser.add_argument("--addressed", help="findings already addressed, included verbatim")
    parser.add_argument("--paths", nargs="+", metavar="PATHSPEC",
                        help="review only this part of the change (git pathspecs, e.g. server/ ':(glob)web/**')")
    parser.add_argument("--reviewer", choices=("claude", "codex"),
                        help="override the reviewer runtime (single-vendor when it equals --writer)")
    parser.add_argument("--ledger", help="JSONL ledger of this run's reviews (e.g. <run>/xreview.jsonl)")
    parser.add_argument("--max-critical", type=int, default=5,
                        help="cap on critical-review/escalation reviews recorded in --ledger (default 5)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=900.0)
    args = parser.parse_args()
    if bool(args.spec) != bool(args.sections or args.section):
        parser.error("--spec and --section/--sections go together")
    if args.round == 2 and not args.addressed:
        parser.error("round 2 requires --addressed (previous blockers and fixes)")
    if (args.run_dir or args.ledger) and not args.milestone:
        parser.error("--milestone is required with --run-dir or --ledger")
    if args.milestone is not None and not args.milestone.strip():
        parser.error("--milestone must be non-empty")
    args.milestone = args.milestone or Path(args.out).stem
    args.round = args.round if args.round is not None else 2 if args.addressed else 1
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    attempt_id = None
    run_dir = None
    def finish(status, reason):
        if run_dir and attempt_id:
            finish_review(run_dir, attempt_id, status, reason)
    try:
        root = checkout()
        base = base_commit(root, args.base)
        names, raw, skipped = changes(root, base, tuple(args.paths or ()))
        siblings = sorted(set(changes(root, base)[0]) - set(names)) if args.paths else []
        if args.addressed and SECRET_FILE.search(args.addressed):
            raise ReviewError("--addressed points at a secret-looking file; pass a findings report")
        if not names and not raw:
            raise ReviewError(f"Nothing to review: no changes against {args.base}")
        if len(raw) > DIFF_CAP:
            raise ReviewError(f"Diff is {len(raw)} bytes, over the {DIFF_CAP // 1024} KB cap; "
                              "review per unit (a narrower --base or one unit's checkout)")
        tier = tier_of(root, args.base, args.tier)
        reviewer = args.reviewer or REVIEWER[args.writer]
        if not args.reviewer and shutil.which(reviewer) is None:
            raise ReviewError(f"the {reviewer} CLI is not available: pass --reviewer {args.writer} for a "
                              "single-vendor review (recorded as such), or install the other vendor")
        profile_name, model, effort = profile(reviewer, tier[0], reviewer == args.writer, root)
        prompt = read(str(HARNESS / "prompts/xreview.md"), "prompt")
        report = Path(args.out).resolve()
        if report.is_dir():
            raise ReviewError(f"Report path is a directory: {report}")
        ledger = Path(args.ledger).resolve() if args.ledger else None
        run_dir = args.run_dir.resolve() if args.run_dir else ledger.parent if ledger else None
        policy = review_config(root)
        critical = profile_name in ("critical-review", "escalation")
        if ledger and critical and ledger.is_file():
            cap_reached(ledger.read_text(encoding="utf-8"), args.max_critical, ledger)
        text = context(args, base, names, raw.decode("utf-8", errors="replace"), tier, reviewer, profile_name)
        if skipped:
            text += block("Skipped (possible secrets, not sent)", "\n".join(f"- {n}" for n in skipped))
        if siblings:
            text += block("Reviewed separately (sibling parts of this change; do not report them missing)",
                          "\n".join(f"- {n}" for n in siblings))
        where = secret_location(text)
        if where:
            raise ReviewError(f"the review context holds a secret-looking value at {where}; "
                              "nothing was sent — remove it from the diff first")
        argv, env = command(reviewer, model, effort, root, report)
        context_path = Path(f"{report}.context.md")
        report.parent.mkdir(parents=True, exist_ok=True)
        context_path.write_text(text, encoding="utf-8")
        if args.dry_run:
            print(f"argv: {shlex.join(argv)}")
            for key, value in env.items():
                print(f"env: {key}={value}")
            print(f"stdin: {HARNESS / 'prompts/xreview.md'} + context")
            print(f"context: {context_path}")
            return 0
        payload = prompt.rstrip("\n") + "\n\n" + text
        if ledger and run_dir and ledger == run_dir / "review-ledger.jsonl":
            raise ReviewError("--ledger is the vendor usage ledger; use xreview.jsonl, not review-ledger.jsonl")
        def reserve_round():
            nonlocal attempt_id
            if run_dir:
                identity = uuid.uuid4().hex
                review_key = hashlib.sha256(base.encode() + raw + json.dumps(sorted(args.paths or [])).encode()).hexdigest()
                append_event(run_dir, {"type": "review", "milestone": args.milestone, "round": args.round,
                                       "report": str(report), "reviewer": reviewer, "status": "running",
                                       "review_key": review_key, "attempt_id": identity}, reserve_round=True)
                attempt_id = identity
        if ledger:
            reserve(ledger, {"at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                             "reviewer": reviewer, "profile": profile_name, "model": model,
                             "base": base, "tier": tier[0], "single_vendor": reviewer == args.writer},
                    args.max_critical if critical else None, reserve_round)
        else:
            reserve_round()
        env = load_guard.wait_for_capacity(root, heavy=not bool(args.paths),
                                           label='xreview', run=run_dir,
                                           env={**os.environ, **env})
        code = run(argv, env, payload, root, report, args.timeout)
    except (ReviewError, OverlayError, OSError, ValueError) as exc:
        try:
            finish('failed', str(exc))
        except (OSError, ValueError) as ledger_error:
            print(f"xreview: cannot release failed attempt: {ledger_error}", file=sys.stderr)
        print(f"xreview: {exc}", file=sys.stderr)
        return 2
    if code is None:
        try:
            finish('failed', 'reviewer timeout')
        except (OSError, ValueError) as exc:
            print(f"xreview: cannot release failed attempt: {exc}", file=sys.stderr)
        print(f"xreview: timeout after {args.timeout:g} s — not an approval: {report}", file=sys.stderr)
        return 3
    try:
        status, message = verdict(report, policy,
                                  addressed=read(args.addressed, "addressed findings") if args.addressed else "",
                                  round_number=args.round, run_dir=run_dir, milestone=args.milestone)
    except (ReviewError, OSError, ValueError) as exc:
        try:
            finish('failed', 'cannot persist review: ' + str(exc))
        except (OSError, ValueError):
            pass
        print(f"xreview: cannot persist review: {exc} — not an approval", file=sys.stderr)
        return 3
    if code and status != 1:
        status, message = write_verdict(report, 3, f"{argv[0]} exited {code} — not an approval",
                                        report.read_text(encoding="utf-8", errors="replace") if report.exists() else "")
    try:
        finish('failed' if code or status == 3 else 'complete', message)
    except (OSError, ValueError) as exc:
        status, message = 3, f"cannot finalize review attempt: {exc} — not an approval"
    print(f"xreview: {message}: {report}", file=sys.stderr if status else sys.stdout)
    return status


if __name__ == "__main__":
    sys.exit(main())
