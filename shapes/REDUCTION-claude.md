# Independent pattern reduction — Claude

Produced without reading Codex's per-entry verdicts, so the two can be compared.

Catalog: `shapes/query-shapes.yaml` at 98 entries (commit `3e4493f`).


## Criterion

Guha's: a shape tells us how the query may be executed. So two entries are one shape when
they share a **plan skeleton** AND no capability profile gives them different verdicts.

    skeleton = acquisition graph  >>  computation kernel

Bindings (never shape-making): entity, measure, period, constants, sort direction, limit,
predicate values, and any local scalar formula over already-acquired values.

Shape-making (always): a different acquisition axis (varying period vs varying entity vs
enumerating a population), an extra read, a join, a grouping, a kernel that needs the whole
distribution rather than a streaming pass, and any difference in the FEASIBILITY VERDICT
against some capability profile.

The last clause is what mechanical skeleton grouping misses, and it changed six verdicts
below. `quantifier.universal` and `quantifier.existential` have an identical graph and must
still stay apart: on a partial scan the existential can answer YES and the universal cannot
answer at all.

## Result

    98 entries  ->  62 distinct skeletons
    23 entries eliminated by merging  ->  proposed catalog of 75
    20 skeletons shared by more than one entry; 12 merge fully or partly, 7 keep, 1 conditional


## Verdicts on shared skeletons

| skeleton | entries | verdict | reason |
|---|---|---|---|
| `ENUM1 >> DIST` | rank.percentile-of-entity, aggregate.median, aggregate.percentile-value, aggregate.spread, aggregate.histogram | **MERGE-4** | median/percentile/spread/percentile-of-entity all need the full distribution and are INFEASIBLE wherever a prefix scan is all that is available. histogram SPLIT OUT: grouped projection |
| `ENUM1 >> FILTER` | filter.threshold, filter.range, filter.negation-closed, filter.categorical, filter.temporal-active | **MERGE-4** | threshold/range/categorical/temporal-active are predicate bindings. negation-closed SPLIT OUT: it requires the source to RECORD absence, a capability the others do not need |
| `ENUM2-P2 >> ARITH+FILTER` | multi.change-conjunction-all, multi.change-disjunction-any, multi.change-plus-level, multi.direction-relation, multi.independent-windows | **MERGE-4** | conjunction/disjunction/plus-level/direction-relation are predicate-combination bindings over the same two-measure two-period acquisition. independent-windows SPLIT OUT: separately bound windows require per-operand period addressing |
| `ENUM1 >> SORT` | rank.top-n, rank.of-named-entity, rank.extremum-single, rank.neighbors | **MERGE-3** | top-n, extremum-single (n=1) and neighbors (window around a key) are limit/offset bindings on one ordered scan. rank.of-named-entity SPLIT OUT: returns an ordinal, which requires counting the whole population rather than reading a prefix |
| `KEYED-P2 >> ARITH` | change.absolute, change.percent, change.direction, change.period-over-period | **MERGE-3** | absolute/percent/direction differ only by local formula. period-over-period SPLIT OUT: it needs calendar-aligned period addressing, which a source with only latest-value cannot serve |
| `KEYED1 >> NONE` | point.value, point.status, point.categorical | **MERGE** | measure type (number/boolean/category) is a binding; identical keyed read, identical verdict everywhere |
| `ENUM1 >> REDUCE` | aggregate.sum, aggregate.count, aggregate.mean | **MERGE** | sum/count/mean are one streaming reducer; all three are EXACT on a server-aggregate source and COMPOSE on a scan |
| `KEYED-E2 >> ARITH` | comparison.difference, comparison.which, source.agreement | **MERGE-2** | difference/which differ by projection over the same two reads. source.agreement SPLIT OUT: it requires the SAME measure from two publishers, a cross-publisher identity requirement the others do not have |
| `KEYED-E2 >> NONE` | comparison.values, relation.existence | **KEEP** | relation.existence asks whether a RELATION holds, which needs a relation read, not two measure reads. My skeleton was wrong. |
| `KEYED-M2 >> ARITH` | point.ratio, point.component-share | **MERGE** | ratio and component-share differ by which denominator is bound |
| `ENUM2 >> FIT` | assoc.correlation-population, assoc.lagged | **KEEP** | correlation-population and lagged differ in the period binding of the second operand, which changes the acquisition |
| `SERIES >> ARITH` | change.rate-annualized, trend.acceleration | **KEEP** | rate-annualized is a closed-form over endpoints; acceleration needs the second difference across every period |
| `SERIES >> REDUCE` | time.duration-above, time.cumulative-to-date | **MERGE** | cumulative-to-date and duration-above are sum and count over the same series scan |
| `VERSION >> NONE` | time.latest-vintage, source.vintage | **KEEP** | latest-vintage reads the current release and reports its period; source.vintage reads a NAMED PAST release, which not every publisher retains |
| `ENUM2 >> FILTER` | filter.conjunction-all, filter.disjunction-any | **CONDITIONAL** | conjunction/disjunction are one row-local filter ONLY when both predicates are evaluable on the same enumeration. When an operand is membership of a separately enumerable set the graph is a join, not a filter. Codex is right that my earlier unconditional merge was too broad |
| `ENUM1 >> FILTER+REDUCE` | quantifier.universal, quantifier.existential | **KEEP** | universal and existential have DIFFERENT VERDICTS on partial data: existential can answer YES from a prefix, universal cannot answer at all. Same graph, different feasibility |
| `ENUM2 >> FILTER+SORT` | multi.threshold-then-rank, multi.constrained-optimum | **MERGE** | threshold-then-rank and constrained-optimum differ by limit n; n=1 is a binding |
| `TRAVERSE >> REDUCE` | relation.hierarchy-rollup, relation.hierarchy-exclusive | **KEEP** | rollup enumerates subordinates; exclusive must identify and exclude them, which needs the hierarchy to be explicit rather than merely traversable |
| `DESCRIPTOR >> NONE` | meta.availability, meta.definition | **MERGE** | availability and definition are two projections of one descriptor read |
| `ENUM-SET+ENUM-SET >> JOIN(anti)` | set.difference, set.symmetric-difference | **MERGE** | symmetric-difference is set.difference with direction=both |

## Singleton skeletons (no merge candidate)

`aggregate.by-group`, `aggregate.concentration`, `aggregate.count-distinct`, `aggregate.share-by-group`, `aggregate.weighted-mean`, `assoc.conditional-rate`, `assoc.correlation-over-time`, `assoc.group-difference`, `assoc.outlier`, `comparison.growth`, `filter.count-predicate`, `filter.existence`, `filter.negation-open`, `join.aggregate-each-side`, `join.division`, `join.inner-enrichment`, `join.multi-hop`, `join.outer-enrichment`, `multi.ratio-threshold`, `point.breakdown`, `point.record`, `point.threshold-check`, `rank.change`, `rank.within-group`, `records.for-entity`, `records.matching`, `relation.entity-to-population-share`, `relation.geographic-disaggregation`, `spatial.containment`, `spatial.nearest`, `spatial.proximity`, `temporal.as-of`, `temporal.event-sequence`, `temporal.interval-overlap`, `temporal.nearest`, `time.extremum`, `time.first-crossing`, `time.level-at-event`, `time.volatility`, `timeseries.multi-entity`, `timeseries.single-entity`, `topical.eligibility`


## What I could not settle

- `ENUM2 >> FILTER` (conjunction / disjunction) is CONDITIONAL. Row-local AND/OR over one
  enumeration and combining two separately enumerable sets are different graphs. Which one a
  question means is not stated by the question, so this may be a case for accepted label sets
  rather than a catalog decision.
- I assigned `relation.existence` the wrong skeleton (two measure reads); it needs a relation
  read. Recorded rather than silently fixed, because it shows the skeletons are authored
  judgements, not derived facts.
- Entries whose kernel is unspecified — `aggregate.spread` (range? sd? IQR?),
  `aggregate.concentration` (top-N share? HHI?), `assoc.outlier` (no residual model),
  `time.volatility` — got a skeleton anyway. They need splitting before a skeleton means
  anything, and my table understates the catalog by hiding that.

## Comparison hooks for Codex's audit

Their coverage was 20 merge / 47 keep / 13 conditional / 18 split over the same 98. Mine is
23 merges / 7 keeps / 1 conditional over the 20 shared skeletons, and I produced NO split
verdicts because I audited for duplication, not for underspecification. That asymmetry is
the first thing to reconcile: their 18 splits are a defect class my method cannot see.
