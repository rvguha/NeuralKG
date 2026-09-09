"""One accessor registration must work on BOTH dispatch paths.

The defect this guards against is the reason the seam was unified: `executor` reached only the
scalar fetch and `template_reader` only the DAG reader, so an instance that registered the
documented hook and ran the production template path got silence rather than an error.
"""
import os, sys, unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, 'tests', 'fixtures'))

import answer_synthesizer as synth
import extensions
import runtime
import template_dag_execution as dag
from query_context import QueryContext

DESCRIPTOR = {'accessor': 'demo_rows', 'title': 'Demo'}
HITS = [{'identifier': 'demo/rows.md', 'title': 'Demo'}]


def _load(names=('accessor_demo',)):
    extensions.reset()
    with patch('instance.extensions', return_value=list(names)):
        extensions.registry()


class AccessorRegistrationTests(unittest.TestCase):
    def setUp(self):
        import accessor_demo
        accessor_demo.CALLS.clear()
        _load()

    def tearDown(self):
        extensions.reset()

    def test_one_registration_is_visible_to_both_lookups(self):
        self.assertIsNotNone(extensions.accessor('demo_rows'))
        name, fn = extensions.accessor_for(DESCRIPTOR)
        self.assertEqual(name, 'demo_rows')
        self.assertIsNotNone(fn)

    def test_descriptor_without_an_accessor_is_left_alone(self):
        self.assertEqual(extensions.accessor_for({'field': 'x'}), (None, None))
        self.assertEqual(extensions.accessor_for(None), (None, None))

    def test_unknown_accessor_refuses_and_names_the_loaded_set(self):
        with self.assertRaises(runtime.Refused) as caught:
            extensions.accessor_for({'accessor': 'not_installed'})
        self.assertIn('not_installed', str(caught.exception))
        self.assertIn('demo_rows', str(caught.exception))   # says what IS loaded

    def test_duplicate_registration_is_an_error(self):
        reg = extensions.Registry()
        reg.accessor('x')(lambda *a, **k: None)
        with self.assertRaises(ValueError):
            reg.accessor('x')(lambda *a, **k: None)


class BothDispatchPathsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        import accessor_demo
        self.demo = accessor_demo
        self.demo.CALLS.clear()
        _load()

    def tearDown(self):
        extensions.reset()

    async def test_dag_path_reaches_the_accessor_and_keeps_the_full_payload(self):
        node = {'operator': 'ReadScalar'}
        params = {'source': 'demo/rows.md', 'entity': 'A', 'period': '2023'}
        with patch('driver.frontmatter', return_value=DESCRIPTOR):
            result = await dag.read(node, params, [], hits=HITS, context=QueryContext())
        self.assertIsInstance(result, synth.Input)
        self.assertEqual(result.data['rows'], [{'entity': 'A', 'value': 1, 'unit': 'USD'},
                                               {'entity': 'B', 'value': 2, 'unit': 'USD'}])
        self.assertEqual(result.units, {'value': 'USD'})
        self.assertEqual(result.period_basis, 'fiscal-year')
        self.assertEqual(self.demo.CALLS[-1]['path'], 'dag')

    async def test_scalar_path_reaches_the_same_accessor(self):
        import harness
        state = {'hit': HITS[0], 'key': None, 'period': '2023'}
        with patch('driver.frontmatter', return_value=DESCRIPTOR):
            payload = await harness._fetch_async(state, {'attribute': 'value', 'entity': 'A'},
                                                 context=QueryContext())
        self.assertEqual(payload['value'], 1)            # .data, complete, not a projection
        self.assertEqual(payload['rows'][0]['entity'], 'A')
        self.assertEqual(self.demo.CALLS[-1]['path'], 'scalar')

    async def test_the_same_plugin_served_both_without_a_second_interface(self):
        node = {'operator': 'ReadScalar'}
        with patch('driver.frontmatter', return_value=DESCRIPTOR):
            await dag.read(node, {'source': 'demo/rows.md'}, [], hits=HITS, context=QueryContext())
            import harness
            await harness._fetch_async({'hit': HITS[0], 'period': 'latest'}, {},
                                       context=QueryContext())
        self.assertEqual([c['path'] for c in self.demo.CALLS], ['dag', 'scalar'])

    async def test_refusal_propagates_so_the_engine_can_backtrack(self):
        node = {'operator': 'ReadScalar'}
        with patch('driver.frontmatter', return_value=DESCRIPTOR):
            with self.assertRaises(runtime.Refused):
                await dag.read(node, {'source': 'demo/rows.md', 'fail': 'refuse'}, [],
                               hits=HITS, context=QueryContext())

    async def test_a_wrong_return_type_is_refused_not_silently_passed_on(self):
        reg = extensions.registry()
        async def bad(read, *, context): return {'rows': []}
        reg.accessors['bad_accessor'] = bad
        with patch('driver.frontmatter', return_value={'accessor': 'bad_accessor'}):
            with self.assertRaises(runtime.Refused) as caught:
                await dag.read({'operator': 'ReadScalar'}, {'source': 'demo/rows.md'}, [],
                               hits=HITS, context=QueryContext())
        self.assertIn('answer_synthesizer.Input', str(caught.exception))


class AdvertisementTests(unittest.TestCase):
    def tearDown(self):
        extensions.reset()

    def test_a_source_naming_an_uninstalled_accessor_is_not_advertised(self):
        _load(names=())          # nothing registered
        with patch('driver.frontmatter', return_value=DESCRIPTOR), \
             patch('planner.capabilities', return_value={}):
            self.assertEqual(dag.available_sources(HITS), [])

    def test_an_installed_accessor_is_advertised(self):
        _load()
        with patch('driver.frontmatter', return_value=DESCRIPTOR), \
             patch('planner.capabilities', return_value={}):
            advertised = dag.available_sources(HITS)
        self.assertEqual(len(advertised), 1)
        self.assertEqual(advertised[0]['accessor'], 'demo_rows')


if __name__ == '__main__':
    unittest.main()
