"""Descriptor-grounded execution of supported replacement-template kernels.

Unsupported DAGs never become legacy question shapes. The existing verified
single-input fetch is reused as an adapter, with strict period fallback disabled.
"""
import json
import math
import re
import llm
import runtime

DERIVED = {'compare.derived-values', 'compare.derived-winner', 'compare.derived-difference'}
SUPPORTED = {'lookup.scalar', 'lookup.binary', 'compare.values', 'compare.winner'} | DERIVED
SYSTEM = '''Compile one supplied candidate into an executable scalar-input plan using ONLY supplied ARD resources.
Return JSON {"candidate": "shape id or null", "reason": "explanation", "reads": [{"question": "precise data request", "entity": "mention or empty", "type": "entity type", "measure": "exact measure", "period": "explicit requested period or latest if unspecified", "source": "supplied identifier"}], "expression": {"op": "operation", "args": [...]}, "pair_expression": {"op": "operation", "args": [...]}, "direction": "max or min"}. Use expression for lookup templates and pair_expression for derived templates; unused fields may be null.
Only supplied candidate IDs are permitted. lookup.scalar requires one read and expression; lookup.binary exactly two reads and expression. compare.values displays the named inputs; compare.winner orders a CLOSED named set, never discovers a population. Every read must be a supplied scalar, not a hidden aggregation, join, inferred proxy or raw record collection. Check descriptor grain, measure, period and access contract. No missing operands. If unavailable or materially ambiguous return candidate:null and explain.
Expressions are finite trees: {"read":0} references a zero-based read; numeric literals are constants; {"op":"identity|add|subtract|multiply|divide|eq|gt|lt", "args":[...]} computes values. No code. For scalar use identity unless the question requests a test. Bind operations from the question, not from knowledge of likely values. Do not invent identifiers or source availability. Preserve entity and statistical definitions. Distinguish funds received from an agency from data published by that agency.'''
SYSTEM += '\nThe currently supported period bindings are a four-digit year (e.g. "2023") or "latest". Do not expand a year into a date range or invent a calendar/fiscal convention. Copy source identifiers exactly from the provided resources.'
SYSTEM += '\nARD resources are descriptors of callable data sources, NOT fetched values. Your reads WILL be executed after planning. A company/year scalar accessor is sufficient to plan reading AMD or Intel revenue even though neither value appears in the descriptor. Do not refuse because data has not been fetched yet or because executing the plan needs external requests; that is the purpose of the reads. Refuse only if the descriptors do not support the required inputs or intent is unresolved.'
SYSTEM += '\nDerived templates require exactly TWO reads per named entity, consecutive in reads. Use pair_expression (the SAME expression for every pair, with local read indices 0 and 1) to calculate the requested measure. compare.derived-values displays calculated values; compare.derived-winner chooses max/min; compare.derived-difference requires exactly two pairs and subtracts the second calculated value from the first. Order pairs accordingly. Both reads in each pair must name the same entity. Include every operand; never substitute a supplied derived value for its two-input computation. Only the finite expression operators above are implemented; unsupported formulas must be refused.'


def expression_references(node, count):
    """Validate structure without evaluating fake values (which can divide by zero)."""
    if type(node) in (int,float) and math.isfinite(node): return set()
    if not isinstance(node,dict): raise runtime.Refused('Invalid expression')
    if set(node)=={'read'}:
        i=node['read']
        if type(i)!=int or not 0<=i<count:raise runtime.Refused('Invalid read reference')
        return {i}
    if set(node)!={'op','args'} or node['op'] not in {'identity','add','subtract','multiply','divide','eq','gt','lt'}:
        raise runtime.Refused('Unsupported expression operation')
    args=node['args']
    if not isinstance(args,list) or len(args)!=(1 if node['op']=='identity' else 2):raise runtime.Refused('Invalid expression arity')
    return set().union(*(expression_references(a,count) for a in args))


def derived_result(plan, evidence):
    pairs=list(zip(evidence[::2],evidence[1::2]))
    signatures=[tuple((e.get('unit'),e.get('currency')) for e in pair) for pair in pairs]
    if any(not e.get('unit') for pair in pairs for e in pair):raise runtime.Refused('Derived operands require known units')
    if len(set(signatures))!=1:raise runtime.Refused('Derived pairs need corresponding units/currencies')
    rows=[]
    for i,pair in enumerate(pairs):
        expression_units(plan['pair_expression'],pair)
        value=expression(plan['pair_expression'],[e['value'] for e in pair])
        if type(value) not in (int,float) or not math.isfinite(value):raise runtime.Refused('Derived comparison requires finite numeric values')
        rows.append({'entity':plan['reads'][2*i]['entity'],'value':value,'input_indices':[2*i,2*i+1]})
    if plan['candidate']=='compare.derived-values':return rows
    if plan['candidate']=='compare.derived-difference':return rows[0]['value']-rows[1]['value']
    best=(max if plan['direction']=='max' else min)(r['value'] for r in rows)
    return [r for r in rows if r['value']==best]


def expression_units(node, evidence):
    if type(node) in (int,float):return {}
    if 'read' in node:
        e=evidence[node['read']]
        return {(e['unit'],e.get('currency')):1}
    args=[expression_units(a,evidence) for a in node['args']]
    op=node['op']
    if op=='identity':return args[0]
    a,b=args
    if op in ('add','subtract','eq','gt','lt'):
        if a!=b:raise runtime.Refused('Expression combines incompatible units')
        return {} if op in ('eq','gt','lt') else a
    result=dict(a)
    for unit,power in b.items():result[unit]=result.get(unit,0)+(power if op=='multiply' else -power)
    return {unit:power for unit,power in result.items() if power}


def check_period(requested, evidence):
    if requested=='latest':return
    actual=str(evidence.get('period') or '')
    if not re.fullmatch(r'(?:FY)?'+re.escape(requested),actual,re.I):
        raise runtime.Refused(f'Requested {requested}, source returned {actual or "no period"}; refusing period substitution')


def expression(node, values):
    if isinstance(node, (int, float)) and not isinstance(node, bool):
        if not math.isfinite(node): raise runtime.Refused('Non-finite constant')
        return node
    if not isinstance(node, dict): raise runtime.Refused('Invalid expression')
    if set(node)=={'read'}:
        i=node['read']
        if type(i)!=int or not 0<=i<len(values): raise runtime.Refused('Invalid read reference')
        return values[i]
    if set(node)!={'op','args'} or not isinstance(node['args'],list): raise runtime.Refused('Invalid expression node')
    args=[expression(a,values) for a in node['args']]
    op=node['op']
    if op=='identity' and len(args)==1:return args[0]
    if len(args)!=2:raise runtime.Refused('Binary operation requires two values')
    a,b=args
    if op=='eq':return a==b
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in args):raise runtime.Refused('Numeric operands required')
    if op=='add':return a+b
    if op=='subtract':return a-b
    if op=='multiply':return a*b
    if op=='divide':
        if b==0:raise runtime.Refused('Zero denominator')
        return a/b
    if op=='gt':return a>b
    if op=='lt':return a<b
    raise runtime.Refused('Unsupported expression operation')


def validate(plan, candidates, hits):
    if not isinstance(plan,dict) or plan.get('candidate') not in {c['shape'] for c in candidates}:
        raise runtime.Refused('candidate-aware planning: '+str(plan.get('reason','no executable candidate') if isinstance(plan,dict) else 'invalid plan'))
    reads=plan.get('reads')
    if not isinstance(reads,list) or not reads:raise runtime.Refused('Plan has no required reads')
    shape=plan['candidate']
    if shape not in SUPPORTED:raise runtime.Refused('Unsupported template kernel')
    expected={'lookup.scalar':1,'lookup.binary':2}.get(shape)
    if expected and len(reads)!=expected:raise runtime.Refused('Read count violates template')
    for read in reads:
        if not isinstance(read,dict) or read.get('source') not in {h['identifier'] for h in hits}:raise runtime.Refused('Source not supplied by ARD')
        if not all(isinstance(read.get(k),str) for k in ('question','entity','type','measure','period')):raise runtime.Refused('Invalid read coordinates')
        if not read['question'].strip() or not read['measure'].strip() or not read['period'].strip():raise runtime.Refused('Unbound read')
        if read['period']!='latest' and not re.fullmatch(r'\d{4}',read['period']):raise runtime.Refused('Unsupported period binding; use an explicit year, not an inferred date range')
    if shape.startswith('lookup.'):
        if expression_references(plan.get('expression'),len(reads))!=set(range(len(reads))):raise runtime.Refused('Expression omits required inputs')
    if shape in DERIVED:
        if len(reads)%2 or len(reads)<2 or (shape=='compare.derived-difference' and len(reads)!=4):raise runtime.Refused('Derived plan requires pairs of reads')
        if expression_references(plan.get('pair_expression'),2)!={0,1}:raise runtime.Refused('Derived expression must use both operands')
        for a,b in zip(reads[::2],reads[1::2]):
            if not a['entity'].strip() or a['entity'].strip().casefold()!=b['entity'].strip().casefold():raise runtime.Refused('Derived operands must belong to the same named entity')
    if shape in ('compare.winner','compare.derived-winner') and plan.get('direction') not in ('max','min'):raise runtime.Refused('Missing ordering')


async def run(question, understanding, hits, *, context):
    import harness
    candidates=[c for c in understanding['candidates'] if c.get('status')=='ok' and c.get('applicability')=='plausible' and c['shape'] in SUPPORTED]
    if not candidates:raise runtime.Refused('candidate-aware planning: no executable scalar-input candidate; unsupported or failed candidates retained in understanding')
    payload={'question':question,'candidates':candidates,'resources':hits}
    raw=await llm.chat_async(SYSTEM,json.dumps(payload),context=context,json_mode=True,stage='plan',max_tokens=4096,reasoning_effort='low')
    try:plan=json.loads(raw)
    except (ValueError,TypeError) as exc:raise runtime.Refused('Invalid candidate plan JSON') from exc
    context.memo['template_plan']=plan
    validate(plan,candidates,hits)
    await harness._asay(context,'plan_chosen',shape=plan['candidate'],verdict='executable',summary=plan['reason'])
    evidence=[]; attempts=[]
    for read in plan['reads']:
        hit=next(h for h in hits if h['identifier']==read['source'])
        coords={'entity':read['entity'],'type':read['type'],'attribute':read['measure'],'period':read['period'],'strict_period':True}
        _,_,_,_,data,state=await harness._search_async(read['question'],ctx=coords,hits=[hit],context=context)
        ev=state['_evidence']
        check_period(read['period'],ev.to_dict())
        if ev.value is None:raise runtime.Refused('Read did not produce a scalar; hidden aggregation is not permitted')
        evidence.append(ev.to_dict()); attempts.extend(a.to_dict() for a in state.get('_attempts',[]))
    values=[e['value'] for e in evidence]
    shape=plan['candidate']
    if len(values)>1 and shape not in DERIVED:
        # Conservative admission: uncertain conversions require an explicit adapter.
        if len({(e.get('unit'),e.get('currency')) for e in evidence})!=1:raise runtime.Refused('Operands need explicit unit/currency reconciliation')
    if shape in DERIVED: result=derived_result(plan,evidence)
    elif shape.startswith('lookup.'): result=expression(plan['expression'],values)
    elif shape=='compare.values':result=[{'entity':r['entity'],'measure':r['measure'],'period':e['period'],'value':e['value']} for r,e in zip(plan['reads'],evidence)]
    else:
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in values):raise runtime.Refused('Ordering requires numeric scalars')
        best=(max if plan['direction']=='max' else min)(values)
        result=[{'entity':r['entity'],'value':v} for r,v in zip(plan['reads'],values) if v==best]
    data={'result':result,'inputs':evidence,'template':shape}
    # Rendering cannot change the deterministic computed result.
    answer=json.dumps(result,ensure_ascii=False)
    if shape=='lookup.scalar' and type(result) in (int,float):
        unit=evidence[0].get('unit') or ''
        period=evidence[0].get('period') or ''
        answer=f"{plan['reads'][0]['entity']} — {plan['reads'][0]['measure']}: {result:,} {unit} ({period})."
    return {'question':question,'shape':shape,'answer':answer,'answer_renderer':'template-json',
            'plan':plan,'data':data,'evidence':{'kind':'template','inputs':evidence},'attempts':attempts,
            'source':{'title':' + '.join(dict.fromkeys(e['source'] for e in evidence))},
            'candidates':understanding['candidates'],'usage':context.usage_ledger.snapshot(),
            'discovery_usage':context.discovery_ledger.snapshot()}
