import unittest
from unittest.mock import AsyncMock, patch

import driver
import extensions
import harness
import sec_facts
from query_context import QueryContext


def fact(fy,end,val,filed,start=None):
    return {'fy':fy,'end':end,'val':val,'filed':filed,'start':start or f'{int(end[:4])-1}-02-01',
            'fp':'FY','form':'10-K'}


class AnnualTests(unittest.TestCase):
    def test_non_calendar_year_and_later_restatement(self):
        rows=[fact(2025,'2025-01-31',100,'2025-03-01'),
              fact(2026,'2026-01-31',150,'2026-03-01'),
              fact(2026,'2025-01-31',110,'2026-03-01')]
        got=sec_facts.select_annual(rows,'duration')
        self.assertEqual([(r['fy'],r['val']) for r in got],[(2025,110),(2026,150)])
        self.assertEqual(driver.pick_value(rows,'2025','duration',strict=True)['val'],110)

    def test_quarter_cannot_substitute_for_annual(self):
        row=fact(2025,'2025-12-31',3,'2026-02-01','2025-10-01')
        self.assertEqual(sec_facts.select_annual([row],'duration'),[])
        self.assertIsNone(driver.pick_value([row],'2025','duration',strict=True))


class ReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_ratio_uses_both_year_end_balances(self):
        ctx=QueryContext(sec_client=AsyncMock())
        ctx.sec_client.company_facts.return_value={'entityName':'A','facts':{'us-gaap':{
            'NetIncomeLoss':{'units':{'USD':[fact(2025,'2025-01-31',12,'2025-03-01')]}},
            'Assets':{'units':{'USD':[fact(2024,'2024-01-31',80,'2024-03-01'),
                                    fact(2025,'2025-01-31',100,'2025-03-01')]}}}}}
        numerator={'concepts':['NetIncomeLoss'],'unit':'USD','period_type':'duration'}
        balance={'concepts':['Assets'],'unit':'USD','period_type':'instant'}
        expression={'op':'multiply','args':[100,{'op':'divide','args':[{'input':0},
            {'op':'divide','args':[{'op':'add','args':[{'input':1},{'input':2}]},2]}]}]}
        request=extensions.Read({'xbrl':{'components':[numerator,balance,{**balance,'year_offset':-1}],
            'expression':expression,'unit':'percent'}},'s',parameters={'params':{'companies':['A'],'fiscal_year':2025}})
        with patch.object(harness,'_link_entity_async',AsyncMock(return_value=[{'label':'A','keys':{'cik':'1'}}])):
            got=await sec_facts.read(request,context=ctx)
        self.assertAlmostEqual(got.data[0]['value'],13.333333333333334)
    async def test_tag_transition_preserves_latest_years(self):
        ctx=QueryContext(sec_client=AsyncMock())
        ctx.sec_client.company_facts.return_value={'entityName':'Example','facts':{'us-gaap':{
            'Revenues':{'units':{'USD':[fact(2018,'2018-01-31',100,'2018-03-01')]}},
            'NewRevenue':{'units':{'USD':[fact(2025,'2025-01-31',200,'2025-03-01')]}}}}}
        request=extensions.Read({'xbrl':{'concepts':['Revenues','NewRevenue'],'unit':'USD'}},'s',
            parameters={'params':{'companies':['A'],'years':1}})
        with patch.object(harness,'_link_entity_async',AsyncMock(return_value=[{'label':'A','keys':{'cik':'1'}}])):
            got=await sec_facts.read(request,context=ctx)
        self.assertEqual(got.data[0]['fiscal_year'],2025)
        self.assertEqual(got.data[0]['value'],200)
    async def test_multiple_companies_return_separate_series_and_full_facts(self):
        ctx=QueryContext(sec_client=AsyncMock())
        ctx.sec_client.company_facts.return_value={'entityName':'Example','facts':{'us-gaap':{
            'Revenues':{'units':{'USD':[fact(2025,'2025-01-31',100,'2025-03-01')]}}}}}
        request=extensions.Read({'title':'revenue','xbrl':{'concepts':['Revenues'],'unit':'USD'}},'s',
            node={'operator':'MapReadSeries'},parameters={'params':{'companies':['A','B'],'years':1}})
        with patch.object(harness,'_link_entity_async',AsyncMock(return_value=[{'label':'Example','keys':{'cik':'1'}}])):
            got=await sec_facts.read(request,context=ctx)
        self.assertEqual(len(got.data),2)
        self.assertEqual(got.data[0][0]['value'],100)
        self.assertIn('facts',got.data[0][0])

    async def test_ratio_missing_prior_balance_does_not_substitute_current_balance(self):
        ctx=QueryContext(sec_client=AsyncMock())
        ctx.sec_client.company_facts.return_value={'facts':{'us-gaap':{
            'Assets':{'units':{'USD':[fact(2025,'2025-01-31',100,'2025-03-01')]}}}}}
        c={'concepts':['Assets'],'unit':'USD','period_type':'instant'}
        request=extensions.Read({'xbrl':{'components':[c,{**c,'year_offset':-1}],
            'expression':{'op':'divide','args':[{'input':0},{'input':1}]}}},'s',
            parameters={'params':{'companies':['A'],'fiscal_year':2025}})
        with patch.object(harness,'_link_entity_async',AsyncMock(return_value=[{'label':'A','keys':{'cik':'1'}}])):
            with self.assertRaisesRegex(Exception,'No annual facts cover'):
                await sec_facts.read(request,context=ctx)
