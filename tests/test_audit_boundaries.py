"""New checks must stay silent without their inputs, and must not call a correct strategy wrong."""
import math
import random

from qwb import audit_capacity, audit_limits, audit_listings, audit_market, audit_spec

OK = {"status": "verified", "contributions": []}


def _snaps(n, start=100_000., step=lambda i: 0.):
    eq, out = start, []
    for i in range(n):
        eq *= 1 + step(i)
        out.append({"date": f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}", "equity": eq, "cash": eq, "market_value": 0.})
    return out


def test_every_new_check_is_silent_without_its_inputs():
    empty = {"snapshots": _snaps(200), "positions": [], "fills": [], "orders": [], "scores": []}
    assert audit_limits.analyze(empty, OK) is None
    assert audit_listings.analyze(empty, OK) is None
    assert audit_spec.analyze(empty, OK) is None
    assert audit_capacity.analyze(empty, OK) is None
    assert (audit_market.analyze(empty, OK) or {}).get("exposure") is None     # no benchmark, no beta


def test_market_led_only_when_beta_explains_most_of_the_return():
    rng = random.Random(1)
    bench = [rng.gauss(.001, .01) for _ in range(300)]
    snaps = _snaps(300, step=lambda i: bench[i])                       # the strategy is the benchmark
    nav, plat = 1., []
    for s, r in zip(snaps, bench):
        nav *= 1 + r
        plat.append({"date": s["date"], "benchmark_pct": (nav - 1) * 100})
    out = audit_market.exposure({"snapshots": snaps, "platform_returns": plat})
    assert out["status"] == "market" and abs(out["beta"] - 1) < .05
    own = [rng.gauss(.001, .01) for _ in range(300)]                     # unrelated to the benchmark
    out = audit_market.exposure({"snapshots": _snaps(300, step=lambda i: own[i]), "platform_returns": plat})
    assert out["status"] == "normal"


def test_market_zero_residual_variance_does_not_format_missing_t_as_a_number():
    snaps = _snaps(100)
    platform = [{'date': s['date'], 'benchmark_pct': i % 3} for i, s in enumerate(snaps)]
    result = audit_market.exposure({'snapshots': snaps, 'platform_returns': platform})
    assert result['alpha_t'] is None and result['status'] == 'normal'
    assert '不可计算' in result['verdict'] and '不能据此判断' in result['verdict']


def test_market_missing_benchmark_date_does_not_regress_mixed_horizons():
    snaps = _snaps(100, step=lambda i: .001 * (i % 3))
    platform = [{'date': s['date'], 'benchmark_pct': i % 3} for i, s in enumerate(snaps)]
    platform.pop(40)
    assert audit_market.exposure({'snapshots': snaps, 'platform_returns': platform}) is None


def test_limit_lock_needs_a_one_price_day():
    fills = [{"date": "2024-01-02", "code": "A.SZ", "side": "buy", "price": 11., "quantity": 100}]
    limits = [{"date": "2024-01-02", "code": "A.SZ", "limit_up": 11., "limit_down": 9.}]
    locked = audit_limits.analyze({"fills": fills, "limits": limits,
                                   "day_ranges": [{"date": "2024-01-02", "code": "A.SZ", "high": 11., "low": 11.}]}, OK)
    assert locked["locked"] == 1
    opened = audit_limits.analyze({"fills": fills, "limits": limits,
                                   "day_ranges": [{"date": "2024-01-02", "code": "A.SZ", "high": 11., "low": 10.6}]}, OK)
    assert opened["locked"] == 0 and opened["status"] == "normal"            # touched the limit, then traded lower


def test_listing_before_existence_and_the_survivorship_question():
    scores = [{"date": "2024-01-02", "code": f"{i:06d}.SZ", "score": 1.} for i in range(40)]
    listings = [{"code": f"{i:06d}.SZ", "listed_date": "2020-01-01", "delisted_date": None} for i in range(40)]
    out = audit_listings.analyze({"scores": scores, "fills": [], "listings": listings,
                                  "snapshots": [{"date": "2024-01-02"}, {"date": "2024-12-31"}]}, OK)
    assert out["status"] == "normal" and out["survivorship_question"]       # a question, not an error
    listings[0] = {**listings[0], "listed_date": "2024-06-01"}
    out = audit_listings.analyze({"scores": scores, "fills": [], "listings": listings,
                                  "snapshots": [{"date": "2024-01-02"}, {"date": "2024-12-31"}]}, OK)
    assert out["status"] == "flagged" and out["before_listing"][0]["code"] == "000000.SZ"


def test_spec_checks_only_what_was_declared_and_excuses_market_failures():
    days = [f"2024-03-{d:02d}" for d in range(1, 11)]
    snaps = [{"date": d, "equity": 100_000., "cash": 0., "market_value": 100_000.} for d in days]
    positions = [{"date": d, "code": "A.SH", "quantity": 1000, "price": 100., "market_value": 100_000.} for d in days]
    orders = [{"date": d, "code": "A.SH", "side": "sell", "quantity": 1000, "filled": 0} for d in days]
    out = audit_spec.analyze({"snapshots": snaps, "positions": positions, "orders": orders, "fills": [],
                              "spec": {"flat_overnight": "true", "colour": "blue"}}, OK)
    assert out["flat_overnight"]["status"] == "kept"                       # every night had a refused sell
    assert out["max_weight"] is None and out["unknown"] == ["colour"]
    out = audit_spec.analyze({"snapshots": snaps, "positions": positions, "orders": [], "fills": [],
                              "spec": {"flat_overnight": "true"}}, OK)
    assert out["flat_overnight"]["status"] == "breach"


def test_deflated_sharpe_drops_with_more_trials():
    rng = random.Random(3)
    r = [rng.gauss(.0008, .01) for _ in range(500)]
    snaps = _snaps(501, step=lambda i: r[i - 1] if i else 0.)
    one = audit_spec.check_trials({"snapshots": snaps}, {"trials": "1"})
    many = audit_spec.check_trials({"snapshots": snaps}, {"trials": "200"})
    assert one["dsr"] > many["dsr"] and many["threshold_annual"] > one["threshold_annual"]


def test_capacity_is_never_an_error_below_the_ceiling():
    minutes = [{"date": "2024-01-02", "time": f"10:{m:02d}", "code": "A.SH", "volume": 100_000} for m in range(60)]
    fills = [{"date": "2024-01-02", "time": f"10:{m:02d}:00", "code": "A.SH", "quantity": 1000, "side": "buy"} for m in range(5, 20)]
    out = audit_capacity.analyze({"minute_prices": minutes, "fills": fills}, OK)
    # All five adjacent fills consume this window, including other minutes.
    assert out["status"] == "normal" and math.isclose(out["p95"], .01, rel_tol=.01)
