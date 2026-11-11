"""The default report answers the reader's questions without hiding the diagnosis."""
from copy import deepcopy
from html.parser import HTMLParser

from fastapi.testclient import TestClient
from qwb import audit_readable, audit_report, audit_multi_period, strategy_imports
from qwb.audit_app import create_app
from qwb.strategy_audit_ui import sample_files


def full():
    bundle = strategy_imports.normalize(sample_files(), name='可读报告', opening_cash='10000')
    return bundle, audit_multi_period.analyze_one(bundle)


class Structure(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.stack, self.locations = [], {}
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            assert attrs['id'] not in self.locations, attrs['id']
            self.locations[attrs['id']] = list(self.stack)
        if tag not in {'meta', 'link', 'input', 'img', 'br', 'hr'}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            del self.stack[index:]


def test_default_report_restores_three_questions_and_unfolded_seven_chapters():
    b, r = full()
    before = deepcopy(r)
    r['modules']['premise'] = dict(status='not_supported', source='synthetic fixture', premise='测试前提',
        verdict='详细统计解释：需要5044年只是条件情景', overall=None)
    html = audit_report.render({}, b, r)
    intro = html.split('<h2 id="report-conclusions">')[0]
    assert all(q in intro for q in ('收益能信吗', '你的想法对吗', '该改什么'))
    assert '还没证实这套选法有优势' in intro and '5044' not in intro
    assert '<details class="reader-statistics"><summary>查看完整统计解释与样本量情景</summary>' in html
    assert '5044' in html
    structure = Structure(html)
    assert all(key in structure.locations and 'details' not in structure.locations[key] for key, _ in audit_readable.CHAPTERS)
    assert r['result_digest'] == before['result_digest']  # presentation never rewrites archived evidence


def test_inferred_cash_cannot_be_promoted_by_the_restored_summary_or_attribution():
    b, r = full()
    b['actions'] = [dict(date=b['snapshots'][-1]['date'], code='600000.SH', cash=1000., inferred=True)]
    html = audit_report.render({}, b, r)
    intro = html.split('<h2 id="report-conclusions">')[0]
    assert '收益待核实' in intro and '先补分红送转和出入金记录' in intro
    assert '账目对得上，执行仍要核查' not in intro
    assert '未出已核实归因' in html


def test_web_and_download_share_readable_default_and_working_artifact_routes(tmp_path):
    app = create_app(tmp_path)
    with TestClient(app) as client:
        response = client.post('/strategy-audits/submit', data={'token': app.state.token, 'opening_cash': '10000'},
            files=[('backtest1', (source.name, source.data)) for source in sample_files()])
        assert response.status_code == 200
        base = response.url.path
        evidence = client.get(base + '/evidence.json').json()
        for html in (response.text, client.get(base + '/report.html').text):
            assert 'class="tq-report"' in html
            assert all(key in Structure(html).locations for key, _ in audit_readable.CHAPTERS)
            assert '附录 · 完整计算明细' not in html
        assert f'href="{base}/next-steps.md"' in response.text
        for name in ('checkup.html', 'next-steps.md', 'stocks.csv', 'daily.csv'):
            assert client.get(base + '/' + name).status_code == 200
        assert client.get(base + '/evidence.json').json() == evidence
