"""Fills at a price-limit lock: could the backtest really have traded there?

When a name trades the whole day at its upper limit (high == low == the limit), a buy order queues behind a
wall of bids and most often does not fill; at a lower-limit lock a sell rarely fills. Backtest engines fill
such orders anyway unless told otherwise. This check needs the day's limit prices (`limits.csv`:
date, code, limit_up, limit_down — exported by the TemperQ supplement script) and the day's high and low.
Without both, nothing is judged.

Only one-price days at the limit count as locked. A day that touched the limit and opened again is not a lock,
and a fill there is ordinary. A locked-day fill is reported as "possibly unobtainable", never as wrong:
a real order placed early enough can still fill, and the account's own size matters.
"""
from __future__ import annotations

from collections import defaultdict

TOL = 1e-6


def analyze(bundle, accounting, thresholds=None):
    limits = {(r["date"], r["code"]): r for r in bundle.get("limits") or []}
    if not limits or accounting.get("status") != "verified":
        return None
    span = {(r["date"], r["code"]): (r["low"], r["high"]) for r in bundle.get("day_ranges") or []}
    checked, locked = 0, []
    pnl = defaultdict(float)
    for c in accounting.get("contributions") or []:
        pnl[(c["date"], c["code"])] += c["net_pnl"]
    for f in bundle.get("fills") or []:
        key = (f["date"], f["code"])
        lim, rng = limits.get(key), span.get(key)
        if not lim or not rng:
            continue
        checked += 1
        low, high = rng
        if abs(high - low) > TOL:
            continue                                         # the day traded at more than one price: not locked
        if f["side"] == "buy" and lim.get("limit_up") and abs(high - lim["limit_up"]) <= TOL:
            locked.append({"date": f["date"], "code": f["code"], "side": "buy", "price": f["price"], "limit": lim["limit_up"],
                           "quantity": f["quantity"]})
        elif f["side"] == "sell" and lim.get("limit_down") and abs(low - lim["limit_down"]) <= TOL:
            locked.append({"date": f["date"], "code": f["code"], "side": "sell", "price": f["price"],
                           "limit": lim["limit_down"], "quantity": f["quantity"]})
    if not checked:
        return None
    buys = sum(1 for x in locked if x["side"] == "buy")
    out = {"id": "limits", "checked": checked, "locked": len(locked), "locked_buys": buys,
           "locked_sells": len(locked) - buys, "examples": locked[:5], "events": locked,
           "status": "flagged" if locked else "normal",
           "scope": "只认全天一个价格且等于涨跌停价的封板日；打开过的涨跌停不算。封板日成交是可能拿不到，不是一定拿不到"}
    out["verdict"] = (f"{checked} 笔有当天涨跌停价和全天区间的成交里，{len(locked)} 笔发生在封板日（{buys} 笔涨停封板时买入、"
                      f"{len(locked) - buys} 笔跌停封板时卖出）：真实交易中这类委托通常排不上队，回测按成交处理可能高估了收益或低估了亏损"
                      if locked else f"{checked} 笔有当天涨跌停价和全天区间的成交，没有发生在封板日的")
    return out
