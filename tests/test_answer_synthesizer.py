import copy
import unittest
import answer_synthesizer as a
import core
from runtime import Refused
from template_operator_cases import cases, F, E, supplied, JOIN, R, S


class AllTemplateTests(unittest.TestCase):
    def test_computed_answer_contract_cannot_be_replaced_by_an_operand(self):
        self.assertIn('operands and provenance only',core._SYNTHESIS_SYSTEM)
        self.assertIn('answer_contract value',core._SYNTHESIS_SYSTEM)

    def test_one_series_alignment_is_identity(self):
        rows=[{'date':2023,'value':7}]
        self.assertEqual(a.align_time([[rows]],{'joins':[]}),rows)

    def assertDeep(self,actual,expected):
        if type(expected) is float:self.assertAlmostEqual(actual,expected,places=8)
        elif isinstance(expected,list):
            self.assertEqual(len(actual),len(expected))
            for x,y in zip(actual,expected):self.assertDeep(x,y)
        elif isinstance(expected,dict):
            self.assertEqual(set(actual),set(expected))
            for key in expected:self.assertDeep(actual[key],expected[key])
        else:self.assertEqual(actual,expected)

    def test_every_catalog_template_has_fixed_input_and_expected_output(self):
        fixtures=cases()
        self.assertEqual(set(fixtures),set(a.load_templates()))
        self.assertFalse(any(a.coverage().values()))
        for name,case in fixtures.items():
            with self.subTest(template=name):
                result=a.synthesize(name,case['inputs'],case['parameters'])['result']
                if case.get('assert_fields'):result=[r['allocation'] for r in result]
                self.assertDeep(result,case['expected'])

    def test_partial_populations_refused_for_every_template(self):
        for name,case in cases().items():
            with self.subTest(template=name):
                inputs=copy.deepcopy(case['inputs']);next(iter(inputs.values())).complete=False
                with self.assertRaises(Refused):a.synthesize(name,inputs,case['parameters'])

    def test_untrusted_data_cannot_bypass_adapter_evidence(self):
        with self.assertRaises(Refused):a.synthesize('lookup.scalar',{'a':10},{'b':{'expression':{'input':0}}})

    def test_cross_source_identity_and_cardinality(self):
        node={'operator':'Join'}; left=supplied(R);right=supplied(S)
        right.key_domains={'entity':'different-identifier-system'}
        with self.assertRaisesRegex(Refused,'crosswalk'):a.calculate(node,[left,right],JOIN)
        with self.assertRaisesRegex(Refused,'uniqueness'):a.join_rows(R,[*S,S[0]],JOIN)

    def test_arithmetic_null_zero_and_injection(self):
        for expression in [E('divide',1,0),E('add',None,1),E('eval','x'),{'python':'import os'},E('power',-1,.5)]:
            with self.subTest(expression=expression),self.assertRaises(Refused):a.expr(expression)

    def test_full_join_multiplicity_is_not_silently_distinct(self):
        p={**JOIN,'cardinality':'many-to-many'}
        self.assertEqual(len(a.join_rows([R[0],R[0]],[S[0],S[0]],p)),4)

    def test_unknown_eligibility_is_not_false(self):
        self.assertIsNone(a.rule_expr(E('gt',F('income'),10),{}))
        self.assertIs(a.rule_expr(E('and',False,E('gt',F('income'),10)),{}),False)

    def test_empty_division_is_vacuous_only_when_bound(self):
        fixture=cases()['join.division'];fixture['inputs']['c'].data=[]
        self.assertEqual(a.synthesize('join.division',fixture['inputs'],fixture['parameters'])['result'],[{'entity':'A'},{'entity':'B'},{'entity':'C'}])
        fixture['parameters']['f']['empty_set_policy']='clarify'
        with self.assertRaises(Refused):a.synthesize('join.division',fixture['inputs'],fixture['parameters'])
