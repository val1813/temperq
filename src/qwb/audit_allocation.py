"""Target weights against what the account actually held.

With the strategy's own target weights (date, code, target_weight), each closing position's actual
weight is compared with its latest target. Reported per name: mean target, mean actual, the days the
gap exceeded the tolerance, and how many of those gaps were no larger than one board lot (100 shares)
at that day's price, which a lot-sized account cannot close. A gap within one lot is a granularity
limit, not a strategy error. Daily closing weights only; whether an order was due that day is not inferred.
"""
from __future__ import annotations

from collections import defaultdict
from .audit_target_records import portfolio_targets

LOT = 100


def analyze(bundle, accounting, thresholds=None):
    targets = [t for t in portfolio_targets(bundle.get("target_weights") or []) if t["code"] != "CASH"]
    if not targets or accounting.get("status") != "verified":
        return None
    tolerance = (thresholds or {}).get("T-ALLOC-GAP", .15)
    snaps = {s["date"]: s for s in bundle.get("snapshots") or []}
    days = sorted(snaps)
    held = defaultdict(dict)
    for p in bundle.get("positions") or []:
        held[p["date"]][p["code"]] = p
    quotes = {(p["date"], p["code"]): p["close"] for p in bundle.get("prices") or []
              if p.get("close")}
    by_date = defaultdict(dict)
    for t in targets:
        by_date[t["date"]][t["code"]] = t["target_weight"]
    plan_days = sorted(by_date)
    rows = defaultdict(lambda: {"target": [], "actual": [], "over": 0, "within_lot": 0, "lot_share": [],
                                "plan_days": 0, "plan_over": 0})
    current, k = {}, 0
    for d in days:
        while k < len(plan_days) and plan_days[k] <= d:
            current = by_date[plan_days[k]]
            k += 1
        if not current:
            continue
        equity = snaps[d]["equity"]
        if not equity:
            continue
        for code, w in current.items():
            p = held[d].get(code)
            actual = (p["market_value"] / equity) if p else 0.
            r = rows[code]
            r["target"].append(w)
            r["actual"].append(actual)
            price = (p or {}).get("price") or quotes.get((d, code))
            lot = LOT * price / equity if price else None
            if lot:
                r["lot_share"].append(lot)
            gap = abs(actual - w) / w > tolerance if w > 0 else actual > 0
            near = lot and abs(actual - w) <= lot
            if gap:
                r["over"] += 1
                if near:
                    r["within_lot"] += 1
            if d in by_date:                                 # a rebalance day: the target was just restated
                r["plan_days"] += 1
                r["plan_over"] += bool(gap and not near)
    if not rows:
        return None
    names = []
    for code, r in rows.items():
        n = len(r["target"])
        names.append({"code": code, "days": n, "target": sum(r["target"]) / n, "actual": sum(r["actual"]) / n,
                      "over_days": r["over"], "within_lot_days": r["within_lot"],
                      "plan_days": r["plan_days"], "plan_over_days": r["plan_over"],
                      "lot_share": sum(r["lot_share"]) / len(r["lot_share"]) if r["lot_share"] else None})
    names.sort(key=lambda x: -x["target"])
    worst = max(names, key=lambda x: abs(x["actual"] - x["target"]))
    cash = [s["cash"] / s["equity"] for s in snaps.values() if s.get("equity")]
    out = {"id": "allocation", "method_version": "unresolved_gap_v2", "names": names, "tolerance": tolerance, "worst": worst,
           "mean_cash_share": sum(cash) / len(cash) if cash else None,
           "scope": "按收盘持仓与最近一次目标权重比较；是否偏离以调仓日（有目标记录的日子）收盘为准，调仓之间的漂移只列出不判断；"
                    "一手以内的偏差是交易单位所限，不算策略错误"}
    # The largest target matters most; report it first, then any name with many gap days.
    lead = max(names, key=lambda x: x["target"])
    # Targets restated on most days: every close is a test (5% of days beyond tolerance). Sparse targets (a
    # monthly rebalance): drift in between is expected, so only rebalance-day closes beyond one lot count.
    sparse = len(plan_days) < .5 * len(days)
    if sparse:
        flagged = [x for x in names if x["plan_over_days"] >= max(2, .1 * x["plan_days"])]
    else:
        flagged = [x for x in names if x["over_days"] - x["within_lot_days"] > 0
                   and x["over_days"] - x["within_lot_days"] >= .05 * x["days"]]
    out["sparse_targets"] = sparse
    big = bool(flagged)
    def line(x):
        lot = f"一手约占权益 {x['lot_share']:.0%}" if x.get("lot_share") else ""
        return (f"{x['code']} 目标 {x['target']:.0%}，实际日均 {x['actual']:.1%}；{x['over_days']} 个收盘日偏离超过目标的 {tolerance:.0%}"
                + (f"，其中 {x['within_lot_days']} 天缺口不超过一手（{lot}，账户规模下凑不齐整手）" if x["over_days"] else "")
                + (f"；{x['plan_days']} 个调仓日收盘里 {x['plan_over_days']} 天仍偏离超过一手" if sparse and x["plan_days"] else ""))
    out["status"] = "gap" if big else "close"
    # A measured weight gap does not identify its cause. Link only same-day,
    # same-security orders on the side needed to close a beyond-lot gap.
    # Never infer real liquidity from a simulator's rejection message.
    blocked = []
    for o in bundle.get("orders") or []:
        d, code = o.get("date"), o.get("code")
        if d not in by_date or code not in by_date[d] or not snaps.get(d, {}).get("equity"):
            continue
        w = by_date[d][code]
        p = held.get(d, {}).get(code) or {}
        equity = snaps[d]["equity"]
        actual = p.get("market_value", 0) / equity
        price = p.get("price") or quotes.get((d, code))
        if (abs(actual - w) <= tolerance * w or
                (price and abs(actual - w) <= LOT * price / equity)):
            continue
        side = "buy" if actual < w else "sell"
        if o.get("side") != side or o.get("filled", 0) >= o.get("quantity", 0):
            continue
        blocked.append({"date": d, "code": code, "side": side,
                        "quantity": o["quantity"], "filled": o.get("filled", 0),
                        "order_id": o.get("order_id") or "", "time": o.get("time") or "",
                        "message": o.get("message") or "",
                        "reason": o.get("reason") or "未说明", "ref": o.get("ref") or {}})
    out["diagnosis"] = {
        "causal_status": "unresolved", "blocked_orders": blocked,
        "candidate_causes": ["目标或分批调仓约定", "策略下单数量与账户状态", "执行代理的时点、量限或单位", "当时市场的可交易约束"],
        "next_test": "固定策略、数据与费用，仅改变撮合时点或量限重跑，并与委托拒单记录逐项核对；不按收益高低选择执行模型",
    }
    out["scope"] += "；偏离只证明目标未兑现，不识别策略、执行代理或市场哪一项造成了缺口"
    out["verdict"] = ("；".join(line(x) for x in ([lead] + [x for x in flagged if x is not lead])[:3]) if big else
                      ("各标的在调仓日收盘都回到了目标附近（偏离不超过一手或目标的 "
                       f"{tolerance:.0%}）；调仓之间的漂移不计" if sparse else
                       f"扣除一手以内偏差后，各标的收盘权重偏离目标超过 {tolerance:.0%} 的天数都不到 5%"))
    out["worst"] = lead
    return out
