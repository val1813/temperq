from copy import deepcopy

import pytest

from qwb import audit_impact as impact, audit_verification as verification
from qwb import strategy_audit, strategy_imports


def result():
    return {"modules": {"accounting": {"status": "verified", "contributions": [], "daily": []}}}


def test_failed_exit_tracks_only_unsold_shares_and_deduplicates_retries():
    r = result()
    r["modules"]["activity"] = {"exits": {"events": [
        {"date": d, "code": "A", "ordered": 100, "filled": 0} for d in ("2024-01-02", "2024-01-03")]}}
    b = {"snapshots": [{"date": d} for d in ("2024-01-02", "2024-01-03", "2024-01-04")],
         "positions": [{"date": "2024-01-02", "code": "A", "quantity": 100, "price": 10},
                       {"date": "2024-01-03", "code": "A", "quantity": 200, "price": 12},
                       {"date": "2024-01-04", "code": "A", "quantity": 100, "price": 50}],
         "fills": [{"date": "2024-01-03", "time": "10:00", "code": "A", "side": "buy", "quantity": 100, "price": 11, "fee": 2},
                   {"date": "2024-01-04", "time": "10:00", "code": "A", "side": "sell", "quantity": 100, "price": 13, "fee": 5}]}
    m = impact.build(r, b)["failed_exits"]
    assert m["amount"] == 295  # 100*(12-10) + 100*(13-12) - 5; new 100 shares excluded
    assert m["coverage"]["complete"] and len(m["rows"]) == 2


def test_failed_exit_excludes_action_codes_instead_of_inventing_loss():
    r = result()
    r["modules"]["activity"] = {"exits": {"events": [{"date": "2024-01-02", "code": "A", "ordered": 100, "filled": 0}]}}
    m = impact.build(r, {"actions": [{"date": "2024-01-03", "code": "A", "quantity_change": 100}]})["failed_exits"]
    assert m["amount"] is None and not m["coverage"]["complete"]


@pytest.mark.parametrize("end,expected", [(12, 120), (8, -120)])
def test_missing_buy_is_signed_price_scenario_not_realized_profit(end, expected):
    r = result()
    r["modules"]["partial_entry"] = {"episodes": [{"date": "2024-01-02", "end": "2024-01-05", "code": "A", "ordered": 100, "filled": 40}]}
    b = {"positions": [{"date": "2024-01-02", "code": "A", "price": 10}, {"date": "2024-01-05", "code": "A", "price": end}]}
    m = impact.build(r, b)["partial_entry"]
    assert m["amount"] == expected and m["kind"] == "counterfactual"
    assert m["rows"][0]["quantity"] == 60


def test_missing_buy_overlap_and_corporate_action_are_visible():
    r = result()
    e = {"date": "2024-01-02", "end": "2024-01-05", "code": "A", "ordered": 100, "filled": 40}
    r["modules"]["partial_entry"] = {"episodes": [e, deepcopy(e)]}
    b = {"positions": [{"date": d, "code": "A", "price": p} for d, p in (("2024-01-02", 10), ("2024-01-05", 12))]}
    m = impact.build(r, b)["partial_entry"]
    assert m["amount"] == 120 and m["coverage"]["covered"] == 1 and m["excluded"]
    b["actions"] = [{"date": "2024-01-03", "code": "A", "cash": 3}]
    assert impact.build(r, b)["partial_entry"]["amount"] is None


def test_zero_filled_buy_can_use_returned_candidate_closes_and_stops_before_retry():
    r = result()
    b = {"orders": [{"date": d, "code": "A", "side": "buy", "quantity": 100, "filled": 0} for d in ("2024-01-02", "2024-01-04")],
         "snapshots": [{"date": d} for d in ("2024-01-02", "2024-01-03", "2024-01-04")],
         "prices": [{"date": d, "code": "A", "close": p} for d, p in (("2024-01-02", 10), ("2024-01-03", 12), ("2024-01-04", 50))]}
    m = impact.build(r, b)["partial_entry"]
    assert m["amount"] == 200 and m["rows"][0]["end"] == "2024-01-03"


def test_state_money_is_related_full_day_contribution_not_entire_portfolio():
    r = result()
    r["modules"]["accounting"]["contributions"] = [
        {"date": "2024-01-03", "code": "A", "net_pnl": -35},
        {"date": "2024-01-03", "code": "B", "net_pnl": 1000}]
    r["modules"]["state"] = {"rows": [{"date": "2024-01-03", "orphan": ["A"], "phantom": ["C"]}]}
    m = impact.build(r)["state"]
    assert m["amount"] == -35 and m["kind"] == "historical"


def test_target_contribution_is_reused_without_claiming_removable_profit():
    r = result()
    r["modules"]["fulfilment"] = {"by_target": {"outside": 28600, "rows": [
        {"date": "2024-01-03", "code": "A", "group": "outside", "net_pnl": 28600}]}}
    c = [{"id": "off_target_pnl"}]
    impact.attach(c, r)
    assert c[0]["impact"]["amount"] == 28600 and "28,600.00" in c[0]["saving"]
    assert "复跑" in c[0]["net"]


def test_locked_fill_lots_include_open_marks_and_no_duplicate_endpoints():
    r = result()
    buy = {"date": "2024-01-02", "code": "A", "side": "buy", "quantity": 100, "price": 10, "ref": {"source": "fills", "row": 2}}
    sell = {"date": "2024-01-03", "code": "A", "side": "sell", "quantity": 60, "price": 12, "ref": {"source": "fills", "row": 3}}
    r["modules"]["limits"] = {"events": [buy, sell]}
    r["modules"]["holding_analysis"] = {"closed": [{"entry_ref": buy["ref"], "exit_ref": sell["ref"], "exit_date": sell["date"], "quantity": 60, "net_pnl": 110}],
                                           "open": [{"entry_ref": buy["ref"], "quantity": 40, "net_pnl": -42}]}
    m = impact.build(r, {"fills": [buy, sell]})["limits"]
    assert m["amount"] == 68 and len(m["rows"]) == 2 and m["coverage"]["complete"]
    # Unknown opening inventory cannot silently receive the acquired lot's whole P&L.
    r["modules"]["holding_analysis"]["closed"] = []
    m = impact.build(r, {"fills": [buy, sell]})["limits"]
    assert m["amount"] == -42 and not m["coverage"]["complete"]


def test_prelisting_lot_uses_full_life_not_all_lots_of_the_name():
    r = result()
    f = {"date": "2024-01-02", "code": "A", "side": "buy", "quantity": 100, "price": 10, "ref": {"source": "f", "row": 2}}
    r["modules"]["holding_analysis"] = {"closed": [{"entry_ref": f["ref"], "quantity": 100, "net_pnl": 195},
                                                    {"entry_ref": {"source": "f", "row": 4}, "quantity": 100, "net_pnl": 3000}]}
    m = impact.build(r, {"fills": [f], "listings": [{"code": "A", "listed_date": "2024-01-03"}]})["listings"]
    assert m["amount"] == 195


def test_old_example_only_audit_does_not_report_zero_for_uncovered_events():
    r = result()
    r["modules"]["limits"] = {"locked": 10, "examples": [{}]}
    m = impact.build(r, {})["limits"]
    assert m["amount"] is None and not m["coverage"]["complete"]


def test_idle_is_account_profit_not_opportunity_loss():
    r = result()
    r["modules"]["accounting"]["daily"] = [{"date": "2024-01-02", "net_pnl": 2}]
    m = impact.build(r, {"snapshots": [{"date": "2024-01-02"}]})["idle"]
    assert m["amount"] == 2 and "错过" in m["scope"]


def test_unreconciled_books_do_not_create_money():
    r = result()
    r["modules"]["accounting"]["status"] = "needs_reconciliation"
    assert impact.build(r, {}) == {}


def test_full_analyze_and_exports_retain_recomputable_money():
    from qwb import strategy_audit_ui as ui, audit_exports, audit_report, audit_actions
    b = strategy_imports.normalize(ui.sample_files(), opening_cash=10000)
    r = strategy_audit.analyze(b)
    assert "monetary_impact" in r["modules"]
    rows, columns = audit_exports.table(r, "impact-money")
    assert "net_pnl" in columns and isinstance(rows, list)
    assert "改完有什么帮助" in audit_report.impact_section(r, audit_actions.cards(r, b))


def series(values):
    return [{"date": str(i), "difference": float(v)} for i, v in enumerate(values)]


def test_paired_interval_constant_and_short_paths_do_not_promise_significance():
    for values in ([0] * 100, [5] * 100):
        r = verification.paired_inference(series(values), 10000)
        assert r["status"] == "degenerate" and "planning" not in r
    assert verification.paired_inference(series([1, 2, 3]), 10000)["status"] == "insufficient"


def test_paired_interval_and_planning_are_symmetric_for_negative_effect():
    values = [100 + (i % 7 - 3) * 10 for i in range(252)]
    a = verification.paired_inference(series(values), 100000)
    b = verification.paired_inference(series([-v for v in values]), 100000)
    assert a["signal"] == "positive" and b["signal"] == "negative"
    assert a["mean_daily_ci"][0] == pytest.approx(-b["mean_daily_ci"][1])
    assert a["planning"]["total_years"] == pytest.approx(b["planning"]["total_years"])
    assert a["total_difference"] == sum(values)


def test_serial_correlation_widens_interval_and_zero_observed_mean_still_uses_preset_effect():
    clustered = [v for _ in range(10) for v in [10] * 20 + [-10] * 20]
    alternating = [10, -10] * 200
    a = verification.paired_inference(series(clustered), 100000)
    b = verification.paired_inference(series(alternating), 100000)
    assert a["standard_error"] > b["standard_error"]
    assert a["planning"]["total_years"] > 0 and a["signal"] == "unresolved"
    assert a["planning"]["annual_effect_rate"] == .02


def test_explicit_effect_plan_is_not_the_noisy_observed_effect():
    r = verification.paired_inference(series([10, -10] * 126), 100000, annual_effect=.01)
    assert r["planning"]["daily_effect"] == pytest.approx(1000 / 252)
    assert r["planning"]["total_years"] > 0


def test_planning_ignores_observed_mean_and_obeys_user_target():
    values = [10, -10] * 126
    a = verification.paired_inference(series(values), 100000)
    b = verification.paired_inference(series([v + 1000 for v in values]), 100000)
    c = verification.paired_inference(series(values), 100000, annual_effect=.01)
    assert a["planning"]["total_years"] == pytest.approx(b["planning"]["total_years"])
    assert c["planning"]["total_years"] == pytest.approx(4 * a["planning"]["total_years"])
    assert a["planning"]["effect_source"] == "prespecified_annual_initial_capital_rate"


def test_37_sessions_hide_annualization_and_planning_and_sensitivity_hides_signal():
    rows = series([100 + i % 5 for i in range(37)])
    a = verification.paired_inference(rows, 100000)
    b = verification.paired_inference(rows, 100000, sensitivity=True)
    for r in (a, b):
        assert not any(k.startswith("annualized") for k in r)
        assert "planning" not in r
    assert "signal" not in b and "不判策略改善" in verification.uncertainty_text(b)
    c = verification.compare(comparable([10] * 100), comparable([11 + i % 5 for i in range(100)]), sensitivity=True)
    assert "signal" not in c["uncertainty"]["full"] and "inference_state" not in c
    assert c["state"] == "model_sensitivity"


@pytest.mark.parametrize("value", [None, 0, -1, float('nan'), float('inf'), 2])
def test_invalid_planning_effect_is_rejected(value):
    with pytest.raises(ValueError, match="最小有意义效应"):
        verification.paired_inference(series([1, 2] * 100), 100000, annual_effect=value)


def test_cli_legacy_same_code_has_no_improvement_signal(tmp_path):
    import json
    from qwb.audit_cli import compare_runs
    for name, values in [('original', [10] * 37), ('rerun', [11 + i % 5 for i in range(37)])]:
        directory = tmp_path / name
        directory.mkdir()
        result = comparable(values)
        result['input_digest'] = name
        (directory / 'evidence.json').write_text(json.dumps({
            'input': {'code': {'source': 'same_strategy = True'}}, 'result': result}), encoding='utf-8')
    out = compare_runs(tmp_path / 'original', tmp_path / 'rerun', minimum_effect_pct=1.)
    assert out['mode'] == 'model_sensitivity'
    assert 'signal' not in out['uncertainty']['full'] and 'inference_state' not in out
    assert not any(k.startswith('annualized') for k in out['uncertainty']['full'])
    assert '不判策略改善' in out['verdict']


def test_cash_buffer_and_uncompleted_buy_display_same_money_only_once():
    from qwb import audit_report, audit_exports
    primary = impact.money("counterfactual", [{"date": "2024-01-02", "code": "A", "net_pnl": -20436.77}], "price scenario")
    r = {"modules": {"monetary_impact": {"partial_entry": primary, "cash_buffer": primary}}}  # old persisted format
    c = [{"id": "partial_entry", "title": "买单没补"}, {"id": "cash_buffer", "title": "现金缓冲"}]
    impact.attach(c, r)
    assert c[1]["impact"]["reference_to"] == "partial_entry"
    assert c[1]["impact"]["amount"] is None and "20,436.77" not in c[1]["saving"]
    assert audit_report.impact_section(r, c).count('20,436.77') == 1
    assert len(audit_exports.table(r, "impact-money")[0]) == 1  # legacy saved evidence too
    # New persisted form also deduplicates CSV rows, including zero amounts.
    r["modules"]["monetary_impact"]["cash_buffer"] = c[1]["impact"]
    rows, _ = audit_exports.table(r, "impact-money")
    assert len(rows) == 1 and rows[0]["card"] == "partial_entry"
    only_cash = [{"id": "cash_buffer", "title": "现金缓冲"}]
    impact.attach(only_cash, r)
    assert only_cash[0]["impact"]["amount"] == -20436.77


def test_fill_stress_money_displays_eligible_denominator_and_notional_coverage():
    r = result()
    r['modules']['fill_quality'] = {'floor': {
        'change': -100., 'fills': 1, 'eligible_fills': 2, 'coverage': .4,
        'rows': [{'net_pnl': -100.}],
        'observations': [{'reason': 'window_past_close', 'status': 'excluded'},
                         {'reason': 'outside_accounted_intervals', 'status': 'excluded'}]}}
    item = impact.build(r)['fill_quality']
    assert item['coverage'] == {'covered': 1, 'total': 2, 'complete': False}
    assert len(item['excluded']) == 1
    assert '1/2 笔、成交额 40.00%' in item['scope']


@pytest.mark.parametrize("identity,modules,expected", [
    ("minimum_commission", {"fee_analysis": {"minimum_premium": 500}}, 500),
    ("fee_setting", {"fee_analysis": {"reported_fee": 600, "modeled_fee": 200}}, 400),
    ("tick_floor", {"tick_analysis": {"accounted_intervals": {"tick_scenario_cost": 80}}}, 80),
    ("rank_churn", {"signal_mapping": {"avoided_cost": 125}}, 125),
    ("fill_quality", {"fill_quality": {"floor": {"change": -100, "coverage": .5}}}, -100),
])
def test_existing_cost_amounts_enter_structured_scenarios(identity, modules, expected):
    r = result()
    r["modules"].update(modules)
    c = [{"id": identity}]
    impact.attach(c, r)
    assert c[0]["impact"]["amount"] == expected
    assert c[0]["impact"]["kind"] == "cost_scenario"


def test_short_holding_and_low_price_costs_respect_user_thresholds():
    r = result()
    r["rules"] = {"thresholds": {"T-HOLD-SHORT": 2, "T-PRICE-LOW": 3}}
    r["modules"]["holding_analysis"] = {"closed": [
        {"code": "600000.SH", "calendar_days": 2, "entry_price": 2, "fees": 5, "tick_scenario_cost": 2},
        {"code": "600000.SH", "calendar_days": 4, "entry_price": 4, "fees": 50, "tick_scenario_cost": 20}]}
    m = impact.build(r)
    assert m["holding_period"]["amount"] == m["low_price"]["amount"] == 7


@pytest.mark.parametrize("identity,key,events", [
    ("price_jump", "prices", [{"date": "2024-01-03", "code": "A"}] * 25),
    ("decisions", "decisions", None),
])
def test_related_security_day_amount_not_truncated_examples(identity, key, events):
    r = result()
    rows = [{"date": "2024-01-03", "code": "A", "net_pnl": 20}, {"date": "2024-01-03", "code": "B", "net_pnl": 500}]
    r["modules"]["accounting"]["contributions"] = rows
    r["modules"][key] = {"events": events} if events else {"against_selection": {"events": [{"date": "2024-01-03", "code": "A"}]}}
    assert impact.build(r)[identity]["amount"] == 20


def comparable(values):
    from datetime import date, timedelta
    return {"mode": "full", "modules": {"accounting": {"status": "verified", "baseline_equity": 100000,
        "daily": [{"date": (date(2024, 1, 1) + timedelta(days=i)).isoformat(), "net_pnl": v} for i, v in enumerate(values)]}}}


def test_compare_outputs_paired_rows_and_validation_inference():
    a, b = comparable([10] * 100), comparable([10 + (i % 5 - 2) for i in range(100)])
    a["period_review"] = {"partition": {"status": "planned", "development_start": "2024-01-01", "development_end": "2024-02-19",
                                      "validation_start": "2024-02-20", "validation_end": "2024-04-09"}}
    c = verification.compare(a, b)
    assert c["comparable"]
    assert len(c["uncertainty"]["full"]["daily"]) == 100
    assert c["uncertainty"]["full"]["total_difference"] == c["full_gain"]
    assert c["uncertainty"]["validation"]["sessions"] == 50
    assert "区间包含零" in c["verdict"]


def test_compare_rejects_holes_scale_fees_flows_and_nonpositive_equity():
    a = comparable([10] * 100)
    b = deepcopy(a)
    b["modules"]["accounting"]["daily"].pop(40)
    assert verification.compare(a, b)["state"] == "descriptive_only"
    for mutate in (
        lambda r: r["modules"]["accounting"].update(baseline_equity=200000),
        lambda r: r.update(settings={"commission_bp": 3}),
        lambda r: r["modules"]["accounting"]["daily"][0].update(external_flow=100),
        lambda r: r["modules"]["accounting"]["daily"][0].update(net_pnl=-100001),
    ):
        b = deepcopy(a)
        mutate(b)
        assert not verification.compare(a, b)["comparable"]
