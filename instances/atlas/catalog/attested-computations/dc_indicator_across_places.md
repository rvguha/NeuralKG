---
id: ac.dc_indicator_across_places
type: AttestedComputation
title: Rank every county / state / city / country inside a place by a reported statistic (Data Commons)
description: >
  A ranking or comparison of all places of one type inside a parent place —
  every county in California, every state in the US, every country in
  Africa or the world — by one reported statistic (population, median
  income, unemployment rate, GDP per capita, life expectancy, health
  prevalence, …), latest value per place or one named year. Highest,
  lowest, top N. Only for questions that ask about ALL places of a kind
  inside a parent ("which counties in California…", "rank US states by…",
  "the ten African countries with the lowest…"); a question about one
  named place is ac.dc_indicator_for_place instead. Child places come
  from Data Commons' containment graph; one source (facet) is used for the
  whole ranking.
trust: human-reviewed
pack: public
reviewer: Bel
reviewed_on: 2026-09-06
stale_after: 2027-03-06
version: "1"
lifecycle: active
citation_template: "Data Commons (datacommons.org) REST v2: child places enumerated with `containedInPlace+` from Data Commons' place graph, one observation per place from a single source facet (named per row as `source`, with provenance URL, measurement method and observation period), ranked by value."
tags: [datacommons, places, ranking, rank, "which counties", "which states", "which cities", "which countries", "by state", "by county", "across countries", counties, states, cities, countries, highest, lowest, "top 10", "top ten", most, least, largest, smallest, population, "median household income", "unemployment rate", "life expectancy", "gdp per capita"]
source:
  kind: datacommons
  api: observation
  docs: https://docs.datacommons.org/api/rest/v2/observation.html
computation:
  runtime:
    executor: datacommons_children
    attester: human-reviewed
    max_entities: 3500
    parameters:
      - name: indicator
        type: STRING
        required: true
        description: >
          The statistic to rank by, in the question's own words, or one of
          the curated keys: population, median_household_income, median_age,
          unemployment_rate, labor_force, poverty_count, households,
          housing_units, gdp, gdp_per_capita, life_expectancy,
          fertility_rate, co2_emissions, co2_emissions_per_capita,
          diabetes_prevalence, obesity_prevalence, crime_count,
          foreign_born_population, bachelors_or_higher. Never a variable id.
      - name: parent_place
        type: STRING
        required: true
        description: >
          The containing place as written: "California", "United States",
          "Africa", "the world", "Texas". Never a DCID.
      - name: child_type
        type: STRING
        required: true
        description: >
          The kind of place being ranked: county, state, city, country,
          continent, zip code, congressional district. Take it from the
          question's wording ("which counties", "by state", "countries in").
      - name: year
        type: INTEGER
        required: false
        description: A single four-digit year, only when the question names one; otherwise the latest value per place is used.
      - name: top_n
        type: INTEGER
        required: false
        description: How many places to return (default 25, max 500) — set it when the question says "top 10", "five worst", etc.
      - name: order
        type: STRING
        required: false
        description: >
          desc for highest / most / largest first (the default); asc for
          lowest / least / smallest first.
---

## What this answers

"Which counties in California have the highest unemployment rate?", "rank
US states by median household income", "the ten African countries with
the lowest life expectancy". The child places are not typed by the model:
they are enumerated from Data Commons' `containedInPlace+` graph for the
resolved parent, capped at 3,500 places (every US county fits; every US
city does not — Atlas says so and asks for a narrower parent rather than
silently truncating).

## How the ranking is made

1. `parent_place` → DCID and canonical name via Data Commons' resolver.
2. Children of `child_type` → `/v2/node` with `<-containedInPlace+{typeOf:…}`.
3. `indicator` → curated key or Data Commons' indicator resolver, keeping
   the first variable that has data for those children.
4. One `/v2/observation` call for all children; the facet covering the most
   children is used for every row (so the ranking compares like with like);
   the latest observation per place, or the named year.
5. Sorted by value, `top_n` kept, with a `rank` column.

`places_in_parent` and `places_with_data` in the bound params say how many
children exist and how many the chosen source covers — a ranking of 40 of
58 counties is reported as exactly that.

## Limits

- Latest value per place can be different years for different places when
  a source publishes unevenly; the `date` column is on every row and the
  narrative must say so when it varies.
- International sub-national coverage is thin; country-level rankings are
  reliable, state/province rankings outside the US often are not.
