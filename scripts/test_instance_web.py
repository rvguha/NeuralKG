"""Run configured homepage examples through the production web endpoint.

Writes instance-local reports and complete traces. An answer is marked review,
never pass: successful HTTP/LLM calls are not semantic correctness checks.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
import instance
import stage_reports


def messages(text):
    for block in text.split('\n\n'):
        data = '\n'.join(line[6:] for line in block.splitlines() if line.startswith('data: '))
        if data:
            yield json.loads(data)


async def run(args):
    config = instance.config()
    rows = []
    ordinal = 0
    for section in config.get('examples', []):
        for query in section.get('queries', []):
            ordinal += 1
            question = query['q'] if isinstance(query, dict) else query
            if ordinal < args.start_at:
                continue
            if args.match and args.match.casefold() not in question.casefold():
                continue
            rows.append({'id': f'example-{ordinal:02}', 'question': question,
                         'cohort': section['label']})
    run_id = args.run_id or datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-web-examples')
    directory = stage_reports.results_root() / run_id
    rerun_ids = {f'example-{int(value):02}' for value in args.rerun_ids.split(',') if value.strip()}
    if args.resume and (directory / 'results.json').exists():
        previous={row['id']:row for row in json.loads((directory / 'results.json').read_text())}
        rows=[row if row['id'] in rerun_ids else {**row,**previous.get(row['id'],{})} for row in rows]
    manifest = {'run_id': run_id, 'instance': instance.path(),
                'model': config.get('query_understanding', {}).get('selection_model', 'configured'),
                'scope': 'Homepage examples through /ask. Answers require semantic review.',
                'endpoint': args.server, 'started_at': datetime.now(timezone.utc).isoformat()}
    stage_reports.publish(directory, manifest, rows)
    semaphore = asyncio.Semaphore(args.concurrency)
    async with httpx.AsyncClient(timeout=300) as client:
        async def one(row):
            async with semaphore:
                try:
                    response = await client.get(args.server.rstrip('/') + '/ask', params={
                        'query': row['question'], 'sse_format': 'named', 'debug': 'true'})
                    response.raise_for_status()
                    events = list(messages(response.text))
                    stage_reports.atomic_json(directory / (row['id'] + '-events.json'), events)
                    trace_url = next((e['content']['trace_url'] for e in events
                        if isinstance(e.get('content'), dict) and e['content'].get('trace_url')), None)
                    if not trace_url:
                        raise ValueError('The server did not publish a complete trace')
                    response = await client.get(args.server.rstrip('/') + trace_url)
                    response.raise_for_status()
                    trace = response.json()
                    stage_reports.atomic_json(directory / (row['id'] + '-trace.json'), trace)
                    for stage, key in [('understanding', 'understanding'), ('ard', 'resources'),
                                       ('planning', 'planning_attempts')]:
                        output = trace.get(key)
                        row[stage] = {'status': 'review' if output else 'blocked', 'output': output}
                    error = trace.get('error')
                    result = trace.get('result') or {}
                    branches=(result.get('data') or {}).get('interpretation_answers',[]) if isinstance(result.get('data'),dict) else []
                    if branches and not any(branch.get('result') for branch in branches):
                        error='No interpretation returned data: '+ '; '.join(str(branch.get('error','unavailable')) for branch in branches)
                    row['execution'] = {'status': 'error' if error else 'review',
                        'summary': error or 'Answer returned; not yet semantically reviewed',
                        'result': trace.get('result'), 'trace_url': trace_url,
                        'usage': trace.get('usage'), 'discovery_usage': trace.get('discovery_usage')}
                except Exception as exc:
                    row['execution'] = {'status': 'error', 'summary': str(exc)}
                stage_reports.publish(directory, manifest, rows)
                print(row['id'], row['execution']['status'], row['question'], flush=True)
        await asyncio.gather(*(one(row) for row in rows if not row.get('execution')))
    manifest['finished_at'] = datetime.now(timezone.utc).isoformat()
    stage_reports.publish(directory, manifest, rows)
    print(args.server.rstrip('/') + '/tests/' + run_id + '/execution.html', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--server', required=True)
    parser.add_argument('--run-id')
    parser.add_argument('--match')
    parser.add_argument('--start-at', type=int, default=1,
                        help='First one-based homepage example to run')
    parser.add_argument('--resume', action='store_true',
                        help='Keep completed rows from an interrupted run and execute the rest')
    parser.add_argument('--rerun-ids', default='',
                        help='With --resume, rerun comma-separated one-based example numbers')
    parser.add_argument('--concurrency', type=int, default=2)
    args = parser.parse_args()
    if args.concurrency < 1:
        parser.error('concurrency must be positive')
    os.environ['INSTANCE_CONFIG'] = str(Path(args.config).resolve())
    asyncio.run(run(args))
