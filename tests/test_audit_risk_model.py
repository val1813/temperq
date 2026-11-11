"""Independent small answers, covariance cross terms and train/holdout leakage."""
import copy
import csv
from datetime import date, datetime, timedelta, timezone
import io
import json
import math

import numpy as np
import pytest

from qwb import audit_risk_model as risk


def csv_bytes(fields, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream,lineterminator='\n')
    writer.writerow(fields)
    writer.writerows(rows)
    return stream.getvalue().encode()


def fixture():
    days = []
    d = date(2025,1,2)
    while len(days) < 8:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    stamps = [d.isoformat()+'T15:00:00+08:00' for d in days]
    scope = dict(schema_version='risk_model_scope_v1',start_time=stamps[0],end_time=stamps[-1],cutoff_time=stamps[4],
                 assets=['A','B'],min_train_periods=4,min_holdout_periods=2,shrinkage='0',confidence_level='0.95',
                 periods_per_year=252,max_exposure_age_days=366,holdings_complete=True,cash_return='zero',
                 model_definition='Synthetic known linear model, decimal one-session total returns; no real data.',
                 factors=[dict(name='market',kind='style',definition='Synthetic supplied market return',unit='return_per_unit_exposure')])
    calendar = []
    day=days[0]
    while day <= days[-1]:
        calendar.append([day.isoformat(),int(day.weekday()<5)])
        day += timedelta(days=1)
    periods = [[f'p{i+1}',stamps[i],stamps[i+1],'synthetic-session'] for i in range(7)]
    weights = []
    for book,values in [('portfolio',[('A','0.6'),('B','0.2'),('CASH','0.2')]),
                        ('benchmark',[('A','0.5'),('B','0.5'),('CASH','0')])]:
        for code,w in values:
            weights.append([book,code,w,stamps[4],stamps[4],'synthetic-fixed-weights'])
    exposures = [[c,'market',stamps[0],stamps[0],b,'synthetic-given-exposure'] for c,b in [('A','1'),('B','2')]]
    factor_returns,asset_returns = [],[]
    fvalues = ['-0.01','0.01','-0.01','0.01','0.02','-0.03','0.01']
    evalues = ['-0.02','-0.02','0.02','0.02','0','0.01','-0.01']
    from decimal import Decimal as D
    for i,(f,e) in enumerate(zip(fvalues,evalues)):
        factor_returns.append([f'p{i+1}','market',f,stamps[i+1],'synthetic-factor-return'])
        for code,r in [('A',D(f)+D(e)),('B',D('2.5')*D(f)+D(e))]:
            asset_returns.append([f'p{i+1}',code,format(r,'f'),stamps[i+1],'synthetic-asset-total-return'])
    tables = dict(periods=periods,weights=weights,exposures=exposures,factor_returns=factor_returns,asset_returns=asset_returns)
    return dict(scope=json.dumps(scope).encode(),calendar=csv_bytes(['date','is_open'],calendar),
                **{name:csv_bytes(risk.FIELDS[name],rows) for name,rows in tables.items()})


def run(data=None,source_kind='synthetic_fixture'):
    data = data or fixture()
    return risk.analyze(*(data[k] for k in risk.ROLES),source_kind=source_kind)


def table(data,name):
    return list(csv.DictReader(io.StringIO(data[name].decode())))


def set_rows(data,name,rows):
    fields = ['date','is_open'] if name == 'calendar' else risk.FIELDS[name]
    data[name] = csv_bytes(fields,([r[k] for k in fields] for r in rows))


def change(data,name,index,key,value):
    rows=table(data,name)
    rows[index][key]=value
    set_rows(data,name,rows)


def scope_change(data,**kwargs):
    value=json.loads(data['scope'])
    value.update(kwargs)
    data['scope']=json.dumps(value).encode()


def test_known_correlated_residual_and_factor_cross_covariance():
    result = run()
    assert result['status'] == 'computed'
    assert result['risk_model_calibrated'] is False and result['institutional_readiness'] == 'not_established'
    assert result['source_authenticated'] is False
    assert result['training_complete_periods'] == 4
    p,b,a = (result['forecast'][book] for book in ('portfolio','benchmark','active'))
    # F and e_A are orthogonal; e_B=0.5F+e_A. Portfolio=1.1F+0.8e_A.
    vf,ve = 4*.01**2/3,4*.02**2/3
    assert p['variance'] == pytest.approx(1.1**2*vf+.8**2*ve)
    assert b['variance'] == pytest.approx(1.75**2*vf+ve)
    assert a['variance'] == pytest.approx(.65**2*vf+.2**2*ve)
    assert p['factor_variance'] == pytest.approx(vf)
    assert p['residual_variance'] == pytest.approx(.64*ve+.01*vf)
    assert p['factor_residual_cross_variance'] == pytest.approx(.2*vf)
    assert abs(p['variance_closure_error']) < 1e-18
    for book in result['forecast'].values():
        assert sum(book['variance_contributions']) == pytest.approx(book['variance'])
        assert sum(book['volatility_contributions']) == pytest.approx(book['volatility'])
        assert sum(book['asset_variance_contributions']) == pytest.approx(book['variance'])
        assert sum(book['asset_volatility_contributions']) == pytest.approx(book['volatility'])
        assert book['annualized_volatility'] == pytest.approx(book['volatility']*math.sqrt(252))
        assert book['gaussian_es_zero_mean'] > book['gaussian_var_zero_mean'] > 0
    assert result['rank_deficient'] is True
    assert np.asarray(result['joint_covariance'])[1,2] == pytest.approx(ve)
    evaluation=result['evaluation']['portfolio']
    assert evaluation['observations'] == pytest.approx([.022,-.025,.003])
    assert evaluation['exceedances'] == 0
    assert evaluation['tail_sample_status'] == 'too_few_expected_exceedances'
    assert evaluation['calibrated'] is False


def test_holdout_asset_and_factor_perturbation_cannot_change_forecast_or_model_digest():
    data=fixture();before=run(data)
    change(data,'asset_returns',8,'return','-0.9')
    change(data,'factor_returns',4,'return','7')
    after=run(data)
    assert after['forecast'] == before['forecast']
    assert after['model_inputs'] == before['model_inputs']
    assert after['model_digest'] == before['model_digest']
    assert after['input_sha256'] != before['input_sha256']
    assert after['evaluation'] != before['evaluation']


def test_future_exposure_revision_does_not_change_forecast():
    data=fixture();before=run(data)
    rows=table(data,'exposures')
    rows.append(dict(rows[0],available_at=json.loads(data['scope'])['end_time'],exposure='999'))
    set_rows(data,'exposures',rows)
    after=run(data)
    assert after['model_digest'] == before['model_digest']
    assert after['forecast'] == before['forecast']


def test_late_old_exposure_does_not_replace_newer_observation():
    data=fixture();rows=table(data,'exposures');stamps=table(data,'periods')
    newer=dict(rows[0],asof_time=stamps[1]['start_time'],available_at=stamps[1]['start_time'],exposure='3')
    late=dict(rows[0],available_at=stamps[2]['end_time'],exposure='999')
    set_rows(data,'exposures',rows+[newer,late])
    result=run(data)
    assert result['forecast_exposure_versions']['A']['market']['exposure'] == '3'


@pytest.mark.parametrize('name,index,key,value',[
    ('asset_returns',0,'available_at','2025-01-10T15:00:00+08:00'),
    ('factor_returns',0,'available_at','2025-01-10T15:00:00+08:00'),
    ('exposures',0,'available_at','2025-01-03T15:00:00+08:00'),
])
def test_unavailable_training_material_never_dropped_or_filled(name,index,key,value):
    data=fixture();change(data,name,index,key,value)
    result=run(data)
    assert result['status'] == result['forecast_status'] == 'insufficient'
    assert result['forecast'] is None and result['evaluation'] is None
    assert result['missing']


def test_missing_holdout_preserves_forecast_but_refuses_partial_calibration():
    data=fixture();before=run(data)
    set_rows(data,'asset_returns',table(data,'asset_returns')[:-1])
    after=run(data)
    assert after['forecast'] == before['forecast'] and after['model_digest'] == before['model_digest']
    assert after['status'] == after['evaluation_status'] == 'insufficient'
    assert after['forecast_status'] == 'computed'
    assert after['evaluation']['portfolio']['observations'] is None
    assert after['evaluation']['portfolio']['status'] == 'insufficient'


def test_asset_and_factor_history_no_silent_row_drop():
    for name in ['asset_returns','factor_returns']:
        data=fixture();set_rows(data,name,table(data,name)[1:])
        result=run(data)
        assert result['forecast'] is None and result['status'] == 'insufficient'


@pytest.mark.parametrize('name',['periods','weights','exposures','factor_returns','asset_returns','calendar'])
def test_duplicates_rejected(name):
    data=fixture();rows=table(data,name);set_rows(data,name,rows+[rows[0]])
    with pytest.raises(ValueError):run(data)


@pytest.mark.parametrize('name',['calendar','periods'])
def test_common_missing_day_cannot_be_hidden(name):
    data=fixture();rows=table(data,name);set_rows(data,name,rows[:2]+rows[3:])
    with pytest.raises(ValueError):run(data)


def test_period_spanning_missing_market_session_rejected():
    data=fixture();rows=table(data,'periods')
    rows[1]['end_time']=rows[2]['end_time'];del rows[2]
    set_rows(data,'periods',rows)
    with pytest.raises(ValueError):run(data)


@pytest.mark.parametrize('value',['NaN','Infinity','1e-3','.01','-1'])
def test_bad_asset_returns_rejected(value):
    data=fixture();change(data,'asset_returns',0,'return',value)
    with pytest.raises(ValueError):run(data)


@pytest.mark.parametrize('kwargs',[
    {'shrinkage':'1.01'},{'confidence_level':'0.5'},{'periods_per_year':True},
    {'min_train_periods':2},{'assets':['A','A']},{'extra':'unknown'},
    {'cutoff_time':'2025-01-08T14:00:00+08:00'},
])
def test_invalid_scope_rejected(kwargs):
    data=fixture();scope_change(data,**kwargs)
    with pytest.raises(ValueError):run(data)


@pytest.mark.parametrize('table_name,index,key,value',[
    ('weights',0,'available_at','2025-01-09T15:00:00+08:00'),
    ('weights',0,'weight','0.7'),
    ('exposures',0,'factor','undeclared'),
    ('asset_returns',0,'code','UNKNOWN'),
    ('factor_returns',0,'available_at','2025-01-02T15:00:00+08:00'),
    ('periods',0,'start_time','2025-01-02T15:00:00'),
])
def test_identity_time_and_full_weight_constraints(table_name,index,key,value):
    data=fixture();change(data,table_name,index,key,value)
    with pytest.raises(ValueError):run(data)


def test_shrinkage_is_declared_fixed_and_full_diagonal_at_one():
    data=fixture();base=run(data)
    scope_change(data,shrinkage='1')
    result=run(data)
    matrix=np.asarray(result['joint_covariance'])
    assert np.allclose(matrix,np.diag(np.diag(matrix)),atol=0,rtol=0)
    assert result['forecast']['portfolio']['factor_residual_cross_variance'] == 0
    assert result['model_digest'] != base['model_digest']
    assert result['forecast']['portfolio']['variance'] != base['forecast']['portfolio']['variance']


def test_identical_books_active_risk_zero_without_false_calibration_pass():
    data=fixture();rows=table(data,'weights')
    p={r['code']:r['weight'] for r in rows if r['book']=='portfolio'}
    for r in rows:r['weight']=p[r['code']]
    set_rows(data,'weights',rows)
    result=run(data);a=result['forecast']['active'];evaluation=result['evaluation']['active']
    assert a['volatility']==a['variance']==0 and a['variance_shares'] is None
    assert evaluation['status']=='degenerate_forecast' and evaluation['calibrated'] is False
    assert evaluation['observed_nonzero_count']==0 and evaluation['kupiec_p_value'] is None


def test_cash_only_book_and_stress_break_zero_forecast():
    data=fixture();rows=table(data,'weights')
    for r in rows:
        if r['book']=='portfolio':r['weight']='1' if r['code']=='CASH' else '0'
    set_rows(data,'weights',rows)
    result=run(data)
    assert result['forecast']['portfolio']['volatility']==0
    assert result['evaluation']['portfolio']['observations']==[0,0,0]
    evaluation=risk._evaluate([-.1,-.2,.1],dict(volatility=0,gaussian_var_zero_mean=0),.95,2)
    assert evaluation['status']=='degenerate_forecast' and evaluation['observed_nonzero_count']==3


def test_tail_test_zero_and_all_hits_finite_and_power_disclosed():
    forecast=dict(volatility=.01,gaussian_var_zero_mean=.01645)
    for observed,n_hits in [([0]*200,0),([-.1]*200,200),([-.1,0]*100,100)]:
        result=risk._evaluate(observed,forecast,.95,2)
        assert result['exceedances']==n_hits and math.isfinite(result['kupiec_lr'])
        assert 0<=result['kupiec_p_value']<=1 and 0<=result['exact_binomial_p_value']<=1
        assert result['tail_sample_status']=='diagnostic_only'
        assert result['calibrated'] is False


def test_tail_and_scale_diagnostics_distinguish_known_distribution_from_stress():
    from statistics import NormalDist
    confidence=.95
    forecast=dict(volatility=.01,gaussian_var_zero_mean=NormalDist().inv_cdf(confidence)*.01)
    known=[NormalDist().inv_cdf((i+.5)/1000)*.01 for i in range(1000)]
    normal=risk._evaluate(known,forecast,confidence,60)
    stress=risk._evaluate([4*x for x in known],forecast,confidence,60)
    assert normal['exceedances']==50 and normal['exact_binomial_p_value'] > .9
    assert normal['standardized_rms']==pytest.approx(1,abs=.002)
    assert normal['volatility_ratio_ci95_iid_gaussian'][0]<1<normal['volatility_ratio_ci95_iid_gaussian'][1]
    assert stress['exceedances']>300 and stress['exact_binomial_p_value']<1e-50
    assert stress['volatility_ratio']>3.9
    assert normal['calibrated'] is stress['calibrated'] is False


def test_exceedance_cluster_diagnostics_and_degenerate_sequences():
    forecast=dict(volatility=.01,gaussian_var_zero_mean=.02)
    result=risk._evaluate([0,0,-.03,-.04,0,-.05],forecast,.95,2)
    assert result['exceedance_transitions']==[[1,2],[1,1]]
    assert result['independence_test']['lr']>=0
    assert result['conditional_coverage_test']['lr']==pytest.approx(result['kupiec_lr']+result['independence_test']['lr'])
    degenerate=risk._evaluate([0]*100,forecast,.95,2)
    assert degenerate['independence_test']['status']=='insufficient_hit_variation'
    assert degenerate['independence_test']['p_value'] is None
    clustered=risk._evaluate([-.03]*50+[0]*950,forecast,.95,2)
    assert clustered['exceedances']==50 and clustered['exact_binomial_p_value']>.9
    assert clustered['independence_test']['p_value']<1e-50


def test_source_explicit_size_limits_and_no_input_mutation():
    data=fixture();original=copy.deepcopy(data)
    with pytest.raises(ValueError):run(data,source_kind='official_certified')
    result=run(data,source_kind='historical_returns_synthetic_exposures')
    assert result['source_kind']=='historical_returns_synthetic_exposures' and data==original
    data['scope']=b' '* (risk.factor.MAX_BYTES+1)
    with pytest.raises(ValueError):run(data)


def test_insufficient_counts_do_not_establish_calibration():
    data=fixture();scope_change(data,min_train_periods=60)
    assert run(data)['forecast'] is None
    data=fixture();scope_change(data,min_holdout_periods=60)
    result=run(data)
    assert result['forecast_status']=='computed' and result['status']=='insufficient'
    assert result['evaluation']['portfolio']['status']=='insufficient'


def test_industry_sum_missing_exposure_and_zero_not_interchangeable():
    data=fixture();s=json.loads(data['scope']);s['factors'][0]['kind']='industry';data['scope']=json.dumps(s).encode()
    change(data,'exposures',1,'exposure','0.5')
    with pytest.raises(ValueError,match='行业'):run(data)
    data=fixture();set_rows(data,'exposures',table(data,'exposures')[:1])
    assert run(data)['forecast'] is None


def test_header_and_json_duplicate_keys_rejected():
    data=fixture();data['scope']=data['scope'][:-1]+b',"assets":["A"]}'
    with pytest.raises(ValueError):run(data)
    data=fixture();data['asset_returns']=data['asset_returns'].replace(b'source_ref',b'unknown',1)
    with pytest.raises(ValueError):run(data)


def test_cli_known_answer_insufficient_bad_input_and_exclusive_output(tmp_path,capsys):
    from qwb import audit_cli
    import argparse
    data=fixture()
    for name,raw in data.items():(tmp_path/(name+('.json' if name=='scope' else '.csv'))).write_bytes(raw)
    parser=argparse.ArgumentParser();audit_cli.add_parser(parser.add_subparsers(dest='cmd'))
    args=['audit','risk-model','--source-kind','synthetic_fixture','--json']
    for name in risk.ROLES:args.extend(['--'+name.replace('_','-'),str(tmp_path/(name+('.json' if name=='scope' else '.csv')))])
    output=tmp_path/'report.json';args.extend(['--out',str(output)])
    assert audit_cli.main(parser.parse_args(args))==0
    assert json.loads(capsys.readouterr().out)['forecast']['portfolio']['variance']==pytest.approx(.0005026666666666667)
    original=output.read_bytes()
    assert audit_cli.main(parser.parse_args(args))==2
    assert output.read_bytes()==original
    capsys.readouterr()
    args=args[:-2]
    scope_change(data,min_train_periods=60)
    (tmp_path/'scope.json').write_bytes(data['scope'])
    assert audit_cli.main(parser.parse_args(args))==1
    assert json.loads(capsys.readouterr().out)['forecast'] is None
