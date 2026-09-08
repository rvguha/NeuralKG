"""Three-round, source-independent template understanding shared by serving and tests."""
import asyncio
import hashlib
import json
import os
from pathlib import Path

import yaml
import instance
import llm
import runtime


class UnderstandingError(runtime.Refused):
    def __init__(self, message, trace):
        super().__init__(message)
        self.trace = trace

SELECT = '''Select up to three plausible approaches to answering the QUESTION from the supplied shapes.
Use only their descriptions and examples. Negative examples show close but different requests under explicit input conditions; use their explanations to distinguish required operations, not as keyword exclusions. Different approaches can answer the same question depending on available APIs; the planner will choose later. Do not assume any source exists or reject an approach because its inputs may be unavailable.
Return JSON {"shapes": ["id", ...]} in preference order, with distinct supplied IDs only. Return [] for a non-data request or no suitable approach. Do not pad the list.'''
EXTRACT = '''Analyze the QUESTION independently for this one full shape entry. Extract all bindings and details needed by its declared slots and plan, without executing anything or assuming source capabilities.
Return JSON with "bindings" (object keyed only by declared slot names), "entities" (list of objects with string mention, description, type and a potential_matches list), "measures" (list), "periods" (list), "missing" (list of {"slot": "declared slot name", "reason": "what is unknown"}), "acquisition_queries" (list of natural-language request strings for the required inputs), "applicability" ("plausible" or "inapplicable"), and "reason" (string).
Retain entity descriptions and possible identities for later crosswalk. Do not resolve identifiers from memory. Leave unspecified periods unspecified; 'all dollars' does not mean 'all time'. Do not invent statistics or slot bindings. An unknown source capability is a requirement for later planning, not grounds to declare the shape inapplicable. Do not choose between this approach and other shapes.'''


def load_catalog():
    configured = instance.config().get('query_understanding', {}).get('catalog')
    path = Path(configured) if configured else Path(__file__).parent / 'shapes/query-shapes.replacement-codex.yaml'
    if not path.is_absolute():
        path = Path(instance.path()).resolve().parent / path
    raw = path.read_bytes()
    shapes = yaml.safe_load(raw)['shapes']
    ids = [s['id'] for s in shapes]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Catalog must contain distinct shape IDs')
    for s in shapes:
        if not isinstance(s.get('asks'), str) or not isinstance(s.get('examples'), list) or not s['examples']:
            raise ValueError('Every shape needs a description and examples')
    return shapes, hashlib.sha256(raw).hexdigest()


def brief(shape):
    return {key: shape[key] for key in ('id', 'asks', 'examples', 'negative_examples') if key in shape}


async def parallel(calls):
    tasks = [asyncio.create_task(call) for call in calls]
    try:
        return await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done(): task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def understand(question, *, context):
    if not isinstance(question, str) or not question.strip():
        raise runtime.Refused('A nonempty question is required')
    shapes, digest = load_catalog()
    by_id = {s['id']: s for s in shapes}
    trace = []

    async def call(system, payload, stage, validate):
        user = json.dumps({'question': question, **payload}, ensure_ascii=False)
        record = {'stage': stage, 'system': system, 'user': user, 'attempts': []}
        trace.append(record)
        for attempt in range(2):
            try:
                raw = await llm.chat_async(system, user, context=context, json_mode=True,
                                          model=(os.getenv('QUERY_SELECTION_MODEL') or instance.config().get('query_understanding', {}).get('selection_model')) if stage in ('understand-batch','understand-shortlist') else None,
                                          stage=stage, max_tokens=4096 if stage=='understand-extract' else 1200,
                                          reasoning_effort='low')
            except (runtime.QueryCancelled, runtime.QueryBudgetExceeded):
                raise
            except Exception as exc:
                record['error'] = type(exc).__name__ + ': ' + str(exc)[:200]
                raise UnderstandingError(f'{stage}: {record["error"]}', trace) from exc
            record['attempts'].append(raw)
            try:
                result = json.loads(raw)
                validate(result)
                return result
            except (ValueError, TypeError, KeyError) as exc:
                if attempt:
                    raise UnderstandingError(f'{stage} returned invalid structured output: {exc}', trace) from exc

    async def select(entries, stage):
        allowed = {s['id'] for s in entries}
        def validate(result):
            selected = result.get('shapes') if isinstance(result,dict) else None
            if not isinstance(selected,list) or len(selected)>3 or any(not isinstance(x,str) or x not in allowed for x in selected) or len(set(selected))!=len(selected):
                raise ValueError('Expected at most three distinct supplied IDs')
        result = await call(SELECT, {'shapes':[brief(s) for s in entries]}, stage, validate)
        return result['shapes']

    batches = [shapes[i:i+30] for i in range(0,len(shapes),30)]
    context.budget.consume_fanout(len(batches))
    picks = await parallel([select(batch,'understand-batch') for batch in batches])
    shortlist = list(dict.fromkeys(s for batch in picks for s in batch))
    finalists = await select([by_id[s] for s in shortlist],'understand-shortlist') if shortlist else []

    async def extract(identifier):
        shape = by_id[identifier]
        def validate(result):
            if not isinstance(result,dict) or not isinstance(result.get('bindings'),dict):
                raise ValueError('Expected binding object')
            if set(result['bindings']) - set(shape['slots']):
                raise ValueError('Undeclared slot binding')
            for key in ('entities','measures','periods','missing','acquisition_queries'):
                if not isinstance(result.get(key),list): raise ValueError('Expected list: '+key)
            for entity in result['entities']:
                if not isinstance(entity,dict) or not all(isinstance(entity.get(k),str) for k in ('mention','description','type')) or not isinstance(entity.get('potential_matches'),list):
                    raise ValueError('Invalid entity description')
            for missing in result['missing']:
                if not isinstance(missing,dict) or missing.get('slot') not in shape['slots'] or not isinstance(missing.get('reason'),str):
                    raise ValueError('Missing bindings must name a declared slot and reason')
            if not all(isinstance(q,str) and q.strip() for q in result['acquisition_queries']):
                raise ValueError('Acquisition queries must be nonempty strings')
            if result.get('applicability') not in ('plausible','inapplicable') or not isinstance(result.get('reason'),str):
                raise ValueError('Invalid applicability/reason')
        try:
            output = await call(EXTRACT, {'shape':shape}, 'understand-extract', validate)
            fields = ('bindings','entities','measures','periods','missing','acquisition_queries','applicability','reason')
            return {'shape':identifier, 'status':'ok', **{k:output[k] for k in fields}, 'requirements':shape.get('requires',{}), 'plan_template':shape['plan']}
        except runtime.QueryCancelled:
            raise
        except runtime.QueryBudgetExceeded:
            raise
        except Exception as exc:
            return {'shape':identifier,'status':'error','error':str(exc)}

    context.budget.consume_fanout(len(finalists))
    candidates = await parallel([extract(s) for s in finalists])
    return {'question':question, 'candidates':candidates, 'catalog_sha256':digest,
            'shortlist':shortlist, 'finalists':finalists, 'understanding_trace':trace}
