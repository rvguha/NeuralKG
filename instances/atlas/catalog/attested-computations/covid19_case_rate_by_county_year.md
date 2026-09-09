---
id: ac.covid19_case_rate_by_county_year
type: AttestedComputation
title: COVID-19 case rate by county, by year
description: >
  New confirmed cases per 100,000 population for a named US county, summed by
  calendar year. A curated, parameterized template — not model-drafted SQL —
  so this is the preferred path whenever a question fits its shape.
trust: human-reviewed
tags: [covid-19, county, year, rate, public-health]
source:
  kind: bigquery
  project: bigquery-public-data
  dataset: covid19_open_data
  table: covid19_open_data
computation:
  runtime:
    executor: bigquery
    attester: human-reviewed
    parameters:
      - name: county_name
        type: STRING
        description: County name as it appears in `subregion2_name`, e.g. "Alameda County"
      - name: state_code
        type: STRING
        description: Two-letter US state code, e.g. "CA"
    sql: |
      SELECT
        EXTRACT(YEAR FROM date) AS year,
        SUM(new_confirmed) AS total_confirmed,
        ANY_VALUE(population) AS population,
        ROUND(SUM(new_confirmed) / ANY_VALUE(population) * 100000, 1) AS cases_per_100k
      FROM `bigquery-public-data.covid19_open_data.covid19_open_data`
      WHERE subregion2_name = @county_name
        AND subregion1_code = @state_code
        AND country_code = 'US'
      GROUP BY year
      ORDER BY year
---

## Why this exists as a template, not ad-hoc SQL

The plan/synthesis models never write SQL against this table when a question
matches this shape — "case rate in \<county\> by year" is common enough, and
the population-normalization step easy enough to get subtly wrong (which
`population` column, which year's estimate), that a human-reviewed template
is both cheaper (higher byte cap, plan §00/§10) and safer than a fresh draft
per request. The planner's job is only to recognize the shape and extract
`county_name` / `state_code` from the question; the SQL text itself never
changes at request time.

## Trust note

`trust: human-reviewed` — this is the top tier in the OKF provenance model.
Citations synthesized from this template's results are labeled accordingly.
