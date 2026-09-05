"""One repository, many instances: an instance must be able to bring its own CODE, not just data.

instance.yaml says what a deployment answers over. Extensions say what it can DO that core does
not ship -- a guarded warehouse executor, an authentication scheme, a naming authority for a kind
of object. A downstream project forked this engine to add exactly those, and a fork made to hold
code then diverges on everything else too, which is the cost these tests exist to avoid.

Everything here must hold with NO extensions loaded, because that is the shipped deployment.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures"))
import extensions                                                     # noqa: E402
import instance                                                       # noqa: E402


class ExtensionRegistryTests(unittest.TestCase):
    def use(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        handle.write(text)
        handle.close()
        os.environ["INSTANCE_CONFIG"] = handle.name
        instance.reload()
        extensions.reset()
        self.addCleanup(lambda: (os.environ.pop("INSTANCE_CONFIG", None), instance.reload(),
                                 extensions.reset(), os.unlink(handle.name)))

    def test_core_loads_nothing_and_behaves_as_before(self):
        """The shipped instance declares no extensions, and must not require any."""
        self.assertEqual(instance.extensions(), [])
        self.assertEqual(extensions.registry().executors, {})
        self.assertIsNone(extensions.executor("anything"))
        self.assertEqual(extensions.split_candidates(["a", "b"], None), (["a", "b"], []))

    def test_an_instance_registers_its_own_executor(self):
        self.use("extensions: [ext_demo]\n")
        run = extensions.executor("demo_counter")
        self.assertIsNotNone(run)
        import asyncio
        from collections import namedtuple
        frame = namedtuple("F", "fm ident key period attribute mention state ctx")(
            {}, "sources/x/y.md", None, "latest", "size", "Kepler-22 b", {}, {})
        out = asyncio.run(run(frame, context=None))
        self.assertEqual(out["value"], len("Kepler-22 b"))
        self.assertEqual(out["ident"], "sources/x/y.md")

    def test_a_declared_executor_that_is_not_registered_refuses_by_name(self):
        """Silence here would be the worst outcome: a document declaring an executor the running
        deployment does not have would fall through to a built-in and answer from the wrong path.
        """
        self.use("extensions: []\n")
        self.assertIsNone(extensions.executor("bigquery_guarded"))

    def test_a_bad_extension_fails_loudly_at_load(self):
        """A module that registers nothing is a typo or a half-finished port. Ignoring it means
        running with core behaviour while appearing to be extended."""
        self.use("extensions: [json]\n")            # importable, but has no setup()
        with self.assertRaises(TypeError) as raised:
            extensions.registry()
        self.assertIn("setup(registry)", str(raised.exception))

    def test_candidate_filters_run_before_planning_and_report_what_they_withheld(self):
        """Filtering before the planner is the strong form: a source the planner never sees
        cannot be routed to. Reporting the withheld set is what lets a caller REFUSE rather than
        quietly answer from the next-best source -- substituting a different thing for the one
        that was asked about."""
        self.use("extensions: [ext_demo]\n")
        candidates = [{"id": "public-1"},
                      {"id": "private-1", "private": True, "needs": "finance.internal"}]
        visible, withheld = extensions.split_candidates(candidates, {"entitlements": []})
        self.assertEqual([c["id"] for c in visible], ["public-1"])
        self.assertEqual([c["id"] for c in withheld], ["private-1"])

        entitled = {"entitlements": ["finance.internal"]}
        visible, withheld = extensions.split_candidates(candidates, entitled)
        self.assertEqual(len(visible), 2)
        self.assertEqual(withheld, [])

    def test_only_one_principal_provider(self):
        """Two would mean the answer to "who is asking" depends on import order."""
        registry = extensions.Registry()
        registry.principal(lambda request: None)
        with self.assertRaises(ValueError):
            registry.principal(lambda request: None)

    def test_executor_names_do_not_collide_silently(self):
        registry = extensions.Registry()
        registry.executor("dup")(lambda f, *, context: None)
        with self.assertRaises(ValueError):
            registry.executor("dup")(lambda f, *, context: None)


if __name__ == "__main__":
    unittest.main()
