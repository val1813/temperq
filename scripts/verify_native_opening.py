"""Opt-in behavior checks in the pinned professional engine, synthetic quotes.

Run with the existing engine interpreter. No broker, real account, external
strategy execution, original-environment edits or future-date observations.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from hashlib import sha256
import importlib.util
import io
import json
from pathlib import Path
import sys

from native_opening_state import FIELDS, attach, patched_source, read_opening_state


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--engine-root', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(args.engine_root.resolve()))
    from importlib.metadata import version
    if version('nautilus-trader') != '1.231.0':
        raise ValueError('Requires the verified NautilusTrader 1.231.0')
    import pandas as pd
    from quantlocal.engine import run_engine as original_engine
    from quantlocal.data import indicators

    source_path = args.engine_root / 'quantlocal/engine.py'
    source_raw = source_path.read_bytes()
    source_sha = sha256(source_raw).hexdigest()
    patched = patched_source(source_raw)
    generated = args.output / 'opening_state_engine.py'
    generated.write_bytes(patched)
    spec = importlib.util.spec_from_file_location('temperq_verified_opening_engine', generated)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    dates = ['2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07', '2025-01-08', '2025-01-09', '2025-01-10', '2025-01-13']
    # Frozen known orders: buy, sell, no ask supply, not tradable, limit-up,
    # buy, limit-down prevents sell, then exit. No signal research in this test.
    signals = [True, False, True, True, True, True, False, False]
    frame = pd.DataFrame([dict(date=d, symbol='TEST', open=10., close=10., volume=100000,
                              up_limit=11., down_limit=9., signal=signals[i],
                              signal_date=(date.fromisoformat(d)-timedelta(days=1)).isoformat()) for i, d in enumerate(dates)])
    rows = [dict(date=d, symbol='TEST', observed_at=d+'T09:29:59+08:00', available_at=d+'T09:30:00+08:00',
                 bid='9.99', ask='10.00', bid_size='500', ask_size='300', tradable='true',
                 up_limit='11.00', down_limit='9.00', source_ref=f'synthetic-quote-{i}') for i, d in enumerate(dates)]
    rows[2]['ask_size'] = '0'
    rows[3]['tradable'] = 'false'
    rows[4]['ask'] = rows[4]['up_limit'] = '11.00'
    rows[5]['ask_size'] = '200'
    rows[6]['bid'] = rows[6]['down_limit'] = '9.00'
    config = dict(market='CN', capital=100000, fee_bps=10, sell_tax_bps=5, minimum_fee=5)
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    raw = stream.getvalue().encode()
    (args.output / 'opening_state.csv').write_bytes(raw)
    frame.to_csv(args.output / 'daily_fixture.csv', index=False)
    expected = set(zip(frame.date, frame.symbol))
    state = read_opening_state(raw, expected)
    prepared = attach(frame, state)
    stages = []

    def verified(name, condition):
        stages.append(dict(stage=name, passed=bool(condition)))
        if not condition:
            raise AssertionError(name)
        print('PASS '+name, flush=True)

    try:
        baseline = module.run_engine(config, prepared, args.output / 'native')
        fills = baseline['fills']
        verified('native_cash_positions_equity', baseline['reconciliation']['passed'])
        verified('displayed_quote_size_caps_buy', fills[0]['qty'] == 300 and fills[0]['price'] == 10.)
        verified('sell_uses_bid', fills[1]['qty'] == 300 and fills[1]['price'] == 9.99)
        verified('known_order_dates_and_inventory', [(f['date'], f['side'], f['qty']) for f in fills] == [
            (dates[0], 'BUY', 300), (dates[1], 'SELL', 300), (dates[5], 'BUY', 200), (dates[7], 'SELL', 200)])
        verified('zero_supply_nontradable_and_limits_block', [d['reason'] for d in baseline['decisions'] if not d['allowed']] == [
            'no_volume', 'no_volume', 'limit_up', 'limit_down'])
        # Four minimum commissions (5 each), plus sell stamp tax 1.50 + 1.00.
        expected_cash = 100000 + 500 * (9.99 - 10.) - 22.50
        verified('known_answer_cash_and_flat_position', abs(baseline['snapshots'][-1]['cash'] - expected_cash) < .011 and baseline['snapshots'][-1]['TEST'] == 0)
        # Same frozen strategy: a partial exit must remain inventory and be
        # retried next signal-false day, not be marked fully sold on request.
        partial_exit = prepared.iloc[:3].copy()
        partial_exit['signal'] = [True, False, False]
        partial_exit.loc[partial_exit.index[1], 'opening_bid_size'] = 100
        partial_exit.loc[partial_exit.index[2], 'opening_bid_size'] = 500
        exited = module.run_engine(config, partial_exit)
        verified('partial_exit_keeps_residual_and_retries', [f['qty'] for f in exited['fills']] == [300, 100, 200]
                 and [s['TEST'] for s in exited['snapshots']] == [300, 200, 0])
        changed_volume = prepared.copy()
        changed_volume['volume'] = 0
        r = module.run_engine(config, changed_volume)
        verified('daily_final_volume_cannot_change_orders_or_fills', r == baseline)
        old_baseline = original_engine(config, frame)
        old_zero = original_engine(config, frame.assign(volume=0))
        verified('original_adapter_counterexample_reproduced', old_baseline['fills'] != old_zero['fills'])
        changed_close = prepared.copy()
        target = dates[5]
        changed_close.loc[changed_close.date >= target, 'close'] *= .7
        r = module.run_engine(config, changed_close)
        verified('future_closing_marks_cannot_change_orders_or_fills', r['fills'] == baseline['fills'] and r['decisions'] == baseline['decisions'])
        verified('prior_daily_equity_unchanged_by_future_marks', [s for s in r['snapshots'] if s['date'] < target] == [s for s in baseline['snapshots'] if s['date'] < target])
        signal_frame = frame.copy()
        signal_frame['close'] = [10., 10.1, 10.2, 10.3, 10.4, 10.3, 10.2, 10.1]
        signal_base = module.run_engine(config, attach(indicators(signal_frame, 2), state))
        signal_frame.loc[signal_frame.date >= target, 'close'] *= .7
        signal_changed = module.run_engine(config, attach(indicators(signal_frame, 2), state))
        before = lambda result, key: [r for r in result[key] if r['date'] <= target]
        verified('recomputed_signals_and_opening_fills_ignore_current_future_closes',
                 before(signal_base, 'fills') == before(signal_changed, 'fills') and
                 before(signal_base, 'decisions') == before(signal_changed, 'decisions') and
                 bool(before(signal_base, 'fills')))
        r = module.run_engine(config, prepared, cut='2025-01-09')
        verified('streamed_equals_one_pass', r == baseline)
        for name, bad in [('publication_after_open', raw.replace(b'T09:30:00+08:00', b'T20:00:00+08:00')),
                          ('missing_quote', raw.splitlines()[0]+b'\n')]:
            try:
                read_opening_state(bad, expected)
            except ValueError:
                verified(name+'_rejected', True)
            else:
                verified(name+'_rejected', False)
        verified('external_adapter_unchanged', sha256(source_path.read_bytes()).hexdigest() == source_sha)
        final = dict(status='passed', stages=stages, scope='synthetic_quote_native_engine_behavior_not_real_quote_or_broker_validation',
                     engine_version=version('nautilus-trader'), external_engine_sha256=source_sha,
                     generated_engine_sha256=sha256(patched).hexdigest(), state_sha256=sha256(raw).hexdigest(),
                     verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
                     opening_helper_sha256=sha256((Path(__file__).parent / 'native_opening_state.py').read_bytes()).hexdigest(),
                     config=config, native=baseline, real_quote_validation=False, real_broker_validation=False,
                     limitations=['No historical opening quotes with verified publication times were supplied.',
                                  'Finite displayed sizes and immediate IOC simulation do not calibrate queue, latency, replenishment or impact.',
                                  'The installed strategy is retained; partial entry positions are not automatically topped up.'])
    except Exception as exc:
        final = dict(status='failed', stages=stages, error=str(exc))
        raise
    finally:
        (args.output / 'verification.json').write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
