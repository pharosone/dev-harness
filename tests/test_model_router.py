"""Pure exact-effort shadow scoring: policy, costs, evidence and bounded feasibility."""
import copy
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

CORE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CORE / 'scripts'))
import model_router as router

NOW = 1791600000
UNIT20 = {'terminal': .60, 'tool_workflow': .25, 'review': .15}


def usage(scarce=False, buckets=('claude', 'codex')):
    return {bucket: {'status': 'known', 'windows': [{'name': 'five_hour',
             'used': .75 if bucket == 'claude' and scarce else .20,
             'window_minutes': 300,
             'resets_at': NOW + 18000 * (1 - (.50 if bucket == 'claude' and scarce else .30)),
             'observed_at': NOW, 'source': 'fixture'}]} for bucket in buckets}


def estimate(review=0, elapsed=1):
    bound = {'weighted_tokens': 1000, 'upper_fraction': .005, 'reserve_fraction': .005}
    return {'kind': 'bounded', 'elapsed_minutes': elapsed, 'initial_review_cu': review,
            'quota_windows': {'five_hour': copy.deepcopy(bound)}, 'continuation_feasible': True,
            'followup_quota_windows': {bucket: {'five_hour': copy.deepcopy(bound)} for bucket in ('claude', 'codex')}}


class RouterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as overlay, patch.dict(os.environ, {'HARNESS_PROJECT': overlay}):
            cls.base = router.load_context(CORE)

    def context(self, policy_patch=None, dataset=None):
        policy = router.deep_merge(self.base['policy'], {'mode': 'shadow'})
        if policy_patch:
            policy = router.deep_merge(policy, policy_patch)
        return router.make_context(policy, dataset or self.base['dataset'])

    def run_score(self, context=None, **kwargs):
        args = {'profile': 'critical-review', 'tier': 3, 'weights': UNIT20,
                'usage': usage(), 'now': NOW}
        args.update(kwargs)
        return router.score(context or self.context(), **args)

    def scenario(self, profile='critical-review', scarce=False, review=10, **kwargs):
        return self.run_score(profile=profile, tier=1 if profile == 'routine' else 3,
                    weights={'factual': 1} if profile == 'routine' else UNIT20,
                    usage=usage(scarce), estimates={'proxy_mode': 'aa_output_decode', 'initial_review_cu': review}, **kwargs)

    def item(self, result, key):
        return next(c for c in result['candidates'] if c['key'] == key)

    def restrict(self, profile, tuples):
        limits = {m['id']: [] for m in self.base['dataset']['models']}
        for _, mid, eff in tuples:
            limits[mid].append(eff)
        return self.context({'effort_limits': {profile: limits}})

    def test_off_default_and_classifier_contract(self):
        self.assertEqual(self.base['policy']['mode'], 'off')
        self.assertEqual(self.base['policy']['classifier'], {'enabled': False,
                         'model': 'claude-haiku-5-5', 'effort': 'medium', 'timeout_seconds': 20,
                         'max_budget_usd': .05, 'cache': True})
        result = self.run_score(self.base)
        self.assertEqual(result['status'], 'off')
        self.assertIsNone(result['recommendation'])
        self.assertEqual(result['candidates'], [])

    def test_overlay_deep_merge_location_and_unknown_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            overlay = root / 'custom'
            overlay.mkdir()
            (overlay / 'router.json').write_text(json.dumps({'mode': 'shadow', 'classifier': {'cache': False}}))
            with patch.dict(os.environ, {'HARNESS_PROJECT': 'custom'}):
                context = router.load_context(root)
            self.assertEqual(context['policy']['mode'], 'shadow')
            self.assertFalse(context['policy']['classifier']['cache'])
            self.assertEqual(context['policy']['classifier']['timeout_seconds'], 20)
            (overlay / 'router.json').write_text('{"mode":"shadow","unexpected":true}')
            with patch.dict(os.environ, {'HARNESS_PROJECT': str(overlay)}), self.assertRaises(router.RouterError):
                router.load_context(root)
            (overlay / 'router.json').write_text('{"mode":"off","mode":"shadow"}')
            with patch.dict(os.environ, {'HARNESS_PROJECT': str(overlay)}), self.assertRaises(router.RouterError):
                router.load_context(root)

    def test_modes_only_off_shadow_and_budget_bool_invalid(self):
        for patch_value in [{'mode': 'on'}, {'mode': True}, {'classifier': {'max_budget_usd': True}},
                            {'classifier': {'timeout_seconds': 0}}, {'classifier': {'enabled': 'true'}},
                            {'schema_version': True}]:
            with self.subTest(patch=patch_value), self.assertRaises(router.RouterError):
                self.context(patch_value)

    def test_pure_and_hashes_pin_context(self):
        context = self.context()
        snapshot = copy.deepcopy(context)
        report = self.run_score(context)
        self.assertEqual(snapshot, context)
        self.assertEqual(json.loads(json.dumps(report)), report)
        context['policy']['mode'] = 'off'
        self.assertEqual(self.run_score(context)['status'], 'invalid')
        rebuilt = router.make_context(context['policy'], context['dataset'])
        self.assertNotEqual(rebuilt['policy_hash'], snapshot['policy_hash'])

    def test_missing_estimates_keep_quality_unscored_not_free(self):
        result = self.run_score()
        self.assertEqual(result['status'], 'unscored')
        self.assertIsNone(result['recommendation'])
        item = self.item(result, 'claude/claude-opus-5-5/high')
        self.assertAlmostEqual(item['quality'], .5807519330074226, places=12)
        self.assertGreater(item['reference_index'], 51.5)
        self.assertEqual(item['status'], 'unscored')
        self.assertIn('quota_estimate_unknown', item['missing_resources'])
        self.assertIsNone(item['expected_total_cu'])

    def test_all_effort_memberships_no_brand_or_band_bypass(self):
        expected = {
          'mechanical': [('claude-haiku-5-5', ['low','medium','high']), ('claude-sonnet-5-5',['low']), ('gpt-6-luna',list(router.EFFORTS))],
          'routine': [('claude-haiku-5-5',['xhigh','max']), ('claude-sonnet-5-5',['medium','high']), ('claude-opus-5-5',['low']), ('claude-fable-5-1',['low']), ('gpt-6.1-sol',['low','medium']), ('gpt-6-astra',['low'])],
          'implementation': [('claude-fable-5-1',['medium','high']), ('claude-opus-5-5',['medium']), ('claude-sonnet-5-5',['xhigh']), ('gpt-6.1-sol',['high','xhigh','max']), ('gpt-6-astra',['medium','high'])],
          'critical-review': [('claude-opus-5-5',['high','xhigh']), ('claude-sonnet-5-5',['xhigh','max']), ('claude-fable-5-1',['xhigh','max']), ('gpt-6.1-sol',['max']), ('gpt-6-astra',['xhigh','max'])],
          'escalation': [('claude-opus-5-5',['high','xhigh','max']), ('claude-sonnet-5-5',['max']), ('claude-fable-5-1',['xhigh','max']), ('gpt-6-astra',['xhigh','max'])],
          'deep-reasoning': [('claude-opus-5-5',['high','xhigh','max']), ('claude-sonnet-5-5',['max']), ('claude-fable-5-1',['xhigh','max']), ('gpt-6-astra',['xhigh','max'])]}
        for profile, entries in expected.items():
            result = self.run_score(profile=profile, tier=1, weights={'factual':1})
            admitted = {(c['model'], c['effort']) for c in result['candidates']
                        if c['runtime'] in ('claude','codex') and c['api_support'] is True and
                           c['reference_index'] is not None and 'outside_profile_reference_band' not in c['reasons']}
            self.assertEqual(admitted, {(mid, eff) for mid, efforts in entries for eff in efforts}, profile)
        result = self.scenario()
        self.assertIn('outside_profile_reference_band', self.item(result, 'claude/claude-opus-5-5/max')['reasons'])
        self.assertIn('outside_profile_reference_band', self.item(result, 'codex/gpt-6-astra/medium')['reasons'])

    def test_overlay_may_narrow_but_not_weaken_support_or_floors(self):
        for patch_value in [
          {'profiles':{'routine':{'reference_max':50}}},
          {'profiles':{'critical-review':{'reference_min':50}}},
          {'profiles':{'implementation':{'upper_inclusive':True}}},
          {'profiles':{'routine':{'quality_floor':.2}}},
          {'profiles':{'routine':{'rework_cu':1}}},
          {'effort_limits':{'critical-review':{'claude-opus-5-5':['max']}}},
          {'effort_limits':{'routine':{'grok-4.7':['medium']}}},
          {'runtimes':{'codex':{'model_access':{'gpt-6-astra':{'efforts':['none']}}}}}]:
            with self.subTest(patch=patch_value), self.assertRaises(router.RouterError):
                self.context(patch_value)
        result = self.run_score(self.context({'effort_limits':{'critical-review':{'claude-opus-5-5':['high']}}}))
        self.assertIn('overlay_effort_narrowed', self.item(result,'claude/claude-opus-5-5/xhigh')['reasons'])

    def test_quality_floor_tier_minimum_and_no_escalation_by_effort(self):
        result = self.scenario()
        self.assertIn('below_task_quality_floor', self.item(result,'claude/claude-fable-5-1/max')['reasons'])
        result = self.run_score(profile='routine', tier=3, weights={'factual':1},
                               estimates={'proxy_mode':'aa_output_decode','initial_review_cu':10})
        for candidate in result['candidates']:
            if candidate['status']=='scored':
                self.assertEqual(candidate['R0'],120)
        with self.assertRaises(router.RouterError):
            self.context({'cost':{'tier_rework_minima':{'3':30}}})

    def test_three_approved_scenarios_with_full_precision(self):
        examples = [
          ('routine',True,1,('codex','gpt-6.1-sol','medium'),14.76391942726441),
          ('critical-review',True,10,('codex','gpt-6-astra','xhigh'),170.0198914355302),
          ('critical-review',False,10,('claude','claude-sonnet-5-5','max'),59.6900000020667)]
        for profile, scarce, review, expected, total in examples:
            result=self.scenario(profile,scarce,review)
            self.assertEqual(result['status'],'scored')
            self.assertEqual(tuple(result['recommendation'][k] for k in ('runtime','model','effort')),expected)
            self.assertAlmostEqual(result['expected_total_cu'],total,places=9)
            self.assertFalse(any(a.startswith('Unknown active') for a in result['assumptions']))
            chosen=self.item(result,result['selected_key'])
            self.assertAlmostEqual(chosen['A']+chosen['p0']*chosen['R'],total,places=9)
            self.assertEqual(chosen['resources']['capacity_status'],'unmeasured_shadow_assumption')
            self.assertIn('AA output-only', ' '.join(result['assumptions']))
        scarce=self.scenario(scarce=True)
        self.assertAlmostEqual(self.item(scarce,scarce['selected_key'])['R'],405.375)
        fresh=self.scenario()
        self.assertAlmostEqual(self.item(fresh,fresh['selected_key'])['R'],126.75)

    def test_proxy_reasoning_not_double_counted_and_api_dollars_unused(self):
        result=self.scenario()
        chosen=self.item(result,result['selected_key'])
        raw=next(r for r in self.base['dataset']['efforts'] if (r['model_id'],r['effort'])==('claude-sonnet-5-5','max'))
        self.assertEqual(chosen['resources']['quota_window_products'][0]['weighted_tokens'],raw['output_tokens_per_task'])
        data=copy.deepcopy(self.base['dataset'])
        for row in data['efforts']:
            row['api_cost_usd_per_task']=999999
        changed=self.scenario(context=self.context(dataset=data))
        self.assertEqual(changed['recommendation'],result['recommendation'])
        self.assertEqual(changed['expected_total_cu'],result['expected_total_cu'])

    def test_explicit_deadline_is_hard_and_missing_decode_is_unscored(self):
        result=self.run_score(estimates={'proxy_mode':'aa_output_decode','initial_review_cu':10,'deadline_minutes':12})
        self.assertIn('deadline_estimate_exceeded',self.item(result,'claude/claude-sonnet-5-5/max')['reasons'])
        result=self.run_score(profile='mechanical',tier=0,weights={'factual':1},
                             estimates={'proxy_mode':'aa_output_decode','initial_review_cu':1})
        item=self.item(result,'codex/gpt-6-luna/medium')
        self.assertEqual(item['status'],'unscored')
        self.assertIn('aa_decode_proxy_missing',item['missing_resources'])
        self.assertEqual(result['status'],'unscored')
        self.assertIsNone(result['recommendation'])

    def test_planned_runtime_and_account_support_separate(self):
        result=self.run_score(allowed_runtimes=['gemini','grok'])
        self.assertEqual(result['status'],'runtime_limited')
        self.assertIsNone(result['recommendation'])
        argon=self.item(result,'gemini/gemini-4-argon/high')
        self.assertIn('runtime_planned',argon['reasons'])
        self.assertIn('api_effort_support_unverified',argon['reasons'])
        self.assertIsNotNone(argon['reference_index'])
        result=self.run_score(self.context({'runtimes':{'claude':{'account_status':'unavailable'}}}))
        item=self.item(result,'claude/claude-opus-5-5/high')
        self.assertTrue(item['api_support'])
        self.assertEqual(item['runtime_account_status'],'unavailable')
        self.assertIn('account_model_unavailable',item['reasons'])

    def test_single_runtime_requires_explicit_feasible_followup_policy(self):
        result=self.run_score(profile='routine',tier=1,weights={'factual':1},allowed_runtimes=['codex'],
                             estimates={'proxy_mode':'aa_output_decode','initial_review_cu':1})
        self.assertEqual(result['status'],'runtime_limited')
        self.assertIsNone(result['recommendation'])
        result=self.run_score(profile='routine',tier=1,weights={'factual':1},allowed_runtimes=['codex'],
                             estimates={'proxy_mode':'aa_output_decode','initial_review_cu':1,'followup_shares':{'codex':1}})
        self.assertEqual(result['status'],'scored')
        self.assertEqual(result['recommendation']['runtime'],'codex')
        self.assertEqual([x['bucket'] for x in self.item(result,result['selected_key'])['resources']['followup_evidence']],['codex'])
        result=self.run_score(allowed_runtimes=['claude','codex'],not_runtime='claude')
        self.assertTrue(all(c['status']=='excluded' for c in result['candidates'] if c['runtime']=='claude'))

    def test_exact_effort_cohort_missing_and_no_dropped_trait(self):
        for field,value in [('harness',None),('comparison_group','different'),('source_model','different serving setup')]:
            data=copy.deepcopy(self.base['dataset'])
            row=next(r for r in data['results'] if (r['model_id'],r['effort'],r['benchmark_id'])==('gpt-6-astra','xhigh','terminal-bench-4.0-aa'))
            row[field]=value
            result=self.run_score(self.context(dataset=data),estimates={'proxy_mode':'aa_output_decode','initial_review_cu':10})
            item=self.item(result,'codex/gpt-6-astra/xhigh')
            self.assertEqual(item['status'],'insufficient_evidence')
            self.assertIsNone(item['quality'])
            self.assertTrue(item['missing_evidence'])
        data=copy.deepcopy(self.base['dataset'])
        data['results']=[r for r in data['results'] if not (r['model_id']=='gpt-6-astra' and r['effort']=='xhigh' and r['benchmark_id']=='terminal-bench-4.0-aa')]
        item=self.item(self.run_score(self.context(dataset=data)),'codex/gpt-6-astra/xhigh')
        self.assertEqual(item['status'],'insufficient_evidence')
        self.assertIsNone(item['quality'])

    def test_vendor_and_preference_do_not_enter_failure_prior(self):
        data=copy.deepcopy(self.base['dataset'])
        row=next(r for r in data['results'] if (r['model_id'],r['effort'],r['benchmark_id'])==('gpt-6-astra','xhigh','terminal-bench-4.0-aa'))
        row['self_reported']=True
        item=self.item(self.run_score(self.context(dataset=data)),'codex/gpt-6-astra/xhigh')
        self.assertEqual(item['status'],'insufficient_evidence')
        self.assertIn('vendor_source_weight_zero',str(item['missing_evidence']))
        for value in [.7,True,1]:
            with self.subTest(vendor=value),self.assertRaises(router.RouterError):
                self.context({'source_weights':{'vendor':value}})
        mapping=copy.deepcopy(self.base['policy']['trait_maps']['factual'][0])
        mapping.update(benchmark_id='arena-text',unit='Elo')
        with self.assertRaises(router.RouterError):
            self.context({'trait_maps':{'factual':[mapping]}})

    def test_unknown_required_field_and_overlapping_metrics(self):
        mapping=copy.deepcopy(self.base['policy']['trait_maps']['factual'])
        mapping[0]['required']['unknown_protocol_field']='frozen'
        result=self.run_score(self.context({'trait_maps':{'factual':mapping}}),profile='routine',tier=1,weights={'factual':1})
        self.assertEqual(result['status'],'insufficient_evidence')
        self.assertIsNone(result['recommendation'])
        ctx=self.context({'trait_maps':{'instruction':self.base['policy']['trait_maps']['tool_workflow']}})
        self.assertEqual(self.run_score(ctx,weights={'instruction':.5,'tool_workflow':.5})['status'],'invalid')

    def test_gap_returns_in_band_static_hints_not_floor_waiver(self):
        result=self.run_score(weights={'repo_edit':1})
        self.assertEqual(result['status'],'insufficient_evidence')
        self.assertIsNone(result['recommendation'])
        self.assertTrue(result['fallback_hints'])
        for hint in result['fallback_hints']:
            item=self.item(result,router.tuple_key(hint))
            self.assertTrue(router.in_band(item['reference_index'],self.base['policy']['profiles']['critical-review']))
            self.assertIsNone(item['quality'])

    def test_forced_choice_is_not_labeled_learned_winner(self):
        ctx=self.restrict('routine',[('codex','gpt-6.1-sol','medium')])
        result=self.run_score(ctx,profile='routine',tier=1,weights={'factual':1},
                             estimates={'proxy_mode':'aa_output_decode','initial_review_cu':1})
        self.assertEqual(result['status'],'forced')
        self.assertIn('forced/static',result['reason'])

    def test_scarcity_constants_and_continuity(self):
        cost=self.base['policy']['cost']
        self.assertEqual({k:cost[k] for k in ['scarcity_exponent','runout_coefficient','runout_warmup_fraction','multiplier_cap','stop_at','unknown_used','unknown_pressure_index','base_price_cu_per_1k','latency_cu_per_minute','failure_prior_min','failure_prior_max','prior_equivalent_observations','rework_human_fraction','rework_compute_fraction','tie_cu']},
             {'scarcity_exponent':2,'runout_coefficient':2,'runout_warmup_fraction':.25,'multiplier_cap':50,'stop_at':.85,'unknown_used':.5,'unknown_pressure_index':1,'base_price_cu_per_1k':.01,'latency_cu_per_minute':.1,'failure_prior_min':.02,'failure_prior_max':.98,'prior_equivalent_observations':20,'rework_human_fraction':.9,'rework_compute_fraction':.1,'tie_cu':.000001})
        self.assertAlmostEqual(router.scarcity(.2,.3,cost)['multiplier'],1.5625)
        self.assertAlmostEqual(router.scarcity(.75,.5,cost)['multiplier'],48)
        a=router.scarcity(.6,.25-1e-7,cost)['multiplier']
        b=router.scarcity(.6,.25+1e-7,cost)['multiplier']
        self.assertLess(abs(a-b),.0001)
        self.assertEqual(router.scarcity(1,.5,cost)['multiplier'],50)
        self.assertTrue(router.scarcity(.85,.5,cost)['hard_stop'])
        self.assertFalse(router.scarcity(.85-1e-8,.5,cost)['hard_stop'])

    def test_unknown_usage_not_free_and_known_stop_survives_until_reset(self):
        result=self.run_score(usage={})
        self.assertEqual(result['usage']['claude']['multiplier'],4)
        self.assertTrue(any(a.startswith('Unknown active') for a in result['assumptions']))
        report=usage()
        report['claude']['status']='unknown'
        report['claude']['windows'][0]['used']=.85
        result=self.run_score(usage=report)
        self.assertTrue(result['usage']['claude']['hard_stop'])
        self.assertIn('quota_stop_line',self.item(result,'claude/claude-opus-5-5/high')['reasons'])
        report['claude']['windows'][0]['resets_at']=NOW
        result=self.run_score(usage=report)
        self.assertFalse(result['usage']['claude']['hard_stop'])
        self.assertEqual(result['usage']['claude']['status'],'unknown')

    def test_multiwindow_charge_maximum_product_not_max_multiplier(self):
        policy=self.base['policy']
        windows=[dict(router.scarcity(.75,.5,policy['cost']),name='five_hour'),
                 dict(router.scarcity(.2,.3,policy['cost']),name='seven_day')]
        charge,details=router.quota_charge(policy,'claude',windows,{'five_hour':1000,'seven_day':100000})
        self.assertAlmostEqual(charge,1.5625)
        self.assertAlmostEqual(details[0]['charge_cu'],.48)
        changed=copy.deepcopy(policy)
        changed['cost']['window_base_prices']={'claude':{'five_hour':.02,'seven_day':.005}}
        charge,_=router.quota_charge(changed,'claude',windows,{'five_hour':1000,'seven_day':100000})
        self.assertAlmostEqual(charge,.96)

    def test_bounded_writer_and_followup_capacity_required(self):
        key='codex/gpt-6.1-sol/medium'
        ctx=self.restrict('routine',[('codex','gpt-6.1-sol','medium')])
        bound=estimate()
        missing=copy.deepcopy(bound)
        del missing['followup_quota_windows']
        result=self.run_score(ctx,profile='routine',tier=1,weights={'factual':1},estimates={'candidates':{key:missing}})
        item=self.item(result,key)
        self.assertEqual(item['status'],'unscored')
        self.assertIn('followup_capacity_bounds_unknown',str(item['missing_resources']))
        self.assertIsNone(result['recommendation'])
        result=self.run_score(ctx,profile='routine',tier=1,weights={'factual':1},estimates={'candidates':{key:bound}})
        self.assertEqual(result['status'],'forced')
        self.assertEqual(self.item(result,key)['resources']['capacity_status'],'bounded_against_all_active_stop_lines')
        fail=copy.deepcopy(bound)
        fail['followup_quota_windows']['claude']['five_hour']['upper_fraction']=.7
        result=self.run_score(ctx,profile='routine',tier=1,weights={'factual':1},estimates={'candidates':{key:fail}})
        self.assertIn('followup_capacity_reserve_exceeds_stop_line',self.item(result,key)['reasons'])
        fail=copy.deepcopy(bound)
        fail['quota_windows']['five_hour']['upper_fraction']=.64
        fail['quota_windows']['five_hour']['reserve_fraction']=.009
        result=self.run_score(ctx,profile='routine',tier=1,weights={'factual':1},estimates={'candidates':{key:fail}})
        self.assertIn('followup_capacity_reserve_exceeds_stop_line',self.item(result,key)['reasons'])

    def test_every_window_checked_and_followup_max_product_normalized(self):
        key='claude/claude-opus-5-5/low'
        ctx=self.restrict('routine',[('claude','claude-opus-5-5','low')])
        report=usage(True)
        report['claude']['windows'].append({'name':'seven_day','used':.2,'window_minutes':10080,'resets_at':NOW+10080*60*.7})
        bound=estimate()
        bound['quota_windows']['seven_day']={'weighted_tokens':100000,'upper_fraction':.005,'reserve_fraction':.005}
        bound['followup_quota_windows']['claude']['seven_day']={'weighted_tokens':100000,'upper_fraction':.005,'reserve_fraction':.005}
        result=self.run_score(ctx,profile='routine',tier=1,weights={'factual':1},usage=report,estimates={'candidates':{key:bound}})
        self.assertEqual(result['status'],'forced')
        resources=self.item(result,key)['resources']
        self.assertAlmostEqual(resources['quota_cu'],1.5625)
        self.assertAlmostEqual(resources['followup_multiplier'],1.5625)
        del bound['followup_quota_windows']['claude']['seven_day']
        result=self.run_score(ctx,profile='routine',tier=1,weights={'factual':1},usage=report,estimates={'candidates':{key:bound}})
        self.assertEqual(self.item(result,key)['status'],'unscored')

    def test_mixed_missing_resource_coverage_cannot_pick_measured_winner(self):
        key='claude/claude-opus-5-5/low'
        result=self.run_score(profile='routine',tier=1,weights={'factual':1},estimates={'candidates':{key:estimate()}})
        self.assertEqual(self.item(result,key)['status'],'scored')
        self.assertEqual(result['status'],'unscored')
        self.assertIsNone(result['recommendation'])

    def test_runtime_command_never_executed_and_custom_registry_valid(self):
        context=self.context({'runtimes':{'codex':{'command':{'argv':['nonexistent-runtime'],'protocol':'custom'}}}})
        with patch('subprocess.run',side_effect=AssertionError('scoring cannot spawn processes')):
            result=self.run_score(context)
        self.assertEqual(result['status'],'unscored')
        self.assertEqual(context['policy']['runtimes']['codex']['command']['argv'],['nonexistent-runtime'])
        with self.assertRaises(router.RouterError):
            self.context({'runtimes':{'gemini':{'availability':'available'}}})

    def test_retry_strength_and_unchanged_tuple(self):
        retry={'failed_tuple':{'runtime':'claude','model':'claude-sonnet-5-5','effort':'max'},
               'context_changed':False,'capability_escalation':True,'task_specific_exception':False}
        result=self.run_score(estimates={'proxy_mode':'aa_output_decode','initial_review_cu':10,'retry':retry})
        self.assertEqual(result['status'],'escalation_required')
        self.assertIsNone(result['recommendation'])
        result=self.run_score(profile='escalation',estimates={'proxy_mode':'aa_output_decode','initial_review_cu':10,'retry':retry})
        self.assertEqual(result['status'],'forced')
        self.assertEqual(result['recommendation'],{'runtime':'claude','model':'claude-opus-5-5','effort':'max'})
        retry['task_specific_exception']=True
        with self.assertRaises(router.RouterError):
            router.validate_estimates(router.deep_merge(self.base['policy']['estimates'],{'retry':retry}),self.base['policy'],{m['id']:m for m in self.base['dataset']['models']})

    def test_ties_higher_quality_lower_latency_stable_tuple(self):
        a=('claude','claude-opus-5-5','low');b=('codex','gpt-6.1-sol','medium')
        context=self.restrict('routine',[a,b]);first=self.run_score(context,profile='routine',tier=1,weights={'factual':1})
        qa=self.item(first,'/'.join(a))['quality'];qb=self.item(first,'/'.join(b))['quality'];R=10*(.9+.1*1.5625)
        estimates={'/'.join(a):estimate((qa-qb)*R),'/'.join(b):estimate()}
        result=self.run_score(context,profile='routine',tier=1,weights={'factual':1},estimates={'candidates':estimates})
        self.assertEqual(result['recommendation'],dict(zip(('runtime','model','effort'),a)))
        data=copy.deepcopy(context['dataset'])
        row=next(r for r in data['results'] if (r['model_id'],r['effort'],r['benchmark_id'])==('gpt-6.1-sol','medium','aa-omniscience-accuracy'))
        row['value']=qa*100
        context=router.make_context(context['policy'],data)
        estimates={'/'.join(a):estimate(0,2),'/'.join(b):estimate(.1,1)}
        result=self.run_score(context,profile='routine',tier=1,weights={'factual':1},estimates={'candidates':estimates})
        self.assertEqual(result['recommendation'],dict(zip(('runtime','model','effort'),b)))
        estimates={'/'.join(a):estimate(),'/'.join(b):estimate()}
        result=self.run_score(context,profile='routine',tier=1,weights={'factual':1},estimates={'candidates':estimates})
        self.assertEqual(result['recommendation'],dict(zip(('runtime','model','effort'),a)))

    def test_nonfinite_bool_and_invalid_arguments_fail_predictably(self):
        changes=[{'tier':True},{'tier':4},{'now':True},{'now':float('nan')},{'weights':{'factual':True}},
                 {'weights':{'factual':float('inf')}},{'weights':{'unknown':1}},{'weights':{'factual':.3}},
                 {'allowed_runtimes':['unknown']},{'not_runtime':'unknown'},{'estimates':[]},
                 {'estimates':{'initial_review_cu':True}}, {'estimates':{'proxy_mode':'on'}}]
        for args in changes:
            with self.subTest(args=args):
                result=self.run_score(**args)
                self.assertEqual(result['status'],'invalid')
                self.assertIsNone(result['recommendation'])
        report=usage();report['claude']['windows'][0]['used']=True
        self.assertEqual(self.run_score(usage=report)['status'],'invalid')
        for field in ['value','self_reported']:
            data=copy.deepcopy(self.base['dataset'])
            data['results'][0][field]=float('nan') if field=='value' else 1
            with self.assertRaises(router.RouterError):
                self.context(dataset=data)
        with self.assertRaises(router.RouterError):
            self.context({'cost':{'tie_cu':.02}})


if __name__=='__main__':
    unittest.main()
