"""Rescore saved production outputs without any LLM calls or changed predictions."""
import argparse
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import stage_reports
from template_stage_run import corpus
from template_expectations import expectation, apply, fingerprint


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('run_id')
    args=parser.parse_args()
    # Build expectations from the fixtures before opening model results.
    expectations={r['id']:expectation(r) for r in corpus()}
    root=stage_reports.results_root()
    source=(root/args.run_id).resolve()
    if source.parent!=root.resolve(): raise ValueError('Expected a run ID, not a path')
    manifest=json.loads((source/'manifest.json').read_text())
    rows=json.loads((source/'results.json').read_text())
    new_id=args.run_id+'-multi-expected'
    target=root/new_id
    if target.exists(): raise ValueError('Rescored run already exists; preserve it')
    manifest=copy.deepcopy(manifest)
    manifest.update(run_id=new_id,source_run=args.run_id,expectations_sha256=fingerprint(expectations),
        scope='Offline rescore of saved production outputs; no new LLM calls. Multiple acceptable approaches have explicit API conditions. Coverage measures template selection only; precision is bounded when labels are incomplete. Binding checks are separate and partial. Unlisted templates are NOT automatically wrong.')
    for row in rows:
        apply(row,expectations[row['id']])
        stage_reports.atomic_json(target/'cases'/(row['id']+'.json'),row)
    stage_reports.atomic_json(target/'expectations.json',expectations)
    stage_reports.publish(target,manifest,rows)
    print(target,flush=True)


if __name__=='__main__': main()
