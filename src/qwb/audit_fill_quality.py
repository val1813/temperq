"""Was the fill price realistic? Three checks on where and how much each fill traded.

1. Position in the day's range. Each fill price is placed in that day's high-low range (0 = low,
   1 = high). A strategy that buys near the low and sells near the high far more often than chance
   is the footprint of a signal that saw the day's path, or of an engine that filled at an unreachable
   price. Compared with the uniform distribution: mean position and the share in the best tenth.
2. Worst price soon after. Each buy is restated at the highest high, each sell at the lowest low,
   of the minutes up to WINDOW minutes after the fill. Quantities and fees fixed. This is a
    per-fill stress scenario, not a replay or an estimate of unavoidable execution loss.
3. Share of the fill's own minute volume (already in fill_timing; repeated here as the capacity side).

None of this proves an order could have filled: queue position and depth need tick or L2 data.
"""
from __future__ import annotations

import math

WINDOW = 10          # minutes after the fill for the worst-price floor
BEST_TENTH = .1


def _minutes_after(hhmm, n, own=True):
    """The fill's minute (if own) and the n minutes after it, skipping lunch and stopping at the close."""
    h, m = int(hhmm[:2]), int(hhmm[3:5])
    out = []
    for k in range(0 if own else 1, n + 1):
        t = h * 60 + m + k
        if 11 * 60 + 30 < t < 13 * 60 + 1:      # lunch break: jump to the afternoon session
            t += 90
        if t > 15 * 60:
            break
        out.append(f"{t // 60:02d}:{t % 60:02d}")
    return out


def analyze(bundle, accounting, thresholds=None):
    from .audit_timing import hhmm
    fills = [f for f in bundle.get("fills") or [] if f.get("quantity") and f.get("price")]
    if not fills:
        return None
    t = thresholds or {}
    best_bar = t.get("T-FILL-BEST-SHARE", .25)
    ranges = {(r["date"], r["code"]): r for r in bundle.get("day_ranges") or []}
    net = sum(d["net_pnl"] for d in accounting.get("daily") or []) if accounting.get("status") == "verified" else None
    out = {"id": "fill_quality", "fills": len(fills), "window": WINDOW,
           "scope": "只看成交价相对当天价格区间和成交后几分钟的位置；排队位置和盘口深度需要逐笔数据，本项不审",
           "thresholds": {"best_share": best_bar}}

    # 1. position in the day's range
    from . import audit_slippage
    slippage = audit_slippage.declared(bundle)
    placed = []
    for f in fills:
        r = ranges.get((f["date"], f["code"]))
        if not r or r["high"] <= r["low"]:
            continue
        side_bp = audit_slippage.per_side_bp(slippage, f["code"], f["price"]) or 0.
        slip = f["price"] * side_bp / 1e4 + 1e-6
        pos = (f["price"] - r["low"]) / (r["high"] - r["low"])
        beyond = f["price"] < r["low"] - slip or f["price"] > r["high"] + slip
        good = 1 - pos if f["side"] == "buy" else pos          # 1 = best possible price of the day
        placed.append({"side": f["side"], "pos": min(max(pos, 0.), 1.), "good": min(max(good, 0.), 1.),
                       "outside": beyond})
    if placed:
        n = len(placed)
        good_mean = sum(p["good"] for p in placed) / n
        best_share = sum(p["good"] >= 1 - BEST_TENTH for p in placed) / n
        # Under no skill, "good" is uniform on [0, 1]: mean 0.5 with sd 1/sqrt(12n); best tenth binomial(n, 0.1).
        z_mean = (good_mean - .5) / (1 / math.sqrt(12 * n))
        z_best = (best_share - BEST_TENTH) / math.sqrt(BEST_TENTH * (1 - BEST_TENTH) / n)
        sides = {}
        for side in ("buy", "sell"):
            g = [p for p in placed if p["side"] == side]
            if g:
                sides[side] = {"fills": len(g), "mean_position": sum(p["pos"] for p in g) / len(g),
                               "best_tenth_share": sum(p["good"] >= 1 - BEST_TENTH for p in g) / len(g)}
        suspicious = (best_share >= best_bar and z_best >= 3) or z_mean >= 3
        out["range"] = {"fills": n, "coverage": n / len(fills), "good_mean": good_mean, "best_share": best_share,
                        "z_mean": z_mean, "z_best": z_best, "sides": sides, "outside": sum(p["outside"] for p in placed),
                        "status": "suspicious" if suspicious else "normal",
                        "verdict": (f"{best_share:.0%} 的成交落在当天最有利的一成价格以内（随机约 10%），成交价偏向当天最好的一侧"
                                    f"明显超过随机：疑似信号用了当天走势，或回测按不可能拿到的价格成交" if suspicious else
                                    f"成交价在当天区间的位置接近随机（落在最有利一成以内的占 {best_share:.0%}，随机约 10%），"
                                    f"没有专挑当天低点买、高点卖的迹象")}
        if out["range"]["outside"]:
            out["range"]["verdict"] += (f"；另有 {out['range']['outside']} 笔成交价超出当天最高最低价加声明滑点的范围，"
                                        "需核对价格口径（复权）或撮合方式")

    # 2. independent per-fill extreme-price stress, on the SAME P&L intervals.
    # A sparse window is not a complete ten-minute window. Preserve exclusions
    # for independent recomputation instead of treating one observed bar as ten.
    worse, value, used, truncated = 0., 0., 0, 0
    observations, stress_rows = [], []
    accounted_dates = {r["date"] for r in accounting.get("daily") or [] if r.get("date")}
    eligible = [f for f in fills if not accounted_dates or f["date"] in accounted_dates]
    minute_rows = {}
    duplicates = set()
    for m in bundle.get("minute_prices") or []:
        key = (m["date"], m["code"], m["time"])
        if key in minute_rows:
            duplicates.add(key)
        minute_rows[key] = m
    for f in fills:
        row = {"date": f["date"], "time": f.get("time"), "code": f["code"], "side": f["side"],
               "quantity": f["quantity"], "original_price": f["price"], "required_minutes": WINDOW,
               "observed_minutes": 0, "status": "excluded", "reason": "", "fill_ref": f.get("ref")}
        observations.append(row)
        if accounted_dates and f["date"] not in accounted_dates:
            row["reason"] = "outside_accounted_intervals"
            continue
        clock = hhmm(f.get("time"))
        if not clock:
            row["reason"] = "missing_fill_clock"
            continue
        # Bars are labelled by their end, so the fill's own bar may have traded its high or low before the fill;
        # only the bars after it count. A fill too near the close has no full window and is left out.
        after = _minutes_after(clock, WINDOW, own=False)
        if len(after) < WINDOW:
            truncated += 1
            row["reason"] = "window_past_close"
            continue
        keys = [(f["date"], f["code"], m) for m in after]
        bars = [minute_rows.get(key) for key in keys]
        valid = lambda b: b and all(isinstance(b.get(k), (int, float)) and math.isfinite(b[k]) and b[k] > 0
                                   for k in ("high", "low")) and b["high"] >= b["low"]
        row["observed_minutes"] = sum(bool(valid(b)) for b in bars)
        if any(key in duplicates for key in keys):
            row["reason"] = "duplicate_minute_rows"
            continue
        if row["observed_minutes"] != WINDOW:
            row["reason"] = "incomplete_or_invalid_window"
            continue
        if f["side"] == "buy":
            worst = max(b["high"] for b in bars)
            delta = (f["price"] - worst) * f["quantity"]       # negative: paying more
        else:
            worst = min(b["low"] for b in bars)
            delta = (worst - f["price"]) * f["quantity"]       # negative: receiving less
        row.update(status="included", reason="", stress_price=worst, net_pnl=delta,
                   minute_refs=[b.get("ref") for b in bars])
        stress_rows.append(dict(row))
        worse += delta
        value += f["quantity"] * f["price"]
        used += 1
    if used or observations:
        total = sum(f["quantity"] * f["price"] for f in eligible)
        coverage = value / total if total else 0.
        floor = {"fills": used, "eligible_fills": len(eligible), "coverage": coverage,
                 "covered_notional": value, "eligible_notional": total,
                 "change": worse if used else None, "change_bp": worse / value * 1e4 if value else None,
                 "net_pnl": net, "net_after": net + worse if net is not None and used else None,
                 "rows": stress_rows, "observations": observations,
                 "change_to_abs_net_pnl": worse / abs(net) if net else None,
                 "sign_convention": "stress_net_pnl_minus_reported_net_pnl; negative means less P&L",
                 "scope": "逐笔独立取后10分钟极端价、数量与费用固定；不重放订单、资金、排队、成交量或后续信号，未覆盖成交沿用原价。可超过原利润，不能当作实盘损失或回测必然多赚。",
                 "truncated": truncated, "window": WINDOW}
        exclusions = {}
        for r in observations:
            if r["reason"]:
                exclusions[r["reason"]] = exclusions.get(r["reason"], 0) + 1
        floor["exclusions"] = exclusions
        if not used:
            floor["verdict"] = "没有位于核账区间且具有完整后10分钟高低价窗口的成交；不计算价格压力金额"
        elif net is None:
            floor["verdict"] = f"按成交后 {WINDOW} 分钟内最差价重算，盈亏变化 {worse:,.0f} 元（账目未闭合，不和净盈亏比较）"
        elif coverage < .9:
            floor["verdict"] = (f"核账区间只有 {coverage:.0%} 的成交额有成交后完整 {WINDOW} 分钟高低价，已覆盖部分盈亏变化 {worse:+,.2f} 元"
                                + (f"（{truncated} 笔离收盘不足 {WINDOW} 分钟）" if truncated else "") + "，最差价下限不完整，不下结论")
        elif net + worse <= 0 < net:
            floor["verdict"] = (f"按成交后 {WINDOW} 分钟内最差价重算，盈亏少 {-worse:,.0f} 元，净盈亏从 {net:,.0f} 元变为 {net + worse:,.0f} 元："
                                "收益依赖较好的成交价；这是每笔都取最差价的压力情景，不能单独证明实盘成交不了")
        else:
            floor["verdict"] = (f"按成交后 {WINDOW} 分钟内逐笔极端价重算，盈亏变化 {worse:+,.2f} 元，仍为 {net + worse:,.2f} 元")
        floor["verdict"] += "。负数表示压力情景比原回测少赚/多亏；逐笔独立取极端价，可超过原净利润，不代表实盘必然损失"
        out["floor"] = floor

    if "range" not in out and not stress_rows:
        return {**out, "status": "needs_prices",
                "verdict": "没有成交日的全天最高最低价或成交后分钟价，无法检验成交价是否现实",
                "missing": ["成交日全天区间（date, code, high, low）", "成交日分钟价含 high、low"]}
    floor = out.get("floor") or {}
    out["status"] = ("suspicious" if (out.get("range") or {}).get("status") == "suspicious"
                     or floor.get("coverage", 0) >= .9 and floor.get("net_after") is not None and floor["net_after"] <= 0 < (net or 0)
                     else "normal")
    return out
