import csv
import io
import json
from decimal import Decimal

import pytest

from qwb.audit_tca import FILL_FIELDS, ORDER_FIELDS, analyze


def csv_bytes(fields, rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def order(**change):
    return dict(order_id="p1", code="510300.SH", side="buy", quantity="100",
                decision_time="2024-01-02T09:30:00+08:00", decision_price="10",
                arrival_time="2024-01-02T09:31:00+08:00", arrival_price="11",
                end_time="2024-01-02T15:00:00+08:00", end_price="13") | change


def fill(**change):
    return dict(order_id="p1", trade_id="t1", code="510300.SH", side="buy", quantity="60",
                time="2024-01-02T09:32:00+08:00", price="12", fee="5") | change


def run(orders=None, fills=None):
    return analyze(csv_bytes(ORDER_FIELDS, [order()] if orders is None else orders),
                   csv_bytes(FILL_FIELDS, [fill()] if fills is None else fills))


def test_partial_execution_shortfall_closes_with_unfilled_opportunity():
    r = run()
    t = r["totals"]
    assert {k: Decimal(t[k]) for k in ("delay_cost", "execution_cost", "opportunity_cost", "fees", "total_cost")} == {
        "delay_cost": 100, "execution_cost": 60, "opportunity_cost": 80, "fees": 5, "total_cost": 245}
    assert r["cost_bp"] == "2450.000"
    assert r["rows"][0]["unfilled_quantity"] == "40"
    assert r["rows"][0]["decomposition_residual"] == "0"


def test_sells_use_adverse_cost_sign_and_fees_remain_positive():
    r = run([order(side="sell")], [fill(side="sell")])
    assert Decimal(r["totals"]["total_cost"]) == -235
    assert Decimal(r["totals"]["fees"]) == 5


def test_unfilled_orders_are_not_zero_cost_and_full_orders_have_no_opportunity():
    r = run(fills=[])
    assert Decimal(r["totals"]["total_cost"]) == 300
    assert r["rows"][0]["completion_ratio"] == "0"
    r = run(fills=[fill(quantity="100")])
    assert Decimal(r["totals"]["opportunity_cost"]) == 0
    assert Decimal(r["totals"]["total_cost"]) == 205


def test_fragmentation_does_not_change_aggregate_cost():
    a = run(fills=[fill(quantity="100")])
    b = run(fills=[fill(quantity="40", fee="2"), fill(trade_id="t2", quantity="60", fee="3")])
    assert a["totals"] == b["totals"]
    assert a["cost_bp"] == b["cost_bp"]


@pytest.mark.parametrize("change", [{"quantity": "101"}, {"time": "2024-01-02T09:30:00+08:00"},
                                     {"time": "2024-01-02T15:01:00+08:00"}, {"time": "2024-01-02T09:32:00"},
                                     {"code": "OTHER"}, {"side": "sell"}, {"price": "NaN"},
                                     {"order_id": "unmatched"}])
def test_invalid_execution_evidence_never_produces_a_partial_success(change):
    with pytest.raises(ValueError):
        run(fills=[fill(**change)])


def test_duplicate_orders_or_fills_and_invalid_benchmark_timing_are_rejected():
    with pytest.raises(ValueError, match="母订单编号重复"):
        run(orders=[order(), order()])
    with pytest.raises(ValueError, match="成交编号重复"):
        run(fills=[fill(), fill()])
    with pytest.raises(ValueError, match="决策时间"):
        run(orders=[order(arrival_time="2024-01-02T09:29:00+08:00")])
    assert run(orders=[], fills=[])["status"] == "insufficient"


def test_tca_cli_preserves_existing_result_and_marks_simulated_origin(tmp_path, capsys):
    from qwb.cli import main
    orders, fills, output = tmp_path / "orders.csv", tmp_path / "fills.csv", tmp_path / "tca.json"
    orders.write_bytes(csv_bytes(ORDER_FIELDS, [order()]))
    fills.write_bytes(csv_bytes(FILL_FIELDS, [fill()]))
    args = ["audit", "tca", "--orders", str(orders), "--fills", str(fills),
            "--source-kind", "simulator_native", "--out", str(output), "--json"]
    assert main(args) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["source_kind"] == "simulator_native" and r["source_independence"] == "not_verified"
    original = output.read_bytes()
    assert main(args) == 2
    capsys.readouterr()
    assert output.read_bytes() == original
