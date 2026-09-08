import copy
import unittest

from template_expectations import apply, expectation, score
from template_stage_run import corpus


class ExpectationTests(unittest.TestCase):
    def setUp(self):
        self.gold = {'acceptable': [{'template': x, 'review': 'reviewed'} for x in ('a', 'b')],
                     'rejected': [{'template': 'bad'}], 'exhaustive': False, 'binding_checks': []}

    def assess(self, *shapes):
        return score({'candidates': [{'shape': s, 'status': 'ok'} for s in shapes]}, self.gold)

    def test_multiple_acceptable(self):
        result = self.assess('a', 'b')
        self.assertTrue(result['coverage'])
        self.assertEqual(result['candidate_precision'], 1)

    def test_explicitly_wrong(self):
        self.assertEqual(self.assess('a', 'bad')['candidate_precision'], .5)
        self.assertFalse(self.assess('bad')['coverage'])

    def test_unknown_not_wrong(self):
        result = self.assess('a', 'unknown')
        self.assertTrue(result['coverage'])
        self.assertIsNone(result['candidate_precision'])
        self.assertEqual(result['precision_bounds'], [.5, 1])
        self.assertIsNone(self.assess('unknown')['coverage'])

    def test_empty_not_perfect_precision(self):
        self.assertIsNone(self.assess()['candidate_precision'])
        self.assertIsNone(self.assess()['coverage'])

    def test_extraction_separate(self):
        result = score({'candidates': [{'shape': 'a', 'status': 'error'}]}, self.gold)
        self.assertTrue(result['coverage'])
        self.assertEqual(result['extraction_errors'], 1)

    def test_nih_period_separate(self):
        fixture = {'question': 'What fraction of all NIH grant dollars goes to Johns Hopkins?', 'expected': []}
        result = score({'candidates': [{'shape': 'lookup.binary', 'status': 'ok',
                                       'bindings': {'period': 'all_time'}}]}, expectation(fixture))
        self.assertTrue(result['coverage'])
        self.assertEqual(result['binding_failures'], 1)

    def test_missing_output_preserves_error(self):
        row = {'understanding': {'status': 'error', 'summary': 'provider failed'}}
        apply(row, self.gold)
        self.assertEqual(row['understanding']['status'], 'error')

    def test_expectations_independent_and_nonmutating(self):
        for fixture in corpus():
            before = copy.deepcopy(fixture)
            gold = expectation(fixture)
            self.assertEqual(fixture, before)
            fixture['understanding'] = {'output': {'candidates': [{'shape': 'arbitrary'}]}}
            self.assertEqual(expectation(fixture), gold)
            accepted = {a['template'] for a in gold['acceptable']}
            self.assertFalse(accepted & {a['template'] for a in gold['rejected']})


if __name__ == '__main__':
    unittest.main()
