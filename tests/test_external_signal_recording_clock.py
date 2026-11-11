"""External score receipt times come from the local clock, not a replay cutoff."""
import pandas as pd
import pytest

from qwb import experiments as ex, external_signals as signals
from test_external_signals import raw
from test_strategy_workspace import setup


def test_backdated_cutoff_does_not_backdate_first_receipt(setup, monkeypatch):
    root, _, _, panel = setup
    monkeypatch.setattr(ex, 'now', lambda: '2026-10-27T18:00:00+08:00')
    contract = signals.contract('clock', 'v1', list(panel['close'].columns))
    source = signals.create(root, contract, raw(panel, '2026-10-23', '2026-10-23'),
                            now='2026-10-23T18:00:00+08:00')
    meta = ex.read(source/'final_model/meta.json')
    receipts = signals.current(meta['signal_feed'])[2]
    assert pd.Timestamp(receipts['2026-10-23']) == pd.Timestamp('2026-10-27T18:00:00+08:00')
    with pytest.raises(ValueError, match='尚未收到'):
        signals.live_scores(meta, panel, pd.DatetimeIndex(['2026-10-23']), now='2026-10-23T18:00:00+08:00')


def test_future_cutoff_does_not_admit_future_predictions(setup, monkeypatch):
    root, _, _, panel = setup
    monkeypatch.setattr(ex, 'now', lambda: '2026-10-23T14:00:00+08:00')
    contract = signals.contract('clock', 'v1', list(panel['close'].columns))
    with pytest.raises(ValueError, match='未来决策'):
        signals.create(root, contract, raw(panel, '2026-10-26', '2026-10-26'),
                       now='2026-10-27T18:00:00+08:00')


def test_appending_retains_actual_first_receipts(setup, monkeypatch):
    root, _, _, panel = setup
    clock = ['2026-10-23T18:00:00+08:00']
    monkeypatch.setattr(ex, 'now', lambda: clock[0])
    contract = signals.contract('clock', 'v1', list(panel['close'].columns))
    source = signals.create(root, contract, raw(panel, '2026-10-23', '2026-10-23'), now=clock[0])
    meta = ex.read(source/'final_model/meta.json')
    feed = meta['signal_feed']
    first = signals.current(feed)[2]['2026-10-23']
    clock[0] = '2026-10-27T18:00:00+08:00'
    signals.append(feed, raw(panel, '2026-10-26', '2026-10-26'), now='2026-10-26T18:00:00+08:00')
    receipts = signals.current(feed)[2]
    assert receipts['2026-10-23'] == first
    assert pd.Timestamp(receipts['2026-10-26']) == pd.Timestamp(clock[0])
