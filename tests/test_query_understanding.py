import json
import unittest
from unittest import mock

import ard_client
import harness
import llm
from query_context import QueryContext


def context():
    return QueryContext(usage_ledger=llm.Ledger(), discovery_ledger=ard_client.DiscoveryUsage())


class QueryUnderstandingTests(unittest.IsolatedAsyncioTestCase):
    async def understand(self, shape, entity, measure):
        # Structure and entity are ONE call: six of eleven shapes are defined by entity count, and
        # splitting them measured 92.2% -> 82.1% on the 308-case corpus. The mock therefore returns
        # their union first, then measure/period.
        with mock.patch.object(llm, "chat_async", mock.AsyncMock(
                side_effect=[json.dumps({**shape, **entity}), json.dumps(measure)])) as chat:
            result = await harness.query_understanding_async("fixture question", context=context())
        return result, chat

    async def test_two_calls_merge_into_one_context(self):
        result, chat = await self.understand(
            {"shape": "point", "subshape": "single-value", "threshold": None,
             "quantifier": "exhaustive"},
            {"entity": "Apple", "entities": [], "type": "company",
             "canonical_entity": "Apple Inc.", "entity_description": "The technology company",
             "entity_status": "resolved", "entity_candidates": []},
            {"attribute": "total revenue", "period": "FY2023", "periods": [],
             "interpretations": []})
        self.assertEqual((result["shape"], result["subshape"]), ("point", "single-value"))
        self.assertEqual((result["entity"], result["canonical_entity"]), ("Apple", "Apple Inc."))
        self.assertEqual((result["attribute"], result["period"]), ("total revenue", "FY2023"))
        self.assertNotIn("sources", result)
        self.assertEqual(chat.await_count, 2)

    async def test_structure_call_completes_before_the_measure_call(self):
        """Measure is the only genuinely one-directional dependency: `attribute` is the question
        minus the entity, so it must run after the entity spans exist."""
        started = []
        async def answer(system, _question, **_kwargs):
            if system == harness._structure_understanding_system():
                started.append("structure")
                return '{"shape":"point","entity":"","entities":[],"entity_status":"none"}'
            self.assertEqual(started, ["structure"])       # measure never runs first
            started.append("measure")
            return '{"attribute":"national debt","period":"latest"}'
        with mock.patch.object(llm, "chat_async", side_effect=answer):
            await harness.query_understanding_async("US national debt", context=context())
        self.assertEqual(started, ["structure", "measure"])

    async def test_measure_prompt_receives_entity_spans_but_no_source_vocabulary(self):
        result, chat = await self.understand(
            {"shape": "point"},
            {"entity": "Chicago", "entities": [], "canonical_entity": "Chicago, Illinois",
             "entity_description": "The city in Illinois", "type": "city",
             "entity_status": "resolved"},
            {"attribute": "unemployment rate for Asian residents", "period": "latest"})
        measure_system = chat.await_args_list[1].args[0]   # call 0 is structure+entity
        self.assertIn("Chicago, Illinois", measure_system)
        self.assertNotIn("SOURCES:", measure_system)
        self.assertEqual(result["attribute"], "unemployment rate for Asian residents")

    async def test_ambiguous_candidates_keep_full_descriptions_for_clarification(self):
        candidates = [{"name": "Springfield, Illinois", "description": "Illinois state capital",
                       "type": "city"},
                      {"name": "Springfield, Massachusetts", "description": "City in Massachusetts",
                       "type": "city"}]
        result, _ = await self.understand(
            {"shape": "point"},
            {"entity": "Springfield", "entities": [], "canonical_entity": "",
             "entity_status": "ambiguous", "entity_candidates": candidates, "type": "city"},
            {"attribute": "population", "period": "latest"})
        self.assertEqual(result["entity_candidates"], candidates)
        self.assertEqual(result["canonical_entity"], "")

    async def test_multiple_entities_keep_descriptions_for_later_crosswalk(self):
        details = [
            {"mention": "Harvard", "name": "Harvard University",
             "description": "Private research university in Cambridge, Massachusetts",
             "type": "university"},
            {"mention": "MIT", "name": "Massachusetts Institute of Technology",
             "description": "Private research university in Cambridge, Massachusetts",
             "type": "university"},
        ]
        result, _ = await self.understand(
            {"shape": "comparison"},
            {"entity": "", "entities": details, "canonical_entity": "",
             "entity_status": "resolved", "entity_candidates": [], "type": "university"},
            {"attribute": "NIH funding", "period": "latest"})
        self.assertEqual(result["entities"], ["Harvard", "MIT"])
        self.assertEqual(result["entity_details"], details)
        self.assertEqual(result["shape"], "comparison")

    async def test_shape_entity_consistency_is_checked_after_merge(self):
        result, _ = await self.understand(
            {"shape": "ranking", "subshape": "top-n"},
            {"entity": "Apple", "entities": [], "canonical_entity": "Apple Inc.",
             "entity_status": "resolved", "type": "company"},
            {"attribute": "revenue", "period": "latest"})
        self.assertEqual(result["shape"], "point")


class DiscoveryModularityTests(unittest.IsolatedAsyncioTestCase):
    async def test_ard_search_is_unfiltered_without_explicit_caller_override(self):
        understood = {"shape": "point", "subshape": "", "entity": "Apple", "entities": [],
                      "type": "company", "canonical_entity": "Apple Inc.",
                      "entity_description": "", "entity_status": "resolved",
                      "entity_candidates": [], "attribute": "revenue", "period": "latest",
                      "periods": [], "interpretations": [], "threshold": None,
                      "quantifier": "exhaustive", "question": "Apple revenue"}
        with mock.patch.object(harness, "query_understanding_async",
                               mock.AsyncMock(return_value=understood)), \
             mock.patch.object(ard_client, "search_many_async",
                               mock.AsyncMock(return_value=[])) as search:
            await harness.discover_async("Apple revenue", context=context())
        self.assertIsNone(search.await_args.kwargs["sources"])

    async def test_explicit_caller_filter_passes_through_without_local_catalog_validation(self):
        understood = {"shape": "topical", "subshape": "keyword", "entity": "", "entities": [],
                      "type": "none", "canonical_entity": "", "entity_description": "",
                      "entity_status": "none", "entity_candidates": [], "attribute": "", 
                      "period": "latest", "periods": [], "interpretations": [], "threshold": None,
                      "quantifier": "exhaustive", "question": "new domain data"}
        with mock.patch.object(harness, "query_understanding_async",
                               mock.AsyncMock(return_value=understood)), \
             mock.patch.object(ard_client, "search_many_async",
                               mock.AsyncMock(return_value=[])) as search:
            await harness.discover_async("new domain data", sites=["remote-ard-source"],
                                         context=context())
        self.assertEqual(search.await_args.kwargs["sources"], ["remote-ard-source"])


if __name__ == "__main__":
    unittest.main()
