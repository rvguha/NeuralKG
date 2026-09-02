"""Exact contracts for deterministic structural validation (stage 9).

These tests intentionally import validation directly: no finder, model, catalog, or network is
part of the stage being tested.
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from domain import Check, QueryIntent
import validation


def intent(question, **changes):
    return QueryIntent(question, **changes)


def checks_by_name(verdict):
    return {check.name: check for check in verdict.checks}


class QuestionConstraintTests(unittest.TestCase):
    def test_unit_constraints_are_narrow_and_explicit(self):
        cases = {
            "Apple's diluted earnings per share": "per-share",
            "What is Apple's EPS?": "per-share",
            "What share of revenue came from grants?": "percent",
            "What was the response rate?": "percent",
            "What was total revenue?": None,
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(validation._question_unit(intent(question)), expected)

    def test_explicit_currency_constraints(self):
        cases = {
            "Revenue in U.S. dollars": "USD",
            "How much did it receive in euros?": "EUR",
            "What was profit in pounds sterling?": "GBP",
            "What was total revenue?": None,
        }
        for question, expected in cases.items():
            with self.subTest(question=question):
                self.assertEqual(validation._question_currency(intent(question)), expected)

    def test_response_unit_aliases_are_normalized(self):
        cases = {"%": "percent", "percentage": "percent", "pure": "percent",
                 "USD/share": "per-share", "dollars per share": "per-share",
                 "people": "people"}
        for unit, expected in cases.items():
            with self.subTest(unit=unit):
                self.assertEqual(validation._data_unit({"unit": unit}), expected)


class StructuralValidationTests(unittest.TestCase):
    def test_empty_or_non_object_response_is_rejected(self):
        for data in ({}, [], None):
            with self.subTest(data=data):
                verdict = validation.structural(intent("population"), data)
                self.assertFalse(verdict.accepted)
                self.assertEqual(verdict.reason, "empty or non-object response")

    def test_nested_suppression_sentinel_is_rejected(self):
        for value in (-888888888, "-666666666"):
            with self.subTest(value=value):
                verdict = validation.structural(
                    intent("poverty rate", measure="poverty rate"),
                    {"rows": [{"value": value}], "unit": "percent"})
                self.assertFalse(verdict.accepted)
                check = checks_by_name(verdict)["sentinel"]
                self.assertEqual(check.status, "fail")
                self.assertIn(str(value), check.reason)

    def test_zero_is_valid_data_not_a_missing_value(self):
        verdict = validation.structural(
            intent("What was the response rate?", measure="response rate"),
            {"value": 0, "unit": "percent", "metric": "response rate"})
        self.assertTrue(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["sentinel"].status, "pass")
        self.assertEqual(checks_by_name(verdict)["unit"].status, "pass")

    def test_per_share_question_is_not_treated_as_percent(self):
        verdict = validation.structural(
            intent("Apple's diluted earnings per share", measure="diluted earnings per share"),
            {"value": 6.42, "unit": "USD/share", "metric": "diluted earnings per share"})
        self.assertTrue(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["unit"].status, "pass")

    def test_wrong_unit_is_a_decisive_failure(self):
        verdict = validation.structural(
            intent("What share of revenue came from grants?"),
            {"value": 1200000, "unit": "USD"})
        self.assertFalse(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["unit"].status, "fail")
        self.assertEqual(verdict.reason, "asked for percent, got usd")

    def test_requested_unit_missing_is_inconclusive_not_rejected(self):
        verdict = validation.structural(
            intent("What was the response rate?"), {"value": 12.5})
        self.assertTrue(verdict.accepted)
        self.assertTrue(verdict.residual_semantic_check)
        self.assertEqual(checks_by_name(verdict)["unit"].status, "inconclusive")

    def test_wrong_explicit_currency_is_a_decisive_failure(self):
        verdict = validation.structural(
            intent("What was revenue in U.S. dollars?"),
            {"value": 4_000_000, "currency": "EUR"})
        self.assertFalse(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["currency"].status, "fail")
        self.assertEqual(verdict.reason, "asked for USD, got EUR")

    def test_unmarked_question_does_not_impose_currency(self):
        verdict = validation.structural(
            intent("What was total revenue?", measure="revenue"),
            {"value": 4_000_000, "currency": "EUR", "metric": "revenue"})
        self.assertTrue(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["currency"].status, "pass")

    def test_wrong_geographic_grain_is_a_decisive_failure(self):
        verdict = validation.structural(
            intent("Which counties have the highest income?"),
            {"rows": [{"name": "California"}], "grain": "state"})
        self.assertFalse(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["grain"].status, "fail")

    def test_period_mismatch_requires_residual_check_but_is_not_rejected(self):
        verdict = validation.structural(
            intent("What was revenue in 2022?", measure="revenue", period="2022"),
            {"value": 10, "year": "2021", "metric": "revenue"})
        self.assertTrue(verdict.accepted)
        self.assertTrue(verdict.residual_semantic_check)
        check = checks_by_name(verdict)["period"]
        self.assertEqual(check.status, "inconclusive")
        self.assertEqual(check.reason, "requested 2022; source returned 2021")

    def test_matching_canonical_entity_key_passes(self):
        verdict = validation.structural(
            intent("What was Apple's revenue?", entity="Apple", measure="revenue"),
            {"value": 10, "requested_entity": {"cik": "0000320193"},
             "entity": {"cik": "0000320193"}, "metric": "revenue"})
        self.assertTrue(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["entity-key"].status, "pass")

    def test_mismatched_canonical_entity_key_is_rejected(self):
        verdict = validation.structural(
            intent("What was Apple's revenue?", entity="Apple", measure="revenue"),
            {"value": 10, "requested_entity": {"cik": "0000320193"},
             "entity": {"cik": "0000789019"}, "metric": "revenue"})
        self.assertFalse(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["entity-key"].status, "fail")

    def test_entity_name_containment_passes_without_keys(self):
        verdict = validation.structural(
            intent("What was Microsoft revenue?", entity="Microsoft", measure="revenue"),
            {"value": 10, "company": "Microsoft Corporation", "metric": "revenue"})
        self.assertTrue(verdict.accepted)
        self.assertEqual(checks_by_name(verdict)["entity-name"].status, "pass")

    def test_wrong_unkeyed_entity_is_inconclusive_not_decisively_wrong(self):
        verdict = validation.structural(
            intent("What was Microsoft's revenue?", entity="Microsoft", measure="revenue"),
            {"value": 10, "company": "Apple Inc.", "metric": "revenue"})
        self.assertTrue(verdict.accepted)
        self.assertTrue(verdict.residual_semantic_check)
        self.assertEqual(checks_by_name(verdict)["entity-key"].status, "inconclusive")

    def test_distinct_measure_remains_inconclusive(self):
        verdict = validation.structural(
            intent("What was net income?", measure="net income"),
            {"value": 10, "metric": "operating income"})
        self.assertTrue(verdict.accepted)
        self.assertTrue(verdict.residual_semantic_check)
        self.assertEqual(checks_by_name(verdict)["measure"].status, "inconclusive")

    @unittest.expectedFailure
    def test_total_revenue_does_not_accept_program_service_revenue(self):
        """Known defect: dropping "total" turns a material Form 990 distinction into a match."""
        verdict = validation.structural(
            intent("What was total revenue?", measure="total revenue"),
            {"value": 10, "metric": "program service revenue"})
        self.assertTrue(verdict.residual_semantic_check)
        self.assertEqual(checks_by_name(verdict)["measure"].status, "inconclusive")

    def test_source_specific_failure_is_decisive(self):
        def source_rule(_intent, _data):
            return [Check("scope", "fail", "result does not cover the requested population")]

        verdict = validation.structural(intent("How many organizations?"), {"value": 10}, source_rule)
        self.assertFalse(verdict.accepted)
        self.assertEqual(verdict.reason, "result does not cover the requested population")


if __name__ == "__main__":
    unittest.main()
