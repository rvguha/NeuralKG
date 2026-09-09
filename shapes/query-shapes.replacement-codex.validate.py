"""Offline structural checks; deliberately no model calls or runtime catalog mutation."""
from pathlib import Path
import hashlib
import json
import yaml

class UniqueLoader(yaml.SafeLoader):
    pass

def unique_mapping(loader, node, deep=False):
    result = {}
    for k, v in node.value:
        key = loader.construct_object(k, deep=deep)
        assert key not in result, ('duplicate YAML key', key)
        result[key] = loader.construct_object(v, deep=deep)
    return result

UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)
root = Path(__file__).resolve().parent
raw = (root/'query-shapes.replacement-codex.yaml').read_bytes()
catalog = yaml.load(raw, Loader=UniqueLoader)
mapping = yaml.load((root/'query-shapes.replacement-codex.mapping.yaml').read_text(), Loader=UniqueLoader)
examples = json.loads((root/'query-shapes.replacement-codex.examples.json').read_text())
baseline = yaml.safe_load((root/'query-shapes.reduction-baseline.yaml').read_text())['shapes']
ids = {s['id'] for s in catalog['shapes']}
assert len(ids) == len(catalog['shapes']) == catalog['catalog']['entry_count'] == 76
digest = hashlib.sha256(raw).hexdigest()
assert digest == mapping['to_sha256'] == examples['catalog_sha256']
shapes = {s['id']:s for s in catalog['shapes']}
for s in shapes.values():
    assert all(s[k] for k in ('title','asks','examples','not','requires','returns','plan'))
    assert not (set(s['not'])-ids) and s['id'] not in s['not']
    assert not s['id'].startswith('legacy.')
    assert set(s['slots']) == set(s['plan']['parameter_names'])
    assert not any(v.get('type') == 'explicit typed binding' for v in s['slots'].values())
    seen=set()
    for n in s['plan']['nodes']:
        assert n['id'] not in seen and set(n['inputs']) <= seen
        seen.add(n['id'])
    assert s['plan']['output'] in seen
    live={s['plan']['output']}
    for n in reversed(s['plan']['nodes']):
        if n['id'] in live:
            live.update(n['inputs'])
    assert live == seen
assert {m['original_id'] for m in mapping['originals']} == {b['id'] for b in baseline}
assert len(mapping['originals']) == 98
for m in mapping['originals']:
    assert m['routes'] and all(r['pattern'] in ids and r['when'] for r in m['routes'])
assert len(examples['cases']) == 76
assert {c['expected']['shape'] for c in examples['cases']} == ids
for c in examples['cases']:
    s=shapes[c['expected']['shape']]
    assert c['question'] in s['examples'] and c['input_contract']
    assert c['expected']['output_grain'] == s['returns']['grain']
    assert c['expected']['operator_sequence'] == [n['operator'] for n in s['plan']['nodes']]
# Specific separation of the user's cross-source example from change-plus-level.
def reads(pid):
    return sum(n['operator']=='ReadRows' for n in shapes[pid]['plan']['nodes'])
assert reads('join.double-change') == 4
assert reads('join.change-and-level') == 3
assert 'OrderStatistics' in examples['cases'][list(shapes).index('reduce.quantiles')]['expected']['operator_sequence']
assert 'OrderStatistics' not in [n['operator'] for n in shapes['reduce.streaming']['plan']['nodes']]
print(json.dumps({'result':'PASS (structural only)', 'entries':len(ids),'mapped_originals':98,
                  'authored_plan_examples':76, 'portable_boundary_links':sum(len(s['not']) for s in shapes.values()),
                  'catalog_sha256':digest},indent=2))
