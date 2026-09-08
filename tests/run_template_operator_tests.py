"""Publish independently executable fixed-input template results to the instance server."""
import argparse
from dataclasses import asdict
import math

import answer_synthesizer as synth
import stage_reports
import instance
from template_operator_cases import cases


def equivalent(a,b):
    if type(b) is float:return type(a) in (int,float) and math.isclose(a,b,rel_tol=1e-8,abs_tol=1e-8)
    if isinstance(b,list):return isinstance(a,list) and len(a)==len(b) and all(equivalent(x,y) for x,y in zip(a,b))
    if isinstance(b,dict):return isinstance(a,dict) and set(a)==set(b) and all(equivalent(a[k],b[k]) for k in b)
    return a==b


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--run-id',default='20260909-all-template-operators');args=parser.parse_args()
    templates=synth.load_templates(); records=[]
    for name,case in cases().items():
        detail={'inputs':{k:asdict(v) for k,v in case['inputs'].items()},'parameters':case['parameters'],'expected':case['expected']}
        try:
            output=synth.synthesize(name,case['inputs'],case['parameters']);actual=output['result']
            if case.get('assert_fields'):actual=[r['allocation'] for r in actual]
            detail.update(status='pass' if equivalent(actual,case['expected']) else 'fail',output=output)
        except Exception as exc:detail.update(status='error',error=str(exc))
        records.append({'id':name,'question':'Fixed-data test: '+name,
                        'example_query':templates[name]['examples'][0],
                        'note':'Template illustration; execution below uses fixed synthetic inputs, not live answers to this example.',
                        'execution':detail})
    directory=stage_reports.results_root()/args.run_id
    stage_reports.publish(directory,{'run_id':args.run_id,'kind':'fixed-input operator tests','cases':len(records),
        'instance':str(instance.path()), 'model':'None — deterministic operators',
        'scope':'76 catalog templates on fixed synthetic inputs. Tests calculations, not live answers to natural-language questions.',
        'note':'All template DAGs executed with fixed synthetic data. No LLM or live ARD/source calls. Not an end-to-end corpus score.'},records)
    passed=sum(r['execution']['status']=='pass' for r in records)
    print(f'{passed}/{len(records)} passed; /tests/{args.run_id}/execution.html')
    if passed!=len(records):raise SystemExit(1)


if __name__=='__main__':main()
