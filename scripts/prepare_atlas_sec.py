"""Materialize Atlas's reviewed SEC metrics as shared-engine descriptors."""
from pathlib import Path
import json
import yaml

ROOT=Path(__file__).resolve().parents[1]


def prepare():
    catalog=ROOT/'instances/atlas/catalog/attested-computations'
    data=yaml.safe_load((ROOT/'instances/atlas/sec-curation.yaml').read_text())
    def component(name,offset=0):
        taxonomy,tags,unit,kind=data['metrics'][name]
        return {'taxonomy':taxonomy,'concepts':tags,'unit':unit,'period_type':kind,'year_offset':offset}
    specs={name:component(name) for name in data['metrics']}
    for name,(num,den,average,extra,definition) in data['ratios'].items():
        components=[component(num),component(den)]
        divisor={'input':1}
        if average:
            components.append(component(den,-1))
            divisor={'op':'divide','args':[{'op':'add','args':[{'input':1},{'input':2}]},2]}
        elif extra:
            components.append(component(extra))
            divisor={'op':'add','args':[{'input':1},{'input':2}]}
        specs[name]={'components':components,'unit':'percent','definition':definition,
                     'expression':{'op':'multiply','args':[100,{'op':'divide','args':[{'input':0},divisor]}]}}
    for name,spec in specs.items():
        label=name.replace('_',' ')
        fm={'type':'Financial Statement Concept','title':label+' — SEC EDGAR',
            'description':f"Reported annual {label} for public companies, one fiscal year or a series. "+
                spec.get('definition','Uses the curated XBRL concept family and reported units.'),
            'accessor':'sec_company_facts','visibility':'public','pack':'public',
            'xbrl':spec,'tags':['SEC','financials',label],
            'curation_provenance':{'reviewer':'Bel','reviewed_on':'2026-09-03',
                'scope':'original metric-to-concept mapping; generated adapter implementation not reviewed by Bel'},
            'computation':{'runtime':{'parameters':[
                {'name':'companies','type':'ARRAY<STRING>','required':True,'description':'Array of separately named companies, e.g. ["Apple","Microsoft","Nvidia"]. Never one comma-separated string.'},
                {'name':'fiscal_year','type':'INTEGER','required':False,'description':'Explicit fiscal year only.'},
                {'name':'years','type':'INTEGER','required':False,'description':'Number of latest fiscal years requested.'},
                {'name':'year_from','type':'INTEGER','required':False,'description':'First requested fiscal year.'},
                {'name':'year_to','type':'INTEGER','required':False,'description':'Last requested fiscal year.'},
                {'name':'period','type':'STRING','required':False,'description':'latest for current value; all for history.'}]}}}
        (catalog/f'sec_{name}.md').write_text('---\n'+yaml.safe_dump(fm,sort_keys=False,allow_unicode=True)+'---\n')
    old=catalog/'sec_edgar_company_metric_by_year.md'
    if old.exists():
        archive=ROOT/'instances/atlas/upstream'
        archive.mkdir(parents=True,exist_ok=True)
        (archive/old.name).write_text(old.read_text())
        old.unlink()
    manifest_path=ROOT/'instances/atlas/catalog/atlas-source.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text())
        manifest['static_documents']=[p.name for p in sorted(catalog.glob('*.md'))]+['bigquery/covid19_open_data/table.md']
        manifest['total_documents']=len(manifest['static_documents'])+sum(manifest['table_descriptor_counts'].values())-1
        manifest_path.write_text(json.dumps(manifest,indent=2)+'\n')
    print(f'{len(specs)} shared SEC descriptors prepared')


if __name__=='__main__':
    prepare()
