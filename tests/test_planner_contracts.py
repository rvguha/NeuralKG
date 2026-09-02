"""Exact capability-boundary contracts for the deterministic planner (stage 4)."""
import os
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import planner


def cap(*paths, grain="organization", complete=True, key_kind=None,
        complete_for=None):
    value = {"paths": list(paths), "grain": grain,
             "population": {"complete": complete}}
    if key_kind:
        value["key"] = {"kind": key_kind}
    if complete_for:
        value["page"] = {"complete_for": complete_for}
    return value


class PlannerVerdictTests(unittest.TestCase):
    def verdict(self, shape, capabilities):
        with mock.patch.object(planner, "capabilities", return_value=capabilities):
            return planner.verdict(shape, "fixture.md")

    def test_point_uses_any_keyed_operation_directly(self):
        verdict, operation, _, why = self.verdict(
            "point", {"by_entity": cap("key", key_kind="canonical-id")})
        self.assertEqual((verdict, operation, why), ("exact", "by_entity", ""))

    def test_nonpopulation_shape_without_access_assumes_keyed_read(self):
        verdict, operation, capability, why = self.verdict("status", {})
        self.assertEqual(verdict, "exact")
        self.assertIsNone(operation)
        self.assertEqual(capability, {})
        self.assertIn("assuming keyed read", why)

    def test_server_order_at_entity_grain_answers_ranking_exactly(self):
        verdict, operation, _, why = self.verdict(
            "ranking", {"top": cap("order", grain="county")})
        self.assertEqual((verdict, operation, why), ("exact", "top", ""))

    def test_complete_enumeration_requires_client_side_ranking(self):
        verdict, operation, _, _ = self.verdict(
            "ranking", {"all": cap("enumerate", grain="organization")})
        self.assertEqual((verdict, operation), ("compose:scan-and-rank", "all"))

    def test_ordered_subentity_rows_cannot_rank_entities(self):
        verdict, operation, capability, why = self.verdict(
            "ranking", {"projects": cap("order", grain="project")})
        self.assertEqual(verdict, "infeasible")
        self.assertIsNone(operation)
        self.assertEqual(capability, {})
        self.assertIn("entity-grain population scan", why)
        self.assertIn("order", why)

    def test_incomplete_enumeration_cannot_answer_population_shape(self):
        verdict, _, _, why = self.verdict(
            "aggregate", {"partial": cap("enumerate", complete=False)})
        self.assertEqual(verdict, "infeasible")
        self.assertIn("enumerate", why)

    def test_correlation_requires_materializing_entity_population(self):
        verdict, operation, _, _ = self.verdict(
            "correlation", {"all": cap("enumerate", grain="county")})
        self.assertEqual((verdict, operation),
                         ("compose:materialize-and-correlate", "all"))

    def test_filtered_subset_uses_complete_population_scan(self):
        verdict, operation, _, _ = self.verdict(
            "filtered-subset", {"all": cap("enumerate", grain="company")})
        self.assertEqual((verdict, operation), ("compose:scan-and-filter", "all"))


class PlannerSelectionTests(unittest.TestCase):
    def test_direct_plan_beats_earlier_composed_plan(self):
        hits = [{"identifier": "enumerated", "title": "Enumerated"},
                {"identifier": "ordered", "title": "Ordered"}]
        verdicts = {
            "enumerated": ("compose:scan-and-rank", "all", cap("enumerate"), ""),
            "ordered": ("exact", "top", cap("order"), ""),
        }
        with mock.patch.object(planner, "verdict",
                               side_effect=lambda shape, ident: verdicts[ident]):
            result = planner.plan("ranking", hits)
        self.assertEqual(result["verdict"], "exact")
        self.assertEqual(result["hit"]["identifier"], "ordered")
        self.assertEqual([a["identifier"] for a in result["alternatives"]], ["enumerated"])

    def test_semantic_discovery_order_breaks_equal_fitness_ties(self):
        hits = [{"identifier": "first", "title": "First"},
                {"identifier": "second", "title": "Second"}]
        with mock.patch.object(planner, "verdict",
                               return_value=("exact", "top", cap("order"), "")):
            result = planner.plan("ranking", hits)
        self.assertEqual(result["hit"]["identifier"], "first")

    def test_infeasible_candidates_preserve_refusal_reasons(self):
        hits = [{"identifier": "projects", "title": "Projects", "publisher": "NIH"}]
        with mock.patch.object(planner, "verdict", return_value=(
                "infeasible", None, {}, "no entity-grain population scan")):
            result = planner.plan("ranking", hits)
        self.assertEqual(result["verdict"], "infeasible")
        self.assertIn("NIH", result["why"])
        self.assertEqual(result["rejected"][0]["outcome"], "structurally-infeasible")

    def test_existential_filter_can_generate_and_test_complete_keyed_reads(self):
        hit = {"identifier": "keyed", "title": "Keyed source"}
        keyed = cap("key", grain="entity", key_kind="canonical-id")
        with mock.patch.object(planner, "verdict", return_value=(
                "infeasible", None, {}, "no population scan")), \
             mock.patch.object(planner, "capabilities", return_value={"by_id": keyed}):
            result = planner.plan("filtered-subset", [hit], quantifier="existential")
        self.assertEqual(result["verdict"], "compose:generate-and-test")
        self.assertEqual(result["operation"], "by_id")

    def test_exhaustive_filter_never_uses_generate_and_test(self):
        hit = {"identifier": "keyed", "title": "Keyed source"}
        keyed = cap("key", grain="entity", key_kind="canonical-id")
        with mock.patch.object(planner, "verdict", return_value=(
                "infeasible", None, {}, "no population scan")), \
             mock.patch.object(planner, "capabilities", return_value={"by_id": keyed}):
            result = planner.plan("filtered-subset", [hit], quantifier="exhaustive")
        self.assertEqual(result["verdict"], "infeasible")


if __name__ == "__main__":
    unittest.main()
