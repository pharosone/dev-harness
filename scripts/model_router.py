#!/usr/bin/env python3
"""Pure, opt-in shadow scoring of exact model/effort tuples. No dispatch or model calls."""
from __future__ import annotations

import copy
import hashlib
import json
import math
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.dont_write_bytecode = True
import project

CORE = Path(__file__).resolve().parents[1]
EFFORTS = ('none', 'low', 'medium', 'high', 'xhigh', 'max')
APPROVED = {
    'mechanical': (0, 40, False, .20, 2),
    'routine': (40, 48, False, .45, 10),
    'implementation': (48, 52, False, .50, 30),
    'critical-review': (51.5, 56.5, False, .55, 120),
    'escalation': (52, 100, True, .58, 240),
    'deep-reasoning': (52, 100, True, .58, 240),
}
TIERS = {'0': 2, '1': 10, '2': 30, '3': 120}
TRAITS = ('repo_edit', 'terminal', 'polyglot', 'algorithmic', 'tool_workflow',
          'browser', 'review', 'long_context', 'factual', 'instruction', 'web_research')
TOP_KEYS = {'schema_version', 'policy_version', 'taxonomy_version', 'mode', 'classifier',
            'runtimes', 'profiles', 'reference', 'trait_maps', 'source_weights', 'cost',
            'estimates', 'effort_limits', 'fallbacks'}
COST_KEYS = {'scarcity_exponent', 'runout_coefficient', 'runout_warmup_fraction',
             'multiplier_cap', 'stop_at', 'unknown_used', 'unknown_pressure_index',
             'base_price_cu_per_1k', 'window_base_prices', 'latency_cu_per_minute',
             'failure_prior_min', 'failure_prior_max', 'prior_equivalent_observations',
             'tier_rework_minima', 'rework_human_fraction', 'rework_compute_fraction', 'tie_cu'}
ESTIMATE_KEYS = {'proxy_mode', 'initial_review_cu', 'deadline_minutes', 'followup_shares',
                 'candidates', 'retry'}


class RouterError(ValueError):
    """Invalid policy, data or score arguments; root retains static dispatch."""


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def number(value, name, minimum=0, maximum=None):
    if not finite(value) or value < minimum or (maximum is not None and value > maximum):
        raise RouterError(f'{name}: expected finite number in allowed range')
    return float(value)


def boolean(value, name):
    if type(value) is not bool:
        raise RouterError(f'{name}: expected boolean')
    return value


def object_keys(value, allowed, name, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise RouterError(f'{name}: unknown or missing fields')
    return value


def string(value, name):
    if not isinstance(value, str) or not value.strip():
        raise RouterError(f'{name}: expected nonempty string')
    return value


def strings(value, name, known=None):
    if not isinstance(value, list) or any(not isinstance(x, str) or not x for x in value):
        raise RouterError(f'{name}: expected string list')
    if len(value) != len(set(value)) or (known is not None and set(value) - set(known)):
        raise RouterError(f'{name}: duplicate/unknown values')
    return value


def source_url(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ('http', 'https') and bool(parsed.hostname)
    except ValueError:
        return False


def canonical_hash(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                         allow_nan=False).encode('utf-8')
    except (TypeError, ValueError) as exc:
        raise RouterError('context is not finite JSON') from exc
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RouterError(f'{path}: duplicate JSON key {key}')
            result[key] = value
        return result
    def invalid_constant(value):
        raise RouterError(f'{path}: nonfinite JSON constant {value}')
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=unique,
                          parse_constant=invalid_constant)
    except (OSError, ValueError) as exc:
        raise RouterError(f'{path}: {exc}') from exc


def deep_merge(base, overlay):
    """Only schema-supported keys survive validation; arrays replace explicitly."""
    if not isinstance(overlay, dict):
        raise RouterError('router overlay must be an object')
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if key == 'followup_shares':
            result[key] = copy.deepcopy(value)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def tuple_key(candidate):
    return '/'.join(candidate[k] for k in ('runtime', 'model', 'effort'))


def validate_dataset(dataset):
    if not isinstance(dataset, dict) or set(dataset) != {'models', 'benchmarks', 'results', 'efforts', 'taxonomy_version', 'traits'}:
        raise RouterError('dataset: expected models/benchmarks/results/efforts/taxonomy_version/traits')
    if dataset['taxonomy_version'] != '1.1' or dataset['traits'] != list(TRAITS):
        raise RouterError('dataset: unsupported taxonomy/version')
    indexes = {}
    for name in ('models', 'benchmarks', 'results'):
        rows = dataset[name]
        if not isinstance(rows, list):
            raise RouterError(f'dataset {name}: expected list')
        index = {}
        for row in rows:
            if not isinstance(row, dict):
                raise RouterError(f'dataset {name}: expected row object')
            rid = string(row.get('id'), f'{name}.id')
            if rid in index:
                raise RouterError(f'dataset {name}: duplicate id')
            if not source_url(row.get('url')):
                raise RouterError(f'dataset {name}: missing source URL')
            index[rid] = row
        indexes[name] = index
    for mid, model in indexes['models'].items():
        string(model.get('vendor'), 'model vendor')
        price = object_keys(model.get('price'), {'input', 'output', 'unit'}, 'model price', {'input', 'output', 'unit'})
        if price['unit'] != 'USD/Mtok':
            raise RouterError('model price: unknown unit')
        for field in ('input', 'output'):
            if price[field] is not None:
                number(price[field], 'model price ' + field)
        if model.get('context') is not None and (type(model['context']) is not int or model['context'] <= 0):
            raise RouterError('model context: expected positive integer/null')
        if model.get('runtime') not in ('claude', 'codex', 'planned', 'none'):
            raise RouterError('model: unknown availability')
        if model['runtime'] == 'planned' and model.get('planned_runtime') not in ('gemini', 'grok'):
            raise RouterError('model: planned runtime missing')
    for benchmark in dataset['benchmarks']:
        norm = object_keys(benchmark.get('normalization'), {'min', 'max', 'higher_is_better'}, 'normalization', {'min', 'max', 'higher_is_better'})
        if not finite(norm['min']) or not finite(norm['max']) or norm['max'] <= norm['min']:
            raise RouterError('benchmark: invalid normalization')
        boolean(norm['higher_is_better'], 'higher_is_better')
        boolean(benchmark.get('active'), 'benchmark active')
        string(benchmark.get('unit'), 'benchmark unit')
    effort_index = {}
    if not isinstance(dataset['efforts'], list):
        raise RouterError('efforts: expected list')
    for row in dataset['efforts']:
        if not isinstance(row, dict) or row.get('model_id') not in indexes['models'] or row.get('effort') not in EFFORTS:
            raise RouterError('efforts: invalid model/effort')
        key = (row['model_id'], row['effort'])
        if key in effort_index:
            raise RouterError('efforts: duplicate tuple')
        if row.get('supported') is not None:
            boolean(row['supported'], 'effort supported')
        boolean(row.get('observed'), 'effort observed')
        for field in ('aa_intelligence_v4_3_2', 'output_tokens_per_task', 'reasoning_tokens_per_task',
                      'answer_tokens_per_task', 'derived_decode_seconds_per_task', 'api_cost_usd_per_task',
                      'suite_total_input_tokens', 'suite_total_output_tokens', 'suite_total_reasoning_tokens',
                      'suite_total_answer_tokens', 'input_tokens_per_task', 'subscription_quota_per_task',
                      'lead_wall_seconds', 'failure_rework_probability'):
            if row.get(field) is not None:
                number(row[field], field)
        effort_index[key] = row
    if set(effort_index) != {(mid, effort) for mid in indexes['models'] for effort in EFFORTS}:
        raise RouterError('efforts: missing categorical slots')
    for row in dataset['results']:
        if row.get('model_id') not in indexes['models'] or row.get('benchmark_id') not in indexes['benchmarks']:
            raise RouterError('result: invalid foreign key')
        if not finite(row.get('value')):
            raise RouterError('result: nonfinite/bool value')
        boolean(row.get('self_reported'), 'result self_reported')
        if row.get('effort') is not None and row['effort'] not in EFFORTS:
            raise RouterError('result: invalid effort')
        if row.get('unit') != indexes['benchmarks'][row['benchmark_id']]['unit']:
            raise RouterError('result: unit mismatch')
    canonical_hash(dataset)
    return indexes, effort_index


def in_band(reference, profile):
    return reference >= profile['reference_min'] and (reference <= profile['reference_max']
            if profile['upper_inclusive'] else reference < profile['reference_max'])


def validate_estimates(estimates, policy, models):
    object_keys(estimates, ESTIMATE_KEYS, 'estimates', ESTIMATE_KEYS)
    if estimates['proxy_mode'] not in ('disabled', 'aa_output_decode'):
        raise RouterError('estimates: proxy_mode must be disabled/aa_output_decode')
    for field in ('initial_review_cu', 'deadline_minutes'):
        if estimates[field] is not None:
            number(estimates[field], field)
    shares = estimates['followup_shares']
    if not isinstance(shares, dict) or not shares:
        raise RouterError('followup_shares: expected nonempty usage-bucket weights')
    buckets = {r['usage_bucket'] for r in policy['runtimes'].values()}
    if set(shares) - buckets:
        raise RouterError('followup_shares: unknown usage bucket')
    total = sum(number(v, 'followup share', maximum=1) for v in shares.values())
    if not math.isclose(total, 1, abs_tol=1e-9):
        raise RouterError('followup shares must sum to one')
    records = estimates['candidates']
    if not isinstance(records, dict):
        raise RouterError('candidate estimates: expected object')
    for key, estimate in records.items():
        if not isinstance(key, str) or len(key.split('/')) != 3:
            raise RouterError('candidate estimate: invalid tuple key')
        runtime, model, effort = key.split('/')
        if runtime not in policy['runtimes'] or model not in policy['runtimes'][runtime]['models'] or effort not in EFFORTS:
            raise RouterError('candidate estimate: unknown tuple')
        object_keys(estimate, {'kind', 'elapsed_minutes', 'initial_review_cu', 'quota_windows', 'continuation_feasible', 'followup_window_tokens', 'followup_quota_windows'}, 'candidate estimate', {'kind', 'elapsed_minutes', 'initial_review_cu', 'quota_windows', 'continuation_feasible'})
        if estimate['kind'] not in ('measured', 'bounded'):
            raise RouterError('candidate estimate kind must be measured/bounded')
        number(estimate['elapsed_minutes'], 'elapsed_minutes')
        number(estimate['initial_review_cu'], 'initial_review_cu')
        boolean(estimate['continuation_feasible'], 'continuation_feasible')
        if not isinstance(estimate['quota_windows'], dict) or not estimate['quota_windows']:
            raise RouterError('quota_windows: expected explicit window bounds')
        for name, window in estimate['quota_windows'].items():
            string(name, 'quota window name')
            object_keys(window, {'weighted_tokens', 'upper_fraction', 'reserve_fraction'}, 'quota bound', {'weighted_tokens', 'upper_fraction', 'reserve_fraction'})
            number(window['weighted_tokens'], 'weighted_tokens')
            number(window['upper_fraction'], 'upper_fraction', maximum=1)
            number(window['reserve_fraction'], 'reserve_fraction', maximum=1)
        if 'followup_quota_windows' in estimate:
            followup = estimate['followup_quota_windows']
            if not isinstance(followup, dict) or set(followup) - {b for b, v in shares.items() if v > 0}:
                raise RouterError('followup quota bounds: unknown consumed bucket')
            for bucket, windows in followup.items():
                if not isinstance(windows, dict) or not windows:
                    raise RouterError('followup quota bounds: missing windows')
                for name, bound in windows.items():
                    string(name, 'followup window name')
                    object_keys(bound, {'weighted_tokens', 'upper_fraction', 'reserve_fraction'}, 'followup quota bound', {'weighted_tokens', 'upper_fraction', 'reserve_fraction'})
                    if number(bound['weighted_tokens'], 'followup weighted_tokens') <= 0:
                        raise RouterError('followup normalized tokens must be positive')
                    number(bound['upper_fraction'], 'followup upper_fraction', maximum=1)
                    number(bound['reserve_fraction'], 'followup reserve_fraction', maximum=1)
        if 'followup_window_tokens' in estimate:
            weights = estimate['followup_window_tokens']
            if not isinstance(weights, dict) or set(weights) != {b for b, v in shares.items() if v > 0}:
                raise RouterError('followup window tokens: must cover positive continuation buckets')
            for bucket, windows in weights.items():
                if not isinstance(windows, dict) or not windows:
                    raise RouterError('followup window tokens: missing windows')
                for name, tokens in windows.items():
                    string(name, 'followup window name')
                    if number(tokens, 'followup normalized tokens') <= 0:
                        raise RouterError('followup normalized tokens must be positive')
    retry = estimates['retry']
    if retry is not None:
        object_keys(retry, {'failed_tuple', 'context_changed', 'new_context_reason', 'capability_escalation', 'task_specific_exception', 'exception_reason'}, 'retry', {'failed_tuple', 'context_changed', 'capability_escalation', 'task_specific_exception'})
        validate_tuple(retry['failed_tuple'], policy, models)
        for field in ('context_changed', 'capability_escalation', 'task_specific_exception'):
            boolean(retry[field], field)
        if retry['context_changed']:
            string(retry.get('new_context_reason'), 'new_context_reason')
        if retry['task_specific_exception']:
            string(retry.get('exception_reason'), 'exception_reason')
    canonical_hash(estimates)


def validate_tuple(candidate, policy, models):
    object_keys(candidate, {'runtime', 'model', 'effort'}, 'tuple', {'runtime', 'model', 'effort'})
    if candidate['runtime'] not in policy['runtimes'] or candidate['model'] not in models or candidate['model'] not in policy['runtimes'][candidate['runtime']]['models'] or candidate['effort'] not in EFFORTS:
        raise RouterError('tuple: unknown runtime/model/effort')


def validate_policy(policy, dataset):
    indexes, effort_index = validate_dataset(dataset)
    models = indexes['models']
    object_keys(policy, TOP_KEYS, 'policy', TOP_KEYS)
    if type(policy['schema_version']) is not int or policy['schema_version'] != 1:
        raise RouterError('policy schema_version must be integer 1')
    string(policy['policy_version'], 'policy_version')
    if policy['mode'] not in ('off', 'shadow') or policy['taxonomy_version'] != '1.1':
        raise RouterError('policy accepts only off/shadow and taxonomy 1.1')
    classifier = object_keys(policy['classifier'], {'enabled', 'model', 'effort', 'timeout_seconds', 'max_budget_usd', 'cache'}, 'classifier', {'enabled', 'model', 'effort', 'timeout_seconds', 'max_budget_usd', 'cache'})
    boolean(classifier['enabled'], 'classifier.enabled')
    boolean(classifier['cache'], 'classifier.cache')
    if (classifier['model'], classifier['effort']) not in effort_index or effort_index[classifier['model'], classifier['effort']]['supported'] is not True:
        raise RouterError('classifier: unknown/unsupported model effort')
    if number(classifier['timeout_seconds'], 'classifier timeout') <= 0:
        raise RouterError('classifier timeout must be positive')
    number(classifier['max_budget_usd'], 'classifier max_budget_usd')
    runtimes = policy['runtimes']
    if not isinstance(runtimes, dict) or not runtimes:
        raise RouterError('runtimes: expected registry')
    registered = set()
    for rid, runtime in runtimes.items():
        string(rid, 'runtime ID')
        if '/' in rid:
            raise RouterError('runtime ID cannot contain slash')
        object_keys(runtime, {'availability', 'enabled', 'implemented', 'usage_bucket', 'capabilities', 'command', 'account_status', 'models', 'model_access', 'model_runtime'}, 'runtime', {'availability', 'enabled', 'implemented', 'usage_bucket', 'capabilities', 'command', 'account_status', 'models', 'model_access'})
        if runtime['availability'] not in ('available', 'planned', 'unavailable') or runtime['account_status'] not in ('available', 'unknown', 'unavailable'):
            raise RouterError('runtime: invalid availability/account status')
        boolean(runtime['enabled'], 'runtime enabled')
        boolean(runtime['implemented'], 'runtime implemented')
        string(runtime['usage_bucket'], 'usage_bucket')
        strings(runtime['capabilities'], 'runtime capabilities')
        command = object_keys(runtime['command'], {'argv', 'protocol'}, 'command', {'argv', 'protocol'})
        strings(command['argv'], 'command argv')
        string(command['protocol'], 'command protocol')
        if runtime['availability'] == 'available' and (not runtime['implemented'] or not command['argv']):
            raise RouterError('available runtime requires implemented adapter and command metadata')
        strings(runtime['models'], 'runtime models', models)
        access = runtime['model_access']
        if not isinstance(access, dict) or set(access) - set(runtime['models']):
            raise RouterError('model_access: unknown registry model')
        for mid in runtime['models']:
            if mid in registered:
                raise RouterError('model registered in multiple runtimes')
            registered.add(mid)
            target = models[mid].get('planned_runtime', models[mid]['runtime'])
            if target != runtime.get('model_runtime', rid):
                raise RouterError('runtime model family contract mismatch')
            item = access.get(mid, {'status': 'unknown', 'efforts': None})
            object_keys(item, {'status', 'efforts'}, 'model access', {'status', 'efforts'})
            if item['status'] not in ('available', 'unknown', 'unavailable'):
                raise RouterError('model access: unknown status')
            if item['efforts'] is not None:
                strings(item['efforts'], 'account efforts', EFFORTS)
                if any(effort_index[mid, eff]['supported'] is not True for eff in item['efforts']):
                    raise RouterError('account efforts cannot invent API support')
    if set(policy['profiles']) != set(APPROVED):
        raise RouterError('profiles: unknown or missing profile')
    for name, values in APPROVED.items():
        prof = object_keys(policy['profiles'][name], {'reference_min', 'reference_max', 'upper_inclusive', 'quality_floor', 'rework_cu'}, 'profile', {'reference_min', 'reference_max', 'upper_inclusive', 'quality_floor', 'rework_cu'})
        lo, hi = number(prof['reference_min'], 'reference_min', maximum=100), number(prof['reference_max'], 'reference_max', maximum=100)
        boolean(prof['upper_inclusive'], 'upper_inclusive')
        floor = number(prof['quality_floor'], 'quality_floor', maximum=1)
        rework = number(prof['rework_cu'], 'rework_cu')
        if lo >= hi or lo < values[0] or hi > values[1] or (hi == values[1] and prof['upper_inclusive'] and not values[2]) or floor < values[3] or rework < values[4]:
            raise RouterError('profile overlay cannot bypass approved band/floor/rework minimum')
    ref = object_keys(policy['reference'], {'benchmark_id', 'comparison_group', 'harness', 'unit', 'as_of'}, 'reference', {'benchmark_id', 'comparison_group', 'harness', 'unit', 'as_of'})
    if ref['benchmark_id'] != 'aa-intelligence-v4.3.2' or ref['unit'] != 'index':
        raise RouterError('only approved AA v4.3.2 admission reference supported')
    for field in ('comparison_group', 'harness', 'as_of'):
        string(ref[field], field)
    if policy['source_weights'] != {'independent': 1, 'vendor': 0, 'preference': 0} or any(type(x) is not int for x in policy['source_weights'].values()):
        raise RouterError('shadow prior requires independent1/vendor0/preference0')
    if not isinstance(policy['trait_maps'], dict) or set(policy['trait_maps']) != set(TRAITS):
        raise RouterError('trait_maps: fixed taxonomy keys required')
    for trait, mappings in policy['trait_maps'].items():
        if not isinstance(mappings, list):
            raise RouterError('trait map must be an explicit list')
        total = 0
        seen = set()
        for mapping in mappings:
            object_keys(mapping, {'benchmark_id', 'weight', 'comparison_group', 'harness', 'unit', 'required', 'setting_matches_reference', 'note'}, 'trait mapping', {'benchmark_id', 'weight', 'comparison_group', 'harness', 'unit', 'required', 'setting_matches_reference', 'note'})
            bid = mapping['benchmark_id']
            if bid not in indexes['benchmarks'] or bid in seen:
                raise RouterError('trait map: unknown/duplicate metric')
            seen.add(bid)
            benchmark = indexes['benchmarks'][bid]
            if not benchmark['active'] or benchmark.get('category') in ('composite', 'preference', 'operational', 'observational_agent'):
                raise RouterError('trait map: inactive/reference/preference metric cannot enter p0')
            if mapping['unit'] != benchmark['unit']:
                raise RouterError('trait map: metric unit mismatch')
            total += number(mapping['weight'], 'trait metric weight', maximum=1)
            for field in ('comparison_group', 'harness', 'note'):
                string(mapping[field], field)
            boolean(mapping['setting_matches_reference'], 'setting_matches_reference')
            if not isinstance(mapping['required'], dict) or not mapping['setting_matches_reference']:
                raise RouterError('trait map requires exact serving setting contract')
            if any(not isinstance(k, str) or v is None or isinstance(v, (dict, list)) or (type(v) in (int, float) and not finite(v)) for k, v in mapping['required'].items()):
                raise RouterError('trait map required fields must be known scalar values')
        if mappings and not math.isclose(total, 1, abs_tol=1e-9):
            raise RouterError('trait metric weights must sum to one')
    cost = object_keys(policy['cost'], COST_KEYS, 'cost', COST_KEYS)
    for field in COST_KEYS - {'window_base_prices', 'tier_rework_minima'}:
        number(cost[field], field)
    if not 0 < cost['stop_at'] <= .85 or cost['scarcity_exponent'] <= 0 or not 0 < cost['runout_warmup_fraction'] <= 1 or cost['multiplier_cap'] < 1 or not 0 <= cost['unknown_used'] < cost['stop_at'] or cost['unknown_pressure_index'] <= 0 or cost['base_price_cu_per_1k'] <= 0:
        raise RouterError('invalid scarcity/unknown-usage parameters')
    if not 0 < cost['failure_prior_min'] < cost['failure_prior_max'] < 1 or not math.isclose(cost['rework_human_fraction'] + cost['rework_compute_fraction'], 1, abs_tol=1e-9) or cost['tie_cu'] > .000001:
        raise RouterError('invalid failure/rework/tie policy')
    if set(cost['tier_rework_minima']) != set(TIERS) or any(number(cost['tier_rework_minima'][tier], 'tier minimum') < minimum for tier, minimum in TIERS.items()):
        raise RouterError('tier rework minima cannot be bypassed')
    prices = cost['window_base_prices']
    buckets = {r['usage_bucket'] for r in runtimes.values()}
    if not isinstance(prices, dict) or set(prices) - buckets:
        raise RouterError('window_base_prices: unknown usage bucket')
    for windows in prices.values():
        if not isinstance(windows, dict):
            raise RouterError('window base prices must be indexed by window name')
        for name, value in windows.items():
            string(name, 'window price name')
            if number(value, 'window base price') <= 0:
                raise RouterError('window base prices must be positive')
    limits = policy['effort_limits']
    if not isinstance(limits, dict) or set(limits) - set(APPROVED):
        raise RouterError('effort_limits: unknown profile')
    for profile_name, memberships in limits.items():
        if not isinstance(memberships, dict) or set(memberships) - set(models):
            raise RouterError('effort_limits: unknown model')
        for mid, efforts in memberships.items():
            strings(efforts, 'effort limit', EFFORTS)
            for eff in efforts:
                source = effort_index[mid, eff]
                if source['supported'] is not True or source.get('aa_intelligence_v4_3_2') is None or not in_band(source['aa_intelligence_v4_3_2'], policy['profiles'][profile_name]):
                    raise RouterError('effort limit cannot add unsupported/unmeasured/out-of-band tuple')
    if not isinstance(policy['fallbacks'], dict) or set(policy['fallbacks']) != set(APPROVED):
        raise RouterError('fallbacks: expected all profiles')
    for name, tuples in policy['fallbacks'].items():
        if not isinstance(tuples, list):
            raise RouterError('fallbacks: expected explicit tuple list')
        seen = set()
        for candidate in tuples:
            validate_tuple(candidate, policy, models)
            key = tuple_key(candidate)
            if key in seen:
                raise RouterError('fallbacks: duplicate tuple')
            seen.add(key)
            source = effort_index[candidate['model'], candidate['effort']]
            if source['supported'] is not True or source.get('aa_intelligence_v4_3_2') is None or not in_band(source['aa_intelligence_v4_3_2'], policy['profiles'][name]):
                raise RouterError('fallback must be a supported measured in-band tuple')
    validate_estimates(policy['estimates'], policy, models)
    canonical_hash(policy)
    return indexes, effort_index


def make_context(policy, dataset):
    try:
        validate_policy(policy, dataset)
    except (TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise RouterError('invalid policy/data shape: ' + str(exc)) from exc
    return {'policy': copy.deepcopy(policy), 'dataset': copy.deepcopy(dataset),
            'policy_hash': canonical_hash(policy), 'data_hash': canonical_hash(dataset)}


def load_context(root):
    policy = read_json(CORE / 'adapters' / 'router.json')
    overlay = project.overlay_dir(Path(root)) / 'router.json'
    if overlay.exists():
        policy = deep_merge(policy, read_json(overlay))
    envelopes = {name: read_json(CORE / 'data' / 'benchmarks' / (name + '.json'))
                 for name in ('models', 'benchmarks', 'results', 'efforts', 'taxonomy')}
    for name in ('models', 'benchmarks', 'results', 'efforts'):
        if not isinstance(envelopes[name], dict) or type(envelopes[name].get('schema_version')) is not int or envelopes[name]['schema_version'] != 1:
            raise RouterError('unsupported dataset schema')
        field = 'rows' if name == 'efforts' else name
        if not isinstance(envelopes[name].get(field), list):
            raise RouterError('missing dataset array: ' + field)
    taxonomy = envelopes['taxonomy']
    if not isinstance(taxonomy, dict) or type(taxonomy.get('schema_version')) is not int or taxonomy['schema_version'] != 1 or not isinstance(taxonomy.get('traits'), list) or any(not isinstance(row, dict) or not isinstance(row.get('id'), str) for row in taxonomy['traits']):
        raise RouterError('invalid taxonomy envelope')
    dataset = {name: envelopes[name][name] for name in ('models', 'benchmarks', 'results')}
    dataset.update(efforts=envelopes['efforts']['rows'], taxonomy_version=envelopes['taxonomy'].get('version'),
                   traits=[row['id'] for row in envelopes['taxonomy']['traits']])
    return make_context(policy, dataset)


def scarcity(used, elapsed, cost, *, pressure_index=None):
    used = number(used, 'used', maximum=1)
    elapsed = number(elapsed, 'elapsed', maximum=1)
    r = (1 - used) / max(1 - elapsed, .01) if pressure_index is None else number(pressure_index, 'pressure_index')
    h = min(1, elapsed / cost['runout_warmup_fraction'])
    if used == 1 or r == 0:
        multiplier = cost['multiplier_cap']
    else:
        multiplier = min(cost['multiplier_cap'], (1 - used) ** -cost['scarcity_exponent'] *
                         (1 + cost['runout_coefficient'] * h * max(0, 1 / r - 1)))
    return {'used': used, 'elapsed_fraction': elapsed, 'pressure_index': r,
            'warmup': h, 'multiplier': multiplier, 'hard_stop': used >= cost['stop_at']}


def usage_state(usage, policy, now):
    if not isinstance(usage, dict):
        raise RouterError('usage: expected balance.usage object')
    states = {}
    for bucket in sorted({r['usage_bucket'] for r in policy['runtimes'].values()}):
        vendor = usage.get(bucket, {'status': 'unknown', 'windows': []})
        if not isinstance(vendor, dict) or vendor.get('status') not in ('known', 'unknown') or not isinstance(vendor.get('windows'), list):
            raise RouterError('usage bucket: invalid status/windows')
        windows = []
        seen = set()
        for raw in vendor['windows']:
            if not isinstance(raw, dict):
                raise RouterError('usage window: expected object')
            name = string(raw.get('name'), 'usage window name')
            if name in seen:
                raise RouterError('usage: duplicate window')
            seen.add(name)
            used = number(raw.get('used'), 'usage used', maximum=1)
            reset = number(raw.get('resets_at'), 'usage resets_at')
            minutes = raw.get('window_minutes')
            if minutes is not None and number(minutes, 'window_minutes') <= 0:
                raise RouterError('window_minutes must be positive')
            if 'observed_at' in raw:
                number(raw['observed_at'], 'observed_at')
            if reset <= now:
                continue
            if minutes is None:
                state = scarcity(used, 0, policy['cost'], pressure_index=policy['cost']['unknown_pressure_index'])
                state['unknown_elapsed'] = True
            else:
                elapsed = min(1, max(0, 1 - (reset - now) / (minutes * 60)))
                state = scarcity(used, elapsed, policy['cost'])
            state.update(name=name, resets_at=reset, window_minutes=minutes,
                         observed_at=raw.get('observed_at'), source=raw.get('source'), measured=True)
            windows.append(state)
        if not windows:
            state = scarcity(policy['cost']['unknown_used'], 0, policy['cost'], pressure_index=policy['cost']['unknown_pressure_index'])
            state.update(name='unknown', measured=False, resets_at=None, window_minutes=None)
            windows = [state]
        states[bucket] = {'windows': windows, 'multiplier': max(w['multiplier'] for w in windows),
                          'hard_stop': any(w['hard_stop'] for w in windows),
                          'status': 'known' if all(w['measured'] for w in windows) else 'unknown'}
    return states


def base_price(policy, bucket, window):
    return policy['cost']['window_base_prices'].get(bucket, {}).get(window, policy['cost']['base_price_cu_per_1k'])


def quota_charge(policy, bucket, windows, tokens):
    products = []
    for window in windows:
        name = window['name']
        if name not in tokens:
            return None, []
        amount = number(tokens[name], 'window weighted tokens')
        price = base_price(policy, bucket, name)
        products.append({'window': name, 'multiplier': window['multiplier'], 'base_price': price,
                         'weighted_tokens': amount, 'charge_cu': price * window['multiplier'] * amount / 1000})
    return max((r['charge_cu'] for r in products), default=0), products


def evidence_for(dataset, candidate, contract, reference_setting=None):
    rows = [r for r in dataset['results'] if r['model_id'] == candidate['model'] and
            r['benchmark_id'] == contract['benchmark_id'] and r.get('effort') == candidate['effort']]
    matches = []
    rejected = []
    expected = {k: contract[k] for k in ('comparison_group', 'harness', 'unit')}
    expected.update(contract.get('required', {}))
    if 'as_of' in contract:
        expected['as_of'] = contract['as_of']
    for row in rows:
        reasons = []
        if row['self_reported']:
            reasons.append('vendor_source_weight_zero')
        for field, value in expected.items():
            if row.get(field) is None or type(row[field]) is not type(value) or row[field] != value:
                reasons.append('cohort_field_mismatch:' + field)
        if reference_setting is not None and (row.get('source_model') != reference_setting or row.get('setting') != reference_setting):
            reasons.append('serving_setting_mismatch')
        if not row.get('source_model') or row.get('setting') is None:
            reasons.append('unknown_serving_setting')
        if reasons:
            rejected.append({'id': row['id'], 'reasons': reasons})
        else:
            matches.append(row)
    if len(matches) != 1:
        return None, {'benchmark_id': contract['benchmark_id'], 'reason': 'ambiguous_exact_rows' if len(matches) > 1 else 'missing_exact_independent_row', 'rejected': rejected}
    return matches[0], None


def normalize(row, benchmark):
    norm = benchmark['normalization']
    value = row['value']
    if not norm['min'] <= value <= norm['max']:
        raise RouterError('result outside declared normalization domain')
    result = (value - norm['min']) / (norm['max'] - norm['min'])
    return result if norm['higher_is_better'] else 1 - result


def candidate_resources(candidate, source, policy, estimates, states, allowed):
    key = tuple_key(candidate)
    runtime = policy['runtimes'][candidate['runtime']]
    bucket = runtime['usage_bucket']
    windows = states[bucket]['windows']
    explicit = estimates['candidates'].get(key)
    assumptions = []
    missing = []
    if explicit is not None:
        if not explicit['continuation_feasible']:
            return {'exclusion': 'continuation_infeasible'}
        elapsed = explicit['elapsed_minutes']
        review = explicit['initial_review_cu']
        bounds = explicit['quota_windows']
        if set(bounds) - {w['name'] for w in windows}:
            raise RouterError('quota bounds: unknown/expired consumed window')
        tokens = {}
        for window in windows:
            bound = bounds.get(window['name'])
            if bound is None:
                missing.append('missing_consumed_window_bound:' + window['name'])
                continue
            if not window['measured']:
                missing.append('capacity_unknown:' + window['name'])
                continue
            if window['used'] + bound['upper_fraction'] + bound['reserve_fraction'] >= policy['cost']['stop_at']:
                return {'exclusion': 'capacity_reserve_exceeds_stop_line', 'capacity_window': window['name']}
            tokens[window['name']] = bound['weighted_tokens']
        kind = explicit['kind']
        capacity = 'bounded_against_all_active_stop_lines' if not missing else 'unknown'
    elif estimates['proxy_mode'] == 'aa_output_decode':
        output, seconds = source.get('output_tokens_per_task'), source.get('derived_decode_seconds_per_task')
        review = estimates['initial_review_cu']
        if output is None:
            missing.append('aa_output_proxy_missing')
        if seconds is None:
            missing.append('aa_decode_proxy_missing')
        if review is None:
            missing.append('initial_review_cost_unknown')
        tokens = {window['name']: output for window in windows} if output is not None else {}
        elapsed = seconds / 60 if seconds is not None else None
        kind = 'aa_output_decode_proxy'
        capacity = 'unmeasured_shadow_assumption'
        assumptions.extend(['AA output-only quota proxy; input/cache/tools unknown, not free or measured.',
                            'AA derived decode is latency proxy; first-token delay/overhead omitted equally, not measured elapsed time.',
                            'Equal output-token quota weights across consumed windows assumed; quota calibration N=0.',
                            'No token-to-window-share conversion; capacity/reserve feasibility unmeasured, shadow scenario only.'])
    else:
        return {'missing': ['quota_estimate_unknown', 'elapsed_estimate_unknown', 'initial_review_cost_unknown'],
                'assumptions': ['Resource estimates absent; quality retained without a cost winner.']}
    if missing:
        return {'missing': missing, 'assumptions': assumptions}
    if estimates['deadline_minutes'] is not None and elapsed > estimates['deadline_minutes']:
        return {'exclusion': 'deadline_estimate_exceeded', 'elapsed_minutes': elapsed, 'estimate_kind': kind}
    charge, products = quota_charge(policy, bucket, windows, tokens)
    if charge is None:
        return {'missing': ['quota_window_estimate_unknown']}
    # Followup buckets are charged once, not once per alias runtime or number of calls.
    followup_multiplier = 0
    followup_evidence = []
    for follow_bucket, share in estimates['followup_shares'].items():
        if share == 0:
            continue
        available = [rid for rid, cfg in policy['runtimes'].items() if cfg['usage_bucket'] == follow_bucket
                     and cfg['enabled'] and cfg['implemented'] and cfg['availability'] == 'available'
                     and cfg['account_status'] != 'unavailable' and rid in allowed]
        if not available or states[follow_bucket]['hard_stop']:
            return {'exclusion': 'followup_runtime_or_capacity_unavailable', 'followup_bucket': follow_bucket}
        follow_windows = states[follow_bucket]['windows']
        if explicit is not None:
            bounds = explicit.get('followup_quota_windows', {}).get(follow_bucket)
            if bounds is None:
                return {'missing': ['followup_capacity_bounds_unknown:' + follow_bucket]}
            if set(bounds) - {w['name'] for w in follow_windows}:
                raise RouterError('followup quota bounds: unknown/expired consumed window')
            follow_tokens = {}
            capacity_windows = []
            for window in follow_windows:
                bound = bounds.get(window['name'])
                if bound is None or not window['measured']:
                    return {'missing': ['followup_capacity_bounds_unknown:' + follow_bucket + '/' + window['name']]}
                # The writer and continuation consume the same bucket once: include both reservations.
                initial_bound = explicit['quota_windows'].get(window['name']) if follow_bucket == bucket else None
                combined = bound['upper_fraction'] + bound['reserve_fraction']
                if initial_bound is not None:
                    combined += initial_bound['upper_fraction'] + initial_bound['reserve_fraction']
                if window['used'] + combined >= policy['cost']['stop_at']:
                    return {'exclusion': 'followup_capacity_reserve_exceeds_stop_line',
                            'followup_bucket': follow_bucket, 'capacity_window': window['name']}
                capacity_windows.append({'window': window['name'], 'used': window['used'],
                                         'combined_upper_reserve_fraction': combined})
                follow_tokens[window['name']] = bound['weighted_tokens']
            if explicit.get('followup_window_tokens') is not None:
                supplied = explicit['followup_window_tokens'][follow_bucket]
                if supplied != follow_tokens:
                    raise RouterError('followup token workloads disagree with capacity-bound workload')
            follow_charge, detail = quota_charge(policy, follow_bucket, follow_windows, follow_tokens)
            # Normalize by baseline charge for the SAME indexed window workload.
            base = max((base_price(policy, follow_bucket, w['name']) * follow_tokens.get(w['name'], 0) / 1000 for w in follow_windows), default=0)
            if follow_charge is None or base <= 0:
                return {'missing': ['followup_window_estimate_missing']}
            multiplier = follow_charge / base
        else:
            multiplier = states[follow_bucket]['multiplier']
            detail = []
            capacity_windows = []
            assumptions.append('Followup shares are normalized baseline compute cost/consumption, not call counts; common window weights assumed.')
        followup_multiplier += share * multiplier
        followup_evidence.append({'bucket': follow_bucket, 'share': share, 'multiplier': multiplier,
                                 'window_products': detail, 'capacity_windows': capacity_windows})
    return {'quota_cu': charge, 'elapsed_minutes': elapsed, 'initial_review_cu': review,
            'quota_window_products': products, 'capacity_status': capacity, 'estimate_kind': kind,
            'followup_multiplier': followup_multiplier, 'followup_evidence': followup_evidence,
            'assumptions': assumptions}


def _score(context, *, profile, tier, weights, usage, now, allowed_runtimes, not_runtime, estimates):
    if not isinstance(context, dict) or 'policy' not in context or 'dataset' not in context:
        raise RouterError('invalid scoring context')
    policy, dataset = context['policy'], context['dataset']
    indexes, effort_index = validate_policy(policy, dataset)
    policy_hash, data_hash = canonical_hash(policy), canonical_hash(dataset)
    if context.get('policy_hash') != policy_hash or context.get('data_hash') != data_hash:
        raise RouterError('context hashes do not match policy/data')
    if profile not in APPROVED or type(tier) is not int or str(tier) not in TIERS:
        raise RouterError('unknown profile or non-integer tier0–3')
    now = number(now, 'now')
    if not isinstance(weights, dict) or set(weights) - set(TRAITS):
        raise RouterError('weights: unknown traits')
    normalized = {trait: number(weight, 'trait weight', maximum=1) for trait, weight in weights.items() if number(weight, 'trait weight', maximum=1) > 0}
    total = sum(normalized.values())
    if not math.isclose(total, 1, abs_tol=.001):
        raise RouterError('trait weights must sum to1 within rounding tolerance')
    normalized = {trait: weight / total for trait, weight in normalized.items()}
    mapped_metrics = [mapping['benchmark_id'] for trait in normalized
                      for mapping in policy['trait_maps'][trait]]
    if len(mapped_metrics) != len(set(mapped_metrics)):
        raise RouterError('active trait maps overlap a metric; freeze a non-overlapping comparison')
    allowed = set(policy['runtimes']) if allowed_runtimes is None else set(strings(allowed_runtimes, 'allowed_runtimes', policy['runtimes']))
    if not_runtime is not None and (not isinstance(not_runtime, str) or not_runtime not in policy['runtimes']):
        raise RouterError('not_runtime: unknown runtime')
    effective = deep_merge(policy['estimates'], estimates or {})
    if estimates is not None and not isinstance(estimates, dict):
        raise RouterError('estimates: expected object')
    validate_estimates(effective, policy, indexes['models'])
    states = usage_state(usage, policy, now)
    result = {'status': 'off', 'recommendation': None, 'reason': 'Shadow policy is off; static dispatch unchanged.',
              'candidates': [], 'evidence': [], 'policy_hash': policy_hash, 'data_hash': data_hash,
              'assumptions': [], 'estimate_hash': canonical_hash(effective), 'profile': profile, 'tier': tier,
              'weights': normalized, 'usage': states}
    if policy['mode'] == 'off':
        return result
    result['assumptions'] = ['p0=clip(1−Q,.02,.98) is an uncalibrated benchmark error prior, not measured acceptance/incident rate.',
                             'API effort support, adapter availability and account entitlement are separate; unknown accounts are shadow assumptions.']
    active_buckets = {cfg['usage_bucket'] for rid, cfg in policy['runtimes'].items()
                      if cfg['enabled'] and cfg['implemented'] and cfg['availability'] == 'available'
                      and cfg['account_status'] != 'unavailable' and cfg['models'] and rid in allowed
                      and (rid != not_runtime or effective['followup_shares'].get(cfg['usage_bucket'], 0) > 0)}
    if any(states[bucket]['status'] == 'unknown' for bucket in active_buckets):
        result['assumptions'].append('Unknown active usage assumed u=.50,r=1; not fresh/free capacity. Known stop breaches retained until reset.')
    retry = effective['retry']
    failed_reference = None
    if retry is not None:
        row, _ = evidence_for(dataset, retry['failed_tuple'], policy['reference'])
        failed_reference = row['value'] if row else None
    reference_contract = policy['reference']
    prof = policy['profiles'][profile]
    costs = policy['cost']
    rework_base = max(prof['rework_cu'], costs['tier_rework_minima'][str(tier)])
    for rid, cfg in sorted(policy['runtimes'].items()):
        for mid in sorted(cfg['models']):
            access = cfg['model_access'].get(mid, {'status': 'unknown', 'efforts': None})
            for eff in EFFORTS:
                candidate = {'runtime': rid, 'model': mid, 'effort': eff}
                item = dict(candidate, key=tuple_key(candidate), status='excluded', reasons=[], evidence=[],
                            reference_index=None, quality=None, p0=None, expected_total_cu=None,
                            api_support=effort_index[mid, eff]['supported'], account_status=access['status'],
                            runtime_account_status=cfg['account_status'])
                source = effort_index[mid, eff]
                ref_row, ref_missing = evidence_for(dataset, candidate, reference_contract)
                if ref_row is not None:
                    item['reference_index'] = ref_row['value']
                    item['reference_evidence'] = {'id': ref_row['id'], 'url': ref_row['url'], 'source_model': ref_row['source_model'], 'comparison_group': ref_row.get('comparison_group'), 'harness': ref_row.get('harness')}
                reasons = item['reasons']
                if source['supported'] is not True:
                    reasons.append('api_effort_unsupported' if source['supported'] is False else 'api_effort_support_unverified')
                if not cfg['enabled']:
                    reasons.append('runtime_disabled')
                if cfg['availability'] != 'available' or not cfg['implemented']:
                    reasons.append('runtime_planned' if cfg['availability'] == 'planned' else 'runtime_unavailable')
                if rid not in allowed:
                    reasons.append('runtime_mode_filtered')
                if rid == not_runtime:
                    reasons.append('not_runtime_filtered')
                if cfg['account_status'] == 'unavailable' or access['status'] == 'unavailable':
                    reasons.append('account_model_unavailable')
                if access['efforts'] is not None and eff not in access['efforts']:
                    reasons.append('account_effort_unavailable')
                if states[cfg['usage_bucket']]['hard_stop']:
                    reasons.append('quota_stop_line')
                required_caps = {'text'}
                if normalized.keys() & {'repo_edit', 'terminal'}:
                    required_caps.update(('tools', 'terminal'))
                if normalized.keys() & {'tool_workflow', 'browser', 'web_research'}:
                    required_caps.add('tools')
                if not required_caps <= set(cfg['capabilities']):
                    reasons.append('runtime_capability_missing')
                if ref_row is None:
                    reasons.append('reference_uncalibrated')
                    item['reference_missing'] = ref_missing
                elif not in_band(ref_row['value'], prof):
                    reasons.append('outside_profile_reference_band')
                limits = policy['effort_limits'].get(profile, {}).get(mid)
                if limits is not None and eff not in limits:
                    reasons.append('overlay_effort_narrowed')
                if retry is not None:
                    if tuple_key(candidate) == tuple_key(retry['failed_tuple']) and not retry['context_changed']:
                        reasons.append('unchanged_failed_tuple')
                    if retry['capability_escalation'] and not retry['task_specific_exception'] and (failed_reference is None or ref_row is None or ref_row['value'] <= failed_reference):
                        reasons.append('not_strictly_stronger_reference')
                if reasons:
                    result['candidates'].append(item)
                    continue
                quality = 0
                missing = []
                for trait, weight in sorted(normalized.items()):
                    mappings = policy['trait_maps'][trait]
                    if not mappings:
                        missing.append({'trait': trait, 'reason': 'no_frozen_independent_cohort'})
                    for mapping in mappings:
                        row, problem = evidence_for(dataset, candidate, mapping, ref_row['source_model'])
                        if row is None:
                            missing.append({'trait': trait, **problem})
                            continue
                        value = normalize(row, indexes['benchmarks'][mapping['benchmark_id']])
                        contribution = weight * mapping['weight'] * value
                        quality += contribution
                        item['evidence'].append({'trait': trait, 'id': row['id'], 'benchmark_id': row['benchmark_id'],
                            'value': row['value'], 'unit': row['unit'], 'normalized': value,
                            'trait_weight': weight, 'metric_weight': mapping['weight'], 'contribution': contribution,
                            'source_weight': 1, 'url': row['url'], 'effort': row['effort'], 'source_model': row['source_model'],
                            'setting': row['setting'], 'harness': row['harness'], 'comparison_group': row['comparison_group'],
                            'as_of': row['as_of'], 'source_snapshot_sha256': row.get('source_snapshot_sha256')})
                if missing:
                    item.update(status='insufficient_evidence', missing_evidence=missing)
                    reasons.append('missing_required_task_trait')
                    result['candidates'].append(item)
                    continue
                item.update(quality=quality, p0=min(costs['failure_prior_max'], max(costs['failure_prior_min'], 1 - quality)))
                if quality < prof['quality_floor']:
                    reasons.append('below_task_quality_floor')
                    result['candidates'].append(item)
                    continue
                resource = candidate_resources(candidate, source, policy, effective, states, allowed)
                if 'exclusion' in resource:
                    reasons.append(resource['exclusion'])
                    item.update(resource_details=resource)
                elif resource.get('missing'):
                    item.update(status='unscored', missing_resources=resource['missing'], assumptions=resource.get('assumptions', []))
                else:
                    initial = resource['quota_cu'] + costs['latency_cu_per_minute'] * resource['elapsed_minutes'] + resource['initial_review_cu']
                    conditional = rework_base * (costs['rework_human_fraction'] + costs['rework_compute_fraction'] * resource['followup_multiplier'])
                    item.update(status='scored', resources=resource, A=initial, R0=rework_base, R=conditional,
                                failure_rework_cu=item['p0'] * conditional, expected_total_cu=initial + item['p0'] * conditional)
                result['candidates'].append(item)
    scored = [c for c in result['candidates'] if c['status'] == 'scored']
    unscored = [c for c in result['candidates'] if c['status'] == 'unscored']
    result['comparable_quality_count'] = len(scored) + len(unscored)
    result['scored_count'] = len(scored)
    result['evidence'] = [row for c in result['candidates'] for row in c['evidence']]
    if unscored:
        result.update(status='unscored', reason='Qualifying tuples lack resource estimates; partial cost coverage cannot choose a winner.')
    elif scored:
        minimum = min(c['expected_total_cu'] for c in scored)
        tied = [c for c in scored if c['expected_total_cu'] <= minimum + costs['tie_cu']]
        selected = min(tied, key=lambda c: (-c['quality'], c['resources']['elapsed_minutes'], c['key']))
        forced = len(scored) < 2
        result.update(status='forced' if forced else 'scored', recommendation={k: selected[k] for k in ('runtime', 'model', 'effort')},
                      reason=('Only one comparable, fully estimated tuple; forced/static choice, not a learned winner.' if forced else
                              'Minimum A+p0R among exact in-band independent cohorts; uncalibrated shadow estimate.'),
                      expected_total_cu=selected['expected_total_cu'], selected_key=selected['key'],
                      assumptions=list(dict.fromkeys(result['assumptions'] + selected['resources']['assumptions'])))
    else:
        admitted = [c for c in result['candidates'] if c['api_support'] is True and c['reference_index'] is not None
                    and in_band(c['reference_index'], prof)]
        runtime_reasons = {'runtime_disabled', 'runtime_planned', 'runtime_unavailable', 'runtime_mode_filtered',
                           'not_runtime_filtered', 'account_model_unavailable', 'account_effort_unavailable',
                           'runtime_capability_missing', 'quota_stop_line'}
        if any(c['status'] == 'insufficient_evidence' for c in result['candidates']):
            result.update(status='insufficient_evidence', reason='Required exact independent task evidence is missing; no trait dropped or floor relaxed.')
        elif any('quota_stop_line' in c['reasons'] or 'followup_runtime_or_capacity_unavailable' in c['reasons'] for c in result['candidates']):
            result.update(status='runtime_limited', reason='Runtime/quota/continuation constraints leave no fully feasible comparison.')
        elif not allowed or (admitted and all(runtime_reasons & set(c['reasons']) for c in admitted)) or not any(cfg['availability'] == 'available' and cfg['implemented'] and rid in allowed and rid != not_runtime for rid, cfg in policy['runtimes'].items()):
            result.update(status='runtime_limited', reason='No available allowed writer runtime.')
        else:
            result.update(status='escalation_required', reason='No tuple meets the reference band, support and task-quality floor; no band widening permitted.')
        result['fallback_hints'] = [c for c in policy['fallbacks'][profile] if c['runtime'] in allowed and c['runtime'] != not_runtime and
                                   any(i['key'] == tuple_key(c) and i['status'] == 'insufficient_evidence' for i in result['candidates'])]
        if result.get('fallback_hints'):
            result['assumptions'].append('Full-tuple static hints need manual task-floor disposition; unknown Q is not waived.')
    canonical_hash(result)
    return result


def score(context, *, profile, tier, weights, usage, now, allowed_runtimes=None,
          not_runtime=None, estimates=None):
    """Return a pure shadow recommendation or explicit unknown/invalid result."""
    try:
        return _score(context, profile=profile, tier=tier, weights=weights, usage=usage,
                      now=now, allowed_runtimes=allowed_runtimes, not_runtime=not_runtime,
                      estimates=estimates)
    except (RouterError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        return {'status': 'invalid', 'recommendation': None, 'reason': str(exc), 'candidates': [],
                'evidence': [], 'policy_hash': context.get('policy_hash') if isinstance(context, dict) else None,
                'data_hash': context.get('data_hash') if isinstance(context, dict) else None, 'assumptions': []}
