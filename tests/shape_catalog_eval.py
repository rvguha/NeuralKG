"""Fixed-input catalog classification evaluation, with separate common/rest targets.

Usage: python tests/shape_catalog_eval.py --cohort common|rest|all --output PATH
Errors count as failures. Labels are fixed before running; accepted sets represent
documented ambiguity. This evaluates catalog classification, not end-to-end answers.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import llm

SYSTEM = '''Classify the requested query structure using the supplied catalog. Return JSON
{"requested_result": "what the answer contains", "candidate_scope": "one named entity / explicitly named candidates / unenumerated population", "computation": "required operations", "shape": "an exact catalog id", "reason": "brief structural reason", "missing": "" }, or shape:null
for a request outside the catalog.
Set "missing" to the name of a plan-changing parameter the question leaves unstated — the
concentration index, the dispersion statistic, the aggregate operator, the expectation model,
the measure, the entity, the threshold, the period. Naming one means the shape is identified
but the computation is not determined, and guessing it would answer a different question.
Leave "missing" empty when the question determines its own computation. Do not populate it
merely because a value must be looked up. Select the most specific definition justified by the
words of the question. Determine the result requested first, then required computations.

A stored metric named rate, percentage, median, earnings per share, or exchange rate is
a value lookup unless the question explicitly requests computing a ratio or aggregate.
A statistic named as an attribute of one city/company is a point lookup; a statistic
computed across explicitly requested members of a population is an aggregate.
Latest or current alone specifies the period; time.latest-vintage additionally asks for
the observation date/currency. A categorical attribute includes names, URLs and dates.
A yes/no status question differs from asking which category an entity belongs to.
An ambiguous measure such as size or performance still permits identifying its point
structure. Do not return null merely because the metric requires clarification.
Named candidates with a requested winner use comparison.which. Unnamed populations with
one requested winner use rank.extremum-single. Ordered lists use rank.top-n.
Naming a population TYPE (counties, companies, universities) is NOT enumerating candidates.
'Which county has the highest X?' is rank.extremum-single, never comparison.which.
An omitted limit in 'rank companies by X' still uses rank.top-n with an unbounded limit.
Taxonomy choices ('private foundation or public charity?') request a categorical value;
'is it a public charity?' requests a boolean status.
An unqualified comparison asks for values alongside each other; do not invent a quotient
or subtraction. Explicit ratio, difference, winner and normalized comparison requests
must preserve those operations. Source names and topic restrictions are operand details.
Entity lists request individual records. Amount questions request a value even when
computing it might require summing source records. Do not infer joins from source layout.
Bare noun phrases requesting awards/filings/contracts for a named recipient return those
records. Never convert a record request into a count without 'how many' or 'count'.
A named entity's total revenue remains point.value. 'Total revenue from 2019 to 2023'
requests an annual series unless 'combined across years' explicitly requests a sum.
'Grants an actor can apply for' asks eligibility, even if the actor is described by type.
One location condition uses spatial.containment rather than a conjunction of invented
conditions. A geographic name defining the population of a stored rate is a point lookup.
For multi-measure conditions inspect each operand: level vs change, its period and
threshold. Independent windows are explicit. Same/opposite direction without numeric
thresholds uses the direction-relation shape. Conditional proportions specify their
denominator; do not turn an ordinary numeric mean into a proportion.
Return null for writing tasks, advice, instructions, translation or conversation. Factual
attribute lookups remain data queries regardless of subject or whether a connected source
can answer. Use a metadata definition shape only for the measure definitions it describes.
'''

REVIEW_SYSTEM = '''You audit a proposed query-shape classification. The initial classifier
often chooses a broad operation while overlooking a more specific requested computation.
Choose the most specific catalog shape supported by the QUESTION, using these checks.
Do not agree with the initial answer out of deference. All catalog definitions are supplied.

Check in this order, and state which explicit words settle the choice:
1. Scope of an ordinary metric: 'median income/rent/age in [one named place]' is point.value.
   These are named statistical attributes of ONE place. aggregate.median requires a median
   ACROSS multiple stated population members. A company's 'contributions and grants' is
   one combined financial line item, not two comparison operands. Total funding received
   by ONE organisation is point.value even if transactions might need summing.
2. Dates: a founding/ruling/status-start date is point.categorical. time.first-crossing
   requires a numeric measure crossing a cut-off. Explicit observation date/fiscal year
   requested WITH the newest value is time.latest-vintage. Original unrevised release
   is source.vintage. A value at a named event is time.level-at-event.
3. Temporal matches: 'for each event/record ... value in force/current at that record's
   date' is temporal.as-of. Nearest/closest observation relative to each reference date
   is temporal.nearest. Before/after ordering of two events is temporal.event-sequence.
   None of these are plain time series, generic enrichment or simultaneous sign change.
4. Change: one entity, one measure at two dates, difference in units -> change.absolute;
   difference divided by earlier level -> change.percent. Explicit year-over-year or
   matching month/quarter in earlier years -> change.period-over-period. Cumulative total
   since a start -> time.cumulative-to-date. Annualized growth -> change.rate-annualized.
5. Multi-measure selection: independently stated time windows -> multi.independent-windows.
   Two change magnitude thresholds with AND/OR -> multi.change-conjunction-all/disjunction-any.
   One change threshold plus one level threshold -> multi.change-plus-level.
   Only same/opposite increase/decrease directions, no numeric cut-offs ->
   multi.direction-relation. Don't demote any of these to a generic level predicate.
6. Relations: return locations/entities reached through intermediate related entities ->
   join.multi-hop, even if only two hops. Explicit include subordinate entities ->
   relation.hierarchy-rollup; explicit exclude subordinates -> relation.hierarchy-exclusive.
   Derive a small area's value from a larger area's total and allocation share ->
   relation.geographic-disaggregation. Named relational membership ('member of [entity]')
   -> relation.existence; general tax/registration classification -> point.status.
7. Spatial: within a named place/boundary -> spatial.containment; within N distance ->
   spatial.proximity; nearest entity -> spatial.nearest. Do not use records.matching for
   an explicit spatial criterion. A location qualifier on a metric lookup is still point.
8. Aggregation: each group with a statistic -> aggregate.by-group. Independent totals for
   each of two measures aligned by the SAME groups -> join.aggregate-each-side. Unmatched
   group members explicitly retained -> join.outer-enrichment. Explicit unique/distinct
   entities -> aggregate.count-distinct. Share captured by TOP N -> aggregate.concentration.
   Share for EVERY group -> aggregate.share-by-group. Fraction of population below a
   NAMED entity's value -> rank.percentile-of-entity. These differ in denominator/scope.
9. Negation: explicitly reported/recorded zero -> filter.negation-closed. Not listed,
   no reported record or missing from a file -> filter.negation-open. Use set.difference
   only when the question asserts authoritative non-membership or subtracts two complete
   membership lists; do not invent source completeness. Both directional differences
   ('A but not B, or vice versa', 'exactly one') -> set.symmetric-difference.
10. Counts of RELATED records meeting a number -> filter.count-predicate, not a simple
    attribute threshold. Interval activity/any observation DURING a window ->
    filter.temporal-active. A yes/no population verdict differs from a returned member list.
11. Association: one series predicting another at a LATER period -> assoc.lagged.
    Deviations from a predicted relationship -> assoc.outlier, not a ratio cut-off.
    Comparison of outcome distributions/means for two groups -> assoc.group-difference;
    named places compared on published rates -> comparison.which.
12. Eligibility of a described actor for opportunities -> topical.eligibility. Factual
    weather/attribute questions remain data questions; unavailable data does not mean null.

For any case not settled above use the exact catalog definition. Preserve common-point rules,
record lists versus amounts, named-candidate comparisons versus population rankings, requested
measure transformations, and result projection. Do not invent operations or additional inputs.
Carry forward any unstated plan-changing parameter as "missing" (see the classifier contract);
do not silently resolve it.
Return JSON {"evidence": "decisive query words and operator", "shape": "exact id or null", "missing": ""}.
'''

def review(question, initial, entries, ids):
    compact = '\n'.join(e['id'] + ': ' + e['asks'] for e in entries)
    errors = []
    for attempt in range(4):
        try:
            raw = llm.chat(SYSTEM+'\n\n'+REVIEW_SYSTEM, 'CATALOG:\n'+compact+'\nQUESTION: '+question+
                           '\nINITIAL PROPOSAL: '+json.dumps(initial,ensure_ascii=False),
                           json_mode=True, model=llm.rerank_model(), stage='rerank',
                           max_tokens=4500+1000*attempt, reasoning_effort='medium')
            result = json.loads(raw) if raw else {}
            if 'shape' not in result or (result['shape'] is not None and result['shape'] not in ids):
                raise ValueError('invalid review output')
            return {'pick':result['shape'], 'reason':result.get('evidence',''), 'missing':result.get('missing',''),
                    'initial_pick':initial.get('pick'), 'attempts':attempt+1}
        except Exception as exc:
            errors.append(type(exc).__name__+': '+str(exc)[:160])
    return {'pick':'ERROR','errors':errors,'initial_pick':initial.get('pick')}

def cards(entries):
    fields = ('id', 'title', 'asks', 'disc', 'requires', 'slots', 'examples')
    return '\n'.join(json.dumps({k: e[k] for k in fields if e.get(k)}, ensure_ascii=False) for e in entries)

def accepted(case):
    if 'accepted' in case:
        return case['accepted']
    gold = case.get('gold')
    return [gold] if isinstance(gold, str) else (gold or [])

def score_rows(rows):
    metrics = {}
    for name in ('common', 'rest', 'restored_common'):
        group = [r for r in rows if (r.get('provenance') == 'authored-common-regression' if name == 'restored_common'
                 else r['cohort'] == name and r.get('provenance') != 'authored-common-regression')]
        if group:
            hit = sum(r['correct'] for r in group)
            metrics[name] = dict(correct=hit, total=len(group), accuracy=hit/len(group),
                                 errors=sum(r['pick']=='ERROR' for r in group), target=.95 if name=='rest' else .99)
    neg = [r for r in rows if r['cohort'] == 'negatives']
    pos = [r for r in rows if r['cohort'] != 'negatives']
    if neg:
        by_kind = {}
        for kind in sorted({r['negative_kind'] for r in neg}):
            g = [r for r in neg if r['negative_kind'] == kind]
            by_kind[kind] = dict(correct=sum(r['correct'] for r in g), total=len(g),
                                 accuracy=sum(r['correct'] for r in g)/len(g))
        refuse = [r for r in neg if r.get('expect') == 'refuse']
        # Precision/recall of the refusal decision itself, over every case that was scored:
        # recall = refusals we should have made and did; precision = refusals we made that
        # were warranted. A harness that only counts negatives cannot see the second.
        tp = sum(r['pick'] is None for r in refuse)
        fp = sum(r['pick'] is None for r in pos)
        attract = {}
        for r in refuse:
            if r.get('false_accept'):
                attract[r['pick']] = attract.get(r['pick'], 0) + 1
        metrics['negatives'] = dict(
            by_kind=by_kind,
            no_shape_recall=tp/len(refuse) if refuse else None,
            no_shape_precision=tp/(tp+fp) if (tp+fp) else None,
            false_accepts=sum(r.get('false_accept', False) for r in neg),
            false_clarifies=sum(r.get('false_clarify', False) for r in pos),
            attractors=dict(sorted(attract.items(), key=lambda kv: -kv[1])))
    return metrics

def classify(question, catalog, ids):
    reasons = []
    for attempt in range(4):
        try:
            raw = llm.chat(SYSTEM, 'CATALOG:\n' + catalog + '\nQUESTION: ' + question,
                           json_mode=True, model=llm.rerank_model(), stage='rerank',
                           max_tokens=1600 + 800 * attempt)
            if raw is None:
                raise ValueError('empty completion')
            result = json.loads(raw)
            if 'shape' not in result or (result['shape'] is not None and result['shape'] not in ids):
                raise ValueError('invalid shape output')
            return {'pick': result['shape'], 'reason': result.get('reason', ''),
                    'missing': result.get('missing', ''), 'attempts': attempt + 1}
        except Exception as exc:
            reasons.append(type(exc).__name__ + ': ' + str(exc)[:160])
    return {'pick': 'ERROR', 'errors': reasons}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cohort', choices=['common', 'rest', 'negatives', 'all'], default='all')
    parser.add_argument('--output', required=True)
    parser.add_argument('--workers', type=int, default=5)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--review-from', help='Review every prediction from a previous complete run; labels are never sent to the LLM')
    parser.add_argument('--review', action='store_true', help='Run classification followed by structural review for every question')
    args = parser.parse_args()
    catalog_path = ROOT / 'shapes/query-shapes.yaml'
    catalog_bytes = catalog_path.read_bytes()
    entries = yaml.safe_load(catalog_bytes)['shapes']
    ids = {e['id'] for e in entries}
    corpus = []
    for name, file in [('common', 'shape_common_queries.json'), ('rest', 'shape_queries.json'),
                       ('negatives', 'shape_negatives.json')]:
        if args.cohort not in (name, 'all'):
            continue
        cases = json.loads((ROOT / 'tests' / file).read_text())['cases']
        corpus.extend(dict(case, cohort=name) for case in cases
                      if case.get('stage', 'classification') == 'classification')
    if args.limit:
        corpus = corpus[:args.limit]
    assert all(set(accepted(c)) <= ids for c in corpus), 'Dangling expected labels'
    text = cards(entries)
    prior = json.loads(Path(args.review_from).read_text())['rows'] if args.review_from else None
    prior_by_question = {(r['cohort'],r['q']): {'pick':r['pick'],'reason':r.get('reason','')} for r in prior} if prior else {}
    print(f'{len(entries)} shapes; {len(corpus)} cases; model={llm.rerank_model()}; prompt chars={len(text)}', flush=True)
    started = time.time()
    rows = []
    def run(case):
        result = review(case['q'], prior_by_question[(case['cohort'],case['q'])], entries, ids) if prior else classify(case['q'], text, ids)
        if args.review and not prior:
            result = review(case['q'], result, entries, ids)
        expected = accepted(case)
        missing = (result.get('missing') or '').strip()
        expect = case.get('expect')
        if expect == 'refuse':
            # A refusal is only correct as a refusal. Naming a shape is a false accept even
            # when the shape is a defensible reading of the words.
            correct = result['pick'] is None
        elif expect == 'clarify':
            # The shape IS identifiable; the failure mode is silently choosing a parameter.
            # Both halves are required: right shape AND an admission that something is unstated.
            # `missing` here is the MODEL's answer; case['missing_expected'] is the corpus label,
            # deliberately differently named after the two collided in dict(case, **result).
            correct = result['pick'] in expected and bool(missing)
        else:
            # A positive answered with a clarification request is not correct either; it is a
            # false clarify, and is tracked separately below rather than hidden in the total.
            correct = (result['pick'] in expected if expected else result['pick'] is None) and not missing
        return dict(case, accepted=expected, **result, correct=correct,
                    false_accept=(expect == 'refuse' and result['pick'] not in (None, 'ERROR')),
                    false_clarify=(expect not in ('refuse', 'clarify') and bool(missing)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(run, corpus):
            rows.append(row)
            Path(args.output + '.partial').write_text(json.dumps(rows, ensure_ascii=False))
            if len(rows) % 25 == 0:
                print(f'{len(rows)}/{len(corpus)} complete; {sum(r["correct"] for r in rows)} correct', flush=True)
    metrics = score_rows(rows)
    artifact = dict(metrics=metrics, model=llm.rerank_model(), seconds=time.time()-started,
                    catalog_sha256=hashlib.sha256(catalog_bytes).hexdigest(),
                    prompt_sha256=hashlib.sha256(((SYSTEM+'\n\n'+REVIEW_SYSTEM+'\n'+'\n'.join(e['id']+': '+e['asks'] for e in entries)) if prior or args.review else SYSTEM+text).encode()).hexdigest(),
                    review_from=args.review_from, rows=rows)
    Path(args.output).write_text(json.dumps(artifact, indent=2, ensure_ascii=False)+'\n')
    print(json.dumps(metrics, indent=2), flush=True)
    for row in rows:
        if not row['correct']:
            print(json.dumps({k: row[k] for k in ('q','accepted','pick')}, ensure_ascii=False), flush=True)
    print('artifact: ' + args.output, flush=True)

if __name__ == '__main__':
    main()
