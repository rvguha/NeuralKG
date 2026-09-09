---
id: ac.usa_top_baby_names
type: AttestedComputation
title: Most popular US baby names, by year (optionally by state and sex)
description: >
  The most common baby names in the United States — or in one state — for a
  given year, from the Social Security Administration's name counts,
  optionally for boys or girls only. A curated, parameterized template so
  "the most popular baby name in 2020" never depends on model-drafted SQL
  against the 6-million-row names table.
trust: human-reviewed
pack: public
reviewer: Bel
reviewed_on: 2026-09-06
stale_after: 2027-09-06
version: "1"
lifecycle: active
citation_template: "SSA baby names (bigquery-public-data.usa_names.usa_1910_current), births summed by name and sex for the year (and state, when named); names with fewer than 5 births in a state-year are not in the source."
tags: [baby names, "most popular name", "popular baby names", names, births, ssa, year, state, boys, girls]
source:
  kind: bigquery
  project: bigquery-public-data
  dataset: usa_names
  table: usa_1910_current
cost_profile:
  expected_bytes: 200000000
  cap_bytes: 1073741824
computation:
  runtime:
    executor: bigquery
    attester: human-reviewed
    parameters:
      - name: year
        type: INTEGER
        required: true
        description: Four-digit year, e.g. 2020. If the question says "most recent" or names no year, use 2023.
      - name: state
        type: STRING
        required: false
        description: Two-letter US state code (e.g. "CA") only when the question names a state; omit for the whole country.
      - name: sex
        type: STRING
        required: false
        description: >
          M for boys / male names, F for girls / female names, only when the
          question asks for one; omit for both.
      - name: top_n
        type: INTEGER
        required: false
        default: 10
        description: How many names to return (default 10).
    sql: |
      SELECT
        name,
        gender AS sex,
        SUM(number) AS births
      FROM `bigquery-public-data.usa_names.usa_1910_current`
      WHERE year = @year
        AND (@state IS NULL OR state = @state)
        AND (@sex IS NULL OR gender = @sex)
      GROUP BY name, gender
      QUALIFY ROW_NUMBER() OVER (ORDER BY births DESC) <= @top_n
      ORDER BY births DESC
---

## Why a template

"What was the most popular baby name in the United States in 2020?" is one
of the public demo's oldest example questions and answered correctly from
ad-hoc SQL most of the time — but not every time: in two of four golden
runs on 2026-09-06 the planner's SQL failed against the table and the
question came back as "couldn't fetch". The table is small (~6 M rows,
~160 MB), the question shape is fixed, and the SSA data has one trap — a
name appears once per state per sex per year, so a national answer must
SUM across states — which is exactly what a reviewed template pins down.

## Notes

- SSA publishes names with at least 5 births in a state-year; rarer names
  are absent, so state totals slightly undercount.
- `sex` is the SSA's `gender` column, values `M` / `F`.
