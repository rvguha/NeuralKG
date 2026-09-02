import io
import json
import os
import unittest
from unittest import mock

from tests import run_queries


class Response(io.BytesIO):
    def __enter__(self):
        return self
    def __exit__(self, *_):
        self.close()


class DeploymentSmokeTests(unittest.TestCase):
    def test_preflight_records_presence_not_credential_values(self):
        payload = Response(json.dumps({"ok": True, "version": "abc"}).encode())
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "super-secret"}, clear=True):
            manifest = run_queries.deployment_manifest(
                "https://example.test/ask", opener=lambda *_args, **_kwargs: payload)
        self.assertEqual(manifest["health"]["version"], "abc")
        self.assertTrue(manifest["credentials_present"]["OPENAI_API_KEY"])
        self.assertNotIn("super-secret", json.dumps(manifest))

    def test_preflight_names_target_health_failure(self):
        def fail(*_args, **_kwargs):
            raise TimeoutError("health timed out")
        manifest = run_queries.deployment_manifest("https://example.test/ask", opener=fail)
        self.assertEqual(manifest["target"], "https://example.test/ask")
        self.assertIn("health timed out", manifest["health_error"])

    def test_smoke_selection_covers_sources_and_complex_shapes(self):
        cases = [
            {"q": "a", "shape": "point", "dirs": ["alpha"]},
            {"q": "b", "shape": "point", "dirs": ["alpha"]},
            {"q": "c", "shape": "ranking", "dirs": ["alpha"]},
            {"q": "d", "shape": "point", "dirs": ["beta"]},
            {"q": "e", "shape": "ratio", "dirs": ["alpha", "beta"]},
        ]
        selected = run_queries.select_deployment_smoke(cases)
        self.assertEqual([case["q"] for case in selected], ["a", "c", "d", "e"])

    def test_transport_errors_are_classified_as_environment_errors(self):
        status, note = run_queries.classify({"expect": "answer", "shape": "point"}, None,
                                            "HTTP 429")
        self.assertEqual(status, "error")
        self.assertEqual(note, "HTTP 429")


if __name__ == "__main__":
    unittest.main()
