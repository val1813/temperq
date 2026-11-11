"""Damaged archive members must yield a recoverable upload error before storage."""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from qwb.audit_app import create_app
from qwb.strategy_audit_ui import sample_files


def damaged_archive(kind, *, workbook=False):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_STORED) as archive:
        if workbook:
            archive.writestr('xl/worksheets/sheet1.xml', b'<worksheet>capital_base</worksheet>')
        else:
            archive.writestr('strategy.py', b'capital_base=10000\n')
    raw = stream.getvalue()
    if kind == 'crc':
        return raw.replace(b'capital_base', b'zapital_base'), '压缩包损坏'
    output = bytearray(raw)
    local, central = output.index(b'PK\x03\x04'), output.index(b'PK\x01\x02')
    offset, value = (6, 1) if kind == 'encrypted' else (8, 99)
    output[local+offset:local+offset+2] = value.to_bytes(2, 'little')
    output[central+offset+2:central+offset+4] = value.to_bytes(2, 'little')
    return bytes(output), '压缩包带密码' if kind == 'encrypted' else '不支持这种压缩方式'


@pytest.mark.parametrize('kind', ['crc', 'encrypted', 'compression'])
@pytest.mark.parametrize('multi', [False, True])
def test_unreadable_member_returns_actionable_error_and_keeps_upload_atomic(tmp_path, kind, multi):
    raw, message = damaged_archive(kind)
    app = create_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        files = [('backtest2' if multi else 'backtest1', ('broken.zip', raw))]
        if multi:
            files += [('backtest1', (source.name, source.data)) for source in sample_files()]
        response = client.post('/strategy-audits/submit', data={'token': app.state.token}, files=files)
        assert response.status_code == 400 and message in response.text
        assert '回测数据 2' in response.text if multi else '回测数据 1' in response.text
        assert not (tmp_path/'runs/strategy_audits').exists()
        assert not (tmp_path/'runs/strategy_audit_batches').exists()
        # The same service still accepts the next upload and exposes a saved report.
        response = client.post('/strategy-audits/submit', data={'token': app.state.token},
            files=[('backtest1', (source.name, source.data)) for source in sample_files()])
        assert response.status_code == 200 and '/reports/' in response.url.path


@pytest.mark.parametrize('kind', ['crc', 'encrypted', 'compression'])
def test_unreadable_xlsx_content_is_also_a_recoverable_upload_error(tmp_path, kind):
    raw, _ = damaged_archive(kind, workbook=True)
    app = create_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/strategy-audits/submit', data={'token': app.state.token},
            files=[('backtest1', ('summary.xlsx', raw)), ('code', ('strategy.py', b'capital_base=10000\n'))])
        assert response.status_code == 400 and '无法读取 xlsx' in response.text
        assert not (tmp_path/'runs/strategy_audits').exists()


@pytest.mark.parametrize('with_code', [False, True])
@pytest.mark.parametrize('metadata', ['summary', 'config'])
def test_summary_and_config_without_primary_tables_still_produce_partial_reports(tmp_path, with_code, metadata):
    if metadata == 'summary':
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('xl/worksheets/sheet1.xml',
                '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row>'
                '<c t="inlineStr"><is><t>total_returns</t></is></c><c><v>0.1</v></c>'
                '</row></sheetData></worksheet>')
        files = [('backtest1', ('summary.xlsx', stream.getvalue()))]
    else:
        files = [('backtest1', ('config.json', b'{"mod": {}}'))]
    if with_code:
        files.append(('code', ('strategy.py', b'capital_base=10000\n')))
    app = create_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post('/strategy-audits/submit', data={'token': app.state.token}, files=files)
        assert response.status_code == 200 and '/reports/' in response.url.path
        evidence = client.get(response.url.path+'/evidence.json').json()
        assert not evidence['input']['fills'] and not evidence['input']['snapshots']
        assert evidence['evidence_assessment']['status'] == 'missing'
        assert '账目对得上' not in response.text
        assert {s['kind'] for s in evidence['input']['sources']} >= {
            'platform_summary' if metadata == 'summary' else 'engine_config'}
