---
type: Financial Statement Concept
title: operating cash flow — SEC EDGAR
description: Reported annual operating cash flow for public companies, one fiscal
  year or a series. Uses the curated XBRL concept family and reported units.
accessor: sec_company_facts
visibility: public
pack: public
xbrl:
  taxonomy: us-gaap
  concepts:
  - NetCashProvidedByUsedInOperatingActivities
  unit: USD
  period_type: duration
  year_offset: 0
tags:
- SEC
- financials
- operating cash flow
curation_provenance:
  reviewer: Bel
  reviewed_on: '2026-09-03'
  scope: original metric-to-concept mapping; generated adapter implementation not
    reviewed by Bel
computation:
  runtime:
    parameters:
    - name: companies
      type: ARRAY<STRING>
      required: true
      description: Array of separately named companies, e.g. ["Apple","Microsoft","Nvidia"].
        Never one comma-separated string.
    - name: fiscal_year
      type: INTEGER
      required: false
      description: Explicit fiscal year only.
    - name: years
      type: INTEGER
      required: false
      description: Number of latest fiscal years requested.
    - name: year_from
      type: INTEGER
      required: false
      description: First requested fiscal year.
    - name: year_to
      type: INTEGER
      required: false
      description: Last requested fiscal year.
    - name: period
      type: STRING
      required: false
      description: latest for current value; all for history.
---
