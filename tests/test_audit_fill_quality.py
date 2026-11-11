"""Were the fill prices obtainable? Position in the day's range and the worst price soon after."""
import random

from qwb import audit_fill_quality


def bundle(lucky, n=60, seed=1, after=0.):
    """Fills on n days. lucky=True buys at the day's low and sells at its high; False places them at random."""
    rng = random.Random(seed)
    fills, ranges, minutes = [], [], []
    for i in range(n):
        day = f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}"
        low, high = 10., 11.
        side = "buy" if i % 2 == 0 else "sell"
        pos = (0. if side == "buy" else 1.) if lucky else rng.random()
        price = low + pos * (high - low)
        fills.append({"date": day, "time": "09:31:00", "code": "A.SH", "side": side, "quantity": 100, "price": price})
        ranges.append({"date": day, "code": "A.SH", "high": high, "low": low})
        for k in range(11):
            m = f"09:{31 + k:02d}"
            minutes.append({"date": day, "time": m, "code": "A.SH", "close": price,
                            "high": price + after, "low": price - after})
    return {"fills": fills, "day_ranges": ranges, "minute_prices": minutes}


def test_buying_lows_and_selling_highs_is_flagged():
    out = audit_fill_quality.analyze(bundle(lucky=True), {"status": "verified", "daily": [{"net_pnl": 100.}]})
    assert out["range"]["status"] == "suspicious" and out["status"] == "suspicious"
    assert out["range"]["best_share"] == 1. and "疑似" in out["range"]["verdict"]


def test_random_fills_are_normal():
    out = audit_fill_quality.analyze(bundle(lucky=False), {"status": "verified", "daily": [{"net_pnl": 100.}]})
    assert out["range"]["status"] == "normal" and "接近随机" in out["range"]["verdict"]


def test_worst_price_floor_can_erase_the_profit():
    data = bundle(lucky=False, after=.05)                    # 60 fills x 100 shares x 0.05 = 300 yuan worse
    out = audit_fill_quality.analyze(data, {"status": "verified", "daily": [{"net_pnl": 200.}]})
    assert abs(out["floor"]["change"] + 300.) < 1e-6 and out["floor"]["net_after"] < 0
    assert out["status"] == "suspicious" and "压力情景" in out["floor"]["verdict"]
    kept = audit_fill_quality.analyze(data, {"status": "verified", "daily": [{"net_pnl": 2000.}]})
    assert kept["floor"]["net_after"] > 0 and "仍为" in kept["floor"]["verdict"]


def test_lunch_break_is_skipped_and_missing_inputs_are_named():
    assert audit_fill_quality._minutes_after("11:28", 4) == ["11:28", "11:29", "11:30", "13:01", "13:02"]
    assert audit_fill_quality._minutes_after("14:58", 10, own=False) == ["14:59", "15:00"]
    out = audit_fill_quality.analyze({"fills": bundle(False)["fills"]}, {"status": "verified", "daily": []})
    assert out["status"] == "needs_prices" and out["missing"]


def test_supplement_writes_the_fill_window():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "src" / "qwb" / "static" / "temperq_supplement.py"
    spec = importlib.util.spec_from_file_location("supplement", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    clocks = module.fill_clocks([{"date": "2024-01-02", "time": "09:31:00", "code": "510300.SH"}])
    assert clocks[("2024-01-02", "510300.XSHG")] >= {"09:31", "09:41"}


def test_sparse_window_does_not_masquerade_as_full_coverage():
    data = bundle(False, n=1, after=.05)
    data['minute_prices'] = data['minute_prices'][:2]
    out = audit_fill_quality.analyze(data, {'status': 'verified', 'daily': [{'net_pnl': 10.}]})
    assert out['floor']['change'] is None and out['floor']['coverage'] == 0
    assert out['floor']['observations'][0]['observed_minutes'] == 1
    assert out['floor']['exclusions']['incomplete_or_invalid_window'] == 1


def test_price_stress_excludes_baseline_and_is_signed_not_realized_loss():
    data = bundle(False, n=2, after=.05)
    day = data['fills'][1]['date']
    out = audit_fill_quality.analyze(data, {'status': 'verified', 'daily': [{'date': day, 'net_pnl': 1.}]})
    floor = out['floor']
    assert abs(floor['change'] + 5) < 1e-6
    assert floor['exclusions']['outside_accounted_intervals'] == 1
    assert floor['net_after'] < 0 and floor['coverage'] == 1
    assert '不代表实盘必然损失' in floor['verdict']
    row = floor['rows'][0]
    assert abs(row['net_pnl'] - (row['stress_price'] - row['original_price']) * row['quantity']) < 1e-9


def test_duplicate_bars_and_zero_net_profit_do_not_produce_false_amount_or_divide_zero():
    data = bundle(False, n=1, after=.05)
    out = audit_fill_quality.analyze(data, {'status': 'verified', 'daily': [{'net_pnl': 0.}]})
    assert out['floor']['change_to_abs_net_pnl'] is None
    data['minute_prices'].append(dict(data['minute_prices'][1]))
    out = audit_fill_quality.analyze(data, {'status': 'verified', 'daily': [{'net_pnl': 0.}]})
    assert out['floor']['change'] is None and out['floor']['exclusions']['duplicate_minute_rows'] == 1
