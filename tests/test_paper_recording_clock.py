"""A caller's calculation cutoff must never become a recording timestamp."""
import json

import pytest

from qwb import experiments as ex, paper, strategy_workspace as workspace
import pandas as pd
from test_daily_strategy import fixture
from test_strategy_workspace import setup, evaluated


def test_workspace_does_not_backdate_actual_recording_from_now(setup, monkeypatch):
    root, sid, vid = evaluated(setup)
    workspace.start(root, sid, vid, now='2026-10-23T10:00:00+08:00')
    actual = '2026-10-27T18:00:00+08:00'
    monkeypatch.setattr(ex, 'now', lambda: actual)
    workspace.tick(root, sid, vid, now='2026-10-23T18:00:00+08:00')
    row = workspace.inspect(root, sid, vid)['records'][0]
    assert row['recorded_at'] == actual
    assert row['scope'] == 'late_reconstruction'
    assert row['decision_recorded_before_execution'] is False


def clock_account(setup, monkeypatch, kind):
    clock = ['2026-10-23T10:00:00+08:00']
    monkeypatch.setattr(ex, 'now', lambda: clock[0])
    if kind == 'workspace':
        root, sid, vid = evaluated(setup)
        runtime = workspace.start(root, sid, vid, now='2026-10-23T09:00:00+08:00')
        assert runtime['activated_at'] == clock[0]
        account_root = workspace.location(root, sid)/'versions'/vid
        tick = lambda cutoff: workspace.tick(root, sid, vid, now=cutoff)
        rows = lambda: workspace.inspect(root, sid, vid)['records']
    else:
        root, run, _, _ = setup
        paper.initialize(root, 'clock', run, 100000, [], replay_start='2026-10-23')
        account_root = paper.location(root, 'clock')
        # Re-sign only this isolated fixture to exercise forward recording.
        config = ex.read(account_root/'strategy.json')
        config['scope'] = 'forward_paper'
        ex.write(account_root/'strategy.json', config)
        with paper.connection(account_root) as con:
            con.execute("UPDATE metadata SET value=? WHERE key='config_hash'", (ex.object_digest(config),))
        tick = lambda cutoff: paper.tick(root, 'clock', now=cutoff)
        rows = lambda: paper.inspect(root, 'clock')['sessions']
    return clock, tick, rows, account_root


@pytest.mark.parametrize('kind', ['workspace', 'paper'])
@pytest.mark.parametrize('actual,scope,before_execution', [
    ('2026-10-23T18:00:00+08:00', 'forward_paper', True),
    ('2026-10-23T10:00:00+00:00', 'forward_paper', True),
    ('2026-10-24T00:00:00+08:00', 'late_reconstruction', True),
    ('2026-10-26T09:30:00+08:00', 'late_reconstruction', False),
])
def test_recording_uses_actual_clock_and_local_session_date(setup, monkeypatch, kind, actual, scope, before_execution):
    clock, tick, rows, _ = clock_account(setup, monkeypatch, kind)
    clock[0] = actual
    tick('2026-10-23T18:00:00+08:00')
    row = rows()[0]
    assert row['recorded_at'] == actual
    assert row['scope'] == scope
    assert row['decision_recorded_before_execution'] is before_execution


@pytest.mark.parametrize('kind', ['workspace', 'paper'])
def test_future_cutoff_never_records_an_unfinished_session(setup, monkeypatch, kind):
    clock, tick, rows, _ = clock_account(setup, monkeypatch, kind)
    clock[0] = '2026-10-23T15:29:59+08:00'
    tick('2026-10-27T18:00:00+08:00')
    assert rows() == []
    clock[0] = '2026-10-23T18:00:00+08:00'
    tick('2026-10-27T18:00:00+08:00')
    assert [r['day'] for r in rows()] == ['2026-10-23']
    assert rows()[0]['scope'] == 'forward_paper'


@pytest.mark.parametrize('kind', ['workspace', 'paper'])
def test_crossing_midnight_during_calculation_marks_record_late(setup, monkeypatch, kind):
    _, tick, rows, _ = clock_account(setup, monkeypatch, kind)
    times = iter(['2026-10-23T18:00:00+08:00'])
    monkeypatch.setattr(ex, 'now', lambda: next(times, '2026-10-24T00:00:00+08:00'))
    tick('2026-10-23T18:00:00+08:00')
    assert rows()[0]['scope'] == 'late_reconstruction'
    assert pd.Timestamp(rows()[0]['recorded_at']).day == 24


@pytest.mark.parametrize('kind', ['workspace', 'paper'])
def test_timely_flag_cannot_override_late_previous_decision(setup, monkeypatch, kind):
    clock, tick, rows, account_root = clock_account(setup, monkeypatch, kind)
    clock[0] = '2026-10-26T10:00:00+08:00'
    tick('2026-10-23T18:00:00+08:00')
    previous = rows()[0]
    assert previous['decision' if kind == 'workspace' else 'next_weights'] is not None
    assert previous['decision_recorded_before_execution'] is False
    previous['decision_recorded_before_execution'] = True
    with workspace.connection(account_root/'ledger.sqlite3') as con:
        con.execute('UPDATE sessions SET payload=? WHERE day=?', (json.dumps(previous), previous['day']))
    clock[0] = '2026-10-26T18:00:00+08:00'
    tick(clock[0])
    assert rows()[-1]['day'] == '2026-10-26'
    assert rows()[-1]['scope'] == 'late_reconstruction'


def test_legacy_paper_does_not_use_backdated_cutoff_to_claim_timely_record(tmp_path, monkeypatch):
    run, _ = fixture(tmp_path, monkeypatch)
    paper.initialize(tmp_path, 'clock', run, 100000, [], replay_start='2026-10-19')
    root = paper.location(tmp_path, 'clock')
    # Explicitly fabricate a forward-shaped config in this isolated fixture only.
    config = ex.read(root/'strategy.json')
    config['scope'] = 'forward_paper'
    ex.write(root/'strategy.json', config)
    with paper.connection(root) as con:
        con.execute("UPDATE metadata SET value=? WHERE key='config_hash'", (ex.object_digest(config),))
    actual = '2026-10-27T18:00:00+08:00'
    monkeypatch.setattr(ex, 'now', lambda: actual)
    paper.tick(tmp_path, 'clock', now='2026-10-19T16:00:00+08:00')
    row = paper.inspect(tmp_path, 'clock')['sessions'][0]
    assert row['recorded_at'] == actual
    assert row['scope'] == 'late_reconstruction'
    assert row['decision_recorded_before_execution'] is False
