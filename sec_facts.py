"""Shared, descriptor-driven annual XBRL facts; no company or metric vocabulary."""
import datetime
import re

import answer_synthesizer as synth
import driver
import runtime


def select_annual(rows, period_type):
    eligible=[]
    for row in rows:
        if row.get('form') not in ('10-K','10-K/A','20-F','20-F/A') or row.get('fp') != 'FY':
            continue
        if not row.get('end') or not row.get('fy'):
            continue
        if period_type == 'duration':
            try:
                duration=(datetime.date.fromisoformat(row['end'])-datetime.date.fromisoformat(row['start'])).days
            except (KeyError,TypeError,ValueError):
                continue
            if not 300 <= duration <= 380:
                continue
        eligible.append(row)
    # FY on a comparative fact identifies the filing, not necessarily the fact.
    # Anchor each filing year to its latest end, then find the latest amendment
    # or comparative filing reporting that same period.
    ends={}
    for row in eligible:
        ends[row['fy']]=max(ends.get(row['fy'],''),row['end'])
    result=[]
    for fy,end in sorted(ends.items()):
        matching=[row for row in eligible if row['end']==end]
        chosen=max(matching,key=lambda row:row.get('filed') or '')
        result.append({**chosen,'fy':fy})
    return result


def setup(registry):
    registry.accessor('sec_company_facts',operators=('ReadScalar','ReadSeries','MapReadSeries'),
                     output_fields=('company','cik','fiscal_year','date','period_end','value','unit','metric'))(read)


async def read(request, *, context):
    import harness
    p=request.parameters.get('params',request.parameters)
    spec=request.descriptor.get('xbrl') or {}
    components=spec.get('components') or [spec]
    companies=p.get('companies') or p.get('company') or p.get('entity')
    if isinstance(companies,str):
        companies=[companies]
    if not isinstance(companies,list) or not companies:
        raise runtime.Refused('SEC acquisition requires a companies array')
    client=context.sec_client
    if client is None:
        if context.http_client is None:raise runtime.Refused('SEC requires an HTTP client')
        client=context.sec_client=driver.AsyncSecClient(context.http_client)
    all_rows=[]; receipts=[]
    for company in companies:
        entities=await harness._link_entity_async(
            {'entity':company,'type':'company','question':p.get('question') or str(company)},context=context)
        matches={str(entity['keys']['cik']):entity for entity in entities
                 if entity and (entity.get('keys') or {}).get('cik')}
        # An operating-company identity may carry a subsidiary CIK while its
        # consolidated financials are filed by a declared parent.  Keep the
        # direct identity first, then try only Wikidata-declared parent/owner
        # relationships; do not maintain company-name aliases here.
        import resolver
        for entity in entities:
            if not entity or not entity.get('qid'):continue
            try:
                parents=await resolver.reporting_parents_async(entity['qid'],context=context)
            except (runtime.QueryCancelled,):
                raise
            except Exception:
                parents=[]
            for parent in parents:
                cik=(parent.get('keys') or {}).get('cik')
                if cik:matches.setdefault(str(cik),parent)
        if not matches:
            # Match source-native identifiers/names supplied in the binding;
            # parenthesized canonical names are separate names, not a ticker.
            for name in [str(company),*re.findall(r'\(([^)]+)\)',str(company))]:
                try:
                    cik,title=await client.resolve_company(name,context)
                except runtime.Refused:
                    continue
                if cik:matches[str(cik)]={'label':title,'keys':{'cik':cik}}
                if matches:break
        if not matches:raise runtime.Refused('No canonical SEC identifier for '+str(company))
        answered=False
        for cik,entity in matches.items():
            payload=await client.company_facts(cik,context)
            if not isinstance(payload,dict):
                continue
            series=[]; tags=[]
            for component in components:
                taxonomy=component.get('taxonomy','us-gaap'); unit=component.get('unit','USD')
                by_year={}
                for tag in component.get('concepts') or [component.get('concept')]:
                    facts=(((payload.get('facts') or {}).get(taxonomy) or {}).get(tag) or {}).get('units',{}).get(unit,[])
                    for row in select_annual(facts,component.get('period_type','duration')):
                        previous=by_year.get(row['fy'])
                        if previous is None or (row['end'],row.get('filed') or '') > (previous['end'],previous.get('filed') or ''):
                            by_year[row['fy']]={**row,'concept':f'{taxonomy}:{tag}'}
                if not by_year:raise runtime.Refused('No annual facts for the declared concept')
                series.append(by_year);tags.append(sorted({row['concept'] for row in by_year.values()}))
            years=sorted(series[0])
            if p.get('fiscal_year') is not None:years=[int(p['fiscal_year'])]
            elif p.get('year_from') is not None:years=[year for year in years if year>=int(p['year_from'])]
            if p.get('year_to') is not None:years=[year for year in years if year<=int(p['year_to'])]
            if p.get('years'):years=years[-int(p['years']):]
            if p.get('period')=='latest':years=years[-1:]
            company_rows=[]
            for year in years:
                selected=[values.get(year+int(component.get('year_offset',0)))
                          for component,values in zip(components,series)]
                if any(row is None for row in selected):continue
                values=[row['val'] for row in selected]
                value=synth.expr(spec['expression'],inputs=values) if spec.get('expression') else values[0]
                row={'company':payload.get('entityName') or entity['label'],'cik':str(cik),
                     'fiscal_year':year,'date':year,'period_end':selected[0]['end'],
                     'value':value,'unit':spec.get('unit','USD'),'concepts':tags,
                     'metric':request.descriptor.get('title'),'facts':selected}
                company_rows.append(row)
            if not company_rows:raise runtime.Refused('No annual facts cover the requested fiscal period')
            all_rows.append(company_rows)
            receipts.append({'cik':cik,'entity':entity,'concepts':tags})
            answered=True
            # A direct filer wins.  A parent is only a fallback when the named
            # operating-company identity has no company-facts record.
            break
        if not answered:
            raise runtime.Refused('SEC has no company-facts record for resolved CIKs: '+
                                  ', '.join(matches))
    flat=[row for rows in all_rows for row in rows]
    return synth.Input(all_rows if (request.node or {}).get('operator')=='MapReadSeries' else flat,
        True,{'source':request.source,'provider':'SEC EDGAR','entities':receipts,
              'payload':{'rows':flat},'selection':'annual FY facts; latest filing for each fiscal period'},
        'company-fiscal-year',key_domains={'cik':'sec-cik','date':'fiscal-year','fiscal_year':'fiscal-year'},
        units={'value':spec.get('unit','USD')},period_basis='fiscal-year')
