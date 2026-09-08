"""Replay fixed saved understanding outputs through production discovery."""
import asyncio
import contextvars
import copy
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ard_client
import harness
import llm
import stage_reports
from query_context import QueryContext

saved_output = contextvars.ContextVar('saved_understanding')


async def replay(question, *, context):
    output = saved_output.get()
    assert output['question'] == question
    return copy.deepcopy(output)


async def main():
    source_id, run_id = sys.argv[1:3]
    root = stage_reports.results_root()
    assert all((root / name).resolve().parent == root.resolve() for name in (source_id, run_id))
    source = root / source_id
    folder = root / run_id
    rows = json.loads((source / 'results.json').read_text())
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest.update(run_id=run_id, source_run=source_id, completed=False,
                    finder_url=ard_client.BASE,
                    scope='Saved 120b understanding replayed through harness.discover_async. ARD uses text-embedding-3-small and OSS-120b reranking. Returned hits are unverified retrieval candidates, NOT answer correctness; planning and execution not run.')
    for row in rows:
        checkpoint = folder / 'cases' / (row['id'] + '.json')
        if checkpoint.exists():
            row.update(json.loads(checkpoint.read_text()))
        else:
            row['ard'] = {'status': 'pending', 'summary': 'Awaiting discovery'}
        row['planning'] = {'status': 'blocked', 'summary': 'Candidate-aware compiler not implemented; no planning test attempted.'}
        row['execution'] = {'status': 'blocked', 'summary': 'No executable plan; no publisher requests sent.'}
    stage_reports.publish(folder, manifest, rows)
    semaphore = asyncio.Semaphore(12)
    completed = 0
    async with ard_client.create_async_http_client() as http:
        async def run(row):
            nonlocal completed
            async with semaphore:
                if row['ard']['status'] != 'pending':
                    completed += 1
                    return
                output = row['understanding'].get('output')
                start = time.monotonic()
                if not output:
                    row['ard'] = {'status': 'blocked', 'summary': 'No understanding output; no discovery request sent.'}
                else:
                    ledger = ard_client.DiscoveryUsage()
                    context = QueryContext.with_timeout(240, http_client=http, discovery_ledger=ledger, usage_ledger=llm.Ledger())
                    token = saved_output.set(output)
                    queries = list(dict.fromkeys(q for c in output['candidates'] if c.get('status') == 'ok' for q in c.get('acquisition_queries', [])))
                    try:
                        _, hits = await harness.discover_async(row['question'], context=context)
                        row['ard'] = {'status': 'review' if queries else 'blocked',
                                      'summary': f'{len(hits)} candidate resources returned; relevance and input coverage unverified' if queries else 'No acquisition queries from understanding; no discovery request sent.',
                                      'queries': queries, 'output': hits, 'usage': ledger.snapshot()}
                    except Exception as exc:
                        row['ard'] = {'status': 'error', 'summary': type(exc).__name__ + ': ' + str(exc), 'queries': queries, 'usage': ledger.snapshot()}
                    finally:
                        saved_output.reset(token)
                row['ard']['seconds'] = time.monotonic() - start
                stage_reports.atomic_json(folder / 'cases' / (row['id'] + '.json'), row)
                completed += 1
                if completed % 10 == 0 or completed == len(rows):
                    stage_reports.publish(folder, manifest, rows)
                    print(f'{completed}/{len(rows)} saved', flush=True)
        with patch.object(harness, 'query_understanding_async', replay):
            await asyncio.gather(*(run(row) for row in rows))
    manifest['completed'] = True
    stage_reports.publish(folder, manifest, rows)
    print(folder, flush=True)


if __name__ == '__main__':
    asyncio.run(main())
