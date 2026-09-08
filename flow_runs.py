"""Instance-local production traces, readable through the existing report server."""
import json
import time
from pathlib import Path

import stage_reports


def save(context, question, events, result=None, error=None):
    run_id = 'flow-' + context.trace_id
    folder = stage_reports.results_root() / run_id
    data = {'query': question, 'started_at': context.memo.get('flow_started'),
            'finished_at': time.time(), 'events': events,
            'understanding': context.memo.get('understanding'),
            'resources': context.memo.get('resources'),
            'planning_attempts': context.memo.get('planning_attempts', []),
            'compatibility_attempts': context.memo.get('compatibility_attempts', []),
            'compatibility': context.memo.get('compatibility'),
            'result': result, 'error': error,
            'usage': context.usage_ledger.snapshot() if context.usage_ledger else None,
            'discovery_usage': context.discovery_ledger.snapshot() if context.discovery_ledger else None}
    stage_reports.atomic_json(folder / 'trace.json', data)
    # A normal HTML link works without a client-side route or browser storage.
    import html
    body = '<h1>' + html.escape(question) + '</h1><p><a href="/">Back to chat</a> · <a href="trace.json">Exact JSON trace</a></p><pre>' + html.escape(json.dumps(data, indent=2, ensure_ascii=False)) + '</pre>'
    (folder / 'execution.html').write_text(stage_reports.shell('Query trace', body), encoding='utf-8')
    return '/tests/' + run_id + '/trace.json'


def recent():
    root = stage_reports.results_root()
    records = []
    for path in sorted(root.glob('flow-*/trace.json'), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
        try:
            data = json.loads(path.read_text())
            records.append({'id': path.parent.name, 'query': data['query'],
                            'error': data.get('error'), 'finished_at': data.get('finished_at')})
        except (OSError, ValueError, KeyError):
            continue
    return records
