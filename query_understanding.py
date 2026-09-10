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
Retain materially different acquisition plans, not merely different phrasings. A requested aggregate may be available as a directly published scalar, derivable from a small number of published scalars, or computable from complete rows; include each supplied shape that represents one of those legitimate routes. For example, "How many people in Texas have diabetes?" can use lookup.scalar when a source publishes that count, lookup.binary when population and diabetes prevalence are published separately, or reduce.streaming when a source enumerates the relevant people. Source availability is resolved only after this step.
Return JSON {"shapes": ["id", ...]} in preference order, with distinct supplied IDs only. Return [] for a non-data request or no suitable approach. Do not pad the list.'''
EXTRACT = '''Analyze the QUESTION independently for this one full shape entry. Extract all bindings and details needed by its declared slots and plan, without executing anything or assuming source capabilities.
Return JSON with "bindings" (object keyed only by declared slot names), "entities" (list of objects with string mention, description, type and a potential_matches list), "measures" (list), "periods" (list), "missing" (list of {"slot": "declared slot name", "reason": "what is unknown"}), "acquisition_queries" (list of natural-language request strings for the required inputs), "applicability" ("plausible" or "inapplicable"), and "reason" (string).
Distinguish missing parameters from missing data. "missing" records unknown slot bindings or clarification needs; "acquisition_queries" requests the external data required by the plan, even when every parameter is known. Knowing an entity, measure and period does NOT supply its value. For example, "What was AMD's total revenue in 2023?" still requires an acquisition request for AMD total revenue in 2023 even if missing is [].
For a plausible plan containing external reads or discovery, acquisition_queries must be nonempty and describe every required input in natural language. Do not use this list to ask the user for internal slot names, arithmetic expressions, dataset identifiers, or already-specified parameters. Unknown parameters can remain explicitly unspecified in the data requests; do not invent them. Do not assume data has already been fetched. Inapplicable approaches may have an empty list.
When the selected shape is an algebraically equivalent way to produce the requested output, mark it plausible even if the question does not spell out that computation; the planner must later prove compatible definitions, units, period and scope from actual sources. For example, lookup.binary is a plausible route to "How many people in Texas have diabetes?" using separately published population and diabetes prevalence, while lookup.scalar is the direct-published-count route and reduce.streaming is the complete-row route. Do not discard one of these API-dependent approaches merely because another would be simpler if its input existed.
Retain entity descriptions and possible identities for later crosswalk. Do not resolve identifiers from memory. Leave unspecified periods unspecified; 'all dollars' does not mean 'all time'. Do not invent statistics or slot bindings. An unknown source capability is a requirement for later planning, not grounds to declare the shape inapplicable. Do not choose between this approach and other shapes.'''
EXTRACT += '''
Also return interpretations: a list of {entity: string, attribute: string, description: string}.
Use this when the question plausibly refers to different real entities or different attributes; these will ALL be answered using ordinary data calls, not presented as a clarification question. For "How big is Microsoft?", give separate attributes revenue, total assets, market capitalization and number of employees for Microsoft Corporation. For an unqualified place name retain plausible city/county identities with fully qualified names; San Francisco city and county can be coextensive, so do not duplicate the same identity. For a club versus its separately registered foundation, or a university versus a separately registered board/trust, retain genuinely plausible distinct identities, not aliases. Do not invent identifiers or organizations. Do not include a related entity excluded by explicit wording. Do not treat an explicit comparison of named entities, arithmetic operands, required input measures, or alternative execution templates as ambiguous interpretations: those stay one question. If both entity and attribute vary, include the meaningful combinations. Each entry pins only entity/attribute; all other original constraints remain unchanged. Return [] when there is only one interpretation. Descriptions explain the distinction. Do not assume source availability and do not ask the user to choose.'''
EXTRACT += '''
Entity scope examples: "Obesity rate in Miami" must retain City of Miami, Florida and Miami-Dade County, Florida as separately labeled interpretations, rather than leaving both entities as "Miami". "Is the Sierra Club a 501(c)(3)?" retains Sierra Club and Sierra Club Foundation as distinct organization interpretations, with the SAME 501(c)(3) predicate. The bare organization's familiar name is not itself an explicit exclusion of its foundation. Wording such as "the city, not the county", "Sierra Club itself, excluding the foundation", or an explicit canonical identifier DOES exclude other identities. Prioritize these entity distinctions rather than manufacturing different measures while leaving an ambiguous entity unresolved. Keep the full specific measure wording, not generic eligibility or size.'''


EXTRACT += '\nUse reference_date as today for relative periods. Never substitute a guessed latest dataset year for today. Preserve requested date boundaries even when recent data may be unavailable. For year-grain "last N years", use reference year minus N through the reference year; use the previous N completed years only when complete years are requested. State the interpreted boundaries.'
EXTRACT += '\nDistinguish a published statistic from an operation on observations: a trend of median income requests yearly median income values, not the median across years. A trend of a rate likewise does not request a new rate calculation across those observations. Mark a shape inapplicable when it changes the requested output in this way.'
EXTRACT += '\nEvery acquisition query must preserve the required population and grain: a ranking of counties within a state needs values for ALL counties within that state, not the value for the state itself. A comparison of named entities must retain every name. Include the requested measure and temporal scope in each independently discoverable input request.'
SELECT += '\nAlways retain the simplest supplied approach that can produce the requested output. For a ranking of one reported measure across a population, retain the direct population-ranking approach, even when a join-based approach is also plausible. Do not keep only a more complex plan by assuming the direct input cannot exist.'


def load_catalog():
    configured = instance.config().get('query_understanding', {}).get('catalog')
    path = Path(configured) if configured else Path(__file__).parent / 'shapes/query-shapes.replacement-codex.yaml'
    if not path.is_absolute():
        path = Path(instance.path()).resolve().parent / path
    raw = path.read_bytes()
    shapes = _parse_catalog(raw)
    ids = [s['id'] for s in shapes]
    if not ids or len(set(ids)) != len(ids):
        raise ValueError('Catalog must contain distinct shape IDs')
    for s in shapes:
        if not isinstance(s.get('asks'), str) or not isinstance(s.get('examples'), list) or not s['examples']:
            raise ValueError('Every shape needs a description and examples')
    return shapes, hashlib.sha256(raw).hexdigest()


from functools import lru_cache

@lru_cache(maxsize=4)
def _parse_catalog(raw):
    return yaml.safe_load(raw)['shapes']


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
        payload={'reference_date':context.reference_date,**payload}
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
                user = json.dumps({'question': question, **payload, 'repair': {
                    'error': str(exc), 'previous_output': raw,
                    'instruction': 'Return a corrected complete JSON response; preserve valid information.'}}, ensure_ascii=False)
                record.setdefault('repair_prompts', []).append(user)

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
            interpretations=result.setdefault('interpretations',[])
            if not isinstance(interpretations,list) or any(not isinstance(i,dict) or
                not all(isinstance(i.get(k),str) for k in ('entity','attribute','description')) or
                not (i['entity'].strip() or i['attribute'].strip()) for i in interpretations):
                raise ValueError('Interpretations require entity, attribute and description strings')
            for key in ('entities','measures','periods','missing','acquisition_queries'):
                if not isinstance(result.get(key),list): raise ValueError('Expected list: '+key)
            for entity in result['entities']:
                if not isinstance(entity,dict) or not all(isinstance(entity.get(k),str) for k in ('mention','description','type')) or not isinstance(entity.get('potential_matches'),list):
                    raise ValueError('Invalid entity description')
            for missing in result['missing']:
                slot = missing.get('slot') if isinstance(missing,dict) else None
                if not isinstance(slot,str) or slot.split('.')[0] not in shape['slots'] or not all(slot.split('.')) or not isinstance(missing.get('reason'),str):
                    raise ValueError('Missing bindings must name a declared slot (optionally a dotted subfield) and reason')
            if not all(isinstance(q,str) and q.strip() for q in result['acquisition_queries']):
                raise ValueError('Acquisition queries must be nonempty strings')
            if result.get('applicability') not in ('plausible','inapplicable') or not isinstance(result.get('reason'),str):
                raise ValueError('Invalid applicability/reason')
            needs_data = any('Read' in n.get('operator', '') or n.get('operator', '').startswith(('Resolve', 'Discover'))
                             for n in shape['plan']['nodes'])
            if result['applicability'] == 'plausible' and needs_data and not result['acquisition_queries']:
                raise ValueError('Plan requires external data: acquisition_queries must describe required inputs even when all parameters are bound')
        try:
            output = await call(EXTRACT, {'shape':shape}, 'understand-extract', validate)
            fields = ('bindings','entities','measures','periods','missing','acquisition_queries','applicability','reason','interpretations')
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
