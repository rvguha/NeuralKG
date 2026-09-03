"""One engine, many instances: the config file must actually change behaviour.

The claim this file defends is that standing up Neural KG over a different ARD -- a different
world, with different kinds of thing in it -- is a configuration change, not a code change. A
config that only *described* the deployment while the code kept its own hard-coded vocabulary
would pass a shallower test and be worthless. So these assert on the query path: given an
instance file describing the Hugging Face Hub, the engine must classify `repo_id` as an
identifier and must stop treating `cik` as one.

The Hub is used rather than an invented corpus because its parameter names are real and
checkable -- `/api/models/{repo_id}`, `?author=openbmb` selecting an account's models, and
`?search=MiniCPM` matching a string. That distinction is exactly what `domains` encodes: an
identity a returned record can be validated against, versus a guess that must be resolved
before anything is trusted.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import instance                                                       # noqa: E402


def write(text):
    handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
    handle.write(text)
    handle.close()
    return handle.name


class InstanceSwapTests(unittest.TestCase):
    def use(self, text):
        path = write(text)
        os.environ["INSTANCE_CONFIG"] = path
        instance.reload()
        self.addCleanup(lambda: (os.environ.pop("INSTANCE_CONFIG", None), instance.reload(),
                                 os.unlink(path)))

    def test_a_different_world_changes_what_counts_as_an_identifier(self):
        """The query path reads this, so it is the assertion that matters."""
        # Write question lists as block sequences, not inline `[...]`: YAML rejects a bare
        # "?" inside a flow sequence, and every sample question ends in one.
        self.use("""
identity: {name: Model Hub KG}
ard: {finder_url: "http://finder.internal:9000"}
domains:
  - name: model
    identifiers: [repo_id, model_id]
  - name: organization
    identifiers: [author]
  - name: dataset
    identifiers: ["dataset*"]
name_selectors: [search]
""")
        self.assertTrue(instance.is_identifier("repo_id"))
        self.assertTrue(instance.is_identifier("author"))
        self.assertTrue(instance.is_identifier("dataset_id"))       # trailing * is a prefix
        self.assertFalse(instance.is_identifier("search"))          # a query, not an identity
        self.assertFalse(instance.is_identifier("cik"))             # this ARD has no companies
        self.assertEqual(instance.name_selectors(), ("search",))
        self.assertEqual(instance.identity()["name"], "Model Hub KG")
        self.assertEqual(instance.finder_url(), "http://finder.internal:9000")

    def test_derive_classifies_parameters_through_the_instance(self):
        """derive.py is the consumer; assert through it, not just through the accessor."""
        self.use("domains:\n  - {name: model, identifiers: [repo_id]}\n")
        import derive
        self.assertTrue(instance.is_identifier("repo_id"))
        self.assertFalse(instance.is_identifier("ein"))
        # derive reads the vocabulary rather than carrying its own copy
        self.assertNotIn("_KEY_ID", vars(derive))

    def test_a_declared_instance_does_not_inherit_another_corpus_questions(self):
        """The trap this caught: `examples() or <legacy literal>`.

        An instance over the Hugging Face Hub correctly declares no example questions of its
        own, and inherited a homepage of US nonprofit questions -- "What was the American Red
        Cross total revenue?" offered against a corpus of models. A file that exists governs,
        including where it is silent.
        """
        self.use("identity: {name: Model Hub KG}\ndomains:\n  - {name: model, identifiers: [repo_id]}\n")
        import importlib
        import harness
        importlib.reload(harness)
        self.addCleanup(lambda: (instance.reload(), importlib.reload(harness)))
        self.assertEqual(harness.EXAMPLE_TABS, [])
        self.assertEqual(harness._SOURCE_ORDER, [])
        self.assertIn("<h1>Model Hub KG</h1>", harness.PAGE)
        self.assertNotIn("American Red Cross", harness.PAGE)

    def test_environment_overrides_the_file(self):
        """A container is configured by its platform; a file baked into an image must not win."""
        self.use('ard: {finder_url: "http://from-file:1234"}')
        os.environ["AGENT_FINDER_URL"] = "http://from-env:5678"
        self.addCleanup(os.environ.pop, "AGENT_FINDER_URL", None)
        self.assertEqual(instance.finder_url(), "http://from-env:5678")

    def test_a_missing_file_is_not_an_error(self):
        """An instance that wants the defaults should not have to write them out."""
        os.environ["INSTANCE_CONFIG"] = "/nonexistent/instance.yaml"
        instance.reload()
        self.addCleanup(lambda: (os.environ.pop("INSTANCE_CONFIG", None), instance.reload()))
        self.assertEqual(instance.config(), {})
        self.assertTrue(instance.is_identifier("cik"))              # fallback vocabulary
        self.assertEqual(instance.identity()["name"], "Neural KG")
        self.assertEqual(instance.examples(), [])

    def test_partial_file_inherits_the_rest(self):
        self.use("identity: {name: OnlyTheName}")
        self.assertEqual(instance.identity()["name"], "OnlyTheName")
        self.assertEqual(instance.identity()["tagline"], "OKF + ARD")
        self.assertTrue(instance.is_identifier("ein"))

    def test_shipped_instance_describes_this_corpus(self):
        instance.reload()
        names = [d["name"] for d in instance.domains()]
        self.assertIn("nonprofit", names)
        self.assertTrue(instance.is_identifier("fips_place"))
        self.assertGreaterEqual(len(instance.examples()), 1)


if __name__ == "__main__":
    unittest.main()
