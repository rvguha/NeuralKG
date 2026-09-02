import json
import os
import unittest
from unittest import mock

from tests import stage_llm_eval


class LlmStageFixtureContracts(unittest.IsolatedAsyncioTestCase):
    def test_every_case_has_unique_id_and_explicit_expectation(self):
        with open(stage_llm_eval.FIXTURE, encoding="utf-8") as stream:
            fixtures = json.load(stream)
        identifiers = []
        self.assertEqual(set(fixtures), set(stage_llm_eval.RUNNERS))
        for stage, cases in fixtures.items():
            self.assertTrue(cases, stage)
            for case in cases:
                identifiers.append(case["id"])
                self.assertTrue(any(key.startswith("expected_") for key in case), case["id"])
        self.assertEqual(len(identifiers), len(set(identifiers)))

    async def test_synthesis_comparison_normalizes_only_unicode_whitespace(self):
        case = {"question": "q", "data": {},
                "expected_contains": ["$12.34 billion"], "expected_absent": ["total"]}
        with mock.patch.object(stage_llm_eval.harness.TK, "synthesize_async",
                               mock.AsyncMock(return_value="$12.34\u202fbillion")):
            passed, actual = await stage_llm_eval.synthesis(case)
        self.assertTrue(passed, actual)

    def test_fixture_is_resolved_relative_to_the_runner(self):
        self.assertTrue(os.path.isfile(stage_llm_eval.FIXTURE))


if __name__ == "__main__":
    unittest.main()
