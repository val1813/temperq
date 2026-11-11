import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from qwb import execution_evidence as evidence, experiments as ex, strategy_audit_store as store
from qwb import strategy_audit_ui as audit_view, strategy_imports as imports, ui

FILLS = b'date,order_id,trade_id,code,side,quantity,price,fee\n2024-01-02,o1,t1,510300.SH,buy,100,4.00,5\n'
ORDERS = ('order_id,code,side,quantity,decision_time,decision_price,arrival_time,arrival_price,end_time,end_price\n'
          'p1,510300.SH,buy,100,2024-01-02T09:30:00+08:00,10,2024-01-02T09:31:00+08:00,11,2024-01-02T15:00:00+08:00,13\n').encode()
TCA_FILLS = ('order_id,trade_id,code,side,time,quantity,price,fee\n'
             'p1,t1,510300.SH,buy,2024-01-02T09:32:00+08:00,60,12,5\n').encode()


@pytest.fixture
def workspace(tmp_path):
    files = audit_view.sample_files()
    bundle = imports.normalize(files, name='Synthetic execution evidence context', opening_cash=10000)
    identity = store.save(tmp_path, bundle, files)
    app = ui.create_app(tmp_path)
    data = {'token': app.state.token, 'input_digest': ex.object_digest(bundle), 'mode': 'reconcile',
            'source_kind': 'synthetic_fixture', 'scope_confirmed': 'yes', 'note': '<script>alert(1)</script>'}
    return tmp_path, identity, TestClient(app), data


def submit(workspace, first=FILLS, second=FILLS, changes=None):
    workdir, identity, client, data = workspace
    return client.post(f'/strategy-audits/{identity}/execution', data=data | (changes or {}),
                       files={'first': ('first.csv', first, 'text/csv'), 'second': ('second.csv', second, 'text/csv')},
                       follow_redirects=False)


def test_web_reconciliation_finds_break_preserves_materials_and_exports_exact_inputs(workspace):
    workdir, identity, client, data = workspace
    original = {p.name: p.read_bytes() for p in store.root(workdir, identity).iterdir() if p.is_file()}
    r = submit(workspace, second=FILLS.replace(b',100,', b',200,'))
    assert r.status_code == 303
    url = r.headers['location']
    page = client.get(url)
    assert page.status_code == 200 and '存在订单差异' in page.text
    assert '合成测试材料' in page.text and '来源未认证' in page.text
    assert '<script>alert(1)</script>' not in page.text and '&lt;script&gt;' in page.text
    result = client.get(url + '/report.json')
    report = result.json()
    assert report['result']['breaks'] == 1 and report['result']['rows'][0]['internal_minus_broker']['quantity'] == '-100'
    assert result.headers['cache-control'] == 'no-store'
    raw = client.get(url + '/inputs/internal.csv')
    assert raw.content == FILLS and raw.headers['x-content-type-options'] == 'nosniff'
    assert client.get(url + '/inputs/orders.csv').status_code == 404
    assert all((store.root(workdir, identity) / name).read_bytes() == content for name, content in original.items())
    assert '已有执行证据' in client.get(f'/strategy-audits/{identity}').text


def test_web_tca_preserves_unfilled_opportunity_and_matches_known_answer(workspace):
    _, _, client, _ = workspace
    r = submit(workspace, ORDERS, TCA_FILLS, {'mode': 'tca'})
    assert r.status_code == 303
    page = client.get(r.headers['location'])
    assert '分解已计算' in page.text and '未完成量机会成本' in page.text
    report = client.get(r.headers['location'] + '/report.json').json()
    assert report['result']['totals']['total_cost'] == '245'
    assert report['result']['totals']['opportunity_cost'] == '80'
    assert report['result']['rows'][0]['unfilled_quantity'] == '40'
    assert report['source_kind'] == 'synthetic_fixture'


def test_same_bytes_warning_empty_inputs_and_bad_lifecycle_are_not_false_success(workspace):
    _, _, client, _ = workspace
    r = submit(workspace)
    assert '两份输入字节完全相同' in client.get(r.headers['location']).text
    header = FILLS.splitlines()[0] + b'\n'
    r = submit(workspace, header, header)
    assert r.status_code == 303 and '证据不足' in client.get(r.headers['location']).text
    r = submit(workspace, ORDERS, TCA_FILLS.replace(b',60,', b',101,'), {'mode': 'tca'})
    assert r.status_code == 400 and '超过母订单' in r.text


@pytest.mark.parametrize('changes', [{'source_kind': ''}, {'source_kind': 'trusted'}, {'input_digest': 'stale'},
                                     {'mode': 'unknown'}, {'scope_confirmed': ''}, {'note': 'x' * 81}])
def test_invalid_declarations_or_stale_context_cannot_create_evidence(workspace, changes):
    workdir, identity, _, _ = workspace
    r = submit(workspace, changes=changes)
    assert r.status_code == 400
    assert list(evidence.root(workdir, identity).glob('*/report.json')) == []


def test_auth_size_and_duplicate_fields_are_checked_without_persisting(workspace, monkeypatch):
    workdir, identity, client, data = workspace
    assert submit(workspace, changes={'token': 'bad'}).status_code == 403
    r = client.post(f'/strategy-audits/{identity}/execution', data=data, headers={'host': 'evil.example'},
                    files={'first': ('a.csv', FILLS), 'second': ('b.csv', FILLS)})
    assert r.status_code == 403
    r = client.post(f'/strategy-audits/{identity}/execution',
                    files=[('first', ('a.csv', FILLS)), ('second', ('b.csv', FILLS)),
                           *[(k, (None, v)) for k, v in data.items()], ('mode', (None, 'tca'))])
    assert r.status_code == 400 and '重复字段' in r.text
    monkeypatch.setattr(evidence, 'MAX_BYTES', 10)
    assert submit(workspace).status_code == 400
    assert list(evidence.root(workdir, identity).glob('*/report.json')) == []


def test_recomputed_reports_reject_rehashed_forgery_and_changed_input(workspace):
    workdir, identity, client, _ = workspace
    r = submit(workspace)
    url = r.headers['location']
    evidence_id = url.rsplit('/', 1)[1]
    directory = evidence.root(workdir, identity, evidence_id)
    original = json.loads((directory / 'report.json').read_text(encoding='utf-8'))
    forged = copy.deepcopy(original)
    forged['result']['matched_orders'] = 999
    forged['report_digest'] = ex.object_digest({k: v for k, v in forged.items() if k != 'report_digest'})
    (directory / 'report.json').write_text(json.dumps(forged), encoding='utf-8')
    assert client.get(url).status_code == client.get(url + '/report.json').status_code == 404
    (directory / 'report.json').write_text(json.dumps(original), encoding='utf-8')
    (directory / 'internal.csv').write_bytes(FILLS.replace(b',100,', b',200,'))
    assert client.get(url + '/inputs/internal.csv').status_code == 404
    assert '复算核对失败' in client.get(f'/strategy-audits/{identity}').text


def test_append_only_reports_template_and_cross_strategy_reference(workspace):
    workdir, identity, client, data = workspace
    r1, r2 = submit(workspace), submit(workspace)
    assert r1.headers['location'] != r2.headers['location']
    assert len(evidence.history(workdir, identity)) == 2
    files = audit_view.sample_files()
    other = store.save(workdir, imports.normalize(files, name='Other context', opening_cash=10000), files)
    assert client.get(r1.headers['location'].replace(identity, other)).status_code == 404
    response = client.get(f'/strategy-audits/{identity}/execution/templates/tca.zip')
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {'orders.csv', 'fills.csv', 'README.txt'}
        assert len(archive.read('orders.csv').splitlines()) == 1
    assert client.get(f'/strategy-audits/{identity}/execution/templates/unknown.zip').status_code == 404
