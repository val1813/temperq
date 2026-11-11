"""The audit edition exposes only offline audit flows and explicit confirmation."""
from html.parser import HTMLParser
import io
import json
import zipfile

from fastapi.testclient import TestClient

from qwb.audit_app import create_app
from qwb import strategy_audit_store as store


class Form(HTMLParser):
    def __init__(self, text, action):
        super().__init__()
        self.action, self.active, self.values = action, False, {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'form':
            self.active = attrs.get('action') == self.action
        if tag == 'input' and self.active and attrs.get('name') and attrs.get('type') not in {'file', 'checkbox', 'radio'}:
            self.values[attrs['name']] = attrs.get('value', '')

    def handle_endtag(self, tag):
        if tag == 'form':
            self.active = False


def import_sample(client, app):
    sample = client.get('/strategy-audits/sample.zip').content
    response = client.post('/strategy-audits/import', data={'token': app.state.token,
        'name': 'Synthetic audit edition', 'adapter': 'auto', 'opening_empty': 'yes', 'opening_cash': '10000'},
        files={'files': ('synthetic.zip', sample)}, follow_redirects=False)
    assert response.status_code == 303, response.text
    return response.headers['location']


def test_offline_edition_has_no_training_download_or_account_automation_routes(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        assert client.get('/').url.path == '/strategy-audits/new'
        page = client.get('/strategy-audits/new').text
        assert '支持多时期审计' in page and '回测时期' in page and '选填' in page
        assert '提交审计' in page and 'Tushare' not in page and 'api_token' not in page
        for path in ('/train', '/run', '/data', '/data/tushare', '/data/free', '/start', '/models',
                     '/paper', '/jobs', '/job/123/retry', '/paper/account/automation'):
            assert client.get(path).status_code == 404
            assert client.post(path, data={'token': app.state.token}).status_code == 404
        assert '/data' not in client.get('/settings').text
    assert not (tmp_path / 'runs/jobs').exists()
    assert not (tmp_path / 'runs/paper').exists()


def test_upload_confirm_audit_and_export_in_audit_only_app(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    assert client.post('/strategy-audits/import', data={'token': 'wrong'}).status_code == 403
    path = import_sample(client, app)
    page = client.get(path).text
    assert '一键审计' in page and '补充或替换选填材料' in page
    assert '独立成交、账户与风险证据（选填）' in page
    fields = Form(page, path + '/analyze').values
    assert client.post(path + '/analyze', data=fields).status_code == 400
    assert client.post(path + '/analyze', data={**fields, 'same_run': 'yes', 'input_digest': 'changed'}).status_code == 400
    response = client.post(path + '/analyze', data={**fields, 'same_run': 'yes'}, follow_redirects=False)
    assert response.status_code == 303
    identity = path.rsplit('/', 1)[-1]
    report = next((store.root(tmp_path, identity) / 'reports').glob('*/result.json')).parent.name
    base = path + '/reports/' + report
    evidence = client.get(base + '/evidence.json').json()
    assert evidence['result']['modules']['accounting']['exceptions'] == []
    assert abs(evidence['result']['modules']['attribution']['net_pnl'] - 183.9) < 1e-6
    for suffix in ('/report.html', '/stocks.csv', '/daily.csv'):
        assert client.get(base + suffix).status_code == 200
    for route in ('strategy-governance', 'strategy-attribution', 'strategy-comparison', 'strategy-simulation'):
        assert client.get('/' + route + '?strategy=' + identity).status_code == 200
    assert client.get(base + '/evidence.json').json() == evidence


def test_code_only_remains_static_and_uploaded_code_is_not_executed(tmp_path):
    app = create_app(tmp_path)
    client = TestClient(app)
    code = "raise RuntimeError('uploaded code must never execute')\ncapital_base=10000\n"
    response = client.post('/strategy-audits/import', data={'token': app.state.token,
        'name': 'Static only', 'code_text': code}, follow_redirects=False)
    assert response.status_code == 303
    path = response.headers['location']
    fields = Form(client.get(path).text, path + '/analyze').values
    response = client.post(path + '/analyze', data={**fields, 'same_run': 'yes'}, follow_redirects=False)
    assert response.status_code == 303
    identity = path.rsplit('/', 1)[-1]
    report = next((store.root(tmp_path, identity) / 'reports').glob('*/result.json'))
    result = json.loads(report.read_text(encoding='utf-8'))
    assert result['mode'] == 'static' and 'accounting' not in result['modules']
