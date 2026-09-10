import asyncio
import json
import unittest
from unittest import mock

import harness
import llm
import query_understanding as qu
import runtime
from query_context import QueryContext
from template_stage_run import evaluate_case


def catalog(n=76):
    return [dict(id=f's{i}', asks=f'Description {i}', examples=[f'Example {i}'],
                 negative_examples=[{'question':f'Near miss {i}', 'input_condition':'Other operation required', 'why_not':'Different output', 'instead':'other'}],
                 slots={'x':{'type':'number'}}, requires={'proof':'provided by API'},
                 plan={'nodes':[]}, returns={'grain':'scalar'}) for i in range(n)]


def extracted(**overrides):
    return dict(bindings={'x':1}, entities=[], measures=['amount'], periods=[],
                missing=[], acquisition_queries=['input amount'], applicability='plausible',
                reason='Independent approach', **overrides)


class ThreeRoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_date_is_fixed_across_rounds_and_forks(self):
        context=QueryContext(reference_date='2031-04-05')
        self.assertEqual(context.fork().reference_date,'2031-04-05')
        async def reply(system,user,**kw):
            self.assertEqual(json.loads(user)['reference_date'],'2031-04-05')
            if kw['stage']!='understand-extract':return json.dumps({'shapes':['s0']})
            return json.dumps(extracted())
        with mock.patch.object(qu,'load_catalog',return_value=(catalog(1),'hash')),mock.patch.object(llm,'chat_async',side_effect=reply):
            await qu.understand('last ten years',context=context)

    def test_selection_prompt_requires_api_dependent_alternative_plans(self):
        self.assertIn('lookup.scalar', qu.SELECT)
        self.assertIn('lookup.binary', qu.SELECT)
        self.assertIn('reduce.streaming', qu.SELECT)
        self.assertIn('How many people in Texas have diabetes?', qu.SELECT)
        self.assertIn('lookup.binary is a plausible route', qu.EXTRACT)

    async def test_entity_attribute_interpretations_survive_extraction(self):
        alternatives=[{'entity':'Microsoft Corporation','attribute':a,'description':a}
                      for a in ('revenue','number of employees')]
        async def reply(system,user,**kw):
            if kw['stage']!='understand-extract':return json.dumps({'shapes':['s0']})
            self.assertIn('interpretations',system)
            return json.dumps(extracted(interpretations=alternatives))
        with mock.patch.object(qu,'load_catalog',return_value=(catalog(1),'hash')),mock.patch.object(llm,'chat_async',side_effect=reply):
            result=await qu.understand('How big is Microsoft?',context=QueryContext())
        self.assertEqual(result['candidates'][0]['interpretations'],alternatives)

    async def test_nested_missing_slot_is_preserved(self):
        async def reply(system,user,**kw):
            if kw['stage']!='understand-extract':return json.dumps({'shapes':['s0']})
            value=extracted();value['missing']=[{'slot':'x.period','reason':'Period unspecified'}]
            return json.dumps(value)
        with mock.patch.object(qu,'load_catalog',return_value=(catalog(1),'hash')),mock.patch.object(llm,'chat_async',side_effect=reply):
            result=await qu.understand('fixed question',context=QueryContext())
        self.assertEqual(result['candidates'][0]['status'],'ok')
        self.assertEqual(result['candidates'][0]['missing'][0]['slot'],'x.period')

    async def test_external_read_cannot_succeed_without_acquisition(self):
        shapes=catalog(1)
        shapes[0]['plan']={'nodes':[{'id':'a','operator':'ReadScalar','inputs':[]}]}
        calls=0
        async def reply(system,user,**kw):
            nonlocal calls
            if kw['stage']!='understand-extract': return json.dumps({'shapes':['s0']})
            calls+=1
            value=extracted()
            value['acquisition_queries']=[]
            return json.dumps(value)
        with mock.patch.object(qu,'load_catalog',return_value=(shapes,'hash')),mock.patch.object(llm,'chat_async',side_effect=reply):
            result=await qu.understand('fixed question',context=QueryContext())
        self.assertEqual(calls,2)
        self.assertEqual(result['candidates'][0]['status'],'error')
        self.assertIn('requires external data',result['candidates'][0]['error'])

    async def test_missing_parameters_empty_still_acquires_data(self):
        shapes=catalog(1)
        shapes[0]['plan']={'nodes':[{'id':'a','operator':'ReadScalar','inputs':[]}]}
        async def reply(system,user,**kw):
            if kw['stage']!='understand-extract': return json.dumps({'shapes':['s0']})
            self.assertIn('Knowing an entity, measure and period does NOT supply its value',system)
            return json.dumps(extracted())
        with mock.patch.object(qu,'load_catalog',return_value=(shapes,'hash')),mock.patch.object(llm,'chat_async',side_effect=reply):
            result=await qu.understand('fixed question',context=QueryContext())
        self.assertEqual(result['candidates'][0]['status'],'ok')
        self.assertEqual(result['candidates'][0]['missing'],[])
        self.assertTrue(result['candidates'][0]['acquisition_queries'])

    async def test_exact_rounds_parallelism_and_payloads(self):
        shapes=catalog(); seen=[]; batch_started=0; extract_started=0
        batches_ready=asyncio.Event(); extracts_ready=asyncio.Event()
        async def reply(system,user,**kw):
            nonlocal batch_started,extract_started
            payload=json.loads(user); stage=kw['stage']; seen.append((stage,payload))
            self.assertEqual(payload['question'],'fixed question')
            if stage=='understand-batch':
                batch_started+=1
                if batch_started==3: batches_ready.set()
                await batches_ready.wait()
                self.assertLessEqual(len(payload['shapes']),30)
                for s in payload['shapes']:
                    self.assertEqual(set(s),{'id','asks','examples','negative_examples'})
                    self.assertEqual(s['negative_examples'],shapes[int(s['id'][1:])]['negative_examples'])
                return json.dumps({'shapes':[s['id'] for s in payload['shapes'][:3]]})
            if stage=='understand-shortlist':
                self.assertEqual(batch_started,3)
                self.assertEqual(len(payload['shapes']),9)
                self.assertTrue(all(set(s)=={'id','asks','examples','negative_examples'} for s in payload['shapes']))
                return json.dumps({'shapes':['s0','s30','s60']})
            self.assertEqual(stage,'understand-extract')
            self.assertEqual(set(payload),{'question','shape'})
            self.assertEqual(payload['shape'],shapes[int(payload['shape']['id'][1:])])
            extract_started+=1
            if extract_started==3: extracts_ready.set()
            await extracts_ready.wait()
            return json.dumps(extracted())
        with mock.patch.object(qu,'load_catalog',return_value=(shapes,'digest')),mock.patch.object(llm,'chat_async',side_effect=reply) as chat:
            result=await asyncio.wait_for(harness.query_understanding_async('fixed question',context=QueryContext()),2)
        self.assertEqual(chat.await_count,7)
        self.assertEqual([c['shape'] for c in result['candidates']],['s0','s30','s60'])
        self.assertNotIn('shape',result)
        self.assertEqual([len(p['shapes']) for s,p in seen if s=='understand-batch'],[30,30,16])
        self.assertEqual(len(result['understanding_trace']),7)

    async def test_no_candidates_stops_after_first_round(self):
        with mock.patch.object(qu,'load_catalog',return_value=(catalog(),'hash')),mock.patch.object(llm,'chat_async',return_value='{"shapes": []}') as chat:
            result=await harness.query_understanding_async('write a poem',context=QueryContext())
        self.assertEqual(chat.await_count,3)
        self.assertEqual(result['candidates'],[])

    async def test_out_of_batch_duplicate_and_overlong_selections_rejected(self):
        for ids in (['invented'],['s0','s0'],['s0','s1','s2','s3']):
            with self.subTest(ids=ids),mock.patch.object(qu,'load_catalog',return_value=(catalog(4),'hash')),mock.patch.object(llm,'chat_async',return_value=json.dumps({'shapes':ids})):
                with self.assertRaises(runtime.Refused):
                    await harness.query_understanding_async('q',context=QueryContext())

    async def test_one_extraction_failure_does_not_discard_other_approaches(self):
        async def reply(system,user,**kw):
            if kw['stage']!='understand-extract': return '{"shapes":["s0","s1","s2"]}'
            if json.loads(user)['shape']['id']=='s1': return '{}'
            return json.dumps(extracted())
        with mock.patch.object(qu,'load_catalog',return_value=(catalog(3),'hash')),mock.patch.object(llm,'chat_async',side_effect=reply):
            r=await harness.query_understanding_async('q',context=QueryContext())
        self.assertEqual([c['status'] for c in r['candidates']],['ok','error','ok'])

    async def test_empty_question_never_calls_model(self):
        with mock.patch.object(llm,'chat_async') as chat:
            with self.assertRaises(runtime.Refused): await harness.query_understanding_async('',context=QueryContext())
        chat.assert_not_called()

    async def test_test_runner_calls_production_function(self):
        output={'candidates':[{'shape':'s2','status':'ok'}]}
        context=QueryContext(usage_ledger=llm.Ledger())
        with mock.patch.object(harness,'query_understanding_async',return_value=output) as production:
            result=await evaluate_case({'question':'fixture','expected':['s2'],'scoreable':True},context)
        production.assert_awaited_once_with('fixture',context=context)
        self.assertIs(result['output'],output)
        self.assertEqual(result['status'],'pass')

    async def test_discovery_preserves_candidates_and_explicit_source_filter(self):
        candidates=[{'shape':'s0','status':'ok','acquisition_queries':['first']},
                    {'shape':'s1','status':'ok','acquisition_queries':['second','first']}]
        ctx={'question':'q','candidates':candidates}
        with mock.patch.object(harness,'query_understanding_async',return_value=ctx),mock.patch.object(harness.ard_client,'search_many_async',return_value=[]) as search:
            result,_=await harness.discover_async('q',sites=['explicit'],context=QueryContext())
        self.assertIs(result,ctx)
        self.assertEqual(search.await_args.args[0],['q','first','second'])
        self.assertEqual(search.await_args.kwargs['sources'],['explicit'])

    async def test_empty_candidates_cannot_become_an_arbitrary_point_answer(self):
        with mock.patch.object(harness,'discover_async',return_value=({'candidates':[]},[])),mock.patch.object(harness.planner,'plan') as planner:
            with self.assertRaisesRegex(runtime.Refused,'agent finder returned no sources'):
                await harness.run('q',context=QueryContext())
        planner.assert_not_called()

    def test_real_catalog_has_76_entries_and_all_examples_survive(self):
        shapes,_=qu.load_catalog()
        self.assertEqual(len(shapes),76)
        for shape in shapes:
            self.assertEqual(qu.brief(shape)['examples'],shape['examples'])
            self.assertEqual(qu.brief(shape)['negative_examples'],shape['negative_examples'])
            self.assertTrue(shape['negative_examples'])
            for example in shape['negative_examples']:
                self.assertEqual(set(example),{'question','input_condition','why_not','instead'})
                self.assertTrue(all(isinstance(v,str) and v.strip() for v in example.values()))
                self.assertIn(example['instead'],{s['id'] for s in shapes})
                self.assertNotEqual(example['instead'],shape['id'])

    async def test_cancellation_does_not_leave_parallel_requests_running(self):
        started=asyncio.Event(); active=0
        async def reply(*a,**k):
            nonlocal active
            active+=1
            if active==3: started.set()
            try: await asyncio.Event().wait()
            finally: active-=1
        with mock.patch.object(qu,'load_catalog',return_value=(catalog(),'hash')),mock.patch.object(llm,'chat_async',side_effect=reply):
            task=asyncio.create_task(harness.query_understanding_async('q',context=QueryContext()))
            await asyncio.wait_for(started.wait(),2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertEqual(active,0)
