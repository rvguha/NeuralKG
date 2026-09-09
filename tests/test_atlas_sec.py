import unittest

import extensions
import runtime
from plugins import atlas_sec as sec
from query_context import QueryContext


def fact(tag, values, unit='USD'):
    return {tag: {'units': {unit: values}}}


class Client:
    async def resolve_company(self, value, context): return '320193', 'Apple Inc.'
    async def company_facts(self, cik, context):
        annual=lambda fy,val,filed: {'val':val,'fy':fy,'fp':'FY','form':'10-K','start':f'{fy}-01-01','end':f'{fy}-12-31','filed':filed}
        return {'entityName':'Apple Inc.','facts':{'us-gaap':{
          **fact('Revenues',[annual(2022,100,'2023-01-01'),annual(2023,120,'2024-01-01')]),
          **fact('NetIncomeLoss',[annual(2023,12,'2024-01-01')]),
          **fact('Assets',[{'val':80,'fy':2022,'fp':'FY','form':'10-K','end':'2022-12-31','filed':'2023-01-01'},
                            {'val':100,'fy':2023,'fp':'FY','form':'10-K','end':'2023-12-31','filed':'2024-01-01'}])}}}


class SecTests(unittest.IsolatedAsyncioTestCase):
    def context(self): return QueryContext(sec_client=Client())
    async def test_metric_series_and_annual(self):
        read=extensions.Read({},'sec',parameters={'params':{'company':'Apple','metric':'revenue','years':2}})
        result=await sec.metric(read,context=self.context())
        self.assertEqual([r['value'] for r in result.data],[100,120])
        annual=await sec.annual(extensions.Read({},'sec',parameters={'params':{
            'company':'Apple','metric':'revenue','fiscal_year':2023}}),context=self.context())
        self.assertEqual(annual.data[0]['value'],120)
        self.assertEqual(annual.provenance['payload']['rows'],annual.data)
        self.assertEqual(annual.period_basis,'fiscal-year')
    async def test_ratio_uses_average_balance_sheet_denominator(self):
        result=await sec.ratio(extensions.Read({},'sec',parameters={'params':{
            'company':'Apple','ratio':'roa','fiscal_year':2023}}),context=self.context())
        self.assertAlmostEqual(result.data[0]['ratio_pct'],13.333)
        self.assertEqual(result.data[0]['averaging'],'average of two fiscal year-ends')
    async def test_unknown_metric_and_missing_company_refuse(self):
        for p in ({'company':'Apple','metric':'made_up'},{'metric':'revenue'}):
            with self.assertRaises(runtime.Refused):
                await sec.metric(extensions.Read({},'sec',parameters={'params':p}),context=self.context())

if __name__ == '__main__': unittest.main()
