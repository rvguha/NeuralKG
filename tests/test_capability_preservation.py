import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch
from domain import Evidence
from query_context import QueryContext
import harness
import runtime
import template_execution


class PreservationTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_keeps_ambiguous_measures_visible_to_ard(self):
        understood={'shape':'point','entity':'Microsoft','entity_status':'resolved','attribute':'size','interpretations':['revenue','employees'],'period':'latest'}
        with patch.object(harness.ard_client,'search_many_async',AsyncMock(return_value=[])) as search:
            await harness.discover_async('How big is Microsoft?',context=QueryContext(),_understand=AsyncMock(return_value=understood))
        self.assertIn('revenue',search.await_args.args[0])
        self.assertIn('employees',search.await_args.kwargs['rerank_query'])
        self.assertIn('How big is Microsoft?',search.await_args.kwargs['rerank_query'])

    def test_compatibility_mode_is_inherited_without_sharing_scratch_state(self):
        root=QueryContext(execution_mode='established')
        branch=root.fork()
        self.assertEqual(branch.execution_mode,'established')
        self.assertIs(branch.budget,root.budget)
        branch.memo['private']=True
        self.assertNotIn('private',root.memo)
        self.assertEqual(QueryContext().execution_mode,'template')

    async def test_rich_scalar_keeps_records_and_uses_existing_renderer(self):
        candidate={'shape':'lookup.scalar','status':'ok','applicability':'plausible'}
        plan={'candidate':'lookup.scalar','reads':[{'source':'s','entity':'Hospital','type':'nonprofit','measure':'NIH funding','period':'latest','question':'Hospital NIH funding'}],'expression':{'read':0}}
        hit={'identifier':'s','title':'NIH grants'}
        data={'total_usd':100,'results':[{'award_amount':100}],'matched_entities':2,'entity_groups':[{'name':'Hospital'},{'name':'School'}]}
        ev=Evidence(kind='complex',source='NIH grants',identifier='s',value=100,payload=data)
        context=QueryContext(usage_ledger=harness.llm.Ledger(),discovery_ledger=harness.ard_client.DiscoveryUsage())
        with patch.object(template_execution,'compile_plan',AsyncMock(return_value=plan)), \
             patch.object(harness.llm,'chat_async',AsyncMock(return_value='{"interpretations":[]}')), \
             patch.object(harness,'_search_async',AsyncMock(return_value=({},[hit],hit,[],data,{'_evidence':ev}))), \
             patch.object(harness,'_present_async',AsyncMock(return_value=('100 across two recipients','llm-synthesis'))) as present:
            result=await template_execution.run('Hospital NIH funding',{'candidates':[candidate]},[hit],context=context)
        self.assertEqual(result['data'],data)
        self.assertEqual(result['answer'],'100 across two recipients')
        present.assert_awaited_once()

    async def test_ambiguous_measure_is_not_silently_bound_to_eps(self):
        plan={'candidate':'lookup.scalar','reads':[{'entity':'Apple'}]}
        with patch.object(template_execution,'compile_plan',AsyncMock(return_value=plan)), \
             patch.object(harness.llm,'chat_async',AsyncMock(return_value=json.dumps({'interpretations':['net income','operating income','gross profit']}))), \
             patch.object(harness,'_search_async',AsyncMock()) as search:
            with self.assertRaisesRegex(runtime.Refused,'clarification'):
                await template_execution.run('Apple earnings',{'candidates':[{'shape':'lookup.scalar','status':'ok','applicability':'plausible'}]},[],context=QueryContext())
        search.assert_not_awaited()

    async def test_refused_new_path_preserves_complex_data_and_actual_source(self):
        old={'shape':'point','entity':'St. Jude','type':'nonprofit','attribute':'NIH funding','period':'latest'}
        a={'identifier':'a','title':'Federal awards','score':80}
        b={'identifier':'b','title':'NIH awards','score':95}
        g={'identifier':'g','title':'IRS grants','score':70}
        data={'total_usd':115948847,'record_count':185,'match':'name','matched_entities':2,
              'entity_groups':[{'name':'Hospital','total_usd':115646820},{'name':'Graduate school','total_usd':302027}],
              'results':[{'award_amount':10}]}
        ev=Evidence(kind='complex',source='NIH awards',identifier='b',payload=data)
        context=QueryContext()
        with patch.object(harness,'discover_async',AsyncMock(side_effect=[({'candidates':[]},[b,g,a]),(old,[b,g,a])])) as discovery, \
             patch.object(template_execution,'run',AsyncMock(side_effect=runtime.Refused('missing row adapter'))), \
             patch.object(harness,'_link_entity_async',AsyncMock(return_value=[{'qid':'Q1'}])), \
             patch.object(harness.planner,'plan',return_value={'verdict':'exact','hit':a}), \
             patch.object(harness.driver,'frontmatter',side_effect=lambda ident: {'irsgrants':True} if ident=='g' else {}), \
             patch.object(harness,'_search_async',AsyncMock(return_value=(old,[b,a],b,[],data,{}))), \
             patch.object(harness,'_admit_async',AsyncMock(return_value=(ev,[]))), \
             patch.object(harness,'_present_async',AsyncMock(return_value=('Preserved answer','llm-synthesis'))):
            result=await harness.run('St. Jude NIH funding',context=context)
        self.assertEqual(result['execution_path'],'established-pipeline')
        self.assertEqual(result['data'],data)
        self.assertEqual(result['source']['identifier'],'b')
        self.assertIn('NIH awards',result['plan'])
        self.assertEqual(discovery.await_count,2)
        self.assertIs(discovery.await_args_list[1].kwargs['_understand'],harness._legacy_query_understanding_async)

    async def test_budgets_cancellation_and_programming_errors_do_not_fallback(self):
        for error in [runtime.QueryBudgetExceeded('budget'),runtime.QueryCancelled('cancel'),ValueError('bug')]:
            with self.subTest(error=error),patch.object(harness,'discover_async',AsyncMock(side_effect=error)) as discover:
                with self.assertRaises(type(error)):await harness.run('q',context=QueryContext())
                self.assertEqual(discover.await_count,1)

    async def test_clarification_uses_established_assumption_protocol(self):
        with patch.object(harness,'discover_async',AsyncMock(return_value=({},[]))) as discover:
            with self.assertRaises(runtime.Refused):await harness.run('q',assumptions={'entity':'Chosen hospital'},context=QueryContext())
        self.assertEqual(discover.await_count,1)
        self.assertEqual(discover.await_args.kwargs['assumptions'],{'entity':'Chosen hospital'})
        self.assertIn('_understand',discover.await_args.kwargs)

    async def test_new_success_does_not_invoke_compatibility(self):
        with patch.object(harness,'discover_async',AsyncMock(return_value=({'candidates':[]},[]))) as discover,patch.object(template_execution,'run',AsyncMock(return_value={'answer':'new answer'})):
            result=await harness.run('q',context=QueryContext())
        self.assertEqual(result['execution_path'],'template');self.assertEqual(discover.await_count,1)
