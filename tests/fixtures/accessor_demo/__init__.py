"""A minimal accessor plugin. This is the whole of what an instance writes.

One registration, reached from both dispatch paths -- the scalar fetch and the template/DAG
reader. Nothing here knows which one invoked it, and nothing here is edited in the harness.
"""
import answer_synthesizer as synth
import runtime

CALLS = []


def setup(registry):
    @registry.accessor("demo_rows")
    async def demo_rows(read, *, context):
        """Return a complete payload, whichever path asked for it."""
        CALLS.append({"source": read.source, "operation": read.operation,
                      "parameters": dict(read.parameters), "path": "dag" if read.node else "scalar"})
        if read.parameters.get("fail") == "refuse":
            raise runtime.Refused("demo_rows was asked to refuse")
        rows = [{"entity": "A", "value": 1, "unit": "USD"},
                {"entity": "B", "value": 2, "unit": "USD"}]
        return synth.Input(
            data={"rows": rows, "value": rows[0]["value"], "unit": "USD", "period": "2023"},
            complete=True,
            provenance={"source": read.source, "operation": read.operation, "accessor": "demo_rows"},
            grain="entity",
            units={"value": "USD"},
            period_basis="fiscal-year")
