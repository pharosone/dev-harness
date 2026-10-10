#!/usr/bin/env python3
"""Validate sourced benchmark observations and render offline SVG reports (stdlib)."""
import argparse
import collections
import csv
import datetime as dt
import html
import json
import math
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
TRAITS = frozenset(('repo_edit', 'terminal', 'polyglot', 'algorithmic',
                    'tool_workflow', 'browser', 'review', 'long_context',
                    'factual', 'instruction', 'web_research'))
ID = re.compile(r'^[a-z0-9][a-z0-9._-]*$')
REQUIRED = {
    'models': ('id', 'vendor', 'family', 'runtime', 'aliases', 'price', 'context', 'url', 'as_of'),
    'benchmarks': ('id', 'name', 'description', 'task_traits', 'normalization', 'unit',
                   'active', 'saturation_note', 'url'),
    'results': ('id', 'model_id', 'benchmark_id', 'value', 'unit', 'setting', 'harness',
                'date', 'url', 'self_reported', 'as_of'),
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('expected YYYY-MM-DD')
    return dt.date.fromisoformat(value)


def url(value):
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def load_dataset(data_dir=ROOT / 'data' / 'benchmarks'):
    """Load contract envelopes; no network calls, and no taxonomy ownership needed."""
    dataset = {key: json.loads((Path(data_dir) / (key + '.json')).read_text())
               for key in REQUIRED}
    effort_path = Path(data_dir) / 'efforts.json'
    if effort_path.exists():
        dataset['efforts'] = json.loads(effort_path.read_text())
    return dataset


def validate_dataset(dataset, today=None):
    """Return errors/warnings; malformed JSON shapes are diagnostics, not exceptions."""
    today = date(today) if isinstance(today, str) else today or dt.date.today()
    errors, warnings, tables, snapshots = [], [], {}, {}
    if not isinstance(dataset, dict):
        return {'errors': ['dataset must be an object'], 'warnings': []}

    def check_date(value, label, snapshot=None):
        try:
            parsed = date(value)
        except (ValueError, TypeError):
            errors.append(label + ': invalid ISO date')
            return None
        if parsed > today:
            errors.append(label + ': future date')
        if snapshot is not None and parsed > snapshot:
            errors.append(label + ': after snapshot')
        if (today - parsed).days > 60:
            warnings.append(label + ': stale (>60 days)')
        return parsed

    for key, required in REQUIRED.items():
        envelope = dataset.get(key)
        if not isinstance(envelope, dict):
            errors.append(key + ': missing object envelope')
            tables[key] = []
            continue
        if type(envelope.get('schema_version')) is not int or envelope['schema_version'] != 1:
            errors.append(key + ': schema_version must be 1')
        snapshots[key] = check_date(envelope.get('as_of'), key + '.as_of')
        rows = envelope.get(key)
        if not isinstance(rows, list):
            errors.append(key + ': expected list')
            rows = []
        tables[key] = []
        seen = set()
        for n, row in enumerate(rows):
            label = f'{key}[{n}]'
            if not isinstance(row, dict):
                errors.append(label + ': expected object')
                continue
            tables[key].append(row)
            for field in required:
                if field not in row:
                    errors.append(label + ': missing ' + field)
            rid = row.get('id')
            if not isinstance(rid, str) or not ID.fullmatch(rid):
                errors.append(label + ': invalid id')
            elif rid in seen:
                errors.append(label + ': duplicate id ' + rid)
            else:
                seen.add(rid)
            if not url(row.get('url')):
                errors.append(label + ': invalid source URL')
            if key != 'benchmarks':
                check_date(row.get('as_of'), label + '.as_of', snapshots[key])

    if len(set(x for x in snapshots.values() if x is not None)) > 1:
        errors.append('envelope as_of dates must agree')
    model_index = {r['id']: r for r in tables['models'] if isinstance(r.get('id'), str)}
    benchmark_index = {r['id']: r for r in tables['benchmarks'] if isinstance(r.get('id'), str)}
    aliases = {}
    for row in tables['models']:
        label = 'model ' + str(row.get('id'))
        for field in ('vendor', 'family'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(label + ': invalid ' + field)
        if row.get('runtime') not in ('claude', 'codex', 'planned', 'none'):
            errors.append(label + ': invalid runtime')
        if row.get('runtime') == 'planned':
            if row.get('planned_runtime') not in ('gemini', 'grok'):
                errors.append(label + ': planned runtime requires gemini/grok planned_runtime')
            if not isinstance(row.get('availability_note'), str) or not row['availability_note'].strip():
                errors.append(label + ': planned runtime requires availability_note')
        elif row.get('planned_runtime') is not None:
            errors.append(label + ': planned_runtime requires runtime=planned')
        items = row.get('aliases')
        if not isinstance(items, list) or any(not isinstance(a, str) or not a.strip() for a in items):
            errors.append(label + ': invalid aliases')
        else:
            for alias in items + ([row['id']] if isinstance(row.get('id'), str) else []):
                if alias in aliases and aliases[alias] != row.get('id'):
                    errors.append(label + ': alias collision ' + alias)
                aliases[alias] = row.get('id')
            if len(items) != len(set(items)):
                errors.append(label + ': duplicate aliases')
        price = row.get('price')
        if not isinstance(price, dict) or price.get('unit') != 'USD/Mtok':
            errors.append(label + ': invalid price object/unit')
        else:
            for field in ('input', 'output'):
                if field not in price or (price[field] is not None and
                        (not finite(price[field]) or price[field] < 0)):
                    errors.append(label + ': invalid price ' + field)
        if row.get('context') is not None and (type(row['context']) is not int or row['context'] <= 0):
            errors.append(label + ': invalid context')
        for field in ('price_url', 'context_url', 'availability_url'):
            if field in row and not url(row[field]):
                errors.append(label + ': invalid ' + field)
        fallback = row.get('fallback_models', [])
        if not isinstance(fallback, list):
            errors.append(label + ': fallback_models must be list')
        else:
            for target in fallback:
                if not isinstance(target, str) or target not in model_index:
                    errors.append(label + ': unknown fallback model')
                elif target == row.get('id') or model_index[target].get('vendor') != row.get('vendor'):
                    errors.append(label + ': fallback must be another same-vendor model')

    for row in tables['benchmarks']:
        label = 'benchmark ' + str(row.get('id'))
        for field in ('name', 'description', 'unit', 'saturation_note'):
            if not isinstance(row.get(field), str) or not row[field].strip():
                errors.append(label + ': invalid ' + field)
        traits = row.get('task_traits')
        if not isinstance(traits, list) or any(not isinstance(t, str) or t not in TRAITS for t in traits):
            errors.append(label + ': invalid task_traits')
        if type(row.get('active')) is not bool:
            errors.append(label + ': active must be boolean')
        norm = row.get('normalization')
        if not isinstance(norm, dict) or not finite(norm.get('min')) or not finite(norm.get('max')):
            errors.append(label + ': invalid normalization')
        elif norm['min'] >= norm['max'] or type(norm.get('higher_is_better')) is not bool:
            errors.append(label + ': invalid normalization bounds/direction')

    observations = set()
    covered = set()
    for row in tables['results']:
        label = 'result ' + str(row.get('id'))
        mid, bid = row.get('model_id'), row.get('benchmark_id')
        if not isinstance(mid, str) or mid not in model_index:
            errors.append(label + ': unknown model_id')
        else:
            covered.add(mid)
        benchmark = benchmark_index.get(bid) if isinstance(bid, str) else None
        if benchmark is None:
            errors.append(label + ': unknown benchmark_id')
        elif row.get('unit') != benchmark.get('unit'):
            errors.append(label + ': unit mismatch')
        if not finite(row.get('value')):
            errors.append(label + ': value must be finite number')
        elif benchmark and isinstance(benchmark.get('normalization'), dict):
            norm = benchmark['normalization']
            if finite(norm.get('min')) and finite(norm.get('max')) and not norm['min'] <= row['value'] <= norm['max']:
                warnings.append(label + ': outside fixed normalization display range')
        if type(row.get('self_reported')) is not bool:
            errors.append(label + ': self_reported must be boolean')
        for field in ('setting', 'harness'):
            if row.get(field) is not None and (not isinstance(row[field], str) or not row[field].strip()):
                errors.append(label + ': invalid ' + field)
        observed = check_date(row.get('as_of'), label + '.as_of')
        check_date(row.get('date'), label + '.date', observed)
        if row.get('date_type') == 'observation' and row.get('date') != row.get('as_of'):
            errors.append(label + ': observation date must equal as_of')
        if 'date_type' in row and row['date_type'] not in ('observation', 'publication', 'evaluation'):
            errors.append(label + ': invalid date_type')
        if row.get('effort') is not None and row['effort'] not in ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
            errors.append(label + ': invalid effort')
        for field in ('vote_count', 'session_count', 'observation_count'):
            if row.get(field) is not None and (type(row[field]) is not int or row[field] < 0):
                errors.append(label + ': invalid ' + field)
        ci = row.get('confidence_interval')
        if ci is not None:
            if not isinstance(ci, dict):
                errors.append(label + ': confidence_interval must be object/null')
            elif any(ci.get(f) is not None and not finite(ci[f]) for f in ('lower', 'upper')):
                errors.append(label + ': nonfinite confidence interval')
            elif finite(ci.get('lower')) and finite(ci.get('upper')) and (
                    ci['lower'] > ci['upper'] or (finite(row.get('value')) and not ci['lower'] <= row['value'] <= ci['upper'])):
                errors.append(label + ': inconsistent confidence interval')
        if row.get('confidence_level') is not None and (not finite(row['confidence_level']) or not 0 < row['confidence_level'] < 1):
            errors.append(label + ': invalid confidence_level')
        for field in ('reported_error_pp', 'standard_error'):
            if row.get(field) is not None and (not finite(row[field]) or row[field] < 0):
                errors.append(label + ': invalid ' + field)
        if row.get('n_samples') is not None and (type(row['n_samples']) is not int or row['n_samples'] < 0):
            errors.append(label + ': invalid n_samples')
        if row.get('uncertainty_kind') == 'source_error_unverified' and (
                row.get('confidence_interval') is not None or row.get('confidence_level') is not None):
            errors.append(label + ': unverified error must not become confidence interval/level')
        if row.get('style_control') not in (None, 'enabled', 'disabled', 'not_offered'):
            errors.append(label + ': invalid style_control')
        if 'source_snapshot_sha256' in row and (not isinstance(row['source_snapshot_sha256'], str) or
                not re.fullmatch(r'[0-9a-f]{64}', row['source_snapshot_sha256'])):
            errors.append(label + ': invalid source_snapshot_sha256')
        if 'comparison_group' in row and (not isinstance(row['comparison_group'], str) or not row['comparison_group'].strip()):
            errors.append(label + ': invalid comparison_group')
        if benchmark and benchmark.get('category') in ('preference', 'observational_agent'):
            for field in ('confidence_interval', 'vote_count', 'style_control', 'statistic'):
                if field not in row:
                    errors.append(label + ': Arena row missing ' + field)
            missing = row.get('missing_metadata', {})
            if not isinstance(missing, dict):
                errors.append(label + ': missing_metadata must be object')
                missing = {}
            nullable = ['vote_count', 'style_control', 'confidence_level']
            if not isinstance(ci, dict) or ci.get('lower') is None or ci.get('upper') is None:
                nullable.append('confidence_interval')
            for field in nullable:
                if row.get(field) is None and (not isinstance(missing.get(field), str) or not missing[field].strip()):
                    errors.append(label + ': unexplained null ' + field)
            if benchmark['category'] == 'observational_agent':
                if row.get('vote_count') is not None:
                    errors.append(label + ': observational_agent must not use pairwise vote_count')
                if row.get('unit') != 'percentage_points':
                    errors.append(label + ': observational_agent net improvement unit must be percentage_points')
                for field in ('session_count', 'observation_count'):
                    if row.get(field) is None:
                        errors.append(label + ': observational_agent missing ' + field)
        fingerprint = tuple(str(row.get(k)) for k in ('model_id', 'benchmark_id', 'setting', 'harness', 'date', 'url'))
        if fingerprint in observations:
            errors.append(label + ': duplicate observation')
        observations.add(fingerprint)
    for mid in sorted(set(model_index) - covered):
        warnings.append('model ' + mid + ': no sourced results')
    if 'efforts' in dataset:
        errors.extend(validate_efforts(dataset['efforts'], model_index))
        if isinstance(dataset['efforts'], dict) and dataset['efforts'].get('as_of') != dataset.get('models', {}).get('as_of'):
            errors.append('efforts: as_of must match model snapshot')
    return {'errors': errors, 'warnings': sorted(set(warnings))}


EFFORT_ORDER = ('none', 'low', 'medium', 'high', 'xhigh', 'max')
EFFORT_METRICS = ('aa_intelligence_v4_3_2', 'output_tokens_per_task',
                 'reasoning_tokens_per_task', 'answer_tokens_per_task',
                 'derived_decode_seconds_per_task', 'api_cost_usd_per_task')


def validate_efforts(envelope, models):
    errors = []
    if not isinstance(envelope, dict) or not isinstance(envelope.get('rows'), list):
        return ['efforts: expected object envelope with rows list']
    if type(envelope.get('schema_version')) is not int or envelope['schema_version'] != 1:
        errors.append('efforts: schema_version must be 1')
    try:
        date(envelope.get('as_of'))
    except (ValueError, TypeError):
        errors.append('efforts: invalid as_of')
    if envelope.get('effort_order') != list(EFFORT_ORDER):
        errors.append('efforts: categorical order must be none/low/medium/high/xhigh/max')
    seen = set()
    for row in envelope['rows']:
        if not isinstance(row, dict):
            errors.append('efforts: row must be object')
            continue
        mid, effort = row.get('model_id'), row.get('effort')
        if not isinstance(mid, str) or mid not in models or effort not in EFFORT_ORDER:
            errors.append('efforts: invalid model/effort')
            continue
        key = (mid, effort)
        if key in seen:
            errors.append('efforts: duplicate model/effort')
        seen.add(key)
        if row.get('runtime') != models[mid].get('runtime'):
            errors.append('efforts: runtime differs from model catalog')
        if row.get('supported') is not None and type(row['supported']) is not bool:
            errors.append('efforts: supported must be boolean/null')
        expected = 'supported' if row.get('supported') is True else 'unsupported' if row.get('supported') is False else 'unverified'
        if row.get('support_status') != expected or not url(row.get('support_url')):
            errors.append('efforts: invalid support status/source')
        if type(row.get('observed')) is not bool:
            errors.append('efforts: observed must be boolean')
        for field in EFFORT_METRICS:
            if field not in row or (row[field] is not None and (not finite(row[field]) or row[field] < 0)):
                errors.append('efforts: invalid ' + field)
        if not row.get('observed') and any(row.get(field) is not None for field in EFFORT_METRICS):
            errors.append('efforts: missing observation must not contain copied metrics')
        if row.get('observed') and not url(row.get('source_url')):
            errors.append('efforts: observation missing source URL')
        if row.get('input_tokens_per_task') is not None:
            errors.append('efforts: input/task denominator unverified; preserve null')
        if row.get('quota_calibration_status') == 'unmeasured' and any(row.get(field) is not None for field in
                ('subscription_quota_per_task', 'lead_wall_seconds', 'failure_rework_probability')):
            errors.append('efforts: unmeasured quota/lead/rework metric must remain null')
    if seen != {(mid, effort) for mid in models for effort in EFFORT_ORDER}:
        errors.append('efforts: all categorical model/effort slots must remain visible')
    return errors


def validate(data_dir=ROOT / 'data' / 'benchmarks', today=None):
    return validate_dataset(load_dataset(data_dir), today)


def normalize(value, benchmark):
    """Fixed source-metric domain, never a per-model/ranking rescale."""
    norm = benchmark['normalization']
    if not finite(value) or not finite(norm['min']) or not finite(norm['max']) or norm['min'] >= norm['max']:
        raise ValueError('invalid normalization input')
    fraction = max(0.0, min(1.0, (value - norm['min']) / (norm['max'] - norm['min'])))
    return fraction if norm['higher_is_better'] else 1.0 - fraction


def svg_start(width, height, title, description):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            '<title id="title">' + html.escape(title) + '</title>',
            '<desc id="desc">' + html.escape(description) + '</desc>',
            '<defs><pattern id="hatch" width="7" height="7" patternUnits="userSpaceOnUse"><rect width="7" height="7" fill="#dbeafe"/><path d="M-2 2L2-2M0 7L7 0M5 9L9 5" stroke="#1e3a8a" stroke-width="1.5"/></pattern></defs>',
            f'<rect width="{width}" height="{height}" fill="#fff"/>',
            '<g font-family="Arial, sans-serif" font-size="13" fill="#172033">']


def text(x, y, value, size=13, extra=''):
    return f'<text x="{x}" y="{y}" font-size="{size}" {extra}>{html.escape(str(value))}</text>'


def bar_chart(benchmark, rows):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[(row.get('comparison_group') or 'Unspecified cohort', row.get('effort') or 'effort unspecified')].append(row)
    height = 155 + len(rows) * 34 + len(groups) * 44
    parts = svg_start(1120, height, benchmark['name'],
                      'Every sourced observation, grouped by cohort and reasoning effort. Solid independent, hatched vendor-hosted; black lines are score confidence intervals.')
    parts += [text(24, 30, benchmark['name'], 20), text(24, 54, f"Raw {benchmark['unit']}; {'higher' if benchmark['normalization']['higher_is_better'] else 'lower'} is better. Efforts and scaffolds are separate."),
              text(24, 77, 'Solid: independent. Hatched: self-reported/vendor-hosted. CI: source score interval. No rank intervals.')]
    y = 104
    low, high = benchmark['normalization']['min'], benchmark['normalization']['max']
    def xpos(value):
        return 430 + 540 * max(0, min(1, (value - low) / (high - low)))
    origin = xpos(0) if low < 0 < high else 430
    if low < 0 < high:
        parts.append(f'<path d="M{origin:.2f} 95V{height-42}" stroke="#6b7280" stroke-dasharray="3 3"/>')
    if not rows:
        parts.append(text(24, y + 25, 'No verified observations for retained models. Missing is not zero.', 16))
    for (group, effort), members in sorted(groups.items()):
        label = group if len(group) <= 115 else group[:112] + '...'
        parts.append(text(24, y, effort + ' | ' + label, 12))
        y += 25
        members.sort(key=lambda r: (-r['value'] if benchmark['normalization']['higher_is_better'] else r['value'], r['model_id']))
        for row in members:
            tooltip = json.dumps({k: row.get(k) for k in ('model_id', 'value', 'unit', 'setting', 'harness', 'date', 'url', 'vote_count', 'session_count', 'n_samples', 'reported_error_pp', 'standard_error', 'confidence_interval')}, ensure_ascii=False)
            parts.append('<g><title>' + html.escape(tooltip) + '</title>')
            parts.append(text(24, y + 15, row['model_id']))
            value_x = xpos(row['value'])
            width = abs(value_x - origin)
            fill = 'url(#hatch)' if row['self_reported'] else '#1d4ed8'
            parts.append(f'<rect x="{min(origin, value_x):.2f}" y="{y}" width="{width:.2f}" height="21" fill="{fill}"/>')
            ci = row.get('confidence_interval') or {}
            if finite(ci.get('lower')) and finite(ci.get('upper')):
                left, right = xpos(ci['lower']), xpos(ci['upper'])
                parts.append(f'<path d="M{left:.2f} {y+10}H{right:.2f}M{left:.2f} {y+5}V{y+15}M{right:.2f} {y+5}V{y+15}" stroke="#111827" stroke-width="2"/>')
            label = f"{row['value']:.2f}"
            if row.get('reported_error_pp') is not None:
                label += f" ±{row['reported_error_pp']:.1f}*"
            parts.append(text(980, y + 15, label))
            parts.append('</g>')
            y += 34
        y += 19
    parts += [text(430, height - 22, low), text(970, height - 22, high), '</g></svg>']
    if any(r.get('reported_error_pp') is not None for r in rows):
        parts.insert(-1, text(24, height - 44, '* Published ±error; confidence level/estimator unverified. n_samples is not successes.', 12))
    if low < 0 < high:
        parts.insert(-1, text(origin, height - 22, '0'))
    return '\n'.join(parts) + '\n'


def heatmap(models, benchmarks, rows):
    """One row per exact model/effort, avoiding any best-effort aggregation."""
    variants = sorted({(r['model_id'], r.get('effort') or 'unspecified') for r in rows})
    lookup = collections.defaultdict(list)
    for row in rows:
        lookup[(row['model_id'], row.get('effort') or 'unspecified', row['benchmark_id'])].append(row)
    width, height = 330 + 72 * len(benchmarks), 315 + 28 * len(variants)
    parts = svg_start(width, height, 'Model × benchmark coverage by exact effort',
                      'Gray cells are missing; normalized fixed-domain values are shaded blue. Models repeat per effort; multiple observations are not averaged. Hatched cells are vendor-hosted.')
    parts += [text(24, 28, 'Benchmark observations by model and exact effort', 20),
              text(24, 53, 'Gray = missing; hatch = self-reported; * = multiple observations, inspect chart/JSON. Values use fixed benchmark domains.'),
              text(24, 75, 'Each row preserves effort. Operational and preference columns are different metrics; colors do not imply one universal capability.')]
    for j, benchmark in enumerate(benchmarks):
        x = 330 + j * 72
        parts.append(text(x + 14, 265, benchmark['id'], 11, f'transform="rotate(-60 {x+14} 265)"'))
    for i, (mid, effort) in enumerate(variants):
        y = 285 + i * 28
        parts.append(text(24, y + 18, mid + ' / ' + effort, 12))
        for j, benchmark in enumerate(benchmarks):
            x = 330 + j * 72
            observations = lookup.get((mid, effort, benchmark['id']), [])
            fill, label = '#e5e7eb', '—'
            if observations:
                # Stable display representative: prefer independent, latest snapshot, then ID.
                row = sorted(observations, key=lambda r: (r['self_reported'], -date(r['as_of']).toordinal(), r['id']))[0]
                quality = normalize(row['value'], benchmark)
                shade = int(245 - quality * 125)
                fill = 'url(#hatch)' if row['self_reported'] else f'rgb({shade},{min(255, shade+12)},255)'
                label = f"{row['value']:.1f}" + ('*' if len(observations) > 1 else '')
            tip = json.dumps(observations, ensure_ascii=False)
            parts.append(f'<g><title>{html.escape(tip)}</title><rect x="{x}" y="{y}" width="70" height="26" fill="{fill}"/>' + text(x + 5, y + 18, label, 11) + '</g>')
    parts.append('</g></svg>')
    return '\n'.join(parts) + '\n'


def effort_curve(model, rows):
    """Categorical per-model trajectories; missing adjacent points break each line."""
    by_effort = {row['effort']: row for row in rows}
    parts = svg_start(1120, 1380, model['name'] + ': effort evidence',
                      'Four panels: AA Intelligence v4.3.2, weighted tokens/task, derived decode seconds/task, API cost/task. Effort categories are ordinal per model. Missing data is not zero and is not interpolated.')
    parts += [text(28, 30, model['name'] + ' — effort evidence', 22),
              text(28, 54, 'AA suite proxies. Decode time excludes first-token delay and overhead. API cost and tokens are not subscription quota.'),
              text(28, 77, 'Each effort is categorical within this model; identical names across models do not imply equal work. Gaps break lines.')]
    panels = [('AA Intelligence Index v4.3.2 (fixed 0–100)', [('aa_intelligence_v4_3_2', '#1d4ed8', 'Index')], 100),
              ('Weighted output tokens per suite task', [('output_tokens_per_task', '#1d4ed8', 'Total'),
                 ('reasoning_tokens_per_task', '#7e22ce', 'Reasoning'), ('answer_tokens_per_task', '#0f766e', 'Answer')], None),
              ('Derived weighted decode seconds/task — NOT wall-clock task latency', [('derived_decode_seconds_per_task', '#1d4ed8', 'Seconds')], None),
              ('Weighted API USD/task — NOT Max/Codex quota charges', [('api_cost_usd_per_task', '#1d4ed8', 'USD')], None)]
    x_positions = {effort: 150 + n * 170 for n, effort in enumerate(EFFORT_ORDER)}
    for panel_number, (title, series, fixed_top) in enumerate(panels):
        top_y = 130 + panel_number * 300
        values = [row.get(field) for row in rows for field, _, _ in series if finite(row.get(field))]
        largest = max(values, default=0)
        step = 10 ** math.floor(math.log10(largest)) if largest > 0 else 1
        scale_top = fixed_top or max(step, math.ceil(largest * 1.15 / step) * step)
        baseline = top_y + 185
        parts.append(text(28, top_y - 20, title, 17))
        if not values:
            parts.append(text(160, top_y + 70, 'No published values; these slots remain gaps.', 16))
        for tick in range(5) if values else ():
            value = scale_top * tick / 4
            y = baseline - tick * 42
            parts.append(f'<path d="M100 {y}H1050" stroke="#e5e7eb"/>')
            parts.append(text(90, y + 4, f'{value:,.2f}' if scale_top <= 10 else f'{value:,.0f}', 11, 'text-anchor="end"'))
        for index, (field, color, label) in enumerate(series):
            parts.append(text(650 + index * 130, top_y - 20, label, 12, f'fill="{color}"'))
            prior = None
            for effort in EFFORT_ORDER:
                row = by_effort[effort]
                value = row.get(field)
                x = x_positions[effort]
                if not finite(value):
                    prior = None
                    continue
                y = baseline - 168 * value / scale_top
                if prior is not None:
                    parts.append(f'<path d="M{prior[0]} {prior[1]:.2f}L{x} {y:.2f}" fill="none" stroke="{color}" stroke-width="2"/>')
                tooltip = {'model_id': model['id'], 'effort': effort, 'field': field, 'value': value,
                           'source_url': row.get('source_url'), 'support_status': row['support_status']}
                parts.append('<g><title>' + html.escape(json.dumps(tooltip)) + '</title>' +
                             f'<circle cx="{x}" cy="{y:.2f}" r="4" fill="{color}"/></g>')
                if len(series) == 1:
                    number = f'{value:,.2f}' if field == 'api_cost_usd_per_task' else f'{value:,.1f}'
                    parts.append(text(x, y - 10, number, 12, 'text-anchor="middle"'))
                prior = (x, y)
        for effort in EFFORT_ORDER:
            x = x_positions[effort]
            row = by_effort[effort]
            parts.append(text(x, baseline + 22, effort, 13, 'text-anchor="middle"'))
            available = any(finite(row.get(field)) for field, _, _ in series)
            status = row['support_status']
            if not available:
                status = 'unsupported' if status == 'unsupported' else 'unmeasured' if status == 'supported' else 'unverified / gap'
            elif status == 'unverified':
                status = 'observed; support unverified'
            parts.append(text(x, baseline + 40, status, 10, 'text-anchor="middle"'))
    parts += [text(28, 1360, 'Input/task, subscription quota, lead wall time, failure/rework remain unmeasured. Source suite totals are not divided.'), '</g></svg>']
    return '\n'.join(parts) + '\n'


def render_efforts(envelope, models, out):
    rows = envelope['rows']
    files = []
    for model in models:
        target = out / 'charts' / ('effort-' + model['id'] + '.svg')
        target.write_text(effort_curve(model, [r for r in rows if r['model_id'] == model['id']]))
        files.append(str(target.relative_to(out)))
    fields = ['runtime', 'target_runtime', 'model_id', 'effort', 'supported', 'support_status', 'observed',
              *EFFORT_METRICS, 'input_tokens_per_task', 'subscription_quota_per_task',
              'lead_wall_seconds', 'failure_rework_probability', 'quota_calibration_status',
              'tokens_method', 'time_method', 'cost_method', 'source_url', 'source_snapshot_sha256', 'support_url']
    with (out / 'effort-curves.csv').open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction='ignore', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)
    lines = ['# Effort curves', '', 'One model per figure, with categorical none/low/medium/high/xhigh/max slots. Lines only join adjacent measured categories; they are visual guides, not interpolation or monotonic fits. Quality uses the original fixed 0–100 AA index, never each model’s best score as a denominator.', '',
             '[Complete tuple values and method flags](effort-curves.csv) · [Evidence JSON](../../data/benchmarks/efforts.json) · [Source definitions](../../data/benchmarks/SOURCES.md)', '',
             'API-documented effort support is separate from local CLI flag/account verification. Gemini/Grok runtimes remain planned. Argon high is an AA observed setting; its complete publisher effort matrix is unverified. Unsupported, supported-but-unmeasured, and unverified settings remain visible.', '',
             'AA output/reasoning/answer tokens per task are benchmark-weighted suite measurements. Raw canonical suite totals are retained in JSON; input/task is not inferred from an unknown denominator. Decode time is a source-derived estimate from output-token use and speed, weighted across benchmarks; it excludes first-token delay and overhead. It is neither elapsed task latency nor a 12-minute lead-research measurement.', '',
             'API USD/task incorporates token prices and caching assumptions. It is not Claude Max/Codex subscription quota. Subscription quota, failure/rework, input/task and lead wall time remain null pending calibration. The plan’s quality bands and expected-total-cost choices are outside this reporting code.', '',
             '| Model | API-supported settings | AA observations | Decode-time observations |', '|---|---|---:|---:|']
    for model in models:
        subset = [r for r in rows if r['model_id'] == model['id']]
        supported = ', '.join(r['effort'] for r in subset if r['supported'] is True) or 'full matrix unverified'
        lines.append(f"| {model['id']} | {supported} | {sum(r['observed'] for r in subset)} | {sum(r['derived_decode_seconds_per_task'] is not None for r in subset)} |")
    for model in models:
        lines += ['', '## ' + model['name'], '', f"![{model['name']} effort observations](charts/effort-{model['id']}.svg)", '']
    (out / 'effort-curves.md').write_text('\n'.join(lines))
    return files


def render(dataset, output_dir=ROOT / 'docs' / 'benchmarks', today=None):
    report = validate_dataset(dataset, today)
    if report['errors']:
        raise ValueError('\n'.join(report['errors']))
    out = Path(output_dir)
    charts = out / 'charts'
    charts.mkdir(parents=True, exist_ok=True)
    models, benchmarks, results = (dataset[k][k] for k in REQUIRED)
    files = []
    for benchmark in benchmarks:
        target = charts / (benchmark['id'] + '.svg')
        target.write_text(bar_chart(benchmark, [r for r in results if r['benchmark_id'] == benchmark['id']]))
        files.append(str(target.relative_to(out)))
    target = charts / 'heatmap.svg'
    target.write_text(heatmap(models, benchmarks, results))
    files.append(str(target.relative_to(out)))
    if 'efforts' in dataset:
        files.extend(render_efforts(dataset['efforts'], models, out))
    # Prune only paths owned by a previous renderer manifest; preserve router-eval/other docs.
    manifest = out / 'charts-manifest.json'
    if manifest.exists():
        for old in json.loads(manifest.read_text()).get('files', []):
            if isinstance(old, str) and re.fullmatch(r'charts/[a-z0-9][a-z0-9._-]*\.svg', old) and old not in files:
                (out / old).unlink(missing_ok=True)
    manifest.write_text(json.dumps({'files': files}, indent=2) + '\n')
    with (out / 'coverage.csv').open('w', newline='') as handle:
        writer = csv.writer(handle, lineterminator='\n')
        writer.writerow(['model_id'] + [b['id'] for b in benchmarks])
        for model in models:
            cells = []
            for benchmark in benchmarks:
                observations = [r for r in results if r['model_id'] == model['id'] and r['benchmark_id'] == benchmark['id']]
                cells.append('; '.join(f"{r.get('effort') or 'unspecified'}={r['value']:.6g} ({'self' if r['self_reported'] else 'independent'})" for r in observations))
            writer.writerow([model['id']] + cells)
    independent = sum(not r['self_reported'] for r in results)
    readme = ['# Benchmark evidence', '', f"Snapshot: {dataset['models']['as_of']}. {len(models)} models, {len(results)} observations: {independent} independent and {len(results)-independent} vendor-hosted/self-reported.", '',
              'Scope: latest retained Claude 5, GPT-6, Gemini 4 Argon, and Grok 4.7 classes only. Gemini/Grok runtime integration is planned. Earlier generations, superseded revisions and other vendors are excluded.', '',
              'Evidence only: model routing and classifier evaluation are not implemented by this dataset. `active` is proposed eligibility, not a deployed routing claim. [Source methods and gaps](../../data/benchmarks/SOURCES.md) explain comparability and scope.', '',
              '1. Validate: `python3 scripts/benchmarks.py validate --as-of 2026-10-10`',
              '2. Re-render offline: `python3 scripts/benchmarks.py render --as-of 2026-10-10`',
              '3. Test: `python3 -m unittest discover -s tests -p test_benchmarks.py -v`', '',
              'Refresh requires re-reading primary sources and updating JSON/provenance first. Rendering never fetches new scores. Staleness is >60 days since publication/evaluation or observed snapshot; a fresh observation does not establish a fresh evaluation execution date.', '',
              'Bars preserve effort and cohort. Solid bars are independent, hatched bars vendor-hosted. Black error lines show score intervals; confidence level is unknown unless provided. No score is copied across models or efforts. Lower-is-better metrics retain their raw bar direction.', '',
              'Arena Text/WebDev/Vision measure human preference; planned design caps relevant preference at 15%. Search has no retained rows. LongerQuery is report-only and does not prove long-context retrieval. Agent Code net improvement is a separate observational causal-effect statistic, report-only, with 95% CIs and session counts instead of votes.', '',
              'Deep web research evidence uses the v1.1 `web_research` trait. DRACO report rubrics, WANDR frozen-index supported-fact soft F1, Mercor BrowseComp subsets and Epoch closed-book factuality retain distinct protocols. None establishes 10–15-minute lead-dossier success. Mercor ±error is unverified uncertainty; its n_samples counts are not successes. Missing product-to-base-model identity remains a gap.', '',
              '## Coverage', '', '| Model | Runtime | Filled benchmarks | Independent rows | Self-reported rows |', '|---|---|---:|---:|---:|']
    for model in models:
        rows = [r for r in results if r['model_id'] == model['id']]
        readme.append(f"| {model['id']} | {model['runtime']} | {len({r['benchmark_id'] for r in rows})}/{len(benchmarks)} | {sum(not r['self_reported'] for r in rows)} | {sum(r['self_reported'] for r in rows)} |")
    readme += ['', '[Complete model × benchmark values, with efforts and source kinds](coverage.csv)', '',
               '## Heatmap', '', 'One model row per exact effort; no best-effort average. Missing cells are gray. Multiple observations have `*`; open the benchmark chart or JSON for all rows.', '', '![Model × benchmark observations](charts/heatmap.svg)', '', '## Benchmark charts', '']
    if 'efforts' in dataset:
        readme[4:4] = ['[Per-model effort curves and resource proxies](effort-curves.md)', '']
    for benchmark in benchmarks:
        count = sum(r['benchmark_id'] == benchmark['id'] for r in results)
        readme += [f"### {benchmark['name']}", '', f"{benchmark['description']} {benchmark['saturation_note']} ({count} observations; {'proposed eligible' if benchmark['active'] else 'report-only'}; {benchmark.get('category', 'unspecified')}).", '',
                   f"![{benchmark['name']}](charts/{benchmark['id']}.svg)", '']
    (out / 'README.md').write_text('\n'.join(readme))
    return {'charts': len(files), 'models': len(models), 'results': len(results), **report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('validate', 'render'), default='validate')
    parser.add_argument('--data-dir', type=Path, default=ROOT / 'data' / 'benchmarks')
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'docs' / 'benchmarks')
    parser.add_argument('--as-of', help='Deterministic validation date (YYYY-MM-DD); defaults to today')
    args = parser.parse_args(argv)
    try:
        dataset = load_dataset(args.data_dir)
        report = validate_dataset(dataset, args.as_of)
        if args.command == 'render' and not report['errors']:
            report = render(dataset, args.output_dir, args.as_of)
    except (OSError, ValueError, TypeError) as exc:
        report = {'errors': [str(exc)], 'warnings': []}
    print(json.dumps(report, indent=2))
    return 1 if report['errors'] else 0


if __name__ == '__main__':
    sys.exit(main())
