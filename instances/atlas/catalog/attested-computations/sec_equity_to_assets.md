---
type: Financial Statement Concept
title: equity to assets — SEC EDGAR
description: Reported annual equity to assets for public companies, one fiscal year
  or a series. Stockholders' equity / total assets
accessor: sec_company_facts
visibility: public
pack: public
xbrl:
  components:
  - taxonomy: us-gaap
    concepts:
    - StockholdersEquity
    - StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest
    unit: USD
    period_type: instant
    year_offset: 0
  - taxonomy: us-gaap
    concepts:
    - Assets
    unit: USD
    period_type: instant
    year_offset: 0
  unit: percent
  definition: Stockholders' equity / total assets
  expression:
    op: multiply
    args:
    - 100
    - op: divide
      args:
      - input: 0
      - input: 1
tags:
- SEC
- financials
- equity to assets
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
