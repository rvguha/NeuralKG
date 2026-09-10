---
type: Financial Statement Concept
title: roa — SEC EDGAR
description: Reported annual roa for public companies, one fiscal year or a series.
  Net income / average total assets
accessor: sec_company_facts
visibility: public
pack: public
xbrl:
  components:
  - taxonomy: us-gaap
    concepts:
    - NetIncomeLoss
    unit: USD
    period_type: duration
    year_offset: 0
  - taxonomy: us-gaap
    concepts: &id001
    - Assets
    unit: USD
    period_type: instant
    year_offset: 0
  - taxonomy: us-gaap
    concepts: *id001
    unit: USD
    period_type: instant
    year_offset: -1
  unit: percent
  definition: Net income / average total assets
  expression:
    op: multiply
    args:
    - 100
    - op: divide
      args:
      - input: 0
      - op: divide
        args:
        - op: add
          args:
          - input: 1
          - input: 2
        - 2
tags:
- SEC
- financials
- roa
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
