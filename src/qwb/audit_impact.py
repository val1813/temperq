"""Money attached to findings, never a forecast of a corrected strategy.

Each amount retains its rows, convention and exclusions. Overlapping findings
must not be summed. Missing coverage is not a zero. No uploaded code is run.
"""
from __future__ import annotations

import math
from collections import defaultdict


def _finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def money(kind, rows, scope, *, total=None, covered=None, excluded=None):
    excluded = excluded or []
    amount = sum(r["net_pnl"] for r in rows) if rows else (0. if total == 0 else None)
    covered = len(rows) if covered is None else covered
    total = covered + len(excluded) if total is None else total
    return {"kind": kind, "amount": amount, "currency": "CNY", "scope": scope,
            "direction": None if amount is None else "profit" if amount > 0 else "loss" if amount < 0 else "flat",
            "coverage": {"covered": covered, "total": total, "complete": covered == total and not excluded},
            "rows": rows, "excluded": excluded,
            "note": "金额可能与其他卡重叠，不能相加；不等于修复后可增加的收益。"}


def _lot_impacts(bundle, modules):
    """Reuse existing FIFO lots; match source refs, never all inventory of a flagged code."""
    holding = modules.get("holding_analysis") or {}
    lots = holding.get("closed", []) + holding.get("open", [])
    fills = bundle.get("fills") or []
    events = (modules.get("limits") or {}).get("events") or []
    listings = {r["code"]: r for r in bundle.get("listings") or []}
    out = {}
    for identity in ("limits", "listings"):
        if identity == "limits" and (modules.get("limits") or {}).get("locked") and "events" not in modules["limits"]:
            out[identity] = money("historical", [], "旧审计仅存示例，需重新审计生成全部封板成交明细。",
                                  total=modules["limits"]["locked"], covered=0)
            continue
        selected = []
        for f in fills:
            if identity == "limits":
                hit = any(all(e.get(k) == f.get(k) for k in ("date", "code", "side", "quantity", "price")) for e in events)
            else:
                r = listings.get(f["code"], {})
                hit = bool((r.get("listed_date") and f["date"] < r["listed_date"]) or
                           (r.get("delisted_date") and f["date"] > r["delisted_date"]))
            if hit:
                selected.append(f)
        # A lot touched at both ends is included once. FIFO quantities/fees are
        # already allocated in holding_analysis; opening/action exclusions stay excluded.
        rows, covered = [], set()
        for lot in lots:
            hits = [i for i, f in enumerate(selected) if f.get("ref") and
                    (lot.get("entry_ref") == f["ref"] or (lot.get("exit_date") and lot.get("exit_ref") == f["ref"]))]
            if hits:
                covered.update(hits)
                rows.append({**lot, "event_indices": hits})
        excluded = []
        for i, f in enumerate(selected):
            matched_qty = sum(r["quantity"] for r in rows if i in r["event_indices"])
            if matched_qty < f["quantity"]:
                covered.discard(i)
                excluded.append({"date": f["date"], "code": f["code"], "quantity": f["quantity"] - matched_qty,
                                 "reason": "FIFO未覆盖：期初批次未知、公司行动、估值或数量异常"})
        out[identity] = money("historical", rows,
            "关联成交所匹配FIFO批次的全持有期净盈亏（含按股数摊分的买卖费用；未平仓按期末估值）；"
            "不是封板后盈亏，也不是禁止这笔成交后的收益差。" if identity == "limits" else
            "上市前或退市后成交所匹配FIFO批次的全持有期净盈亏；仅打分入池、未交易的标的不产生此金额。",
            total=len(selected), covered=len(covered), excluded=excluded)
    return out


def _failed_exits(bundle, modules):
    """Tag unsold shares at failure close, then allocate later actual sells first.

    This explicit quantity convention excludes new buys and the failure day's
    pre-order P&L. Repeated orders cannot tag the same shares twice.
    """
    events = ((modules.get("activity") or {}).get("exits") or {}).get("events") or []
    days = [s["date"] for s in bundle.get("snapshots") or []]
    positions = {(p["date"], p["code"]): p for p in bundle.get("positions") or []}
    actions = {a["code"] for a in bundle.get("actions") or [] if a.get("cash") or a.get("quantity_change")}
    fills = defaultdict(list)
    for f in bundle.get("fills") or []:
        fills[(f["date"], f["code"])].append(f)
    by_code = defaultdict(list)
    for e in events:
        by_code[e["code"]].append(e)
    rows, excluded, covered = [], [], 0
    for code, own in by_code.items():
        if code in actions:
            excluded.extend({"date": e["date"], "code": code, "reason": "有公司行动，未分配失败卖单批次"} for e in own)
            continue
        active, prior_price = 0., None
        for day in days:
            p = positions.get((day, code), {})
            if active:
                before, gross, fee = active, 0., 0.
                for f in sorted(fills[(day, code)], key=lambda f: str(f.get("time") or "")):
                    if f["side"] != "sell" or not active:
                        continue
                    q = min(active, f["quantity"])
                    gross += q * (f["price"] - prior_price)
                    fee += f["fee"] * q / f["quantity"]
                    active -= q
                if active:
                    if not p.get("price") or p.get("quantity", 0) < active:
                        excluded.append({"date": day, "code": code, "reason": "残余股数或收盘估值不完整，终止该段"})
                        active = 0.
                        continue
                    gross += active * (p["price"] - prior_price)
                rows.append({"date": day, "code": code, "quantity_before": before, "quantity_after": active,
                             "gross_pnl": gross, "fees": fee, "net_pnl": gross - fee})
            todays = [e for e in own if e["date"] == day]
            if todays:
                if p.get("price"):
                    # Same-close repeat orders are alternative attempts, not extra exposure.
                    missing = max(e["ordered"] - e["filled"] for e in todays)
                    active = max(active, min(missing, p.get("quantity", 0)))
                    covered += len(todays)
                else:
                    excluded.extend({"date": day, "code": code, "reason": "缺少失败日收盘估值"} for _ in todays)
            prior_price = p.get("price") if active else None
    return money("historical", rows,
        "失败日收盘未卖出股数，自下一交易日起至实际卖出/期末的盯市盈亏；实际卖单优先分配给这些股数，"
        "计摊分卖出费用，排除新增买入、原买入费用和失败当日盈亏；同日多张委托取最大未成交数，不假设独立增量。",
        total=len(events), covered=covered, excluded=excluded)


def _missing_buys(bundle, modules):
    episodes = list((modules.get("partial_entry") or {}).get("episodes") or [])
    days = [s["date"] for s in bundle.get("snapshots") or []]
    orders, fills = bundle.get("orders") or [], bundle.get("fills") or []
    # Fully unfilled entries have no holding mark; only returned candidate
    # closes can price them. Stop at any subsequent order/fill or explicit
    # deselection, before a changed decision could extend the hypothetical bet.
    for o in orders:
        if o.get("side") != "buy" or o.get("filled", 0) != 0 or o.get("quantity", 0) <= 0:
            continue
        stops = {r["date"] for r in orders + fills if r["code"] == o["code"] and r["date"] > o["date"]}
        stops.update(s["date"] for s in bundle.get("scores") or [] if s["code"] == o["code"] and s.get("selected") is False and s["date"] > o["date"])
        stop = min(stops) if stops else None
        window = [d for d in days if d >= o["date"] and (stop is None or d < stop)]
        episodes.append({"date": o["date"], "end": window[-1] if window else o["date"], "code": o["code"],
                         "ordered": o["quantity"], "filled": 0})
    prices = {(p["date"], p["code"]): p.get("close") for p in bundle.get("prices") or []}
    prices.update({(p["date"], p["code"]): p.get("price") for p in bundle.get("positions") or [] if p.get("price")})
    actions = bundle.get("actions") or []
    rows, excluded, last = [], [], {}
    for e in sorted(episodes, key=lambda e: (e["date"], e["code"])):
        code, start, end = e["code"], e["date"], e["end"]
        a, b = prices.get((start, code)), prices.get((end, code))
        reason = None
        if start <= last.get(code, ""):
            reason = "与同标的另一未补买单观察窗重叠，未叠加假设股数"
        elif not a or not b or not all(_finite(p) and p > 0 for p in (a, b)):
            reason = "缺少起止日同口径收盘价"
        elif any(x["code"] == code and start < x["date"] <= end and (x.get("cash") or x.get("quantity_change")) for x in actions):
            reason = "观察窗有公司行动，缺少假设股数的总回报调整"
        if reason:
            excluded.append({"date": start, "code": code, "reason": reason})
            continue
        last[code] = end
        qty = e["ordered"] - e["filled"]
        rows.append({"date": start, "end": end, "code": code, "quantity": qty,
                     "start_price": a, "end_price": b, "net_pnl": qty * (b - a)})
    return money("counterfactual", rows,
        "未成交股数×（观察终点收盘价−下单日收盘价）；部分成交终点沿用未补单观察窗，零成交终点为下一委托/成交/明确落选之前或期末；"
        "正值表示少赚，负值表示少亏。未计费用、资金占用、可成交性及信号变化，不是实际盈亏或补单收益。",
        total=len(episodes), covered=len(rows), excluded=excluded)


def build(result, bundle=None):
    modules = result.get("modules") or {}
    account = modules.get("accounting") or {}
    if account.get("status") != "verified":
        return {}
    out = {}
    contributions = account.get("contributions") or []
    target = (modules.get("fulfilment") or {}).get("by_target") or {}
    if _finite(target.get("outside")):
        rows = [r for r in target.get("rows", []) if r["group"] == "outside"]
        out["off_target_pnl"] = money("historical", rows, "前一收盘已在声明目标外的持仓，当日逐标的净盈亏。")
        out["off_target_pnl"]["amount"] = target["outside"]  # older saved audits retain the exact aggregate
        out["off_target_pnl"]["direction"] = "profit" if target["outside"] > 0 else "loss" if target["outside"] < 0 else "flat"
        if "rows" not in target:
            out["off_target_pnl"]["coverage"]["complete"] = False
            out["off_target_pnl"]["note"] += "旧审计仅保留合计，重新审计可导出逐日明细。"
    state = modules.get("state") or {}
    if state.get("rows") is not None:
        keys = {(r["date"], c) for r in state["rows"] for c in set(r["orphan"]) | set(r["phantom"])}
        selected = [r for r in contributions if (r["date"], r["code"]) in keys]
        out["state"] = money("historical", selected,
            "状态不一致日，相关实际持仓的整日逐标的净盈亏；包括决策前盈亏，不声称由状态错误造成。虚构持仓无实际贡献。", total=len(selected))
    for identity, events, scope in (
        ("price_jump", (modules.get("prices") or {}).get("events"),
         "价格异常日相关证券在导出估值下的整日净盈亏；估值错误可能污染这个金额，不能解释为真实经济收益。"),
        ("decisions", ((modules.get("decisions") or {}).get("against_selection") or {}).get("events"),
         "买入违背当日选择的证券在当日的整日净盈亏；不涵盖本卡其他决策问题，也不等于错读信号造成的损失。")):
        if events is not None:
            keys = {(e["date"], e["code"]) for e in events}
            selected = [r for r in contributions if (r["date"], r["code"]) in keys]
            out[identity] = money("historical", selected, scope, total=len(selected))
    if bundle is not None:
        out.update(_lot_impacts(bundle, modules))
        out["failed_exits"] = _failed_exits(bundle, modules)
        out["partial_entry"] = _missing_buys(bundle, modules)
        out["cash_buffer"] = {"kind": "reference", "amount": None, "rows": [],
                              "reference_to": "partial_entry", "scope": "同未补买单价格情景，金额只列一次。"}
        traded = {f["date"] for f in bundle.get("fills") or []}
        held = {p["date"] for p in bundle.get("positions") or [] if p.get("quantity")}
        idle = {s["date"] for s in bundle.get("snapshots") or [] if s["date"] not in traded | held}
        selected = [r for r in account.get("daily", []) if r["date"] in idle]
        out["idle"] = money("historical", selected,
            "既无成交又无收盘持仓日的账户实际净盈亏；不能用零投资收益估计错过的机会。", total=len(selected))
    # Existing quantified costs are ceilings/scenarios; none is a guaranteed saving.
    fees, tick = modules.get("fee_analysis") or {}, modules.get("tick_analysis") or {}
    costs = {"minimum_commission": fees.get("minimum_premium"),
             "tick_floor": (tick.get("accounted_intervals") or {}).get("tick_scenario_cost"),
             "rank_churn": (modules.get("signal_mapping") or {}).get("avoided_cost")}
    for identity, amount in costs.items():
        if _finite(amount):
            out[identity] = money("cost_scenario", [{"net_pnl": amount}], "现有成交路径上的费用/价差情景或可避免成本上限；改变交易后需复跑核验。")
    from .audit_rules import defaults
    from .securities import asset_class
    thresholds = {**defaults(), **((result.get("rules") or {}).get("thresholds") or {})}
    closed = (modules.get("holding_analysis") or {}).get("closed")
    if closed is not None:
        for identity, selected in (
            ("holding_period", [r for r in closed if _finite(r.get("calendar_days")) and r["calendar_days"] <= thresholds["T-HOLD-SHORT"]]),
            ("low_price", [r for r in closed if _finite(r.get("entry_price")) and r["entry_price"] < thresholds["T-PRICE-LOW"] and asset_class(r["code"]) == "stock"])):
            out[identity] = money("cost_scenario", [{**r, "net_pnl": r["fees"] + r["tick_scenario_cost"]} for r in selected],
                "对应已平仓FIFO批次实际费用加价差情景；全部不交易时的成本暴露上限，不代表延长持有/剔除后能省下同额。", total=len(selected))
    reported, modeled = fees.get("reported_fee"), fees.get("modeled_fee")
    if _finite(reported) and _finite(modeled):
        out["fee_setting"] = money("cost_scenario", [{"net_pnl": reported - modeled}],
            "原成交保持不变，导出费用减审计费率情景费用；正数为原回测多扣，负数为少扣，不改变实际券商收费。")
    floor = (modules.get("fill_quality") or {}).get("floor") or {}
    if _finite(floor.get("change")):
        detail = floor.get("rows")
        excluded = [r for r in floor.get("observations", [])
                    if r.get("reason") and r["reason"] != "outside_accounted_intervals"]
        coverage = floor.get("coverage")
        coverage_text = (f"核账区间覆盖 {floor['fills']}/{floor['eligible_fills']} 笔、成交额 {coverage:.2%}；"
                         if _finite(coverage) and "eligible_fills" in floor else "旧记录覆盖明细不足；")
        out["fill_quality"] = money("cost_scenario", floor.get("rows") or [{"net_pnl": floor["change"]}],
            coverage_text + "后10分钟完整窗口逐笔取买入最高价/卖出最低价，数量和费用固定；负号表示情景净盈亏比原回测少这么多。"
            "各笔分别取极端价，可超过原净利润；这不是实盘损失、必然多赚或可节省金额。",
            covered=len(detail) if detail is not None else 0,
            total=floor.get("eligible_fills", len(detail or []) or 1), excluded=excluded)
        out["fill_quality"]["notional_coverage"] = coverage
        out["fill_quality"]["coverage"]["complete"] = detail is not None and floor.get("coverage", 0) >= 1 and not excluded
    return out


def attach(cards, result, bundle=None):
    impacts = (result.get("modules") or {}).get("monetary_impact")
    if impacts is None:
        impacts = build(result, bundle)
    titles = {c["id"]: c.get("title", c["id"]) for c in cards}
    for card in cards:
        impact = impacts.get(card["id"])
        # Older saved reports may contain the original duplicated object.
        if card["id"] == "cash_buffer" and "partial_entry" in impacts:
            primary = impacts["partial_entry"]
            if "partial_entry" in titles:
                card["impact"] = {"kind": "reference", "amount": None, "rows": [], "reference_to": "partial_entry",
                                  "reference_title": titles["partial_entry"], "scope": "同一未补买单价格情景，金额只列一次。"}
                card["saving"] = f"同『{titles['partial_entry']}』的金额，不重复列示。"
                card["net"] = "与未补买单共用同一份价格情景明细；不能另计现金缓冲的改善收益。"
                continue
            impact = primary  # no partial-entry card: cash buffer is the sole display owner
        if impact is None or impact["amount"] is None:
            card["impact"] = impact or {"kind": "unknown", "amount": None,
                "scope": "现有材料不能把此问题单独分配为金额；需对应成交/逐标的估值或原平台复跑。", "rows": []}
            continue
        card["impact"] = impact
        label = {"historical": "已发生的关联净盈亏", "counterfactual": "未买到股数的价格变动情景",
                 "cost_scenario": "成本情景/上限"}[impact["kind"]]
        partial = "（部分覆盖）" if not impact["coverage"]["complete"] else ""
        card["saving"] = f"{label}{partial}：¥{impact['amount']:+,.2f}；{impact['scope']}"
        card["net"] = "上述金额可按明细复算；修复后的净效果需保持区间、资金、费用一致回原平台复跑，再用 qwb audit compare 对比。"
    return cards
