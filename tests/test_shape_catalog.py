"""Offline guards against silent catalog/corpus corruption and missing common coverage."""
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1]

class UniqueKeysLoader(yaml.SafeLoader):
    pass

def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f'Duplicate YAML key: {key}')
        result[key] = loader.construct_object(value_node, deep=deep)
    return result

UniqueKeysLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)

class ShapeCatalogTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT/'shapes/query-shapes.yaml').read_bytes()
        cls.entries = yaml.load(cls.raw, Loader=UniqueKeysLoader)['shapes']
        cls.ids = {e['id'] for e in cls.entries}

    def test_duplicate_yaml_keys_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Duplicate YAML key'):
            yaml.load('facets: first\nfacets: second\n', Loader=UniqueKeysLoader)

    def test_ids_and_neighbor_references_are_valid(self):
        self.assertEqual(len(self.ids), len(self.entries))
        for entry in self.entries:
            with self.subTest(shape=entry['id']):
                self.assertTrue(entry['asks'])
                self.assertTrue(entry['examples'])
                self.assertFalse(set(entry.get('not', {})) - self.ids)
                self.assertNotIn(entry['id'], entry.get('not', {}))
                self.assertFalse(entry['id'].startswith('legacy.'))

    def test_common_result_forms_have_explicit_coverage(self):
        required = {'point.value','point.status','records.for-entity','timeseries.single-entity',
                    'rank.top-n','aggregate.sum','comparison.values','point.ratio',
                    'records.matching','assoc.correlation-population'}
        self.assertFalse(required - self.ids)

    def test_corpora_have_no_stale_labels_or_missing_shapes(self):
        for name in ('shape_queries.json','shape_common_queries.json'):
            corpus = json.loads((ROOT/'tests'/name).read_text())
            self.assertEqual(corpus['n'],len(corpus['cases']))
            covered = set()
            for row in corpus['cases']:
                labels = row.get('accepted')
                if labels is None:
                    labels = [row['gold']] if isinstance(row['gold'],str) else row['gold']
                self.assertFalse(set(labels)-self.ids, (row['q'], labels))
                if len(labels)>1:
                    self.assertTrue(row.get('label_reason'), row['q'])
                covered.update(labels)
            if name == 'shape_queries.json':
                self.assertFalse(self.ids-covered)
                self.assertEqual(corpus['entries'],len(self.ids))
                self.assertEqual(corpus['catalog_sha256'],hashlib.sha256(self.raw).hexdigest())

    def test_evaluator_rejects_unknown_ids_and_retries_empty_completions(self):
        sys.path.insert(0, str(ROOT/'tests'))
        import shape_catalog_eval as evaluator
        with patch.object(evaluator.llm, 'chat', side_effect=[None, '{"shape":"missing.id"}', '{"shape":"point.value"}']):
            result = evaluator.classify('A fixed question', 'A fixed card', {'point.value'})
        self.assertEqual(result['pick'],'point.value')
        self.assertEqual(result['attempts'],3)

    def test_evaluator_returns_error_after_exhausted_retries(self):
        sys.path.insert(0, str(ROOT/'tests'))
        import shape_catalog_eval as evaluator
        with patch.object(evaluator.llm, 'chat', return_value=None):
            result = evaluator.classify('A fixed question', 'A fixed card', {'point.value'})
        self.assertEqual(result['pick'],'ERROR')
        self.assertEqual(len(result['errors']),4)

    def test_reviewer_sees_question_and_proposal_but_not_expected_labels(self):
        sys.path.insert(0, str(ROOT/'tests'))
        import shape_catalog_eval as evaluator
        with patch.object(evaluator.llm, 'chat', return_value='{"shape":"point.value", "evidence":"one amount"}') as chat:
            result = evaluator.review('A fixed question', {'pick':'aggregate.sum'},
                                      [{'id':'point.value','asks':'one amount'}], {'point.value'})
        prompt = chat.call_args.args[1]
        self.assertIn('A fixed question',prompt)
        self.assertIn('aggregate.sum',prompt)
        self.assertNotIn('accepted',prompt)
        self.assertNotIn('gold',prompt)
        self.assertEqual(result['initial_pick'],'aggregate.sum')

    def test_scores_include_errors_and_separate_restored_common_cases(self):
        sys.path.insert(0, str(ROOT/'tests'))
        import shape_catalog_eval as evaluator
        rows = [{'cohort':'common','pick':'point.value','correct':True},
                {'cohort':'common','pick':'ERROR','correct':False},
                {'cohort':'rest','pick':None,'correct':True},
                {'cohort':'rest','pick':'point.value','correct':True,'provenance':'authored-common-regression'}]
        metrics = evaluator.score_rows(rows)
        self.assertEqual(metrics['common']['accuracy'],.5)
        self.assertEqual(metrics['common']['errors'],1)
        self.assertEqual(metrics['rest']['total'],1)
        self.assertEqual(metrics['restored_common']['total'],1)

if __name__ == '__main__':
    unittest.main()
