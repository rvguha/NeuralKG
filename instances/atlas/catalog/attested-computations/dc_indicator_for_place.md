---
id: ac.dc_indicator_for_place
type: AttestedComputation
title: Reported statistic for a place — point value, trend or comparison (Data Commons)
description: >
  Any reported statistic Data Commons holds — population, median age, median
  household income, unemployment rate, GDP, life expectancy, CO2 emissions, health
  prevalence rates and ~250,000 more — for one or more named places
  (countries, states, counties, cities), as the latest value, one year, a
  year range, or the full history. "What is the population of India?",
  "how has median household income in Santa Clara County changed over
  the last 10 years?", "life expectancy in Japan vs the United States",
  "unemployment rate in California". Places and variables are resolved by
  Data Commons' own resolvers, never guessed; one source (facet) is used
  for the whole answer and named in every row.
trust: human-reviewed
pack: public
reviewer: Bel
reviewed_on: 2026-09-06
stale_after: 2027-03-06
version: "1"
lifecycle: active
citation_template: "Data Commons (datacommons.org) REST v2 observation API; place and variable resolved by Data Commons' resolvers; a single source facet (named per row as `source`, with its provenance URL, measurement method and observation period) is used for the whole answer."
tags: [datacommons, places, population, "population of a country", "population of a state", "population of a city", "median household income", "median income", "unemployment rate", "poverty rate", gdp, "gdp per capita", "life expectancy", "co2 emissions", "diabetes prevalence", "obesity", "median age", households, "housing units", "foreign born", "education attainment", statistic, "what is the", "how many people", trend, "over the last 10 years", "since 2010", compare, versus, country, state, county, city, "India", "Japan", "United States", "California", "Texas"]
representativeQueries:
  - What is the median age in Miami, Florida?
  - What is the population of India?
  - How has median household income in Santa Clara County changed over the last 10 years?
  - Compare life expectancy in Japan and the United States.
  - What is the diabetes prevalence in Texas?
source:
  kind: datacommons
  api: observation
  docs: https://docs.datacommons.org/api/rest/v2/observation.html
computation:
  runtime:
    executor: datacommons_place
    attester: human-reviewed
    parameters:
      - name: indicator
        type: STRING
        required: true
        description: >
          The statistic the question asks about, in the question's own words
          ("median household income", "unemployment rate", "life expectancy",
          "CO2 emissions per capita", "population"). Prefer one of these
          curated keys when one fits exactly: population, adult_population
          (people age 18 and older; use this denominator with diabetes_prevalence),
          median_household_income, median_age, unemployment_rate, labor_force,
          poverty_count, households, housing_units, gdp, gdp_per_capita,
          life_expectancy, fertility_rate, co2_emissions,
          co2_emissions_per_capita, diabetes_prevalence (adult prevalence), obesity_prevalence,
          crime_count, foreign_born_population, bachelors_or_higher. Otherwise
          pass the phrase as written — never a Data Commons variable id.
      - name: places
        type: ARRAY<STRING>
        required: true
        description: >
          Array of separately named places, e.g. ["Japan", "United States"] or
          ["Santa Clara County, CA"]. Keep a qualified place name together.
          Never a comma-separated string and never a DCID.
      - name: place_type
        type: STRING
        required: false
        description: >
          Only when the question makes the kind of place explicit: country,
          state, county, city. Omit otherwise.
      - name: period
        type: STRING
        required: false
        description: >
          "latest" when the question asks for the current value ("what is",
          "how many ... now"); "all" when it asks for a trend, history or
          change over time. Omit when a specific year or range is named.
      - name: year
        type: INTEGER
        required: false
        description: A single four-digit year, only when the question names one.
      - name: year_from
        type: INTEGER
        required: false
        description: Start year of a range ("since 2015", "2010 to 2020"), only when named.
      - name: year_to
        type: INTEGER
        required: false
        description: End year of a range, only when named ("the last 10 years" is year_from = current year minus 10, no year_to).
---

## What this answers

Reported statistics in the public catalog: a number an agency
published (Census, BLS, World Bank, WHO, CDC, …) for a named place, as
Data Commons has harmonised it onto one place graph. One template covers
the three shapes a plain question takes — a point value ("population of
India"), a trend ("how has it changed since 2010"), and a comparison of
named places ("Japan vs. the United States") — because the same fetch
answers all three; only the year filter and the number of places differ.

## Why the model binds words, not identifiers

Data Commons keys everything by DCID (`geoId/06085`, `Median_Income_Household`).
Letting the planner emit those would let a plausible-looking but wrong id
reach a real request. Instead the planner passes the question's own words
and `backend/accessor/datacommons_accessor.py` resolves them:

- **place** → Data Commons' `/v2/resolve` (typed by `place_type` when
  given), with a tiny human-reviewed list for names the resolver can't be
  asked for ("the world" → `Earth`). The canonical name Data Commons
  returns is recorded, so a "Springfield" that resolved to the wrong one is
  visible in the receipt rather than hidden.
- **indicator** → the curated key map first, then Data Commons' indicator
  resolver (`resolver=indicator`); the first candidate that actually has an
  observation for the place asked about wins. Topics are never candidates.

## One source per answer

Data Commons holds the same variable from several sources (ACS 1-year vs
5-year, BLS vs Census unemployment). The accessor picks the facet covering
the most of the requested places (ties → Data Commons' preferred order),
uses only that facet for every place and year, and writes its
`importName`, `provenanceUrl`, `measurementMethod` and `observationPeriod`
on every row. The receipt lists the other facets that were available.

## Limits

- Coverage is deepest for the United States; international coverage is
  mostly country-level (World Bank, UN, WHO). A place that resolves but
  holds no observation for the variable returns no rows — Atlas says so.
- No unit conversion or inflation adjustment: values are as published.
- The API has no date-range filter; ranges are applied after the fetch, and
  a single fetch is capped at 5,000 observations.
