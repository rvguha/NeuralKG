"""Question/catalog-based expectations, independent of model predictions.

Positive alternatives are deliberately non-exhaustive. An unlisted candidate is
unknown, not automatically wrong. Raw input assumptions are conditions for later
planning, not facts query understanding should invent.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Reviewed from question wording and the catalog contracts, not observed picks.
# These are alternate acquisition paths, not new interpretations of the question.
ALTERNATIVES = {
    'replacement.reduce.entity-share': [
        ('lookup.binary', 'The entity amount and the whole-population total are both available as scalars with the same period, grant scope, units and reporting basis.')],
    'replacement.lookup.binary': [
        ('series.window-comparison', 'The two annual revenue totals must be summed from raw within-year observations before computing percentage growth; contributions must not overlap.')],
    'replacement.series.window-comparison': [
        ('lookup.binary', 'Both requested quarterly spending totals are supplied as comparable scalars; subtract the earlier total from the later one.')],
    'replacement.compare.derived-values': [
        ('compare.values', 'The requested research-funding-per-faculty values are already supplied for each named university with equivalent definitions and periods.')],
    'replacement.compare.derived-winner': [
        ('compare.winner', 'The requested 2020–2024 growth values are already supplied for both named companies under the same growth convention.')],
    'replacement.compare.derived-difference': [
        ('lookup.binary', 'The two universities\' requested funding-per-faculty values are supplied as comparable scalars; compute their difference.')],
    'replacement.join.grouped-measures': [
        ('join.enrich', 'Both inputs already contain one independently aggregated total per county; joining must preserve that grain and measure scope.')],
    'replacement.join.paired-reduce': [
        ('reduce.streaming', 'Each county row co-delivers its obesity rate and population weight, with compatible definitions and no missing weight/value pairs.')],
}


def expectation(row):
    """Only reads question/fixture metadata. Never reads model outputs or scores."""
    routes = row.get('conditional_routes', [])
    acceptable = []
    for identifier in row['expected']:
        conditions = [r['when'] for r in routes if r['pattern'] == identifier]
        acceptable.append({'template': identifier,
                           'when': ' OR '.join(conditions) or row.get('input_contract', 'The template\'s declared input contract is satisfied.'),
                           'review': 'inherited' if row.get('scoreable') else 'provisional'})
    for identifier, condition in ALTERNATIVES.get(row.get('id'), []):
        if identifier not in {a['template'] for a in acceptable}:
            acceptable.append({'template':identifier, 'when':condition, 'review':'reviewed'})
    rejected = []
    binding_checks = []
    if row['question'].strip() == 'What fraction of all NIH grant dollars goes to Johns Hopkins?':
        acceptable = [
            {'template':'reduce.entity-share', 'when':'The Johns Hopkins numerator is supplied; the matching NIH-wide denominator must be summed from raw grant contributions.', 'review':'reviewed'},
            {'template':'lookup.binary', 'when':ALTERNATIVES['replacement.reduce.entity-share'][0][1], 'review':'reviewed'}]
        rejected = [{'template':'compare.derived-values', 'reason':'The requested result is one fraction, not separately derived measures displayed for multiple named candidates.'}]
        binding_checks = [{'kind':'forbid_period_value', 'value':'all time',
                           'reason':'All NIH grant dollars denotes population scope, not a lifetime time range.'}]
    return {'acceptable':acceptable, 'rejected':rejected, 'exhaustive':False,
            'review_status':'partial' if row.get('scoreable') or binding_checks else 'pending',
            'binding_checks':binding_checks,
            'notes':'Alternative API conditions are not asserted to be available. Unlisted approaches remain unreviewed. Binding checks are partial, never full extraction validation.'}


def period_values(value, period_context=False):
    if isinstance(value, dict):
        for key, child in value.items():
            yield from period_values(child, period_context or key in ('period','periods','time_range','time_scope'))
    elif isinstance(value, list):
        for child in value: yield from period_values(child, period_context)
    elif period_context and isinstance(value,str):
        yield ' '.join(value.lower().replace('-', ' ').replace('_', ' ').split())


def score(output, expected):
    approved = {a['template'] for a in expected['acceptable'] if a['review'] != 'provisional'}
    rejected = {a['template'] for a in expected['rejected']}
    candidates = output.get('candidates', [])
    verdicts=[]
    for candidate in candidates:
        identifier=candidate['shape']
        verdict='acceptable' if identifier in approved else ('invalid' if identifier in rejected or expected['exhaustive'] else 'unreviewed')
        failures=[]
        for check in expected['binding_checks']:
            if check['kind']=='forbid_period_value' and check['value'] in set(period_values(candidate)):
                failures.append(check['reason'])
        binding_status=('error' if candidate.get('status')!='ok' else 'fail' if failures else
                        'partially_checked' if expected['binding_checks'] else 'unscored')
        verdicts.append({'template':identifier,'verdict':verdict,
                         'extraction_status':candidate.get('status','error'),
                         'binding_status':binding_status,'binding_failures':failures})
    n=len(verdicts); valid=sum(v['verdict']=='acceptable' for v in verdicts)
    unknown=sum(v['verdict']=='unreviewed' for v in verdicts)
    coverage = True if valid else (False if n and not unknown else False if expected['exhaustive'] else None)
    precision = valid/n if n and not unknown else None
    return {'coverage':coverage, 'candidate_precision':precision,
            'precision_bounds':[valid/n,(valid+unknown)/n] if n else None,
            'acceptable_candidates':valid,'invalid_candidates':n-valid-unknown,
            'unreviewed_candidates':unknown,'candidate_count':n,
            'candidate_verdicts':verdicts,
            'extraction_errors':sum(v['extraction_status']!='ok' for v in verdicts),
            'binding_failures':sum(v['binding_status']=='fail' for v in verdicts),
            'note':'Coverage scores retained template selection independently of extraction success. Precision is unknown when any retained candidate is unreviewed; bounds are shown instead.'}


def apply(row, expected):
    result=row['understanding']
    row['expectations']=expected
    result.setdefault('original_scoring', {k:result.get(k) for k in ('status','summary','expected','label_agreement','extraction_scored')})
    result['expected']=[a['template'] for a in expected['acceptable']]
    result.pop('label_agreement',None)
    if not isinstance(result.get('output'),dict):
        result['evaluation']={'coverage':None,'candidate_precision':None,'status':'not_run',
                              'note':'Understanding did not finish; existing error is preserved.'}
        return
    assessment=score(result['output'],expected)
    result['evaluation']=assessment
    result['status']='pass' if assessment['coverage'] is True else 'fail' if assessment['coverage'] is False else 'review'
    result['summary']=('Covered' if assessment['coverage'] is True else 'Missed' if assessment['coverage'] is False else 'Needs label review') + '; ' + ', '.join(c['shape'] for c in result['output']['candidates'])


def fingerprint(expectations):
    return hashlib.sha256(json.dumps(expectations,sort_keys=True).encode()).hexdigest()
