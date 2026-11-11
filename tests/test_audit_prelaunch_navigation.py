"""Exercise public template links and keep navigation independent of full ledgers."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from qwb import audit_materials as materials, audit_multi_period as multi
from qwb import strategy_audit_store as store, strategy_audit_ui as view
from qwb import strategy_imports as imports
from qwb.audit_app import create_app


def test_every_advertised_material_template_downloads_for_each_adapter(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        for kind in materials.MATERIALS:
            for adapter in ('auto', 'generic', 'bigquant', 'joinquant', 'ricequant'):
                response = client.get(f'/strategy-audits/templates/{kind}.zip?adapter={adapter}')
                assert response.status_code == 200, (kind, adapter)
                with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                    filename, _ = materials.template(kind, adapter)
                    assert archive.read(filename).decode('utf-8-sig').strip()
                    assert '不含示例数据' in archive.read('填写说明.txt').decode('utf-8-sig')
        assert client.get('/strategy-audits/templates/unknown.zip').status_code == 404


def test_order_template_fields_can_be_filled_and_imported():
    filename, header = materials.template('orders')
    source = imports.SourceFile(filename, (header +
        '2024-01-02 09:30:00,600000.SH,BUY,100,order,1,FILLED,100,\n').encode())
    assert materials.material_kind(source) == 'orders'
    bundle = imports.normalize(view.sample_files() + [source], adapter='auto', name='orders')
    assert len(bundle['orders']) == 1
    assert bundle['orders'][0]['quantity'] == bundle['orders'][0]['filled'] == 100
    assert bundle['orders'][0]['status'] == 'filled'


def test_material_management_opens_when_account_has_supplied_corporate_actions(tmp_path):
    files = [imports.SourceFile(name, text.encode()) for name, text in {
        'strategy.py': 'capital_base=10000\n',
        'account.csv': 'date,equity,cash\n2024-01-02,10000,10000\n2024-01-03,10000,10000\n',
        'actions.csv': 'date,code,cash,quantity_change\n2024-01-03,600000.SH,0,0\n',
    }.items()]
    bundle = imports.normalize(files, name='actions', opening_cash=10000)
    identity = store.save(tmp_path, bundle, files)
    with TestClient(create_app(tmp_path)) as client:
        response = client.get('/strategy-audits/'+identity)
        assert response.status_code == 200 and '分红送转等公司行动' in response.text
        assert 'actions.csv' in response.text


def test_history_navigation_does_not_reaudit_every_ledger_but_report_open_still_checks_integrity(tmp_path, monkeypatch):
    files = view.sample_files()
    bundle = imports.normalize(files, name='history', opening_cash=10000)
    batch = multi.run(tmp_path, '批次', [dict(slot=i, label=f'回测数据 {i}',
        bundle=bundle, files=files, experiment='fresh_start') for i in (1, 2)])
    row = batch['periods'][0]
    report_path = f'/strategy-audits/{row["identity"]}/reports/{row["report_id"]}'
    with monkeypatch.context() as patch:
        def unexpected(*args, **kwargs):
            raise AssertionError('Navigation must not read full input, sources or result files')
        patch.setattr(store, 'read', unexpected)
        patch.setattr(store, 'report', unexpected)
        patch.setattr(multi, 'read', unexpected)
        html = view.report_history(tmp_path)
        assert f'/strategy-audit-batches/{batch["id"]}' in html
        assert report_path not in html  # Batch children are grouped under the batch.
        with TestClient(create_app(tmp_path)) as client:
            assert client.get('/strategy-audits/new').status_code == 200
    raw = store.root(tmp_path, row['identity']) / 'source-0.bin'
    raw.write_bytes(raw.read_bytes() + b'\n')
    with pytest.raises(ValueError, match='摘要'):
        store.report(tmp_path, row['identity'], row['report_id'])
    with pytest.raises(ValueError, match='摘要'):
        multi.read(tmp_path, batch['id'])
    with TestClient(create_app(tmp_path)) as client:
        assert client.get(report_path).status_code == 404
        assert client.get(f'/strategy-audit-batches/{batch["id"]}').status_code == 404
