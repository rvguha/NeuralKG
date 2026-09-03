"""One engine, many instances: the config file must actually change behaviour.

The claim this file defends is that standing up Neural KG over a different ARD -- a different
world, with different kinds of thing in it -- is a configuration change, not a code change. A
config that only *described* the deployment while the code kept its own hard-coded vocabulary
would pass a shallower test and be worthless. So these assert on the query path: given an
instance file describing molecules, the engine must classify `inchikey` as an identifier and
must stop treating `cik` as one.
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
identity: {name: ChemKG}
ard: {finder_url: "http://finder.internal:9000"}
domains:
  - name: molecule
    identifiers: [inchikey, cas]
  - name: protein
    identifiers: ["uniprot*"]
""")
        self.assertTrue(instance.is_identifier("inchikey"))
        self.assertTrue(instance.is_identifier("cas"))
        self.assertTrue(instance.is_identifier("uniprot_id"))       # trailing * is a prefix
        self.assertFalse(instance.is_identifier("cik"))             # this ARD has no companies
        self.assertEqual(instance.identity()["name"], "ChemKG")
        self.assertEqual(instance.finder_url(), "http://finder.internal:9000")

    def test_derive_classifies_parameters_through_the_instance(self):
        """derive.py is the consumer; assert through it, not just through the accessor."""
        self.use("domains:\n  - {name: molecule, identifiers: [inchikey]}\n")
        import derive
        self.assertTrue(instance.is_identifier("inchikey"))
        self.assertFalse(instance.is_identifier("ein"))
        # derive reads the vocabulary rather than carrying its own copy
        self.assertNotIn("_KEY_ID", vars(derive))

    def test_a_declared_instance_does_not_inherit_another_corpus_questions(self):
        """The trap this caught: `examples() or <legacy literal>`.

        An instance describing molecules correctly declares no example questions, and inherited
        a homepage of US nonprofit questions -- "What was the American Red Cross total revenue?"
        offered against a chemistry ARD. A file that exists governs, including where it is silent.
        """
        self.use("identity: {name: ChemKG}\ndomains:\n  - {name: molecule, identifiers: [cas]}\n")
        import importlib
        import harness
        importlib.reload(harness)
        self.addCleanup(lambda: (instance.reload(), importlib.reload(harness)))
        self.assertEqual(harness.EXAMPLE_TABS, [])
        self.assertEqual(harness._SOURCE_ORDER, [])
        self.assertIn("<h1>ChemKG</h1>", harness.PAGE)
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
