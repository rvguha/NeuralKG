"""Paired deployed/local capability regression probes, with unmodified saved responses."""
import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx
import harness
import instance
import stage_reports


def questions():
    result=[('nih-scope','How much NIH research funding does St. Jude receive?')]
    seen={result[0][1]}
    for tab in harness.EXAMPLE_TABS:
        for entry in tab['queries']:
            question=entry if isinstance(entry,str) else entry['q']
            if question not in seen:result.append((tab['label'],question));seen.add(question)
    return result


def outcome(response):
    messages=response.get('messages',[])
    answers=[m['content'] for m in messages if m.get('message_type')=='nlws']
    errors=[m['content'] for m in messages if m.get('message_type')=='error']
    answer=answers[-1] if answers else None
    return {'status':('clarification' if answer.get('@type')=='ClarificationRequest' else 'answered') if answer else 'error',
            'answer':answer,'errors':errors or ([response['error']] if response.get('error') else [])}


async def main(args):
    if args.merge_retest:
        original=Path(args.baseline_run); retest=Path(args.merge_retest)
        rows={r['id']:r for r in json.loads((original/'results.json').read_text())}
        rows.update({r['id']:r for r in json.loads((retest/'results.json').read_text())})
        manifest=json.loads((original/'manifest.json').read_text())
        manifest.update(run_id=args.run_id,original_run=str(original),targeted_retest=str(retest),
                        scope='Full paired run with targeted clarification retests overlaid. Response availability and displayed-field retention, NOT verified semantic equivalence.')
        folder=stage_reports.results_root()/args.run_id
        stage_reports.publish(folder,manifest,sorted(rows.values(),key=lambda r:r['id']))
        print(folder/'execution.html');return
    rows=[]; folder=stage_reports.results_root()/args.run_id
    manifest={'run_id':args.run_id,'instance':str(instance.path()),'model':'openai/gpt-oss-120b',
              'scope':'Paired live compatibility probes. Both answered means response availability, NOT verified semantic equivalence.',
              'baseline':args.baseline,'current':args.current,'started':time.time()}
    todo=questions()[:args.limit] if args.limit else questions()
    sem=asyncio.Semaphore(args.workers)
    async with httpx.AsyncClient(timeout=240) as client:
        if not args.baseline_run:
            health=await client.get(args.baseline+'/healthz');health.raise_for_status()
        costs={} if args.baseline_run else (await client.get(args.baseline+'/costs')).json()
        allowed={'openai/gpt-oss-120b','openai/gpt-oss-20b','openai/text-embedding-3-small'}
        if set(costs.get('by_model',{}))-allowed:raise RuntimeError('Baseline reports a model outside the approved test set')
        async def ask(base,question):
            started=time.monotonic()
            try:
                r=await client.post(base+'/ask',json={'query':question,'streaming':False,'on_ambiguity':'ask'})
                r.raise_for_status();payload=r.json()
            except Exception as exc:payload={'error':type(exc).__name__+': '+str(exc)}
            return {'response':payload,'elapsed_seconds':round(time.monotonic()-started,2),**outcome(payload)}
        async def case(i,cohort,question):
            async with sem:
                saved=Path(args.baseline_run)/f'case-{i:03d}.json' if args.baseline_run else None
                prior=json.loads(saved.read_text()) if saved else None
                if prior and prior['question'] != question:raise ValueError('Baseline question mismatch')
                old=prior['baseline'] if prior else await ask(args.baseline,question)
                new=await ask(args.current,question)
                status='error' if old['status']=='answered' and new['status']=='error' else 'review'
                summary=f"baseline {old['status']} · current {new['status']}"
                if old['status']=='clarification' and new['status']!='clarification':
                    status='error';summary+=' · clarification behavior changed'
                old_data=(old.get('answer') or {}).get('data') or {}; new_data=(new.get('answer') or {}).get('data') or {}
                losses=[k for k in ('results','entity_groups','interpretations','ranking','series','coverage','ambiguity') if old_data.get(k) and not new_data.get(k)]
                if losses and old['status']==new['status']=='answered':
                    status='error';summary+=' · missing displayed data: '+', '.join(losses)
                ident=f'case-{i:03d}'
                stage_reports.atomic_json(folder/(ident+'.json'),{'question':question,'baseline':old,'current':new})
                row={'id':ident,'question':question,'cohort':cohort,'execution':{
                    'status':status,'summary':summary,'baseline':old,'current':new}}
                rows.append(row);rows.sort(key=lambda r:r['id'])
                stage_reports.publish(folder,manifest,rows)
                print(ident,summary,question,flush=True)
        await asyncio.gather(*(case(i,c,q) for i,(c,q) in enumerate(todo) if not args.cases or i in args.cases))
    manifest['finished']=time.time();stage_reports.publish(folder,manifest,rows)
    print(json.dumps({'total':len(rows),'availability_regressions':sum(r['execution']['status']=='error' for r in rows),'report':str(folder/'execution.html')}),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--baseline',default='https://rvguha-neuralkg.hf.space');p.add_argument('--current',default='http://127.0.0.1:8100')
    p.add_argument('--run-id',default='20260908-preserve-capabilities');p.add_argument('--limit',type=int);p.add_argument('--workers',type=int,default=3)
    p.add_argument('--baseline-run',help='Reuse saved deployed responses; only call the local server')
    p.add_argument('--cases',type=int,nargs='+',help='Original zero-based case indices to retest')
    p.add_argument('--merge-retest',help='Publish an overlay of this retest onto --baseline-run, without model calls')
    asyncio.run(main(p.parse_args()))
