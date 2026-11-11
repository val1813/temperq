"""Independent synthetic accounts must never be merged by the upload workflow."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from qwb.audit_app import create_app
from qwb import strategy_audit_store as store
from qwb.strategy_audit_ui import sample_files


def period(year=2024, *, code=True):
    files = []
    for source in sample_files():
        if not code and source.name.endswith('.py'):
            continue
        data = source.data.replace(b'2024-', str(year).encode() + b'-')
        files.append((source.name, data))
    return files


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in files:
            z.writestr(name, data)
    return buf.getvalue()


def submit(client, app, periods, **data):
    files = [(f'backtest{slot}', (name, raw)) for slot, members in periods.items() for name, raw in members]
    return client.post('/strategy-audits/submit', data={'token': app.state.token, **data}, files=files,
                       follow_redirects=False)


def batch_from(client, response):
    assert response.status_code == 303, response.text
    link = response.headers['location']
    assert link.startswith('/strategy-audit-batches/')
    assert client.get(link).status_code == 200
    return client.get(link + '/evidence.json').json()


def test_two_periods_reuse_accounting_and_keep_independent_provenance(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        evidence = batch_from(client, submit(client, app, {1: period(), 2: period(2025)},
            name='跨年', opening_cash_1='10000', opening_cash_2='10000'))
        batch = evidence['batch']
        assert len(evidence['evidence']) == 2
        for row, item in zip(batch['periods'], evidence['evidence']):
            assert item['result']['modules']['accounting']['exceptions'] == []
            assert item['result']['modules']['attribution']['net_pnl'] == pytest.approx(183.9)
            assert item['input']['opening']['cash'] == 10000
            assert not item['result']['confirmation']['same_run']
            assert row['return_reported'] == pytest.approx(.01839)
            assert {f['date'][:4] for f in item['input']['fills']} == {str(2023 + row['slot'])}
            assert client.get(f'/strategy-audit-batches/{batch["id"]}/periods/{row["slot"]}').status_code == 200
        assert batch['coverage']['repeated_dates'] == 0
        assert batch['selection_adjustment']['status'] == 'not_computed'
        assert batch['grid']['method'].startswith('audit_grid_')
        assert '多时期' in client.get('/strategy-audits/new').text


def test_shared_code_zip_periods_and_supplement_have_explicit_owner(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        p1, p2 = period(code=False), period(2025, code=False)
        p2 = [(n, b) for n, b in p2 if n != 'logs.csv']
        p1 = [(n, b) for n, b in p1 if n != 'logs.csv']
        files = [('code', ('strategy.py', b'capital_base=10000\n')),
                 ('backtest1', ('first.zip', zipped(p1))), ('backtest2', ('second.zip', zipped(p2))),
                 ('logs', ('extra.csv', '时间,级别,内容\n2025-01-02 09:30:00,INFO,synthetic\n'.encode()))]
        r = client.post('/strategy-audits/submit', data={'token': app.state.token, 'supplement_period': '2'},
                        files=files, follow_redirects=False)
        ev = batch_from(client, r)
        one, two = [i['input'] for i in ev['evidence']]
        assert one['code']['source'] == two['code']['source'] == 'capital_base=10000\n'
        # The supplement area always feeds period 1; other periods carry their own files.
        assert one['logs'][0]['内容'] == 'synthetic'
        assert not two['logs']


def test_overlapping_duplicate_uploads_are_disclosed_and_counted_once(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        ev = batch_from(client, submit(client, app, {1: period(), 2: period()}))
        cov = ev['batch']['coverage']
        assert cov['window_dates'] == 2 * cov['distinct_dates']
        assert cov['repeated_dates'] == cov['distinct_dates']
        assert any('输入完全相同' in s for s in ev['batch']['grid']['strategies'][0]['identity_issues'])


def test_missing_code_and_partial_period_still_have_persisted_reports(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        ev = batch_from(client, submit(client, app, {1: period(code=False), 2: period(2025, code=False)}))
        assert all(row['status'] == 'materials' for row in ev['batch']['periods'])
        assert all(row['return_reported'] is None for row in ev['batch']['periods'])
        link = '/strategy-audit-batches/' + ev['batch']['id']
        assert '各期缺少什么材料' in client.get(link).text
        assert '没有收到策略代码' in client.get(link + '/periods/2').text
        assert ev['batch']['id'] in client.get('/strategy-audits/new').text


def test_same_kind_twice_in_same_period_remains_an_error_before_any_write(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        members = period() + [('second-trades.csv', next(b for n, b in period() if n == 'trades.csv'))]
        r = submit(client, app, {1: members, 2: period(2025)})
        assert r.status_code == 400 and '回测数据 1' in r.text
        assert not (tmp_path / 'runs/strategy_audits').exists()


@pytest.mark.parametrize('analysis_error', [False, True])
def test_single_incomplete_report_has_durable_refresh_and_exports(tmp_path, monkeypatch, analysis_error):
    from qwb import audit_multi_period
    if analysis_error:
        def fail(bundle):
            raise ValueError('synthetic account failure')
        monkeypatch.setattr(audit_multi_period, 'analyze_one', fail)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = submit(client, app, {1: period(code=analysis_error)})
        assert response.status_code == 303
        path = response.headers['location']
        assert '/reports/' in path
        for _ in range(2):
            page = client.get(path)
            assert page.status_code == 200 and '收益能信吗' in page.text
        evidence = client.get(path + '/evidence.json')
        assert evidence.status_code == 200
        result = evidence.json()['result']
        assert result['mode'] == 'materials'
        assert bool(result['analysis_error']) is analysis_error
        if analysis_error:
            assert 'synthetic account failure' in page.text
        else:
            assert '没有收到策略代码' in page.text
        for suffix in ('/report.html', '/checkup.html'):
            assert client.get(path + suffix).status_code == 200


def test_per_period_code_difference_does_not_silently_use_shared_code(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        p2 = [(n, b.replace(b'stock_count = 2', b'stock_count = 3')) for n, b in period(2025)]
        ev = batch_from(client, submit(client, app, {1: period(), 2: p2}))
        assert any('源码不一致' in s for s in ev['batch']['grid']['strategies'][0]['identity_issues'])


def test_batch_or_child_tampering_is_detected(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        ev = batch_from(client, submit(client, app, {1: period(), 2: period(2025)}))
        batch = ev['batch']
        first = batch['periods'][0]
        path = store.root(tmp_path, first['identity']) / 'reports' / first['report_id'] / 'result.json'
        obj = json.loads(path.read_text(encoding='utf-8'))
        obj['settings']['commission_bp'] = 0
        path.write_text(json.dumps(obj), encoding='utf-8')
        assert client.get('/strategy-audit-batches/' + batch['id']).status_code == 404


def test_single_period_legacy_files_still_redirects_to_original_report(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        files = [('files', (n, b)) for n, b in period()]
        r = client.post('/strategy-audits/submit', data={'token': app.state.token}, files=files,
                        follow_redirects=False)
        assert r.status_code == 303 and '/reports/' in r.headers['location']
        assert client.get(r.headers['location']).status_code == 200
        assert client.post('/strategy-audits/submit', data={'token': 'wrong'}, files=files).status_code == 403


def test_five_periods_and_automatic_reference_without_slot_one(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        groups = {i: [('period.zip', zipped(period(2020 + i)))] for i in range(1, 6)}
        ev = batch_from(client, submit(client, app, groups))
        assert len(ev['batch']['periods']) == 5
        ev = batch_from(client, submit(client, app, {2: period(), 3: period(2025)}))
        assert ev['batch']['reference_slot'] == 2
        assert len(ev['batch']['periods']) == 2
        page = client.get('/strategy-audits/new').text
        assert 'name="reference_period"' not in page
        assert '所有已上传时期一起审计' in page


def test_unreconciled_account_retained_without_comparable_returns(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        # Break just the cash identity, not CSV syntax, to exercise comparison gating.
        p2 = [(n, b.replace('现金: 6990'.encode(), '现金: 5990'.encode())) for n, b in period(2025)]
        ev = batch_from(client, submit(client, app, {1: period(), 2: p2}))
        bad = ev['batch']['periods'][1]
        assert bad['comparison_status'] == 'invalid'
        assert bad['return_reported'] is None


def test_declared_fresh_start_runs_enter_existing_grid_path_comparison(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        p2 = [('second-' + n, b) for n, b in period()]
        ev = batch_from(client, submit(client, app, {1: period(), 2: p2},
            opening_cash_1='10000', opening_cash_2='10000'))
        pair = ev['batch']['grid']['strategies'][0]['pairs'][0]
        assert pair['status'] == 'compared'
        assert pair['fresh_minus_continuous_pp'] == pytest.approx(0)


def test_analysis_error_retains_failed_period_instead_of_selecting_successes(tmp_path, monkeypatch):
    from qwb import audit_multi_period as multi
    original = multi.analyze_one

    def analyze(bundle):
        if bundle['fills'][0]['date'].startswith('2025'):
            raise ValueError('synthetic analysis error')
        return original(bundle)

    monkeypatch.setattr(multi, 'analyze_one', analyze)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        ev = batch_from(client, submit(client, app, {1: period(), 2: period(2025)}))
        failed = ev['batch']['periods'][1]
        assert failed['status'] == 'error' and failed['return_reported'] is None
        text = client.get('/strategy-audit-batches/' + ev['batch']['id'] + '/periods/2').text
        assert 'synthetic analysis error' in text
        assert '没有收到策略代码' not in text
