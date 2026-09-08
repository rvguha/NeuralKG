import unittest
from unittest.mock import AsyncMock, patch
import template_execution as te
import runtime
from query_context import QueryContext
import harness


class TemplateExecutionTests(unittest.TestCase):
    def test_period_substitution_is_rejected(self):
        te.check_period('2023',{'period':'FY2023'})
        with self.assertRaises(runtime.Refused):te.check_period('2023',{'period':'FY2025'})
        with self.assertRaises(runtime.Refused):te.check_period('2023',{'period':'Q12023'})
    def test_arithmetic_and_no_code(self):
        self.assertEqual(te.expression({'op':'divide','args':[{'read':0},{'read':1}]},[30,100]),.3)
        for value in ('__import__("os")', {'read':-1}, {'op':'eval','args':[1,2]}, {'op':'divide','args':[1,0]}):
            with self.assertRaises(runtime.Refused):te.expression(value,[1])

    def test_plan_cannot_invent_sources_or_change_read_count(self):
        p={'candidate':'lookup.scalar','reads':[{'source':'invented'}]}
        with self.assertRaises(runtime.Refused):te.validate(p,[{'shape':'lookup.scalar'}],[{'identifier':'actual'}])
        p['reads']=[]
        with self.assertRaises(runtime.Refused):te.validate(p,[{'shape':'lookup.scalar'}],[])


class WiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_candidates_dispatch_to_new_executor(self):
        with patch.object(harness,'discover_async',AsyncMock(return_value=({'candidates':[]},[]))), patch.object(te,'run',AsyncMock(return_value={'answer':'fixed'})) as execute:
            answer=await harness.run('q',context=QueryContext())
        self.assertEqual(answer,{'answer':'fixed'})
        execute.assert_awaited_once()
