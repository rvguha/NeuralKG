"""Enrich mechanically imported table schemas with callable accessor metadata."""
import json
from pathlib import Path
import re
import yaml

ROOT = Path(__file__).resolve().parents[1]


def prepare():
    allowed = []
    for path in sorted((ROOT / 'instances/atlas/catalog/bigquery').glob('*/*.md')):
        parts = path.read_text().split('---', 2)
        if len(parts) != 3:
            continue
        fm = yaml.safe_load(parts[1])
        source = fm.get('source')
        if not isinstance(source, dict) or source.get('kind') != 'bigquery':
            continue
        table = '.'.join(source[k] for k in ('project', 'dataset', 'table'))
        columns = [{'name': name, 'type': kind} for name, kind in
                   re.findall(r'^- `([^`]+)` \((.+)\)$', parts[2], re.M)]
        if not columns:
            continue
        fm.update(accessor='bigquery_table', columns=columns)
        path.write_text('---\n' + yaml.safe_dump(fm, sort_keys=False, allow_unicode=True) + '---' + parts[2])
        allowed.append(table)
    target = ROOT / 'instances/atlas/allowed-tables.json'
    target.write_text(json.dumps(allowed, indent=2) + '\n')
    print(f'{len(allowed)} table descriptors prepared')


if __name__ == '__main__':
    prepare()
