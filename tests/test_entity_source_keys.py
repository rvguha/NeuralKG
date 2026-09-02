import json
import os
import unittest
from unittest import mock

import resolver

HERE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(HERE, "fixtures", "entity_source_keys.json")) as stream:
    CASES = json.load(stream)["cases"]


def entity_payload(case):
    claims = {}
    reverse = {name: prop for prop, name in resolver.PROPS.items()}
    for name, value in case["keys"].items():
        claims[reverse[name]] = [{"mainsnak": {"datavalue": {"value": value}}}]
    return {"labels": {"en": {"value": case["canonical"]}}, "claims": claims}


class EntitySourceKeyGoldenTests(unittest.IsolatedAsyncioTestCase):
    def test_fixture_is_stratified_and_reviewable(self):
        self.assertGreaterEqual(len(CASES), 30)
        self.assertGreaterEqual(len({case["type"] for case in CASES}), 5)
        self.assertGreaterEqual(len({case["ambiguity"] for case in CASES}), 8)
        for case in CASES:
            with self.subTest(mention=case["mention"]):
                self.assertTrue(case["qid"].startswith("Q"))
                self.assertIsInstance(case["equivalent_keys"], dict)

    def test_wikidata_claims_map_to_source_keys_exactly(self):
        for case in CASES:
            with self.subTest(mention=case["mention"]):
                label, keys = resolver._claim_values(entity_payload(case))
                self.assertEqual(label, case["canonical"])
                self.assertEqual(keys, case["keys"])

    async def test_resolver_returns_selected_canonical_identity_and_keys(self):
        for sequence, case in enumerate(CASES):
            # A unique mention avoids resolver's process cache affecting another fixture row.
            mention = f"{case['mention']} [golden-{sequence}]"
            candidates = [{"id": "Qwrong", "label": "Wrong"},
                          {"id": case["qid"], "label": case["canonical"]}]
            async def pick(_mention, _type, _candidates):
                return case["qid"]
            with self.subTest(mention=case["mention"]), \
                 mock.patch.object(resolver, "search_async", return_value=candidates), \
                 mock.patch.object(resolver, "claims_async",
                                   return_value=(case["canonical"], case["keys"])):
                actual = await resolver.resolve_async(mention, case["type"], pick, context=object())
                self.assertEqual(actual, {"qid": case["qid"], "label": case["canonical"],
                                          "keys": case["keys"]})

    def test_reviewed_equivalent_keys_are_local_not_global(self):
        alphabet = next(case for case in CASES if case["mention"] == "Alphabet")
        self.assertEqual(alphabet["equivalent_keys"]["ticker"], ["GOOG", "GOOGL"])
        microsoft = next(case for case in CASES if case["mention"] == "Microsoft")
        self.assertNotIn("ticker", microsoft["equivalent_keys"])


if __name__ == "__main__":
    unittest.main()
