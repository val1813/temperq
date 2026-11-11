"""G0: known-bad evidence must not produce "better", a fee explanation, or a supported conclusion."""
import copy

import pytest

from qwb import audit_factorial, audit_verification as av, strategy_audit, strategy_audit_ui, strategy_imports

SETTINGS = {"commission_bp": 3., "minimum_commission": 5., "max_weight": 1.}


@pytest.fixture(scope="module")
def base():
    return strategy_imports.normalize(strategy_audit_ui.sample_files(), name="base", opening_cash=10000)


def remark(bundle, value=0., price=0.):
    """Move the last closing valuation: by `value` alone (books no longer reconcile) or by a new price (they do)."""
    b = copy.deepcopy(bundle)
    day = b["snapshots"][-1]["date"]
    p = next(x for x in b["positions"] if x["date"] == day)
    if price:
        value = p["quantity"] * price
        p["price"] += price
    p["market_value"] += value
    b["snapshots"][-1]["market_value"] += value
    b["snapshots"][-1]["equity"] += value
    b["code"]["source"] += "\n# fixture\n"
    return b


def minimal(capital, pnl, status="verified"):
    return {"mode": "full", "modules": {"accounting": {"status": status, "baseline_equity": capital,
                                                       "daily": [{"date": "2024-01-02", "net_pnl": pnl}]},
                                        "fee_analysis": {"fills": []}, "tick_analysis": {"fills": []}}}


def test_a_book_that_does_not_reconcile_blocks_the_comparison(base):
    clean = strategy_audit.analyze(base, SETTINGS)
    bad = strategy_audit.analyze(remark(base, value=1000.), SETTINGS)
    assert bad["modules"]["accounting"]["status"] == "needs_reconciliation"
    out = av.compare(clean, bad)
    assert not out["comparable"] and out["state"] == "invalid" and "账目未闭合" in out["reason"]
    assert av.restate(bad) is None


def test_a_missing_status_is_not_treated_as_verified():
    ok, reasons = av.eligibility(minimal(1e6, 1., status=None))
    assert not ok and any("账目未闭合" in r for r in reasons)


@pytest.mark.parametrize("daily, word", [
    ([{"date": "2024-01-03", "net_pnl": 1.}, {"date": "2024-01-02", "net_pnl": 1.}], "排序"),
    ([{"date": "2024-01-02", "net_pnl": 1.}, {"date": "2024-01-02", "net_pnl": 1.}], "重复"),
    ([{"date": "2024-01-02", "net_pnl": float("nan")}], "非有限"),
])
def test_malformed_daily_books_are_not_eligible(daily, word):
    run = minimal(1e6, 0.)
    run["modules"]["accounting"]["daily"] = daily
    ok, reasons = av.eligibility(run)
    assert not ok and any(word in r for r in reasons)


def test_a_larger_account_earning_more_yuan_is_not_a_better_strategy():
    small, large = minimal(1e6, 10000.), minimal(2e6, 15000.)
    out = av.compare(small, large)
    assert not out["comparable"] and out["state"] == "scale_sensitivity"
    four = audit_factorial.analyze(small, small, small, large)
    assert not four["comparable"] and four["state"] == "scale_sensitivity"


def test_same_fills_with_different_marks_are_not_explained_by_fees(base):
    clean = strategy_audit.analyze(base, SETTINGS)
    marked = strategy_audit.analyze(remark(base, price=1.), SETTINGS)
    assert marked["modules"]["accounting"]["status"] == "verified"
    out = av.compare(clean, marked)
    assert out["comparable"] and out["route_similarity"] == "identical"
    assert not out["fee_explained"] and "费率敏感性" not in out["verdict"] and "不能只归到费用" in out["verdict"]


def test_identical_fills_and_marks_with_only_fees_changed_are_explained_by_fees(base):
    clean = strategy_audit.analyze(base, SETTINGS)
    again = strategy_audit.analyze(base, {**SETTINGS, "commission_bp": 1.})
    out = av.compare(clean, again)
    assert out["fee_explained"] and "费率敏感性" in out["verdict"]


def test_serially_dependent_ic_is_not_called_significant_by_the_iid_t():
    from qwb import audit_premise as ap
    ics = ([1.] * 30 + [-1.] * 20) * 5          # mean 0.2, long runs of the same sign
    s = ap._summary(ics)
    assert s["iid_t"] == pytest.approx(3.221, abs=1e-3)
    assert s["hac_lag"] == 4 and s["t"] < 2 and ap._judge(s, 2.) == "unclear"
    assert set(s["hac_sensitivity"]) == {"2", "4", "8"}


def test_hac_t_is_withheld_below_the_minimum_periods():
    from qwb import audit_premise as ap
    s = ap._summary([.1, .2, -.1] * 8)          # 24 periods < max(30, 4(L+1))
    assert s["t"] is None and s["iid_t"] is not None and ap._judge(s, 2.) == "too_few"


def test_a_label_ending_after_the_segment_is_not_counted_in_it():
    from qwb import audit_premise as ap
    rows = [{"date": "2020-05-19", "next": "2020-05-20"}, {"date": "2020-05-20", "next": "2020-05-21"}]
    assert ap.within(rows, "2020-01-02", "2020-05-20") == rows[:1]


def test_factorial_needs_the_same_dates_in_all_four_cells():
    a, b = minimal(1e6, 1.), minimal(1e6, 2.)
    b["modules"]["accounting"]["daily"] = [{"date": "2024-01-03", "net_pnl": 2.}]
    out = audit_factorial.analyze(a, a, a, b)
    assert not out["comparable"] and out["state"] == "insufficient"


def test_pair_comparison_cannot_rank_runs_with_a_missing_interior_session():
    a, b = minimal(1e6, 1.), minimal(1e6, 1000.)
    a["modules"]["accounting"]["daily"] = [{"date": d, "net_pnl": v} for d, v in
        [("2024-01-02", 10.), ("2024-01-03", -100.), ("2024-01-04", 200.)]]
    b["modules"]["accounting"]["daily"] = [{"date": "2024-01-02", "net_pnl": 10.},
                                              {"date": "2024-01-04", "net_pnl": 1000.}]
    out = av.compare(a, b)
    assert not out["comparable"] and out["state"] == "descriptive_only"
    assert out["only_original_dates"] == ["2024-01-03"] and "full_gain" not in out


def test_cash_flow_cannot_be_treated_as_strategy_gain():
    a, b = minimal(1e6, 1.), minimal(1e6, 1000.)
    b["modules"]["accounting"]["daily"][0]["external_flow"] = 1000.
    assert av.compare(a, b)["state"] == "invalid"


def test_missing_modeled_cost_blocks_restatement(base):
    run = strategy_audit.analyze(base, SETTINGS)
    run["modules"]["fee_analysis"]["fills"] = []
    assert av.restate(run) is None
