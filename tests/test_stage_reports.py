import asyncio
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import stage_reports as reports
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient


class StageReportsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {'TEST_RESULTS_DIR': str(self.root / 'results')})
        self.env.start()
        self.client = TestClient(Starlette(routes=[Route('/tests/', reports.serve), Route('/tests/{artifact:path}', reports.serve)]))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def publish(self):
        manifest = {'run_id': 'run-one', 'instance': 'local', 'model': 'offline', 'scope': 'test fixture'}
        rows = [{'id': 'x', 'question': '<script>alert(1)</script>', 'understanding': {'status': 'pass', 'summary': '<img src=x>', 'output': {'value': 3}}}]
        reports.publish(reports.results_root() / 'run-one', manifest, rows)
        return rows

    def test_atomic_json_round_trip(self):
        p = self.root / 'a' / 'x.json'
        reports.atomic_json(p, {'a': 1})
        self.assertEqual(json.loads(p.read_text()), {'a': 1})
        self.assertFalse(p.with_suffix('.json.tmp').exists())

    def test_html_escapes_all_result_content(self):
        self.publish()
        text = self.client.get('/tests/run-one/understanding.html').text
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('<script>alert', text)
        self.assertNotIn('<img src=x>', text)

    def test_index_and_four_stage_pages(self):
        self.publish()
        self.assertIn('run-one', self.client.get('/tests/').text)
        for stage in reports.STAGES:
            self.assertEqual(self.client.get(f'/tests/run-one/{stage}.html').status_code, 200)

    def test_raw_artifact_is_exact(self):
        rows = self.publish()
        response = self.client.get('/tests/run-one/results.json')
        self.assertEqual(response.json(), rows)
        self.assertEqual(response.headers['cache-control'], 'no-store')

    def test_unknown_and_wrong_extension_are_not_served(self):
        reports.results_root().mkdir(parents=True)
        (reports.results_root() / 'secret.env').write_text('not served')
        self.assertEqual(self.client.get('/tests/secret.env').status_code, 404)
        self.assertEqual(self.client.get('/tests/missing.json').status_code, 404)

    def test_symlink_escape_is_rejected(self):
        reports.results_root().mkdir(parents=True)
        outside = self.root / 'secret.json'
        outside.write_text('{}')
        (reports.results_root() / 'escape.json').symlink_to(outside)
        self.assertEqual(self.client.get('/tests/escape.json').status_code, 404)

    def test_instances_get_separate_default_roots(self):
        with patch.dict(os.environ, {'TEST_RESULTS_DIR': ''}), patch.object(reports.instance, 'config', return_value={}):
            with patch.object(reports.instance, 'path', return_value='/tmp/a/instance.yaml'):
                a = reports.results_root()
            with patch.object(reports.instance, 'path', return_value='/tmp/b/instance.yaml'):
                b = reports.results_root()
        self.assertNotEqual(a, b)

    def test_relative_config_directory_is_instance_relative(self):
        with patch.dict(os.environ, {'TEST_RESULTS_DIR': ''}), patch.object(reports.instance, 'config', return_value={'tests': {'results_dir': 'saved'}}), patch.object(reports.instance, 'path', return_value='/tmp/a/instance.yaml'):
            self.assertEqual(reports.results_root(), Path('/tmp/a/saved').resolve())
