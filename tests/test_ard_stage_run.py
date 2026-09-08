import unittest
from unittest.mock import AsyncMock, patch
import ard_stage_run as runner
from query_context import QueryContext


class SavedDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_is_a_copy_and_checks_question(self):
        source = {'question': 'q', 'candidates': []}
        token = runner.saved_output.set(source)
        try:
            result = await runner.replay('q', context=None)
            result['candidates'].append({})
            self.assertEqual(source['candidates'], [])
            with self.assertRaises(AssertionError):
                await runner.replay('different', context=None)
        finally:
            runner.saved_output.reset(token)

    async def test_actual_discovery_receives_saved_acquisition_queries(self):
        source = {'question': 'q', 'candidates': [
            {'shape': 'a', 'status': 'ok', 'acquisition_queries': ['one', 'two']},
            {'shape': 'b', 'status': 'ok', 'acquisition_queries': ['two', 'three']}]}
        token = runner.saved_output.set(source)
        search = AsyncMock(return_value=[{'identifier': 'fixed'}])
        try:
            with patch.object(runner.harness, 'query_understanding_async', runner.replay), patch.object(runner.ard_client, 'search_many_async', search):
                output, hits = await runner.harness.discover_async('q', context=QueryContext())
            self.assertEqual(output, source)
            self.assertEqual(hits, [{'identifier': 'fixed'}])
            self.assertEqual(search.call_args.args[0], ['one', 'two', 'three'])
            self.assertIsNone(search.call_args.kwargs['sources'])
        finally:
            runner.saved_output.reset(token)
