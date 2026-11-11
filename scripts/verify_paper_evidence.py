"""Synthetic paper init/tick/restart and actual read-only verification CLI.

Writes only a new, isolated fixture directory. No downloads, user accounts,
automatic scheduling, training, external engine or authenticated forward claim.
"""
from copy import deepcopy
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from qwb import daily, experiments, paper, paper_verification, pipeline
import numpy as np
import pandas as pd
import yaml


def write(path,value):
    with path.open('x',encoding='utf-8') as stream:json.dump(value,stream,ensure_ascii=False,indent=2)


def inventory(root):
    return {p.relative_to(root).as_posix():experiments.digest(p) for p in root.rglob('*') if p.is_file()}


def verify(out):
    out.mkdir(parents=True,exist_ok=False)
    work=out/'workspace'
    work.mkdir()
    dates=pd.bdate_range('2026-07-01','2026-10-30')
    codes=['000001.SZ','600000.SH','688001.SH']
    bars=pipeline.synth_bars(len(dates),len(codes),seed=120)
    bars['date']=np.repeat(dates,len(codes))
    bars['code']=np.tile(codes,len(dates))
    bars['up_limit']=bars['close']*1.2
    bars['down_limit']=bars['close']*.8
    natural=pd.date_range('2026-07-01','2026-10-30')
    calendar=pd.DataFrame({'date':natural.strftime('%Y-%m-%d'),'is_open':(natural.weekday<5).astype(int)})
    calendar_raw=calendar.to_csv(index=False).encode('utf-8')
    with (out/'calendar.csv').open('xb') as stream:stream.write(calendar_raw)
    feed=work/'feed'
    for name,frame in [('missing_first',bars[bars.date.ne(pd.Timestamp('2026-10-19'))]),('complete',bars)]:
        snapshot=feed/'snapshots'/name
        (snapshot/'bars').mkdir(parents=True)
        frame.to_parquet(snapshot/'bars/2026.parquet',index=False)
        calendar.to_csv(snapshot/'calendar.csv',index=False)
        write(snapshot/'snapshot.json',dict(data_snapshot_id='synthetic-'+name,source='synthetic_fixture',
            start='2026-07-01',end='2026-10-30',synthetic=True))
    write(feed/'current.json',dict(snapshot='snapshots/complete'))
    panel=daily.panel_from_bars(bars)
    model=work/'runs/model/final_model'
    model.mkdir(parents=True)
    names=daily.feature_names(daily.features(panel))
    cfg=deepcopy(daily.DEFAULTS)
    cfg.update(universe='all',min_age_days=1,min_amount_quantile=0,portfolio={'n_hold':1})
    card=yaml.safe_load((ROOT/'examples/cn_rank_5d.yaml').read_text(encoding='utf-8'))
    write(model/'meta.json',dict(model='ridge',files=['ridge.json'],features=names,
        train_end='2026-09-30',data_end='2026-10-01',snapshot_id='synthetic-complete',
        card=card,cfg=cfg,data=dict(source='snapshot',dir=str(feed))))
    write(model/'ridge.json',dict(coef=[0.]*(len(names)+1)))
    paper.initialize(work,'normal',model.parent,100000,[],replay_start='2026-10-19')
    first=paper.tick(work,'normal',now='2026-10-27T16:00:00+08:00')
    assert first['new_sessions']==7
    original=paper.inspect(work,'normal')['sessions']
    again=paper.tick(work,'normal',now='2026-10-27T16:00:00+08:00')
    assert again['new_sessions']==0 and paper.inspect(work,'normal')['sessions']==original
    assert all(r['scope']=='historical_replay' for r in original)
    assert original[1]['audit']['fills']
    write(out/'tick_restart.json',dict(status='passed',sessions=7,first=first,retry=again,
        scope='synthetic_historical_replay_not_forward',sessions_unchanged_on_retry=True))
    experiments.write(feed/'current.json',dict(snapshot='snapshots/missing_first'))
    paper.initialize(work,'gap_recovery',model.parent,100000,[],replay_start='2026-10-19')
    blocked=paper.tick(work,'gap_recovery',now='2026-10-27T16:00:00+08:00')
    assert blocked['state']=='WAITING_DATA' and blocked['new_sessions']==0
    experiments.write(feed/'current.json',dict(snapshot='snapshots/complete'))
    recovered=paper.tick(work,'gap_recovery',now='2026-10-27T16:00:00+08:00')
    assert recovered['new_sessions']==7
    assert paper.inspect(work,'gap_recovery')['sessions'][0]['day']=='2026-10-19'
    write(out/'gap_recovery.json',dict(status='passed',blocked=blocked,recovered=recovered,
        original_inception_retained=True,scope='synthetic_fixture'))
    stages=[]
    env=dict(os.environ,PYTHONUTF8='1',PYTHONPATH=str(ROOT/'src'),OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='1')

    def cli(name,account_name,expected_status,expected_code=None,calendar_path=None):
        root=paper.location(work,account_name)
        before=inventory(root)
        output=out/(name+'.json')
        command=[sys.executable,'-X','utf8','-m','qwb.cli','paper','verify',account_name,
            '--calendar',str(calendar_path or out/'calendar.csv'),'--through','2026-10-27',
            '--source-kind','synthetic_fixture','--out',str(output),'--json']
        run=subprocess.run(command,cwd=work,env=env,capture_output=True)
        for suffix,raw in [('stdout.txt',run.stdout),('stderr.txt',run.stderr)]:
            with (out/(name+'.'+suffix)).open('xb') as stream:stream.write(raw)
        assert run.returncode==1,name
        assert inventory(root)==before,'Read-only verification changed an account'
        if expected_status is None:
            assert not output.exists()
        else:
            result=json.loads(output.read_text(encoding='utf-8'))
            assert result['status']==expected_status,(name,result['issues'])
            assert result['locally_eligible_forward_return_intervals']==0
            assert result['institutional_readiness']=='not_established'
            if expected_code:assert expected_code in {i['code'] for i in result['issues']}
        stages.append(dict(name=name,passed=True,exit_code=run.returncode,expected_status=expected_status,
                           account_files_unchanged=True,expected_issue=expected_code))

    cli('normal_readonly','normal','matched')
    cli('recovered_readonly','gap_recovery','matched')
    source=paper.location(work,'normal')
    for name,mutation,expected in [
        ('missing_middle','delete','missing_expected_session'),
        ('wrong_equity','equity','state_equity_mismatch'),
        ('false_forward','scope','historical_account_relabelled_as_forward'),
        ('changed_model','model','frozen_model_digest_mismatch')]:
        dest=paper.location(work,name)
        shutil.copytree(source,dest)
        if mutation=='model':
            with (dest/'final_model/ridge.json').open('ab') as stream:stream.write(b'\n')
        else:
            with paper.connection(dest) as con:
                day='2026-10-21'
                if mutation=='delete':con.execute('DELETE FROM sessions WHERE day=?',(day,))
                else:
                    row=json.loads(con.execute('SELECT payload FROM sessions WHERE day=?',(day,)).fetchone()[0])
                    if mutation=='equity':row['equity']+=1
                    else:row['scope']='forward_paper'
                    con.execute('UPDATE sessions SET payload=? WHERE day=?',(json.dumps(row),day))
        cli(name,name,'failed',expected)
    with (out/'bad_calendar.csv').open('xb') as stream:stream.write(b'date,is_open\n2026-10-19,1\n')
    cli('bad_calendar_rejected','normal',None,calendar_path=out/'bad_calendar.csv')
    write(out/'verification.json',dict(status='passed',stages=stages,synthetic_sessions=7,
        initial_gap_recovery_passed=True,restart_idempotent=True,professional_engine_rerun=False,
        model_trained=False,actual_pipeline_and_executor_used=True,model_is_zero_coefficient_fixture=True,
        source_kind='synthetic_fixture',real_forward_intervals=0,real_broker_validation=False,
        implementation_sha256=experiments.digest(Path(paper_verification.__file__)),
        paper_implementation_sha256=experiments.digest(Path(paper.__file__)),
        verifier_sha256=experiments.digest(Path(__file__)),institutional_readiness='not_established'))
    print(json.dumps(dict(status='passed',cli_stages=len(stages),synthetic_sessions=7,
        initial_gap_recovery=True,real_forward_intervals=0)))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    verify(parser.parse_args().out.resolve())
