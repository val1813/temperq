"""Reader decisions preserve units, periods, evidence and runnable upload paths."""
from copy import deepcopy

from fastapi.testclient import TestClient

from qwb import audit_institutional as ir, audit_multi_period, audit_reader, audit_verification, strategy_imports
from qwb.audit_app import create_app
from qwb.strategy_audit_ui import sample_files, verification_page


def full():
    bundle = strategy_imports.normalize(sample_files(), name='reader fixture', opening_cash='10000')
    return bundle, audit_multi_period.analyze_one(bundle)


def findings_case():
    bundle, result = full()
    # Presentation fixtures: these already-computed diagnostics stand for a
    # larger audit. Tests check their meaning, not the underlying ledger math.
    bundle['opening']['cash'] = 1000000.
    result['modules']['capacity'] = dict(fills=9, total_fills=12, ceiling=.1,
        window_count=9, p95=.32, scope='synthetic fixture', exceeding_windows=1,
        windows=[dict(date='2024-01-03', code='159915.SZ', participation=1/3)])
    result['modules']['risk_analysis'] = {'max_drawdown': dict(loss=338000., ratio=.255,
        peak_date='2024-01-02', trough_date='2024-01-03', contribution_gap=0.,
        stocks=[dict(code='159915.SZ', net_pnl=-338000., gross_pnl=-338000., fees=0.)])}
    result['modules']['premise'] = dict(status='not_supported', overall=dict(periods=40, mean=.01, t=.2))
    return bundle, result


def test_front_page_uses_observed_window_and_drawdown_period_and_two_actions():
    b, r = findings_case()
    page = ir.checkup(b, r)
    assert '33.3%' in page and '成交附近5分钟的买卖量' in page
    assert '25.5%' in page and '33.8万元' in page and '这段下跌中' in page
    assert '100.0%' in page and '所有亏损标的金额' in page
    assert '100.0万元降到30.0万元' in page
    assert '简单等权对照' in page and '选股规则尚未显示正向排序优势' in page
    assert '参与率' not in page and '原引擎' not in page
    assert '比随机选更差' not in page and '手续费不高' not in page
    assert 'ir-status pass' not in page  # execution risk stays visible next to consistent books
    assert len(audit_reader.tasks([{'label': '本期', 'bundle': b, 'result': r}])) == 2


def test_unknown_initial_capital_does_not_invent_a_yuan_budget():
    b, r = findings_case()
    b['opening'] = {'kind': 'first_close', 'cash': None}
    task = next(t for t in audit_reader.tasks([{'label': '本期', 'bundle': b, 'result': r}]) if t['key'] == 'capacity')
    assert task['test_capital'] is None and '30.0%' in task['action']
    assert '30.0万元' not in task['action'] and '期初持仓' in task['action']


def test_unreconciled_name_attribution_cannot_promote_a_loss_as_confirmed():
    b, r = findings_case()
    r['modules']['accounting']['status'] = 'provisional'
    b['actions'] = [dict(date='2024-01-03', inferred=True, cash=1000.)]
    page = ir.checkup(b, r)
    assert '先补分红送转' in page and '33.8万元' not in page
    plan = audit_reader.plan([{'label': '本期', 'bundle': b, 'result': r}])
    assert plan.index('先补凭证或修复账目') < plan.index('订单相对市场成交量偏大')


def test_missing_or_weak_premise_evidence_is_not_a_negative_result():
    b, r = full()
    for premise in [dict(status='needs_scores'), dict(status='insufficient', overall=dict(periods=3, t=None)),
                    dict(status='not_supported', overall=dict(periods=3, t=None))]:
        r['modules']['premise'] = premise
        assert not any(t['key'] == 'premise' for t in audit_reader.observations([{'label': '本期', 'bundle': b, 'result': r}]))


def test_same_minute_funding_dependency_does_not_hide_completed_risk_diagnoses():
    b, r = findings_case()
    r['modules']['sequence'] = {'funding': {'checked_switches': 1, 'dependent': 1, 'scope': 'dependency only'}}
    page = ir.batch_checkup([{'label': '本期', 'bundle': b, 'result': r}])
    assert '100.0万元降到30.0万元' in page and '简单等权对照' in page


def test_multiple_periods_select_worst_risk_and_do_not_pool_profit_denominators():
    b, r = findings_case()
    other = deepcopy(r)
    other['modules']['capacity']['windows'][0]['participation'] = .13
    columns = [{'label': '回测数据 1', 'bundle': b, 'result': other}, {'label': '回测数据 3', 'bundle': b, 'result': r}]
    page = ir.batch_checkup(columns)
    assert '回测数据 3：159915.SZ' in page and '33.3%' in page
    assert '费用占费用前盈利' in page and '已检验的2期' in page


def test_missing_control_is_collapsed_and_computed_control_remains_visible():
    b, r = full()
    page = ir.value_section([{'label': '本期', 'bundle': b, 'result': r}])
    assert page.startswith('<details class="ir-optional">')
    assert '本次尚无完整的同起点持有对照' not in page


def test_web_task_downloads_use_uploaded_records_and_preserve_archived_result(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        files = [('backtest1', (s.name, s.data)) for s in sample_files()]
        response = client.post('/strategy-audits/submit', data={'token': app.state.token, 'opening_cash': '10000'}, files=files)
        path = response.url.path
        before = client.get(path + '/evidence.json').json()
        task = client.get(path + '/next-steps.md')
        assert task.status_code == 200 and '本工具不提供或下载数据' in task.text
        assert '接受条件' in task.text and '尚未执行' in task.text
        assert client.get(path + '/evidence.json').json() == before
        identity = path.split('/')[2]
        # The old comparison workspace now returns to the single entry page.
        assert client.get('/strategy-comparison', params={'strategy': identity}, follow_redirects=False).status_code == 303
        compare = client.get('/strategy-audits/new', params={'mode': 'compare', 'original': identity})
        assert compare.status_code == 200 and f'value="{identity}"' in compare.text
        response = client.post('/strategy-audits/submit', data={'token': app.state.token},
            files=files + [('backtest2', (s.name, s.data.replace(b'2024-', b'2025-'))) for s in sample_files()])
        page = response.text
        assert '<details class="ir-appendix" open><summary>详细报告列表</summary>' in page
        assert '<details class="period-path-details">' in page
        assert client.get(response.url.path + '/next-steps.md').status_code == 200


def test_new_capital_upload_uses_new_start_and_demands_complete_account_materials(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        files = [('backtest1', (s.name, s.data)) for s in sample_files()]
        response = client.post('/strategy-audits/submit', data={'token': app.state.token, 'opening_cash': '10000'}, files=files)
        identity = response.url.path.split('/')[2]
        form = {'token': app.state.token, 'rerun_opening': 'empty', 'rerun_cash': '3000'}
        fills = next(s for s in sample_files() if s.name == 'trades.csv')
        bad = client.post(f'/strategy-audits/{identity}/optimized', data=form, files={'files': (fills.name, fills.data)})
        assert bad.status_code == 400 and '不能沿用旧版账目' in bad.text
        b, _ = full()
        new_files = {'strategy.py': 'capital_base=3000\n'}
        for name, key, fields in [('fills.csv', 'fills', ['date', 'code', 'side', 'quantity', 'price', 'fee']),
                                  ('positions.csv', 'positions', ['date', 'code', 'quantity', 'price'])]:
            new_files[name] = ','.join(fields) + '\n' + ''.join(','.join(str(row[f]) for f in fields) + '\n' for row in b[key])
        new_files['account.csv'] = 'date,equity,cash\n' + ''.join(f'{s["date"]},{s["equity"]-7000},{s["cash"]-7000}\n' for s in b['snapshots'])
        response = client.post(f'/strategy-audits/{identity}/optimized', data=form,
            files=[('files', (name, text.encode())) for name, text in new_files.items()], follow_redirects=False)
        assert response.status_code == 303, response.text
        new_identity = response.headers['location'].split('/')[-1]
        from qwb import strategy_audit_store
        _, bundle = strategy_audit_store.read(tmp_path, new_identity)
        assert bundle['opening']['cash'] == 3000
        assert strategy_audit_store.read(tmp_path, identity)[1]['opening']['cash'] == 10000
        from qwb.experiments import object_digest
        confirmed = client.post(f'/strategy-audits/{new_identity}/analyze', data={'token': app.state.token,
            'input_digest': object_digest(bundle), 'same_run': 'yes',
            'commission_bp': '3', 'minimum_commission': '5', 'max_weight': '50'}, follow_redirects=False)
        assert confirmed.status_code == 303
        report_id = next((strategy_audit_store.root(tmp_path, new_identity) / 'reports').glob('*/result.json')).parent.name
        evidence = client.get(f'/strategy-audits/{new_identity}/reports/{report_id}/evidence.json').json()
        assert evidence['result']['modules']['accounting']['status'] == 'verified'
        from qwb import audit_verification
        def latest(i):
            path = max((strategy_audit_store.root(tmp_path, i) / 'reports').glob('*/result.json'), key=lambda q: q.stat().st_mtime)
            return strategy_audit_store.report(tmp_path, i, path.parent.name)[2]
        assert audit_verification.compare(latest(identity), latest(new_identity))['state'] == 'scale_sensitivity'


def test_different_capitals_show_rates_without_claiming_an_improvement():
    b, a = full()
    other = deepcopy(b)
    other['opening']['cash'] = 20000
    for snap in other['snapshots']:
        snap['cash'] += 10000
        snap['equity'] += 10000
    c = audit_multi_period.analyze_one(other)
    comparison = audit_verification.compare(a, c)
    assert comparison['state'] == 'scale_sensitivity' and not comparison['comparable']
    assert comparison['scale_observations'][1]['reported_return'] == comparison['scale_observations'][0]['reported_return'] / 2
    assert 'verdict' not in comparison
    page = verification_page('original', {'id': 'original', 'name': 'original'}, a, [],
        ({'id': 'new', 'name': 'new'}, c), 'test')
    assert '缩小资金后的历史表现' in page and '不将缩资算作新增盈利' in page


def test_bigquant_rerun_accepts_explicit_account_rows_embedded_in_new_holdings(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        files = [('backtest1', (s.name, s.data)) for s in sample_files()]
        response = client.post('/strategy-audits/submit', data={'token': app.state.token, 'opening_cash': '10000'}, files=files)
        identity = response.url.path.split('/')[2]
        uploaded = client.post(f'/strategy-audits/{identity}/optimized',
            data={'token': app.state.token, 'rerun_opening': 'empty', 'rerun_cash': '10000'},
            files=[('files', (s.name, s.data)) for s in sample_files()], follow_redirects=False)
        assert uploaded.status_code == 303
