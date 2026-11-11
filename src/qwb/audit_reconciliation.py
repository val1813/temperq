"""Read-only execution reconciliation against an independently supplied broker export.

Match explicit order identity, never nearest prices or dates. Broker executions
may be fragmented differently: compare quantity, gross amount and fees per
order/day/security/side. This does not validate positions, cash or a backtest's
economic realism, and does not authenticate the origin of a supplied CSV.
"""
from __future__ import annotations

import csv
import hashlib
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

FIELDS = ("date", "order_id", "trade_id", "code", "side", "quantity", "price", "fee")
SOURCE_KINDS = ("broker_export", "simulator_native", "synthetic_fixture")


def _number(value, name, positive=False):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{name} 须为有限数字") from None
    if not result.is_finite() or result < 0 or (positive and not result):
        raise ValueError(f"{name} 须为{'正数' if positive else '非负有限数字'}")
    return result


def read(raw: bytes, label: str):
    try:
        table = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        headers = table.fieldnames or []
        if len(headers) != len(set(headers)) or not set(FIELDS) <= set(headers):
            raise ValueError(f"{label} 需要唯一表头：{','.join(FIELDS)}")
        rows, identities = [], set()
        for line, source in enumerate(table, 2):
            if None in source or any(source.get(k) is None for k in FIELDS):
                raise ValueError(f"{label} 第 {line} 行列数不完整")
            r = {k: source[k].strip() for k in FIELDS}
            if any(not r[k] for k in FIELDS):
                raise ValueError(f"{label} 第 {line} 行有必填项为空")
            try:
                if date.fromisoformat(r["date"]).isoformat() != r["date"]:
                    raise ValueError()
            except ValueError:
                raise ValueError(f"{label} 第 {line} 行日期须为 YYYY-MM-DD") from None
            if r["side"] not in ("buy", "sell"):
                raise ValueError(f"{label} 第 {line} 行 side 须为 buy/sell")
            identity = (r["date"], r["trade_id"])
            if identity in identities:
                raise ValueError(f"{label} 第 {line} 行成交编号重复，不能自动去重")
            identities.add(identity)
            for k in ("quantity", "price", "fee"):
                r[k] = _number(r[k], f"{label} 第 {line} 行 {k}", positive=k != "fee")
            rows.append(r)
    except UnicodeDecodeError:
        raise ValueError(f"{label} 须为 UTF-8 CSV") from None
    except csv.Error as exc:
        raise ValueError(f"{label} CSV 格式错误：{exc}") from None
    return rows


def _orders(rows):
    groups = defaultdict(lambda: {"quantity": Decimal(0), "notional": Decimal(0),
                                  "fee": Decimal(0), "trade_ids": []})
    for r in rows:
        key = tuple(r[k] for k in ("date", "order_id", "code", "side"))
        g = groups[key]
        g["quantity"] += r["quantity"]
        g["notional"] += r["quantity"] * r["price"]
        g["fee"] += r["fee"]
        g["trade_ids"].append(r["trade_id"])
    for g in groups.values():
        g["trade_ids"].sort()
    return groups


def reconcile(internal_raw: bytes, broker_raw: bytes, amount_tolerance="0.01", fee_tolerance="0.01",
              source_kind="broker_export"):
    """Normalized same-account, same-scope, same-currency exports, one row per fill.

    Decimal strings in output preserve exact money; tolerance applies per order,
    quantity must match exactly. Missing orders never pass under a tolerance.
    """
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source_kind 须为 broker_export/simulator_native/synthetic_fixture")
    amount_tol = _number(amount_tolerance, "amount_tolerance")
    fee_tol = _number(fee_tolerance, "fee_tolerance")
    internal, broker = read(internal_raw, "internal"), read(broker_raw, "broker")
    left, right = _orders(internal), _orders(broker)
    rows = []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        delta = {k: a[k] - b[k] for k in ("quantity", "notional", "fee")} if a and b else None
        state = ("missing_internal" if a is None else "missing_broker" if b is None else
                 "matched" if delta["quantity"] == 0 and abs(delta["notional"]) <= amount_tol
                 and abs(delta["fee"]) <= fee_tol else "mismatch")
        serialize = lambda g: {k: str(v) if isinstance(v, Decimal) else v for k, v in g.items()} if g else None
        rows.append({**dict(zip(("date", "order_id", "code", "side"), key)),
                     "state": state, "internal": serialize(a), "broker": serialize(b),
                     "internal_minus_broker": serialize(delta)})
    breaks = sum(r["state"] != "matched" for r in rows)
    return {"schema_version": "execution_reconciliation_v1",
            "source_kind": source_kind, "source_independence": "not_verified",
            "same_input_bytes": internal_raw == broker_raw,
            "status": "insufficient" if not internal or not broker else "breaks" if breaks else "matched",
            "input_sha256": {"internal": hashlib.sha256(internal_raw).hexdigest(),
                             "broker": hashlib.sha256(broker_raw).hexdigest()},
            "tolerances": {"quantity": "0", "notional": str(amount_tol), "fee": str(fee_tol)},
            "internal_fills": len(internal), "broker_fills": len(broker),
            "orders": len(rows), "matched_orders": len(rows) - breaks, "breaks": breaks,
            "rows": rows,
            "scope": "同一账户、同一期间和币种的成交导出，按明确订单编号匹配；金额和量须统一为元及股/份。"
                     "容差按订单应用；不抵消不同订单的差额。不核验现金、持仓、公司行动或文件来源真实性，"
                     "不把回测与实盘差异当作账务一致性；matched 不是机构认证"}
