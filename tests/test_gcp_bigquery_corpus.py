import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from tools import gen_gcp_bigquery_corpus as generator
import agent_finder
from registry import index


class GcpBigQueryCorpusTests(unittest.TestCase):
    def test_checked_in_catalog_counts_and_uniqueness(self):
        public, marketplace = generator.load_inputs()
        self.assertEqual(len(public), 357)
        self.assertEqual(len(marketplace), 241)
        self.assertEqual(len({x["datasetReference"]["datasetId"] for x in public}), 357)
        self.assertEqual(len({x["url"] for x in marketplace}), 241)
        self.assertTrue(all(x["url"].startswith(
            "https://console.cloud.google.com/marketplace/product/") for x in marketplace))
        public_meta = json.loads(
            (generator.CATALOG / "gcp-bigquery-public-datasets.meta.json").read_text())
        self.assertEqual(public_meta["captured_at"], "2026-08-26")

    def test_mechanical_projection_preserves_effective_okf(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            access = root / "_access.md"
            leaf = root / "leaf.md"
            generator.write_okf(access, {
                "type": "Data Source", "publisher": "Inherited Publisher",
                "nested": {"auth": "gcp"}, "collision": "access",
                "trust": {"identity": "did:web:cloud.google.com", "identityType": "did"},
            }, "access body")
            generator.write_okf(leaf, {
                "type": "Dataset", "title": "Leaf", "description": "Description",
                "tags": ["bigquery"], "representativeQueries": ["What is here?"],
                "source": "./_access.md", "collision": "leaf",
            }, "# Leaf body")
            with mock.patch.object(generator, "ROOT", root):
                entry = generator.mechanical_ard(access, leaf, "family", "cloud.google.com")

        self.assertEqual(entry["displayName"], "Leaf")
        self.assertEqual(entry["okf:publisher"], "Inherited Publisher")
        self.assertEqual(entry["okf:nested"], {"auth": "gcp"})
        self.assertEqual(entry["okf:collision"], "leaf")
        self.assertEqual(entry["okf:source"], "./_access.md")
        self.assertEqual(entry["okf:type"], "Dataset")
        self.assertEqual(entry["data"], {"content": "# Leaf body\n"})
        self.assertNotIn("okf:title", entry)
        self.assertNotIn("okf:representativeQueries", entry)

    def test_generated_entries_are_complete_and_truthful_about_cost(self):
        manifest = json.loads((generator.CORPUS / "manifest.json").read_text())
        self.assertEqual(manifest["counts"], {"public": 357, "marketplace_free": 241})
        self.assertEqual(manifest["okf_entries"], 598)
        self.assertEqual(manifest["ard_entries"], 598)

        public_json = sorted((generator.ARD / "public").glob("*.json"))
        market_json = sorted((generator.ARD / "marketplace-free").glob("*.json"))
        self.assertEqual((len(public_json), len(market_json)), (357, 241))
        for path in public_json + market_json:
            entry = json.loads(path.read_text())
            self.assertEqual(entry["@context"][1], {"okf": generator.OKF_NS})
            self.assertEqual(set(entry["data"]), {"content"})
            self.assertTrue(entry["representativeQueries"])
            self.assertIn("usage charges", entry["okf:accessCost"])
            self.assertIn("apply", entry["okf:accessCost"])
        self.assertTrue(all(json.loads(path.read_text())["okf:marketplacePrice"] == "free"
                            for path in market_json))

    def test_discovery_query_fixture_references_real_entries(self):
        cases = json.loads((generator.ROOT / "tests" / "fixtures" /
                            "gcp_bigquery_corpus_queries.json").read_text())
        identifiers = {json.loads(path.read_text())["identifier"]
                       for path in generator.ARD.glob("*/*.json")}
        self.assertEqual(len(cases), 22)
        self.assertTrue(all(case["expected"] in identifiers for case in cases))
        self.assertEqual(len({case["query"] for case in cases}), len(cases))

    def test_runtime_indexes_the_separate_corpus_without_copying_it_to_sources(self):
        docs, _texts = index._collect_docs("fixture-model")
        usa = next(doc for doc in docs if doc["identifier"].endswith("/public/usa-names.md"))
        self.assertEqual(agent_finder.publisher(usa["identifier"]), "gcp-bigquery-public")
        self.assertEqual(agent_finder._access_document(usa["identifier"]),
                         "corpora/gcp-bigquery/okf/public/_access.md")


if __name__ == "__main__":
    unittest.main()
