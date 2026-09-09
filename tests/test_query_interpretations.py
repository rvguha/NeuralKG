import json
import unittest
from unittest.mock import AsyncMock, patch
import harness
import runtime
from query_context import QueryContext


class InterpretationTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return QueryContext(usage_ledger=harness.llm.Ledger(),discovery_ledger=harness.ard_client.DiscoveryUsage())

    def choices(self):
        return [{'entity':'Microsoft Corporation','attribute':a,'description':a} for a in ('revenue','number of employees')]

    async def test_same_harness_full_payloads_one_render_original_constraints(self):
        seen=[]
        async def branch(question,**kw):
            seen.append((question,kw))
            return {'data':{'value':10,'unit':'USD','nested':{'detail':[1,2]}},'evidence':{'source':'s'}}
        context=self.context()
        with patch.object(harness,'run',side_effect=branch),patch.object(harness.TK,'synthesize_async',AsyncMock(return_value='Separate answers')) as render:
            result=await harness._answer_interpretations('How big was Microsoft in 2023?',self.choices(),sites=['allowed'],context=context)
        self.assertEqual(len(seen),2)
        for question,kw in seen:
            self.assertIn('How big was Microsoft in 2023?',question)
            self.assertEqual(kw['sites'],['allowed'])
            self.assertTrue(kw['context'].interpretation_bound)
            self.assertTrue(kw['context'].defer_render)
            self.assertIs(kw['context'].budget,context.budget)
        render.assert_awaited_once()
        self.assertEqual(result['data']['interpretation_answers'][0]['result']['data']['nested'],{'detail':[1,2]})
        self.assertFalse(context.defer_render)

    async def test_failed_interpretation_does_not_replace_success(self):
        with patch.object(harness,'run',AsyncMock(side_effect=[{'data':{'value':10}},runtime.Refused('No headcount')])),patch.object(harness.TK,'synthesize_async',AsyncMock(return_value='Revenue available; headcount unavailable')):
            result=await harness._answer_interpretations('How big?',self.choices(),sites=None,context=self.context())
        rows=result['data']['interpretation_answers']
        self.assertEqual(rows[1]['status'],'unavailable')
        self.assertNotIn('result',rows[1])
        self.assertEqual(rows[0]['result']['data']['value'],10)

    async def test_budget_and_cancellation_are_not_missing_data(self):
        for exc in (runtime.QueryBudgetExceeded('budget'),runtime.QueryCancelled('cancel')):
            with patch.object(harness,'run',AsyncMock(side_effect=exc)):
                with self.assertRaises(type(exc)):
                    await harness._answer_interpretations('q',self.choices(),sites=None,context=self.context())

    async def test_bound_interpretation_does_not_expand_again(self):
        understood={'candidates':[{'status':'ok','applicability':'plausible','interpretations':self.choices()}]}
        import template_execution
        context=self.context();context.interpretation_bound=True
        with patch.object(harness,'discover_async',AsyncMock(return_value=(understood,[]))),patch.object(template_execution,'run',AsyncMock(return_value={'data':{'value':1}})) as execute,patch.object(harness,'_answer_interpretations',AsyncMock()) as expand:
            await harness.run('q',context=context)
        execute.assert_awaited_once();expand.assert_not_awaited()

    async def test_deferred_render_makes_no_llm_call(self):
        context=self.context();context.defer_render=True
        with patch.object(harness.llm,'chat_async',AsyncMock()) as chat:
            result=await harness.TK.synthesize_async('q',{'value':10},context=context)
        self.assertEqual(result,'');chat.assert_not_awaited()

    def test_alternative_templates_are_not_extra_interpretations(self):
        c={'status':'ok','applicability':'plausible','interpretations':self.choices()*2}
        self.assertEqual(harness._extracted_interpretations({'candidates':[c,c]}),self.choices())
        self.assertEqual(harness._extracted_interpretations({'candidates':[{'status':'ok','applicability':'plausible','entities':[1,2]}]}),[])
