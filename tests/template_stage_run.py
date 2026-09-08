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

MODEL = 'openai/gpt-oss-20b'
SYSTEM = '''Understand the question using the supplied logical query-template catalog, independently of source availability.
Return JSON with: template (catalog ID or null), entities (list of names/descriptions), measures (list), periods (list), bindings (object), missing (list of necessary clarifications), acquisition_queries (list of source-independent requests for input operands), reason (short).
Bind only information justified by the question. Do not invent periods, identities, statistics, parameters or sources. A factual request has a template even when data may be unavailable. Missing parameters do not make the template null. Use null for non-data requests (writing, translation, conversation) or genuinely unsupported computation. Do not copy schema placeholders into values. Select the logical execution structure, not a legacy category. Do not execute the question.'''


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


def validate(output, ids):
    if not isinstance(output,dict) or 'template' not in output or output['template'] not in ids|{None}:
        raise ValueError('Invalid template')
    for field in ('entities','measures','periods','missing','acquisition_queries'):
        if not isinstance(output.get(field),list):
            raise ValueError('Invalid '+field)
    if not isinstance(output.get('bindings'),dict):
        raise ValueError('Invalid bindings')


async def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run-id')
    parser.add_argument('--workers',type=int,default=6)
    parser.add_argument('--limit',type=int)
    args=parser.parse_args()
    assert llm.provider()=='openrouter'
    raw=(ROOT/'shapes/query-shapes.replacement-codex.yaml').read_bytes()
    shapes=yaml.safe_load(raw)['shapes']; ids={s['id'] for s in shapes}
    catalog=json.dumps([{k:s[k] for k in ('id','asks','slots','requires','returns','plan','not')} for s in shapes],separators=(',',':'))
    run_id=args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-templates76-20b'
    folder=stage_reports.results_root()/run_id
    rows=corpus()[:args.limit] if args.limit else corpus()
    manifest=dict(run_id=run_id,instance=instance.identity()['name'],instance_config=str(Path(instance.path()).resolve()),
        model=MODEL,catalog_sha256=hashlib.sha256(raw).hexdigest(),
        prompt_sha256=hashlib.sha256((SYSTEM+catalog).encode()).hexdigest(),
        scope=f'{len(rows)} questions; all 76 authored template examples plus common/rest/negative corpora. Conditional migrations and disputed negatives require review. Extraction is unscored.',
        reasoning='low',max_tokens=4096,timeout_seconds=60,workers=args.workers,
        started=datetime.now(timezone.utc).isoformat(),completed=False)
    if (folder/'manifest.json').exists():
        previous=json.loads((folder/'manifest.json').read_text())
        assert previous['prompt_sha256']==manifest['prompt_sha256'],'Cannot resume a different prompt'
        manifest['started']=previous['started']
    for i,row in enumerate(rows):
        checkpoint=folder/'cases'/(row['id']+'.json')
        if checkpoint.exists(): rows[i]=json.loads(checkpoint.read_text())
    stage_reports.publish(folder,manifest,rows)
    stage_reports.atomic_json(folder/'prompt.json',{'system':SYSTEM,'catalog':json.loads(catalog)})
    print(f'REPORT /tests/{run_id}/understanding.html; {len(rows)} cases; {len(catalog)} prompt chars',flush=True)
    client=llm.async_client().with_options(max_retries=0,timeout=45)
    semaphore=asyncio.Semaphore(args.workers); completed=sum('understanding' in r for r in rows)
    async def run(row):
        nonlocal completed
        if 'understanding' in row: return
        async with semaphore:
            start=time.monotonic(); attempts=[]
            for attempt in range(2):
                try:
                    response=await asyncio.wait_for(client.chat.completions.create(model=MODEL,temperature=0,max_tokens=4096,
                        response_format={'type':'json_object'},messages=[{'role':'system','content':SYSTEM},
                        {'role':'user','content':'CATALOG:\n'+catalog+'\nQUESTION: '+row['question']}],
                        extra_body={'reasoning':{'effort':'low'},'provider':{'sort':'throughput'}}),60)
                    content=response.choices[0].message.content
                    attempts.append(dict(response_model=response.model,content=content,usage=response.usage.model_dump(),finish_reason=response.choices[0].finish_reason))
                    if not content: raise ValueError('Empty completion')
                    output=json.loads(content); validate(output,ids)
                    agreement=output['template'] in row['expected']
                    status=('pass' if agreement else 'fail') if row['scoreable'] else 'review'
                    row['understanding']=dict(status=status,summary=str(output['template'])+('; clarification requested' if output['missing'] else ''),
                        output=output,expected=row['expected'],label_agreement=agreement,extraction_scored=False,seconds=time.monotonic()-start,attempts=attempts)
                    break
                except Exception as exc:
                    attempts.append(dict(error=type(exc).__name__,detail=str(exc)[:160]))
            else:
                row['understanding']=dict(status='error',summary='No valid structured output after two attempts',attempts=attempts,seconds=time.monotonic()-start)
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
        await client.close()
    print('SAVED '+str(folder),flush=True)


if __name__=='__main__': asyncio.run(main())
