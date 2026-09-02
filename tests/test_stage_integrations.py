"""Deterministic tests across adjacent stage boundaries, with external edges injected."""
import unittest
from unittest import mock

import connectors
import planner
import renderers
from domain import Attempt, QueryIntent


class PlanToEvidenceIntegrationTests(unittest.TestCase):
    def test_selected_plan_flows_through_normalization_validation_and_evidence(self):
        intent = QueryIntent("What percentage lives below poverty?", measure="poverty rate")
        hit = {"identifier": "sources/census/poverty.md", "title": "Poverty rate"}
        capability = {"paths": ["key"], "grain": "county", "key": {"kind": "fips"}}
        with mock.patch.object(planner, "verdict",
                               return_value=("exact", "by_place", capability, "")):
            plan = planner.plan("point", [hit])
        attempt = Attempt("census", hit["identifier"])
        with mock.patch("driver.frontmatter", return_value={"title": "Percent below poverty"}), \
             mock.patch.object(planner, "capabilities", return_value={"by_place": capability}):
            evidence = connectors.for_hit(hit).execute(
                intent, attempt, hit,
                lambda: {"value": "12.5", "variable": "DP03_0128E", "period": "2024",
                         "grain": "county"})
        self.assertEqual(plan["operation"], "by_place")
        self.assertEqual(attempt.outcome, "accepted")
        self.assertEqual((evidence.value, evidence.unit, evidence.grain), (12.5, "%", "county"))
        self.assertEqual(evidence.provenance["source_document"], hit["identifier"])

    def test_normalized_sentinel_is_rejected_before_it_becomes_evidence(self):
        intent = QueryIntent("What percentage lives below poverty?", measure="poverty rate")
        hit = {"identifier": "sources/census/poverty.md", "title": "Poverty rate"}
        attempt = Attempt("census", hit["identifier"])
        with mock.patch("driver.frontmatter", return_value={"title": "Percent below poverty"}):
            with self.assertRaises(connectors.Rejected):
                connectors.CENSUS.execute(intent, attempt, hit,
                    lambda: {"value": "-888888888", "variable": "DP03_0128E"})
        self.assertEqual(attempt.outcome, "rejected")
        self.assertIn("sentinel", attempt.reason)

    def test_residual_semantic_check_controls_admission(self):
        intent = QueryIntent("What was net income?", measure="net income")
        hit = {"identifier": "sources/fixture/metric.md", "title": "Metric"}
        attempt = Attempt("fixture", hit["identifier"])
        adjudicator = mock.Mock(return_value=(False, "operating income is not net income"))
        with self.assertRaises(connectors.Rejected):
            connectors.GENERIC.execute(intent, attempt, hit,
                lambda: {"value": 10, "metric": "operating income"}, adjudicator)
        adjudicator.assert_called_once()
        self.assertEqual(attempt.reason, "operating income is not net income")


class EvidenceToRenderingIntegrationTests(unittest.TestCase):
    def test_status_boolean_survives_connector_and_renderer_boundary(self):
        intent = QueryIntent("Is it a 501(c)(3)?", operation="status",
                             measure="501(c)(3) status")
        hit = {"identifier": "sources/fixture/status.md", "title": "IRS status"}
        attempt = Attempt("fixture", hit["identifier"])
        evidence = connectors.GENERIC.execute(intent, attempt, hit, lambda: {
            "is_501c3": False, "status": "active", "organization": "Fixture nonprofit"})
        answer = renderers.render(evidence)
        self.assertIs(evidence.value, False)
        self.assertEqual(answer.evidence_kind, "status")
        self.assertIn("No", answer.text)

    def test_connector_records_exception_as_attempt_error(self):
        intent = QueryIntent("fixture")
        hit = {"identifier": "sources/fixture/data.md", "title": "Fixture"}
        attempt = Attempt("fixture", hit["identifier"])
        def fail():
            raise TimeoutError("publisher timed out")
        with self.assertRaises(TimeoutError):
            connectors.GENERIC.execute(intent, attempt, hit, fail)
        self.assertEqual(attempt.outcome, "error")
        self.assertEqual(attempt.reason, "publisher timed out")


class AsyncConnectorIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_executor_and_adjudicator_preserve_same_contract(self):
        intent = QueryIntent("What was net income?", measure="net income")
        hit = {"identifier": "sources/fixture/metric.md", "title": "Metric"}
        attempt = Attempt("fixture", hit["identifier"])
        async def execute():
            return {"value": 10, "metric": "operating income"}
        async def adjudicate(_data, _validation):
            return True, "reviewed equivalent"
        evidence = await connectors.GENERIC.execute_async(
            intent, attempt, hit, execute, adjudicate)
        self.assertEqual(attempt.outcome, "accepted")
        self.assertEqual(evidence.value, 10)


if __name__ == "__main__":
    unittest.main()
