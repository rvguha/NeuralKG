"""Atlas Data Commons v2 accessors using NeuralKG's async HTTP/cancellation context."""
import json
import os
import re
import urllib.parse

import httpx

import answer_synthesizer as synth
import instance
import runtime

INDICATORS={
 'population':'Count_Person','adult_population':'Count_Person_18OrMoreYears','median_household_income':'Median_Income_Household','median_age':'Median_Age_Person',
 'unemployment_rate':'UnemploymentRate_Person','gdp':'Amount_EconomicActivity_GrossDomesticProduction_Nominal',
 'gdp_per_capita':'Amount_EconomicActivity_GrossDomesticProduction_Nominal_PerCapita','life_expectancy':'LifeExpectancy_Person',
 'fertility_rate':'FertilityRate_Person_Female','diabetes_prevalence':'Percent_Person_WithDiabetes',
 'obesity_prevalence':'Percent_Person_Obesity','co2_emissions':'Amount_Emissions_CarbonDioxide',
 'co2_emissions_per_capita':'Amount_Emissions_CarbonDioxide_PerCapita'}
SYNONYMS={'people':'population','residents':'population','adult population':'adult_population',
 'population age 18 and older':'adult_population','people age 18 and older':'adult_population','median household income':'median_household_income',
 'household income':'median_household_income','median income':'median_household_income','median age':'median_age',
 'unemployment':'unemployment_rate','unemployment rate':'unemployment_rate','gdp per capita':'gdp_per_capita',
 'gross domestic product':'gdp','life expectancy':'life_expectancy','fertility':'fertility_rate',
 'fertility rate':'fertility_rate','diabetes':'diabetes_prevalence','obesity':'obesity_prevalence',
 'co2 per capita':'co2_emissions_per_capita','co2 emissions':'co2_emissions'}
PLACE_TYPES={'country':'Country','countries':'Country','state':'State','states':'State','province':'State',
 'provinces':'State','county':'County','counties':'County','city':'City','cities':'City','town':'City',
 'towns':'City','continent':'Continent','continents':'Continent'}
KNOWN={'world':'Earth','earth':'Earth','global':'Earth','united states':'country/USA','usa':'country/USA',
 'us':'country/USA','u.s.':'country/USA','america':'country/USA','africa':'africa','europe':'europe',
 'asia':'asia','north america':'northamerica','south america':'southamerica','oceania':'oceania'}


def setup(registry):
    fields=('place','place_dcid','date','year','value','unit','source')
    registry.accessor('datacommons_place',operators=('ReadScalar','ReadSeries','MapReadSeries'),output_fields=fields)(place)
    registry.accessor('datacommons_children',operators=('ReadRows',),output_fields=fields+('rank',))(children)


def config(): return instance.config().get('plugin_config',{}).get('atlas_datacommons',{})
def norm(value): return re.sub(r'\s+',' ',str(value or '').strip().casefold())
def place_grain(places,hint=None):
    explicit=norm(hint).rstrip('s')
    if explicit in ('country','state','county','city','continent'):return explicit
    inferred=[]
    for place in places:
        dcid=str(place.get('dcid') or '')
        if dcid.startswith('country/'):inferred.append('country')
        elif dcid.startswith('geoId/'):
            digits=dcid.split('/',1)[1]
            inferred.append({2:'state',5:'county',7:'city'}.get(len(digits),'place'))
        else:inferred.append('place')
    return inferred[0] if inferred and len(set(inferred))==1 else 'place'
def reported_unit(indicator, facet):
    raw=(facet or {}).get('raw') or {}
    unit=raw.get('unit')
    if not unit and (str(indicator.get('dcid') or '').startswith('Percent_') or
                     re.search(r'\bpercent(?:age)?\b',str(indicator.get('name') or ''),re.I)):
        return 'percent'
    if not unit and str(indicator.get('dcid') or '').startswith('Count_'):
        return 'count'
    return unit
def params(read):
    value=read.parameters.get('params',read.parameters)
    if not isinstance(value,dict):return {}
    value=dict(value);value.setdefault('place',value.get('entity'))
    # The fixed-template path names this coordinate ``measure``; the established
    # point-retrieval frame names the same coordinate ``attribute``. Both invoke
    # this one accessor, so normalize the two interface spellings here.
    value.setdefault('indicator',value.get('measure') or value.get('attribute'))
    return value


def requested_year(value):
    raw=value.get('year')
    if raw in (None,'') and re.fullmatch(r'\d{4}',str(value.get('period') or '')):
        raw=value['period']
    return int(raw) if raw not in (None,'') else None


class Client:
    def __init__(self, context):
        self.context=context; self.cfg=config(); self.base=self.cfg.get('api_base','https://api.datacommons.org/v2').rstrip('/')
        self.key=self.cfg.get('api_key') or os.getenv(self.cfg.get('api_key_env','DC_API_KEY'),'')
        # This is a terminal server-credential failure, not evidence that a lower-ranked
        # source should silently replace Data Commons.  AccessDenied deliberately bypasses
        # the ordinary candidate backtracking and compatibility fallback paths.
        if not self.key: raise runtime.AccessDenied('Data Commons API key is not configured')
        if context.http_client is None: raise runtime.Refused('Data Commons requires an async HTTP client')
        self.max_entities=int(self.cfg.get('max_entities',3500)); self.max_rows=int(self.cfg.get('max_rows',5000))
        self.max_bytes=int(self.cfg.get('max_response_bytes',32*1024*1024)); self.max_pages=int(self.cfg.get('max_pages',20))

    async def request(self, method, path, *, query=None, body=None):
        cache=self.context.memo.setdefault('_datacommons_cache',{})
        key=json.dumps([method,path,query,body],sort_keys=True)
        if key in cache:return cache[key]
        headers={'X-API-Key':self.key,'Accept':'application/json'}
        kwargs={'headers':headers,'timeout':min(float(self.cfg.get('timeout_seconds',20)),self.context.remaining() or 20)}
        if method=='GET':kwargs['params']={**(query or {}),'key':self.key}
        else:kwargs['json']=body or {}
        response=await self.context.provider_call('datacommons',lambda:self.context.http_client.request(method,self.base+'/'+path.lstrip('/'),**kwargs))
        if response.status_code in (401,403):raise runtime.AccessDenied('Data Commons rejected its configured API key')
        if response.status_code==429:raise runtime.Refused('Data Commons rate limit reached')
        response.raise_for_status()
        if len(response.content)>self.max_bytes:raise runtime.Refused('Data Commons response exceeds configured size cap')
        try:value=response.json()
        except ValueError as exc:raise runtime.Refused('Data Commons returned invalid JSON') from exc
        cache[key]=value;return value

    async def pages(self,path,body):
        pages=[];token=None
        for _ in range(self.max_pages):
            page=await self.request('POST',path,body={**body,**({'nextToken':token} if token else {})})
            pages.append(page);token=page.get('nextToken')
            if not token:return pages
        raise runtime.Refused('Data Commons pagination exceeded configured page cap')

    async def names(self,dcids):
        out={}
        for start in range(0,len(dcids),500):
            chunk=dcids[start:start+500];resp=await self.request('POST','node',body={'nodes':chunk,'property':'->name'})
            data=resp.get('data') or {}
            for dcid in chunk:
                nodes=((((data.get(dcid) or {}).get('arcs') or {}).get('name') or {}).get('nodes') or [])
                out[dcid]=str(nodes[0].get('value')) if nodes and nodes[0].get('value') is not None else dcid
        return out

    async def resolve_place(self,name,type_hint=None):
        raw=re.sub(r'^the\s+','',str(name or '').strip(),flags=re.I)
        if not raw:raise runtime.Refused('No place was supplied')
        if raw.startswith(('geoId/','country/','wikidataId/','nuts/')) or raw=='Earth':dcid=raw
        elif norm(raw) in KNOWN:dcid=KNOWN[norm(raw)]
        else:
            dc_type=PLACE_TYPES.get(norm(type_hint),type_hint if re.fullmatch(r'[A-Z][A-Za-z0-9]+',str(type_hint or '')) else None)
            prop=f'<-description{{typeOf:{dc_type}}}->dcid' if dc_type else '<-description->dcid'
            result=await self.request('GET','resolve',query={'nodes':raw,'property':prop})
            candidates=[c for e in result.get('entities',[]) for c in e.get('candidates',[])]
            if not candidates and dc_type:
                result=await self.request('GET','resolve',query={'nodes':raw,'property':'<-description->dcid'})
                candidates=[c for e in result.get('entities',[]) for c in e.get('candidates',[])]
            if not candidates:raise runtime.Refused('Data Commons could not resolve place: '+raw)
            dcid=candidates[0].get('dcid')
            if not dcid:raise runtime.Refused('Data Commons place resolution returned no identifier')
        canonical=(await self.names([dcid]))[dcid]
        return {'dcid':dcid,'name':canonical,'as_written':raw}

    async def resolve_indicator(self,text,entities):
        key=str(text or '').strip().casefold().replace('_',' '); curated=SYNONYMS.get(key,key.replace(' ','_'))
        candidates=[]
        if curated in INDICATORS:candidates.append({'dcid':INDICATORS[curated],'via':'curated:'+curated})
        try:
            result=await self.request('GET','resolve',query={'nodes':text,'resolver':'indicator'})
        except (httpx.HTTPError, runtime.Refused):
            if not candidates:raise
            result={}
        for item in [c for e in result.get('entities',[]) for c in e.get('candidates',[])]:
            dcid=item.get('dcid')
            if dcid and not dcid.startswith('dc/topic/') and dcid not in [c['dcid'] for c in candidates]:
                candidates.append({'dcid':dcid,'via':'resolver','score':item.get('score')})
        if not candidates:raise runtime.Refused('Data Commons could not resolve indicator: '+str(text))
        sample=list(dict.fromkeys(entities))[:50]
        pages=await self.pages('observation',{'date':'','variable':{'dcids':[c['dcid'] for c in candidates[:8]]},
                                             'entity':{'dcids':sample},'select':['entity','variable']})
        merged=merge(pages);available=(merged.get('byVariable') or {})
        chosen=next((c for c in candidates[:8] if (available.get(c['dcid']) or {}).get('byEntity')),None)
        if not chosen:raise runtime.Refused('Data Commons has no matching observation for the requested places')
        # ``chosen`` is one of ``candidates``. Mutating it with the candidates list made the
        # returned object contain itself (chosen -> considered -> chosen), which blew up evidence
        # serialization after an otherwise successful provider call.
        result=dict(chosen);result['name']=(await self.names([chosen['dcid']]))[chosen['dcid']]
        result['considered']=[dict(candidate) for candidate in candidates[:8]]
        return result

    async def observations(self,variable,entities,year=None,year_from=None,year_to=None,latest=False):
        if len(entities)>self.max_entities:raise runtime.Refused('Data Commons entity cap exceeded')
        dcids=[e['dcid'] for e in entities];names={e['dcid']:e['name'] for e in entities}
        date=str(year) if year else ('LATEST' if latest and not (year_from or year_to) else '')
        merged=merge(await self.pages('observation',{'date':date,'variable':{'dcids':[variable]},
            'entity':{'dcids':dcids},'select':['entity','variable','date','value']}))
        facets=merged.get('facets') or {};by_entity=(((merged.get('byVariable') or {}).get(variable) or {}).get('byEntity') or {})
        coverage={};rank={}
        for block in by_entity.values():
            for i,item in enumerate(block.get('orderedFacets') or []):
                fid=str(item.get('facetId'));coverage[fid]=coverage.get(fid,0)+1;rank[fid]=min(rank.get(fid,999),i)
        if not coverage:return {'rows':[],'facet':None,'facets_available':[]}
        fid=sorted(coverage,key=lambda x:(-coverage[x],rank[x],x))[0];meta=facets.get(fid) or {}
        source=meta.get('importName') or urllib.parse.urlparse(str(meta.get('provenanceUrl') or '')).netloc or meta.get('measurementMethod')
        rows=[]
        for entity,block in by_entity.items():
            chosen=next((x for x in block.get('orderedFacets') or [] if str(x.get('facetId'))==fid),None)
            observations=(chosen or {}).get('observations') or [];kept=[]
            for observation in observations:
                match=re.match(r'(\d{4})',str(observation.get('date') or ''));y=int(match.group(1)) if match else None
                if year and y!=year:continue
                if year_from and (y is None or y<year_from):continue
                if year_to and (y is None or y>year_to):continue
                kept.append(observation)
            if latest and kept:kept=[max(kept,key=lambda x:str(x.get('date')))]
            for observation in kept:rows.append({'place':names.get(entity,entity),'place_dcid':entity,'date':observation.get('date'),
                'year':int(str(observation['date'])) if re.fullmatch(r'\d{4}',str(observation.get('date'))) else None,
                'value':observation.get('value'),'unit':meta.get('unit'),'source':source,
                'measurement_method':meta.get('measurementMethod'),'observation_period':meta.get('observationPeriod'),
                'provenance_url':meta.get('provenanceUrl')})
        if len(rows)>self.max_rows:raise runtime.Refused('Data Commons row cap exceeded')
        return {'rows':sorted(rows,key=lambda x:(x['place'],str(x['date']))),
                'facet':{'facet_id':fid,'source':source,'raw':meta,'covers_places':coverage[fid]},
                'facets_available':[{'facet_id':x,'covers_places':coverage[x],'raw':facets.get(x) or {}} for x in sorted(coverage,key=lambda x:(-coverage[x],rank[x],x))]}


def merge(pages):
    result={'byVariable':{},'facets':{}}
    for page in pages:
        result['facets'].update(page.get('facets') or {})
        for variable,block in (page.get('byVariable') or {}).items():
            target=result['byVariable'].setdefault(variable,{'byEntity':{}})['byEntity']
            for entity,value in (block.get('byEntity') or {}).items():
                if entity in target:target[entity].setdefault('orderedFacets',[]).extend(value.get('orderedFacets') or [])
                else:target[entity]=value
    return result


async def place(read,*,context):
    p=params(read);client=Client(context);names=p.get('places') or p.get('place')
    if isinstance(names,str):names=[x.strip() for x in re.split(r';|\s+and\s+|\s+vs\.?\s+',names) if x.strip()]
    if not names:raise runtime.Refused('Data Commons place is required')
    places=[await client.resolve_place(name,p.get('place_type')) for name in names]
    indicator=await client.resolve_indicator(p.get('indicator'),[x['dcid'] for x in places])
    year=requested_year(p)
    start=int(p['year_from']) if p.get('year_from') not in (None,'') else None
    end=int(p['year_to']) if p.get('year_to') not in (None,'') else None
    observed=await client.observations(indicator['dcid'],places,year,start,end,
                                      latest=norm(p.get('period'))=='latest' and not (year or start or end))
    unit=reported_unit(indicator,observed['facet'])
    for row in observed['rows']:
        if row.get('unit') is None:row['unit']=unit
    data={'rows':[{'variable':indicator['name'],**r} for r in observed['rows']],
          'params':{'indicator':p.get('indicator'),'variable_dcid':indicator['dcid'],'place_dcids':[x['dcid'] for x in places]},
          'considered':indicator['considered'],'facet':observed['facet'],'facets_available':observed['facets_available']}
    result_rows=data['rows']
    if (read.node or {}).get('operator')=='MapReadSeries':
        # A mapped-series acquisition returns one complete relation per requested entity.
        # Keep the request order so the fixed AlignTime contracts bind deterministically.
        result_rows=[[row for row in data['rows'] if row.get('place_dcid')==item['dcid']]
                     for item in places]
    return synth.Input(result_rows,True,{'source':read.source,'provider':'Data Commons v2','resolved':data['params'],
                       'facet':data['facet'],'payload':data},
                       place_grain(places,p.get('place_type')),key_domains={'place_dcid':'datacommons-place','year':'calendar-year'},
                       units={'value':unit},period_basis='source-reported')


async def children(read,*,context):
    p=params(read);client=Client(context);parent=await client.resolve_place(p.get('parent_place'),p.get('parent_type'))
    child_type=PLACE_TYPES.get(norm(p.get('child_type')),p.get('child_type') or 'County');nodes=[]
    for page in await client.pages('node',{'nodes':[parent['dcid']], 'property':f'<-containedInPlace+{{typeOf:{child_type}}}'}):
        arcs=(((page.get('data') or {}).get(parent['dcid']) or {}).get('arcs') or {})
        nodes.extend(n for arc in arcs.values() for n in arc.get('nodes',[]) if n.get('dcid'))
    unique={n['dcid']:n.get('name') or n['dcid'] for n in nodes}
    if len(unique)>client.max_entities:raise runtime.Refused('Data Commons entity cap exceeded')
    missing=[k for k,v in unique.items() if k==v]
    if missing:unique.update(await client.names(missing))
    places=[{'dcid':k,'name':v} for k,v in unique.items()]
    if not places:return synth.Input({'rows':[],'map_rows':[],'parent':parent},True,{'source':read.source},'place-ranking')
    indicator=await client.resolve_indicator(p.get('indicator'),list(unique))
    year=requested_year(p)
    observed=await client.observations(indicator['dcid'],places,year=year,latest=True)
    unit=reported_unit(indicator,observed['facet'])
    for row in observed['rows']:
        if row.get('unit') is None:row['unit']=unit
    rows=[{'variable':indicator['name'],**r} for r in observed['rows'] if isinstance(r.get('value'),(int,float))]
    rows.sort(key=lambda x:x['value'],reverse=norm(p.get('order','desc'))!='asc')
    for i,row in enumerate(rows,1):row['rank']=i
    top=min(int(p.get('top_n') or 25),500)
    # A DAG needs the whole input relation: the downstream Take owns top-N.
    selected=rows if (read.node or {}).get('operator')=='ReadRows' else rows[:top]
    missing=sorted(set(unique)-{row['place_dcid'] for row in rows})
    data={'rows':selected,'map_rows':rows,'parent':parent,'facet':observed['facet'],
        'coverage':{'enumerated_places':len(places),'observed_places':len(rows),'missing_place_dcids':missing,
                    'scope':'places with observations from the selected source facet'},
        'params':{'variable_dcid':indicator['dcid'],'child_type':child_type,'places_in_parent':len(places),'top_n':top}}
    return synth.Input(data['rows'],True,{'source':read.source,'provider':'Data Commons v2','facet':data['facet'],'payload':data},
                       child_type.lower(),key_domains={'place_dcid':'datacommons-place','year':'calendar-year'},
                       units={'value':unit},period_basis='source-reported')
