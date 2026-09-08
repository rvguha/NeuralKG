# Catalog execution

`answer_synthesizer.py` executes the exact nodes/dependencies/output in the active
replacement catalog. All 76 graphs have handlers and a fixed-input expected-output
case in `tests/template_operator_cases.py`. This is computational coverage, **not**
76 proven live source integrations or a full natural-language evaluation.

`template_execution.py` retains the seven live scalar-input implementations.
When none of those is applicable, `template_dag_execution.py` binds a candidate's
fixed DAG and calls the shared interpreter. It cannot add operators or generate
Python/SQL. `ARGUMENTS` is the parameter contract sent to planning, only for the
operators of the candidate templates. Failed bindings/reads are retained and
receive specific repair feedback; at most three attempts, within query budgets.

## Two independent entry points, one interpreter

```python
from answer_synthesizer import Input, synthesize

result = synthesize('lookup.binary', {
    'a': Input(30, True, {'source': 'fixture-a'}, 'scalar'),
    'b': Input(10, True, {'source': 'fixture-b'}, 'scalar'),
}, {'c': {'expression': {
    'op': 'subtract', 'args': [{'input': 0}, {'input': 1}]
}}})
assert result['result'] == 20
```

`await execute(template, parameters, reader, context=context)` runs those same
operators, acquiring each read after its dependencies complete. Reader signature:

```python
async def reader(node, parameters, dependencies, *, context) -> Input: ...
```

`Input` is a trusted connector result, not a planner response. It carries `data`,
`complete`, `provenance`, `grain`, `key_domains`, `units`, and `period_basis`.
Bare data, incomplete scope, missing provenance, or missing grain is refused.
This first implementation conservatively requires complete reads even for
positive existential witnesses; relaxing that requires proof-sensitive propagation.

### Shapes of read data

- Scalar/date: one value. Versioned scalar: `{value, vintage}`.
- Rows, series, membership, hierarchy, seeds, mapped scalar/pairs/rules: relations
  (lists of objects). Pair rows explicitly carry both operands; no hidden arithmetic.
- Mapped series/rows: lists of relations.
- Descriptor/record: an object. Point/boundary: GeoJSON geometry.
- Spatial allocation input: `{origin: GeoJSON, targets: relation}`.
- Traversal adapter supplies one edge relation per finite path step; the interpreter
  actually traverses these edges, including cycle handling. It never accepts final
  endpoints as a substitute for edges.

## Instance/source adapters

An instance extension can register:

```python
def setup(registry):
    @registry.template_reader('my_rows')
    async def read(node, parameters, dependencies, *, source, context):
        # Fetch/paginate, bind dependency scopes, validate response and identity.
        # Return Input(...) only after verifying completeness of the requested scope.
        ...
```

The descriptor selects it with `template_reader: my_rows`. The model cannot
register a handler or select a handler not declared by the source. Registered
readers must enforce authorization and faithful scope/measure/period/grain.

For ordinary HTTP responses an operation's existing `capability` can contain a
descriptor-owned `synthesis` contract:

```yaml
synthesis:
  read_only: true
  operators: [ReadRows]
  parameters: [year]
  data_path: rows
  total_path: total
  key_domains: {entity: wikidata}
  units: {value: USD}
  period_basis: calendar-year
```

Only declared GET/POST operations and parameter names are allowed. Completeness
requires the actual response's `complete_path == true`, or `len(rows) == total_path`.
Alternatively an explicitly unpaginated operation AND `population.complete: true`
can attest it. A short first page is not proof. Dependent/paginated/non-HTTP reads
without a suitable registered reader refuse rather than quietly truncate data.

The existing verified scalar adapter supports explicit annual periods and latest;
the descriptor adapter reads actual source metadata. Other source/API input
variants still need appropriately implemented/declared reader contracts. Merely
having a computational handler never implies live source availability.

## Calculation policies

- Expressions are finite JSON trees, never eval. Fields and input references are
  explicit; arithmetic rejects nulls, non-finite numbers, undefined powers and zero
  denominators. Reduce operations require explicit null handling.
- Joins preserve multiplicity. Cardinality is checked against actual keys; ordinary
  joins require matching key domains. Crosswalks must come from an adapter, not names.
- Ordering, rank conventions, tie handling and unmatched-row policy are bindings.
- Time differences require declared numeric spacing; gaps and duplicate times fail.
- Allocations require nonnegative normalized weights. Spatial area weights require
  uniform-density permission and a complete non-overlapping partition.
- Spatial calculations require matching explicit CRS; geodesic distance uses WGS84
  points. Callers must select a scientifically suitable projected CRS for area or
  planar distance. Geometry transformation belongs in the adapter.
- Welch tests and OLS are explicit methods, not inferred synonyms for comparison.
  Rule evaluation preserves true/false/unknown; absence of an actor attribute is
  not evidence of ineligibility.
- Unit metadata is carried through the generic DAG, but it is **not a complete
  dimensional type checker**. The scalar execution path has stricter expression
  unit checks. Generic adapter/planner admission must enforce compatible measures.

## Reproduce and inspect

```sh
ARD_STORE=json PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/python -m unittest test_answer_synthesizer test_template_dag_execution
ARD_STORE=json PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=tests:. .venv/bin/python tests/run_template_operator_tests.py
```

The existing home/chat UI uses production `/ask`; `/flow` redirects to it rather
than presenting a separate design. New stages appear in its existing activity log
and answer details. Debug runs save exact
understanding, descriptors, planner attempts, events, result/error and accounting
under the instance-local test-results directory. Synthetic reports are explicitly
labelled and do not make live answer-quality claims.
