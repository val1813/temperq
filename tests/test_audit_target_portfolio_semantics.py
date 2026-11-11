from qwb import audit_allocation, audit_capital


def bundle(targets):
    return {"target_weights": targets,
            "snapshots": [{"date": "2024-01-02", "equity": 1_000_000, "cash": 100_000}],
            "positions": [{"date": "2024-01-02", "code": "510300.SH", "quantity": 300_000,
                           "price": 3., "market_value": 900_000}],
            "prices": [{"date": "2024-01-02", "code": "510300.SH", "close": 3.}]}


def test_incomplete_exit_instruction_is_not_a_complete_portfolio():
    b = bundle([{"date": "2024-01-02", "code": "510300.SH", "target_weight": 0.,
                 "complete": False, "target_scope": "order_instruction"}])
    assert audit_allocation.analyze(b, {"status": "verified"}) is None
    assert audit_capital.analyze(b, {"status": "verified"}) is None


def test_zero_policy_target_does_not_pass_with_a_large_remaining_position():
    b = bundle([{"date": "2024-01-02", "code": "510300.SH", "target_weight": 0., "complete": True}])
    result = audit_allocation.analyze(b, {"status": "verified"})
    assert result["status"] == "gap"
    assert result["names"][0]["over_days"] == 1


def test_residual_cash_is_not_an_unpriced_security(monkeypatch):
    targets = [{"date": "2024-01-02", "code": "510300.SH", "target_weight": .9, "complete": True},
               {"date": "2024-01-02", "code": "CASH", "target_weight": .1,
                "complete": True, "target_scope": "residual_cash"}]
    b = bundle(targets)
    monkeypatch.setattr(audit_capital, "floor", lambda weights, prices, capital: 0.)
    allocation = audit_allocation.analyze(b, {"status": "verified"})
    assert [r["code"] for r in allocation["names"]] == ["510300.SH"]
    assert allocation["status"] == "close"
    assert audit_capital.analyze(b, {"status": "verified"})["account_mean"] == 0.


def test_zero_target_one_lot_residue_remains_a_granularity_limit():
    b = bundle([{"date": "2024-01-02", "code": "510300.SH", "target_weight": 0., "complete": True}])
    b["positions"][0].update(quantity=100, market_value=300)
    result = audit_allocation.analyze(b, {"status": "verified"})
    assert result["status"] == "close"
    assert result["names"][0]["within_lot_days"] == 1


def test_small_positive_target_one_lot_gap_is_not_a_strategy_error():
    b = bundle([{"date": "2024-01-02", "code": "510300.SH", "target_weight": .0003,
                 "complete": True}])
    b["positions"] = []
    # An absent holding still has an observed close for one-lot granularity.
    result = audit_allocation.analyze(b, {"status": "verified"})
    assert result["status"] == "close"
