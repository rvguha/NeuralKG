import unittest
from unittest.mock import AsyncMock, patch
import template_execution as te
import runtime
from query_context import QueryContext
import harness


class TemplateExecutionTests(unittest.TestCase):
    def test_derived_display_winner_difference(self):
        plan={'candidate':'compare.derived-values','reads':[{'entity':e} for e in ['A','A','B','B']],
              'pair_expression':{'op':'divide','args':[{'read':0},{'read':1}]},'direction':'max'}
        evidence=[{'value':v,'unit':u,'currency':'USD' if u=='USD' else None} for v,u in [(100,'USD'),(5,'persons'),(90,'USD'),(3,'persons')]]
        self.assertEqual([r['value'] for r in te.derived_result(plan,evidence)],[20,30])
        plan['candidate']='compare.derived-winner'
        self.assertEqual(te.derived_result(plan,evidence)[0]['entity'],'B')
        plan['candidate']='compare.derived-difference'
        self.assertEqual(te.derived_result(plan,evidence),-10)
        evidence[1]['value']=0
        with self.assertRaises(runtime.Refused):te.derived_result(plan,evidence)

    def test_derived_rejects_dimensionally_invalid_expression(self):
        with self.assertRaises(runtime.Refused):
            te.expression_units({'op':'add','args':[{'read':0},{'read':1}]},[{'unit':'USD'},{'unit':'persons'}])

    def test_derived_plan_rejects_missing_operand_or_mixed_entities(self):
        read={'entity':'A','type':'company','measure':'revenue','period':'2023','question':'revenue for A','source':'s'}
        plan={'candidate':'compare.derived-values','reads':[dict(read),dict(read)],
              'pair_expression':{'op':'divide','args':[{'read':0},{'read':1}]}}
        candidates=[{'shape':'compare.derived-values'}]; hits=[{'identifier':'s'}]
        te.validate(plan,candidates,hits)
        plan['reads'][1]['entity']='B'
        with self.assertRaises(runtime.Refused):te.validate(plan,candidates,hits)
        plan['reads']=[read]
        with self.assertRaises(runtime.Refused):te.validate(plan,candidates,hits)
        plan['reads']=[read,dict(read)]
        plan['pair_expression']={'read':0}
        with self.assertRaises(runtime.Refused):te.validate(plan,candidates,hits)

    def test_structure_validation_does_not_evaluate_placeholder_data(self):
        expr={'op':'divide','args':[{'read':0},{'op':'subtract','args':[{'read':0},{'read':1}]}]}
        self.assertEqual(te.expression_references(expr,2),{0,1})
        self.assertEqual(te.expression(expr,[6,3]),2)

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
    async def test_planner_repair_includes_failed_output_and_reason(self):
        good={'candidate':'lookup.scalar','reads':[{'source':'s','entity':'A','type':'company','measure':'revenue','period':'2023','question':'A revenue'}],'expression':{'read':0}}
        import json
        context=QueryContext()
        with patch.object(te.llm,'chat_async',AsyncMock(side_effect=['{"candidate":"lookup.scalar","reads":[]}',json.dumps(good),'{"valid":true,"issues":[]}'])) as chat:
            result=await te.compile_plan({'question':'q'},[{'shape':'lookup.scalar'}],[{'identifier':'s'}],context=context)
        self.assertEqual(result,good)
        repaired=json.loads(chat.call_args_list[1].args[1])['repair']
        self.assertIn('no required reads',repaired['error'])
        self.assertIn('"reads":[]',repaired['previous_output'])
        self.assertEqual(len(context.memo['planning_attempts']),2)

    async def test_candidates_dispatch_to_new_executor(self):
        with patch.object(harness,'discover_async',AsyncMock(return_value=({'candidates':[]},[]))), patch.object(te,'run',AsyncMock(return_value={'answer':'fixed'})) as execute:
            answer=await harness.run('q',context=QueryContext())
        self.assertEqual(answer,{'answer':'fixed'})
        execute.assert_awaited_once()
