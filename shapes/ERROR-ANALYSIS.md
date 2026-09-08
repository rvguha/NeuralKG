# What the ten raw mismatches have in common

The strongest run missed none of the 308 ordinary questions. Its ten catalog mismatches mostly
show overlapping or underspecified labels, not a failure to identify the question's subject or
its main computation. Model explanations frequently state information that the selected ID
cannot represent. A one-label score obscures that difference.

## Four questions do not uniquely imply their expected operation

* “How much of Ohio's ... funding goes to community college programs?” does not demand a
  fraction rather than an amount. The model's amount reading is defensible.
* “year-to-year variation” does not uniquely demand a dispersion statistic. A series of annual
  changes is also a defensible reading.
* “How much ... change each year?” is naturally annual period-over-period change. The existing
  accepted set allowed several interpretations but accidentally excluded that one.
* “Of states below 4% unemployment, which have the highest income?” asks for maximizers. It
  does not clearly request an ordered list rather than the winner(s). The model's constrained
  optimum reading is defensible.

These need explicit tests for both interpretations or clarification expectations. Choosing a
single arbitrary gold label does not make the wording unambiguous.

## Two expected labels are wrong or misleading

* “Which university generates more ... Stanford or MIT?” explicitly asks for the winner. The
  merge mechanically mapped this case to comparison.values. It is now corrected, with the raw
  mismatch retained in the measured report; both targets were met before the correction.
* “Crime incidents and police budget rose” asserts two positive changes: delta(crime)>0 AND
  delta(budget)>0. The predicted change-conjunction is a good reading. Sign agreement alone
  also admits both falling, so a same-direction pattern must retain explicit polarity
  restrictions to answer this question. A direction-only label is insufficient by itself.

## Four pairs label different dimensions of the same computation

| Question | Competing labels | Both dimensions that need representation |
|---|---|---|
| June 2023 to June 2024 unemployment change | absolute change / period-over-period | subtraction AND a calendar-aligned period pair |
| Which university grew faster? | winning named candidate / growth comparison | argmax over named candidates AND a growth expression |
| Distinct patients in each state | grouped aggregate / count-distinct | GROUP BY state AND COUNT(DISTINCT patient) |
| Vaccine totals versus uninsured rates per county | comparison values / independent grouped measures | project two measures at county grain, preserving each measure's expression |

The final case does not establish that BOTH measures need aggregation. The uninsured rate
could be directly supplied. Inferring aggregate-each-side from the wording crosses into an
assumption about source capabilities. Independent operand grain still matters, but source
layout cannot decide the semantic label.

For grouped patient counting, the model's explanation explicitly retained “in each state”
while selecting count-distinct. Thus the scalar shape ID would be incomplete, even though
the explanation understood the group boundary. The remedy is a representation that can
carry both, rather than training the classifier to forget one dimension.

## Implication for the flat catalog

We can retain a flat catalog. Its entries need consistent semantic contracts: requested result,
operand expressions, predicates, grouping grain, period relationships and unmatched semantics.
Parameter values such as comparator direction or named sources must not create duplicate labels.
Where the catalog deliberately enumerates a compound leaf, that leaf must state all its defining
dimensions; a competing broad card must not claim the same question.

The next evaluation should separately score the operator structure and its bound parameters.
Then a correct GROUP BY plus COUNT DISTINCT is recognized as correct regardless of which partial
description happens to be the ID. Do not erase these ten raw mismatches: use them as the fixed
boundary cases for that representation work. No further catalog mergers were made merely to
increase the reported score.
