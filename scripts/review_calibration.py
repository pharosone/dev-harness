#!/usr/bin/env python3
"""Calibrated review policy and append-only run evidence (stdlib only).

Commands: record (QA/panel), triage, outcome, report, fixture. Use --help on a command for arguments.
A report suggests policy changes; it never changes the shared scale or sends feedback.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
try:
    import fcntl
except ImportError:
    fcntl = None

sys.dont_write_bytecode = True
from project import OverlayError, load as load_overlay, repo_root

LIKELIHOODS = ('will', 'plausible', 'edge')
IMPACTS = ('severe', 'functional', 'cosmetic')
FINDING = re.compile(r'^\s*(?:[-*]|\d+\.)\s*\[(Critical|Major|Minor)\]\s*(.*)$')
LOCATION = re.compile(r'^.+:\d+$')
RATING = re.compile(r'^(will|plausible|edge)\s*/\s*(severe|functional|cosmetic)$')


def review_config(root: Path) -> dict:
    data = load_overlay(root, 'project.json', {}).get('review', {})
    if not isinstance(data, dict):
        raise OverlayError('project.json: review must be an object')
    unknown = set(data) - {'stage', 'invariants'}
    if unknown:
        raise OverlayError('project.json: unknown review keys: ' + ', '.join(sorted(unknown)))
    stage, invariants = data.get('stage', 'pilot'), data.get('invariants', [])
    if stage not in ('pilot', 'production'):
        raise OverlayError('project.json: review.stage must be pilot or production')
    if not isinstance(invariants, list) or not all(
            isinstance(value, str) and value.strip() and '\n' not in value and '\r' not in value
            for value in invariants):
        raise OverlayError('project.json: review.invariants must be non-empty single-line strings')
    return {'stage': stage, 'invariants': list(invariants)}


def finding_id(finding: dict) -> str:
    # Line shifts during fix-up do not create a new defect; preserve the defect title.
    path = finding.get('location', '').rsplit(':', 1)[0]
    title = ' '.join(finding.get('defect', '').split()).casefold()
    identity = path + '\n' + title
    return hashlib.sha256(identity.encode()).hexdigest()[:16]


def parse_findings(text: str) -> list[dict]:
    """Parse explicit fields; absent or invalid evidence stays unverified, never inferred."""
    findings = []
    for line in text.splitlines():
        match = FINDING.match(line)
        if not match:
            continue
        parts = [part.strip() for part in match[2].split(' — ')]
        finding = {'reviewer_rating': match[1], 'raw': line.strip(),
                   'location': parts[0] if parts else '',
                   'defect': parts[1] if len(parts) > 1 else '',
                   'likelihood': None, 'impact': None, 'reachable_path': '',
                   'scenario': '', 'evidence': '', 'workaround': None, 'invariant': None}
        if len(parts) >= 5:
            rating = RATING.fullmatch(parts[2])
            if rating:
                finding['likelihood'], finding['impact'] = rating.groups()
            finding['reachable_path'], finding['scenario'] = parts[3:5]
            for extra in parts[5:]:
                key, separator, value = extra.partition(':')
                if separator and key in ('evidence', 'workaround', 'invariant'):
                    finding[key] = value.strip()
        finding['id'] = finding_id(finding)
        findings.append(finding)
    return findings


def compute(finding: dict, review: dict | None = None, *, round_number: int = 1,
            previous_ids: set[str] | None = None) -> dict:
    review = review or {'stage': 'pilot', 'invariants': []}
    missing = [key for key in ('defect', 'reachable_path', 'scenario', 'evidence')
               if not finding.get(key) or finding.get(key, '').casefold() in ('none', 'unknown', 'memory')]
    if not LOCATION.fullmatch(finding.get('location', '')):
        missing.append('path:line')
    if not LOCATION.fullmatch(finding.get('evidence', '')):
        missing.append('code-read evidence path:line')
    likelihood, impact = finding.get('likelihood'), finding.get('impact')
    if likelihood not in LIKELIHOODS or impact not in IMPACTS:
        missing.append('likelihood/impact')
    if missing:
        return {'verdict': 'backlog', 'label': 'Minor',
                'reason': 'unverified: missing/invalid ' + ', '.join(dict.fromkeys(missing))}
    blocks = False
    reason = f'{impact} × {likelihood}'
    if impact == 'severe':
        invariant = finding.get('invariant')
        blocks = (likelihood != 'edge' or review['stage'] == 'production'
                  or bool(invariant and invariant in review['invariants']))
        if likelihood == 'edge':
            reason += ('; production stage' if review['stage'] == 'production' else
                       '; breaks overlay invariant: ' + invariant if blocks else
                       '; pilot, no matching overlay invariant')
    elif impact == 'functional':
        blocks = likelihood == 'will' or (likelihood == 'plausible' and finding.get('workaround') == 'none')
        if likelihood == 'plausible':
            reason += ('; no workaround' if blocks else '; workaround available or absence unverified')
    if (blocks and round_number == 2 and impact != 'severe'
            and finding_id(finding) not in (previous_ids or set())):
        blocks = False
        reason += '; round 2 new blocker must be severe'
    return {'verdict': 'block' if blocks else 'backlog',
            'label': ('Critical' if impact == 'severe' else 'Major') if blocks else 'Minor',
            'reason': reason}


def ledger_path(run: Path) -> Path:
    return run / 'review-ledger.jsonl'


def read_events(run: Path) -> list[dict]:
    path = ledger_path(run)
    if not path.exists():
        return []
    events = []
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
            if not isinstance(event, dict) or event.get('type') not in ('review', 'finding', 'triage', 'outcome'):
                raise ValueError('invalid event')
            events.append(event)
        except ValueError as exc:
            raise ValueError(f'{path}:{number}: invalid ledger: {exc}') from None
    return events


def append_event(run: Path, event: dict, *, reserve_round: bool = False) -> None:
    run.mkdir(parents=True, exist_ok=True)
    path = ledger_path(run)
    with path.open('a+', encoding='utf-8') as stream:
        if fcntl:
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            if reserve_round:
                events = read_events(run)
                rounds = [e for e in events if e['type'] == 'review' and e['milestone'] == event['milestone']]
                used = {e['round'] for e in rounds}
                requested = event['round']
                if requested not in (1, 2) or (requested == 1 and 2 in used):
                    raise ValueError(f"milestone {event['milestone']}: at most 2 review rounds; cannot return to round 1")
                if requested == 2 and 1 not in used:
                    raise ValueError(f"milestone {event['milestone']}: expected round 1 before round 2")
                if any(e['round'] == requested and e.get('report') == event.get('report') for e in rounds):
                    raise ValueError(f"milestone {event['milestone']}: report already reviewed in round {requested}; at most 2 review rounds")
            stream.write(json.dumps({'at': datetime.now(timezone.utc).isoformat(), **event}, ensure_ascii=False) + '\n')
            stream.flush()
        finally:
            if fcntl:
                fcntl.flock(stream, fcntl.LOCK_UN)


def snapshots(run: Path) -> dict[str, dict]:
    result = {}
    for event in read_events(run):
        kind = event['type']
        if kind == 'finding':
            prior = result.get(event['id'], {})
            # A second review keeps historical decisions/outcomes until a new triage.
            result[event['id']] = {**prior, **event}
        elif kind in ('triage', 'outcome'):
            if event['id'] not in result:
                raise ValueError('ledger update references unknown finding: ' + event['id'])
            result[event['id']].update({k: v for k, v in event.items() if k not in ('type', 'at')})
    return result


def record_findings(run: Path, findings: list[dict], review: dict, milestone: str,
                    round_number: int, previous_ids: set[str]) -> None:
    existing = snapshots(run)
    for finding in findings:
        result = compute(finding, review, round_number=round_number, previous_ids=previous_ids)
        observation_id = hashlib.sha256((milestone + '\n' + finding_id(finding)).encode()).hexdigest()[:16]
        prior = existing.get(observation_id, {})
        append_event(run, {'type': 'finding', **finding, 'id': observation_id, 'computed_verdict': result['verdict'],
                           'computed_label': result['label'], 'computed_reason': result['reason'],
                           'review': review, 'milestone': milestone, 'round': round_number,
                           'orchestrator_triage': None, 'triage_reason': None,
                           'user_override': prior.get('user_override'), 'outcome': prior.get('outcome'),
                           'missed': prior.get('missed', False), 'over_blocked': prior.get('over_blocked', False),
                           'previous_ids': sorted(previous_ids)})
    write_backlog(run)


def decision(finding: dict) -> str:
    return finding.get('user_override') or finding.get('orchestrator_triage') or finding['computed_verdict']


def write_backlog(run: Path) -> None:
    """Own one marked section and retain project notes outside it."""
    path = run / 'backlog.md'
    start, end = '<!-- calibrated-review:start -->', '<!-- calibrated-review:end -->'
    old = path.read_text(encoding='utf-8') if path.exists() else '# Review backlog\n\n'
    body = start + '\n\n'
    for finding in snapshots(run).values():
        if decision(finding) == 'backlog':
            body += (f"- [{finding['id']}] {finding['location']} — {finding['defect']} — "
                     f"{finding.get('triage_reason') or finding['computed_reason']}"
                     + (f"; outcome: {finding['outcome']}" if finding.get('outcome') else '') + '\n')
    body += '\n' + end
    if start in old and end in old:
        before, rest = old.split(start, 1)
        _, after = rest.split(end, 1)
        text = before + body + after
    else:
        text = old.rstrip() + '\n\n' + body + '\n'
    path.write_text(text, encoding='utf-8')


def get_finding(run: Path, identity: str) -> dict:
    try:
        return snapshots(run)[identity]
    except KeyError:
        raise ValueError('unknown finding id: ' + identity) from None


def triage(run: Path, identity: str, choice: str, reason: str, **updates) -> None:
    finding = get_finding(run, identity)
    updates = {k: v for k, v in updates.items() if v is not None}
    candidate = {**finding, **updates}
    result = compute(candidate, finding['review'], round_number=finding['round'],
                     previous_ids=set(finding['previous_ids']))
    if choice != result['verdict']:
        raise ValueError('triage contradicts matrix (' + result['reason'] + '); record a user override via outcome')
    if not reason.strip():
        raise ValueError('triage needs a reason')
    if choice == 'block' and not LOCATION.fullmatch(candidate.get('evidence', '')):
        raise ValueError('blocking triage needs code-read evidence path:line')
    append_event(run, {'type': 'triage', 'id': identity, 'orchestrator_triage': choice,
                       'triage_reason': reason, 'triage_computed_reason': result['reason'], **updates})
    write_backlog(run)


def mark_outcome(run: Path, identity: str, outcome: str, source: str, reason: str,
                 user_override: str | None = None) -> None:
    finding = get_finding(run, identity)
    if outcome not in (None, 'failure', 'harmless', 'confirmed') or source not in ('fix-up', 'incident', 'user-report', 'review'):
        raise ValueError('invalid outcome/source')
    if (user_override not in (None, 'block', 'backlog') or not reason.strip()
            or (outcome is None and user_override is None)):
        raise ValueError('outcome needs a reason and valid user override')
    original = finding.get('orchestrator_triage') or finding['computed_verdict']
    accepted = user_override or decision(finding)
    append_event(run, {'type': 'outcome', 'id': identity, 'outcome': outcome or finding.get('outcome'), 'outcome_source': source,
                       'outcome_reason': reason, 'user_override': user_override or finding.get('user_override'),
                       'missed': accepted == 'backlog' and (outcome or finding.get('outcome')) == 'failure',
                       'over_blocked': original == 'block' and ((outcome or finding.get('outcome')) == 'harmless' or accepted == 'backlog')})
    write_backlog(run)


def policy_suggestions(findings: list[dict], counts: dict, over_block_threshold: float,
                       missed_threshold: float, min_outcomes: int) -> list[str]:
    suggestions = []
    for flag, threshold, count_key in (
            ('over_blocked', over_block_threshold, 'observed_blockers'), ('missed', missed_threshold, 'observed_backlog')):
        if counts[count_key] >= min_outcomes and counts[flag + '_rate'] > threshold:
            cells = Counter((f.get('impact'), f.get('likelihood')) for f in findings if f.get(flag))
            for (impact, likelihood), count in cells.most_common(1):
                if flag == 'missed' and impact == 'severe' and likelihood == 'edge':
                    action = 'Add a product invariant for the demonstrated severe × edge failure.'
                elif flag == 'missed':
                    action = f'Propose moving {impact} × {likelihood} to block for the demonstrated path.'
                else:
                    action = f'Propose narrowing {impact} × {likelihood} blocking to the demonstrated product path (or backlog with a workaround).'
                suggestions.append(f'{flag} rate {counts[flag + "_rate"]:.1%} > {threshold:.1%}: {action} Evidence: {count} findings; validate sanitized fixtures before changing defaults.')
    return suggestions


def summary(runs: list[Path], *, over_block_threshold: float = .25,
            missed_threshold: float = .10, min_outcomes: int = 3) -> dict:
    rows, all_findings, rounds = [], [], {}
    for run in dict.fromkeys(path.resolve() for path in runs):
        findings = list(snapshots(run).values())
        milestones = {}
        seen_rounds = set()
        for event in read_events(run):
            if event['type'] == 'review':
                name = event['milestone']
                marker = (name, event['round'])
                if marker not in seen_rounds:
                    milestones.setdefault(name, {'blockers': 0, 'rounds_used': 0})['rounds_used'] += 1
                    seen_rounds.add(marker)
        for finding in findings:
            m = milestones.setdefault(finding['milestone'], {'blockers': 0, 'rounds_used': 0})
            original = finding.get('orchestrator_triage') or finding['computed_verdict']
            m['blockers'] += original == 'block'
        counts = rates(findings)
        rows.append({'run': str(run), 'milestones': milestones, **counts,
                     'suggestions': policy_suggestions(findings, counts, over_block_threshold, missed_threshold, min_outcomes)})
        all_findings.extend(findings)
        for name, values in milestones.items():
            rounds[str(run) + ':' + name] = values
    totals = rates(all_findings)
    suggestions = policy_suggestions(all_findings, totals, over_block_threshold, missed_threshold, min_outcomes)
    return {'runs': rows, 'across_runs': {**totals, 'milestones': rounds}, 'suggestions': suggestions,
            'thresholds': {'over_blocked': over_block_threshold, 'missed': missed_threshold,
                           'min_outcomes': min_outcomes}}


def rates(findings: list[dict]) -> dict:
    blockers = [f for f in findings if (f.get('orchestrator_triage') or f['computed_verdict']) == 'block']
    backlog = [f for f in findings if decision(f) == 'backlog']
    over = sum(bool(f.get('over_blocked')) for f in blockers)
    missed = sum(bool(f.get('missed')) for f in backlog)
    return {'findings': len(findings), 'blockers': len(blockers), 'backlog': len(backlog),
            'over_blocked': over, 'missed': missed,
            'over_blocked_rate': over / len(blockers) if blockers else 0,
            'missed_rate': missed / len(backlog) if backlog else 0,
            'observed_blockers': sum(bool(f.get('outcome') or f.get('user_override')) for f in blockers),
            'observed_backlog': sum(bool(f.get('outcome')) for f in backlog)}


def render_summary(data: dict) -> str:
    lines = ['# Review calibration', '', 'Rates use all blocker/backlog decisions; unresolved outcomes are counted in the denominator.', '']
    for row in data['runs'] + [{'run': 'Across runs', **data['across_runs']}]:
        lines.append(f"{row['run']}: {row['blockers']} blockers; over-blocked {row['over_blocked']}/{row['blockers']} ({row['over_blocked_rate']:.1%}); missed {row['missed']}/{row['backlog']} ({row['missed_rate']:.1%}); observed outcomes {row['observed_blockers']} blocker, {row['observed_backlog']} backlog.")
        for name, milestone in row['milestones'].items():
            lines.append(f"- {name}: {milestone['blockers']} blockers, {milestone['rounds_used']}/2 rounds")
        lines.extend(row.get('suggestions', []))
        lines.append('')
    lines.extend(data['suggestions'] or ['No threshold crossed with sufficient observed outcomes.'])
    return '\n'.join(lines) + '\n'


def export_fixture(run: Path, identity: str, case: dict, out: Path) -> None:
    """Only explicitly supplied sanitized content leaves the run; never export raw evidence."""
    finding = get_finding(run, identity)
    if not finding.get('user_override') and not finding.get('triage_reason'):
        raise ValueError('fixture requires an orchestrator or user correction')
    if set(case) != {'name', 'finding', 'review'} or not isinstance(case['name'], str):
        raise ValueError('sanitized case requires exactly name, finding, review')
    if not isinstance(case['finding'], str) or not isinstance(case['review'], dict):
        raise ValueError('invalid sanitized case')
    allowed = {'stage', 'invariants'}
    if set(case['review']) != allowed or case['review']['stage'] not in ('pilot', 'production') or not isinstance(case['review']['invariants'], list) or not all(isinstance(x, str) for x in case['review']['invariants']):
        raise ValueError('invalid sanitized review config')
    text = json.dumps(case)
    # Refuse likely private context; the caller must replace it with synthetic details.
    if re.search(r'(?i)(/(?:Users|home|private|tmp)/|PRIVATE KEY|AKIA[0-9A-Z]{16}|[A-Z]:\\|https?://|\b\S+@\S+\b|\b(?:sk-|gh[pousr]_|xox[baprs]-))', text):
        raise ValueError('case contains private paths, recipients, URLs or credential-like text; sanitize it')
    parsed = parse_findings(case['finding'])
    if len(parsed) != 1 or compute(parsed[0], case['review'])['reason'].startswith('unverified:'):
        raise ValueError('case requires one verified canonical finding')
    exported = {**case, 'expected': decision(finding), 'origin': 'user-correction'}
    cases = json.loads(out.read_text()) if out.exists() else []
    if not isinstance(cases, list):
        raise ValueError('fixture output must be a JSON array')
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps([*cases, exported], indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('record', 'triage', 'outcome', 'report', 'fixture'):
        cmd = sub.add_parser(name)
        cmd.add_argument('--run', type=Path, required=True, action='append' if name == 'report' else 'store')
        if name not in ('record', 'report'):
            cmd.add_argument('--id', required=True)
        if name in ('triage', 'outcome'):
            cmd.add_argument('--reason', required=True)
        if name == 'record':
            cmd.add_argument('--report', type=Path, required=True)
            cmd.add_argument('--root', type=Path, help='product checkout; default current Git checkout')
            cmd.add_argument('--milestone', required=True)
            cmd.add_argument('--round', type=int, choices=(1, 2), default=1)
            cmd.add_argument('--addressed', type=Path)
        elif name == 'triage':
            cmd.add_argument('--decision', choices=('block', 'backlog'), required=True)
            cmd.add_argument('--likelihood', choices=LIKELIHOODS)
            cmd.add_argument('--impact', choices=IMPACTS)
            for field in ('workaround', 'invariant', 'evidence', 'location', 'defect', 'reachable_path', 'scenario'):
                cmd.add_argument('--' + field.replace('_', '-'))
        elif name == 'outcome':
            cmd.add_argument('--outcome', choices=('failure', 'harmless', 'confirmed'), help='later outcome; optional with --user-override')
            cmd.add_argument('--source', choices=('fix-up', 'incident', 'user-report', 'review'), required=True)
            cmd.add_argument('--user-override', choices=('block', 'backlog'))
        elif name == 'report':
            cmd.add_argument('--json', action='store_true')
            cmd.add_argument('--over-block-threshold', type=float, default=.25)
            cmd.add_argument('--missed-threshold', type=float, default=.10)
            cmd.add_argument('--min-outcomes', type=int, default=3)
        else:
            cmd.add_argument('--case', type=Path, required=True, help='explicitly sanitized JSON (name, finding, review)')
            cmd.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'record':
            policy = review_config(args.root.resolve() if args.root else repo_root())
            text = args.report.read_text(encoding='utf-8')
            round_number = 2 if args.addressed else args.round
            if round_number == 2 and not args.addressed:
                raise ValueError('round 2 requires --addressed')
            if not args.milestone.strip():
                raise ValueError('milestone must be non-empty')
            prior = parse_findings(args.addressed.read_text(encoding='utf-8')) if args.addressed else []
            previous = {finding_id(f) for f in prior if compute(f, policy)['verdict'] == 'block'}
            append_event(args.run, {'type': 'review', 'milestone': args.milestone, 'round': round_number,
                                   'report': str(args.report.resolve())}, reserve_round=True)
            record_findings(args.run, parse_findings(text), policy, args.milestone, round_number, previous)
        elif args.command == 'triage':
            triage(args.run, args.id, args.decision, args.reason,
                   **{k: getattr(args, k) for k in ('likelihood', 'impact', 'workaround', 'invariant', 'evidence', 'location', 'defect', 'reachable_path', 'scenario')})
        elif args.command == 'outcome':
            mark_outcome(args.run, args.id, args.outcome, args.source, args.reason, args.user_override)
        elif args.command == 'fixture':
            export_fixture(args.run, args.id, json.loads(args.case.read_text(encoding='utf-8')), args.out)
        else:
            if not all(0 <= x <= 1 for x in (args.over_block_threshold, args.missed_threshold)) or args.min_outcomes < 1:
                raise ValueError('thresholds must be in [0, 1]; min-outcomes must be positive')
            for run in args.run:
                if not ledger_path(run).is_file():
                    raise ValueError('no review ledger: ' + str(ledger_path(run)))
            result = summary(args.run, over_block_threshold=args.over_block_threshold,
                             missed_threshold=args.missed_threshold, min_outcomes=args.min_outcomes)
            print(json.dumps(result, indent=2) if args.json else render_summary(result), end='\n' if args.json else '')
        return 0
    except (OSError, ValueError, KeyError, TypeError, OverlayError) as exc:
        print(f'review_calibration: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
