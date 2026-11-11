"""Web account/factor evidence: binding, known answers, integrity and ZIP boundaries."""
import copy
import csv
import io
import json
from pathlib import Path
import zipfile

import pytest
from fastapi.testclient import TestClient

from qwb import experiments as ex, strategy_audit_store as store, strategy_audit_ui as audit_view
from qwb import strategy_imports as imports, ui
from qwb import validation_evidence as evidence, validation_evidence_ui as view

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples'


def zip_bytes(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files:
            archive.writestr(name, raw)
    return buffer.getvalue()


def account_inputs():
    folder = EXAMPLES / 'account_reconciliation_synthetic'
    bundle = zip_bytes([(name, (folder / name).read_bytes()) for name in evidence.account.FILES])
    return dict(internal=bundle, external=bundle, calendar=(folder / 'calendar.csv').read_bytes())


def factor_inputs():
    folder = EXAMPLES / 'factor_attribution_synthetic'
    return {role: (folder / (role+suffix)).read_bytes() for role, suffix in evidence.ROLES['factor'].items()}


def risk_inputs():
    folder = EXAMPLES / 'risk_model_synthetic'
    return {role:(folder/(role+suffix)).read_bytes() for role,suffix in evidence.ROLES['risk'].items()}


@pytest.fixture
def workspace(tmp_path):
    files = audit_view.sample_files()
    bundle = imports.normalize(files, name='Synthetic supplemental context', opening_cash=10000)
    identity = store.save(tmp_path, bundle, files)
    app = ui.create_app(tmp_path)
    return tmp_path, identity, TestClient(app), {'token': app.state.token, 'input_digest': ex.object_digest(bundle),
        'mode': 'account', 'source_kind': 'synthetic_fixture', 'scope_confirmed': 'yes', 'note': '<script>alert(1)</script>'}


def submit(workspace, mode='account', inputs=None, changes=None):
    _, identity, client, data = workspace
    inputs = inputs or {'account':account_inputs,'factor':factor_inputs,'risk':risk_inputs}[mode]()
    return client.post(f'/strategy-audits/{identity}/validation', data=data | {'mode': mode} | (changes or {}),
        files={role: (role+evidence.ROLES[mode][role], raw) for role, raw in inputs.items()}, follow_redirects=False)


def test_account_web_known_answer_preserves_originals_and_downloads_exact_archives(workspace):
    workdir, identity, client, _ = workspace
    initial = {p.name: p.read_bytes() for p in store.root(workdir, identity).iterdir() if p.is_file()}
    inputs = account_inputs()
    response = submit(workspace, inputs=inputs)
    assert response.status_code == 303
    url = response.headers['location']
    page = client.get(url)
    assert page.status_code == 200 and '日终账户材料匹配' in page.text
    assert '机构准入未建立' in page.text and '双方输入字节完全相同' in page.text
    assert '<script>alert(1)</script>' not in page.text and '&lt;script&gt;' in page.text
    report = client.get(url+'/report.json')
    assert report.headers['cache-control'] == 'no-store'
    assert report.json()['institutional_readiness'] == 'not_established'
    assert report.json()['result']['breaks'] == 0
    assert report.json()['strategy_input_digest'] == workspace[3]['input_digest']
    raw = client.get(url+'/inputs/internal')
    assert raw.content == inputs['internal'] and raw.headers['x-content-type-options'] == 'nosniff'
    assert client.get(url+'/inputs/scope').status_code == 404
    assert all((store.root(workdir, identity)/name).read_bytes() == content for name, content in initial.items())
    assert '已有账户、归因与风险证据' in client.get(f'/strategy-audits/{identity}').text


def test_factor_web_links_multi_period_known_answer_and_keeps_residual_not_alpha(workspace):
    _, _, client, _ = workspace
    response = submit(workspace, 'factor')
    assert response.status_code == 303
    url = response.headers['location']
    page = client.get(url)
    assert '给定因子模型分解已计算' in page.text
    assert '0.9800%' in page.text and '4.0300%' in page.text and '-3.0500%' in page.text
    assert '残差（不是alpha）' in page.text
    report = client.get(url+'/report.json').json()
    assert report['source_kind'] == 'synthetic_fixture'
    assert report['result']['aggregate']['active']['compounded_return_difference'] == '-0.0305'
    assert client.get(url+'/inputs/exposures').content == factor_inputs()['exposures']


def test_account_breaks_and_common_missing_day_are_saved_as_such(workspace):
    _, _, client, _ = workspace
    inputs = account_inputs()
    with zipfile.ZipFile(io.BytesIO(inputs['external'])) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files['balances.csv'] = files['balances.csv'].replace(b',8995,', b',8996,')
    inputs['external'] = zip_bytes(files.items())
    response = submit(workspace, inputs=inputs)
    assert response.status_code == 303
    assert '日终账户存在差异' in client.get(response.headers['location']).text
    assert client.get(response.headers['location']+'/report.json').json()['result']['breaks'] == 3
    files['balances.csv'] = b'\n'.join(r for r in files['balances.csv'].split(b'\n') if b'2025-01-03' not in r)
    inputs['internal'] = inputs['external'] = zip_bytes(files.items())
    response = submit(workspace, inputs=inputs)
    assert response.status_code == 303 and '证据不足' in client.get(response.headers['location']).text


def test_factor_missing_exposure_is_visible_with_no_full_aggregate(workspace):
    _, _, client, _ = workspace
    inputs = factor_inputs()
    inputs['exposures'] = inputs['exposures'].splitlines(keepends=True)[0]
    response = submit(workspace, 'factor', inputs)
    assert response.status_code == 303
    page = client.get(response.headers['location'])
    assert '证据不足' in page.text and '未生成完整多期归因' in page.text and '材料缺口' in page.text
    assert client.get(response.headers['location']+'/report.json').json()['result']['aggregate'] is None


@pytest.mark.parametrize('changes', [{'source_kind': ''}, {'source_kind': 'trusted'}, {'input_digest': 'stale'},
    {'mode': 'unknown'}, {'scope_confirmed': ''}, {'note': 'x'*81}])
def test_bad_or_stale_declarations_cannot_create_evidence(workspace, changes):
    workdir, identity, _, _ = workspace
    assert submit(workspace, changes=changes).status_code == 400
    assert list(evidence.root(workdir, identity).glob('*/report.json')) == []


def test_host_csrf_duplicate_fields_and_bounded_upload(workspace, monkeypatch):
    workdir, identity, client, data = workspace
    assert submit(workspace, changes={'token': 'wrong'}).status_code == 403
    files = [(role, (role+evidence.ROLES['account'][role], raw)) for role, raw in account_inputs().items()]
    url = f'/strategy-audits/{identity}/validation'
    assert client.post(url, data=data, files=files, headers={'host': 'evil.example'}).status_code == 403
    response = client.post(url, files=files+[(k, (None, v)) for k, v in data.items()]+[('mode', (None, 'factor'))])
    assert response.status_code == 400 and '重复字段' in response.text
    monkeypatch.setattr(evidence, 'MAX_BYTES', 10)
    assert submit(workspace).status_code == 400
    assert list(evidence.root(workdir, identity).glob('*/report.json')) == []


@pytest.mark.parametrize('mode', ['account', 'factor', 'risk'])
def test_changed_input_forged_result_and_calculator_versions_fail_closed(workspace, monkeypatch, mode):
    workdir, identity, client, _ = workspace
    response = submit(workspace, mode)
    url = response.headers['location']
    directory = evidence.root(workdir, identity, url.rsplit('/', 1)[1])
    original = json.loads((directory/'report.json').read_text(encoding='utf-8'))
    forged = copy.deepcopy(original)
    forged['result']['status'] = 'computed' if mode == 'account' else 'matched'
    forged['report_digest'] = ex.object_digest({k: v for k, v in forged.items() if k != 'report_digest'})
    (directory/'report.json').write_text(json.dumps(forged), encoding='utf-8')
    assert client.get(url).status_code == client.get(url+'/report.json').status_code == 404
    (directory/'report.json').write_text(json.dumps(original), encoding='utf-8')
    role = next(iter(original['inputs']))
    path = directory/original['inputs'][role]['stored']
    initial = path.read_bytes()
    path.write_bytes(initial+b'\n')
    assert client.get(url+'/inputs/'+role).status_code == 404
    path.write_bytes(initial)
    monkeypatch.setattr(evidence, 'calculator_digest', lambda mode: {'changed.py': 'other'})
    assert client.get(url).status_code == 404
    assert '需重新验证' in client.get(f'/strategy-audits/{identity}').text


def test_append_only_and_cross_strategy_context_rejected(workspace):
    workdir, identity, client, _ = workspace
    first, second = submit(workspace), submit(workspace)
    assert first.headers['location'] != second.headers['location']
    assert len(evidence.history(workdir, identity)) == 2
    sources = audit_view.sample_files()
    other = store.save(workdir, imports.normalize(sources, opening_cash=10000), sources)
    assert client.get(first.headers['location'].replace(identity, other)).status_code == 404
    incomplete = evidence.root(workdir, identity, 'a'*32)
    incomplete.mkdir()
    (incomplete/'internal.zip').write_bytes(b'incomplete')
    assert any(r['status'] == 'requires_revalidation' for r in evidence.history(workdir, identity))


@pytest.mark.parametrize('member', ['../scope.json', '/scope.json', 'nested/scope.json', 'scope.json/evil.py', 'run.py'])
def test_zip_paths_and_extra_code_rejected_without_extraction(member, tmp_path):
    raw = zip_bytes([(member, b'not executed')])
    with pytest.raises(ValueError, match='根目录'):
        evidence.account_zip(raw)
    assert list(tmp_path.iterdir()) == []


def test_zip_duplicates_corruption_member_and_expanded_size_limits(monkeypatch):
    files = [(name, b'field\n') for name in evidence.account.FILES]
    with pytest.warns(UserWarning, match='Duplicate'):
        duplicate = zip_bytes(files+[files[0]])
    with pytest.raises(ValueError, match='重复'):
        evidence.account_zip(duplicate)
    with pytest.raises(ValueError, match='有效'):
        evidence.account_zip(b'not a zip')
    raw = bytearray(zip_bytes(files))
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        info = archive.infolist()[0]
        offset = info.header_offset + 30 + len(info.filename.encode()) + len(info.extra)
        raw[offset:offset+info.compress_size] = b'\xff' * info.compress_size
    with pytest.raises(ValueError, match='有效'):
        evidence.account_zip(bytes(raw))
    monkeypatch.setattr(evidence, 'MAX_EXPANDED_BYTES', 1)
    with pytest.raises(ValueError, match='最多32MB'):
        evidence.account_zip(zip_bytes(files))


def test_templates_contain_no_trades_and_do_not_predeclare_completeness(workspace):
    _, identity, client, _ = workspace
    for mode in ('account', 'factor', 'risk'):
        response = client.get(f'/strategy-audits/{identity}/validation/templates/{mode}.zip')
        assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            if mode == 'account':
                with zipfile.ZipFile(io.BytesIO(archive.read('internal.zip'))) as inner:
                    assert set(inner.namelist()) == set(evidence.account.FILES)
                    assert json.loads(inner.read('scope.json'))['fills_complete'] is False
                    assert len(inner.read('fills.csv').splitlines()) == 1
            else:
                assert json.loads(archive.read('scope.json'))['holdings_complete'] is False
                assert len(archive.read('periods.csv').splitlines()) == 1
                if mode == 'risk':
                    assert set(archive.namelist()) == {role+suffix for role,suffix in evidence.ROLES['risk'].items()}|{'README.txt'}
                    assert len(archive.read('asset_returns.csv').splitlines()) == 1
    assert client.get(f'/strategy-audits/{identity}/validation/templates/unknown.zip').status_code == 404


def change_risk_rows(inputs,role,edit):
    reader=csv.DictReader(io.StringIO(inputs[role].decode()))
    headers=reader.fieldnames;rows=list(reader)
    edit(rows)
    stream=io.StringIO(newline='')
    writer=csv.DictWriter(stream,fieldnames=headers,lineterminator='\n')
    writer.writeheader();writer.writerows(rows)
    inputs[role]=stream.getvalue().encode()


def test_risk_web_known_answer_matches_calculator_and_preserves_raw_inputs(workspace):
    workdir,identity,client,data=workspace
    sources={p.name:p.read_bytes() for p in store.root(workdir,identity).iterdir() if p.is_file()}
    response=submit(workspace,'risk')
    assert response.status_code==303
    url=response.headers['location'];page=client.get(url)
    assert page.status_code==200 and '风险估计与留出诊断已计算' in page.text
    assert '模型未校准' in page.text and '不是预注册前向验证' in page.text
    assert '0.000502667' in page.text and '预期尾部超限少于5次' in page.text
    assert '证券波动贡献' in page.text and '协方差秩不足' in page.text
    assert '<script>alert(1)</script>' not in page.text
    response=client.get(url+'/report.json');report=response.json()
    assert response.headers['cache-control']=='no-store'
    assert report['mode']=='risk' and report['institutional_readiness']=='not_established'
    assert report['result']['risk_model_calibrated'] is False
    inputs=risk_inputs()
    assert report['result']==evidence.risk.analyze(*(inputs[k] for k in evidence.risk.ROLES),source_kind='synthetic_fixture')
    assert report['strategy_input_digest']==data['input_digest']
    assert report['calculator_sha256']==evidence.calculator_digest('risk')
    for role,raw in inputs.items():
        response=client.get(url+'/inputs/'+role)
        assert response.content==raw and response.headers['x-content-type-options']=='nosniff'
    assert all((store.root(workdir,identity)/name).read_bytes()==raw for name,raw in sources.items())
    assert '风险估计与留出诊断已计算' in client.get(f'/strategy-audits/{identity}').text


@pytest.mark.parametrize('stage',['training','holdout'])
def test_risk_missing_material_keeps_distinct_forecast_and_evaluation_states(workspace,stage):
    _,_,client,_=workspace
    inputs=risk_inputs()
    change_risk_rows(inputs,'asset_returns',lambda rows:rows.pop(0 if stage=='training' else -1))
    response=submit(workspace,'risk',inputs)
    assert response.status_code==303
    url=response.headers['location'];report=client.get(url+'/report.json').json()['result'];page=client.get(url)
    assert page.status_code==200 and '证据不足' in page.text
    if stage=='training':
        assert report['forecast'] is None and '未生成风险预测' in page.text
        assert '训练与截止材料缺口' in page.text
    else:
        assert report['forecast'] is not None and report['forecast_status']=='computed'
        assert report['evaluation_status']=='insufficient'
        assert report['evaluation']['portfolio']['observations'] is None
        assert '预测已保留' in page.text and '留出材料缺口' in page.text


def test_risk_holdout_shock_and_future_exposure_leave_prediction_fixed(workspace):
    _,_,client,_=workspace
    first=submit(workspace,'risk');normal=client.get(first.headers['location']+'/report.json').json()['result']
    inputs=risk_inputs()
    def shock(rows):
        for row in rows:
            if int(row['period_id'][1:])>4:row['return']='-0.8'
    change_risk_rows(inputs,'asset_returns',shock)
    change_risk_rows(inputs,'exposures',lambda rows:rows.append(dict(rows[0],available_at=json.loads(inputs['scope'])['end_time'],exposure='999')))
    second=submit(workspace,'risk',inputs)
    after=client.get(second.headers['location']+'/report.json').json()['result']
    assert second.status_code==303 and first.headers['location']!=second.headers['location']
    assert after['forecast']==normal['forecast'] and after['model_digest']==normal['model_digest']
    assert after['evaluation']['portfolio']['exceedances']==3


@pytest.mark.parametrize('kind',['calendar_gap','future_weight','missing_scope'])
def test_bad_risk_material_refused_before_creating_report(workspace,kind):
    workdir,identity,_,_=workspace
    inputs=risk_inputs()
    if kind=='calendar_gap':change_risk_rows(inputs,'calendar',lambda rows:rows.pop(2))
    if kind=='future_weight':change_risk_rows(inputs,'weights',lambda rows:rows[0].update(available_at=json.loads(inputs['scope'])['end_time']))
    if kind=='missing_scope':inputs['scope']=b'{}'
    response=submit(workspace,'risk',inputs)
    assert response.status_code==400
    assert list(evidence.root(workdir,identity).glob('*/report.json'))==[]


def test_risk_backend_version_changes_require_revalidation(workspace,monkeypatch):
    _,identity,client,_=workspace
    response=submit(workspace,'risk');url=response.headers['location']
    before=evidence.calculator_digest('risk')
    monkeypatch.setattr(evidence.risk.scipy,'__version__','changed-backend')
    assert evidence.calculator_digest('risk')!=before
    assert client.get(url).status_code==client.get(url+'/report.json').status_code==404
    assert '需重新验证' in client.get(f'/strategy-audits/{identity}').text


def test_risk_xss_strings_and_zero_active_risk_are_explained(workspace):
    _,_,client,_=workspace;inputs=risk_inputs();scope=json.loads(inputs['scope'])
    scope['model_definition']='<script>alert(2)</script>'
    scope['factors'][0]['definition']='<img src=x onerror=alert(3)>'
    inputs['scope']=json.dumps(scope).encode()
    def identical(rows):
        lookup={r['code']:r['weight'] for r in rows if r['book']=='portfolio'}
        for r in rows:r['weight']=lookup[r['code']]
    change_risk_rows(inputs,'weights',identical)
    response=submit(workspace,'risk',inputs);page=client.get(response.headers['location'])
    assert page.status_code==200 and '零方差预测' in page.text
    assert '<script>alert(2)</script>' not in page.text and '<img src=x' not in page.text
    assert '&lt;script&gt;alert(2)&lt;/script&gt;' in page.text
    assert '模型未校准' in page.text


def test_risk_web_form_uses_seven_distinct_files_and_24mb_limit(workspace,monkeypatch):
    workdir,identity,client,_=workspace
    page=client.get(f'/strategy-audits/{identity}')
    assert 'id="validation-risk"' in page.text and '证券总收益 CSV' in page.text
    monkeypatch.setattr(evidence,'MAX_TOTAL_BYTES',10)
    response=submit(workspace,'risk')
    assert response.status_code==400 and '合计不能超过24MB' in response.text
    assert list(evidence.root(workdir,identity).glob('*/report.json'))==[]
