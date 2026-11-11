"""Known-answer benchmark: each injected defect is found by its own detector; clean strategies raise nothing."""
from qwb import audit_benchmark, audit_prices, audit_state
from qwb.audit_instrument import compare
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "qwb" / "static"


def test_benchmark_finds_each_fault_and_nothing_in_clean_runs():
    r = audit_benchmark.run(seeds=4, days=260, recorder=True)
    assert r["books_verified"] == r["runs"]
    assert r["clean"]["any_fired"] == 0
    for fault in ("state_on_request", "exit_forgotten", "full_cash", "price_break"):
        s = r["faults"][fault]
        assert s["runs"] and s["detected"] >= s["runs"] - 1, (fault, s)


def test_state_needs_the_recorder():
    files = audit_benchmark.simulate(1, "state_on_request", 200)
    with_rec = audit_benchmark.audit(files)
    files.pop("believed.csv")
    without = audit_benchmark.audit(files)
    assert with_rec["state"]["status"] == "flagged" and "state" not in without


def test_clean_and_faulty_runs_share_market_events():
    a, b = audit_benchmark.simulate(3, "clean"), audit_benchmark.simulate(3, "price_break")
    assert a["trades.csv"] == b["trades.csv"] and a["prices.csv"] != b["prices.csv"]


def test_state_compares_at_decision_time():
    snaps = [{"date": d, "equity": 100., "cash": 100.} for d in ("2024-01-02", "2024-01-03")]
    positions = [{"date": "2024-01-02", "code": "A.SH", "quantity": 100}]
    fills = [{"date": "2024-01-03", "time": "14:51:00", "code": "A.SH", "side": "sell", "quantity": 100}]
    believed = [{"date": "2024-01-03", "time": "14:50:00", "codes": ["A.SH"]}]
    ok = audit_state.analyze({"snapshots": snaps, "positions": positions, "fills": fills, "believed": believed},
                             {"status": "verified", "contributions": []})
    assert ok["mismatch_days"] == 0                           # still held at 14:50, sold at 14:51
    wrong = [{"date": "2024-01-03", "time": "14:50:00", "codes": []}]
    out = audit_state.analyze({"snapshots": snaps, "positions": positions, "fills": fills, "believed": wrong},
                              {"status": "verified", "contributions": []})
    assert out["mismatch_days"] == 1 and out["orphan_days"] == 1


def test_price_jump_allows_compounded_limits_between_sparse_observations():
    rows = [{"date": f"2024-01-{d:02d}", "code": "000001.SZ", "close": 10.} for d in range(2, 9)]
    weekly = rows + [{"date": "2024-01-15", "code": "000001.SZ", "close": 13.}]     # +30% over 5 trading days
    snaps = [{"date": f"2024-01-{d:02d}"} for d in (2, 3, 4, 5, 8, 9, 10, 11, 12, 15)]
    assert audit_prices.analyze({"prices": weekly, "snapshots": snaps})["count"] == 0
    daily = rows + [{"date": "2024-01-09", "code": "000001.SZ", "close": 13.}]       # +30% in one day
    assert audit_prices.analyze({"prices": daily, "snapshots": snaps})["count"] == 1


def test_recorder_with_state_only_adds_recording():
    recorder = (STATIC / "temperq_recorder.py").read_text(encoding="utf-8")
    original = "def handle_bar(context, bar_dict):\n    g.holding = 'A'\n    order_target_percent('A', 1)\n"
    instrumented = recorder + "\ndef handle_bar(context, bar_dict):\n    g.holding = 'A'\n" \
        "    temperq_record(context, {'A': 1.}, ['A'], believed=[g.holding])\n    order_target_percent('A', 1)\n"
    assert compare(original, instrumented)["same_logic"]


def test_allocation_and_intraday_families():
    for sim, faults, expected in ((audit_benchmark.simulate_allocation, audit_benchmark.ALLOC_FAULTS, audit_benchmark.ALLOC_EXPECTED),
                                  (audit_benchmark.simulate_intraday, audit_benchmark.INTRA_FAULTS, audit_benchmark.INTRA_EXPECTED)):
        clean = audit_benchmark.audit(sim(0, "clean"))
        assert clean["accounting"]["status"] == "verified"
        assert not [k for k, t in audit_benchmark.DETECTORS.items() if t(clean)]
        for fault in faults:
            m = audit_benchmark.audit(sim(0, fault))
            assert audit_benchmark.DETECTORS[expected[fault]](m), fault


def test_signal_price_breaks_are_checked():
    rows = [{"date": f"2024-01-{d:02d}", "code": "518800.SH", "score": 0., "signal_price": 10.} for d in range(2, 10)]
    rows.append({"date": "2024-01-10", "code": "518800.SH", "score": 0., "signal_price": 3.8})
    out = audit_prices.analyze({"scores": rows, "snapshots": [{"date": r["date"]} for r in rows]})
    assert out["count"] == 1 and out["jumps"][0]["source"] == "signal"


def test_a_refused_top_up_is_not_a_forgotten_one():
    from qwb import audit_completion
    days = ["2024-02-01", "2024-02-02", "2024-02-05"]
    snaps = [{"date": d, "equity": 100., "cash": 50.} for d in days]
    positions = [{"date": d, "code": "A.SH", "quantity": 100} for d in days]
    scores = [{"date": d, "code": "A.SH", "score": 1., "selected": True} for d in days]
    orders = [{"date": days[0], "code": "A.SH", "side": "buy", "quantity": 200, "filled": 100, "reason": ""},
              {"date": days[1], "code": "A.SH", "side": "buy", "quantity": 100, "filled": 0, "reason": "无成交量"}]
    out = audit_completion.analyze({"orders": orders, "snapshots": snaps, "positions": positions, "scores": scores,
                                    "fills": [{"date": days[0], "code": "A.SH"}]}, {"status": "verified"})
    assert out["episodes"][0]["still_target_days"] == 1          # day 3 only; day 2 had a refused retry


def _snaps(days):
    return [{"date": d, "equity": 100., "cash": 100.} for d in days]


def test_buy_submitted_before_sell_is_flagged_only_when_it_cost_something():
    from qwb import audit_decisions
    days = [f"2024-03-{d:02d}" for d in range(1, 8)]
    orders = []
    for i, d in enumerate(days):
        orders += [{"date": d, "time": "14:50:00", "order_id": str(2 * i), "code": "B.SH", "side": "buy", "quantity": 200, "filled": 100},
                   {"date": d, "time": "14:50:00", "order_id": str(2 * i + 1), "code": "A.SH", "side": "sell", "quantity": 100, "filled": 100}]
    out = audit_decisions.buy_before_sell({"orders": orders, "snapshots": _snaps(days)})
    assert out["status"] == "flagged" and out["costly_days"] == 7
    full = [{**o, "filled": o["quantity"]} for o in orders]
    snaps = [{**s, "cash": 1.} for s in _snaps(days)]
    assert audit_decisions.buy_before_sell({"orders": full, "snapshots": snaps})["status"] == "normal"


def test_selected_but_never_ordered():
    from qwb import audit_decisions
    days = [f"2024-04-{d:02d}" for d in range(1, 8)]
    scores = [{"date": d, "code": "A.SH", "score": 1., "selected": True} for d in days]
    out = audit_decisions.selected_not_ordered({"scores": scores, "orders": [], "fills": [], "positions": [],
                                                "snapshots": _snaps(days)})
    assert out["status"] == "flagged" and out["days"] == 6        # the first day has no prior close


def test_intraday_day_without_any_exit_attempt():
    from qwb import audit_decisions
    days = [f"2024-05-{d:02d}" for d in range(1, 31)]
    fills = [{"date": d, "time": "09:31:00", "code": "A.SH", "side": "buy"} for d in days] + \
            [{"date": d, "time": "14:55:00", "code": "A.SH", "side": "sell"} for d in days[:25]]
    positions = [{"date": d, "code": "A.SH", "quantity": 100} for d in days[25:]]
    orders = [{"date": d, "code": "A.SH", "side": "sell"} for d in days[:25]]
    out = audit_decisions.no_exit_attempt({"fills": fills, "positions": positions, "orders": orders})
    assert out["status"] == "flagged" and out["days"] == 5


def test_lookahead_hint_needs_power_concentrated_in_the_next_bar():
    import random
    from qwb import audit_decisions
    rng = random.Random(0)
    days = [f"2024-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(120)]
    codes = [f"{i:06d}.SZ" for i in range(6)]
    px = {c: [10.] for c in codes}
    for _ in days[1:]:
        for c in codes:
            px[c].append(px[c][-1] * (1 + rng.gauss(0, .01)))
    prices = [{"date": d, "code": c, "close": px[c][i]} for i, d in enumerate(days) for c in codes]
    peek = [{"date": d, "code": c, "score": px[c][i + 1] / px[c][i] + rng.gauss(0, .01)}
            for i, d in enumerate(days[:-1]) for c in codes]
    fair = [{"date": d, "code": c, "score": rng.random()} for d in days for c in codes]
    assert audit_decisions.lookahead_hint({"scores": peek, "prices": prices})["status"] == "flagged"
    assert audit_decisions.lookahead_hint({"scores": fair, "prices": prices})["status"] == "normal"


def test_repeated_adds_to_a_fully_filled_target():
    from qwb import audit_decisions
    days = [f"2024-06-{d:02d}" for d in range(3, 13)]
    scores = [{"date": d, "code": "A.SH", "score": 1., "selected": True} for d in days]
    positions = [{"date": d, "code": "A.SH", "quantity": 100 * (i + 1)} for i, d in enumerate(days)]
    fills = [{"date": d, "code": "A.SH", "side": "buy", "quantity": 100} for d in days]
    orders = [{"date": d, "code": "A.SH", "side": "buy", "quantity": 100, "filled": 100} for d in days]
    out = audit_decisions.repeated_adds({"scores": scores, "positions": positions, "fills": fills, "orders": orders,
                                         "snapshots": _snaps(days)})
    assert out["status"] == "flagged" and out["days"] == 9
    sold = fills + [{"date": d, "code": "A.SH", "side": "sell", "quantity": 100} for d in days]   # intraday re-entry
    out = audit_decisions.repeated_adds({"scores": scores, "positions": positions, "fills": sold, "orders": orders,
                                         "snapshots": _snaps(days)})
    assert out is None or out["status"] == "normal"


def test_flat_at_every_close_is_a_full_account():
    from qwb import audit_input
    b = {"snapshots": [{"date": "2024-01-02", "equity": 100., "cash": 100., "market_value": 0.}], "positions": [],
         "quality": {}}
    assert audit_input.full_account_available(b)
