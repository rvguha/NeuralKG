"""Instance-local, read-only publication of staged evaluation artifacts."""
import hashlib
import html
import json
import os
from pathlib import Path

import instance

STAGES = ('understanding', 'ard', 'planning', 'execution')


def results_root():
    explicit = os.getenv('TEST_RESULTS_DIR') or instance.config().get('tests', {}).get('results_dir')
    config = Path(instance.path()).resolve()
    if explicit:
        p = Path(explicit)
        return (p if p.is_absolute() else config.parent / p).resolve()
    key = hashlib.sha256(str(config).encode()).hexdigest()[:12]
    return config.parent / '.local' / 'test-runs' / key


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def shell(title, body):
    return '''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>''' + html.escape(title) + '''</title><style>
body{font:15px system-ui;margin:32px auto;padding:0 24px;max-width:1500px;color:#172638;background:#f5f7fb}
a{color:#135ab1}nav{display:flex;gap:20px;flex-wrap:wrap;margin:24px 0}h1{font-size:28px;overflow-wrap:anywhere}
table{display:block;overflow-x:auto;border-collapse:collapse;width:100%;background:white}td,th{text-align:left;border-bottom:1px solid #dde4ed;padding:12px;vertical-align:top}
th{position:sticky;top:0;background:#e9eff8}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-width:950px;font-size:12px}
.pass{color:#176a3b}.fail,.error{color:#b02727}.blocked,.review{color:#845300}summary{cursor:pointer}input{padding:10px;width:60%;margin:15px 0}
</style><body>''' + body + '</body></html>'


def publish(run_dir, manifest, rows):
    run_dir = Path(run_dir)
    atomic_json(run_dir / 'manifest.json', manifest)
    atomic_json(run_dir / 'results.json', rows)
    esc = html.escape
    nav = '<nav><a href="/tests/">All runs</a>' + ''.join(
        f'<a href="{s}.html">{s.title()}</a>' for s in STAGES) + '<a href="results.json">Raw JSON</a></nav>'
    for stage in STAGES:
        counts = {}
        records = []
        for row in rows:
            result = row.get(stage, {'status': 'pending'})
            status = result.get('status', 'pending')
            counts[status] = counts.get(status, 0) + 1
            detail = esc(json.dumps(result, ensure_ascii=False, indent=2))
            assessment = result.get('evaluation')
            extra = ''
            if stage == 'understanding' and assessment:
                bounds = assessment.get('precision_bounds')
                precision = assessment.get('candidate_precision')
                ptext = f'{precision:.0%}' if precision is not None else (f'{bounds[0]:.0%}–{bounds[1]:.0%} (unreviewed candidates)' if bounds else 'not scored')
                coverage = assessment.get('coverage')
                ctext = 'covered' if coverage is True else 'missed' if coverage is False else 'unknown'
                expectation_text = esc(json.dumps(row.get('expectations', {}),ensure_ascii=False,indent=2))
                extra = f'<p>Coverage: {ctext} · Candidate precision: {esc(ptext)}</p><p>Extraction errors: {assessment.get("extraction_errors", "—")} · Known binding failures: {assessment.get("binding_failures", "—")} (not full binding validation)</p><details><summary>Expected approaches and conditions</summary><pre>{expectation_text}</pre></details>'
            records.append(f'<tr><td>{esc(row["id"])}</td><td>{esc(row["question"])}</td>'
                           f'<td class="{esc(status)}">{esc(status)}</td>'
                           f'<td>{extra}<details><summary>{esc(result.get("summary", status))}</summary><pre>{detail}</pre></details></td></tr>')
        body = f'<h1>{stage.title()} · {esc(manifest["run_id"])}</h1>{nav}'
        body += f'<p>Instance: {esc(manifest["instance"])} · Model: {esc(manifest["model"])}</p>'
        body += '<p>' + esc(manifest['scope']) + '</p><p>' + esc(str(counts)) + '</p>'
        cohorts = {}
        for row in rows:
            tally = cohorts.setdefault(row.get('cohort', 'all'), {})
            state = row.get(stage, {}).get('status', 'pending')
            tally[state] = tally.get(state, 0) + 1
        states = ('pass', 'fail', 'error', 'review', 'blocked', 'pending')
        body += '<table><tr><th>Cohort</th>' + ''.join('<th>'+s.title()+'</th>' for s in states) + '</tr>'
        body += ''.join('<tr><td>'+esc(name)+'</td>'+''.join('<td>'+str(tally.get(s,0))+'</td>' for s in states)+'</tr>' for name,tally in cohorts.items())+'</table>'
        body += '<p>Pass describes template selection only, not verified bindings or a data answer. For multi-expected runs, an unlisted candidate remains unreviewed; precision bounds expose incomplete labels. Extraction and binding checks are separate. Blocked is not a completed stage. Refresh to see saved progress.</p>'
        body += '<input id="filter" placeholder="Filter by question, status, or output" aria-label="Filter results">'
        body += '<table><thead><tr><th>Case</th><th>Question</th><th>Status</th><th>Saved output / explanation</th></tr></thead><tbody>' + ''.join(records) + '</tbody></table>'
        body += '<script>document.getElementById("filter").oninput=function(){for(const r of document.querySelectorAll("tbody tr"))r.hidden=!r.textContent.toLowerCase().includes(this.value.toLowerCase())}</script>'
        target = run_dir / (stage + '.html')
        temp = target.with_suffix('.tmp')
        temp.write_text(shell(stage.title(), body), encoding='utf-8')
        temp.replace(target)


async def serve(request):
    from starlette.responses import HTMLResponse, FileResponse, JSONResponse
    root = results_root().resolve()
    relative = request.path_params.get('artifact', '').strip('/')
    if not relative:
        links = []
        if root.exists():
            for run in sorted(root.iterdir(), reverse=True):
                if run.is_dir() and (run / 'understanding.html').is_file() and run.resolve().is_relative_to(root):
                    links.append(f'<li><a href="/tests/{html.escape(run.name)}/understanding.html">{html.escape(run.name)}</a></li>')
        return HTMLResponse(shell('Instance test runs', '<h1>Instance test runs</h1><p>Saved outputs, separated by stage.</p><ul>' + ''.join(links) + '</ul>'))
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or target.suffix not in ('.html', '.json') or not target.is_file():
        return JSONResponse({'error': 'not found'}, 404)
    return FileResponse(target, headers={'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
