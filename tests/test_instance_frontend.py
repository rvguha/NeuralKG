import os
import pathlib
import tempfile
import unittest

import instance
import instance_frontend


class InstanceFrontendTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.directory.name)
        (root / "page.html").write_text(
            "<title>{{INSTANCE_NAME}}</title><script>window.x={{EXAMPLES_JSON}}</script>",
            encoding="utf-8")
        (root / "site.css").write_text(":root{--ink:#123}", encoding="utf-8")
        (root / "site.js").write_text("window.ready=true", encoding="utf-8")
        self.config = root / "instance.yaml"
        self.config.write_text("""
identity: {name: Example Atlas}
frontend: {template: page.html, stylesheet: site.css, javascript: site.js}
examples:
  - label: Places
    queries:
      - Population?
""", encoding="utf-8")
        os.environ["INSTANCE_CONFIG"] = str(self.config)
        instance.reload()

    def tearDown(self):
        os.environ.pop("INSTANCE_CONFIG", None)
        instance.reload()
        self.directory.cleanup()

    def test_instance_owns_html_css_and_javascript(self):
        page = instance_frontend.page()
        self.assertIn("<title>Example Atlas</title>", page)
        self.assertIn('"label": "Places"', page)
        self.assertEqual(instance_frontend.asset("stylesheet"), ":root{--ink:#123}")
        self.assertEqual(instance_frontend.asset("javascript"), "window.ready=true")

    def test_frontend_assets_cannot_escape_the_instance_directory(self):
        self.config.write_text("frontend: {template: ../outside.html}\n", encoding="utf-8")
        instance.reload()
        with self.assertRaises(ValueError):
            instance_frontend.page()

    def test_default_instance_has_no_alternate_page(self):
        os.environ["INSTANCE_CONFIG"] = "/nonexistent/instance.yaml"
        instance.reload()
        self.assertIsNone(instance_frontend.page())
        self.assertIsNone(instance_frontend.asset("stylesheet"))


if __name__ == "__main__":
    unittest.main()
