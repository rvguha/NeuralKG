import unittest
from unittest.mock import AsyncMock, patch
import template_execution as te
import runtime
from query_context import QueryContext
import harness
from types import SimpleNamespace


class TemplateExecutionTests(unittest.TestCase):
    def test_derived_display_winner_difference(self):
        plan={'candidate':'compare.derived-values','reads':[{'entity':e} for e in ['A','A','B','B']],
              'pair_expression':{'op':'divide','args':[{'read':0},{'read':1}]},'direction':'max'}
        evidence=[{'value':v,'unit':u,'currency':'USD' if u=='USD' else None} for v,u in [(100,'USD'),(5,'persons'),(90,'USD'),(3,'persons')]]
        self.assertEqual([r['value'] for r in te.derived_result(plan,evidence)],[20,30])
        plan['candidate']='compare.derived-winner'
        self.assertEqual(te.derived_result(plan,evidence)[0]['entity'],'B')
        plan['candidate']='compare.derived-difference'
        self.assertEqual(te.derived_result(plan,evidence),-10)
        evidence[1]['value']=0
        with self.assertRaises(runtime.Refused):te.derived_result(plan,evidence)

    def test_derived_rejects_dimensionally_invalid_expression(self):
        with self.assertRaises(runtime.Refused):
            te.expression_units({'op':'add','args':[{'read':0},{'read':1}]},[{'unit':'USD'},{'unit':'persons'}])

    def test_derived_plan_rejects_missing_operand_or_mixed_entities(self):
        read={'entity':'A','type':'company','measure':'revenue','period':'2023','question':'revenue for A','source':'s'}
        plan={'candidate':'compare.derived-values','reads':[dict(read),dict(read)],
              'pair_expression':{'op':'divide','args':[{'read':0},{'read':1}]}}
        candidates=[{'shape':'compare.derived-values'}]; hits=[{'identifier':'s'}]
        te.validate(plan,candidates,hits)
        plan['reads'][1]['entity']='B'
        with self.assertRaises(runtime.Refused):te.validate(plan,candidates,hits)
        plan['reads']=[read]
        with self.assertRaises(runtime.Refused):te.validate(plan,candidates,hits)
        plan['reads']=[read,dict(read)]
        plan['pair_expression']={'read':0}
        with self.assertRaises(runtime.Refused):te.validate(plan,candidates,hits)

    def test_structure_validation_does_not_evaluate_placeholder_data(self):
        expr={'op':'divide','args':[{'read':0},{'op':'subtract','args':[{'read':0},{'read':1}]}]}
        self.assertEqual(te.expression_references(expr,2),{0,1})
        self.assertEqual(te.expression(expr,[6,3]),2)
        units=te.expression_units(
            {'op':'divide','args':[{'op':'multiply','args':[{'read':0},{'read':1}]},100]},
            [{'unit':'count'},{'unit':'percent'}])
        self.assertEqual(units,{('count',None):1})

    def test_period_substitution_is_rejected(self):
        te.check_period('2023',{'period':'FY2023'})
        with self.assertRaises(runtime.Refused):te.check_period('2023',{'period':'FY2025'})
        with self.assertRaises(runtime.Refused):te.check_period('2023',{'period':'Q12023'})
    def test_full_accessor_json_does_not_hide_an_unambiguous_scalar(self):
        data={'value':7,'results':[{'value':7,'date':'2024'}]}
        self.assertFalse(te.requires_structured_path(data,SimpleNamespace(value=7,kind='point')))
        self.assertTrue(te.requires_structured_path({'results':[{'value':7}]},
                                                     SimpleNamespace(value=None,kind='complex')))
    def test_latest_binary_reads_rebind_to_older_common_candidate_year(self):
        reads=[{'period':'latest'},{'period':'latest'}]
        self.assertEqual(te.latest_common_year(reads,[{'period':'2025'},{'period':'2022'}]),'2022')
        self.assertIsNone(te.latest_common_year(reads,[{'period':'2022'},{'period':'2022'}]))
        with self.assertRaises(runtime.Refused):
            te.latest_common_year([{'period':'2025'},{'period':'2022'}],
                                  [{'period':'2025'},{'period':'2022'}])
    def test_arithmetic_and_no_code(self):
        self.assertEqual(te.expression({'op':'divide','args':[{'read':0},{'read':1}]},[30,100]),.3)
        for value in ('__import__("os")', {'read':-1}, {'op':'eval','args':[1,2]}, {'op':'divide','args':[1,0]}):
            with self.assertRaises(runtime.Refused):te.expression(value,[1])

    def test_percent_product_uses_fraction_scale_but_difference_does_not(self):
        evidence=[{'unit':'count'},{'unit':'percent'}]
        product={'op':'multiply','args':[{'read':0},{'read':1}]}
        self.assertEqual(te.expression(te.normalize_percent_product(product,evidence),[1000,12]),120)
        difference={'op':'subtract','args':[{'read':0},{'read':1}]}
        self.assertIs(te.normalize_percent_product(difference,evidence),difference)

    def test_population_scope_comes_from_full_source_receipt(self):
        self.assertEqual(te.population_scope({'payload':{'variable':'Adult Population With Diabetes'}}),'adult')
        self.assertEqual(te.population_scope({'payload':{'variable_dcid':'Count_Person'}}),'all-persons')

    def test_plan_cannot_invent_sources_or_change_read_count(self):
        p={'candidate':'lookup.scalar','reads':[{'source':'invented'}]}
        with self.assertRaises(runtime.Refused):te.validate(p,[{'shape':'lookup.scalar'}],[{'identifier':'actual'}])
        p['reads']=[]
        with self.assertRaises(runtime.Refused):te.validate(p,[{'shape':'lookup.scalar'}],[])

    def test_adult_prevalence_rejects_total_population_denominator(self):
        base={'source':'s','entity':'Texas','type':'state','period':'latest','question':'q'}
        plan={'candidate':'lookup.binary','reads':[
            {**base,'measure':'total population'},
            {**base,'measure':'adult diabetes prevalence'}],
            'expression':{'op':'multiply','args':[{'read':0},{'read':1}]}}
        with self.assertRaisesRegex(runtime.Refused,'adult population denominator'):
            te.validate(plan,[{'shape':'lookup.binary'}],[{'identifier':'s'}])
        plan['reads'][0]['measure']='adult population age 18 and older'
        te.validate(plan,[{'shape':'lookup.binary'}],[{'identifier':'s'}])


class WiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_source_replans_against_another_accessor(self):
        context=QueryContext(); seen=[]
        async def selected(question,understanding,hits,*,context,excluded):
            seen.append([h['identifier'] for h in hits])
            if len(seen)==1:
                context.memo['template_plan']={'candidate':'lookup.scalar'}
                context.memo['template_active_source']='bq'
                raise runtime.Refused('BigQuery table acquisition requires an input question')
            return {'shape':'lookup.scalar','source':hits[0]['identifier']}
        understood={'candidates':[{'shape':'lookup.scalar','status':'ok','applicability':'plausible'}]}
        hits=[{'identifier':'bq','metadata':{'accessor':'atlas_bigquery'}},
              {'identifier':'dc','metadata':{'accessor':'atlas_datacommons'}}]
        with patch.object(te,'_run_selected',side_effect=selected):
            got=await te.run('median age',understood,hits,context=context)
        self.assertEqual(got['source'],'dc')
        self.assertEqual(seen,[['bq','dc'],['dc']])

    async def test_failed_direct_plan_tries_other_understood_api_shape(self):
        context=QueryContext(); calls=[]
        async def selected(question,understanding,hits,*,context,excluded):
            calls.append(set(excluded))
            if not excluded:
                context.memo['template_plan']={'candidate':'lookup.scalar'}
                context.memo['template_active_source']='s'
                raise runtime.Refused('source returned a rate, not a count')
            return {'shape':'lookup.binary','data':{'result':10}}
        understood={'candidates':[
            {'shape':'lookup.scalar','status':'ok','applicability':'plausible'},
            {'shape':'lookup.binary','status':'ok','applicability':'plausible'}]}
        with patch.object(te,'_run_selected',side_effect=selected):
            got=await te.run('How many?',understood,[{'identifier':'s'}],context=context)
        self.assertEqual(got['shape'],'lookup.binary')
        self.assertEqual(calls,[set(),{'lookup.scalar'}])
        self.assertEqual(context.memo['plan_execution_failures'][0]['candidate'],'lookup.scalar')

    async def test_latest_rebind_preserves_original_question_for_renderer(self):
        original='How many people in Texas have diabetes?'
        reads=[
            {'source':'s','entity':'Texas','type':'place','measure':'population',
             'period':'latest','question':'population for Texas'},
            {'source':'s','entity':'Texas','type':'place','measure':'diabetes prevalence',
             'period':'latest','question':'diabetes prevalence for Texas'}]
        plan={'candidate':'lookup.binary','reads':reads,
              'expression':{'op':'divide','args':[
                  {'op':'multiply','args':[{'read':0},{'read':1}]},100]},
              'reason':'test'}
        def evidence(value,period,unit):
            record={'value':value,'period':period,'unit':unit,'currency':None,
                    'source':'provider','payload':{'results':[{'value':value}]}}
            return SimpleNamespace(value=value,kind='point',to_dict=lambda:dict(record))
        searches=[
            (None,None,None,None,{'value':31}, {'_evidence':evidence(31,'2025','count'),'_attempts':[]}),
            (None,None,None,None,{'value':10}, {'_evidence':evidence(10,'2022','percent'),'_attempts':[]}),
            (None,None,None,None,{'value':29}, {'_evidence':evidence(29,'2022','count'),'_attempts':[]})]
        ledger=SimpleNamespace(snapshot=lambda:{})
        context=QueryContext(usage_ledger=ledger,discovery_ledger=ledger)
        with patch.object(te,'compile_plan',AsyncMock(return_value=plan)), \
             patch.object(harness,'_search_async',AsyncMock(side_effect=searches)), \
             patch.object(harness,'_asay',AsyncMock()), \
             patch.object(harness.TK,'synthesize_async',AsyncMock(return_value='answer')) as synthesize:
            result=await te.run(original,{'candidates':[{'shape':'lookup.binary','status':'ok',
                                'applicability':'plausible'}]},[{'identifier':'s'}],context=context)
        self.assertEqual(synthesize.await_args.args[0],original)
        self.assertEqual(result['question'],original)
        self.assertEqual(result['data']['aligned_period'],'2022')

    async def test_planner_repair_includes_failed_output_and_reason(self):
        good={'candidate':'lookup.scalar','reads':[{'source':'s','entity':'A','type':'company','measure':'revenue','period':'2023','question':'A revenue'}],'expression':{'read':0}}
        import json
        context=QueryContext()
        with patch.object(te.llm,'chat_async',AsyncMock(side_effect=['{"candidate":"lookup.scalar","reads":[]}',json.dumps(good),'{"valid":true,"issues":[]}'])) as chat:
            result=await te.compile_plan({'question':'q'},[{'shape':'lookup.scalar'}],[{'identifier':'s'}],context=context)
        self.assertEqual(result,good)
        repaired=json.loads(chat.call_args_list[1].args[1])['repair']
        self.assertIn('no required reads',repaired['error'])
        self.assertIn('"reads":[]',repaired['previous_output'])
        self.assertEqual(len(context.memo['planning_attempts']),2)

    async def test_candidates_dispatch_to_new_executor(self):
        with patch.object(harness,'discover_async',AsyncMock(return_value=({'candidates':[]},[]))), patch.object(te,'run',AsyncMock(return_value={'answer':'fixed'})) as execute:
            answer=await harness.run('q',context=QueryContext())
        self.assertEqual(answer,{'answer':'fixed','execution_path':'template'})
        execute.assert_awaited_once()
