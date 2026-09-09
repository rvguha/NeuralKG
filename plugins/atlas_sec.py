"""Atlas SEC EDGAR metric/annual/ratio accessors on NeuralKG's async SEC client."""
import datetime

import answer_synthesizer as synth
import driver
import runtime


METRICS = {
 'revenue': ('us-gaap', ('Revenues','RevenueFromContractWithCustomerExcludingAssessedTax','RevenueFromContractWithCustomerIncludingAssessedTax'), 'USD','duration'),
 'net_income': ('us-gaap',('NetIncomeLoss',),'USD','duration'),
 'operating_income': ('us-gaap',('OperatingIncomeLoss',),'USD','duration'),
 'interest_expense': ('us-gaap',('InterestExpense',),'USD','duration'),
 'income_tax_expense': ('us-gaap',('IncomeTaxExpenseBenefit',),'USD','duration'),
 'net_interest_income': ('us-gaap',('InterestIncomeExpenseNet','InterestIncomeExpenseAfterProvisionForLoanLoss'),'USD','duration'),
 'provision_for_credit_losses': ('us-gaap',('ProvisionForLoanLeaseAndOtherLosses','ProvisionForLoanLossesExpensed','ProvisionForCreditLosses'),'USD','duration'),
 'noninterest_expense': ('us-gaap',('NoninterestExpense',),'USD','duration'),
 'noninterest_income': ('us-gaap',('NoninterestIncome',),'USD','duration'),
 'eps_diluted': ('us-gaap',('EarningsPerShareDiluted',),'USD/shares','duration'),
 'operating_cash_flow': ('us-gaap',('NetCashProvidedByUsedInOperatingActivities',),'USD','duration'),
 'capital_expenditures': ('us-gaap',('PaymentsToAcquirePropertyPlantAndEquipment',),'USD','duration'),
 'total_assets': ('us-gaap',('Assets',),'USD','instant'),
 'total_liabilities': ('us-gaap',('Liabilities',),'USD','instant'),
 'stockholders_equity': ('us-gaap',('StockholdersEquity','StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest'),'USD','instant'),
 'cash_and_equivalents': ('us-gaap',('CashAndCashEquivalentsAtCarryingValue','CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'),'USD','instant'),
 'deposits': ('us-gaap',('Deposits',),'USD','instant'),
 'loans': ('us-gaap',('LoansAndLeasesReceivableNetReportedAmount','NotesReceivableNet','FinancingReceivableExcludingAccruedInterestAfterAllowanceForCreditLoss'),'USD','instant'),
 'long_term_debt': ('us-gaap',('LongTermDebt','LongTermDebtNoncurrent'),'USD','instant'),
 'shares_outstanding': ('dei',('EntityCommonStockSharesOutstanding',),'shares','instant'),
}
RATIOS = {
 'roa': ('net_income','total_assets',True,None,'Net income / average total assets'),
 'roe': ('net_income','stockholders_equity',True,None,"Net income / average stockholders' equity"),
 'net_margin': ('net_income','revenue',False,None,'Net income / revenue'),
 'efficiency_ratio': ('noninterest_expense','net_interest_income',False,'noninterest_income','Noninterest expense / (net interest income + noninterest income)'),
 'equity_to_assets': ('stockholders_equity','total_assets',False,None,"Stockholders' equity / total assets"),
}


def setup(registry):
    registry.accessor('sec_edgar')(metric)
    registry.accessor('sec_edgar_annual')(annual)
    registry.accessor('sec_ratio')(ratio)


def params(read):
    value = read.parameters.get('params', read.parameters)
    if not isinstance(value, dict): return {}
    value = dict(value)
    value.setdefault('company', value.get('entity'))
    value.setdefault('metric', value.get('measure') or value.get('attribute'))
    period = value.get('period')
    if value.get('fiscal_year') in (None, '') and str(period or '').isdigit():
        value['fiscal_year'] = int(period)
    return value


async def client_and_company(company, context):
    client = context.sec_client
    if client is None:
        if context.http_client is None: raise runtime.Refused('SEC accessor requires an async HTTP client')
        client = context.sec_client = driver.AsyncSecClient(context.http_client)
    cik, title = await client.resolve_company(company, context)
    facts = await client.company_facts(cik, context)
    if not facts: raise runtime.Refused('SEC company-facts record is unavailable')
    return client, cik, facts.get('entityName') or title, facts


def facts_for(payload, metric_name, year=None):
    if metric_name not in METRICS: raise runtime.Refused('Unknown curated SEC metric: ' + str(metric_name))
    taxonomy, tags, unit, kind = METRICS[metric_name]
    rows, used, seen = [], [], set()
    for tag in tags:
        values = ((((payload.get('facts') or {}).get(taxonomy) or {}).get(tag) or {}).get('units') or {}).get(unit, [])
        for value in values:
            if year is not None and value.get('fy') != year: continue
            key = tuple(value.get(k) for k in ('end','start','fy','fp','form','val'))
            if key in seen: continue
            seen.add(key); used.append(tag)
            rows.append({'value': value.get('val'), 'unit': unit, 'period_end': value.get('end'),
                         'period_start': value.get('start'), 'fiscal_year': value.get('fy'),
                         'fiscal_period': value.get('fp'), 'form': value.get('form'), 'filed': value.get('filed')})
    rows.sort(key=lambda x: (x.get('fiscal_year') or 0, x.get('period_end') or ''))
    return rows, f"{taxonomy}:" + '|'.join(dict.fromkeys(used or tags[:1])), kind


def annual_rows(rows, kind, years=None, fiscal_year=None):
    candidates=[]
    for row in rows:
        if row.get('form') not in ('10-K','10-K/A') or row.get('fiscal_period') != 'FY' or not row.get('period_end'): continue
        if kind == 'duration':
            try:
                if (datetime.date.fromisoformat(row['period_end']) - datetime.date.fromisoformat(row['period_start'])).days < 300: continue
            except (TypeError, ValueError): continue
        candidates.append(row)
    if fiscal_year is not None:
        candidates = [r for r in candidates if r.get('fiscal_year') == fiscal_year]
        if not candidates: return []
        end = max(r['period_end'] for r in candidates)
        return [{**max((r for r in candidates if r['period_end'] == end), key=lambda x: x.get('filed') or ''),
                 'source': 'sec_edgar_api'}]
    by_year={}
    for row in candidates:
        label = row.get('fiscal_year') or int(row['period_end'][:4])
        previous=by_year.get(label)
        if previous is None or (row['period_end'],row.get('filed') or '') > (previous['period_end'],previous.get('filed') or ''):
            by_year[label]=row
    result=[by_year[k] for k in sorted(by_year)]
    return result[-int(years):] if years else result


async def fetch(company, metric_name, context, year=None, annual_only=False, years=None):
    _, cik, entity, payload = await client_and_company(company, context)
    rows, concept, kind = facts_for(payload, metric_name, year if not annual_only else None)
    if annual_only: rows = annual_rows(rows, kind, years=years, fiscal_year=year)
    return {'rows': rows, 'cik': str(int(cik)).zfill(10), 'entity_name': entity, 'concept': concept,
            'selection_rule': '10-K/FY; duration at least 300 days; latest filing' if annual_only else 'reported facts'}


def result(read, data):
    return synth.Input(data['rows'], True, {'source': read.source, 'provider': 'SEC EDGAR company-facts API',
                                            'payload': data},
                       'company-fiscal-fact', units={'value': data['rows'][0]['unit']} if data['rows'] else {},
                       period_basis='fiscal-year')


async def metric(read, *, context):
    p=params(read); companies=p.get('companies') or [p.get('company')]
    if isinstance(companies,str): companies=[companies]
    if not companies or any(not c for c in companies): raise runtime.Refused('SEC company is required')
    combined=[]; details=[]
    for company in companies:
        data=await fetch(company,p.get('metric'),context,year=int(p['fiscal_year']) if p.get('fiscal_year') else None,
                         annual_only=bool(p.get('years')),years=p.get('years'))
        details.append(data); combined.extend([{**r,'company':data['entity_name'],'cik':data['cik']} for r in data['rows']])
    return result(read, {'rows':combined,'companies':details})


async def annual(read, *, context):
    p=params(read); data=await fetch(p.get('company'),p.get('metric'),context,year=int(p['fiscal_year']),annual_only=True)
    return result(read,data)


async def ratio(read, *, context):
    p=params(read); name=p.get('ratio'); year=int(p.get('fiscal_year'))
    if name not in RATIOS: raise runtime.Refused('Unknown curated SEC ratio: '+str(name))
    numerator,denominator,average,extra,definition=RATIOS[name]
    async def one(metric_name, fy):
        data=await fetch(p.get('company'),metric_name,context,year=fy,annual_only=True)
        return data, data['rows'][0] if data['rows'] else None
    base,num=await one(numerator,year); _,den=await one(denominator,year)
    _,more=await one(extra,year) if extra else (None,None)
    if not num or not den or (extra and not more): rows=[]
    else:
        divisor=float(den['value'])+(float(more['value']) if more else 0); averaging='year-end'
        if average:
            _,prior=await one(denominator,year-1)
            if prior: divisor=(float(den['value'])+float(prior['value']))/2; averaging='average of two fiscal year-ends'
        rows=[] if divisor == 0 else [{'source':'sec_edgar_api','ratio':name,'ratio_pct':round(float(num['value'])/divisor*100,3),
                 'fiscal_year':year,'numerator':numerator,'numerator_value':num['value'],'denominator':denominator,
                 'denominator_value':divisor,'averaging':averaging,'definition':definition}]
    data={'rows':rows,'cik':base['cik'],'entity_name':base['entity_name'],'definition':definition}
    return synth.Input(rows,True,
                       {'source':read.source,'provider':'SEC EDGAR company-facts API','payload':data},'company-ratio',
                       units={'ratio_pct':'percent'},period_basis='fiscal-year')
