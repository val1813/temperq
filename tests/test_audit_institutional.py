"""The reader sees an actionable answer even when the account is missing."""
from qwb import audit_institutional as ir, audit_multi_period, strategy_imports
from qwb.strategy_audit_ui import sample_files
import pytest
from fastapi.testclient import TestClient
from qwb.audit_app import create_app


def full():
    b = strategy_imports.normalize(sample_files(), adapter='auto', name='synthetic', opening_cash='10000')
    return b, audit_multi_period.analyze_one(b)


def test_fee_fraction_uses_same_reconciled_profit_interval():
    b, r = full()
    a = r['modules']['attribution']
    # Extra non-accounted trades must not contaminate the numerator.
    b['fills'].append({**b['fills'][0], 'fee': 999999.})
    h = ir.checkup(b, r)
    assert f'{a["fees"] / a["gross_pnl"]:.1%}' in h
    assert '999,999' not in h
    assert all(q in h for q in ('收益能信吗', '钱花在哪了', '先做什么'))


def test_partial_answer_does_not_turn_fee_scenario_into_saved_profit():
    b, _ = full()
    b['snapshots'] = []
    r = {'mode': 'partial', 'modules': {'partial_diagnostics': {'modeled_fee_scenario': 1}}}
    h = ir.checkup(b, r)
    assert '先补每日账户详情' in h and '账户详情.csv' in h
    assert '收益还未核实' in h
    assert '费用吃掉费用前盈利' not in h
    assert '预计省' not in h


def test_unclosed_account_hides_attribution_even_if_stale_module_says_verified():
    b, r = full()
    r['modules']['accounting']['status'] = 'invalid'
    h = ir.attribution([{'label': '本期', 'result': r}])
    assert '未核实' in h and '183.90' not in h
    assert '先修复账户差额' in ir.checkup(b, r)


def test_middle_ranked_securities_are_preserved_in_contribution_bridge():
    _, r = full()
    a = r['modules']['attribution']
    a['stocks'] = [{'code': f'{i:06d}.SZ', 'gross_pnl': 1., 'fees': 0., 'net_pnl': 1.} for i in range(12)]
    a.update(gross_pnl=12., fees=0., net_pnl=12.)
    # Force the reported drawdown to have an unambiguous closed bridge.
    r['modules']['risk_analysis'] = {}
    h = ir.attribution([{'label': '本期', 'result': r}])
    assert all(f'{i:06d}.SZ' in h for i in range(12))


def test_code_only_has_action_and_one_page_output_without_fake_amount():
    b = {'name': 'code only', 'code': {'candidates': []}, 'quality': {}, 'sources': [], 'fills': [], 'positions': [], 'snapshots': []}
    r = {'mode': 'static', 'modules': {}}
    h = ir.checkup_document(b, r)
    assert '先上传同一次回测的交易与账户' in h
    assert 'window.print()' in h and 'size:A4' in h
    assert '¥0.00' not in h


@pytest.mark.parametrize('with_trades', [False, True])
def test_limited_report_has_three_answers_before_details_and_can_be_saved(tmp_path, with_trades):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        originals = sample_files()
        files = [('code', (f.name, f.data)) for f in originals if f.name.endswith('.py')]
        if with_trades:
            files += [('backtest1', (f.name, f.data)) for f in originals if f.name == 'trades.csv']
        response = client.post('/strategy-audits/submit', data={'token': app.state.token}, files=files)
        assert response.status_code == 200
        primary = response.text.split('<details class="ir-appendix">', 1)[0]
        assert all(word in primary for word in ('收益能信吗', '钱花在哪了', '先做什么'))
        assert '以下项目本次无法审计' not in primary
        base = response.url.path
        assert client.get(base + '/checkup.html').status_code == 200
        assert client.get(base + '/report.html?download=1').status_code == 200


def test_report_time_is_labelled_in_local_timezone():
    assert ir.local_time('2026-11-10T13:18:19+00:00') == '2026-11-10 21:18:19（北京时间）'


def test_inferred_cash_cannot_certify_profit_or_name_contribution():
    b, r = full()
    # A positive cash change might be a deposit. A balancing dividend proposal
    # must not turn that unknown source into a verified investment gain.
    b['actions'] = [{'date': b['snapshots'][-1]['date'], 'code': '600000.SH',
                     'cash': 1000., 'quantity_change': 0, 'inferred': True}]
    old_digest = r['result_digest']
    h = ir.checkup(b, r)
    assert '使用推定事件后账面闭合' in h and '先补分红送转与出入金记录' in h
    assert '费用吃掉费用前盈利' not in h and 'ir-status pass' not in h
    columns = [{'label': '本期', 'bundle': b, 'result': r}]
    assert '先补推定事件' in ir.batch_checkup(columns)
    attr = ir.attribution(columns)
    assert '未核实' in attr and '原始分红送转' in attr
    assert ir.checked_attribution(r, b) is None
    # Display updates apply to old reports without rewriting their evidence.
    assert r['result_digest'] == old_digest and r['modules']['accounting']['status'] == 'verified'
    assert '使用推定事件后账面闭合' in ir.checkup_document(b, r)


def test_supplied_corporate_action_is_not_treated_as_inferred():
    b, r = full()
    b['actions'] = [{'date': b['snapshots'][-1]['date'], 'code': '600000.SH',
                     'cash': 1000., 'quantity_change': 0, 'inferred': False}]
    assert ir.checked_attribution(r, b)['verified']
    assert 'ir-status pass' in ir.checkup(b, r)


def test_approximate_dsr_is_not_reported_as_probability_of_profitable_strategy():
    from qwb.audit_spec import check_trials
    equity = 100.
    snapshots = []
    for i in range(100):
        equity *= 1.002 if i % 3 else .999
        snapshots.append({'equity': equity})
    out = check_trials({'snapshots': snapshots}, {'trials': '10'})
    assert 'DSR=' in out['verdict'] and '真实夏普大于零的概率' not in out['verdict']
    assert '未校准' in out['scope'] and '不保证校正保守' in out['scope']
