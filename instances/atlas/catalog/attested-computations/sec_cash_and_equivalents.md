---
type: Financial Statement Concept
title: cash and equivalents — SEC EDGAR
description: Reported annual cash and equivalents for public companies, one fiscal
  year or a series. Uses the curated XBRL concept family and reported units.
accessor: sec_company_facts
visibility: public
pack: public
xbrl:
  taxonomy: us-gaap
  concepts:
  - CashAndCashEquivalentsAtCarryingValue
  - CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents
  unit: USD
  period_type: instant
  year_offset: 0
tags:
- SEC
- financials
- cash and equivalents
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
