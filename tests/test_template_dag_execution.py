import asyncio
import copy
import json
import unittest
from unittest.mock import AsyncMock, patch

import answer_synthesizer as synth
import extensions
import template_dag_execution as dag
from query_context import QueryContext
from runtime import Refused
from template_operator_cases import cases, supplied


class AsyncTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_76_async_graphs_match_fixed_input_execution(self):
        for name,case in cases().items():
            with self.subTest(template=name):
                seen=[]
                async def reader(node,p,dependencies,*,context):
                    seen.append((node['id'],len(dependencies)))
                    return copy.deepcopy(case['inputs'][node['id']])
                actual=await synth.execute(name,case['parameters'],reader,context=QueryContext())
                expected=synth.synthesize(name,case['inputs'],case['parameters'])
                self.assertEqual(actual,expected)
                self.assertEqual({node for node,_ in seen},set(case['inputs']))

    async def test_dependent_read_waits_for_date_resolution(self):
        async def reader(node,p,dependencies,*,context):
            if node['operator']=='ResolveDate':return supplied(2020)
            self.assertEqual([v.data for v in dependencies],[2020])
            return supplied(42)
        result=await synth.execute('lookup.event-bound',{'c':{'fields':'*'}},reader,context=QueryContext())
        self.assertEqual(result['result'],42)

    async def test_registered_reader_is_selected_by_descriptor_not_model(self):
        reg=extensions.Registry(); calls=[]
        @reg.template_reader('verified_rows')
        async def read(node,p,dependencies,*,source,context):
            calls.append(source)
            return supplied([{'value':7}])
        with patch.object(extensions,'registry',return_value=reg),patch.object(dag.driver,'frontmatter',return_value={'template_reader':'verified_rows'}):
            value=await dag.read({'operator':'ReadRows'},{'source':'allowed'},[],hits=[{'identifier':'allowed'}],context=QueryContext())
            self.assertEqual(value.data,[{'value':7}]);self.assertEqual(calls,['allowed'])
            with self.assertRaises(Refused):await dag.read({'operator':'ReadRows'},{'source':'invented'},[],hits=[{'identifier':'allowed'}],context=QueryContext())

    async def test_duplicate_reader_registration_rejected(self):
        reg=extensions.Registry();reg.template_reader('one')(lambda:None)
        with self.assertRaises(ValueError):reg.template_reader('one')(lambda:None)

    async def test_missing_population_adapter_does_not_simulate_rows(self):
        with patch.object(dag.driver,'frontmatter',return_value={}),patch('planner.capabilities',return_value={}):
            with self.assertRaisesRegex(Refused,'no admitted complete-input adapter'):
                await dag.read({'operator':'ReadRows'},{'source':'s'},[],hits=[{'identifier':'s'}],context=QueryContext())

    async def test_cancellation_before_acquisition(self):
        context=QueryContext();context.cancel();reader=AsyncMock()
        with self.assertRaises(Exception):await synth.execute('lookup.scalar',{},reader,context=context)
        reader.assert_not_awaited()

    def test_operator_prompt_contracts_cover_every_compute_operator(self):
        self.assertEqual(set(dag.ARGUMENTS),set(synth.OPERATORS))
