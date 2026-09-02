import unittest
from unittest import mock

from tests import route_eval


class RetrievalRigTests(unittest.TestCase):
    def test_evaluate_keeps_prefilter_and_reranker_recall_separate(self):
        pre = [{"publisher": "wrong"}, {"publisher": "expected"}]
        reranked = [{"publisher": "expected"}, {"publisher": "wrong"}]
        with mock.patch.object(route_eval.ard_client, "search", side_effect=[pre, reranked]):
            row = route_eval.evaluate({"q": "question", "dirs": ["expected"], "shape": "point"})
        self.assertFalse(row["prefilter"]["recall"]["1"])
        self.assertTrue(row["prefilter"]["recall"]["5"])
        self.assertTrue(row["reranker"]["recall"]["1"])

    def test_evaluate_bypasses_classification_and_uses_identical_query_for_both_arms(self):
        with mock.patch.object(route_eval.ard_client, "search", return_value=[]) as search:
            route_eval.evaluate({"q": "labelled question", "dirs": ["source"]})
        self.assertEqual(search.call_args_list, [
            mock.call("labelled question", k=15, rerank=False),
            mock.call("labelled question", k=15, rerank=True)])

    def test_summary_excludes_errors_from_recall_counts_but_reports_them(self):
        rows = [{"error": "provider failed"}, {"error": None,
                 "prefilter": {"recall": {"1": False, "5": True, "15": True}},
                 "reranker": {"recall": {"1": True, "5": True, "15": True}}}]
        summary = route_eval.summarize(rows)
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["prefilter"]["recall@1"], 0)
        self.assertEqual(summary["reranker"]["recall@1"], 1)


if __name__ == "__main__":
    unittest.main()
