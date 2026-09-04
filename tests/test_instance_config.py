"""One engine, many instances: the config file must actually change behaviour.

The claim this file defends is that standing up Neural KG over a different ARD -- a different
world, with different kinds of thing in it -- is a configuration change, not a code change. A
config that only *described* the deployment while the code kept its own hard-coded vocabulary
would pass a shallower test and be worthless. So these assert on the query path: given an
instance file describing the NASA Exoplanet Archive, the engine must classify `pl_name` as an
identifier and must stop treating `cik` as one.

The archive is used rather than an invented corpus because it is real, open, and structurally
the same problem as the sources here -- named objects with numeric attributes, answering in the
same shapes: a point read (Kepler-22 b has radius 2.1 R-earth), a ranking (largest radius), a
timeseries (discoveries per year). `pl_name = 'Kepler-22 b'` names a planet and a row returned
for Kepler-22 c is rejected at validation; `like '%Kepler%'` is a guess that must be resolved
first. That is exactly what `domains` encodes.

Case-insensitivity is asserted separately below. An all-lowercase invented example agreed with
the code by construction and hid a real bug; real APIs spell parameters however they like.
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
identity: {name: Exoplanet KG}
ard: {finder_url: "http://finder.internal:9000"}
domains:
  - name: planet
    identifiers: [pl_name]
  - name: star
    identifiers: [hostname]
  - name: photometry
    identifiers: ["sy_*"]
name_selectors: [q]
""")
        self.assertTrue(instance.is_identifier("pl_name"))
        self.assertTrue(instance.is_identifier("hostname"))
        self.assertTrue(instance.is_identifier("sy_dist"))          # trailing * is a prefix
        self.assertFalse(instance.is_identifier("q"))               # a search, not an identity
        self.assertFalse(instance.is_identifier("cik"))             # this ARD has no companies
        self.assertEqual(instance.name_selectors(), ("q",))
        self.assertEqual(instance.identity()["name"], "Exoplanet KG")
        self.assertEqual(instance.finder_url(), "http://finder.internal:9000")

    def test_identifier_matching_is_case_insensitive(self):
        """A real bug the all-lowercase example could not have caught.

        `is_identifier` folded the parameter but not the configured entry, so it worked only
        because every identifier in the original vocabulary was lowercase (cik, ein, fips_place).
        `derive._names()` lowercases every parameter it extracts before classifying it, so a
        camelCase identity parameter was silently taken for neither an identity nor a name."""
        # awardeeName is real and already in this corpus -- derive.py's own URL regex cites
        # `?awardeeName=` from USASpending -- so the camelCase case is not hypothetical.
        self.use("domains:\n  - {name: recipient, identifiers: [awardeeId]}\n"
                 "name_selectors: [awardeeName]\n")
        for spelling in ("awardeeId", "awardeeid", "AWARDEEID"):
            self.assertTrue(instance.is_identifier(spelling), spelling)
        import importlib
        import derive
        importlib.reload(derive)
        self.addCleanup(lambda: (instance.reload(), importlib.reload(derive)))
        self.assertIn("awardeename", derive._KEY_NAME)   # folded to match _names() output

    def test_derive_classifies_parameters_through_the_instance(self):
        """derive.py is the consumer; assert through it, not just through the accessor."""
        self.use("domains:\n  - {name: planet, identifiers: [pl_name]}\n")
        import derive
        self.assertTrue(instance.is_identifier("pl_name"))
        self.assertFalse(instance.is_identifier("ein"))
        # derive reads the vocabulary rather than carrying its own copy
        self.assertNotIn("_KEY_ID", vars(derive))

    def test_a_declared_instance_does_not_inherit_another_corpus_questions(self):
        """The trap this caught: `examples() or <legacy literal>`.

        An instance over the NASA Exoplanet Archive correctly declares no example questions of
        its own, and inherited a homepage of US nonprofit questions -- "What was the American
        Red Cross total revenue?" offered against a catalogue of planets. A file that exists governs,
        including where it is silent.
        """
        self.use("identity: {name: Exoplanet KG}\ndomains:\n  - {name: planet, identifiers: [pl_name]}\n")
        import importlib
        import harness
        importlib.reload(harness)
        self.addCleanup(lambda: (instance.reload(), importlib.reload(harness)))
        self.assertEqual(harness.EXAMPLE_TABS, [])
        self.assertEqual(harness._SOURCE_ORDER, [])
        self.assertIn("<h1>Exoplanet KG</h1>", harness.PAGE)
        self.assertNotIn("American Red Cross", harness.PAGE)

    def test_crosswalk_service_is_declared_not_assumed(self):
        """Entity resolution is corpus-shaped, and Wikidata is one shape of it.

        The engine assumed a hub identifier that CARRIES per-source keys (QID -> CIK, EIN, FIPS),
        which is true of US organizations and places because Wikidata curates exactly those. It is
        false for a corpus whose own designations are the identifiers: the NASA Exoplanet Archive
        keys on "Kepler-22 b", so there is nothing to resolve and the two Wikidata requests buy a
        QID nothing will use."""
        self.assertEqual(instance.crosswalk(), "wikidata")          # the shipped default
        self.use("ard: {crosswalk: none}\n")
        self.assertEqual(instance.crosswalk(), "none")
        self.use("ard: {crosswalk: WIKIDATA}\n")
        self.assertEqual(instance.crosswalk(), "wikidata")          # folded and trimmed

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
