---
id: ac.ee_era5_climate_by_county
type: AttestedComputation
title: Temperature and precipitation by county, from satellite-era climate reanalysis (Earth Engine ERA5-Land in BigQuery)
description: >
  Mean 2 m air temperature (°C) and total precipitation (mm) for every
  county in a US state, for one month or a whole year, computed from the
  ECMWF ERA5-Land monthly reanalysis grid (~11 km) by averaging the grid
  cells inside each county boundary — BigQuery's ST_REGIONSTATS over an
  Earth Engine image. Answers "hottest / wettest counties in California in
  July 2025", "average temperature by county in Texas in 2024", "how much
  rain fell in each Oregon county last winter". Every row carries the
  county FIPS code, so the answer renders as a map. A computed estimate
  from a climate model grid, not a weather-station reading.
trust: human-reviewed
pack: public
reviewer: Bel
reviewed_on: 2026-09-08
stale_after: 2027-09-08
version: "1"
lifecycle: active
measurement_kind: computed
citation_template: "ECMWF ERA5-Land monthly aggregates (Copernicus Climate Change Service), via Google Earth Engine in BigQuery: ST_REGIONSTATS mean of ~11 km reanalysis grid cells inside each county boundary (US Census TIGER, bigquery-public-data.geo_us_boundaries) at 5 km sampling; temperature converted from kelvin, precipitation summed over the months requested. A modelled reanalysis estimate, not a station observation."
tags: [earth engine, era5, climate, weather, temperature, precipitation, rainfall, "by county", "every county", counties, state, hottest, wettest, driest, coldest, month, year, satellite, reanalysis, map]
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
    asset: ECMWF/ERA5_LAND/MONTHLY_AGGR
    docs: https://developers.google.com/earth-engine/datasets/catalog/ECMWF_ERA5_LAND_MONTHLY_AGGR
cost_profile:
  expected_bytes: 50000000
  cap_bytes: 1073741824
  note: "The Earth Engine portion is billed as BigQuery Services-SKU slot time, not bytes: ~58 counties × 12 months at 5 km ≈ seconds of slot time. Statewide, one month: under a second."
computation:
  runtime:
    executor: bigquery
    attester: human-reviewed
    max_geometries: 260
    parameters:
      - name: state
        type: STRING
        required: true
        description: Two-letter US state code for the state whose counties are compared, e.g. "CA", "TX". Always a code, never the state name.
      - name: year
        type: INTEGER
        required: true
        description: Four-digit year. ERA5-Land monthly data runs from 1950 to roughly two months before today; if the question names no year, use the most recent complete year.
      - name: month
        type: INTEGER
        required: false
        description: >
          Month number 1–12, only when the question names a month or season
          ("July 2025" → 7). For a season pick its middle month (summer → 7,
          winter → 1). Omit for the whole year: temperature is then the
          annual mean and precipitation the annual total.
    sql: |
      WITH months AS (
        SELECT m FROM UNNEST(GENERATE_ARRAY(1, 12)) AS m
        WHERE @month IS NULL OR m = @month
      ),
      monthly AS (
        SELECT
          c.geo_id,
          c.county_name,
          m,
          ST_REGIONSTATS(
            c.county_geom,
            CONCAT('ee://ECMWF/ERA5_LAND/MONTHLY_AGGR/', CAST(@year AS STRING), FORMAT('%02d', m)),
            'temperature_2m',
            OPTIONS => JSON '{"scale": 5000}'
          ).mean AS temp_k,
          ST_REGIONSTATS(
            c.county_geom,
            CONCAT('ee://ECMWF/ERA5_LAND/MONTHLY_AGGR/', CAST(@year AS STRING), FORMAT('%02d', m)),
            'total_precipitation_sum',
            OPTIONS => JSON '{"scale": 5000}'
          ).mean AS precip_m
        FROM `bigquery-public-data.geo_us_boundaries.counties` c
        JOIN `bigquery-public-data.geo_us_boundaries.states` s
          ON s.state_fips_code = c.state_fips_code
        CROSS JOIN months
        WHERE s.state = @state
      )
      SELECT
        geo_id,
        county_name,
        ROUND(AVG(temp_k) - 273.15, 1) AS mean_temp_c,
        ROUND(SUM(precip_m) * 1000, 1) AS precipitation_mm,
        COUNT(*) AS months_covered,
        CASE WHEN @month IS NULL THEN CAST(@year AS STRING)
             ELSE FORMAT('%d-%02d', @year, @month) END AS period,
        'ERA5-Land via Earth Engine' AS source
      FROM monthly
      WHERE temp_k IS NOT NULL
      GROUP BY geo_id, county_name
      ORDER BY mean_temp_c DESC
---

## What this answers

County-by-county climate for a US state from a reanalysis grid — the
first Earth Engine source in Atlas, reached through BigQuery's
`ST_REGIONSTATS` so it runs under the same guarded executor (dry run, byte
cap, timeout, receipt) as every other SQL template. Rows carry `geo_id`
(county FIPS), so the answer renders as a choropleth; both temperature and
precipitation come back on every row and synthesis names the one the
question asked about.

## Method, honestly

ERA5-Land is a *model* reanalysis at ~11 km: it assimilates observations
but the number for a county is the mean of grid cells inside its boundary,
not a thermometer reading. It is the right tool for "which counties were
hottest / wettest" comparisons and multi-decade climate questions; for
"what was the temperature in Chicago on a day" the NOAA GSOD station
tables are the reported observation and the planner keeps routing those
questions there. Small or narrow counties (San Francisco) are represented
by few cells at 5 km sampling.

## Limits

- One state at a time (`max_geometries` 260: Texas has 254 counties).
- Monthly granularity; a season is its middle month unless the whole year
  is asked for. Latest month available is typically two months behind.
- US counties only in this template; country and admin-1 versions are the
  next step once FAO GAUL boundaries are wired.
