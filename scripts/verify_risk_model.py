"""Exercise the actual risk CLI with synthetic known answers and failure cases."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import scipy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qwb import audit_risk_model as risk


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def change(raw,edit):
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    fields=reader.fieldnames
    rows=list(reader)
    edit(rows)
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=fields,lineterminator='\n')
    writer.writeheader();writer.writerows(rows)
    return stream.getvalue().encode()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',required=True)
    args=parser.parse_args()
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=False)
    examples=ROOT/'examples/risk_model_synthetic'
    sources={p.name:sha(p) for p in examples.iterdir() if p.is_file()}
    fixture={name:(examples/(name+('.json' if name=='scope' else '.csv'))).read_bytes() for name in risk.ROLES}
    stages=[]
    env=dict(os.environ,PYTHONPATH=str(ROOT/'src'),PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',
             OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1',MKL_NUM_THREADS='1')

    def execute(name,data,expected):
        folder=out/name;folder.mkdir()
        command=[sys.executable,'-B','-m','qwb.cli','audit','risk-model','--source-kind','synthetic_fixture','--json']
        for role,raw in data.items():
            path=folder/(role+('.json' if role=='scope' else '.csv'))
            path.write_bytes(raw)
            command.extend(['--'+role.replace('_','-'),str(path)])
        command.extend(['--out',str(folder/'report.json')])
        completed=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
        (folder/'stdout.txt').write_bytes(completed.stdout)
        (folder/'stderr.txt').write_bytes(completed.stderr)
        assert completed.returncode==expected,(name,completed.returncode)
        result=json.loads(completed.stdout.decode('utf-8'))
        stages.append(dict(name=name,passed=True,exit_code=completed.returncode,status=result['status']))
        print(json.dumps(stages[-1]),flush=True)
        return result,command

    normal,command=execute('known_answer',fixture,0)
    assert abs(normal['forecast']['portfolio']['variance']-.0005026666666666667)<1e-16
    assert normal['forecast']['portfolio']['factor_residual_cross_variance']>0
    assert normal['risk_model_calibrated'] is False
    stress=dict(fixture)
    def shock(rows):
        for row in rows:
            if int(row['period_id'][1:])>4:row['return']='-0.8'
    stress['asset_returns']=change(stress['asset_returns'],shock)
    stressed,_=execute('holdout_shock',stress,0)
    assert stressed['forecast']==normal['forecast'] and stressed['model_digest']==normal['model_digest']
    assert stressed['evaluation']['portfolio']['exceedances']==3
    future=dict(fixture)
    future['factor_returns']=change(future['factor_returns'],lambda rows:rows[-1].update({'return':'9'}))
    revised,_=execute('future_factor_change',future,0)
    assert revised['model_digest']==normal['model_digest'] and revised['forecast']==normal['forecast']
    future=dict(fixture)
    future['exposures']=change(future['exposures'],lambda rows:rows.append(dict(rows[0],available_at=json.loads(fixture['scope'])['end_time'],exposure='999')))
    revised,_=execute('future_exposure_revision',future,0)
    assert revised['model_digest']==normal['model_digest']
    missing=dict(fixture)
    missing['asset_returns']=change(missing['asset_returns'],lambda rows:rows.pop(0))
    insufficient,_=execute('missing_training_asset',missing,1)
    assert insufficient['forecast'] is None
    late=dict(fixture)
    late['factor_returns']=change(late['factor_returns'],lambda rows:rows[0].update({'available_at':json.loads(fixture['scope'])['end_time']}))
    insufficient,_=execute('late_training_factor',late,1)
    assert insufficient['forecast'] is None
    missing=dict(fixture)
    missing['asset_returns']=change(missing['asset_returns'],lambda rows:rows.pop())
    insufficient,_=execute('missing_holdout_asset',missing,1)
    assert insufficient['forecast']==normal['forecast'] and insufficient['model_digest']==normal['model_digest']
    assert insufficient['evaluation']['portfolio']['observations'] is None
    calendar=dict(fixture)
    calendar['calendar']=change(calendar['calendar'],lambda rows:rows.pop(2))
    execute('calendar_gap',calendar,2)
    weight=dict(fixture)
    weight['weights']=change(weight['weights'],lambda rows:rows[0].update({'available_at':json.loads(fixture['scope'])['end_time']}))
    execute('future_weight',weight,2)
    saved=sha(out/'known_answer/report.json')
    duplicate=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
    (out/'existing_output.stdout.txt').write_bytes(duplicate.stdout)
    (out/'existing_output.stderr.txt').write_bytes(duplicate.stderr)
    assert duplicate.returncode==2 and sha(out/'known_answer/report.json')==saved
    stages.append(dict(name='existing_output_refused',passed=True,exit_code=2,status='error'))
    assert sources=={p.name:sha(p) for p in examples.iterdir() if p.is_file()}
    result=dict(schema='risk_model_cli_verification_v1',status='passed',stages=stages,
                source_kind='synthetic_fixture',original_examples_unchanged=True,
                risk_model_calibrated=False,real_factor_model_validation=False,
                institutional_readiness='not_established',numpy_version=np.__version__,scipy_version=scipy.__version__,
                implementation_sha256=sha(Path(risk.__file__)),verifier_sha256=sha(Path(__file__)),
                examples_sha256=sources,normal_model_digest=normal['model_digest'])
    with (out/'verification.json').open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(status='passed',cases=len(stages),risk_model_calibrated=False)))


if __name__=='__main__':
    main()
