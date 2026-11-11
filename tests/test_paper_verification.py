"""Paper evidence must be complete and timely; historical tests are not forward evidence."""
from datetime import date, timedelta
import json
from pathlib import Path
import subprocess
import sys

import pytest

from qwb import experiments, paper, paper_verification as verification, strategy
from test_daily_strategy import fixture


def test_missing_first_session_does_not_silently_move_account_inception(tmp_path, monkeypatch):
    run, panel = fixture(tmp_path, monkeypatch)
    for field in panel:
        panel[field] = panel[field].drop(index='2026-10-19')
    paper.initialize(tmp_path, 'missing_first', run, 100000, [], replay_start='2026-10-19')
    result = paper.tick(tmp_path, 'missing_first', now='2026-10-21T16:00:00+08:00')
    assert result['state'] == 'WAITING_DATA'
    assert result['new_sessions'] == 0
    assert paper.inspect(tmp_path, 'missing_first')['sessions'] == []


def calendar(first='2026-10-19', last='2026-10-30'):
    start, end = date.fromisoformat(first), date.fromisoformat(last)
    return ('date,is_open\n'+''.join(f'{start+timedelta(days=i)},{int((start+timedelta(days=i)).weekday()<5)}\n'
        for i in range((end-start).days+1))).encode()


@pytest.fixture
def account(tmp_path, monkeypatch):
    run, panel = fixture(tmp_path, monkeypatch)
    paper.initialize(tmp_path, 'qa', run, 100000, [], replay_start='2026-10-19')
    paper.tick(tmp_path, 'qa', now='2026-10-27T16:00:00+08:00')
    return tmp_path, paper.location(tmp_path, 'qa'), panel


def rewrite(root, day, change):
    with paper.connection(root) as con:
        row = json.loads(con.execute('SELECT payload FROM sessions WHERE day=?', (day,)).fetchone()[0])
        change(row)
        con.execute('UPDATE sessions SET payload=? WHERE day=?', (json.dumps(row), day))


def check(work, **kwargs):
    return verification.verify(work, 'qa', calendar(), through='2026-10-27', **kwargs)


def codes(report):
    return {i['code'] for i in report['issues']}


def as_forward(root):
    config = experiments.read(root/'strategy.json')
    config.update(scope='forward_paper', activated_at='2026-10-19T08:00:00+08:00')
    experiments.write(root/'strategy.json', config)
    with paper.connection(root) as con:
        con.execute('UPDATE metadata SET value=? WHERE key=?', (experiments.object_digest(config), 'config_hash'))
        rows = con.execute('SELECT day,payload FROM sessions ORDER BY day').fetchall()
        for day, raw in rows:
            row = json.loads(raw)
            row.update(scope='forward_paper', recorded_at=day+'T17:30:00+08:00', decision_recorded_before_execution=True)
            con.execute('UPDATE sessions SET payload=? WHERE day=?', (json.dumps(row), day))


def test_complete_historical_account_is_consistent_but_has_no_forward_evidence(account):
    work, root, _ = account
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    r = check(work)
    assert r['status'] == 'matched' and r['expected_sessions'] == r['observed_sessions'] == 7
    assert r['scope_counts'] == {'historical_replay': 7}
    assert r['locally_eligible_forward_return_intervals'] == 0
    assert r['local_forward_coverage_gate'] == 'insufficient'
    assert r['institutional_readiness'] == 'not_established'
    assert r['account_activated_or_ticked'] is r['account_records_written'] is False
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    assert all(code not in json.dumps(r) for code in ['000001.SZ', '600000.SH', str(root)])


def test_full_coverage_local_flags_cannot_authenticate_time_or_custody(account):
    work, root, _ = account
    as_forward(root)  # Explicitly synthetic tampering fixture, never actual forward evidence.
    r = check(work, minimum_forward_intervals=6)
    assert r['status'] == 'matched', r['issues']
    assert r['locally_eligible_forward_sessions'] == 7
    assert r['locally_eligible_forward_return_intervals'] == 6
    assert r['local_forward_coverage_gate'] == 'passed'
    assert r['source_authenticated'] is r['clock_authenticated'] is False
    assert r['forward_evidence_independently_authenticated'] is False
    assert r['institutional_readiness'] == 'not_established'


def test_declared_synthetic_records_never_fill_forward_requirement(account):
    work, root, _ = account
    as_forward(root)
    r = check(work, minimum_forward_intervals=1, source_kind='synthetic_fixture')
    assert r['status'] == 'matched'
    assert r['locally_eligible_forward_sessions'] == r['locally_eligible_forward_return_intervals'] == 0
    assert r['local_forward_coverage_gate'] == 'insufficient'


@pytest.mark.parametrize('day', ['2026-10-19', '2026-10-22', '2026-10-27'])
def test_missing_start_middle_or_final_session_cannot_pass(account, day):
    work, root, _ = account
    with paper.connection(root) as con:
        con.execute('DELETE FROM sessions WHERE day=?', (day,))
    r = check(work)
    assert r['status'] == 'failed' and day in r['missing_sessions']
    assert 'missing_expected_session' in codes(r)


@pytest.mark.parametrize('mutation,code', [
    ('equity', 'state_equity_mismatch'), ('cash', 'initial_cash_differs_from_declared_account'),
    ('snapshot', 'session_snapshot_id_mismatch'), ('state', 'invalid_account_state'),
    ('observed', 'state_observed_time_mismatch'), ('return', 'daily_return_does_not_match_adjacent_equity'),
    ('protocol', 'session_protocol_hash_mismatch'), ('model', 'session_model_hash_mismatch'),
    ('scope', 'historical_account_relabelled_as_forward'), ('weights', 'invalid_decision_weights'),
    ('execution', 'decision_execution_day_not_next_session'), ('payload_day', 'invalid_session_payload_or_day'),
    ('recorded', 'invalid_recorded_timestamp'), ('future', 'recorded_before_activation_or_after_verification')])
def test_corrupt_or_inconsistent_record_is_not_hidden(account, mutation, code):
    work, root, _ = account
    day = '2026-10-20' if mutation == 'return' else '2026-10-19'
    def change(row):
        if mutation == 'equity': row['equity'] += 1
        if mutation == 'cash': row['state']['cash'] += 1
        if mutation == 'snapshot': row['input_snapshot_id'] = 'other'
        if mutation == 'state': row['state']['cash'] = -1
        if mutation == 'observed': row['state']['observed_at'] = '2026-10-19T15:31:00+08:00'
        if mutation == 'return': row['daily_return'] = .5
        if mutation == 'protocol': row['protocol_hash'] = 'other'
        if mutation == 'model': row['model_hash'] = 'other'
        if mutation == 'scope': row['scope'] = 'forward_paper'
        if mutation == 'weights': row['next_weights'] = {'not-a-holding': -1}
        if mutation == 'execution': row['next_execution_day'] = '2026-10-22'
        if mutation == 'payload_day': row['day'] = '2026-10-20'
        if mutation == 'recorded': row['recorded_at'] = '2026-10-19T16:00:00'
        if mutation == 'future': row['recorded_at'] = '2099-01-01T17:30:00+08:00'
    rewrite(root, day, change)
    r = check(work)
    assert r['status'] == 'failed' and code in codes(r), r['issues']


@pytest.mark.parametrize('mutation,code', [('late', 'forward_record_not_saved_on_session_after_close'),
    ('early', 'forward_record_not_saved_on_session_after_close'), ('decision', 'forward_decision_not_recorded_before_execution')])
def test_forward_label_cannot_replace_actual_recorded_timestamp(account, mutation, code):
    work, root, _ = account
    as_forward(root)
    if mutation == 'decision':
        rewrite(root, '2026-10-19', lambda r: r.update(recorded_at='2026-10-20T09:30:00+08:00'))
    elif mutation == 'late':
        rewrite(root, '2026-10-20', lambda r: r.update(recorded_at='2026-10-21T17:30:00+08:00'))
    else:
        rewrite(root, '2026-10-20', lambda r: r.update(recorded_at='2026-10-20T14:00:00+08:00'))
    r = check(work, minimum_forward_intervals=1)
    assert r['status'] == 'failed' and code in codes(r)


def test_late_scope_creates_no_return_interval_across_it(account):
    work, root, _ = account
    as_forward(root)
    rewrite(root, '2026-10-21', lambda r: r.update(scope='late_reconstruction'))
    r = check(work, minimum_forward_intervals=1)
    assert r['status'] == 'matched'
    assert r['scope_counts']['late_reconstruction'] == 1
    assert r['locally_eligible_forward_return_intervals'] == 4


@pytest.mark.parametrize('kind,code', [('config', 'frozen_configuration_digest_mismatch'),
    ('implementation', 'frozen_execution_implementation_changed'), ('model', 'frozen_model_digest_mismatch')])
def test_changed_frozen_identity_blocks_evidence(account, kind, code):
    work, root, _ = account
    if kind == 'model':
        p = root/'final_model/ridge.json'
        p.write_bytes(p.read_bytes()+b'\n')
    else:
        config = experiments.read(root/'strategy.json')
        config['cash' if kind == 'config' else 'implementation_hash'] = 200000 if kind == 'config' else 'changed'
        experiments.write(root/'strategy.json', config)
    r = check(work)
    assert r['status'] == 'failed' and code in codes(r)
    assert r['locally_eligible_forward_sessions'] == 0


@pytest.mark.parametrize('raw', [b'date,is_open\n2026-10-19,1\n',
    calendar()+b'2026-10-19,1\n', calendar().replace(b'2026-10-20,1', b'2026-10-20,yes')])
def test_complete_natural_date_calendar_is_required(account, raw):
    work, _, _ = account
    with pytest.raises(ValueError): verification.verify(work, 'qa', raw, through='2026-10-27')


def test_missing_account_never_creates_sqlite_or_parent_directory(tmp_path):
    with pytest.raises(ValueError): check(tmp_path)
    assert not paper.location(tmp_path, 'qa').exists()


def test_future_verification_date_and_invalid_minimum_refused(account):
    work, _, _ = account
    with pytest.raises(ValueError, match='尚未完成'): check(work, now='2026-10-27T14:00:00+08:00')
    for n in [0, True, 1.5, 3661]:
        with pytest.raises(ValueError): check(work, minimum_forward_intervals=n)


def test_empty_cash_target_retains_next_execution_date_and_is_valid(tmp_path, monkeypatch):
    run, _ = fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(strategy, 'weights_on', lambda *a, **kw: {})
    paper.initialize(tmp_path, 'qa', run, 100000, [], replay_start='2026-10-19')
    paper.tick(tmp_path, 'qa', now='2026-10-20T16:00:00+08:00')
    rows = paper.inspect(tmp_path, 'qa')['sessions']
    assert rows[0]['next_weights'] == {} and rows[0]['next_execution_day'] == '2026-10-20'
    r = verification.verify(tmp_path, 'qa', calendar(), through='2026-10-20')
    assert r['status'] == 'matched', r['issues']


def test_completed_history_needs_no_fake_forward_days(account):
    work, _, _ = account
    r = check(work, minimum_forward_intervals=1)
    assert r['local_forward_coverage_gate'] == 'insufficient'
    assert r['independent_execution_reconciliation'] is False


def test_unknown_scope_is_reported_without_echoing_its_raw_text(account):
    work, root, _ = account
    rewrite(root, '2026-10-21', lambda r: r.update(scope='private_raw_value'))
    r = check(work)
    assert 'unknown_session_scope' in codes(r)
    assert r['scope_counts']['invalid'] == 1
    assert 'private_raw_value' not in json.dumps(r)


def test_actual_cli_preserves_account_and_existing_output(account):
    work, root, _ = account
    p = work/'calendar.csv'
    p.write_bytes(calendar())
    output = work/'verification.json'
    env = dict(__import__('os').environ, PYTHONPATH=str(Path(paper.__file__).resolve().parents[1]), PYTHONUTF8='1')
    cmd = [sys.executable, '-X', 'utf8', '-m', 'qwb.cli', 'paper', 'verify', 'qa', '--calendar', str(p),
        '--through', '2026-10-27', '--source-kind', 'synthetic_fixture', '--out', str(output), '--json']
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    r = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True, encoding='utf-8')
    assert r.returncode == 1, r.stderr
    assert json.loads(r.stdout)['status'] == 'matched'
    saved = output.read_bytes()
    assert subprocess.run(cmd, cwd=work, env=env, capture_output=True).returncode == 1
    assert output.read_bytes() == saved
    cmd[cmd.index(str(output))] = str(root/'forbidden.json')
    assert subprocess.run(cmd, cwd=work, env=env, capture_output=True).returncode == 1
    assert not (root/'forbidden.json').exists()
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
