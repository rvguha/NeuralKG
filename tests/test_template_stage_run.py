import unittest
from template_stage_run import corpus


class TemplateStageRunTests(unittest.TestCase):
    def test_all_76_authored_templates_are_covered(self):
        rows=[r for r in corpus() if r['cohort']=='template-examples']
        self.assertEqual(len(rows),76)
        self.assertEqual(len({r['expected'][0] for r in rows}),76)

    def test_full_corpus_has_unique_case_identifiers(self):
        rows=corpus()
        self.assertEqual(len(rows),779)
        self.assertEqual(len({r['id'] for r in rows}),len(rows))

    def test_conditional_migrations_are_not_exact_gold(self):
        for row in corpus():
            if len(row.get('conditional_routes',[]))>1:
                self.assertFalse(row['scoreable'])

    def test_disputed_negatives_are_unscored(self):
        rows=[r for r in corpus() if r['cohort']=='negatives']
        self.assertEqual(len(rows),45)
        self.assertTrue(all(not r['scoreable'] for r in rows))
