# Codex independent pattern reduction

## Result

**98 original entries → 76 concrete templates: a net reduction of 22 (22.4%).**

The independent catalog is `query-shapes.codex.yaml`. The unchanged input is
`query-shapes.reduction-baseline.yaml`, SHA-256
`fb7de52ee9b2adcb3740b74754ec6e144ddaa2b9e089d450f97150897c4a11f3`.

All 98 original IDs are mapped. Their original descriptions and examples are preserved in
`baseline_mapping`. 66 originals have one route; 32 have multiple explicit routes because
their wording or examples cover different computations or supplied-versus-raw inputs.
This is a design proposal, not a replacement runtime catalog or a claim of a minimal catalog.
I have not read Claude's new independent reduction.

## Identity rule used

A pattern is a typed, parameterized operator/dependency graph. The YAML lists its nodes,
dependencies, legal parameter names, applicability conditions and original coverage.
These are design-level plans; they are not executable implementations.

I permit identifiers, dates, constants, direction, limit, projection and finite fan-out
cardinality as bindings. I also permit explicitly bounded kernels: pure scalar expressions,
fixed-state streaming reducers, equijoin emission mode, witness polarity, temporal-neighbor
direction, and a finite repeated relation path. These choices define the granularity of this
reduction and should be compared directly against Claude's choices.

An expression cannot contain a read, join, sort, grouping operation or model fit. A reducer
cannot hide a distinct set or quantile calculation. Neutral bindings such as lag zero and
slice radius zero are allowed. Arbitrary optional stages are not.

The capability profile governs feasibility and physical realization. A count-only API does
not establish enumerability; an API supporting DESC does not establish ASC. A precomputed
aggregate can discharge a calculation only with matching semantics and grain. That shortcut
does not erase the raw-input template everywhere else.

## Major reductions

| Original distinctions | Reduced template | Binding that preserves the request |
|---|---|---|
| Numeric/categorical/status/threshold scalar | `lookup.scalar` | Field, expression, output type |
| Ratio/share/difference/change/direction/agreement | `lookup.binary` | Two coordinates and a pure formula |
| Owned records/topic search/direct row filters | `records.select` | Owner/topic/Boolean predicate |
| Top/bottom/N=1 | `rank.population` | Direction, N, ties |
| Sum/count/mean/range/variance/paired weighted mean/conditional rate/HHI | `reduce.streaming` | Finite-state kernel and finish formula |
| Median/percentile/IQR | `reduce.quantiles` | Quantile positions and result formula |
| One/multiple named time series | `series.values` | Named fan-out cardinality |
| Rank of a member/ordinal neighbors | `rank.named-position` | Relative slice and projection |
| Raw level winner/derived growth winner | Separate level and derived templates | Derived winner explicitly reads two values per candidate |
| Both changes/either change/sign relationship/independent windows | `join.double-change` | Endpoint dates, change formulas, predicate, missing policy |
| Constrained winner/constrained top-N | `join.filter-rank` | N |
| Existence/record absence/authoritative set subtraction | `join.membership` | Match polarity and evidence-backed claim |
| Inner enrichment/left enrichment/intersection | `join.enrich` | Join mode, predicate, projection and multiplicity |
| Temporal as-of/nearest | `temporal.match` | Backward/forward/either and tolerance |
| Contemporaneous/lagged series correlation | `assoc.series` or `assoc.series-ranks` | Lag; Pearson and Spearman remain separate plans |

Two corrections to my earlier audit are worth making explicit. HHI does not need a separate
normalization pass: sum(x²)/sum(x)² uses a fixed-state reducer. Pearson correlation likewise
uses paired moments after alignment and therefore shares `join.paired-reduce` with separately
acquired weighted-mean operands under my declared kernel boundary. Spearman requires ranking,
which remains explicit. These are algebraic plan reductions, not answer-equivalence claims.

## Boundaries retained

| Boundary | Execution difference |
|---|---|
| Scalar lookup vs event-bound lookup | A returned date binds the second acquisition |
| Supplied ratio vs entity share with raw denominator | Population scan and reduction |
| Mean vs median | Streaming state vs exact order statistics |
| Count vs distinct count | Deduplication state |
| Grouped vs ungrouped aggregation | Group partitioning |
| Grouped distinct count vs ordinary grouped count | Deduplication on group and identity |
| Overlapping group allocation vs supplied group keys | Membership acquisition, join and allocation |
| Level filtering vs computed change filtering | Endpoint acquisitions and change computations |
| Row-local OR vs independent-set OR | One row predicate vs union/deduplication |
| One-sided difference vs symmetric difference | Both directed anti-joins and union |
| Joined raw records vs independently grouped measures | Each side reduces before alignment |
| Radius selection vs spatial nearest | Threshold filter vs ordering and selection |
| Ordinal neighbors vs closest values | Locate/slice vs reference subtraction and ordering |
| Read a historical value vs detect a revision | One vs two release acquisitions |
| Positive related-count predicate vs one accepting zero | Independent candidates and left join for zero-match members |

Each pattern remains a concrete leaf, not a generic “satisfy a condition” parent.
The catalog does not introduce generic arbitrary subquery/expression leaves.

## Routing and coverage caveats

The mapping is not an old-ID alias table. For example, `aggregate.by-group` maps to distinct
streaming, quantile, distinct-count and membership-bridge plans. Choosing among them requires
binding the requested statistic and the input grain. This avoids declaring a broad old card
one actionable template when it contains several algorithms.

Some old examples need clarification, not a confident label. “Is every 501(c)(3) required to
file a 990?” asks about a rule; observed records alone do not establish the obligation.
Estimating a fine-area rate from a coarse-area rate requires an explicit model. These issues
are preserved in `example_review_flags`, not silently removed.

More elaborate computations are not implicitly covered: unbounded path search, automatic
model selection, arbitrary statistical tests, quantile-derived histogram edges, fractional
weighted quantiles, or arbitrary compositions of derived operands need their own explicit
plans. The bounded methods in this proposal are documented in parameter contracts. Broad
old wording did not supply executable plans for those cases either.

## Validation and comparison

Structural checks passed: baseline hash, 98/98 mapped originals, unique new IDs, valid route
targets, topologically ordered DAG dependencies, reachable outputs, and 76 distinct syntactic
operator/dependency signatures. Run `query-shapes.codex.validate.py` to reproduce these checks
and the declared merge/boundary assertions.

Signature uniqueness is not proof that the operator vocabulary is irreducible. The next
comparison must inspect whether two differently named operators are genuinely different,
and whether either proposal allows too much computation inside one kernel.

No LLM classification or end-to-end benchmark was run for these proposed labels. The earlier
classification scores apply to the old catalog only. Preserving every original ID in a mapping
is coverage bookkeeping, not proof that every possible question has an executable plan.

Compare the independent outputs on: original-ID routing, added/removed computation nodes,
parameter grammar, acquisition contracts, output grain, completeness guards, and concrete
counterexamples. Freeze both versions before reconciling them. Do not prefer the smaller count
unless the removed distinctions are genuinely bindings of the same plan.
