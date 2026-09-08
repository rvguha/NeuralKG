# Query-shape evaluation — 2026-09-08

The catalog has 98 entries: ten common result forms restored and fourteen redundant entries
consolidated. No production harness integration was changed.

## Measured result (before the final expected-label correction)

| Cohort | Correct | Accuracy | Target |
|---|---:|---:|---:|
| Original common questions | 308 / 308 | 100.0% | 99% |
| Other catalog questions | 320 / 330 | 97.0% | 95% |
| Additional restored-common regressions | 20 / 20 | 100.0% | 99% |

There were zero service errors. All 658 cases were scored, including abstentions. Accepted
label sets represent documented ambiguity; the common labels were fixed before evaluation.
This is a development-rig result. The catalog-derived corpus and prompt iteration do not
establish independent holdout accuracy or end-to-end query-answer accuracy.

## Configuration and reproduction

The initial full-catalog proposal used `openai/gpt-oss-120b`. A separate full-catalog structural
review used `openai/gpt-5.5`, medium reasoning effort, eight workers. Review receives the
question, candidate definitions, and initial proposal/reason; expected labels are never sent.
The fixed initial proposals are in `tests/fixtures/shape_eval_initial.json`.

```sh
source ./set_keys.sh
RERANK_MODEL=openai/gpt-5.5 .venv/bin/python tests/shape_catalog_eval.py --cohort all --workers 8 --review-from tests/fixtures/shape_eval_initial.json --output /tmp/query-shapes-review.json
```

For an uncached two-call run use `--review` instead of `--review-from`; both calls then use
the configured model. That different configuration has not been measured here.

`tests/results/shape_catalog_raw.json` preserves raw outcomes, expected sets, initial picks,
reasons, catalog hash and prompt hash. The final editorial cleanup changes only examples and
ranking slot documentation, which the measured review pass did not read. The exact sequence
of id/asks strings used by review was asserted unchanged. One mechanically mistranslated
expected label (the Stanford-versus-MIT normalized winner) is corrected after the run with a
recorded reason. The table above conservatively retains that mismatch. Rescoring against the
corrected label gives 321/330 (97.3%) for the other cases, with no new model call or claimed
model improvement.

## Earlier development runs

* Repaired catalog, old single-pick 20B rig: 276/325 positive cases (84.9%), excluding one
  service error, with incorrect negative labels still present. This is not directly comparable
  to the reviewed corpus.
* Structure-first 120B proposal: common 297/308 (96.4%), combined catalog cohort 279/350 (79.7%).
* 120B review: common 293/308 (95.1%), combined catalog cohort 302/350 (86.3%).
* 5.5 review: the full result above; both thresholds were met before any final label correction.

## Remaining raw mismatches

* How much of Ohio's state education funding goes to community college programs?
  Expected: point.component-share; received: point.value.

* How did the unemployment rate in Cook County change from June 2023 to June 2024?
  Expected: change.period-over-period; received: change.absolute.

* What is the year‑to‑year variation in NIH's grant funding?
  Expected: time.volatility; received: change.period-over-period.

* How much does the unemployment rate in Ohio change each year?
  Expected: time.volatility, change.absolute, change.percent, timeseries.single-entity; received: change.period-over-period.

* Which university generates more research funding per faculty member, Stanford or MIT?
  Expected: comparison.values; received: comparison.which.

* Which grew faster, Stanford or MIT in undergraduate enrollment between 2012 and 2022?
  Expected: comparison.growth; received: comparison.which.

* Of US states with unemployment rate below 4%, which have the highest median household income?
  Expected: multi.threshold-then-rank; received: multi.constrained-optimum.

* Identify cities where crime incidents and police budget rose over the past five years.
  Expected: multi.direction-relation; received: multi.change-conjunction-all.

* Count of patients served by the CDC in each state, where patients may be enrolled in multiple health programs.
  Expected: aggregate.by-group; received: aggregate.count-distinct.

* Pfizer's total vaccine doses per county versus uninsured rate per county.
  Expected: join.aggregate-each-side; received: comparison.values.

These include unresolved overlaps (amount versus component share, annual change versus volatility, single winner versus ranked list) as well as classification errors. They have not been silently relabeled to erase the mismatches.
