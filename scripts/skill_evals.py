#!/usr/bin/env python3
"""Paired skill evals and a model-free evidence gate (stdlib only).

check --base REF includes committed, staged, unstaged and untracked changes.
run --skill NAME|SKILL.md [--model M] [--max-cost-usd X] [--run-dir DIR]
report prints latest evidence, including stale results. Exit 0 clean, 1 unmet
acceptance/runner failure, 2 invalid configuration or incomplete evidence.
See README 'Skill evals' for the case registry and product runner protocol.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True
import project
from tripwire import StateError, changed_files, glob_pattern


class EvalError(Exception):
    pass


def read(path):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise EvalError(f'{path}: {exc}') from None


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def relative(value, where):
    if not isinstance(value, str) or not value.strip() or value != value.strip() \
            or Path(value).is_absolute() or '..' in value.split('/'):
        raise EvalError(f'{where}: expected a checkout-relative path/glob')
    return value


def number(value, where, maximum=None):
    if type(value) not in (float, int) or not math.isfinite(value) or value < 0 \
            or (maximum is not None and value > maximum):
        raise EvalError(f'{where}: expected a finite number in 0..{maximum or "infinity"}')
    return value


def config(root):
    data = project.load(root, 'project.json', {}).get('skills', {})
    if not isinstance(data, dict) or set(data) - {'dirs', 'eval_dirs', 'eval_command', 'tolerance'}:
        raise EvalError('project.json skills: unknown key or expected an object')
    result = {'dirs': [], 'eval_dirs': [], 'eval_command': None, 'tolerance': 0.05, **data}
    for key in ('dirs', 'eval_dirs'):
        if not isinstance(result[key], list):
            raise EvalError(f'skills.{key}: expected a list')
        for value in result[key]:
            relative(value, f'skills.{key}')
            if key == 'eval_dirs' and any(c in value for c in '*?['):
                raise EvalError('skills.eval_dirs: directories cannot be globs')
    command = result['eval_command']
    if command is not None and (not isinstance(command, str) or not command.strip()):
        raise EvalError('skills.eval_command: expected a nonempty command string')
    if command is not None:
        try:
            if not shlex.split(command):
                raise ValueError('empty command')
        except ValueError as exc:
            raise EvalError(f'skills.eval_command: {exc}') from None
    number(result['tolerance'], 'skills.tolerance', 1)
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case_hash(directory):
    files = sorted(p for p in directory.rglob('*') if p.is_file())
    if not files:
        raise EvalError(f'{directory}: empty eval case')
    result = hashlib.sha256()
    for path in files:
        result.update(path.relative_to(directory).as_posix().encode() + b'\0')
        result.update(path.read_bytes() + b'\0')
    return result.hexdigest()


def inventory(root, settings):
    """Explicit ownership: <eval dir>/cases.json maps SKILL.md paths to case dirs."""
    core = project.core_root()
    patterns = [glob_pattern(p.rstrip('/')) for p in settings['dirs']]
    skills = {}
    for path in root.rglob('SKILL.md'):
        # Do not traverse symlinked generated skills or personal agents.
        if any(parent.is_symlink() for parent in path.parents if parent != root):
            continue
        name = path.relative_to(root).as_posix()
        harness = path.is_relative_to(core / 'skills')
        literal_dir = any(not any(c in value for c in '*?[')
                          and name.startswith(value.rstrip('/') + '/') for value in settings['dirs'])
        if harness or literal_dir or any(p.match(name) or p.match(str(Path(name).parent)) for p in patterns):
            skills[name] = {'path': path, 'harness': harness, 'cases': {}}
    eval_dirs = [root / p for p in settings['eval_dirs']]
    if core.is_relative_to(root):
        eval_dirs.insert(0, core / 'plugin/evals')
    for directory in dict.fromkeys(eval_dirs):
        registry = directory / 'cases.json'
        if not registry.is_file():
            continue
        data = read(registry)
        if not isinstance(data, dict):
            raise EvalError(f'{registry}: expected a skill-to-cases object')
        for skill, cases in data.items():
            relative(skill, str(registry))
            # Core registry paths are relative to the core; product ones to checkout.
            key = (core / skill).relative_to(root).as_posix() \
                if directory == core / 'plugin/evals' else skill
            if key not in skills:
                raise EvalError(f'{registry}: unknown skill {skill}')
            if not isinstance(cases, list) or not cases or any(not isinstance(c, str) for c in cases) \
                    or len(set(cases)) != len(cases):
                raise EvalError(f'{registry}: {skill} needs unique case directories')
            item = skills[key]
            if 'eval_dir' in item and item['eval_dir'] != directory:
                raise EvalError(f'{skill}: cases must live in one eval directory')
            item['eval_dir'] = directory
            for case in cases:
                relative(case, str(registry))
                path = directory / case
                if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(directory.resolve()):
                    raise EvalError(f'{path}: missing/unsafe case directory')
                if item['harness'] and (not (path / 'prompt.md').is_file()
                                       or not list((path / 'graders').glob('*.md'))):
                    raise EvalError(f'{path}: needs prompt.md and graders/*.md')
                item['cases'][case] = path
    return skills


def identity(key):
    return Path(key).parent.name + '-' + hashlib.sha256(key.encode()).hexdigest()[:8]


def canonical(key, item):
    # Core evidence remains usable when the core is vendored under harness/core.
    return item['path'].relative_to(project.core_root()).as_posix() if item['harness'] else key


def histories(key, item):
    directory = item.get('eval_dir')
    return sorted((directory / 'history' / identity(canonical(key, item))).glob('*.json')) if directory else []


def latest(key, item):
    files = histories(key, item)
    return read(files[-1]) if files else None


def hashes(item):
    return {name: case_hash(path) for name, path in item['cases'].items()}


def validate(result, expected):
    if not isinstance(result, dict) or result.get('partial', False):
        raise EvalError('runner returned partial/invalid evidence')
    if not isinstance(result.get('model'), str) or not result['model'].strip():
        raise EvalError('result needs the actual model')
    number(result.get('cost_usd'), 'cost_usd')
    cases = result.get('cases')
    if not isinstance(cases, list) or any(not isinstance(c, dict) for c in cases):
        raise EvalError('result needs cases')
    names = [c.get('name') for c in cases]
    if any(not isinstance(name, str) for name in names) or set(names) != set(expected) \
            or len(names) != len(expected):
        raise EvalError('runner cases do not match the registered suite exactly')
    for case in cases:
        number(case.get('score_with'), 'score_with', 1)
        number(case.get('score_without'), 'score_without', 1)
        delta = case['score_with'] - case['score_without']
        if 'delta' in case:
            recorded = case['delta']
            if type(recorded) not in (int, float) or not math.isfinite(recorded) \
                    or not math.isclose(recorded, delta, abs_tol=1e-9):
                raise EvalError(f'{case["name"]}: inconsistent delta')
        case['delta'] = delta
    return result


def fresh(result, key, item):
    if not result:
        return False
    if not isinstance(result, dict):
        raise EvalError('invalid result object')
    if result.get('skill') != canonical(key, item) \
            or result.get('content_hash') != digest(item['path']) \
            or result.get('case_hashes') != hashes(item):
        return False
    validate(result, item['cases'])
    try:
        run_date = result['run_date']
        if not isinstance(run_date, str):
            raise ValueError('invalid date')
        datetime.fromisoformat(run_date.replace('Z', '+00:00'))
    except (KeyError, TypeError, ValueError):
        raise EvalError('result needs an ISO run_date') from None
    return True


def git_json(root, base, path):
    relative_path = path.relative_to(root).as_posix()
    proc = subprocess.run(['git', 'show', f'{base}:{relative_path}'], cwd=root,
                          capture_output=True, text=True)
    if proc.returncode:
        return None
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise EvalError(f'{base}:{relative_path}: invalid JSON') from None


def previous_at_base(root, base, key, item):
    directory = item['eval_dir'] / 'history' / identity(canonical(key, item))
    proc = subprocess.run(['git', 'ls-tree', '-r', '--name-only', base, '--',
                           directory.relative_to(root).as_posix()], cwd=root,
                          capture_output=True, text=True, check=True)
    files = sorted(p for p in proc.stdout.splitlines() if p.endswith('.json'))
    for path in reversed(files):
        result = git_json(root, base, root / path)
        if not isinstance(result, dict):
            raise EvalError(f'{base}:{path}: invalid result object')
        if not result.get('acceptance_findings'):
            return result
    return None


def acceptance(result, prior, changed_cases, tolerance):
    findings = []
    if prior is not None:
        if not isinstance(prior, dict) or not isinstance(prior.get('cases'), list) \
                or any(not isinstance(c, dict) or not isinstance(c.get('name'), str)
                       for c in prior['cases']):
            raise EvalError('invalid prior result cases')
        validate(prior, [c['name'] for c in prior['cases']])
    old = {c['name']: c for c in prior['cases']} if prior else {}
    for case in result['cases']:
        name = case['name']
        if name in changed_cases and case['delta'] <= 0:
            findings.append(f'{name}: Δ must be > 0; got {case["delta"]:+.3f}; fix the case')
        if name in old and case['score_with'] + tolerance + 1e-9 < old[name]['score_with']:
            findings.append(f'{name}: score regressed beyond tolerance {tolerance}')
    return findings


def changed_cases(item, changed, root):
    return {name for name, path in item['cases'].items()
            if any(p == path.relative_to(root).as_posix() or
                   p.startswith(path.relative_to(root).as_posix() + '/') for p in changed)}


def check(root, base, settings, skills):
    changed = changed_files(root, base)
    findings = []
    for key, item in skills.items():
        case_changes = changed_cases(item, changed, root)
        if key not in changed and not case_changes:
            continue
        touched = changed_cases(item, changed, root)
        if key in changed and not touched:
            findings.append(f'{key}: no added/changed eval case; add a scenario and graders')
        result = latest(key, item)
        if not fresh(result, key, item):
            findings.append(f'{key}: no fresh result for current skill/case hashes; run skill_evals.py run --skill {key}')
        else:
            findings.extend(f'{key}: {message}' for message in acceptance(
                result, previous_at_base(root, base, key, item), touched, settings['tolerance']))
    return findings


def claude_result(raw, model):
    if not isinstance(raw, dict):
        raise EvalError('Claude runner must return a JSON object')
    if raw.get('partial'):
        raise EvalError('Claude eval aborted: partial results cannot pass the gate')
    cases = []
    for case in raw.get('cases', []):
        arms = case.get('arms', {})
        scores = {}
        for arm in ('with', 'without'):
            runs = arms.get(arm, [])
            if not runs or any(r.get('error') or r.get('skippedPaidGraders') for r in runs):
                raise EvalError(f'{case.get("name")}: incomplete {arm} arm')
            scores[arm] = sum(number(r.get('score'), 'arm score', 1) for r in runs) / len(runs)
        cases.append({'name': case['name'], 'score_with': scores['with'],
                      'score_without': scores['without']})
    actual = raw.get('suite', {}).get('modelOverride') or model
    return {'model': actual, 'cost_usd': raw.get('costUsd'), 'cases': cases,
            'runner': {'name': 'claude plugin eval', 'version': raw.get('claudeVersion'),
                       'ablation': raw.get('suite', {}).get('ablation', 'with-without')}}


def run(root, args, settings, skills):
    matches = [(key, item) for key, item in skills.items()
               if args.skill in (key, Path(key).parent.name)]
    if len(matches) != 1:
        raise EvalError(f'{args.skill}: skill missing or ambiguous; use its SKILL.md path')
    key, item = matches[0]
    if not item['cases']:
        raise EvalError(f'{key}: no registered eval cases')
    before, case_hashes = digest(item['path']), hashes(item)
    prior = previous_at_base(root, args.base, key, item)
    if prior is None:
        # Until history is committed, compare only to an accepted prior attempt.
        prior = next((read(p) for p in reversed(histories(key, item))
                      if not read(p).get('acceptance_findings')), None)
    # Compare acceptance to the branch base, including untracked new cases.
    touched = changed_cases(item, changed_files(root, args.base), root)
    with tempfile.TemporaryDirectory(prefix='skill-evals-') as work:
        temp = Path(work)
        out = temp / 'runner.json'
        native_claude = item['harness'] and args.runtime == 'claude'
        if native_claude:
            plugin = temp / 'plugin'
            (plugin / '.claude-plugin').mkdir(parents=True)
            write(plugin / '.claude-plugin/plugin.json', {'name': 'skill-evals', 'version': '1.0.0'})
            (plugin / 'skills').mkdir()
            (plugin / 'skills' / Path(key).parent.name).symlink_to(item['path'].parent)
            for name, path in item['cases'].items():
                shutil.copytree(path, plugin / 'evals' / name)
            cmd = ['claude', 'plugin', 'eval', str(plugin), '--trust-plugin', '--no-publish',
                   '--ablation', 'with-without', '--json', str(out), '--model', args.model or 'sonnet']
        else:
            if not settings['eval_command']:
                raise EvalError('product skill requires skills.eval_command (Codex-only: configure a paired Codex runner)')
            cmd = shlex.split(settings['eval_command']) + ['--skill', key, '--out', str(out)]
            if args.model:
                cmd += ['--model', args.model]
        if args.max_cost_usd is not None:
            cmd += ['--max-cost-usd', str(args.max_cost_usd)]
        proc = subprocess.run(cmd, cwd=root)
        # Claude exit 1 means a score below its threshold; our acceptance uses Δ/tolerance.
        if proc.returncode not in ((0, 1) if native_claude else (0,)):
            print(f'runner failed (exit {proc.returncode}); no fresh evidence written', file=sys.stderr)
            return 1
        raw = read(out)
        result = validate(claude_result(raw, args.model or 'sonnet') if native_claude else raw,
                          item['cases'])
    if before != digest(item['path']) or case_hashes != hashes(item):
        raise EvalError('skill/cases changed during the run; rerun')
    if args.max_cost_usd is not None and result['cost_usd'] > args.max_cost_usd:
        raise EvalError('runner exceeded --max-cost-usd; no fresh evidence written')
    now = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    result.update({'schema_version': 1, 'skill': canonical(key, item), 'content_hash': before,
                   'case_hashes': case_hashes, 'run_date': now})
    problems = acceptance(result, prior, touched, settings['tolerance'])
    result['acceptance_findings'] = problems
    history = item['eval_dir'] / 'history' / identity(canonical(key, item)) / (now.replace(':', '-') + '.json')
    write(history, result)
    if args.run_dir:
        write(Path(args.run_dir) / 'evals' / (Path(key).parent.name + '.json'), result)
    print(f'{key}: with={sum(c["score_with"] for c in result["cases"])/len(result["cases"]):.3f} '
          f'without={sum(c["score_without"] for c in result["cases"])/len(result["cases"]):.3f} '
          f'Δ={sum(c["delta"] for c in result["cases"])/len(result["cases"]):+.3f} '
          f'model={result["model"]} cost=${result["cost_usd"]:.4f} date={now}')
    print(history)
    for problem in problems:
        print(problem, file=sys.stderr)
    return int(bool(problems))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    gate = commands.add_parser('check')
    gate.add_argument('--base', required=True)
    runner = commands.add_parser('run')
    runner.add_argument('--skill', required=True)
    runner.add_argument('--model')
    runner.add_argument('--runtime', choices=('claude', 'codex'), default='claude',
                        help='Codex-only uses the overlay paired runner even for core skills')
    runner.add_argument('--max-cost-usd', type=float)
    runner.add_argument('--run-dir')
    runner.add_argument('--base', default='HEAD')
    commands.add_parser('report')
    args = parser.parse_args(argv)
    try:
        root = project.repo_root()
        settings = config(root)
        skills = inventory(root, settings)
        if hasattr(args, 'base'):
            revision = subprocess.run(['git', 'rev-parse', '--verify', '--end-of-options',
                                       args.base + '^{commit}'], cwd=root, capture_output=True, text=True)
            if revision.returncode:
                raise EvalError(f'invalid base ref: {args.base}')
            args.base = revision.stdout.strip()
        if args.command == 'check':
            findings = check(root, args.base, settings, skills)
            print('\n'.join(findings) if findings else 'Skill eval gate: clean')
            return int(bool(findings))
        if args.command == 'run':
            if args.max_cost_usd is not None:
                number(args.max_cost_usd, '--max-cost-usd')
            return run(root, args, settings, skills)
        print('Skill | Cases | With | Baseline | Δ | Evidence')
        for key, item in sorted(skills.items()):
            result = latest(key, item)
            if result:
                stale = not fresh(result, key, item)
                scores = result['cases']
                print(f'{key} | {len(scores)} | {sum(c["score_with"] for c in scores)/len(scores):.3f} | '
                      f'{sum(c["score_without"] for c in scores)/len(scores):.3f} | '
                      f'{sum(c["delta"] for c in scores)/len(scores):+.3f} | {"STALE" if stale else "fresh"}')
            else:
                print(f'{key} | {len(item["cases"])} | — | — | — | missing')
        return 0
    except (EvalError, project.OverlayError, StateError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f'skill_evals: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
