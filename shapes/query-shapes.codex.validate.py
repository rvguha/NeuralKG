"""Read-only structural and declared-boundary checks for the independent proposal."""
from pathlib import Path
import hashlib
import json
import yaml

root = Path(__file__).resolve().parent
raw = (root / 'query-shapes.reduction-baseline.yaml').read_bytes()
base = yaml.safe_load(raw)['shapes']
doc = yaml.safe_load((root / 'query-shapes.codex.yaml').read_text())
assert hashlib.sha256(raw).hexdigest() == doc['proposal']['baseline_sha256']
patterns = {p['id']: p for p in doc['patterns']}
assert len(patterns) == len(doc['patterns']) == doc['proposal']['reduced_count']
mapping = {m['original_id']: m for m in doc['baseline_mapping']}
assert len(mapping) == len(doc['baseline_mapping']) == len(base) == 98
assert set(mapping) == {s['id'] for s in base}
used = set()
signatures = set()
for p in patterns.values():
    seen = set()
    for n in p['plan']:
        assert n['id'] not in seen
        assert set(n['inputs']) <= seen
        seen.add(n['id'])
    assert p['output'] in seen
    live = {p['output']}
    for n in reversed(p['plan']):
        if n['id'] in live:
            live.update(n['inputs'])
    assert live == seen, ('dead stage', p['id'])
    assert set(p['parameters']) <= set(doc['parameter_contracts'])
    sig = tuple((n['operator'], tuple(n['inputs'])) for n in p['plan'])
    assert sig not in signatures, ('duplicate signature', p['id'])
    signatures.add(sig)
for s in base:
    m = mapping[s['id']]
    assert m['original_asks'] == s['asks']
    assert m['original_examples'] == s.get('examples', [])
    assert m['routes']
    for r in m['routes']:
        assert r['pattern'] in patterns and r['when']
        used.add(r['pattern'])
assert used == set(patterns)

def routes(old):
    return {r['pattern'] for r in mapping[old]['routes']}

# Explicit expectations, not LLM predictions or empirical equivalence proofs.
merge_cases = [
    ('point.value', 'point.status', 'lookup.scalar'),
    ('point.ratio', 'change.percent', 'lookup.binary'),
    ('comparison.difference', 'source.agreement', 'lookup.binary'),
    ('rank.top-n', 'rank.extremum-single', 'rank.population'),
    ('aggregate.sum', 'aggregate.mean', 'reduce.streaming'),
    ('aggregate.median', 'aggregate.percentile-value', 'reduce.quantiles'),
    ('filter.negation-open', 'set.difference', 'join.membership'),
    ('multi.change-conjunction-all', 'multi.independent-windows', 'join.double-change'),
    ('multi.threshold-then-rank', 'multi.constrained-optimum', 'join.filter-rank'),
    ('temporal.as-of', 'temporal.nearest', 'temporal.match'),
]
for a, b, target in merge_cases:
    assert target in routes(a) & routes(b), (a, b, target)
boundary_cases = [
    ('lookup.scalar', 'lookup.event-bound', 'ResolveDate'),
    ('lookup.binary', 'reduce.entity-share', 'StreamReduce'),
    ('reduce.streaming', 'reduce.quantiles', 'OrderStatistics'),
    ('reduce.streaming', 'reduce.distinct', 'Distinct'),
    ('reduce.grouped', 'reduce.grouped-distinct', 'Distinct'),
    ('compare.winner', 'compare.derived-winner', 'MapReadScalarPairs'),
    ('records.select', 'set.union', 'Union'),
    ('join.membership', 'set.symmetric-difference', 'Union'),
    ('join.enrich', 'join.grouped-measures', 'GroupStreamReduce'),
    ('join.paired-reduce', 'assoc.population-ranks', 'RankColumns'),
]
for a, b, extra in boundary_cases:
    ops_a = {n['operator'] for n in patterns[a]['plan']}
    ops_b = {n['operator'] for n in patterns[b]['plan']}
    assert extra in ops_b - ops_a, (a, b, extra)
print(json.dumps({'baseline': len(base), 'reduced': len(patterns),
                  'mapped_originals': len(mapping), 'unique_signatures': len(signatures),
                  'merge_checks': len(merge_cases), 'boundary_checks': len(boundary_cases),
                  'result': 'pass; structural checks only'}, indent=2))
