"""Persisted-data lifecycle acceptance in newly created isolated accounts only.

Zero-coefficient model and fixed scores are fixtures, never strategy evidence.
Optional vendor history tests date-level event accounting under declared proxies;
it cannot establish historical receipt times, investor tax or broker payments.
"""
from copy import deepcopy
from dataclasses import asdict
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
INSTALLED = '--installed' in sys.argv
if not INSTALLED:
    sys.path.insert(0, str(ROOT/'src'))
from qwb import paper_corporate_actions as ca, daily, daily_data, experiments as ex, paper, pipeline, strategy_workspace as ws
from qwb.portfolio.lifecycle import BonusShares, Dividend, Split
import numpy as np
import pandas as pd
import yaml


def child_env():
    env = dict(os.environ, PYTHONUTF8='1', PYTHONNOUSERSITE='1')
    if INSTALLED:
        env.pop('PYTHONPATH', None)
    else:
        env['PYTHONPATH'] = str(ROOT/'src')
    return env


def cli(work, argv):
    flags = ['-E', '-s'] if INSTALLED else []
    result = subprocess.run([sys.executable, *flags, '-X', 'utf8', '-m', 'qwb.cli', *argv],
        cwd=work, env=child_env(), capture_output=True, text=True, encoding='utf-8')
    if result.returncode:
        raise ValueError('Fixture CLI failed: '+result.stderr[-2000:])
    return json.loads(result.stdout)


def table(path):
    raw = ex.read(path)
    return pd.DataFrame(raw['items'], columns=raw['fields'])


def fixture(bonus):
    days = pd.bdate_range('2025-01-02', '2025-09-30')
    prices = np.where(days < pd.Timestamp('2025-06-12'), 10., 4.5)
    rows = pd.DataFrame(dict(code='000001.SZ', date=days.strftime('%Y-%m-%d'), open=prices,
        high=prices*1.02, low=prices*.98, close=prices, amount=100000000., volume=10000000.,
        adj_factor=np.where(days < pd.Timestamp('2025-06-12'), 1., 20/9),
        up_limit=prices*1.1, down_limit=prices*.9))
    events = [Dividend('div', '000001.SZ', '2025-06-05T23:59:59+08:00',
        '2025-06-11T15:00:00+08:00', '2025-06-12T09:00:00+08:00',
        '2025-06-16T09:00:00+08:00', 1., 0., .5, 'tax_exempt')]
    if bonus:
        events.append(BonusShares('shares', '000001.SZ', '2025-06-05T23:59:59+08:00',
            '2025-06-11T15:00:00+08:00', '2025-06-12T09:00:00+08:00',
            '2025-06-16T09:00:00+08:00', 1, 1, 'reject'))
    else:
        events.append(Split('shares', '000001.SZ', '2025-06-05T23:59:59+08:00',
            '2025-06-12T09:00:00+08:00', 2, 1, 'reject'))
    natural = pd.date_range('2025-01-01', '2025-09-30')
    calendar = pd.DataFrame(dict(date=natural.strftime('%Y-%m-%d'), is_open=(natural.weekday<5).astype(int)))
    doc = dict(schema_version='paper_corporate_actions_v1', version=1, previous_digest=None,
        source=dict(kind='synthetic_fixture', reference='known-answer lifecycle fixture'),
        coverage=[dict(code='000001.SZ', start='2025-01-01', through='2025-09-30')],
        actions=[asdict(e) for e in events], factor_changes=[dict(code='000001.SZ', date='2025-06-12',
            before=1., after=20/9, event_ids=['div', 'shares'])])
    return rows, calendar, doc, dict(synthetic=True)


def vendor(sample):
    bars, factors, limits, cal = [table(sample/(name+'.json')) for name in ('daily','adj_factor','stk_limit','trade_cal')]
    for frame in (bars, factors, limits):
        if frame.duplicated(['ts_code','trade_date']).any():
            raise ValueError('Duplicate vendor market key')
    bars = bars.merge(factors, on=['ts_code','trade_date'], validate='one_to_one').merge(
        limits[['ts_code','trade_date','up_limit','down_limit']], on=['ts_code','trade_date'], validate='one_to_one')
    bars = bars.rename(columns={'ts_code':'code', 'trade_date':'date', 'vol':'volume'})
    bars['date'] = pd.to_datetime(bars.date, format='%Y%m%d').dt.strftime('%Y-%m-%d')
    bars['amount'] *= 1000
    bars['volume'] *= 100
    bars = bars[['code','date','open','high','low','close','amount','volume','adj_factor','up_limit','down_limit']].sort_values('date')
    calendar = pd.DataFrame(dict(date=pd.to_datetime(cal.cal_date, format='%Y%m%d').dt.strftime('%Y-%m-%d'),
        is_open=cal.is_open.astype(int))).sort_values('date')
    if set(bars.date) != set(calendar.loc[calendar.is_open.eq(1), 'date']):
        raise ValueError('Vendor bars and calendar differ')
    dividends = table(sample/'dividend.json')
    selected = dividends.loc[dividends.div_proc.eq('实施') & dividends.ex_date.ge('20250101') & dividends.ex_date.le('20250930')]
    economic = ['ts_code','end_date','stk_div','cash_div','cash_div_tax','record_date','ex_date','pay_date','div_listdate','imp_ann_date']
    unique = selected.drop_duplicates(economic)
    if len(unique) != 1 or unique.iloc[0].stk_div != 0:
        raise ValueError('Sample requires exactly one unambiguous ordinary cash dividend')
    row = unique.iloc[0]
    def at(value, hour):
        return pd.to_datetime(value, format='%Y%m%d').strftime('%Y-%m-%d')+hour+'+08:00'
    event = Dividend('vendor-cash-2024-final', row.ts_code, at(row.imp_ann_date, 'T23:59:59'),
        at(row.record_date, 'T15:00:00'), at(row.ex_date, 'T09:00:00'), at(row.pay_date, 'T09:00:00'),
        float(row.cash_div_tax), float(row.cash_div_tax-row.cash_div), float(row.cash_div_tax), 'declared_withholding')
    factors = bars.set_index('date').adj_factor
    changed = factors.ne(factors.shift()) & factors.shift().notna()
    if list(factors.index[changed]) != [pd.Timestamp(event.ex_at).strftime('%Y-%m-%d')]:
        raise ValueError('Sample contains unlinked factor changes')
    index = factors.index.get_loc(pd.Timestamp(event.ex_at).strftime('%Y-%m-%d'))
    doc = dict(schema_version='paper_corporate_actions_v1', version=1, previous_digest=None,
        source=dict(kind='provider_archive', reference='vendor sample manifest; implementation-announcement date end bound; date-level payment proxy; declared withholding'),
        coverage=[dict(code=row.ts_code, start=calendar.date.min(), through=calendar.date.max())],
        actions=[asdict(event)], factor_changes=[dict(code=row.ts_code, date=factors.index[index],
            before=float(factors.iloc[index-1]), after=float(factors.iloc[index]), event_ids=[event.event_id])])
    return bars, calendar, doc, dict(synthetic=False, vendor_rows=len(bars),
        duplicated_announcement_rows=len(selected)-len(unique), historical_receipt_time_established=False,
        availability_basis='implementation announcement date 23:59:59 is a declared conservative bound, not a receipt timestamp',
        payment_time_basis='09:00 daily accounting proxy, not verified bank credit time',
        investor_tax_basis='provider gross/net declaration only; no holding-period tax certification',
        archive_sha256={p.name:ex.digest(p) for p in sample.glob('*.json')})


def run(out, inputs):
    out.mkdir(parents=True, exist_ok=False)
    if INSTALLED:
        packages = Path(sys.executable).parent/'Lib/site-packages'
        assert all(Path(m.__file__).resolve().is_relative_to(packages.resolve())
                   for m in (ca, paper, ws, daily_data)), 'Acceptance imported source checkout'
    rows, calendar, doc, provenance = inputs
    ex.write(out/'provenance.json', provenance)
    ca.parse(json.dumps(doc).encode())
    work = out/'work'
    work.mkdir()
    rows.loc[rows.date.le('2025-06-11')].to_csv(out/'initial.csv', index=False)
    rows.to_csv(out/'complete.csv', index=False)
    calendar.to_csv(out/'calendar.csv', index=False)
    ex.write(out/'events.json', doc)
    feed = work/'feed'
    for name in ('initial','complete'):
        cli(work, ['paper','update-data',str(feed),str(out/(name+'.csv')), '--calendar',str(out/'calendar.csv'),
            '--corporate-actions',str(out/'events.json')])
    snapshot = daily_data.current(feed)
    panel = daily.load_panel(snapshot)
    model = work/'model/final_model'
    model.mkdir(parents=True)
    names = daily.feature_names(daily.features(panel))
    cfg = deepcopy(daily.DEFAULTS)
    cfg.update(universe='all', min_age_days=1, min_amount_quantile=0, portfolio={'n_hold':1})
    card = yaml.safe_load((ROOT/'examples/cn_rank_5d.yaml').read_text(encoding='utf-8'))
    ex.write(model/'meta.json', dict(model='ridge', files=['ridge.json'], features=names, train_end='2025-05-30',
        data_end='2025-06-02', snapshot_id='fixture-model', card=card, cfg=cfg, data=dict(source='snapshot',dir=str(feed))))
    ex.write(model/'ridge.json', dict(coef=[0.]*(len(names)+1)))
    pd.DataFrame([dict(code=c, decision_time=str(d.tz_localize('Asia/Shanghai')+pd.Timedelta(hours=15,minutes=30)),prediction=1.)
        for d in panel['close'].index if d >= pd.Timestamp('2025-06-09') for c in panel['close'].columns]).to_parquet(model.parent/'predictions.parquet',index=False)
    paper.initialize(work, 'lifecycle', model.parent, 100000, [], replay_start='2025-06-09')
    accounts = []
    for cost in ('commission_only', 'struct_v1'):
        sid, vid = ws.create(work, model.parent, feed, name=cost, cash=100000, preset='direct',
            cost_model=cost, overrides=dict(n_hold=1, hold_until_rank=1, max_weight=1.))
        evaluation = ws.evaluate(work,sid,vid,start='2025-06-09',end='2025-06-17')
        historical = ex.read(evaluation/'selected.json')
        ws.start(work,sid,vid,historical_start='2025-06-09')
        accounts.append((sid,vid,historical))
    paper.tick(work,'lifecycle',now='2025-06-11T18:00:00+08:00')
    for sid,vid,_ in accounts: ws.tick(work,sid,vid,now='2025-06-11T18:00:00+08:00')
    saved = paper.inspect(work,'lifecycle')['sessions']
    missing = feed/'snapshots/missing-events'
    shutil.copytree(snapshot, missing)
    (missing/ca.FILENAME).unlink()
    ex.write(feed/'current.json',dict(snapshot='snapshots/missing-events'))
    blocked = 0
    for tick in [lambda:paper.tick(work,'lifecycle',now='2025-06-17T18:00:00+08:00'),
                 *[lambda s=s,v=v:ws.tick(work,s,v,now='2025-06-17T18:00:00+08:00') for s,v,_ in accounts]]:
        try: tick()
        except ValueError: blocked += 1
    assert blocked == 3 and paper.inspect(work,'lifecycle')['sessions'] == saved
    ex.write(feed/'current.json',dict(snapshot=snapshot.relative_to(feed).as_posix()))
    paper.tick(work,'lifecycle',now='2025-06-17T18:00:00+08:00')
    for sid,vid,historical in accounts:
        ws.tick(work,sid,vid,now='2025-06-17T18:00:00+08:00')
        recorded = ws.inspect(work,sid,vid)['records']
        assert [r['state'] for r in recorded] == [r['state'] for r in historical['positions']]
        assert [r['path'] for r in recorded] == historical['path']
    from qwb import paper_verification
    report = paper_verification.verify(work,'lifecycle',(out/'calendar.csv').read_bytes(), through='2025-06-17',
        source_kind='synthetic_fixture' if provenance['synthetic'] else 'local_paper')
    assert report['status'] == 'matched', report['issues']
    ex.write(out/'readonly.json',report)
    sessions = paper.inspect(work,'lifecycle')['sessions']
    assert all(r['scope']=='historical_replay' for r in sessions)
    entitlement = next(a['entitled_quantity'] for r in sessions for a in (r.get('audit') or {}).get('corporate_actions',[]) if a['phase']=='record' and a['phase_id'].endswith(':record'))
    assert entitlement > 0
    assert not sessions[-1]['state']['entitlements']
    before = ex.object_digest([sessions, *[ws.inspect(work,s,v)['records'] for s,v,_ in accounts]])
    args = json.dumps([[s,v] for s,v,_ in accounts])
    code = ('import json,sys; from pathlib import Path; from qwb import paper,strategy_workspace as w; '
            'p=Path(sys.argv[1]); paper.tick(p,"lifecycle",now="2025-06-17T18:00:00+08:00"); '
            '[w.tick(p,s,v,now="2025-06-17T18:00:00+08:00") for s,v in json.loads(sys.argv[2])]')
    flags = ['-E', '-s'] if INSTALLED else []
    result = subprocess.run([sys.executable,*flags,'-c',code,str(work),args], env=child_env(),
        capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-2000:]
    assert before == ex.object_digest([paper.inspect(work,'lifecycle')['sessions'], *[ws.inspect(work,s,v)['records'] for s,v,_ in accounts]])
    json_paths = [out/name for name in ex.inventory(out) if name.endswith('.json')]
    long_json = [p for p in json_paths if len(str(p.resolve())) > 260]
    for path in long_json:
        ex.read(path)
    ex.write(out/'acceptance.json',dict(status='passed',synthetic=provenance['synthetic'],
        shared_history_daily_state_and_path_equal=True, accounts_tested=3, missing_event_paused_accounts=blocked,
        restart_in_fresh_process_idempotent=True, actual_loader_parquet_calendar_and_sqlite=True,
        actual_ingestion_cli=True, model_is_zero_coefficient_fixture=True, real_forward_intervals=0,
        installed_package=INSTALLED,
        max_json_path_length=max(len(str(p.resolve())) for p in json_paths),
        json_paths_over_260_read_back=len(long_json),
        broker_reconciliation=False, holding_period_tax_verified=False, institutional_readiness='not_established',
        code_sha256={str(p.relative_to(ROOT)).replace('\\','/'):ex.digest(p) for p in
            [Path(__file__),Path(ca.__file__),Path(paper.__file__),Path(ws.__file__),ROOT/'src/qwb/portfolio/lifecycle.py']}))
    print(json.dumps(dict(status='passed',case=out.name,accounts=3,synthetic=provenance['synthetic'])),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--api-sample',type=Path)
    parser.add_argument('--installed',action='store_true',help='Use installed package, including child processes')
    args = parser.parse_args()
    args.out.mkdir(parents=True,exist_ok=False)
    run(args.out/'split',fixture(False))
    run(args.out/'bonus',fixture(True))
    if args.api_sample: run(args.out/'vendor',vendor(args.api_sample))
