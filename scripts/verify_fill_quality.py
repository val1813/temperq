"""Independently recompute old sparse-window and new complete-window price stress.

Explicit --case NAME OLD_AUDIT NEW_AUDIT; source evidence is read only.
Uses Decimal arithmetic and a trading-minute list, not the engine's functions.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recompute(bundle, account):
    trading = [f'{m // 60:02d}:{m % 60:02d}' for lo, hi in ((571, 690), (781, 900))
               for m in range(lo, hi + 1)]
    bars = defaultdict(list)
    for row in bundle.get('minute_prices') or []:
        bars[row['date'], row['code'], row['time']].append(row)
    dates = {r['date'] for r in account['daily']}
    totals = Counter()
    exclusions = Counter()
    rows = []
    for fill in bundle.get('fills') or []:
        if not fill.get('quantity') or not fill.get('price'):
            continue
        text = str(fill.get('time') or '').strip()
        clock = text[11:16] if len(text) >= 16 and text[10] in ' T' else text[:5]
        # Opening 09:30 is allowed; the next completed bar is 09:31.
        window = [m for m in trading if m > clock][:10] if clock and ':' in clock else []
        window_bars = [bars[fill['date'], fill['code'], m] for m in window]
        side = 'high' if fill['side'] == 'buy' else 'low'
        old_bars = [b[-1] for b in window_bars if b and b[-1].get(side)]
        qty, price = Decimal(str(fill['quantity'])), Decimal(str(fill['price']))

        def delta(selected):
            extreme = (max if side == 'high' else min)(Decimal(str(b[side])) for b in selected)
            return (price - extreme if side == 'high' else extreme - price) * qty

        old_delta = delta(old_bars) if len(window) == 10 and old_bars else Decimal(0)
        totals['legacy_change'] += old_delta
        totals['legacy_change:' + fill['side']] += old_delta
        if fill['date'] not in dates:
            reason = 'outside_accounted_intervals'
        elif not clock or ':' not in clock:
            reason = 'missing_fill_clock'
        elif len(window) < 10:
            reason = 'window_past_close'
        elif any(len(b) > 1 for b in window_bars):
            reason = 'duplicate_minute_rows'
        elif not all(b and all(isinstance(b[0].get(k), (float, int))
                              and Decimal(str(b[0][k])).is_finite() and b[0][k] > 0
                              for k in ('high', 'low')) and b[0]['high'] >= b[0]['low']
                     for b in window_bars):
            reason = 'incomplete_or_invalid_window'
        else:
            reason = ''
        if fill['date'] in dates:
            totals['eligible_notional'] += qty * price
            totals['eligible_fills'] += 1
        if reason:
            exclusions[reason] += 1
            totals['excluded_legacy_change:' + reason] += old_delta
        else:
            change = delta([b[0] for b in window_bars])
            totals['complete_change'] += change
            totals['complete_change:' + fill['side']] += change
            totals['covered_notional'] += qty * price
            totals['covered_fills'] += 1
        rows.append({'date': fill['date'], 'code': fill['code'], 'time': clock,
                     'side': fill['side'], 'legacy_change': float(old_delta), 'exclusion': reason})
    return dict(totals), dict(exclusions), rows


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--case', nargs=3, action='append', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    records = []
    for name, old_dir, new_dir in args.case:
        paths = [Path(p) / 'evidence.json' for p in (old_dir, new_dir)]
        before = [digest(p) for p in paths]
        old, new = [json.loads(p.read_text(encoding='utf-8')) for p in paths]
        account = new['result']['modules']['accounting']
        totals, excluded, rows = recompute(new['input'], account)
        old_floor = old['result']['modules']['fill_quality'].get('floor') or {}
        new_floor = new['result']['modules']['fill_quality']['floor']
        assert abs(float(totals.get('legacy_change', 0)) - old_floor.get('change', 0)) < .01, name
        assert abs(float(totals.get('complete_change', 0)) - (new_floor['change'] or 0)) < .01, name
        assert totals.get('covered_fills', 0) == new_floor['fills'], name
        assert excluded == new_floor['exclusions'], (name, excluded, new_floor['exclusions'])
        for key in ('covered_notional', 'eligible_notional'):
            assert abs(float(totals.get(key, 0)) - new_floor[key]) < .01, (name, key)
        assert [digest(p) for p in paths] == before
        records.append({'case': name, 'status': 'passed',
                        'input_evidence_sha256': before, 'sources_unchanged': True,
                        'independent_totals': {k: float(v) for k, v in totals.items()},
                        'net_pnl': sum(d['net_pnl'] for d in account['daily']),
                        'new_coverage': new_floor['coverage'], 'exclusions': excluded,
                        'legacy_fill_details': rows,
                        'meaning': 'negative: less P&L under independent per-fill extremes; not real loss'})
        print(name, {k: float(v) for k, v in totals.items()}, flush=True)
    with args.output.open('x', encoding='utf-8') as handle:
        json.dump({'status': 'passed', 'cases': records}, handle, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    main()
