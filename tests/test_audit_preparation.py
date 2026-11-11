"""Preparation exports preserve unknowns and do not start an audit or account."""
import hashlib

from fastapi.testclient import TestClient

from qwb import audit_preparation, strategy_imports as imports, strategy_audit_store as store
from qwb.audit_app import create_app


def test_code_only_checklist_does_not_claim_financial_review():
    bundle = imports.normalize([imports.SourceFile('strategy.py', b"raise RuntimeError('must not run')\ncapital_base=10000\n")])
    text = audit_preparation.markdown(bundle)
    assert '仅代码静态检查' in text
    assert '当前不能核验收益、费用或回撤' in text
    assert '输入摘要：' in text
    assert '只改A、只改B、同时改AB四套材料' in text


def test_missing_code_remains_blocked_and_inferred_fills_remain_unknown():
    holdings = ('日期,品种,标的,多空,数量,可用数量,收盘价/结算价,市值/价值,仓位占比\n'
                '2024-01-02,股票,合成甲(600000.XSHG),多,100股,0股,10,1000,10000,10%\n'
                '2024-01-02,,Cash,,,,,9000,,0\n'
                '2024-01-03,股票,合成甲(600000.XSHG),多,200股,100股,11,2200,10100,22%\n'
                '2024-01-03,,Cash,,,,,7900,,0\n')
    files = [imports.SourceFile('holdings.csv', holdings.encode('utf-8'))]
    missing = imports.normalize(files)
    assert '当前材料尚不能启动审计' in audit_preparation.markdown(missing)
    inferred = imports.normalize(files + [imports.SourceFile('strategy.py', b'capital_base=10000\n')])
    text = audit_preparation.markdown(inferred)
    assert '不能据此核验真实逐笔成交与费用' in text
    assert '完整账务核对候选' not in text


def test_download_reflects_import_without_writing_reports_or_changing_inputs(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        sample = client.get('/strategy-audits/sample.zip').content
        response = client.post('/strategy-audits/import', data={'token': app.state.token,
            'name': 'Preparation fixture', 'opening_empty': 'yes', 'opening_cash': '10000'},
            files={'files': ('synthetic.zip', sample)}, follow_redirects=False)
        assert response.status_code == 303
        path = response.headers['location']
        before = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in tmp_path.rglob('*') if p.is_file()}
        checklist = client.get(path + '/preparation.md')
        assert checklist.status_code == 200
        assert 'attachment' in checklist.headers['content-disposition']
        assert checklist.headers['cache-control'] == 'no-store'
        assert checklist.headers['content-type'].startswith('text/markdown')
        assert 'Preparation fixture' in checklist.text
        assert '材料齐全不等于账目已通过' in checklist.text
        identity = path.rsplit('/', 1)[-1]
        assert store.read(tmp_path, identity)[1]['name'] == 'Preparation fixture'
        after = {str(p.relative_to(tmp_path)): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in tmp_path.rglob('*') if p.is_file()}
        assert before == after
        assert not (tmp_path / 'runs/paper').exists()
        assert '下载材料核对清单' in client.get(path).text
        assert client.get('/strategy-audits/unknown/preparation.md').status_code == 404
