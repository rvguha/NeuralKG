---
id: ac.ee_flood_hazard_by_county
type: AttestedComputation
title: Flood hazard by county — modelled inundation depth for a 1-in-N-year river or coastal flood (WRI Aqueduct, Earth Engine in BigQuery)
description: >
  For every county in a US state: the mean modelled inundation depth over
  the whole county (centimetres) and the deepest modelled point (metres)
  for a flood of a chosen return period — the 100-year
  flood by default — from WRI Aqueduct Floods hazard maps (1 km,
  baseline climate), riverine or coastal, computed in BigQuery with
  ST_REGIONSTATS over the Earth Engine image. Answers "which counties in
  Texas face the worst flood risk", "100-year flood hazard by county in
  Florida", "coastal flood exposure by county in Louisiana". Rows carry
  county FIPS, so the answer renders as a map. A regional-scale hazard
  model, not a floodplain map.
trust: human-reviewed
pack: public
reviewer: Bel
reviewed_on: 2026-09-08
stale_after: 2027-09-08
version: "1"
lifecycle: active
measurement_kind: computed
citation_template: "WRI Aqueduct Floods Hazard Maps v2 (baseline climate; riverine: WATCH 1980 model, coastal: no subsidence, historical sea level), via Google Earth Engine in BigQuery: ST_REGIONSTATS mean (reported in cm; unflooded land counts as zero, so this is depth averaged over the whole county — an exposure measure) and max (m) of the 1 km `inundation_depth` band inside each county boundary (US Census TIGER) at 1 km sampling, for the return period requested. Modelled hazard estimates from a regional model WRI recommends for county-scale comparison, not for property-level or floodplain mapping."
tags: [earth engine, flood, flooding, "flood risk", "flood hazard", inundation, "100-year flood", river, coastal, "sea level", "by county", counties, state, aqueduct, wri, map]
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
    asset: WRI/Aqueduct_Flood_Hazard_Maps/V2
    analytics_hub_listing: aqueduct_flood_hazard_v2
    docs: https://developers.google.com/earth-engine/datasets/catalog/WRI_Aqueduct_Flood_Hazard_Maps_V2
cost_profile:
  expected_bytes: 50000000
  cap_bytes: 1073741824
  note: "Earth Engine portion billed as BigQuery Services-SKU slot time; one 1 km image, one ST_REGIONSTATS call per county — a state is seconds of slot time."
computation:
  runtime:
    executor: bigquery
    attester: human-reviewed
    max_geometries: 260
    parameters:
      - name: state
        type: STRING
        required: true
        description: Two-letter US state code, e.g. "TX", "FL", "LA". Always a code, never the state name.
      - name: flood_type
        type: STRING
        required: false
        default: river
        description: >
          river for riverine (inland) flooding — the default and what a
          plain "flood risk" question means; coast for coastal / storm-surge
          / sea-level flooding, only when the question says coastal, sea or
          surge.
      - name: return_period
        type: INTEGER
        required: false
        default: 100
        description: >
          Return period in years — one of 2, 5, 10, 25, 50, 100, 250, 500,
          1000. "100-year flood" → 100 (the default); "once-in-500-years" →
          500. Omit unless the question names one.
    sql: |
      WITH img AS (
        SELECT
          CASE
            WHEN LOWER(@flood_type) = 'coast' THEN
              CONCAT('ee://WRI/Aqueduct_Flood_Hazard_Maps/V2/inuncoast_historical_nosub_hist_rp', FORMAT('%04d', @return_period), '_0')
            ELSE
              CONCAT('ee://WRI/Aqueduct_Flood_Hazard_Maps/V2/inunriver_historical_000000000WATCH_1980_rp', FORMAT('%05d', @return_period))
          END AS asset
      )
      SELECT
        c.geo_id,
        c.county_name,
        ROUND(100 * ST_REGIONSTATS(c.county_geom, img.asset, 'inundation_depth', OPTIONS => JSON '{"scale": 1000}').mean, 1) AS mean_inundation_depth_cm,
        ROUND(ST_REGIONSTATS(c.county_geom, img.asset, 'inundation_depth', OPTIONS => JSON '{"scale": 1000}').max, 2) AS max_inundation_depth_m,
        CONCAT('1-in-', CAST(@return_period AS STRING), '-year ', IF(LOWER(@flood_type) = 'coast', 'coastal', 'riverine'), ' flood, baseline climate') AS period,
        'WRI Aqueduct Floods via Earth Engine' AS source
      FROM `bigquery-public-data.geo_us_boundaries.counties` c
      JOIN `bigquery-public-data.geo_us_boundaries.states` s
        ON s.state_fips_code = c.state_fips_code
      CROSS JOIN img
      WHERE s.state = @state
      ORDER BY mean_inundation_depth_cm DESC
---

## What this answers

County-level flood hazard for a state from WRI's Aqueduct Floods, the
standard global flood-hazard model, through the same guarded BigQuery
path as every other template. Two measures per county for the chosen
event: the mean modelled inundation depth over the county's whole area
(in centimetres — unflooded land counts as zero, so it is an exposure
measure: Harris County, TX averaged 34 cm for the riverine 100-year
flood in the live check, El Paso 3 cm) and the deepest modelled point in
metres. The default event is the riverine 100-year flood
under baseline climate; coastal flooding and other return periods are
parameters.

## Method, honestly

Aqueduct is a 1 km hydrological model. WRI's own guidance, carried in the
dataset's metadata, is that it suits "broad risk to an area the size of a
US county" and relative comparisons, and is *not* for property-level
inundation mapping, flat lowland rivers with backwater effects, or
hydraulic structures. A county mean spreads the modelled floodplain over
the whole county, so it ranks exposure rather than describing any place
within it. Coastal means are small numbers — a 1 km strip of surge
diluted over a county (Monroe County, FL: 0.7 cm for the 100-year
event; inland counties 0) — and are for ranking coastal counties against
each other, not for reading as a depth anyone would see. Only the baseline (historical) climate is offered here; the
2030/2050/2080 RCP scenarios and coastal subsidence variants exist in the
collection and are a natural next parameter.
