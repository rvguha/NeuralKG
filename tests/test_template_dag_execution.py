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
    def test_long_form_alignment_preserves_unequal_time_ranges(self):
        relations=[[{'year':2023,'value':1},{'year':2024,'value':2}],
                   [{'year':2024,'value':3},{'year':2025,'value':4}]]
        got=synth.align_time([relations],{'layout':'long','time':'year'})
        self.assertEqual([(row['year'],row['series_index']) for row in got],
                         [(2023,0),(2024,0),(2024,1),(2025,1)])
        with self.assertRaises(Refused):
            synth.align_time([[[{'year':'unknown'}]]],{'layout':'long','time':'year'})
    def test_lossless_plan_wire_defaults_are_normalized(self):
        templates=synth.load_templates()
        candidates=[{'shape':'series.values','bindings':{'entity_keys':['Santa Clara County']}}]
        plan={'candidate':'0','parameters':{
            'a':{'accessor_parameters':{'indicator':'median household income'}},
            'b':{},'c':{}}}
        normalized=dag.normalize_plan(plan,candidates,templates)
        self.assertEqual(normalized['candidate'],'series.values')
        self.assertEqual(normalized['parameters']['a']['params'],
                         {'indicator':'median household income'})
        self.assertEqual(normalized['parameters']['b']['joins'],[])
        self.assertEqual(normalized['parameters']['c']['fields'],'*')

        ranking={'candidate':'rank.population','parameters':{'a':{},'b':{},'c':{},'d':{}}}
        ranked=dag.normalize_plan(ranking,[{'shape':'rank.population','bindings':{}}],templates)
        self.assertEqual(ranked['parameters']['c']['limit'],'all')
        self.assertEqual(ranked['parameters']['c']['ties'],'all')
        self.assertEqual(ranked['parameters']['d']['fields'],'*')

        # Both identifiers were supplied in the same ARD hit.  Copying the resource
        # URN or OKF id is therefore an unambiguous wire alias, not an invented source.
        hits=[{'identifier':'catalog/table.md','urn':'urn:air:atlas:table',
               'metadata':{'id':'bq.public.dataset.table'}}]
        for alias in ('urn:air:atlas:table','bq.public.dataset.table'):
            ranking={'candidate':'rank.population','parameters':{
                'a':{'source':alias},'b':{},'c':{},'d':{}}}
            ranked=dag.normalize_plan(ranking,[{'shape':'rank.population','bindings':{}}],templates,hits)
            self.assertEqual(ranked['parameters']['a']['source'],'catalog/table.md')

        series={'candidate':'series.values','parameters':{
            'a':{'source':'catalog/place.md','params':{}},'b':{},'c':{}}}
        with patch.object(dag,'available_sources',return_value=[
                {'identifier':'catalog/place.md','output_fields':['place','year','value'],
                 'accessor_parameters':[{'name':'year_from'},{'name':'year_to'}]}]):
            normalized=dag.normalize_plan(series,[{'shape':'series.values',
                'bindings':{'entity_keys':['Nigeria','Germany'],'periods':{
                    'start':'2006-01-01','end':'2025-12-31'}}}],templates,
                [{'identifier':'catalog/place.md'}])
        self.assertEqual(normalized['parameters']['b'],{'layout':'long','time':'year'})
        self.assertEqual(normalized['parameters']['a']['params'],{'year_from':2006,'year_to':2025})

        wrapped={'candidate':'rank.population','parameters':{
            'a':{},'b':{'op':'order','args':[{'input':0},{'by':[{'field':'value','direction':'desc'}],
                                             'nulls':'last'}]},
            'c':{'op':'take','args':[{'input':0},{'limit':'all'},{'ties':'all'}]},
            'd':{'op':'project','args':[{'input':0},{'fields':'*'}]}}}
        normalized=dag.normalize_plan(wrapped,[{'shape':'rank.population','bindings':{}}],templates)
        self.assertEqual(normalized['parameters']['b']['by'][0]['field'],'value')
        self.assertEqual(normalized['parameters']['c']['limit'],'all')
        self.assertEqual(normalized['parameters']['d']['fields'],'*')

        semantic_fields={'candidate':'rank.population','parameters':{
            'a':{'source':'catalog/dc.md'},
            'b':{'by':[{'field':'co2_emissions_per_capita','direction':'desc'}]},
            'c':{'limit':'all','ties':'all'},
            'd':{'fields':['country_name','co2_emissions_per_capita']}}}
        with patch.object(dag,'available_sources',return_value=[
                {'identifier':'catalog/dc.md','output_fields':['place','value']} ]):
            normalized=dag.normalize_plan(semantic_fields,
                [{'shape':'rank.population','bindings':{}}],templates,[{'identifier':'catalog/dc.md'}])
        self.assertEqual(normalized['parameters']['b']['by'][0]['field'],'value')
        self.assertEqual(normalized['parameters']['d']['fields'],['place','value'])

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
