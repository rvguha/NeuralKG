# Writing an accessor plugin

An accessor is how an instance teaches the engine to fetch from a source the engine knows
nothing about — a guarded warehouse runner, a multi-step composite, an API client. You write
one Python module and two lines of configuration. **You do not edit the harness.**

## Why there is one registration and not two

`executor` reached only the scalar fetch path; `template_reader` reached only the template/DAG
path. Production runs the template path, so an instance that registered an `executor` — the
hook the docs pointed at — got silence rather than an error. `accessor` is reached from both
dispatchers and is the seam to use. The other two still work and are documented as legacy.

## The whole of a plugin

```python
import answer_synthesizer as synth

def setup(registry):
    @registry.accessor("bigquery_guarded")
    async def run(read, *, context):
        rows = await my_client.query(read.parameters, context=context)
        return synth.Input(
            data={"rows": rows},          # the COMPLETE payload, never a scalar projection
            complete=True,
            provenance={"source": read.source, "operation": read.operation},
            grain="entity",
            units={"value": "USD"},
            period_basis="fiscal-year")
```

`read` is an `extensions.Read`:

| field | scalar path | DAG path |
|---|---|---|
| `descriptor` | the OKF frontmatter as delivered | same |
| `source` | the resource identifier — **treat as opaque** | same |
| `operation` | `None` | the DAG operator |
| `parameters` | the query context | the node's bound parameters |
| `dependencies` | `()` | resolved upstream results |
| `node` | `None` | the DAG node |
| `frame` | the scalar `_F` | `None` |

A plugin that reads only the first four works on both paths. Reading `frame` or `node` makes it
path-specific — that is allowed, but check rather than crash.

## Rules the engine enforces

- **Return `answer_synthesizer.Input`.** Anything else is refused, naming the type you returned.
- **Return the complete payload.** The scalar path narrows it downstream and the DAG path does
  not; a plugin cannot know which ran, so narrowing inside the plugin loses evidence.
- **`raise runtime.Refused`** to reject this source and let the engine backtrack to the next
  candidate. Any other exception propagates as a real error.
- **A descriptor may SELECT an installed name, never supply one.** Naming an accessor no loaded
  extension registers is refused, and the refusal names the loaded set. A source naming an
  uninstalled accessor is not advertised to the planner at all.
- **Use `context`.** Cancellation, deadlines and the usage ledger travel on it. An accessor that
  makes its own model or data calls outside the shared services is invisible to accounting.
- Registering the same name twice is an error, not last-wins.

## Installing one

Descriptor — the only new field is `accessor:`:

```yaml
---
type: Loan Book Segment
title: Default rate by segment
accessor: bigquery_guarded
representativeQueries:
  - What is the default rate by income band?
access:
  operations:
    query:
      method: BIGQUERY
      capability: {grain: segment, paths: [key, filter, aggregate]}
---
```

Instance config:

```yaml
extensions:
  - my_instance.accessors
```

Worked example: `tests/fixtures/accessor_demo/` (the plugin) and `tests/test_accessor_plugin.py`
(both paths reaching one registration). Trusted in-process plugins are **not** a sandbox — an
installed plugin runs with the engine's privileges. Installation is the trust boundary.
