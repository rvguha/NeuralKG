---
id: ac.ee_wildfire_risk_by_county
type: AttestedComputation
title: Wildfire risk by county — burn probability, hazard potential and risk to homes (USDA Wildfire Risk to Communities, Earth Engine in BigQuery)
description: >
  For every county in a US state: annual burn probability, the USDA
  Wildfire Hazard Potential index, and the relative risk to potential
  structures (homes), averaged over each county from the USDA Forest
  Service "Wildfire Risk to Communities" 30 m rasters — BigQuery's
  ST_REGIONSTATS over the Earth Engine mosaic. Answers "which counties in
  Oregon face the highest wildfire risk", "wildfire hazard by county in
  Colorado", "burn probability by county in California". Only for
  questions about fire, wildfire or burning — a question about heat,
  hottest counties or temperature is climate, not fire, and belongs to the
  ERA5-Land climate template. Rows carry county FIPS, so the answer
  renders as a map. Modelled risk, not a fire history.
trust: human-reviewed
pack: public
reviewer: Bel
reviewed_on: 2026-09-08
stale_after: 2027-09-08
version: "1"
lifecycle: active
measurement_kind: computed
citation_template: "USDA Forest Service Wildfire Risk to Communities v0 (FSim burn probability, landscape conditions as of end-2020; fire intensity as of end-2022), via Google Earth Engine in BigQuery: ST_REGIONSTATS mean of the 30 m bands BP (annual burn probability, shown ×100 as %), WHP (Wildfire Hazard Potential, the continuous relative index — higher is more hazardous; county means run into the thousands in high-hazard counties) and RPS (relative risk to potential structures) inside each county boundary (US Census TIGER) at 300 m sampling. Modelled, landscape-wide risk estimates — not a record of fires that occurred."
tags: [earth engine, wildfire, fire, "wildfire risk", "burn probability", hazard, "risk to homes", "by county", counties, state, usda, forest service, map]
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
    asset: USDA/WRC/v0_MOSAIC
    analytics_hub_listing: wildfire_risk_to_community_v0_mosaic
    docs: https://developers.google.com/earth-engine/datasets/catalog/USDA_WRC_v0
cost_profile:
  expected_bytes: 50000000
  cap_bytes: 1073741824
  note: "Earth Engine portion billed as BigQuery Services-SKU slot time; three ST_REGIONSTATS calls per county at 300 m over a 30 m mosaic — a state is tens of seconds of slot time."
computation:
  runtime:
    executor: bigquery
    attester: human-reviewed
    max_geometries: 260
    parameters:
      - name: state
        type: STRING
        required: true
        description: Two-letter US state code, e.g. "OR", "CO", "CA". Always a code, never the state name.
    sql: |
      SELECT
        c.geo_id,
        c.county_name,
        ROUND(100 * ST_REGIONSTATS(
          c.county_geom, 'ee://USDA/WRC/v0_MOSAIC', 'BP',
          OPTIONS => JSON '{"scale": 300}'
        ).mean, 3) AS annual_burn_probability_pct,
        ROUND(ST_REGIONSTATS(
          c.county_geom, 'ee://USDA/WRC/v0_MOSAIC', 'WHP',
          OPTIONS => JSON '{"scale": 300}'
        ).mean, 1) AS wildfire_hazard_potential,
        ROUND(ST_REGIONSTATS(
          c.county_geom, 'ee://USDA/WRC/v0_MOSAIC', 'RPS',
          OPTIONS => JSON '{"scale": 300}'
        ).mean, 2) AS risk_to_potential_structures,
        'conditions as of 2020–2022' AS period,
        'USDA Wildfire Risk to Communities via Earth Engine' AS source
      FROM `bigquery-public-data.geo_us_boundaries.counties` c
      JOIN `bigquery-public-data.geo_us_boundaries.states` s
        ON s.state_fips_code = c.state_fips_code
      WHERE s.state = @state
      ORDER BY wildfire_hazard_potential DESC
---

## What this answers

County-level wildfire risk for a state, from the USDA Forest Service's
national Wildfire Risk to Communities product, through the same guarded
BigQuery path as every other template. Three measures per county, each
the mean over the county's area:

- **annual burn probability** — the modelled chance any given point burns
  in a year (FSim, 270 m upsampled to 30 m), shown as a percentage;
- **Wildfire Hazard Potential** — the Forest Service's continuous index
  combining burn probability and expected intensity (a relative measure;
  Jackson County, OR averaged ~3,500 against Multnomah's ~220 in the live
  check, so compare counties by it, don't read it as a percentage);
- **risk to potential structures** — the relative risk to a house *if one
  stood at that location*, which is the measure the product was built for.

The default ranking is by hazard potential; synthesis names whichever
measure the question asked about.

## Method, honestly

These are landscape-wide *modelled* values from fuels and weather
simulations — not fire perimeters, not a count of past fires. A county
mean blends wilderness and town; the source itself describes "risk at the
location where the adverse effects take place". Data reflect landscape
conditions at the end of 2020 (burn probability) and 2022 (intensity),
from the USDA `v0` mosaic published to BigQuery through Analytics Hub.
