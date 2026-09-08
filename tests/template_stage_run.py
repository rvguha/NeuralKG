"""Saved, resumable replacement-template understanding evaluation; no legacy fallback."""
import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import yaml
import instance
import llm
import stage_reports
import harness
import query_understanding
from query_context import QueryContext

MODEL = 'openai/gpt-oss-20b'


def corpus():
    examples = json.loads((ROOT/'shapes/query-shapes.replacement-codex.examples.json').read_text())['cases']
    mapping = yaml.safe_load((ROOT/'shapes/query-shapes.replacement-codex.mapping.yaml').read_text())
    routes = {r['original_id']: r['routes'] for r in mapping['originals']}
    rows = [dict(id=c['id'], question=c['question'], cohort='template-examples', expected=[c['expected']['shape']],
                 scoreable=True, expected_contract=c['expected'], input_contract=c['input_contract']) for c in examples]
    for cohort, filename in [('common','shape_common_queries.json'),('rest','shape_queries.json'),('negatives','shape_negatives.json')]:
        for i,c in enumerate(json.loads((ROOT/'tests'/filename).read_text())['cases']):
            if c.get('stage','classification') != 'classification':
                continue
            old = c.get('accepted', [c['gold']] if c.get('gold') else [])
            choices = [r for label in old for r in routes.get(label,[])]
            rows.append(dict(id=f'{cohort}.{i:03}', question=c['q'], cohort=cohort,
                expected=sorted({r['pattern'] for r in choices}), original_labels=old,
                conditional_routes=choices, scoreable=cohort!='negatives' and len(old)==1 and len(choices)==1))
    return rows


async def evaluate_case(row, context):
    output = await harness.query_understanding_async(row['question'], context=context)
    selected = [c['shape'] for c in output['candidates'] if c.get('status') == 'ok']
    agreement = bool(set(selected) & set(row['expected']))
    status = ('pass' if agreement else 'fail') if row['scoreable'] else 'review'
    if output['candidates'] and not selected:
        status = 'error'
    return dict(status=status, summary=', '.join(selected) or 'No usable candidates',
                output=output, expected=row['expected'], label_agreement=agreement,
                extraction_scored=False, usage=context.usage_ledger.snapshot())


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id')
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    assert llm.provider()=='openrouter'
    assert llm.chat_model()==MODEL, 'Configure the instance chat model as openai/gpt-oss-20b for this OSS evaluation'
    shapes, digest=query_understanding.load_catalog()
    run_id=args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-production-three-round'
    folder=stage_reports.results_root()/run_id
    rows=corpus()[:args.limit] if args.limit else corpus()
    manifest=dict(run_id=run_id,instance=instance.identity()['name'],instance_config=str(Path(instance.path()).resolve()),
        model=llm.chat_model(),catalog_sha256=digest,
        prompt_sha256=hashlib.sha256((Path(query_understanding.__file__).read_text()+digest).encode()).hexdigest(),
        scope=f'{len(rows)} questions through harness.query_understanding_async. Score is expected-template recall among up to three extracted candidates, NOT top-1 accuracy. Examples are included; authored examples are in-prompt checks, not holdout. Conditional migrations require review. Extraction is unscored.',
        reasoning='low',timeout_seconds=240,workers=args.workers,
        started=datetime.now(timezone.utc).isoformat(),completed=False)
    if (folder/'manifest.json').exists():
        previous=json.loads((folder/'manifest.json').read_text())
        assert previous['prompt_sha256']==manifest['prompt_sha256'],'Cannot resume a different prompt'
        manifest['started']=previous['started']
    for i,row in enumerate(rows):
        checkpoint=folder/'cases'/(row['id']+'.json')
        if checkpoint.exists(): rows[i]=json.loads(checkpoint.read_text())
    stage_reports.publish(folder,manifest,rows)
    stage_reports.atomic_json(folder/'prompt.json',{'selection':query_understanding.SELECT,'extraction':query_understanding.EXTRACT,'catalog':shapes})
    print(f'REPORT /tests/{run_id}/understanding.html; {len(rows)} cases; production harness',flush=True)
    semaphore=asyncio.Semaphore(args.workers); completed=sum('understanding' in r for r in rows)
    async def run(row):
        nonlocal completed
        if 'understanding' in row: return
        async with semaphore:
            start=time.monotonic()
            context=QueryContext.with_timeout(240, usage_ledger=llm.Ledger())
            try:
                row['understanding']=await evaluate_case(row, context)
            except Exception as exc:
                row['understanding']=dict(status='error',summary=type(exc).__name__+': '+str(exc)[:240],
                                          understanding_trace=getattr(exc,'trace',[]),usage=context.usage_ledger.snapshot())
            row['understanding']['seconds']=time.monotonic()-start
            row['ard']=dict(status='blocked',summary='OSS-only preflight: configured ARD embedding is openai/text-embedding-3-small. No search sent.',
                            input=row['understanding'].get('output'),requests_sent=0)
            row['planning']=dict(status='blocked',summary='No ARD output; replacement-template compiler not implemented.')
            row['execution']=dict(status='blocked',summary='No executable plan; no publisher requests sent.',requests_sent=0)
            stage_reports.atomic_json(folder/'cases'/(row['id']+'.json'),row)
            completed+=1
            if completed%10==0 or completed==len(rows):
                stage_reports.publish(folder,manifest,rows)
                print(f'{completed}/{len(rows)} saved',flush=True)
    try:
        await asyncio.gather(*(run(row) for row in rows))
        manifest['completed']=True
        manifest['finished']=datetime.now(timezone.utc).isoformat()
    finally:
        stage_reports.publish(folder,manifest,rows)
        await llm.close_async_client()
    print('SAVED '+str(folder),flush=True)


if __name__=='__main__': asyncio.run(main())
