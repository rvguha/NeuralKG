"""Bind the fixed catalog DAG to actual source adapters; execute no generated code."""
import json
import re

import answer_synthesizer as synth
import driver
import extensions
import llm
import runtime


# These describe parameter bindings, not additional logical operations. The model
# cannot alter the catalog DAG, add rows, attest completeness, or register code.
ARGUMENTS = {
    'Scalar':'expression: finite expression over input indices; {input:0}, {input:0,field:"total"}, {op:"divide",args:[...]}.',
    'Project':'fields: explicit field-name list or "*" for all available fields.',
    'Assemble':'fields: explicit field-name list or "*".',
    'MapScalar':'rows_input: index of relation input; output: output column; expression over {field:"name"}, {input:n}, constants, op/args.',
    'Filter':'predicate: Boolean finite expression; true allowed.',
    'Test':'predicate: Boolean finite expression.',
    'SelectResiduals':'predicate: explicit test of the residual field.',
    'Any':'No parameters. Existence of rows.', 'Count':'No parameters. Count rows.',
    'Distinct':'keys: nonempty equality-field list.', 'DistinctIfRequested':'keys: equality-field list; [] retains duplicates.',
    'Order':'by: [{field,direction:"asc"|"desc"}], nulls:"first"|"last"|"error".',
    'TimeOrder':'by: [{field:time-column,direction:"asc"}], nulls:"error".',
    'Take':'limit: positive integer or "all"; ties:"all" with tie_keys, or "exact" with unique tie_break_keys. Do not invent tie policy.',
    'Rank':'by:[{field,direction}], nulls:"error"; method:"min"|"max"|"average"|"dense"|"ordinal"; output:rank column.',
    'Locate':'predicate: identify exactly one row.', 'RelativeSlice':'before,after: nonnegative integer row counts.',
    'Group':'keys: grouping-field list.', 'GroupCount':'output:count column.',
    'StreamReduce':'method: sum|count|mean|min|max|variance|std|geometric_mean|pearson; field, nulls:error|drop; variance/std requires ddof:0|1; pearson requires fields:[x,y].',
    'OrderStatistics':'field, nulls:error|drop, q:[0,1], method:linear|lower|higher|midpoint|nearest.',
    'Join':'left_keys,right_keys: aligned identity-field lists; cardinality:one-to-one|one-to-many|many-to-one|many-to-many; right_prefix, right_fields; unmatched:drop|keep|error. Right columns receive prefix.',
    'AlignTime':'joins: one Join contract for each additional series. Numeric time keys and identical temporal basis required.',
    'AlignEndpoints':'joins: one Join contract for each additional endpoint relation.',
    'MapChanges':'expressions: {output_column:finite expression}; all endpoints are explicitly available columns.',
    'FillUnmatchedCountZero':'field: joined count column. Only unmatched rows become zero.',
    'BuildMembership':'keys: identity-field list.', 'MembershipFilter':'keys: identity-field list, mode:in|not-in.',
    'Union':'fields: common output schema field list.',
    'MatchRequired':'candidate_key, subject_key, object_key, required_key: corresponding column names.',
    'AllRequired':'required_key: key in required universe; empty_set_policy:vacuous|clarify.',
    'ReferenceCounts':'field, nulls:error|drop. Returns {less,equal,greater,total}.',
    'DeriveBins':'field,nulls,method:equal-width with count, or quantile with quantiles and quantile_method.',
    'Bin':'field, edges (unless input supplies them), include_last:Boolean, outside:error|drop, output:bin column.',
    'Allocate':'mode:fractional|full; field,weight,output:columns; contribution_keys:list. Fractional weights must sum to one per contribution.',
    'AdjacentPairs':'time: numeric ordered coordinate, field:value, spacing:positive interval. Produces previous/current columns.',
    'Difference':'time,field,spacing:positive numeric interval. Rejects gaps. Computes differences divided by spacing.',
    'ShiftTime':'time:column, offset: numeric coordinate shift.',
    'SummarizeAcceleration':'method:return_second_differences|sign_of_mean_second_difference; field,nulls:error|drop.',
    'RankColumns':'columns:{input_column:output_column}, method:average|min|max|dense.',
    'TwoSampleTest':'method:welch_two_sample_t, field,nulls:error|drop, alpha:(0,1), alternative:two-sided|less|greater. Do not invent significance level.',
    'FitModel':'method:ols, features:[columns], target:column, intercept:Boolean; must be explicitly requested.',
    'EvaluateBaseline':'expression: supplied baseline formula over fields.',
    'Residual':'target:observed column, output:residual column. Subtracts fitted/baseline predictions.',
    'ResolveScope':'root:identity, parent,child:edge columns, max_depth:integer, cycles:stop|error, include_root:Boolean.',
    'ReconcileScope':'root:identity, entity:column, scope:column of constituent identities, field:consolidated value column. Returns total/excluded.',
    'EvaluateRules':'rule_key,entity_key,rule_field:columns; actor:explicit known attributes; output:eligibility column. Unknown remains unknown.',
    'TemporalNeighborMatch':'left_keys,right_keys,right_prefix,right_fields,unmatched; direction:backward|forward|either; left_time,right_time: numeric coordinate columns; tolerance:nonnegative or null (unbounded), ties:all|error.',
    'IntervalJoin':'left_keys,right_keys,right_prefix,right_fields,unmatched; left_start,left_end,right_start,right_end; unbounded:Boolean, closed:both|left|right|neither.',
    'SpatialPredicate':'crs:exact source CRS, method:contains|covers, geometry:GeoJSON column, output:Boolean column.',
    'Distance':'crs:exact source CRS, method:projected|geodesic, unit:m|km, geometry:GeoJSON column, output:distance column.',
    'SpatialIntersect':'crs:projected area CRS, geometry:GeoJSON column. Input contains origin geometry and targets relation.',
    'DeriveWeights':'method:uniform-area, uniform_density:true only if supplied assumption, output:weight column.',
    'LorenzReduce':'field, nulls:error|drop. Nonnegative values with positive total.',
    'RepeatTraverse':'source:descriptor identifier; path:[{from:column,to:column},...] length>=2, cycles:stop|error|allow, seed_key,output. Adapter supplies one edge relation per path step, never final endpoints.'
}
for alias,base in {'LeftJoin':'Join','AntiJoin':'Join','AlignEntity':'Join','GroupOrder':'Order','GroupTake':'Take','GroupStreamReduce':'StreamReduce','GroupOrderStatistics':'OrderStatistics'}.items():
    ARGUMENTS[alias]=ARGUMENTS[base]+(' Also output:result column.' if alias in ('GroupStreamReduce','GroupOrderStatistics') else '')


def available_sources(hits):
    import planner
    readers=extensions.registry().template_readers
    result=[]
    for h in hits:
        fm=driver.frontmatter(h['identifier']) or {}
        name=fm.get('template_reader')
        caps=planner.capabilities(h['identifier'])
        usable={op:cap for op,cap in caps.items() if (cap.get('synthesis') or {}).get('read_only') is True}
        # An accessor is validated at ADVERTISE time, not at execution time: a descriptor naming
        # one that is not installed must not reach the planner as a usable source at all.
        accessor=fm.get('accessor')
        if accessor and accessor not in extensions.registry().accessors:
            continue
        result.append({**h,'template_reader':name if name in readers else None,'read_operations':usable,
                       'accessor':accessor,'scalar_adapter':True})
    return result


async def read(node,p,dependencies,*,hits,context):
    source=p.get('source')
    if source not in {h['identifier'] for h in hits}:raise runtime.Refused('Read source was not supplied by ARD')
    fm=driver.frontmatter(source) or {}
    # One accessor registration serves both dispatch paths. Checked FIRST so a descriptor that
    # declares one is never shadowed by a legacy template_reader or a built-in operator branch.
    accessor_name,accessor_fn=extensions.accessor_for(fm)
    if accessor_fn:
        read_request=extensions.Read(descriptor=fm,source=source,operation=node.get('operator'),
                                     parameters=p,dependencies=tuple(dependencies or ()),node=node)
        result=await accessor_fn(read_request,context=context)
        if not isinstance(result,synth.Input):
            raise runtime.Refused(f'accessor {accessor_name!r} returned {type(result).__name__}, '
                                  'not an answer_synthesizer.Input')
        return result
    name=fm.get('template_reader')
    if name:
        handler=extensions.registry().template_readers.get(name)
        if not handler:raise runtime.Refused('Source template reader is not registered: '+name)
        return await handler(node,p,dependencies,source=source,context=context)
    op=node['operator']
    if op=='ReadDescriptor':
        return synth.Input(fm,True,{'source':source,'kind':'descriptor'},'descriptor')
    if op=='ReadScalar':
        import harness
        from template_execution import check_period
        period=p.get('period')
        if dependencies:
            if len(dependencies)!=1:raise runtime.Refused('Scalar date dependency must resolve one value')
            period=str(dependencies[0].data)
        if period!='latest' and not re.fullmatch(r'\d{4}',str(period)):raise runtime.Refused('Scalar adapter supports explicit year or latest')
        ctx={'entity':p['entity'],'type':p['type'],'attribute':p['measure'],'period':period,'strict_period':True}
        _,_,_,_,_,state=await harness._search_async(p['question'],ctx=ctx,hits=[h for h in hits if h['identifier']==source],context=context)
        evidence=state['_evidence'].to_dict();check_period(period,evidence)
        if evidence.get('value') is None:raise runtime.Refused('Scalar read did not produce a value')
        return synth.Input(evidence['value'],True,evidence,'scalar',units={'value':evidence.get('unit')},period_basis=evidence.get('period_basis'))
    # A declarative adapter contract is descriptor-owned, not planner-supplied.
    import planner
    operation=p.get('operation'); cap=planner.capabilities(source).get(operation,{})
    contract=cap.get('synthesis') or {}
    if contract.get('read_only') is not True or op not in contract.get('operators',[]):
        raise runtime.Refused(f'{op}: source has no admitted complete-input adapter; a search hit is not coverage')
    if dependencies:raise runtime.Refused('This dependent read needs a registered template reader')
    if planner.operation_methods(source).get(operation) not in ('GET','POST'):raise runtime.Refused('Declarative input adapter requires a read-only HTTP operation')
    params=p.get('params',{})
    if set(params)-set(contract.get('parameters',[])):raise runtime.Refused('Input contains undeclared operation parameters')
    raw=await driver.accessor_async(source,operation,context=context,**params)
    data=synth.get(raw,contract['data_path']) if contract.get('data_path') else raw
    complete=False
    if contract.get('complete_path'):complete=synth.get(raw,contract['complete_path']) is True
    elif contract.get('total_path') and isinstance(data,list):complete=len(data)==synth.get(raw,contract['total_path'])
    elif contract.get('unpaginated') is True and (cap.get('population') or {}).get('complete') is True:complete=True
    return synth.Input(data,complete,{'source':source,'operation':operation,'params':params,'crs':contract.get('crs'),'payload':raw},
                       cap.get('grain',''),contract.get('key_domains',{}),contract.get('units',{}),contract.get('period_basis'))


async def run(question,understanding,hits,*,context):
    templates=synth.load_templates()
    candidates=[c for c in understanding['candidates'] if c.get('status')=='ok' and c.get('applicability')=='plausible' and c['shape'] in templates]
    if not candidates:raise runtime.Refused('candidate-aware planning: no successfully understood candidate template')
    operators={n['operator'] for c in candidates for n in templates[c['shape']]['plan']['nodes']}
    system='''Bind ONE supplied candidate to its FIXED catalog DAG. Return JSON {"candidate":"id or null","reason":"explanation","parameters":{"node_id":{...}}}.
Do not change node IDs, operators, dependencies or output. Bind only listed node parameters. Never invent data, crosswalks, source identifiers, capability evidence, methods, units or user constraints.
Every acquisition node needs source (exact ARD identifier), question, and accessor coordinates. ReadScalar: entity,type,measure,period (YYYY or latest). Other read nodes require a registered template_reader or a listed read_operations synthesis contract and operation/params. ReadDescriptor reads source metadata. If no suitable input adapter exists, return candidate:null and explain the missing input contract, not a simulated answer.
Expressions use {field:"column"}, {input:0}, {input:0,field:"path"}, bare constants, or {op:"identity|abs|sign|not|add|subtract|multiply|divide|power|eq|ne|lt|le|gt|ge|and|or",args:[...]}. No code or SQL. A descriptor is a callable source, not fetched data. Different viable templates may have different input needs. Choose one all of whose reads can be served.'''
    payload={'question':question,'candidates':[{'understanding':c,'template':templates[c['shape']]} for c in candidates],
             'resources':available_sources(hits),'operator_parameters':{o:ARGUMENTS[o] for o in operators if o in ARGUMENTS}}
    trace=context.memo.setdefault('planning_attempts',[])
    for attempt in range(3):
        raw=await llm.chat_async(system,json.dumps(payload),context=context,json_mode=True,stage='plan',max_tokens=7000,reasoning_effort='low')
        record={'system':system,'user':json.dumps(payload),'raw':raw};trace.append(record)
        try:
            plan=json.loads(raw)
            if not isinstance(plan,dict) or plan.get('candidate') not in {c['shape'] for c in candidates}:raise runtime.Refused(str(plan.get('reason','No executable plan') if isinstance(plan,dict) else 'Invalid plan'))
            shape=plan['candidate'];parameters=plan['parameters'];synth._template(shape,parameters)
            for node in templates[shape]['plan']['nodes']:
                if node['id'] not in parameters:raise runtime.Refused('Missing parameters for node '+node['id'])
                if node['operator'] in synth.READS|{'RepeatTraverse'} and parameters[node['id']].get('source') not in {h['identifier'] for h in hits}:raise runtime.Refused('Source not supplied by ARD')
            context.memo['template_plan']=plan
            await context.emit('plan_ready',plan=plan)
            acquired=[]
            async def reader(node,p,deps,*,context):
                from dataclasses import asdict
                value=await read(node,p,deps,hits=hits,context=context)
                acquired.append({'node':node['id'],'input':asdict(value)})
                return value
            output=await synth.execute(shape,parameters,reader,context=context)
            import harness
            output['inputs']=acquired
            answer=await harness.TK.synthesize_async(question,{'execution_plan':plan,
                'computed_result':output['result'],'retrieved_data':acquired,'execution':output},context=context)
            return {'question':question,'shape':shape,'answer':answer,'answer_renderer':'template-llm',
                    'plan':plan,'data':output,'evidence':output['evidence'],'candidates':hits,'template_candidates':understanding['candidates'],
                    'attempts':trace,'usage':context.usage_ledger.snapshot(),'discovery_usage':context.discovery_ledger.snapshot()}
        except (ValueError,KeyError,TypeError,runtime.Refused) as exc:
            record['error']=str(exc)
            if isinstance(locals().get('plan'),dict) and plan.get('candidate') is None:raise runtime.Refused(str(exc)) from exc
            payload['repair']={'error':str(exc),'previous_plan':raw,'instruction':'Correct the binding or select another supplied candidate. Never fabricate missing data.'}
            await context.emit('plan_repair',attempt=attempt+1,error=str(exc))
    raise runtime.Refused('No executable candidate plan: '+trace[-1]['error'])
