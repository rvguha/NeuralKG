"""Fixed inputs and hand-written expected answers; no models, HTTP, or golden regeneration."""
from answer_synthesizer import Input, load_templates

def F(name):return {'field':name}
def I(n,field=None):return {'input':n,**({'field':field} if field is not None else {})}
def E(op,*args):return {'op':op,'args':list(args)}
def supplied(data,**overrides):
    return Input(data,True,{'source':'fixed-fixture','scope':'complete fixture','crs':'EPSG:3857'},'entity',
                 {'entity':'fixture-id','subject':'fixture-id','object':'fixture-id','group':'fixture-group','state':'fixture-state','time':'year'},
                 {'value':'USD'},'annual',**overrides)

R=[{'entity':'A','value':10,'group':'X'},{'entity':'B','value':20,'group':'X'},{'entity':'C','value':30,'group':'Y'}]
S=[{'entity':'A','value':20,'group':'X'},{'entity':'B','value':10,'group':'X'},{'entity':'C','value':60,'group':'Y'}]
SERIES=[{'time':1,'value':2},{'time':2,'value':4},{'time':3,'value':8}]
ORDER={'by':[{'field':'value','direction':'asc'}],'nulls':'error'}
TAKE={'limit':1,'ties':'all','tie_keys':['value']}
SUM={'method':'sum','field':'value','nulls':'error'}
QUANTILE={'field':'value','nulls':'error','q':.5,'method':'linear'}
JOIN={'left_keys':['entity'],'right_keys':['entity'],'cardinality':'one-to-one',
      'right_prefix':'r_','right_fields':['value'],'unmatched':'error'}
DEFAULTS={'Project':{'fields':'*'},'Assemble':{'fields':'*'},'Scalar':{'expression':I(0)},
          'Order':ORDER,'TimeOrder':{'by':[{'field':'time','direction':'asc'}],'nulls':'error'},'Take':TAKE,
          'Rank':{**ORDER,'method':'min','output':'rank'},'Filter':{'predicate':True},
          'Distinct':{'keys':['entity']},'DistinctIfRequested':{'keys':[]},'Group':{'keys':['group']},
          'GroupOrder':ORDER,'GroupTake':TAKE,'GroupCount':{'output':'value'},
          'StreamReduce':SUM,'GroupStreamReduce':{**SUM,'output':'value'},
          'OrderStatistics':QUANTILE,'GroupOrderStatistics':{**QUANTILE,'output':'value'},
          'Join':JOIN,'AlignEntity':JOIN,'LeftJoin':{**JOIN,'unmatched':'keep'},'AntiJoin':{**JOIN,'unmatched':'drop'},
          'MapScalar':{'rows_input':0,'output':'value','expression':F('value')},
          'Union':{'fields':['entity','value','group']}}

def cases():
    result={}; templates=load_templates()
    def add(name,inputs,expected,**bindings):
        params={n['id']:dict(DEFAULTS.get(n['operator'],{})) for n in templates[name]['plan']['nodes']}
        for ident,override in bindings.items():params[ident].update(override)
        result[name]={'inputs':{k:supplied(v) for k,v in inputs.items()},'parameters':params,'expected':expected}
    add('lookup.scalar',{'a':7},7)
    add('lookup.record',{'a':{'entity':'A','name':'Alpha','secret':'omit'}},{'name':'Alpha'},b={'fields':['name']})
    add('lookup.binary',{'a':30,'b':10},20,c={'expression':E('subtract',I(0),I(1))})
    add('lookup.version',{'a':{'value':7,'vintage':'2020'}},{'value':7,'vintage':'2020'})
    add('lookup.event-bound',{'a':2020,'b':7},7)
    add('lookup.version-comparison',{'a':{'value':7,'vintage':'2020'},'b':{'value':9,'vintage':'2021'}},2,c={'expression':E('subtract',I(1,'value'),I(0,'value'))})
    add('records.select',{'a':R},[{'entity':'C'}],b={'predicate':E('gt',F('value'),20)},c={'fields':['entity']})
    add('compare.values',{'a':R},R)
    add('compare.winner',{'a':R},[R[2]],b={'by':[{'field':'value','direction':'desc'}]})
    pairs=[{'entity':'A','numerator':20,'denominator':10},{'entity':'B','numerator':30,'denominator':10}]
    derived=[{**pairs[0],'value':2},{**pairs[1],'value':3}]
    formula={'expression':E('divide',F('numerator'),F('denominator'))}
    add('compare.derived-values',{'a':pairs},derived,b=formula)
    add('compare.derived-winner',{'a':pairs},[derived[1]],b=formula,c={'by':[{'field':'value','direction':'desc'}]})
    add('compare.derived-difference',{'a':pairs},-1,b=formula,c={'expression':E('subtract',I(0,'0.value'),I(0,'1.value'))})
    add('rank.population',{'a':R},[R[2]],b={'by':[{'field':'value','direction':'desc'}]})
    add('rank.named-position',{'a':R},[{'entity':'B','rank':2}],c={'predicate':E('eq',F('entity'),'B')},d={'before':0,'after':0},e={'fields':['entity','rank']})
    add('rank.reference-distance',{'a':17,'b':R},[{'entity':'B','value':3}],c={'rows_input':1,'expression':E('abs',E('subtract',F('value'),I(0)))},f={'fields':['entity','value']})
    add('rank.change',{'a':R,'b':S},[{'entity':'A','value':1,'group':'X','rank':1,'r_rank':2,'__matched':True}],
        e={'right_fields':['rank']},f={'expression':E('subtract',F('r_rank'),F('rank'))},g={'by':[{'field':'value','direction':'desc'}]})
    add('rank.percentile',{'a':20,'b':R},50,c={'field':'value','nulls':'error'},d={'expression':E('multiply',100,E('divide',E('add',I(0,'less'),E('multiply',.5,I(0,'equal'))),I(0,'total')))})
    add('rank.grouped',{'a':R},[R[0],R[2]])
    add('reduce.streaming',{'a':R},60)
    add('reduce.quantiles',{'a':R},20)
    add('reduce.distinct',{'a':[R[0],R[0],R[1]]},2)
    histogram=[{'bin':0,'value':1},{'bin':1,'value':2}]
    add('reduce.histogram',{'a':R},histogram,b={'field':'value','edges':[0,20,40],'include_last':True,'outside':'error','output':'bin'},c={'keys':['bin']})
    add('reduce.adaptive-histogram',{'a':R},histogram,b={'field':'value','nulls':'error','method':'equal-width','count':2},c={'field':'value','include_last':True,'outside':'error','output':'bin'},d={'keys':['bin']})
    grouped=[{'group':'X','value':30},{'group':'Y','value':30}]
    add('reduce.grouped',{'a':R},grouped)
    add('reduce.grouped-distinct',{'a':[R[0],R[0],R[1],R[2]]},[{'group':'X','value':2},{'group':'Y','value':1}])
    add('reduce.grouped-quantile',{'a':R},[{'group':'X','value':15},{'group':'Y','value':30}])
    membership=[{'entity':'A','bucket':'P','weight':.5},{'entity':'A','bucket':'Q','weight':.5},{'entity':'B','bucket':'Q','weight':1}]
    bridge={**JOIN,'cardinality':'one-to-many','right_fields':['bucket','weight']}
    add('reduce.allocated-groups',{'a':R[:2],'b':membership},[{'r_bucket':'P','value':5},{'r_bucket':'Q','value':25}],
        c=bridge,d={'keys':['entity','r_bucket']},e={'mode':'fractional','field':'value','weight':'r_weight','output':'value','contribution_keys':['entity']},f={'keys':['r_bucket']})
    add('reduce.bridge-distinct',{'a':R[:2],'b':membership},[{'r_bucket':'P','value':1},{'r_bucket':'Q','value':2}],c=bridge,d={'keys':['entity','r_bucket']},e={'keys':['r_bucket']})
    add('reduce.bridge-quantile',{'a':R[:2],'b':membership},[{'r_bucket':'P','value':10},{'r_bucket':'Q','value':15}],c=bridge,d={'keys':['entity','r_bucket']},e={'keys':['r_bucket']})
    add('reduce.group-shares',{'a':R},[{'group':'X','value':.5},{'group':'Y','value':.5}],e={'expression':E('divide',F('value'),I(1))})
    add('reduce.top-share',{'a':R},.5,b={'by':[{'field':'value','direction':'desc'}]},f={'expression':E('divide',I(0),I(1))})
    add('reduce.gini',{'a':R},2/9,c={'field':'value','nulls':'error'})
    add('reduce.entity-share',{'a':30,'b':R},.5,d={'expression':E('divide',I(0),I(1))})
    add('join.paired-reduce',{'a':R,'b':S},90,d={'field':'r_value'})
    timejoin={**JOIN,'left_keys':['time'],'right_keys':['time'],'right_fields':['value']}
    aligned=[{'time':1,'value':2,'r_value':2},{'time':2,'value':4,'r_value':4},{'time':3,'value':8,'r_value':8}]
    add('series.values',{'a':[SERIES,SERIES]},aligned,b={'joins':[timejoin]})
    add('series.streaming',{'a':SERIES},14)
    add('series.event-bound-reduction',{'a':1,'b':SERIES},14)
    add('series.quantiles',{'a':SERIES},4)
    add('series.growth-average',{'a':SERIES},1,b={'time':'time','field':'value','spacing':1},c={'expression':E('subtract',E('divide',F('current'),F('previous')),1)},d={'method':'mean'})
    add('series.window-comparison',{'a':SERIES[:1],'b':SERIES[1:]},10,e={'expression':E('subtract',I(1),I(0))})
    add('series.acceleration',{'a':SERIES},1,b={'time':'time','field':'value','spacing':1},c={'time':'time','field':'value','spacing':1},d={'method':'sign_of_mean_second_difference','field':'value','nulls':'error'})
    add('series.extreme-time',{'a':SERIES},[{'time':3}],b={'by':[{'field':'value','direction':'desc'}]},d={'fields':['time']})
    add('series.first-match',{'a':SERIES},[{'time':2,'value':4}],c={'predicate':E('gt',F('value'),2)},d={'tie_keys':['time']})
    add('quantify.witness',{'a':R},True,b={'predicate':E('gt',F('value'),25)})
    add('join.enrich',{'a':R,'b':S},[{'entity':'A','r_value':20},{'entity':'B','r_value':10},{'entity':'C','r_value':60}],e={'fields':['entity','r_value']})
    add('join.membership',{'a':R,'b':S[:1]},[R[0]],c={'keys':['entity']},d={'keys':['entity'],'mode':'in'})
    add('set.union',{'a':[R[:2],R[1:]]},R)
    add('set.symmetric-difference',{'a':R[:2],'b':R[1:]},[R[0],R[2]])
    endpoint=[JOIN,{**JOIN,'right_prefix':'t_'},{**JOIN,'right_prefix':'u_'}]
    change=E('divide',E('subtract',F('r_value'),F('value')),F('value'))
    second=E('divide',E('subtract',F('u_value'),F('t_value')),F('t_value'))
    add('join.double-change',{'a':R,'b':S,'c':S,'d':R},[{'entity':'A'},{'entity':'C'}],e={'joins':endpoint},f={'expressions':{'growth':change,'complaints':second}},g={'predicate':E('and',E('gt',F('growth'),.2),E('le',F('complaints'),-.15))},h={'fields':['entity']})
    add('join.change-and-level',{'a':R,'b':S,'c':R},[{'entity':'C'}],d={'joins':endpoint[:2]},e={'expressions':{'growth':change}},f={'predicate':E('and',E('gt',F('growth'),.2),E('gt',F('t_value'),15))},g={'fields':['entity']})
    add('join.filter-rank',{'a':R,'b':S},[{'entity':'C'}],d={'predicate':E('gt',F('r_value'),15)},e={'by':[{'field':'value','direction':'desc'}]},g={'fields':['entity']})
    add('join.derived-filter',{'a':R,'b':S},[{'entity':'B'}],d={'expression':E('divide',F('value'),F('r_value'))},e={'predicate':E('gt',F('value'),1)},f={'fields':['entity']})
    add('join.related-count',{'a':R,'b':[{'entity':'A'},{'entity':'A'},{'entity':'B'}]},[{'entity':'A','r_value':2},{'entity':'B','r_value':1},{'entity':'C','r_value':0}],c={'keys':['entity']},f={'field':'r_value'},h={'fields':['entity','r_value']})
    add('join.grouped-measures',{'a':R,'b':S},[{'group':'X','value':30,'r_value':30,'__matched':True},{'group':'Y','value':30,'r_value':60,'__matched':True}],g={'left_keys':['group'],'right_keys':['group']})
    add('join.division',{'a':R,'b':[{'subject':'A','object':'CA'},{'subject':'A','object':'NY'},{'subject':'B','object':'CA'}],'c':[{'state':'CA'},{'state':'NY'}]},[{'entity':'A'}],d={'candidate_key':'entity','subject_key':'subject','object_key':'object','required_key':'state'},e={'keys':['entity']},f={'required_key':'state','empty_set_policy':'vacuous'})
    edges=[[{'from':'A','to':'B'},{'from':'A','to':'C'}],[{'from':'B','to':'D'},{'from':'C','to':'D'}]]
    add('join.path',{'a':[{'entity':'A'}],'b':edges},[{'entity':'D'}],b={'path':[{'from':'from','to':'to'},{'from':'from','to':'to'}],'cycles':'stop','seed_key':'entity','output':'entity'},c={'keys':['entity']},d={'fields':['entity']})
    hierarchy=[{'parent':'root','child':'A'},{'parent':'root','child':'B'}]
    traversal={'root':'root','parent':'parent','child':'child','max_depth':1,'cycles':'error','include_root':False}
    add('hierarchy.rollup',{'a':hierarchy,'c':R[:2]},30,b=traversal)
    consolidated=[{'entity':'root','value':100,'scope':['A','B','root']},{'entity':'A','value':10,'scope':['A']},{'entity':'B','value':20,'scope':['B']}]
    add('hierarchy.exclusive',{'a':hierarchy,'c':consolidated},70,b={**traversal,'include_root':True},d={'root':'root','entity':'entity','scope':'scope','field':'value'},e={'expression':E('subtract',I(0,'total'),I(0,'excluded'))})
    correlation={'method':'pearson','fields':['value','r_value']}
    add('assoc.series',{'a':SERIES,'b':SERIES},1,c={'time':'time','offset':0},d={'joins':[timejoin]},e=correlation)
    add('assoc.series-ranks',{'a':SERIES,'b':SERIES},1,c={'time':'time','offset':0},d={'joins':[timejoin]},e={'columns':{'value':'value','r_value':'r_value'},'method':'average'},f=correlation)
    add('assoc.population-ranks',{'a':R,'b':S},.5,d={'columns':{'value':'value','r_value':'r_value'},'method':'average'},e=correlation)
    add('assoc.group-statistic',{'a':R,'b':S},10,c={'method':'mean'},d={'method':'mean'},e={'expression':E('subtract',I(1),I(0))})
    add('assoc.group-test',{'a':R,'b':R},{'statistic':0,'pvalue':1,'df':4,'alpha':.05,'reject_null':False},c={'method':'welch_two_sample_t','alpha':.05,'alternative':'two-sided','field':'value','nulls':'error'})
    fitrows=[{'entity':'A','value':1},{'entity':'B','value':2},{'entity':'C','value':3},{'entity':'D','value':4}]
    targets=[{'entity':'A','value':2},{'entity':'B','value':4},{'entity':'C','value':6},{'entity':'D','value':10}]
    add('assoc.fitted-outliers',{'a':fitrows,'b':targets},[{'entity':'D','value':4,'r_value':10,'__matched':True,'residual':.6}],d={'method':'ols','features':['value'],'target':'r_value','intercept':True},e={'target':'r_value','output':'residual'},f={'predicate':E('gt',F('residual'),.5)})
    add('assoc.baseline-outliers',{'a':R},[{**R[2],'residual':10}],b={'expression':20},c={'target':'value','output':'residual'},d={'predicate':E('gt',F('residual'),5)})
    add('meta.available',{'a':[{'name':'income'},{'name':'age'}]},True,b={'predicate':E('eq',F('name'),'income')})
    add('meta.count',{'a':[{'name':'income'},{'name':'age'}]},2)
    add('meta.definition',{'a':{'description':'Annual gross income','source':'fixture'}},{'description':'Annual gross income'},b={'fields':['description']})
    rules=[{'entity':'A','rule':E('ge',F('age'),18)},{'entity':'B','rule':E('eq',F('state'),'CA')}]
    add('eligibility.evaluate',{'a':R[:2],'b':rules},[{'entity':'A','eligible':True}],c={'rule_key':'entity','entity_key':'entity','rule_field':'rule','actor':{'age':20},'output':'eligible'},d={'predicate':E('eq',F('eligible'),True)},e={'fields':['entity','eligible']})
    temporal_left=[{'entity':'A','time':5,'start':1,'end':4}]
    temporal_right=[{'entity':'A','time':4,'start':4,'end':6,'value':7},{'entity':'A','time':6,'start':3,'end':5,'value':8}]
    tp={**JOIN,'direction':'backward','left_time':'time','right_time':'time','tolerance':2,'ties':'all','unmatched':'drop'}
    add('temporal.match',{'a':temporal_left,'b':temporal_right},[{**temporal_left[0],'r_value':7}],c=tp)
    add('temporal.overlap',{'a':temporal_left,'b':temporal_right},[{**temporal_left[0],'r_value':8}],c={**JOIN,'left_start':'start','left_end':'end','right_start':'start','right_end':'end','unbounded':False,'closed':'left','unmatched':'drop'})
    add('temporal.sequence',{'a':temporal_left,'b':temporal_right[:1]},[{'entity':'A'}],c={'right_fields':['time']},d={'predicate':E('gt',F('time'),F('r_time'))},e={'fields':['entity']})
    def point(x,y):return {'type':'Point','coordinates':[x,y]}
    def box(x1,y1,x2,y2):return {'type':'Polygon','coordinates':[[[x1,y1],[x2,y1],[x2,y2],[x1,y2],[x1,y1]]]}
    places=[{'entity':'A','geometry':point(0,0)},{'entity':'B','geometry':point(3,4)},{'entity':'C','geometry':point(6,8)}]
    dp={'crs':'EPSG:3857','method':'projected','unit':'m','geometry':'geometry','output':'distance'}
    add('spatial.contains',{'a':box(-1,-1,4,5),'b':places},[{'entity':'A'},{'entity':'B'}],c={'crs':'EPSG:3857','method':'contains','geometry':'geometry','output':'inside'},d={'predicate':F('inside')},e={'fields':['entity']})
    add('spatial.radius',{'a':point(0,0),'b':places},[{'entity':'A','distance':0},{'entity':'B','distance':5}],c=dp,d={'predicate':E('le',F('distance'),5)},e={'fields':['entity','distance']})
    add('spatial.nearest',{'a':point(1,0),'b':places},[{'entity':'A','distance':1}],c=dp,d={'by':[{'field':'distance','direction':'asc'}]},e={'tie_keys':['distance']},f={'fields':['entity','distance']})
    targets=[{'entity':'L','origin':'O','geometry':box(0,0,1,1)},{'entity':'R','origin':'O','geometry':box(1,0,2,1)}]
    # Allocation returns geometries and evidence too; assertion projects only the computed allocations.
    add('spatial.allocate',{'a':100,'b':{'origin':box(0,0,2,1),'targets':targets}},[50,50],c={'crs':'EPSG:3857','geometry':'geometry'},d={'method':'uniform-area','uniform_density':True,'output':'weight'},e={'mode':'fractional','field':'unused','weight':'weight','output':'allocation','contribution_keys':['origin']})
    result['spatial.allocate']['assert_fields']=['allocation']
    return result
