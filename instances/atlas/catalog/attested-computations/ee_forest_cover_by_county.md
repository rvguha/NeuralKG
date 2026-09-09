---
id: ac.ee_forest_cover_by_county
type: AttestedComputation
title: Tree cover and forest loss since 2000 by county (Earth Engine Hansen Global Forest Change in BigQuery)
description: >
  For every county in a US state: mean tree canopy cover in the year 2000
  (% of land, Landsat-derived) and the share of the county's area that has
  lost forest since 2000, from the Hansen / UMD Global Forest Change
  dataset at 30 m, computed in BigQuery with ST_REGIONSTATS over the Earth
  Engine image. Answers "which Oregon counties lost the most forest",
  "tree cover by county in Washington", "deforestation by county". Rows
  carry county FIPS, so the answer renders as a map. A satellite-derived
  estimate.
trust: human-reviewed
pack: public
reviewer: Bel
reviewed_on: 2026-09-08
stale_after: 2027-09-08
version: "1"
lifecycle: active
measurement_kind: computed
citation_template: "Hansen/UMD/Google/USGS/NASA Global Forest Change 2000–2024 (v1.12), via Google Earth Engine in BigQuery: ST_REGIONSTATS over each county boundary (US Census TIGER) at 300 m sampling — mean of `treecover2000` (% canopy in 2000) and mean of the binary `loss` band × 100 (share of county area with canopy loss 2001–2024). Landsat-derived estimates; loss means canopy removal, including harvest and fire, not net change."
tags: [earth engine, hansen, forest, "tree cover", deforestation, "forest loss", canopy, "by county", counties, state, landsat, satellite, map]
source:
  kind: bigquery
  project: bigquery-public-data
  dataset: geo_us_boundaries
  table: counties
sources:
  - kind: bigquery
    project: bigquery-public-data
    dataset: geo_us_boundaries
    table: counties
  - kind: bigquery
    project: bigquery-public-data
    dataset: geo_us_boundaries
    table: states
  - kind: earth_engine
    asset: UMD/hansen/global_forest_change_2024_v1_12
    docs: https://developers.google.com/earth-engine/datasets/catalog/UMD_hansen_global_forest_change_2024_v1_12
cost_profile:
  expected_bytes: 50000000
  cap_bytes: 1073741824
  note: "Earth Engine portion billed as BigQuery Services-SKU slot time; two ST_REGIONSTATS calls per county at 300 m — a state is seconds to tens of seconds of slot time."
computation:
  runtime:
    executor: bigquery
    attester: human-reviewed
    max_geometries: 260
    parameters:
      - name: state
        type: STRING
        required: true
        description: Two-letter US state code, e.g. "OR", "WA", "CA". Always a code, never the state name.
    sql: |
      SELECT
        c.geo_id,
        c.county_name,
        ROUND(ST_REGIONSTATS(
          c.county_geom, 'ee://UMD/hansen/global_forest_change_2024_v1_12', 'treecover2000',
          OPTIONS => JSON '{"scale": 300}'
        ).mean, 1) AS tree_cover_2000_pct,
        ROUND(100 * ST_REGIONSTATS(
          c.county_geom, 'ee://UMD/hansen/global_forest_change_2024_v1_12', 'loss',
          OPTIONS => JSON '{"scale": 300}'
        ).mean, 2) AS forest_loss_since_2000_pct_of_area,
        '2000–2024' AS period,
        'Hansen Global Forest Change via Earth Engine' AS source
      FROM `bigquery-public-data.geo_us_boundaries.counties` c
      JOIN `bigquery-public-data.geo_us_boundaries.states` s
        ON s.state_fips_code = c.state_fips_code
      WHERE s.state = @state
      ORDER BY forest_loss_since_2000_pct_of_area DESC
---

## What this answers

Forest cover and loss by county for a state, from the standard global
Landsat forest-change product, through the same guarded BigQuery path as
every other template. Two measures per county: canopy cover in 2000
(mean of `treecover2000`) and the share of the county's area with any
canopy loss 2001–2024 (mean of the binary `loss` band). ST_REGIONSTATS
has no pixel filter (an `include` option was tried live and rejected), so
the loss share is over the whole county area, water included.

## Method, honestly

"Loss" in Hansen is canopy removal in a pixel — timber harvest, wildfire
and clearing alike — and does not net out regrowth. Cover is a 2000
baseline. At 300 m sampling small counties carry more noise; the figures
are for ranking and comparison, not for inventory.
