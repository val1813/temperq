"""Regressions for causal overreach, fragmented fills and independent execution evidence."""
import json
from datetime import date, timedelta

import pytest

from qwb import audit_actions, audit_allocation, audit_benchmark, audit_capacity
from qwb.audit_reconciliation import FIELDS, reconcile

OK = {"status": "verified"}


def allocation_bundle():
    days = [(date(2024, 1, 2) + timedelta(days=i)).isoformat() for i in range(21)]
    return {"snapshots": [{"date": d, "equity": 100000., "cash": 100000.} for d in days],
            "positions": [],
            "prices": [{"date": d, "code": "511010.SH", "close": 100.} for d in days],
            "target_weights": [{"date": d, "code": "511010.SH", "target_weight": .4, "complete": True} for d in days],
            "orders": [{"date": d, "time": "09:35:00", "code": "511010.SH", "side": "buy",
                        "quantity": 400, "filled": 0, "reason": "09:35 单分钟成交量限制", "ref": {"row": i + 2}}
                       for i, d in enumerate(days)]}


def test_target_gap_keeps_the_finding_but_does_not_identify_a_strategy_defect():
    b = allocation_bundle()
    b["orders"][0].update(reason="其他原因", message="exceeds_observed_minute_volume", order_id="local-001")
    result = audit_allocation.analyze(b, OK)
    assert result["status"] == "gap"
    assert result["names"][0]["over_days"] == 21
    assert len(result["diagnosis"]["blocked_orders"]) == 21
    first_order = result["diagnosis"]["blocked_orders"][0]
    assert first_order["message"] == "exceeds_observed_minute_volume"
    assert first_order["order_id"] == "local-001" and first_order["time"] == "09:35:00"
    cards = audit_actions.cards({"modules": {"allocation": result}})
    assert len(cards) == 1 and cards[0]["state"] == "review"
    assert cards[0]["evidence"] == "E1"  # The measured discrepancy remains a fact.
    assert cards[0]["causal_status"] == "unresolved"
    assert "执行代理" in cards[0]["overturn"]
    assert "--sensitivity" in cards[0]["net"]


def test_target_diagnosis_does_not_attach_wrong_direction_or_unrelated_orders():
    b = allocation_bundle()
    b["orders"][0]["side"] = "sell"
    b["orders"][1]["code"] = "510300.SH"
    result = audit_allocation.analyze(b, OK)
    assert len(result["diagnosis"]["blocked_orders"]) == 19
    b["orders"] = []
    result = audit_allocation.analyze(b, OK)
    assert result["status"] == "gap" and result["diagnosis"]["blocked_orders"] == []
    assert audit_actions.cards({"modules": {"allocation": result}})[0]["state"] == "review"


def capacity_bundle():
    return {"minute_prices": [{"date": "2024-01-02", "code": "510300.SH", "time": f"10:{m:02d}",
                               "volume": 100000} for m in range(30)],
            "fills": [{"date": "2024-01-02", "code": "510300.SH", "time": f"10:{m:02d}:00",
                       "quantity": 1000, "side": "buy"} for m in range(5, 25)]}


def test_capacity_cannot_be_improved_by_fragmenting_orders():
    b = capacity_bundle()
    before = audit_capacity.analyze(b, OK)
    b["fills"] = [{**f, "quantity": 100} for f in b["fills"] for _ in range(10)]
    after = audit_capacity.analyze(b, OK)
    assert before["p95"] == after["p95"] == .01
    assert before["multiple"] == after["multiple"]
    assert after["window_count"] == before["window_count"] == 20
    assert after["fills"] == 200
    assert after["fill_coverage"] == 1


def test_capacity_does_not_net_buys_and_sells_and_aggregates_neighboring_minutes():
    b = capacity_bundle()
    b["fills"] = [{**f, "side": side, "quantity": 6000} for f in b["fills"] for side in ("buy", "sell")]
    r = audit_capacity.analyze(b, OK)
    assert r["status"] == "over" and r["p95"] == .12
    assert max(x["gross_quantity"] for x in r["windows"]) == 60000


def test_capacity_missing_data_does_not_pass_or_produce_a_scale_claim():
    b = capacity_bundle()
    b["fills"].append({**b["fills"][0], "time": "invalid"})
    r = audit_capacity.analyze(b, OK)
    assert r["status"] == "incomplete" and r["missing_fills"] == 1
    assert r["multiple"] is None
    b["minute_prices"] = b["minute_prices"][:3]
    r = audit_capacity.analyze(b, OK)
    assert r["status"] == "insufficient" and r["p95"] is None
    assert r["fill_coverage"] == 0 and r["windows"] == []


def test_capacity_duplicate_volume_conflicts_are_order_independent():
    b = capacity_bundle()
    b["minute_prices"].append({**b["minute_prices"][10], "volume": 9999999})
    a = audit_capacity.analyze(b, OK)
    b["minute_prices"].reverse()
    c = audit_capacity.analyze(b, OK)
    assert a == c
    assert a["conflicting_volume_bars"] == 1 and a["missing_fills"] == 5
    assert a["status"] == "incomplete" and a["multiple"] is None


def test_capacity_zero_volume_bar_is_observed_and_invalid_clocks_do_not_crash():
    b = capacity_bundle()
    b["minute_prices"][10]["volume"] = 0
    b["minute_prices"].append({**b["minute_prices"][0], "time": "ab:cd"})
    r = audit_capacity.analyze(b, OK)
    assert r["fill_coverage"] == 1
    assert max(x["participation"] for x in r["windows"]) == .0125
    assert audit_capacity.analyze(b, {"status": "failed"}) is None


def test_capacity_open_session_uses_observed_forward_bars_not_missing_auction():
    b = {'minute_prices': [{'date': '2024-01-02', 'code': '510300.SH', 'time': f'09:{m:02d}',
                             'volume': 100000} for m in range(31, 36)],
         'fills': [{'date': '2024-01-02', 'code': '510300.SH', 'time': '09:31:00', 'quantity': 10000, 'side': 'buy'}]}
    r = audit_capacity.analyze(b, OK)
    assert r['fill_coverage'] == 1 and r['p95'] == .02
    assert r['windows'][0]['window_start'] == 571 and r['windows'][0]['window_end'] == 575
    assert r['windows'][0]['session_boundary_shifted']
    assert r['multiple'] is None  # one observation is still insufficient
    b['minute_prices'].pop()
    assert audit_capacity.analyze(b, OK)['fill_coverage'] == 0  # actual missing bar stays missing


def test_capacity_session_windows_do_not_cross_lunch_or_use_another_session():
    assert list(audit_capacity._window('510300.SH', 690)) == list(range(686, 691))
    assert list(audit_capacity._window('159915.SZ', 781)) == list(range(781, 786))
    assert list(audit_capacity._window('600000.SH', 900)) == list(range(896, 901))
    assert list(audit_capacity._window('A.UNKNOWN', 571)) == list(range(569, 574))


def execution_csv(*rows):
    return (",".join(FIELDS) + "\n" + "\n".join(rows) + "\n").encode()


ONE = "2024-01-02,o1,t1,510300.SH,buy,100,4.00,5.00"


def test_broker_fragmentation_is_reconciled_by_exact_order_identity():
    internal = execution_csv(ONE)
    broker = execution_csv("2024-01-02,o1,b1,510300.SH,buy,40,4.00,2.00",
                           "2024-01-02,o1,b2,510300.SH,buy,60,4.00,3.00")
    r = reconcile(internal, broker)
    assert r["status"] == "matched" and r["orders"] == 1
    assert r["internal_fills"] == 1 and r["broker_fills"] == 2
    assert r["rows"][0]["internal_minus_broker"]["notional"] == "0.00"
    assert r["input_sha256"]["internal"] != r["input_sha256"]["broker"]


def test_broker_differences_do_not_cancel_across_orders():
    internal = execution_csv(ONE, ONE.replace("o1,t1", "o2,t2"))
    broker = execution_csv(ONE.replace("4.00", "4.01"),
                           ONE.replace("o1,t1", "o2,t2").replace("4.00", "3.99"))
    r = reconcile(internal, broker)
    assert r["status"] == "breaks" and r["breaks"] == 2
    assert {x["internal_minus_broker"]["notional"] for x in r["rows"]} == {"-1.00", "1.00"}


def test_broker_missing_orders_never_match_by_similar_price():
    r = reconcile(execution_csv(ONE), execution_csv(ONE.replace("o1", "other")))
    assert r["breaks"] == 2
    assert {x["state"] for x in r["rows"]} == {"missing_broker", "missing_internal"}
    assert reconcile(execution_csv(), execution_csv())["status"] == "insufficient"


@pytest.mark.parametrize("bad", [ONE.replace(",100,", ",-100,"), ONE.replace(",100,", ",0,"),
                                    ONE.replace("4.00", "NaN"), ONE.replace("5.00", "Infinity"),
                                    ONE.replace(",buy,", ",BUY,"), ONE.replace("2024-01-02", "20240102")])
def test_broker_bad_values_are_rejected(bad):
    with pytest.raises(ValueError):
        reconcile(execution_csv(bad), execution_csv(ONE))


def test_broker_duplicate_trade_ids_and_invalid_tolerances_are_rejected():
    with pytest.raises(ValueError, match="重复"):
        reconcile(execution_csv(ONE, ONE), execution_csv(ONE))
    for tol in ("-1", "NaN", "Infinity"):
        with pytest.raises(ValueError):
            reconcile(execution_csv(ONE), execution_csv(ONE), tol)
    with pytest.raises(ValueError, match="唯一表头"):
        reconcile(b"date,date\n", execution_csv(ONE))


def test_broker_quantity_is_exact_and_fee_tolerance_is_per_order():
    a = execution_csv(ONE)
    b = execution_csv(ONE.replace("5.00", "5.01"))
    assert reconcile(a, b)["status"] == "matched"
    assert reconcile(a, b, fee_tolerance="0")["status"] == "breaks"
    assert reconcile(a, execution_csv(ONE.replace(",100,", ",99,")), "1000", "1000")["status"] == "breaks"


def test_reconciliation_cli_writes_new_evidence_and_preserves_existing_output(tmp_path, capsys):
    from qwb.cli import main
    a, b, out = tmp_path / "a.csv", tmp_path / "b.csv", tmp_path / "result.json"
    a.write_bytes(execution_csv(ONE))
    b.write_bytes(execution_csv(ONE))
    argv = ["audit", "reconcile", "--internal", str(a), "--broker", str(b), "--out", str(out), "--json"]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["status"] == "matched"
    content = out.read_bytes()
    assert main(argv) == 2
    capsys.readouterr()
    assert out.read_bytes() == content
    b.write_bytes(execution_csv(ONE.replace("4.00", "4.01")))
    assert main(argv[:-3] + ["--json"]) == 1
    assert json.loads(capsys.readouterr().out)["status"] == "breaks"


def test_zero_false_alarms_still_has_positive_uncertainty():
    r = audit_benchmark.binomial_interval(0, 60)
    assert r["rate"] == 0 and r["upper"] == pytest.approx(.06017185)
    assert audit_benchmark.binomial_interval(0, 0)["upper"] is None
    r = audit_benchmark.binomial_interval(37, 58)
    assert r["lower"] < 37 / 58 < r["upper"]


def test_benchmark_records_seed_range_rules_and_all_act_cards(monkeypatch):
    seeds = []

    def simulate(seed, fault, days):
        seeds.append(seed)
        return {"trades.csv": fault}

    def audit(files, thresholds):
        fault = files["trades.csv"] != "clean"
        return {"accounting": OK, "_cards": {"finding"} if fault else set(),
                "_act_cards": ["finding"] if fault else [], "hit": fault}

    monkeypatch.setattr(audit_benchmark, "FAMILIES", (("test", simulate, ("fault",), {"fault": "finding"}),))
    monkeypatch.setattr(audit_benchmark, "DETECTORS", {"finding": lambda m: m["hit"]})
    monkeypatch.setattr(audit_benchmark, "audit", audit)
    r = audit_benchmark.run(seeds=2, days=60, seed_start=1000)
    assert set(seeds) == {1000, 1001} and r["seed_start"] == 1000
    assert r["rules"]["version"] == "audit_rules_v2"
    assert r["clean"]["any_act"] == 0
    assert r["faults"]["fault"]["detection_interval"]["rate"] == 1


def test_threshold_default_with_fractional_minimum_is_browser_valid():
    from html.parser import HTMLParser
    from qwb.strategy_audit_ui import threshold_fields

    class Inputs(HTMLParser):
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if tag == "input" and a.get("type") == "number":
                value, low, high = (float(a[k]) for k in ("value", "min", "max"))
                assert low <= value <= high
                if a["step"] != "any":
                    q = (value - low) / float(a["step"])
                    assert q == pytest.approx(round(q)), a["name"]

    Inputs().feed(threshold_fields())
