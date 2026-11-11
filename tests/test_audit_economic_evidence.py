"""Known economic answers, evidence provenance, no-data boundary and web exports."""
from copy import deepcopy
import csv
import io
import json

import pytest
from fastapi.testclient import TestClient

from qwb import audit_evidence, audit_exports, audit_value, strategy_audit as audit, strategy_imports as imports
from qwb.audit_app import create_app


def files():
    values = {
        'strategy.py': 'def initialize(context):\n    pass\n',
        'fills.csv': 'date,time,code,side,quantity,price,fee\n2024-01-02,09:31:00,510300.SH,buy,100,10,5\n2024-01-04,09:31:00,510300.SH,sell,100,12,5\n',
        'positions.csv': 'date,code,quantity,price\n2024-01-02,510300.SH,100,10\n2024-01-03,510300.SH,100,11\n',
        'account.csv': 'date,equity,cash\n2024-01-02,9995,8995\n2024-01-03,10095,8995\n2024-01-04,10190,10190\n',
        'prices.csv': 'date,code,close\n2024-01-02,510300.SH,10\n2024-01-03,510300.SH,11\n2024-01-04,510300.SH,12\n',
        'listings.csv': 'code,listed_date,delisted_date\n510300.SH,2012-05-28,\n',
        'actions.csv': 'date,code,cash,quantity_change\n',
        'strategy_spec.csv': 'key,value\ncontrol_no_corporate_actions,true\ncontrol_no_external_flows,true\ncontrol_unadjusted_prices,true\n',
    }
    return [imports.SourceFile(k, v.encode()) for k, v in values.items()]


def full():
    b = imports.normalize(files(), name='synthetic control')
    return b, audit.analyze(b)


def test_common_start_hold_control_has_known_net_cost_bridge_and_no_first_day_fee():
    b, r = full()
    control = r['modules']['economic_review']['holding_control']
    assert control['status'] == 'computed'
    assert control['actual_net'] == 195 and control['hold_net'] == 200
    assert control['gross_path_difference'] == 0 and control['extra_fees'] == 5
    assert control['net_difference'] == -5 and control['decision'] == 'retain_original'
    assert control['baseline'] == 9995
    assert len(audit_exports.table(r, 'holding-control')[0]) == 3


@pytest.mark.parametrize('change,reason', [
    ('declaration', 'strategy_spec'), ('price', '缺1'), ('price_conflict', '冲突'),
    ('split', '公司行动'), ('delist', '生命周期'), ('baseline', '估值不一致')])
def test_control_refuses_missing_or_incompatible_evidence(change, reason):
    b, r = full()
    if change == 'declaration': b['spec'].pop('control_no_corporate_actions')
    elif change == 'price': b['prices'].pop()
    elif change == 'price_conflict': b['prices'].append({**b['prices'][0], 'close': 9})
    elif change == 'split': b['actions'].append({'date': '2024-01-03', 'cash': 10, 'quantity_change': 0})
    elif change == 'delist': b['listings'][0]['delisted_date'] = '2024-01-03'
    elif change == 'baseline': b['prices'][0]['close'] = 9
    out = audit_value.holding_control(b, r['modules'])
    assert out['status'] == 'insufficient' and reason in str(out['reasons'])
    assert 'net_difference' not in out


def test_balancing_cash_is_not_a_verified_gain_and_cannot_enter_comparison():
    from qwb.audit_verification import eligibility
    b, _ = full()
    b['actions'] = [dict(date='2024-01-04', code='510300.SH', cash=100, quantity_change=0, inferred=True)]
    b['snapshots'][-1]['cash'] += 100
    b['snapshots'][-1]['equity'] += 100
    r = audit.analyze(b)
    assert r['modules']['accounting']['status'] == 'provisional'
    assert r['modules']['accounting']['internally_balanced']
    assert r['evidence_assessment']['inferred_cash'] == 100
    assert not eligibility(r)[0]
    rows, cols = audit_exports.table(r, 'stocks')
    assert all(x['evidence_status'] == 'provisional' and not x['source_authenticated'] for x in rows)
    assert 'evidence_status' in cols
    legacy = deepcopy(r)
    legacy.pop('evidence_assessment')
    legacy['modules']['accounting']['status'] = legacy['modules']['attribution']['status'] = 'verified'
    digest = legacy['result_digest']
    view = audit_evidence.projection(b, legacy)
    assert view['modules']['accounting']['status'] == 'provisional'
    assert legacy['modules']['accounting']['status'] == 'verified' and legacy['result_digest'] == digest


def test_receivable_total_does_not_verify_allocation_between_names():
    b, r = full()
    b['actions'] = [dict(date='2024-01-04', code='510300.SH', cash=0, quantity_change=0, source='receivable_dividend')]
    r = audit.analyze(b)
    assert r['modules']['accounting']['status'] == 'verified'
    assert r['modules']['attribution']['status'] == 'provisional'
    assert r['evidence_assessment']['allocated_count'] == 1


def test_failed_account_status_cannot_be_overridden_by_legacy_balanced_flag():
    b, r = full()
    r['modules']['accounting'].update(status='invalid', internally_balanced=True)
    assert audit_evidence.assess(b, r)['status'] == 'needs_reconciliation'


@pytest.mark.parametrize('kind', ['deposit', 'withdrawal', 'external_flow', '入金'])
def test_explicit_external_cash_flow_is_not_imported_as_a_dividend(kind):
    sources = [s for s in files() if s.name != 'actions.csv']
    sources.append(imports.SourceFile('actions.csv', (
        'date,code,cash,quantity_change,kind\n2024-01-03,510300.SH,1000,0,' + kind + '\n').encode()))
    with pytest.raises(ValueError, match='出入金不能记作分红收益'):
        imports.normalize(sources, name='cash flow')


def test_short_period_and_incomplete_benchmark_are_not_annualized_or_compared():
    from qwb.audit_report import performance
    b, _ = full()
    b['platform_returns'] = [dict(date='2024-01-02', benchmark_pct=0), dict(date='2024-01-04', benchmark_pct=3)]
    b['code']['candidates'] = [dict(key='capital_base', value=50000)]
    p = performance(b)
    assert p['start_equity'] == 9995 and p['annual'] is None and p['annual_252'] is None
    assert 'benchmark_total' not in p


def tca_pack():
    from qwb.audit_tca import ORDER_FIELDS, FILL_FIELDS
    def table(fields, rows):
        out = io.StringIO()
        w = csv.DictWriter(out, fieldnames=fields)
        w.writeheader(); w.writerows(rows)
        return out.getvalue()
    orders = [dict(order_id='o1', code='510300.SH', side='buy', quantity=100,
        decision_time='2024-01-02T09:30:00+08:00', decision_price=10,
        arrival_time='2024-01-02T09:31:00+08:00', arrival_price=11,
        end_time='2024-01-02T15:00:00+08:00', end_price=13)]
    fills = [dict(order_id='o1', trade_id='f1', code='510300.SH', side='buy', quantity=60,
        time='2024-01-02T09:32:00+08:00', price=12, fee=5)]
    return dict(schema_version='audit_advanced_inputs_v1', source_kind='synthetic_fixture',
                tca=dict(orders=table(ORDER_FIELDS, orders), fills=table(FILL_FIELDS, fills)))


def test_advanced_upload_keeps_source_order_and_exports_separate_scope_without_network(tmp_path, monkeypatch):
    import urllib.request
    def forbidden(*a, **k): raise AssertionError('audit must never fetch data')
    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    app = create_app(tmp_path)
    sources = files()
    sources.insert(1, imports.SourceFile('audit_evidence.json', json.dumps(tca_pack()).encode()))
    with TestClient(app) as client:
        response = client.post('/strategy-audits/submit', data={'token': app.state.token},
            files=[('backtest1', (f.name, f.data)) for f in sources])
        assert response.status_code == 200
        base = response.url.path
        ev = client.get(base + '/evidence.json').json()
        tca = ev['result']['modules']['implementation_shortfall']
        assert tca['totals']['total_cost'] == '245'
        assert tca['account_link_status'].startswith('separate_submitted_scope')
        assert ev['result']['modules']['attribution']['net_pnl'] == 195
        assert '订单执行损失' in response.text
        assert client.get(base + '/implementation-shortfall.csv').status_code == 200
        assert client.get(base + '/holding-control.csv').status_code == 200
        assert client.get('/strategy-audits/advanced-guide.md').status_code == 200


def test_bad_optional_evidence_does_not_discard_main_account():
    b, _ = full()
    b['advanced_inputs'] = tca_pack()
    b['advanced_inputs']['tca']['fills'] = 'invalid'
    r = audit.analyze(b)
    assert r['modules']['implementation_shortfall']['status'] == 'invalid'
    assert r['modules']['accounting']['status'] == 'verified'


def test_factor_evidence_web_upload_uses_explicit_weights_and_keeps_residual_separate(tmp_path):
    from qwb.audit_factor_attribution import FIELDS
    start, end = '2024-01-02T15:00:00+08:00', '2024-01-04T15:00:00+08:00'
    def table(name, rows):
        stream = io.StringIO()
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(FIELDS[name]); writer.writerows(rows)
        return stream.getvalue()
    pack = dict(schema_version='audit_advanced_inputs_v1', source_kind='synthetic_fixture', factor={
        'scope': json.dumps(dict(schema_version='factor_attribution_scope_v1', start_time=start, end_time=end,
            factors=[dict(name='market', kind='style', definition='synthetic total return', unit='return_per_unit_exposure')],
            max_exposure_age_days=10, holdings_complete=True, cash_return='zero')),
        'periods': table('periods', [['p1', start, end, '0.02', '0.01', 'synthetic-nav']]),
        'weights': table('weights', [['p1', book, code, weight, start, start, 'synthetic-weights']
            for book in ['portfolio', 'benchmark'] for code, weight in [('A', '0.5'), ('CASH', '0.5')]]),
        'exposures': table('exposures', [['A', 'market', start, start, '1', 'synthetic-exposure']]),
        'factor_returns': table('factor_returns', [['p1', 'market', '0.02', end, 'synthetic-factor']]),
    })
    sources = files() + [imports.SourceFile('audit_evidence.json', json.dumps(pack).encode())]
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post('/strategy-audits/submit', data={'token': app.state.token},
            files=[('backtest1', (f.name, f.data)) for f in sources])
        assert response.status_code == 200
        result = client.get(response.url.path + '/evidence.json').json()['result']
        f = result['modules']['factor_attribution']
        assert f['status'] == 'computed' and f['aggregate']['portfolio']['linked_residual'] == '0.010'
        assert f['aggregate']['active']['compounded_return_difference'] == '0.01'
        assert result['modules']['attribution']['net_pnl'] == 195
        csv_result = client.get(response.url.path + '/factor-contributions.csv')
        assert csv_result.status_code == 200 and 'computed_separate_scope' in csv_result.text
        assert '行业风格归因' in response.text


def test_cash_difference_case_cannot_hide_missing_benchmark_in_catalog():
    b, r = full()
    algorithms = [x for s in r['coverage'] for x in s['algorithms']]
    benchmark = next(x for x in algorithms if '相对基准的' in x['name'])
    assert benchmark['inputs'] == 'missing'


def test_wrong_advanced_source_kind_is_rejected_before_any_result_is_written():
    from qwb.audit_advanced import parse
    d = tca_pack()
    d['source_kind'] = 'historical_data'
    with pytest.raises(ValueError, match='source_kind 不匹配'):
        parse(json.dumps(d).encode())
