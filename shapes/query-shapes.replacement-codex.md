# Codex replacement catalog — independent candidate

**76 concrete entries**, replacing the 98-entry baseline for purposes of comparison.
The shared catalog has not been changed. This candidate uses the existing top-level `shapes`
envelope, rather than the earlier reduction proposal's `patterns` envelope.

## Files

- `query-shapes.replacement-codex.yaml`: the replacement catalog itself.
- `query-shapes.replacement-codex.mapping.yaml`: all 98 original IDs, original descriptions/examples,
  and conditional routes to the replacement entries. This is an audit/migration aid, not legacy aliases.
- `query-shapes.replacement-codex.examples.json`: 76 authored question/input-contract fixtures,
  each with an expected pattern, output grain and operator sequence.
- `query-shapes.replacement-codex.validate.py`: offline structural checks.

Each card has a title, concrete description, example, portable `not` contrasts, parameter
contracts, required inputs/evidence, output grain and explicit operator/dependency graph.
There are 108 directed contrast links. All cards stand alone as concrete leaves; no abstract
parent patterns or legacy labels are retained.

## Central decisions

Shape identity is the parameterized execution template. Constants, field coordinates,
dates, sort direction, limit, output projection and declared bounded kernels are parameters.
Extra acquisitions, joins, grouping, sorting and model fitting stay visible.

For example, the stock-price/complaints question is `join.double-change`: four endpoint
relations, entity alignment, two change calculations and the combined predicate. A revenue
change plus a current employee threshold is `join.change-and-level`, with three input relations.
Independent date windows remain bindings of the four-input template.

Mean and sum share a bounded-state reduction; exact median/quantile and distinct count do
not. Top-N and a single winner share a limit parameter. Named ordinal neighbors require a
Locate stage, while nearest numeric values require a reference-distance calculation.
Absence claims carry explicit evidence requirements even where the membership graph merges.

Many old broad descriptions have multiple routes: calculating a denominator versus reading
a supplied total, ordinary grouped aggregation versus grouped distinct counting, and Pearson
versus rank correlation. The migration file does not pick one blindly.

## Interpretation and planning

Question understanding preserves requested operands and computations without inferring
publishers or API layouts. When layout matters, planning selects the applicable card using
explicit input contracts. The example fixtures therefore include those contracts; they are
not purportedly unambiguous question-only labels.

A supplied aggregate can satisfy an input if its grain, definition, scope and period match.
It does not erase the raw-input computation template globally. Unavailable capabilities make
a binding infeasible; they do not create a separate catalog entry for every source or date.

Do not use a generic expression to conceal an arbitrary subplan. The candidate retains the
bounded methods of the frozen reduction: exact quantiles, specified linear-model fitting,
Welch's two-sample test, finite relation paths and explicit allocation assumptions. Requests
outside those methods need clarification or additional concrete plans, not invented defaults.

## Verification and limits

Run from `shapes/` using the repository Python environment:

```sh
../.venv/bin/python query-shapes.replacement-codex.validate.py
```

The checks cover unique YAML keys and IDs, card completeness, valid contrast/migration targets,
declared parameters, acyclic dependency order, reachable outputs, fixture consistency, and
the three-versus-four-input boundary. All 98 old IDs are accounted for.

These fixtures are authored examples, not independent holdout data. No classification or
end-to-end accuracy is claimed. The DAGs describe execution requirements; implementations of
their operators are not supplied by this catalog-writing task.

The current evaluator still hard-codes old IDs in its prompts and omits `not` fields from its
payload. Its gold labels, serialization and prompts must be updated in a separate integration
step before benchmarking this candidate. Old regression percentages do not transfer.

This artifact is frozen independently of Claude's forthcoming replacement. Compare the actual
cards, old-ID routes, parameter boundaries and plans before choosing a shared catalog.
