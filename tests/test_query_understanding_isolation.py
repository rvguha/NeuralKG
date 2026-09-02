"""Architecture contract: query understanding must know nothing about ARD's contents.

The harness must be pointable at a DIFFERENT ARD without a code change. That fails the moment
this stage names a source, enumerates the local catalog, or reasons about how a particular
publisher exposes data — because the same natural-language question would then get a different
"correct" answer depending on which corpus happens to be mounted.

This has regressed twice and both times the measurement did not catch it, which is why it is a
contract rather than a metric:

  * `sources` was returned by the classifier and applied as a HARD pre-filter before scoring, so
    26 of 193 questions had the answering source removed from the index before discovery ran.
  * A shape rule read "use point only when the source publishes the value as one reported figure",
    which is a fact about a publisher. It also broke timeseries, because its example taught the
    model that "X total revenue" is a point.

Deterministic: no LLM call, no network, no index. It reads the prompts and the returned fields.
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import harness                                                        # noqa: E402


def _source_dirs():
    root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sources")
    return sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))


class QueryUnderstandingIsolationTests(unittest.TestCase):
    def prompts(self):
        return {"structure": harness._structure_understanding_system(),
                "measure": harness._measure_understanding_system({"entity": "", "entities": []})}

    def test_no_prompt_names_a_source_directory(self):
        """The strongest form of the contract: not one of this ARD's directory names appears."""
        dirs = _source_dirs()
        self.assertGreater(len(dirs), 5, "expected a populated sources/ to test against")
        for stage, prompt in self.prompts().items():
            for directory in dirs:
                with self.subTest(stage=stage, source=directory):
                    self.assertNotIn(directory, prompt)

    def test_no_prompt_enumerates_the_local_catalog(self):
        """`SOURCE_TYPES` is built by globbing the filesystem. Injecting it made routing depend on
        a local directory listing while discovery went to a remote ARD, and glob order differs
        between filesystems -- the same question routed differently on macOS and on the VM."""
        for stage, prompt in self.prompts().items():
            with self.subTest(stage=stage):
                self.assertNotIn("covers ", prompt)          # the "- <dir>: covers <type>" listing
                for entity_type in list(harness.SOURCE_TYPES.values())[:12]:
                    if len(entity_type) > 40:                # distinctive enough to be evidence
                        self.assertNotIn(entity_type[:40], prompt)

    def test_understanding_returns_no_source_selection(self):
        """Even as a hint. A field named `sources` invites a caller to filter on it, and the
        filter is applied before scoring, so a wrong pick is unrecoverable."""
        ctx = harness._normalize_shape(
            {"shape": "point", "entity": "Apple", "entities": [], "attribute": "total revenue",
             "period": "latest", "periods": [], "question": "What was Apple's total revenue?"})
        self.assertNotIn("sources", ctx)
        self.assertNotIn("sites", ctx)

    def test_shape_rules_do_not_reason_about_publisher_access(self):
        """A shape must be decidable from the question alone. Whether a publisher exposes a value
        directly or only as records to sum is a capability fact resolved after discovery."""
        structure = self.prompts()["structure"]
        forbidden = ("publishes the value", "one line in a filing", "the publisher exposes",
                     "individual awards and the total is their sum")
        for phrase in forbidden:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, structure)
        # and it must say so positively, not merely omit the coupling
        self.assertIn("planner", structure)


if __name__ == "__main__":
    unittest.main()
