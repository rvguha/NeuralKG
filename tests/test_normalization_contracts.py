"""Frozen input/output contracts for deterministic connector normalization (stage 8)."""
import unittest
from unittest import mock

import connectors
from domain import QueryIntent


INTENT = QueryIntent("fixture question")
HIT = {"identifier": "sources/fixture/metric.md"}


class NumericNormalizationTests(unittest.TestCase):
    def test_exact_integer_string_stays_an_integer(self):
        self.assertEqual(connectors._numeric("9007199254740993"), 9007199254740993)

    def test_decimal_string_becomes_float(self):
        self.assertEqual(connectors._numeric(" 12.50 "), 12.5)

    def test_nonnumeric_publisher_value_is_preserved(self):
        for value in (None, "N/A", "12 people", {"value": 12}):
            with self.subTest(value=value):
                self.assertIs(connectors._numeric(value), value)


class CensusNormalizationTests(unittest.TestCase):
    def normalize(self, data, frontmatter=None):
        with mock.patch("driver.frontmatter", return_value=frontmatter or {}):
            return connectors.CENSUS.normalize(INTENT, HIT, data)

    def test_numeric_value_is_normalized_without_mutating_raw_payload(self):
        raw = {"value": "43125", "variable": "B19013_001E"}
        actual = self.normalize(raw)
        self.assertEqual(actual["value"], 43125)
        self.assertEqual(raw["value"], "43125")

    def test_profile_percentage_variable_sets_percent_unit(self):
        actual = self.normalize({"value": "18.7", "variable": "DP05_0077PE"})
        self.assertEqual((actual["value"], actual["unit"]), (18.7, "%"))

    def test_official_percent_title_sets_unit_when_code_is_not_sufficient(self):
        actual = self.normalize({"value": "6.2", "variable": "DP03_0128E"},
                                {"title": "Percent below poverty level"})
        self.assertEqual(actual["unit"], "%")

    def test_estimate_suffix_alone_does_not_claim_percent(self):
        actual = self.normalize({"value": "100", "variable": "DP05_0001E"},
                                {"title": "Total population"})
        self.assertNotIn("unit", actual)

    def test_suppression_sentinel_survives_for_validation(self):
        actual = self.normalize({"value": "-888888888", "variable": "B19013_001E"})
        self.assertEqual(actual["value"], -888888888)
        self.assertFalse(connectors.CENSUS.validate(INTENT, actual).accepted)


class TreasuryNormalizationTests(unittest.TestCase):
    def normalize(self, field, data):
        with mock.patch("driver.frontmatter", return_value={"tfield": field}):
            return connectors.TREASURY.normalize(INTENT, HIT, data)

    def test_amount_field_declares_usd(self):
        actual = self.normalize("tot_pub_debt_out_amt", {"value": "35000000000000"})
        self.assertEqual(actual["value"], 35_000_000_000_000)
        self.assertEqual((actual["unit"], actual["currency"]), ("USD", "USD"))

    def test_percent_field_declares_percent(self):
        actual = self.normalize("avg_interest_pct", {"value": "4.25"})
        self.assertEqual((actual["value"], actual["unit"]), (4.25, "%"))

    def test_count_field_declares_count(self):
        actual = self.normalize("security_count", {"value": "17"})
        self.assertEqual((actual["value"], actual["unit"]), (17, "count"))

    def test_unknown_field_preserves_explicit_unit(self):
        actual = self.normalize("description", {"value": "open", "unit": "text"})
        self.assertEqual(actual, {"value": "open", "unit": "text"})


class ConnectorSelectionTests(unittest.TestCase):
    def test_source_families_select_their_specialized_normalizer(self):
        cases = {
            "sources/census/income.md": connectors.CENSUS,
            "sources/treasury/debt.md": connectors.TREASURY,
            "sources/sec-edgar/assets.md": connectors.SEC,
            "sources/sec-bq/assets.md": connectors.BIGQUERY,
            "sources/irs-grants/grants.md": connectors.GRANTS,
            "sources/cdc-places/diabetes.md": connectors.GENERIC,
        }
        for identifier, expected in cases.items():
            with self.subTest(identifier=identifier):
                self.assertIs(connectors.for_hit({"identifier": identifier}), expected)


if __name__ == "__main__":
    unittest.main()
