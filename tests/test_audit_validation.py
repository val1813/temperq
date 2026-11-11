"""Frozen cohort protocol, conservative group inference and real static auditing."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from qwb import audit_validation as v


def encoded(value):
    return json.dumps(value, ensure_ascii=False).encode('utf-8')


def settings():
    return dict(commission_bp=3., minimum_commission=5., max_weight=1., thresholds={})


def protocol():
    return dict(cohort_kind='synthetic_development', development_group_ids=['development'],
        defects=[dict(id='price_chain', card_ids=['price_jump'], recall_min=.9, minimum_groups=1)],
        clean_failure_max=.05, minimum_clean_groups=1)


def case(case_id, group_id):
    return dict(id=case_id, group_id=group_id, source_kind='synthetic_fixture', opening_cash=100000.,
        files={case_id+'/prices.csv': v.sha(case_id.encode())})


def label(case_id, defect=None, manifested=False, acceptable=None):
    return dict(case_id=case_id, defect_id=defect, manifested=manifested, acceptable_act_card_ids=acceptable or [])


def reseal(artifact):
    artifact['digest'] = v.object_digest({k: x for k, x in artifact.items() if k != 'digest'})
    return artifact


def predictions(plan, changes=None):
    rows = []
    for c in plan['manifest']['cases']:
        row = dict(case_id=c['id'], group_id=c['group_id'], material_digest=v.object_digest(c['files']),
            status='computed', accounting_status='verified', cards=[], error_type=None)
        row.update((changes or {}).get(c['id'], {}))
        rows.append(row)
    return v._seal('audit_validation_predictions_v1', plan_digest=plan['digest'], rows=rows)


def card(name='price_jump', state='act'):
    return dict(id=name, state=state, evidence='E1')


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(v, 'runtime_fingerprint', lambda: {'test_runtime': 'stable'})
    cases = [case('clean', 'g1'), case('bad', 'g1'), case('not_visible', 'g2')]
    raw = encoded(dict(cases=[label('clean'), label('bad', 'price_chain', True), label('not_visible', 'price_chain')]))
    plan = v.freeze(dict(cases=cases, settings=settings()), protocol(), v.sha(raw))
    return plan, raw


def test_freeze_binds_protocol_rules_code_and_label_commitment(frozen):
    plan, raw = frozen
    assert plan['labels_sha256'] == v.sha(raw)
    assert 'T-PRICE-JUMP' in plan['rules']['thresholds']
    assert 'labels' not in plan
    for field in ('protocol', 'manifest', 'runtime'):
        changed = deepcopy(plan)
        changed[field]['tampered'] = True
        with pytest.raises(ValueError, match='摘要'):
            v.verify_plan(changed)


def test_real_runtime_fingerprint_binds_package_sources_and_reported_versions():
    r = v.runtime_fingerprint()
    assert r['qwb_python_sources']['audit_validation.py'] == v.sha(Path(v.__file__).read_bytes())
    assert any(name.lower() == 'scipy' for name, version in r['distribution_versions'])


def test_environment_or_source_drift_requires_new_plan(frozen, monkeypatch):
    plan, raw = frozen
    monkeypatch.setattr(v, 'runtime_fingerprint', lambda: {'test_runtime': 'changed'})
    with pytest.raises(ValueError, match='源码或依赖环境变化'):
        v.score(plan, predictions(plan), raw)


def test_only_the_predeclared_detector_card_counts_as_a_hit(frozen):
    plan, raw = frozen
    pred = predictions(plan, {'bad': {'cards': [card('state', 'review'), card('fees')]}})
    r = v.score(plan, pred, raw)
    assert r['defects']['price_chain']['detected_cases'] == 0
    pred = predictions(plan, {'bad': {'cards': [card(state='review')]}})
    r = v.score(plan, pred, raw)
    assert r['defects']['price_chain']['detected_cases'] == 1
    assert r['defects']['price_chain']['review_hits'] == 1
    assert r['defects']['price_chain']['act_hits'] == 0
    assert r['defects']['price_chain']['not_manifested_cases'] == 1
    assert r['defects']['price_chain']['group_recall_interval']['n'] == 1
    assert r['institutional_readiness'] == 'not_established'


def test_one_missed_window_fails_its_group_instead_of_inflating_denominator(frozen):
    plan, _ = frozen
    plan['manifest']['cases'].append(case('bad_window2', 'g1'))
    raw = encoded(dict(cases=[label('clean'), label('bad', 'price_chain', True),
        label('bad_window2', 'price_chain', True), label('not_visible', 'price_chain')]))
    plan['labels_sha256'] = v.sha(raw)
    reseal(plan)
    pred = predictions(plan, {'bad': {'cards': [card()]}})
    r = v.score(plan, pred, raw)['defects']['price_chain']
    assert r['manifested_cases'] == 2 and r['detected_cases'] == 1
    assert r['group_all_cases_hit'] == {'g1': False}
    assert r['group_recall_interval']['n'] == 1


def test_acceptable_act_is_distinct_from_false_act_and_review(frozen):
    plan, _ = frozen
    raw = encoded(dict(cases=[label('clean', acceptable=['fees']), label('bad', 'price_chain', True), label('not_visible', 'price_chain')]))
    plan['labels_sha256'] = v.sha(raw)
    reseal(plan)
    r = v.score(plan, predictions(plan, {'clean': {'cards': [card('fees'), card('state', 'review')]}}), raw)
    assert r['clean']['any_act_cases'] == r['clean']['any_review_cases'] == 1
    assert r['clean']['unexpected_act_cases'] == 0
    assert r['clean']['group_failure_interval']['count'] == 0


@pytest.mark.parametrize('status,books', [('error', 'not_verified'), ('incomplete', 'failed')])
def test_failures_are_misses_and_clean_failures_never_disappear(frozen, status, books):
    plan, raw = frozen
    pred = predictions(plan, {x: dict(status=status, accounting_status=books, cards=[card()]) for x in ['clean', 'bad']})
    r = v.score(plan, pred, raw)
    assert r['defects']['price_chain']['detected_cases'] == 0
    assert r['clean']['group_failure_interval']['count'] == 1
    assert r['clean']['operational_error_cases'] == 1
    assert r['operational_error_cases'] == 2


def test_zero_events_has_nonzero_exact_bound_and_multiple_gates_are_adjusted(frozen):
    plan, raw = frozen
    r = v.score(plan, predictions(plan), raw)
    interval = r['clean']['group_failure_interval']
    assert interval['confidence'] == .975  # Two prespecified gates, Bonferroni family 95%.
    assert interval['upper'] == pytest.approx(.9875)
    assert v._interval(0, 60)['upper'] == pytest.approx(1 - .025**(1/60))
    assert v._interval(60, 60)['lower'] == pytest.approx(.025**(1/60))
    assert v._interval(0, 0)['upper'] is None


def test_numeric_success_does_not_authenticate_independent_blind_validation(monkeypatch):
    monkeypatch.setattr(v, 'runtime_fingerprint', lambda: {})
    cases, labels, changes = [], [], {}
    for i in range(100):
        good, bad = f'c{i}', f'b{i}'
        cases.extend([case(good, f'g{i}'), case(bad, f'g{i}')])
        labels.extend([label(good), label(bad, 'price_chain', True)])
        changes[bad] = {'cards': [card()]}
    raw = encoded(dict(cases=labels))
    p = protocol()
    p['cohort_kind'] = 'external_holdout_declared'
    plan = v.freeze(dict(cases=cases, settings=settings()), p, v.sha(raw))
    r = v.score(plan, predictions(plan, changes), raw)
    assert r['numerical_gate'] == 'passed'
    assert r['institutional_readiness'] == 'not_established'
    assert r['independent_custody_authenticated'] is r['chronology_authenticated'] is False
    assert r['thresholds_automatically_changed'] is False


def test_unobserved_defect_and_missing_clean_groups_are_insufficient(frozen):
    plan, _ = frozen
    raw = encoded(dict(cases=[label(x, 'price_chain') for x in ['clean', 'bad', 'not_visible']]))
    plan['labels_sha256'] = v.sha(raw)
    reseal(plan)
    r = v.score(plan, predictions(plan), raw)
    assert r['numerical_gate'] == 'insufficient'
    assert r['clean']['group_failure_interval']['n'] == 0
    assert r['defects']['price_chain']['group_recall_interval']['n'] == 0


@pytest.mark.parametrize('mutation', ['duplicate_case', 'overlap', 'identical_cross_group', 'reused_path', 'case_collision', 'bool_cash', 'zero_cash', 'nan_fee', 'huge_fee', 'unknown_threshold', 'labels_in_manifest', 'unknown_kind'])
def test_invalid_or_nonindependent_cohort_is_refused(frozen, mutation):
    plan, _ = frozen
    m, p = deepcopy(plan['manifest']), deepcopy(plan['protocol'])
    if mutation == 'duplicate_case': m['cases'].append(deepcopy(m['cases'][0]))
    if mutation == 'overlap': p['development_group_ids'].append('g1')
    if mutation == 'identical_cross_group': m['cases'][2]['files'] = {'other/prices.csv': next(iter(m['cases'][0]['files'].values()))}
    if mutation == 'reused_path': m['cases'][2]['files'] = deepcopy(m['cases'][0]['files'])
    if mutation == 'case_collision': m['cases'][0]['files']['CLEAN/PRICES.CSV'] = '1'*64
    if mutation == 'bool_cash': m['cases'][0]['opening_cash'] = True
    if mutation == 'zero_cash': m['cases'][0]['opening_cash'] = 0
    if mutation == 'nan_fee': m['settings']['commission_bp'] = float('nan')
    if mutation == 'huge_fee': m['settings']['commission_bp'] = 10**1000
    if mutation == 'unknown_threshold': m['settings']['thresholds'] = {'not_a_rule': 1}
    if mutation == 'labels_in_manifest': m['cases'][0]['defect_id'] = 'known_to_predictor'
    if mutation == 'unknown_kind': p['cohort_kind'] = 'independent_verified'
    with pytest.raises(ValueError): v.freeze(m, p, '0'*64)


@pytest.mark.parametrize('name', ['../prices.csv', '/prices.csv', 'C:/prices.csv', 'c\\prices.csv', 'c//prices.csv', 'c/./prices.csv', 'c/NUL.csv', 'c/a?.csv', 'c/a\x00.csv', 'c/foo.zip', 'c./prices.csv'])
def test_material_path_escape_and_unsafe_names_are_refused(frozen, name):
    plan, _ = frozen
    m = deepcopy(plan['manifest'])
    m['cases'][0]['files'] = {name: '0'*64}
    with pytest.raises(ValueError): v.freeze(m, plan['protocol'], '0'*64)


@pytest.mark.parametrize('mutation', ['changed_bytes', 'duplicate', 'missing', 'unknown', 'unplanned_defect', 'bad_manifested', 'bad_acceptable'])
def test_labels_must_match_exact_commitment_and_complete_cohort(frozen, mutation):
    plan, raw = frozen
    labels = json.loads(raw)
    if mutation == 'changed_bytes':
        with pytest.raises(ValueError, match='预先承诺'): v.score(plan, predictions(plan), raw+b' ')
        return
    if mutation == 'duplicate': labels['cases'][2] = labels['cases'][0]
    if mutation == 'missing': labels['cases'].pop()
    if mutation == 'unknown': labels['cases'][0]['case_id'] = 'unknown'
    if mutation == 'unplanned_defect': labels['cases'][0]['defect_id'] = 'new_after_viewing'
    if mutation == 'bad_manifested': labels['cases'][0]['manifested'] = True
    if mutation == 'bad_acceptable': labels['cases'][0]['acceptable_act_card_ids'] = ['fees', 'fees']
    raw = encoded(labels)
    plan['labels_sha256'] = v.sha(raw)
    reseal(plan)
    with pytest.raises(ValueError): v.score(plan, predictions(plan), raw)


@pytest.mark.parametrize('mutation', ['plan', 'missing', 'duplicate', 'group', 'hash', 'status', 'book', 'duplicate_card'])
def test_predictions_cannot_change_plan_population_or_validity(frozen, mutation):
    plan, raw = frozen
    pred = predictions(plan)
    if mutation == 'plan': pred['plan_digest'] = '0'*64
    if mutation == 'missing': pred['rows'].pop()
    if mutation == 'duplicate': pred['rows'][1] = deepcopy(pred['rows'][0])
    if mutation == 'group': pred['rows'][0]['group_id'] = 'new'
    if mutation == 'hash': pred['rows'][0]['material_digest'] = '0'*64
    if mutation == 'status': pred['rows'][0]['status'] = 'silently_skipped'
    if mutation == 'book': pred['rows'][0]['accounting_status'] = 'failed'
    if mutation == 'duplicate_card': pred['rows'][0]['cards'] = [card(), card()]
    reseal(pred)
    with pytest.raises(ValueError): v.score(plan, pred, raw)


def test_duplicate_json_keys_and_nonfinite_values_refused(tmp_path):
    p = tmp_path/'a.json'
    for raw in [b'{"x":1,"x":2}', b'{"x":1e999}', b'{"x":NaN}', b'not json']:
        p.write_bytes(raw)
        with pytest.raises(ValueError): v.load(p)


def test_material_changes_are_rejected_before_evaluator_runs(tmp_path, frozen, monkeypatch):
    plan, _ = frozen
    for c in plan['manifest']['cases']:
        for name in c['files']:
            target = tmp_path/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(c['id'].encode())
    (tmp_path/'bad/prices.csv').write_text('changed', encoding='utf-8')
    monkeypatch.setattr(v, '_evaluate', lambda *a: pytest.fail('Do not evaluate changed corpus'))
    with pytest.raises(ValueError, match='SHA256'): v.predict(plan, tmp_path)


def test_per_case_errors_are_preserved_and_no_labels_are_opened(tmp_path, frozen, monkeypatch):
    plan, _ = frozen
    for c in plan['manifest']['cases']:
        target = tmp_path/next(iter(c['files']))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(c['id'].encode())
    (tmp_path/'labels.json').write_bytes(b'not valid JSON and must not be read')
    def fail(*args): raise ValueError('Internal path that should not leak')
    monkeypatch.setattr(v, '_evaluate', fail)
    pred = v.predict(plan, tmp_path)
    assert len(pred['rows']) == 3 and all(r['status'] == 'error' for r in pred['rows'])
    assert 'Internal path' not in json.dumps(pred)


def test_reordering_file_object_does_not_change_consumed_source_order(tmp_path):
    files = {}
    for name in ['z.py', 'a.csv']:
        (tmp_path/name).write_bytes(name.encode())
        files[name] = v.sha(name.encode())
    m = dict(cases=[dict(case('a', 'g'), files=files)], settings=settings())
    first = v._materials(tmp_path, m)['a']
    m['cases'][0]['files'] = dict(reversed(list(files.items())))
    second = v._materials(tmp_path, m)['a']
    assert [s.name for s in first] == [s.name for s in second] == ['a.csv', 'z.py']


def test_runtime_drift_during_prediction_refuses_to_seal_output(tmp_path, frozen, monkeypatch):
    plan, _ = frozen
    for c in plan['manifest']['cases']:
        target = tmp_path/next(iter(c['files']))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(c['id'].encode())
    def change_runtime(*args):
        monkeypatch.setattr(v, 'runtime_fingerprint', lambda: {'test_runtime': 'changed_during_run'})
        return dict(status='computed', accounting_status='verified', cards=[], error_type=None)
    monkeypatch.setattr(v, '_evaluate', change_runtime)
    with pytest.raises(ValueError, match='源码或依赖环境变化'):
        v.predict(plan, tmp_path)


def test_writer_preserves_existing_artifact(tmp_path):
    p = tmp_path/'plan.json'
    v.write({'a': 1}, p)
    before = p.read_bytes()
    with pytest.raises(FileExistsError): v.write({'a': 2}, p)
    assert p.read_bytes() == before


def test_actual_three_stage_cli_uses_static_exports_and_never_executes_source(tmp_path):
    from qwb import audit_benchmark
    cases = []
    marker = tmp_path/'STRATEGY_MUST_NOT_EXECUTE'
    for cid, fault in [('clean', 'clean'), ('bad', 'price_break')]:
        exported = audit_benchmark.simulate(731, fault, 80)
        exported['strategy.py'] += f'from pathlib import Path\nPath({str(marker)!r}).write_text("bad")\n'
        files = {}
        for name, text in exported.items():
            p = tmp_path/cid/name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(text.encode())
            files[cid+'/'+name] = v.sha(p.read_bytes())
        cases.append(dict(case(cid, 'same_generator_family'), files=files))
    labels = encoded(dict(cases=[label('clean'), label('bad', 'price_chain', True)]))
    (tmp_path/'labels.json').write_bytes(labels)
    (tmp_path/'manifest.json').write_bytes(encoded(dict(cases=cases, settings=settings())))
    (tmp_path/'protocol.json').write_bytes(encoded(protocol()))
    def cli(stage, options, expected):
        out = tmp_path/(stage+'.json')
        command = [sys.executable, '-m', 'qwb.cli', 'audit', 'validation', stage, *options, '--out', str(out), '--json']
        r = subprocess.run(command, capture_output=True, text=True, encoding='utf-8')
        assert r.returncode == expected, r.stderr
        return v.load(out)
    p = cli('freeze', ['--manifest', str(tmp_path/'manifest.json'), '--protocol', str(tmp_path/'protocol.json'), '--labels-sha256', v.sha(labels)], 0)
    pred = cli('predict', ['--plan', str(tmp_path/'freeze.json'), '--materials-root', str(tmp_path)], 0)
    assert all(r['accounting_status'] == 'verified' for r in pred['rows'])
    scored = cli('score', ['--plan', str(tmp_path/'freeze.json'), '--predictions', str(tmp_path/'predict.json'), '--labels', str(tmp_path/'labels.json')], 1)
    assert scored['defects']['price_chain']['detected_cases'] == 1
    assert scored['numerical_gate'] == 'failed'  # One family cannot establish the interval targets.
    assert not marker.exists()
    assert p['labels_sha256'] == v.sha(labels)
