import unittest
from unittest.mock import AsyncMock, patch

import extensions
import runtime
from plugins import atlas_accessors as atlas
from query_context import QueryContext


DESC = {'accessor': 'bigquery_guarded', 'trust': 'human-reviewed',
        'computation': {'runtime': {'sql': 'SELECT value FROM `p.d.t` WHERE year=@year',
                                   'parameters': [{'name': 'year', 'type': 'INT64'}]}}}


class AtlasTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        client = AsyncMock()
        client.dry_run.return_value = 50
        client.query.return_value = {'rows': [{'value': 7}], 'complete': True,
                                     'job': {'jobId': 'j'}, 'statistics': {'totalBytesBilled': '50'}}
        import llm, ard_client
        return QueryContext(bigquery_client=client, usage_ledger=llm.Ledger(),
                            discovery_ledger=ard_client.DiscoveryUsage())

    async def test_guarded_parameterized_query_and_shared_attempt_record(self):
        ctx = self.context()
        read = extensions.Read(DESC, 'opaque-resource', parameters={'params': {'year': '2023'}})
        with patch.object(atlas, 'configuration', return_value={'allowed_tables': ['p.d.t'], 'byte_cap': 100}):
            result = await atlas.guarded(read, context=ctx)
        self.assertEqual(result.data, [{'value': 7}])
        self.assertTrue(result.complete)
        args = ctx.bigquery_client.query.call_args.kwargs
        self.assertEqual(args['maximum_bytes_billed'], 100)
        self.assertEqual(args['query_parameters'][0]['parameterValue']['value'], '2023')
        self.assertEqual(ctx.operation_events[0]['status'], 'complete')
        self.assertIs(ctx.fork().operation_events, ctx.operation_events)

    async def test_production_template_plan_calls_plugin_and_renders_full_evidence(self):
        import json
        import harness
        import template_dag_execution as dag
        ctx = self.context()
        reg = extensions.Registry(); atlas.setup(reg)
        descriptor = {**DESC, 'access': {'operations': {'lookup': {
            'capability': {'synthesis': {'read_only': True, 'data_path': '0.value'}}}}}}
        plan = {'candidate': 'lookup.scalar', 'reason': 'reviewed value', 'parameters': {
            'a': {'source': 'opaque', 'operation': 'lookup', 'params': {'year': 2023}},
            'b': {'expression': {'input': 0}}}}
        understood = {'candidates': [{'shape': 'lookup.scalar', 'status': 'ok', 'applicability': 'plausible'}]}
        with patch.object(extensions, 'registry', return_value=reg), \
             patch.object(dag.driver, 'frontmatter', return_value=descriptor), \
             patch('planner.capabilities', return_value={'lookup': {'synthesis': {'read_only': True}}}), \
             patch.object(atlas, 'configuration', return_value={'allowed_tables': ['p.d.t']}), \
             patch.object(dag.llm, 'chat_async', AsyncMock(return_value=json.dumps(plan))), \
             patch.object(harness.TK, 'synthesize_async', AsyncMock(return_value='7')) as render:
            result = await dag.run('What is the value in 2023?', understood,
                                   [{'identifier': 'opaque', 'title': 'Reviewed value'}], context=ctx)
        self.assertEqual(result['answer'], '7')
        data = render.call_args.args[1]
        self.assertEqual(data['computed_result'], 7)
        evidence = data['retrieved_data'][0]['input']
        self.assertEqual(evidence['data'], 7)
        self.assertEqual(evidence['provenance']['payload'], [{'value': 7}])
        self.assertEqual(evidence['provenance']['execution']['params'], {'year': 2023})

    async def test_dry_run_cap_prevents_execution_and_retains_attempt(self):
        ctx = self.context(); ctx.bigquery_client.dry_run.return_value = 101
        with patch.object(atlas, 'configuration', return_value={'allowed_tables': ['p.d.t'], 'byte_cap': 100}):
            with self.assertRaises(runtime.Refused):
                await atlas.guarded(extensions.Read(DESC, 's', parameters={'params': {'year': 2023}}), context=ctx)
        ctx.bigquery_client.query.assert_not_awaited()
        self.assertEqual(ctx.operation_events[0]['status'], 'failed_or_cancelled')

    async def test_private_and_undeclared_tables_refused_before_io(self):
        ctx = self.context()
        with self.assertRaises(runtime.Refused):
            await atlas.guarded(extensions.Read({**DESC, 'visibility': 'private'}, 's'), context=ctx)
        with patch.object(atlas, 'configuration', return_value={'allowed_tables': ['p.other.t']}):
            with self.assertRaises(runtime.Refused):
                await atlas.guarded(extensions.Read(DESC, 's'), context=ctx)
        ctx.bigquery_client.dry_run.assert_not_awaited()

    async def test_nested_model_uses_context_and_rejects_wrong_row_quote(self):
        ctx = self.context()
        rows = [{'complaint_id': '1', 'consumer_complaint_narrative': 'payment was lost'},
                {'complaint_id': '2', 'consumer_complaint_narrative': 'card was stolen'}]
        ctx.bigquery_client.query.return_value['rows'] = rows
        read = extensions.Read(DESC, 's', parameters={'params': {'year': 2023}})
        reply = '{"themes":[{"name":"payments","quotes":[{"complaint_id":"1","quote":"payment was lost"},{"complaint_id":"2","quote":"payment was lost"}]}]}'
        with patch.object(atlas, 'configuration', return_value={'allowed_tables': ['p.d.t']}), \
             patch.object(atlas.llm, 'chat_async', AsyncMock(return_value=reply)) as chat:
            result = await atlas.themes(read, context=ctx)
        self.assertIs(chat.call_args.kwargs['context'], ctx)
        self.assertEqual(chat.call_args.kwargs['stage'], 'accessor-theme')
        self.assertEqual(len(result.data['themes'][0]['quotes']), 1)
        self.assertEqual(result.data['sample'], rows)
        self.assertFalse(result.complete)

    def test_strict_parameter_binding(self):
        for value in ('1.5', 'NaN', True):
            with self.assertRaises(runtime.Refused):
                atlas.parameters([{'name': 'n', 'type': 'INT64'}], {'n': value})
        bound, _ = atlas.parameters([{'name': 'n', 'type': 'INT64'}], {'n': '9007199254740993'})
        self.assertEqual(bound['n'], 9007199254740993)

    def test_sql_read_only_and_scope(self):
        atlas.validate_sql('WITH x AS (SELECT * FROM `p.d.t`) SELECT * FROM x', ['p.d.t'])
        for sql in ('DELETE FROM `p.d.t` WHERE true', 'SELECT 1; SELECT 2',
                    'SELECT * FROM `p.secret.t`', 'SELECT * FROM EXTERNAL_QUERY("c", "SELECT 1")'):
            with self.assertRaises(runtime.Refused): atlas.validate_sql(sql, ['p.d.t'])
