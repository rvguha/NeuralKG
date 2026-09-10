import unittest

import numpy as np

from registry import index


class RetrievalDiversityTests(unittest.TestCase):
    def test_vintages_share_a_family_but_distinct_tables_do_not(self):
        family=index._retrieval_family
        self.assertEqual(family('catalog/acs/place_2018_5yr.md'),
                         family('catalog/acs/place_2017_5yr.md'))
        self.assertNotEqual(family('catalog/acs/place_2018_5yr.md'),
                            family('catalog/acs/county_2018_5yr.md'))

    def test_candidate_window_keeps_only_three_vintages_per_family(self):
        meta=[{'identifier':f'catalog/acs/place_{year}_5yr.md'} for year in range(2020,2015,-1)]
        meta.append({'identifier':'catalog/datacommons/place.md'})
        got=index._embedding_candidates(meta,np.array([1,.99,.98,.97,.96,.5]),None,10)
        self.assertEqual([item['identifier'] for item in got],
            ['catalog/acs/place_2020_5yr.md','catalog/acs/place_2019_5yr.md',
             'catalog/acs/place_2018_5yr.md','catalog/datacommons/place.md'])

    def test_marketplace_dataset_cannot_fill_the_whole_window(self):
        meta=[{'identifier':f'catalog/bigquery/acs/table_{i}.md'} for i in range(12)]
        meta.append({'identifier':'catalog/attested/datacommons.md'})
        got=index._embedding_candidates(meta,np.arange(13,0,-1),None,20)
        self.assertEqual(sum('/bigquery/acs/' in x['identifier'] for x in got),8)
        self.assertIn('catalog/attested/datacommons.md',[x['identifier'] for x in got])


if __name__ == '__main__':
    unittest.main()
