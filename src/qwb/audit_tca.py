"""Implementation shortfall from explicit parent orders and execution records.

Positive cost is adverse for either side. All timestamps must be timezone-aware;
all prices use one declared currency and adjustment basis. This accounting
decomposition does not identify market impact or authenticate a data source.
"""
from __future__ import annotations

import csv
import hashlib
import io
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from .audit_reconciliation import SOURCE_KINDS, _number

ORDER_FIELDS = ("order_id", "code", "side", "quantity", "decision_time", "decision_price",
                "arrival_time", "arrival_price", "end_time", "end_price")
FILL_FIELDS = ("order_id", "trade_id", "code", "side", "time", "quantity", "price", "fee")


def _read(raw, fields, label):
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        names = reader.fieldnames or []
        if len(names) != len(set(names)) or not set(fields) <= set(names):
            raise ValueError(f"{label} 需要唯一表头：{','.join(fields)}")
        rows = []
        for i, source in enumerate(reader, 2):
            if None in source or any(not (source.get(k) or "").strip() for k in fields):
                raise ValueError(f"{label} 第 {i} 行缺字段")
            r = {k: source[k].strip() for k in fields}
            if r["side"] not in ("buy", "sell"):
                raise ValueError(f"{label} 第 {i} 行 side 须为 buy/sell")
            for k in fields:
                if k in ("quantity", "price", "fee") or k.endswith("_price"):
                    r[k] = _number(r[k], f"{label} 第 {i} 行 {k}", positive=k != "fee")
                if k == "time" or k.endswith("_time"):
                    try:
                        t = datetime.fromisoformat(r[k].replace("Z", "+00:00"))
                    except ValueError:
                        raise ValueError(f"{label} 第 {i} 行 {k} 须为 ISO 时间") from None
                    if t.tzinfo is None or t.utcoffset() is None:
                        raise ValueError(f"{label} 第 {i} 行 {k} 必须含时区")
                    r[k] = t.astimezone(timezone.utc)
            rows.append(r)
        return rows
    except (UnicodeError, csv.Error):
        raise ValueError(f"{label} 须为有效 UTF-8 CSV") from None


def analyze(orders_raw, fills_raw, source_kind="broker_export"):
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source_kind 须为 broker_export/simulator_native/synthetic_fixture")
    orders, fills = _read(orders_raw, ORDER_FIELDS, "orders"), _read(fills_raw, FILL_FIELDS, "fills")
    by_id, executions, identities = {}, defaultdict(list), set()
    for o in orders:
        if o["order_id"] in by_id:
            raise ValueError("母订单编号重复；须先统一账户及市场前缀")
        if not o["decision_time"] <= o["arrival_time"] <= o["end_time"]:
            raise ValueError("须满足决策时间 ≤ 到达时间 ≤ 截止时间")
        by_id[o["order_id"]] = o
    for f in fills:
        identity = (f["time"].date(), f["trade_id"])
        if identity in identities:
            raise ValueError("成交编号重复")
        identities.add(identity)
        o = by_id.get(f["order_id"])
        if not o:
            raise ValueError("成交缺少对应母订单；不能忽略无法匹配的成交")
        if (f["code"], f["side"]) != (o["code"], o["side"]):
            raise ValueError("母订单与成交的证券或方向不一致")
        if not o["arrival_time"] <= f["time"] <= o["end_time"]:
            raise ValueError("成交必须位于到达至截止的时间窗口")
        executions[o["order_id"]].append(f)
    rows = []
    components = ("delay_cost", "execution_cost", "opportunity_cost", "fees", "total_cost", "decision_notional")
    totals = dict.fromkeys(components, Decimal(0))
    for identity, o in sorted(by_id.items()):
        fs = executions[identity]
        q = sum((f["quantity"] for f in fs), Decimal(0))
        if q > o["quantity"]:
            raise ValueError("成交数量超过母订单数量；须修复生命周期或数量口径")
        remaining = o["quantity"] - q
        sign = 1 if o["side"] == "buy" else -1
        paid = sum((f["quantity"] * f["price"] for f in fs), Decimal(0))
        fees = sum((f["fee"] for f in fs), Decimal(0))
        delay = sign * o["quantity"] * (o["arrival_price"] - o["decision_price"])
        execution = sign * (paid - q * o["arrival_price"])
        opportunity = sign * remaining * (o["end_price"] - o["arrival_price"])
        total = delay + execution + opportunity + fees
        direct = sign * (paid + remaining * o["end_price"] - o["quantity"] * o["decision_price"]) + fees
        if total != direct:
            raise ValueError("实现短缺分解未闭合")
        values = {"delay_cost": delay, "execution_cost": execution, "opportunity_cost": opportunity,
                  "fees": fees, "total_cost": total, "decision_notional": o["quantity"] * o["decision_price"]}
        for k, v in values.items():
            totals[k] += v
        rows.append({"order_id": identity, "code": o["code"], "side": o["side"],
                     "quantity": str(o["quantity"]), "filled_quantity": str(q), "unfilled_quantity": str(remaining),
                     "completion_ratio": str(q / o["quantity"]), "cost_bp": str(total / values["decision_notional"] * 10000),
                     "decision_time": o["decision_time"].isoformat(), "arrival_time": o["arrival_time"].isoformat(),
                     "end_time": o["end_time"].isoformat(), "trade_ids": sorted(f["trade_id"] for f in fs),
                     **{k: str(v) for k, v in values.items()}, "decomposition_residual": str(total - direct)})
    return {"schema_version": "implementation_shortfall_v1", "status": "computed" if orders else "insufficient",
            "source_kind": source_kind, "source_independence": "not_verified", "orders": len(rows), "fills": len(fills),
            "rows": rows, "totals": {k: str(v) for k, v in totals.items()},
            "cost_bp": str(totals["total_cost"] / totals["decision_notional"] * 10000) if orders else None,
            "input_sha256": {"orders": hashlib.sha256(orders_raw).hexdigest(), "fills": hashlib.sha256(fills_raw).hexdigest()},
            "scope": "同账户同币种、同价格与数量口径的明确母订单及完整成交。正值为不利成本，负值为价格改善。"
                     "延迟按全部目标数量计算，执行偏差按已成交数量计算，未完成量按声明截止价计机会成本。"
                     "截止日期和基准价改变会改变结果；取消订单也须保留。执行偏差含价差和价格变化，不能单独识别市场冲击。"
                     "不认证订单/行情来源，也不将模拟结果当作实盘TCA或校准模型"}
