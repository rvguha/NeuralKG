# Catalog repair

The catalog restores ten ordinary result forms removed with the legacy labels: point value,
boolean status, records for an entity, single-entity series, top/bottom-N ranking, population
sum, side-by-side comparison values, ratio, matching records, and population correlation.
Their descriptions were recovered from the original catalog and harness, then narrowed where
the old buckets mixed operations (notably ratio versus difference and sum versus any statistic).
No compatibility labels or generic parent hierarchy have been restored.

## Consolidations

| Removed entry | Canonical representation | Reason |
|---|---|---|
| set.union | filter.disjunction-any | Set membership is a predicate; return distinct entities satisfying either. |
| relation.set-intersection | filter.conjunction-all | Same membership intersection, independent of source layout. |
| topical.count | aggregate.count | Topic only restricts the counted population. |
| topical.ranked | rank.top-n | Topic only restricts the ranked population. |
| rank.bottom-n | rank.top-n, direction=min | Direction changes the comparator, not the query structure. |
| multi.co-movement and multi.divergence | multi.direction-relation, polarity=same/opposite | Same two changes and sign comparison, with explicit polarity. |
| source.coverage-gap and join.anti | filter.negation-open | Same record-absence request with typed relation/source operands. |
| point.normalized | point.ratio | Explicit per-unit normalization is a quotient. |
| source.discrepancy-magnitude | comparison.difference | Publisher assertions are the operands of subtraction. |
| relation.geographic-rollup | aggregate.sum | Geography defines the population whose contributions are summed. |
| join.many-to-many-bridge | aggregate.by-group | Bridge layout belongs to planning; contribution grain and allocation remain explicit slots. |
| time.calendar-alignment | comparison operand alignment | Period reconciliation modifies a specified comparison. |
| comparison.normalized | comparison with measure-expression operands | Preserve the requested expression and result: values, winner, or difference. |

There are 98 entries after restoring ten and removing fourteen redundant entries.

## Distinctions preserved

Explicit two-measure change computations and independent windows remain separate from level
predicates. The earlier suggestion to collapse everything into generic AND/OR would hide
required computations. Independent grouped measures retain their per-measure contribution
grain; the entry no longer prescribes physical aggregation-before-join. Inner enrichment
requires projecting measures; common membership alone is conjunction. Outer enrichment retains
unmatched entities with missing values rather than asserting zero. Known absence, observed
record absence, and authoritative set non-membership remain explicit, with ambiguity permitted
when the question does not distinguish them. Symmetric difference retains XOR semantics.

## Tests and provenance

`tests/shape_catalog_eval.py` evaluates this authoring catalog independently of the engine.
It makes one classification call per question and supplies the entire catalog. It requests
result, candidate scope and computation before the label. It preserves service errors as
failures and writes partial results, model identity and catalog/prompt hashes.

The common cohort is the original 308 questions, with leaf labels adjudicated before the first
catalog run. Sixteen questions have documented accepted sets for unresolved wording. The
remaining cohort retains the generated questions, relabels actual mergers, adds common and
discriminating regressions, and corrects documented bad gold labels. Corrections are recorded
in `corpus-label-review.json`; original labels and question text are retained in that audit.
Generated development data is not an independent holdout. No end-to-end answer accuracy is
implied by a catalog-classification score.

The old model-voted facets were invalidated because their labels were both stale and sometimes
inconsistent with the definitions. They are absent until separately regenerated and validated;
the full-catalog rig does not use them. The production harness is unchanged.

Targets: at least 99% on the common cohort and 95% on the remaining cohort, including errors.
Run `python -m unittest discover -s tests -p test_shape_catalog.py` for offline invariants.
Run `python tests/shape_catalog_eval.py --cohort all --output /tmp/shape-catalog-results.json`
with the configured credentials for the LLM evaluation. Published outcomes belong in the
companion results note with the actual hashes and model; the target is not a claimed result.
