"""Finite, source-independent execution of the replacement catalog's fixed DAGs.

Read nodes are dependency-aware adapter calls. An adapter, never a planning LLM,
supplies Input evidence. Operators cannot acquire data or execute generated code.
Missing policies fail closed. See ANSWER_SYNTHESIZER.md for the binding protocol.
"""
from __future__ import annotations

import copy
import math
from collections import defaultdict
from dataclasses import dataclass, field
from functools import cmp_to_key
from typing import Any

import numpy as np
from runtime import Refused


@dataclass
class Input:
    data: Any
    complete: bool
    provenance: dict
    grain: str
    key_domains: dict = field(default_factory=dict)
    units: dict = field(default_factory=dict)
    period_basis: str | None = None


@dataclass
class Groups:
    keys: list
    groups: list  # [(key tuple, rows)]


READS = set('ReadScalar ReadRecord ReadVersionedScalar ReadRows ReadSeries ReadMembership ReadHierarchy ReadGeometries ReadBoundary ReadSeeds ReadDescriptor ResolveDate ResolvePoint DiscoverDescriptors MapReadSeries MapReadScalar MapReadScalarPairs MapReadRows MapReadRules'.split())
OPERATORS = {}


def operation(*names):
    def register(fn):
        for name in names:
            OPERATORS[name] = fn
        return fn
    return register


def need(p, name):
    if name not in p:
        raise Refused('Missing operator parameter: ' + name)
    return p[name]


def number(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise Refused('Finite numeric value required; nulls are not zero')
    return value


def get(row, path):
    if isinstance(row, dict) and path in row:
        return row[path]
    value = row
    for part in str(path).split('.'):
        if isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        elif isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise Refused('Missing field: ' + str(path))
    return value


def key(row, fields):
    result = tuple(get(row, f) for f in fields)
    if any(v is None for v in result):
        raise Refused('Null identity/group key requires an explicit upstream resolution')
    try:
        hash(result)
    except TypeError as exc:
        raise Refused('Keys must be scalar values') from exc
    return result


def rows(value):
    if isinstance(value, Groups):
        return [row for _, group in value.groups for row in group]
    if not isinstance(value, list) or any(not isinstance(r, dict) for r in value):
        raise Refused('Operator requires a row relation')
    return value


def expr(tree, row=None, inputs=()):
    """JSON expression: literal, field, input, or whitelisted op/args; no eval."""
    if tree is None or type(tree) in (str, bool, int, float):
        if type(tree) in (int, float): number(tree)
        return tree
    if not isinstance(tree, dict): raise Refused('Invalid expression')
    if set(tree) == {'literal'}: return tree['literal']
    if set(tree) == {'field'}: return get(row, tree['field'])
    if set(tree) in ({'input'}, {'input', 'field'}):
        i = tree['input']
        if type(i) is not int or not 0 <= i < len(inputs): raise Refused('Invalid input reference')
        return get(inputs[i], tree['field']) if 'field' in tree else inputs[i]
    if set(tree) != {'op', 'args'} or not isinstance(tree['args'], list): raise Refused('Invalid expression node')
    op = tree['op']; args = [expr(x, row, inputs) for x in tree['args']]
    arity = {'identity':1, 'abs':1, 'sign':1, 'not':1, 'add':2, 'subtract':2,
             'multiply':2, 'divide':2, 'power':2, 'eq':2, 'ne':2, 'lt':2, 'le':2,
             'gt':2, 'ge':2, 'and':2, 'or':2}
    if op not in arity or len(args) != arity[op]: raise Refused('Unsupported expression/arity: '+str(op))
    if op == 'identity': return args[0]
    if op in ('and', 'or', 'not'):
        if any(type(a) is not bool for a in args): raise Refused('Boolean operands required')
        return (not args[0]) if op == 'not' else (all(args) if op == 'and' else any(args))
    if any(a is None for a in args): raise Refused('Null expression operand')
    for a in args:
        if type(a) in (int,float):number(a)
    if op in ('eq','ne','lt','le','gt','ge'):
        a,b=args
        return {'eq':lambda:a==b,'ne':lambda:a!=b,'lt':lambda:a<b,'le':lambda:a<=b,'gt':lambda:a>b,'ge':lambda:a>=b}[op]()
    for a in args: number(a)
    a = args[0]
    if op == 'abs': return abs(a)
    if op == 'sign': return int(a > 0)-int(a < 0)
    b = args[1]
    if op == 'divide' and b == 0: raise Refused('Zero denominator')
    try:
        value = {'add':lambda:a+b,'subtract':lambda:a-b,'multiply':lambda:a*b,
                 'divide':lambda:a/b,'power':lambda:math.pow(a,b)}[op]()
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        raise Refused('Undefined arithmetic') from exc
    return number(value)


@operation('Scalar')
def scalar(xs, p): return expr(need(p,'expression'), inputs=xs)


@operation('Project', 'Assemble')
def project(xs, p):
    value = xs[0]
    fields = need(p, 'fields')
    if fields == '*': return copy.deepcopy(value)
    if not isinstance(fields, list): raise Refused('Projection must be fields or *')
    if isinstance(value, dict): return {f:get(value,f) for f in fields}
    return [{f:get(r,f) for f in fields} for r in rows(value)]


@operation('MapScalar')
def map_scalar(xs, p):
    index = need(p, 'rows_input')
    return [{**r, need(p,'output'):expr(need(p,'expression'), r, xs)} for r in rows(xs[index])]


@operation('Filter', 'Test', 'SelectResiduals')
def select(xs, p):
    result=[]
    for r in rows(xs[0]):
        flag=expr(need(p,'predicate'),r,xs)
        if type(flag) is not bool: raise Refused('Predicate must return a Boolean')
        if flag: result.append(r)
    return result


@operation('Any', 'Count')
def count(xs,p):
    n=len(rows(xs[0]))
    return bool(n) if p['_operator']=='Any' else n


@operation('Distinct', 'DistinctIfRequested')
def distinct(xs,p):
    fields=need(p,'keys'); data=rows(xs[0])
    if not fields and p['_operator']=='DistinctIfRequested':return data
    if not fields:raise Refused('Distinct requires equality keys')
    seen=set(); result=[]
    for r in data:
        k=key(r,fields)
        if k not in seen: result.append(r); seen.add(k)
    return result


def sorted_rows(data,p):
    fields=need(p,'by'); nulls=need(p,'nulls')
    if not fields or nulls not in ('first','last','error'):raise Refused('Invalid ordering policy')
    def compare(a,b):
        for f in fields:
            x,y=get(a,f['field']),get(b,f['field'])
            if x is None or y is None:
                if nulls=='error':raise Refused('Null ordering value')
                if x is y:continue
                return (-1 if x is None else 1)*(1 if nulls=='first' else -1)
            if f['direction'] not in ('asc','desc'):raise Refused('Invalid direction')
            if x!=y:return ((x>y)-(x<y))*(1 if f['direction']=='asc' else -1)
        return 0
    return sorted(rows(data),key=cmp_to_key(compare))


@operation('Order','TimeOrder')
def order(xs,p):return sorted_rows(xs[0],p)


@operation('Take')
def take(xs,p):
    data=rows(xs[0]); n=need(p,'limit'); ties=need(p,'ties')
    if n=='all':return data
    if type(n) is not int or n<1:raise Refused('Positive limit required')
    if ties not in ('all','exact'):raise Refused('Explicit tie policy required')
    if ties=='exact':
        # Caller must establish a deterministic total ordering, not source arrival order.
        fields=need(p,'tie_break_keys')
        if not fields or len({key(r,fields) for r in data})!=len(data):raise Refused('Exact limit needs unique deterministic tie-break keys')
        tie_keys=need(p,'tie_keys')
        ordered=[]; i=0
        while i<len(data):
            j=i+1
            while j<len(data) and key(data[j],tie_keys)==key(data[i],tie_keys):j+=1
            ordered.extend(sorted(data[i:j],key=lambda r:key(r,fields)));i=j
        return ordered[:n]
    fields=need(p,'tie_keys')
    if not fields:raise Refused('Include-all ties requires ordering keys')
    if len(data)<=n:return data
    boundary=key(data[n-1],fields)
    i=n
    while i<len(data) and key(data[i],fields)==boundary:i+=1
    return data[:i]


@operation('Rank')
def rank(xs,p):
    data=sorted_rows(xs[0],p); fields=[f['field'] for f in p['by']]
    method=need(p,'method'); out=need(p,'output')
    if method not in ('min','max','average','dense','ordinal'):raise Refused('Unknown rank method')
    if method=='ordinal' and len({key(r,fields) for r in data})!=len(data):raise Refused('Ordinal rank requires a total order')
    result=[]; i=0; dense=0
    while i<len(data):
        j=i+1
        while j<len(data) and key(data[j],fields)==key(data[i],fields):j+=1
        dense+=1
        for k in range(i,j):
            r={'min':i+1,'max':j,'average':(i+1+j)/2,'dense':dense,'ordinal':k+1}[method]
            result.append({**data[k],out:r})
        i=j
    return result


@operation('Locate')
def locate(xs,p):
    data=rows(xs[0]); indices=[i for i,r in enumerate(data) if expr(need(p,'predicate'),r,xs)]
    if len(indices)!=1:raise Refused('Named position must resolve to exactly one row')
    return {'rows':data,'index':indices[0]}


@operation('RelativeSlice')
def relative(xs,p):
    value=xs[0]; before=need(p,'before'); after=need(p,'after')
    if any(type(n) is not int or n<0 for n in (before,after)):raise Refused('Invalid relative slice')
    return value['rows'][max(0,value['index']-before):value['index']+after+1]


@operation('Group')
def group(xs,p):
    fields=need(p,'keys'); groups=defaultdict(list)
    for r in rows(xs[0]):groups[key(r,fields)].append(r)
    return Groups(fields,list(groups.items()))


@operation('GroupOrder','GroupTake','GroupCount','GroupStreamReduce','GroupOrderStatistics')
def group_op(xs,p):
    g=xs[0]
    if not isinstance(g,Groups):raise Refused('Grouped input required')
    name=p['_operator'][5:]
    if name=='Order':return Groups(g.keys,[(k,order([v],p)) for k,v in g.groups])
    if name=='Take':return [r for _,v in g.groups for r in take([v],p)]
    result=[]
    for k,v in g.groups:
        value=len(v) if name=='Count' else OPERATORS[name]([v],{**p,'_operator':name})
        result.append({**dict(zip(g.keys,k)), need(p,'output'):value})
    return result


def numbers(data,field,nulls):
    values=[get(r,field) for r in rows(data)]
    if nulls not in ('error','drop'):raise Refused('Specify nulls error or drop')
    return [number(v) for v in values if v is not None or nulls!='drop']


@operation('StreamReduce')
def reduce(xs,p):
    method=need(p,'method')
    if method=='count':return len(rows(xs[0]))
    if method=='pearson':
        a=np.array([[number(get(r,f)) for f in need(p,'fields')] for r in rows(xs[0])])
        if a.ndim!=2 or a.shape[1]!=2 or len(a)<2 or any(np.std(a[:,i])==0 for i in (0,1)):raise Refused('Correlation needs two nonconstant paired samples')
        return float(np.corrcoef(a.T)[0,1])
    values=numbers(xs[0],need(p,'field'),need(p,'nulls'))
    if method=='sum':return sum(values)
    if not values:raise Refused('Undefined reduction of empty population')
    if method=='mean':return sum(values)/len(values)
    if method=='min':return min(values)
    if method=='max':return max(values)
    if method in ('variance','std'):
        ddof=need(p,'ddof')
        if ddof not in (0,1) or len(values)<=ddof:raise Refused('Insufficient sample for variance')
        return float(np.var(values,ddof=ddof) if method=='variance' else np.std(values,ddof=ddof))
    if method=='geometric_mean':
        if min(values)<=0:raise Refused('Geometric mean needs positive factors')
        return math.exp(sum(math.log(x) for x in values)/len(values))
    raise Refused('Unknown reduction: '+str(method))


@operation('OrderStatistics')
def quantile(xs,p):
    values=numbers(xs[0],need(p,'field'),need(p,'nulls')); q=need(p,'q'); method=need(p,'method')
    if not values or type(q) not in (int,float) or not 0<=q<=1:raise Refused('Invalid quantile input')
    if method not in ('linear','lower','higher','midpoint','nearest'):raise Refused('Unknown quantile convention')
    return float(np.quantile(values,q,method=method))


@operation('LorenzReduce')
def gini(xs,p):
    values=sorted(numbers(xs[0],need(p,'field'),need(p,'nulls')))
    if not values or min(values)<0 or sum(values)==0:raise Refused('Gini requires nonnegative values with positive total')
    n=len(values)
    return 2*sum((i+1)*v for i,v in enumerate(values))/(n*sum(values))-(n+1)/n


@operation('ReferenceCounts')
def reference(xs,p):
    ref=number(xs[0]); values=numbers(xs[1],need(p,'field'),need(p,'nulls'))
    if not values:raise Refused('Empty reference population')
    return {'less':sum(v<ref for v in values),'equal':sum(v==ref for v in values),'greater':sum(v>ref for v in values),'total':len(values)}


def join_rows(left,right,p,how='inner'):
    lk=need(p,'left_keys'); rk=need(p,'right_keys'); card=need(p,'cardinality')
    if not lk or len(lk)!=len(rk) or card not in ('one-to-one','one-to-many','many-to-one','many-to-many'):raise Refused('Invalid join contract')
    left=rows(left); right=rows(right); index=defaultdict(list)
    for r in right:index[key(r,rk)].append(r)
    if card in ('one-to-one','many-to-one') and any(len(v)>1 for v in index.values()):raise Refused('Right join-key uniqueness violated')
    if card in ('one-to-one','one-to-many') and len({key(r,lk) for r in left})!=len(left):raise Refused('Left join-key uniqueness violated')
    prefix=need(p,'right_prefix'); right_fields=need(p,'right_fields')
    policy=need(p,'unmatched')
    if policy not in ('drop','keep','error'):raise Refused('Invalid unmatched policy')
    result=[]; seen=set()
    for l in left:
        k=key(l,lk); matches=index.get(k,[]); seen.add(k)
        if how=='anti':
            if not matches:result.append(l)
            continue
        if not matches and policy=='error':raise Refused('Unmatched join key')
        if not matches and (how=='left' or policy=='keep'):matches=[None]
        for r in matches:
            extra={prefix+f:(get(r,f) if r is not None else None) for f in right_fields}
            if set(extra)&set(l) or '__matched' in l:raise Refused('Join output field collision')
            result.append({**l,**extra,'__matched':r is not None})
    if policy=='error' and any(k not in seen for k in index):raise Refused('Right-only join key')
    return result


@operation('Join','LeftJoin','AntiJoin','AlignEntity')
def join(xs,p):
    return join_rows(xs[0],xs[1],p,{'LeftJoin':'left','AntiJoin':'anti'}.get(p['_operator'],'inner'))


@operation('AlignTime')
def align_time(xs,p):
    relations=xs[0] if len(xs)==1 else xs
    if not relations:raise Refused('Time alignment needs at least one series')
    if p.get('layout') == 'long':
        time=need(p,'time')
        combined=[]
        for index,relation in enumerate(relations):
            for row in rows(relation):
                if 'series_index' in row:raise Refused('Reserved series_index column already exists')
                number(get(row,time))
                combined.append({**row,'series_index':index})
        return sorted(combined,key=lambda row:(get(row,time),row['series_index']))
    result=relations[0]
    contracts=need(p,'joins')
    if len(contracts)!=len(relations)-1:raise Refused(f'{len(relations)} series need {len(relations)-1} alignment contracts; received {len(contracts)}. A long-form series display can use layout=long with a numeric time column.')
    for other,contract in zip(relations[1:],contracts):
        result=join_rows(result,other,contract)
        result=[{k:v for k,v in r.items() if k!='__matched'} for r in result]
    return result


@operation('AlignEndpoints')
def align_endpoints(xs,p):return align_time(xs,p)


@operation('MapChanges')
def changes(xs,p):
    formulas=need(p,'expressions')
    return [{**r,**{name:expr(formula,r,xs) for name,formula in formulas.items()}} for r in rows(xs[0])]


@operation('FillUnmatchedCountZero')
def fill_counts(xs,p):
    field=need(p,'field'); result=[]
    for r in rows(xs[0]):
        if r.get('__matched') is False:result.append({**r,field:0})
        elif get(r,field) is None:raise Refused('Matched null count is not zero')
        else:result.append(r)
    return result


@operation('BuildMembership')
def build_membership(xs,p):return {key(r,need(p,'keys')) for r in rows(xs[0])}


@operation('MembershipFilter')
def membership(xs,p):
    mode=need(p,'mode')
    if mode not in ('in','not-in'):raise Refused('Unknown membership mode')
    return [r for r in rows(xs[0]) if (key(r,need(p,'keys')) in xs[1])==(mode=='in')]


@operation('Union')
def union(xs,p):
    relations=xs[0] if len(xs)==1 else xs; fields=need(p,'fields')
    return [{f:get(r,f) for f in fields} for relation in relations for r in rows(relation)]


@operation('MatchRequired')
def match_required(xs,p):
    candidates,edges,required=map(rows,xs)
    ck=need(p,'candidate_key'); ek=need(p,'subject_key'); target=need(p,'object_key'); rk=need(p,'required_key')
    required_keys={key(r,[rk]) for r in required}
    result=[]
    for c in candidates:
        matches={key(e,[target]) for e in edges if get(e,ek)==get(c,ck)}&required_keys
        # Preserve candidates without matches, including an empty required universe.
        result.extend({**c,'__required':m[0],'__has_required':True} for m in matches)
        if not matches:result.append({**c,'__required':None,'__has_required':False})
    return result


@operation('AllRequired')
def all_required(xs,p):
    g,required=xs
    if not isinstance(g,Groups):raise Refused('AllRequired needs grouped candidates')
    needed={key(r,[need(p,'required_key')])[0] for r in rows(required)}
    if not needed and need(p,'empty_set_policy')!='vacuous':raise Refused('Empty required universe needs vacuous-truth permission')
    return [dict(zip(g.keys,k)) for k,rs in g.groups if {r['__required'] for r in rs if r['__has_required']}>=needed]


@operation('DeriveBins')
def derive_bins(xs,p):
    values=numbers(xs[0],need(p,'field'),need(p,'nulls')); method=need(p,'method')
    if not values:raise Refused('Cannot derive bins from empty data')
    if method=='equal-width':
        n=need(p,'count')
        if type(n) is not int or n<1 or min(values)==max(values):raise Refused('Invalid equal-width bin range/count')
        return np.linspace(min(values),max(values),n+1).tolist()
    if method=='quantile':
        qs=need(p,'quantiles')
        if not qs or qs[0]!=0 or qs[-1]!=1 or any(a>=b for a,b in zip(qs,qs[1:])):raise Refused('Quantile bins must span [0,1] in order')
        return np.quantile(values,qs,method=need(p,'quantile_method')).tolist()
    raise Refused('Unsupported adaptive bin method')


@operation('Bin')
def bin_rows(xs,p):
    edges=xs[1] if len(xs)>1 else need(p,'edges')
    if len(edges)<2 or any(number(a)>=number(b) for a,b in zip(edges,edges[1:])):raise Refused('Bin edges must be strictly increasing')
    result=[]
    for r in rows(xs[0]):
        v=number(get(r,need(p,'field'))); j=None
        for i,(a,b) in enumerate(zip(edges,edges[1:])):
            if a<=v<b or (need(p,'include_last') and i==len(edges)-2 and v==b):j=i;break
        if j is None:
            if need(p,'outside')=='drop':continue
            raise Refused('Value outside declared bins')
        result.append({**r,need(p,'output'):j})
    return result


@operation('Allocate')
def allocate(xs,p):
    data=rows(xs[-1]); mode=need(p,'mode'); field=need(p,'field'); weight=need(p,'weight'); out=need(p,'output')
    if mode not in ('fractional','full'):raise Refused('Explicit allocation mode required')
    if mode=='fractional':
        totals=defaultdict(float)
        for r in data:
            w=number(get(r,weight))
            if not 0<=w<=1:raise Refused('Allocation weight outside [0,1]')
            totals[key(r,need(p,'contribution_keys'))]+=w
        if any(not math.isclose(v,1,rel_tol=1e-9,abs_tol=1e-9) for v in totals.values()):raise Refused('Allocation weights must conserve each contribution')
    return [{**r,out:(number(xs[0]) if len(xs)>1 else number(get(r,field)))*(number(get(r,weight)) if mode=='fractional' else 1)} for r in data]


@operation('AdjacentPairs','Difference')
def differences(xs,p):
    data=rows(xs[0]); time=need(p,'time'); value=need(p,'field'); spacing=number(need(p,'spacing'))
    if spacing<=0:raise Refused('Positive time spacing required')
    data=sorted(data,key=lambda r:get(r,time)); result=[]
    for a,b in zip(data,data[1:]):
        gap=number(get(b,time))-number(get(a,time))
        if not math.isclose(gap,spacing):raise Refused('Series has duplicate times, gaps, or incompatible spacing')
        result.append({**b,'previous':get(a,value),'current':get(b,value)} if p['_operator']=='AdjacentPairs'
                      else {**b,value:(number(get(b,value))-number(get(a,value)))/spacing})
    return result


@operation('ShiftTime')
def shift(xs,p):
    field=need(p,'time'); offset=number(need(p,'offset'))
    return [{**r,field:number(get(r,field))+offset} for r in rows(xs[0])]


@operation('SummarizeAcceleration')
def acceleration(xs,p):
    mode=need(p,'method')
    if mode=='return_second_differences':return rows(xs[0])
    if mode!='sign_of_mean_second_difference':raise Refused('Unknown acceleration summary')
    v=reduce(xs,{**p,'method':'mean'})
    return int(v>0)-int(v<0)


@operation('RankColumns')
def rank_columns(xs,p):
    from scipy.stats import rankdata
    data=rows(xs[0]); method=need(p,'method')
    if method not in ('average','min','max','dense'):raise Refused('Explicit rank tie convention required')
    ranked={out:rankdata(numbers(data,f,'error'),method=method).tolist() for f,out in need(p,'columns').items()}
    return [{**r,**{out:v[i] for out,v in ranked.items()}} for i,r in enumerate(data)]


@operation('TwoSampleTest')
def two_sample(xs,p):
    from scipy.stats import ttest_ind
    if need(p,'method')!='welch_two_sample_t':raise Refused('Only Welch two-sample test is defined')
    alpha=number(need(p,'alpha'))
    if not 0<alpha<1:raise Refused('Significance level must lie in (0,1)')
    a=numbers(xs[0],need(p,'field'),need(p,'nulls')); b=numbers(xs[1],p['field'],p['nulls'])
    if min(len(a),len(b))<2:raise Refused('Welch test requires at least two observations per sample')
    result=ttest_ind(a,b,equal_var=False,alternative=need(p,'alternative'))
    statistic=number(float(result.statistic)); probability=number(float(result.pvalue)); df=number(float(result.df))
    return {'statistic':statistic,'pvalue':probability,'df':df,'alpha':alpha,'reject_null':probability<alpha}


@operation('FitModel')
def fit(xs,p):
    if need(p,'method')!='ols':raise Refused('Only explicitly requested OLS is implemented')
    data=rows(xs[0]); features=need(p,'features'); intercept=need(p,'intercept')
    X=np.array([[number(get(r,f)) for f in features] for r in data]); y=np.array(numbers(data,need(p,'target'),'error'))
    if intercept:X=np.column_stack([np.ones(len(X)),X])
    if X.ndim!=2 or len(X)<=X.shape[1] or np.linalg.matrix_rank(X)!=X.shape[1]:raise Refused('Underdetermined or singular fit')
    coef=np.linalg.lstsq(X,y,rcond=None)[0].tolist()
    return {'method':'ols','features':features,'intercept':intercept,'coefficients':coef}


@operation('EvaluateBaseline')
def baseline(xs,p):return [number(expr(need(p,'expression'),r,xs)) for r in rows(xs[0])]


@operation('Residual')
def residual(xs,p):
    data=rows(xs[0]); model=xs[1]
    if isinstance(model,dict):
        predictions=[sum(a*b for a,b in zip(([1] if model['intercept'] else [])+[number(get(r,f)) for f in model['features']],model['coefficients'])) for r in data]
    else:predictions=model
    if len(predictions)!=len(data):raise Refused('Baseline and observation counts differ')
    return [{**r,need(p,'output'):number(get(r,need(p,'target')))-number(v)} for r,v in zip(data,predictions)]


def merge_temporal(left,right,p):
    extra={need(p,'right_prefix')+f:(get(right,f) if right is not None else None) for f in need(p,'right_fields')}
    if set(left)&set(extra):raise Refused('Temporal output field collision')
    return {**left,**extra}


@operation('TemporalNeighborMatch','IntervalJoin')
def temporal(xs,p):
    left,right=map(rows,xs); result=[]; direction=p.get('direction')
    if p['_operator']=='TemporalNeighborMatch' and direction not in ('backward','forward','either'):raise Refused('Invalid temporal direction')
    for l in left:
        candidates=[]
        for r in right:
            if key(l,need(p,'left_keys'))!=key(r,need(p,'right_keys')):continue
            if p['_operator']=='IntervalJoin':
                def endpoint(row,f,start):
                    v=get(row,f)
                    if v is None:
                        if need(p,'unbounded') is not True:raise Refused('Unknown interval endpoint')
                        return -math.inf if start else math.inf
                    return number(v)
                a,b=endpoint(l,need(p,'left_start'),True),endpoint(l,need(p,'left_end'),False)
                c,d=endpoint(r,need(p,'right_start'),True),endpoint(r,need(p,'right_end'),False)
                if a>b or c>d:raise Refused('Reversed interval')
                closed=need(p,'closed')
                if closed not in ('both','left','right','neither'):raise Refused('Invalid interval boundary convention')
                if max(a,c)<min(b,d) or (closed=='both' and max(a,c)==min(b,d)):candidates.append((0,r))
            else:
                delta=number(get(r,need(p,'right_time')))-number(get(l,need(p,'left_time')))
                tolerance=need(p,'tolerance')
                if tolerance is not None and number(tolerance)<0:raise Refused('Negative temporal tolerance')
                if (direction=='backward' and delta>0) or (direction=='forward' and delta<0) or (tolerance is not None and abs(delta)>tolerance):continue
                candidates.append((abs(delta),r))
        if candidates and p['_operator']=='TemporalNeighborMatch':
            best=min(x[0] for x in candidates); candidates=[x for x in candidates if x[0]==best]
            ties=need(p,'ties')
            if ties not in ('all','error'):raise Refused('Temporal ties require all or error')
            if ties=='error' and len(candidates)>1:raise Refused('Ambiguous temporal neighbor')
        if not candidates:
            policy=need(p,'unmatched')
            if policy=='error':raise Refused('Unmatched temporal record')
            if policy=='keep':result.append(merge_temporal(l,None,p))
            elif policy!='drop':raise Refused('Unknown temporal unmatched policy')
        else:result.extend(merge_temporal(l,r,p) for _,r in candidates)
    return result


@operation('ResolveScope')
def scope(xs,p):
    edges=rows(xs[0]); root=need(p,'root'); parent=need(p,'parent'); child=need(p,'child')
    depth=need(p,'max_depth'); policy=need(p,'cycles')
    if type(depth) is not int or depth<0 or policy not in ('stop','error'):raise Refused('Explicit finite hierarchy traversal policy required')
    frontier=[(root,(root,))]; result=[{'entity':root}] if need(p,'include_root') else []
    for _ in range(depth):
        next_frontier=[]
        for current,path in frontier:
            for e in edges:
                if get(e,parent)!=current:continue
                target=get(e,child)
                if target in path:
                    if policy=='error':raise Refused('Hierarchy cycle')
                    continue
                result.append({'entity':target});next_frontier.append((target,(*path,target)))
        frontier=next_frontier
    return result


@operation('ReconcileScope')
def reconcile(xs,p):
    data=rows(xs[0]); entity=need(p,'entity'); root=need(p,'root'); scope_field=need(p,'scope')
    roots=[r for r in data if get(r,entity)==root]
    if len(roots)!=1:raise Refused('Exactly one consolidated root required')
    covered=set(get(roots[0],scope_field)); seen=set(); excluded=0
    for r in data:
        if r is roots[0]:continue
        members=set(get(r,scope_field))
        if not members or not members<=covered or members&seen:raise Refused('Reporting scopes overlap or are outside the consolidated total')
        seen|=members;excluded+=number(get(r,need(p,'field')))
    return {'total':number(get(roots[0],p['field'])),'excluded':excluded}


def rule_expr(tree, actor):
    if not isinstance(tree,dict):return tree
    if set(tree)=={'field'}:
        try:return get(actor,tree['field'])
        except Refused:return None
    if set(tree)=={'literal'}:return tree['literal']
    if set(tree)!={'op','args'}:raise Refused('Invalid eligibility rule')
    values=[rule_expr(a,actor) for a in tree['args']]; op=tree['op']
    if op in ('and','or'):
        if len(values)!=2 or any(v is not None and type(v) is not bool for v in values):raise Refused('Invalid Boolean rule')
        if op=='and':return False if False in values else None if None in values else True
        return True if True in values else None if None in values else False
    if op=='not':
        if len(values)!=1 or values[0] is not None and type(values[0]) is not bool:raise Refused('Invalid negation')
        return None if values[0] is None else not values[0]
    if None in values:
        # Validate the operator even when values are unknown.
        if op not in ('eq','ne','lt','le','gt','ge','add','subtract','multiply','divide'):raise Refused('Unsupported eligibility operator')
        if len(values)!=2:raise Refused('Invalid rule arity')
        return None
    return expr({'op':op,'args':values})


@operation('EvaluateRules')
def evaluate_rules(xs,p):
    data,rules=map(rows,xs); index={key(r,[need(p,'rule_key')]):r for r in rules}
    if len(index)!=len(rules):raise Refused('Duplicate eligibility rule identity')
    result=[]
    for r in data:
        matched=index.get(key(r,[need(p,'entity_key')]))
        if matched is None:raise Refused('Missing eligibility rule')
        flag=rule_expr(get(matched,need(p,'rule_field')),need(p,'actor'))
        if flag is not None and type(flag) is not bool:raise Refused('Eligibility must be true, false, or unknown')
        result.append({**r,need(p,'output'):'unknown' if flag is None else flag})
    return result


def geometry(value):
    from shapely.geometry import shape
    g=shape(value)
    if not g.is_valid or g.is_empty:raise Refused('Invalid or empty geometry')
    return g


@operation('SpatialPredicate')
def spatial_predicate(xs,p):
    boundary=geometry(xs[0]); method=need(p,'method')
    if method not in ('contains','covers'):raise Refused('Explicit boundary-point semantics required')
    return [{**r,need(p,'output'):bool(getattr(boundary,method)(geometry(get(r,need(p,'geometry')))))} for r in rows(xs[1])]


@operation('Distance')
def distance(xs,p):
    from pyproj import CRS, Geod
    origin=geometry(xs[0]); crs=CRS.from_user_input(need(p,'crs')); method=need(p,'method'); unit=need(p,'unit')
    if origin.geom_type!='Point' or unit not in ('m','km'):raise Refused('Point origin and m/km distance required')
    result=[]
    for r in rows(xs[1]):
        target=geometry(get(r,need(p,'geometry')))
        if method=='geodesic':
            if crs.to_epsg()!=4326 or target.geom_type!='Point':raise Refused('Geodesic distance requires WGS84 point geometries')
            if any(not -180<=g.x<=180 or not -90<=g.y<=90 for g in (origin,target)):raise Refused('Invalid longitude/latitude')
            _,_,value=Geod(ellps='WGS84').inv(origin.x,origin.y,target.x,target.y)
        elif method=='projected':
            if not crs.is_projected:raise Refused('Planar distance requires a projected CRS')
            value=origin.distance(target)*crs.axis_info[0].unit_conversion_factor
        else:raise Refused('Unsupported distance metric')
        result.append({**r,need(p,'output'):number(value)/(1000 if unit=='km' else 1)})
    return result


@operation('SpatialIntersect')
def spatial_intersect(xs,p):
    from pyproj import CRS
    from shapely.geometry import mapping
    crs=CRS.from_user_input(need(p,'crs'))
    if not crs.is_projected:raise Refused('Area intersections require a projected area CRS')
    origin=geometry(xs[0]['origin']); result=[]
    for r in rows(xs[0]['targets']):
        g=origin.intersection(geometry(get(r,need(p,'geometry'))))
        result.append({**r,'intersection':mapping(g),'area':g.area,'origin_area':origin.area})
    return result


@operation('DeriveWeights')
def weights(xs,p):
    from shapely.ops import unary_union
    if need(p,'method')!='uniform-area' or need(p,'uniform_density') is not True:raise Refused('Area allocation requires explicit uniform-density assumption')
    data=rows(xs[0])
    if not data:raise Refused('No target areas')
    origin=number(data[0]['origin_area']); area=sum(number(r['area']) for r in data)
    if origin<=0 or not math.isclose(origin,area,rel_tol=1e-8):raise Refused('Target intersections do not partition origin area')
    # Also prove overlaps are absent; equal summed area alone is insufficient.
    from shapely.geometry import shape
    union_area=unary_union([shape(r['intersection']) for r in data]).area
    if not math.isclose(union_area,origin,rel_tol=1e-8):raise Refused('Overlapping allocation geometries')
    return [{**r,need(p,'output'):r['area']/origin} for r in data]


@operation('RepeatTraverse')
def traverse(xs,p):
    seeds=rows(xs[0]); relations=xs[1]; path=need(p,'path'); policy=need(p,'cycles')
    if len(path)<2 or len(path)!=len(relations) or policy not in ('stop','error','allow'):raise Refused('Traversal requires a finite relation path of length >=2 and cycle policy')
    frontier=[(get(r,need(p,'seed_key')),(get(r,p['seed_key']),)) for r in seeds]
    for relation,step in zip(relations,path):
        next_frontier=[]
        for current,visited in frontier:
            for edge in rows(relation):
                if get(edge,need(step,'from'))!=current:continue
                target=get(edge,need(step,'to'))
                if target in visited:
                    if policy=='error':raise Refused('Cycle in relation path')
                    if policy=='stop':continue
                next_frontier.append((target,(*visited,target)))
        frontier=next_frontier
    return [{need(p,'output'):target,'path':list(path)} for target,path in frontier]


def load_templates():
    from query_understanding import load_catalog
    shapes,_=load_catalog()
    return {s['id']:s for s in shapes}


def coverage():
    return {name:sorted({n['operator'] for n in shape['plan']['nodes']}-(READS|set(OPERATORS))) for name,shape in load_templates().items()}


def json_value(value):
    if isinstance(value,Groups):return [{'key':list(k),'rows':json_value(v)} for k,v in value.groups]
    if isinstance(value,dict):return {k:json_value(v) for k,v in value.items()}
    if isinstance(value,(list,tuple,set)):return [json_value(v) for v in value]
    return value


def admit(value, node, operator=None):
    if not isinstance(value,Input):raise Refused('Read adapter must return Input with evidence, not bare/model-generated data')
    if value.complete is not True or not value.provenance or not value.grain:
        raise Refused('Input '+node+' lacks complete-scope evidence or grain')
    if operator in ('ReadScalar','ResolveDate') and (value.data is None or isinstance(value.data,(dict,list))):raise Refused(operator+' requires one bound value')
    if operator=='ReadVersionedScalar' and (not isinstance(value.data,dict) or not {'value','vintage'}<=set(value.data)):raise Refused('Versioned input needs both value and vintage')
    if operator in ('ReadRows','ReadSeries','ReadMembership','ReadHierarchy','ReadSeeds','MapReadScalar','MapReadScalarPairs','MapReadRules','DiscoverDescriptors'):rows(value.data)
    if operator in ('MapReadSeries','MapReadRows'):
        if not isinstance(value.data,list):raise Refused('Mapped read needs a list of relations')
        for relation in value.data:rows(relation)
    return copy.deepcopy(value)


def check_alignment(operator, evidence, p):
    if operator in ('Join','LeftJoin','AntiJoin','AlignEntity','TemporalNeighborMatch','IntervalJoin'):
        left,right=evidence
        for l,r in zip(need(p,'left_keys'),need(p,'right_keys')):
            if not left.key_domains.get(l) or left.key_domains.get(l)!=right.key_domains.get(r):raise Refused('Join identity domains do not match; explicit crosswalk required')
    if operator in ('AlignTime','AlignEndpoints'):
        bases={v.period_basis for v in evidence}
        if len(bases)>1 or None in bases:raise Refused('Aligned inputs need matching explicit temporal basis')
    if operator in ('SpatialPredicate','Distance','SpatialIntersect'):
        crs=need(p,'crs')
        if any(v.provenance.get('crs')!=crs for v in evidence):raise Refused('Geometry CRS does not match; explicit adapter transform required')


def calculate(node, values, p):
    op=node['operator']; check_alignment(op,values,p)
    data=OPERATORS[op]([v.data for v in values],{**p,'_operator':op})
    domains={}; units={}
    for v in values:domains.update(v.key_domains);units.update(v.units)
    if op in ('Join','LeftJoin','AntiJoin','AlignEntity'):
        domains={**values[0].key_domains,**{p['right_prefix']+k:v for k,v in values[1].key_domains.items()}}
        units={**values[0].units,**{p['right_prefix']+k:v for k,v in values[1].units.items()}}
    return Input(data,all(v.complete for v in values),{'operator':op,'inputs':[v.provenance for v in values]},'derived',domains,units,
                 values[0].period_basis if values else None)


def _template(name,parameters):
    templates=load_templates()
    if name not in templates:raise Refused('Unknown catalog template: '+name)
    template=templates[name]
    allowed={n['id'] for n in template['plan']['nodes']}
    if set(parameters)-allowed:raise Refused('Parameters name nodes outside the fixed catalog DAG')
    return template


def synthesize(name, inputs, parameters):
    """Execute an entire catalog template on fixed adapter inputs, independently of LLMs."""
    template=_template(name,parameters); values={}; trace=[]
    for node in template['plan']['nodes']:
        ident=node['id']; op=node['operator']; p=parameters.get(ident,{})
        if op in READS:
            if ident not in inputs:raise Refused('Missing acquisition for node '+ident)
            values[ident]=admit(inputs[ident],ident,op)
        else:
            dependencies=[values[i] for i in node['inputs']]
            if op=='RepeatTraverse':dependencies.append(admit(inputs[ident],ident))
            values[ident]=calculate(node,dependencies,p)
        trace.append({'node':ident,'operator':op,'data':json_value(values[ident].data)})
    answer=values[template['plan']['output']]
    return {'template':name,'result':answer.data,'evidence':answer.provenance,'nodes':trace}


async def execute(name, parameters, reader, *, context):
    """The same interpreter, acquiring inputs only when their dependencies are ready.

    reader(node, parameters, dependencies, context=...) is trusted connector code.
    It must bind dependent read scopes from dependencies and attest actual evidence.
    """
    template=_template(name,parameters); values={}; trace=[]
    for node in template['plan']['nodes']:
        context.check()
        ident=node['id']; op=node['operator']; p=parameters.get(ident,{})
        dependencies=[values[i] for i in node['inputs']]
        if op in READS or op=='RepeatTraverse':
            await context.emit('input_started',index=len(trace),read={'question':p.get('question',op),'node':ident})
            acquired=admit(await reader(node,p,dependencies,context=context),ident,op)
            if op in READS:values[ident]=acquired
            else:values[ident]=calculate(node,[*dependencies,acquired],p)
            await context.emit('input_completed',index=len(trace),evidence=acquired.provenance)
        else:
            await context.emit('synthesis_started',shape=name,node=ident,operator=op)
            values[ident]=calculate(node,dependencies,p)
        trace.append({'node':ident,'operator':op,'data':json_value(values[ident].data)})
    answer=values[template['plan']['output']]
    return {'template':name,'result':answer.data,'evidence':answer.provenance,'nodes':trace}
