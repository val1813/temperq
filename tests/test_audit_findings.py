"""Completed facts survive missing broader evidence; scenarios stay labelled."""
from qwb import audit_findings, audit_multi_period, strategy_imports
from qwb.strategy_audit_ui import sample_files


def sample_result():
    bundle = strategy_imports.normalize(sample_files(), adapter='auto', name='synthetic', opening_cash='10000')
    return audit_multi_period.analyze_one(bundle)


def test_completed_account_is_reported_without_claiming_user_confirmation():
    result = sample_result()
    assert result['confirmation']['same_run'] is False
    account = next(row for row in audit_findings.summarize(result) if row['key'] == 'account')
    assert account['label'] == '内部一致'
    assert '183.90' in account['text']
    assert '未来收益' in account['scope']


def test_broken_account_never_gets_verified_pnl_or_stress_profit():
    result = sample_result()
    result['modules']['accounting']['status'] = 'invalid'
    result['modules']['accounting']['exceptions'] = [{'reason': 'synthetic mismatch'}]
    result['modules']['fill_quality'] = {'floor': {'fills': 1, 'net_after': 500}}
    rows = {r['key']: r for r in audit_findings.summarize(result)}
    assert rows['account']['label'] == '账目需修复'
    assert '净盈亏 ¥' not in rows['account']['text']
    assert 'stress' not in rows


def test_capacity_spike_is_disclosed_even_when_95th_percentile_is_below_threshold():
    result = {'modules': {'capacity': {'window_count': 20, 'fills': 20, 'total_fills': 20,
        'p95': .09, 'exceeding_windows': 1, 'ceiling': .1, 'scope': 'fixed path scenario'}}}
    row = audit_findings.summarize(result)[0]
    assert row['label'] == '超过情景门槛'
    assert '1 个窗口' in row['text']
    assert '重跑' in row['action']


def test_code_fee_consistency_does_not_certify_broker_fee():
    result = {'modules': {'fee_model': {'self_check': {
        'status': 'consistent', 'priced_fills': 2, 'gap': .0024}}}}
    row = audit_findings.summarize(result)[0]
    assert row['label'] == '已核实'
    assert '原代码' in row['text']
    assert '券商实际收费' in row['scope']


def test_partial_missing_materials_does_not_invent_completed_checks():
    assert audit_findings.summarize({'modules': {'partial_diagnostics': {}}}) == []
