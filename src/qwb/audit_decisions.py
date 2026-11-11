"""Did the trades follow the strategy's own decision record? Three checks on order and timing.

1. Buys against the stated selection. With daily scores that mark what the strategy selected, a buy of a
   candidate the record says was not selected that day contradicts the strategy's own decision. A buy placed
   before that day's signal existed (its time earlier than the scores' available_at) is not counted: deciding
   at one close and trading at the next open legitimately uses the previous selection. When most counted buys
   match the previous day's selection instead, the order code read a stale signal.
2. Buy before sell. On a day that sold one name and bought another, the buy was submitted first (by order id,
   which platforms assign in submission order). The buy could then use only the cash held before the sale.
   The close's cash share on those days says how much the order cost.
3. T+0 instrument never sold the same day. For a fund that may be resold the day it is bought, every buy held
   overnight and sold the next morning says the code treated it as T+1. Whether overnight holding was intended
   needs the strategy's rule; the pattern is reported, not judged.
"""
from __future__ import annotations

from collections import defaultdict

from .securities import same_day_resale


def _clock(text):
    text = str(text or "").strip()
    if len(text) >= 16 and text[10] in " T":
        text = text[11:]
    return text[:8] if len(text) >= 5 and text[2] == ":" else ""


def _oid(o):
    try:
        return int(str(o.get("order_id") or "").strip())
    except ValueError:
        return None


def against_selection(bundle, least=3):
    stated, chosen, ready = defaultdict(set), defaultdict(set), {}
    for s in bundle.get("scores") or []:
        if s.get("selected") is None:
            continue
        stated[s["date"]].add(s["code"])
        if s["selected"]:
            chosen[s["date"]].add(s["code"])
        at = _clock(s.get("available_at"))
        if at:
            ready[s["date"]] = min(ready.get(s["date"], at), at)
    if not stated:
        return None
    days = sorted(stated)
    previous = dict(zip(days[1:], days))
    checked, against, stale, untimed = 0, [], 0, 0
    for f in bundle.get("fills") or []:
        d, code = f["date"], f["code"]
        if f["side"] != "buy" or code not in stated.get(d, ()):
            continue
        at, t = ready.get(d), _clock(f.get("time"))
        if at and t and t < at:
            continue                                         # placed before today's signal existed
        checked += 1
        if not (at and t):
            untimed += 1
        if code not in chosen[d]:
            against.append({"date": d, "code": code, "time": t})
            stale += code in chosen.get(previous.get(d), ())
    if not checked:
        return None
    out = {"id": "against_selection", "checked": checked, "against": len(against), "stale": stale,
           "untimed": untimed, "examples": against[:5], "events": against,
           "status": "flagged" if len(against) >= least and len(against) >= .1 * checked else "normal"}
    if against:
        out["verdict"] = (f"{checked} 笔可对照当天选择的买入里，{len(against)} 笔买的是策略记录里当天没选中的标的"
                          + (f"；其中 {stale} 笔正好是前一天选中的：下单可能读了过期的信号" if stale >= .8 * len(against) else ""))
    else:
        out["verdict"] = f"{checked} 笔可对照当天选择的买入，都是策略当天选中的标的"
    return out


IDLE_SHARE = .1          # close cash share that says a sale's proceeds sat unused
NEAR_MINUTES = 5         # a buy and a sell this close in time belong to one switch


def _minutes(o):
    t = _clock(o.get("time"))
    return int(t[:2]) * 60 + int(t[3:5]) if t else None


def _near(b, s):
    x, y = _minutes(b), _minutes(s)
    return x is None or y is None or abs(x - y) <= NEAR_MINUTES


def selected_not_ordered(bundle, least=3):
    """Days the record says a name was selected, it was not held, and no buy order for it was placed at all."""
    if any(s.get("rank") is not None for s in bundle.get("scores") or []):
        return None                                          # ranked scores: signal_mapping covers this
    chosen = defaultdict(set)
    for s in bundle.get("scores") or []:
        if s.get("selected"):
            chosen[s["date"]].add(s["code"])
    if not chosen:
        return None
    ordered = defaultdict(set)
    for o in bundle.get("orders") or []:
        if o["side"] == "buy":
            ordered[o["date"]].add(o["code"])
    for f in bundle.get("fills") or []:
        if f["side"] == "buy":
            ordered[f["date"]].add(f["code"])
    held = defaultdict(set)
    for p in bundle.get("positions") or []:
        if p.get("quantity"):
            held[p["date"]].add(p["code"])
    days = [s["date"] for s in bundle.get("snapshots") or []]
    prior = dict(zip(days[1:], days))
    # Selected, no buy that day, and not held at that close. Being held at the previous close is no excuse: an
    # intraday strategy sells that position in the morning and still has to act on the day's signal.
    rows = [{"date": d, "codes": sorted(c - ordered[d] - held[d])}
            for d, c in sorted(chosen.items()) if d in prior and c - ordered[d] - held[d]]
    out = {"id": "selected_not_ordered", "selected_days": len(chosen), "days": len(rows), "examples": rows[:5],
           "status": "flagged" if len(rows) >= least else "normal"}
    out["verdict"] = (f"{len(chosen)} 个策略记录有选中标的的交易日里，{len(rows)} 天选中的标的既没持有、也没有任何买单："
                      "信号被策略自己的某个条件挡掉了" if rows else f"{len(chosen)} 个有选中标的的交易日都下了买单或已持有")
    return out


def repeated_adds(bundle, least=5):
    """Buys of a name already fully held, while it stays the stated target, with no shortfall left to fill.

    A top-up after a partial fill or a refused buy is legitimate; buying again when the previous buys of that
    name all filled in full means the code adds the target quantity each day instead of trading to it."""
    chosen = defaultdict(set)
    for s in bundle.get("scores") or []:
        if s.get("selected"):
            chosen[s["date"]].add(s["code"])
    if not chosen:
        return None
    days = [s["date"] for s in bundle.get("snapshots") or []]
    prev = dict(zip(days[1:], days))
    held = defaultdict(dict)
    for p in bundle.get("positions") or []:
        if p.get("quantity"):
            held[p["date"]][p["code"]] = p["quantity"]
    short = defaultdict(set)                                 # (code) -> dates with a buy that came up short
    for o in bundle.get("orders") or []:
        if o["side"] == "buy" and o.get("filled", 0) < o.get("quantity", 0):
            short[o["code"]].add(o["date"])
    sold = defaultdict(set)                                  # a buy on a day the name is also sold is intraday trading
    for f in bundle.get("fills") or []:
        if f["side"] == "sell":
            sold[f["code"]].add(f["date"])
    for o in bundle.get("orders") or []:
        if o["side"] == "sell":
            sold[o["code"]].add(o["date"])
    rows, checked = [], 0
    for f in bundle.get("fills") or []:
        d, code = f["date"], f["code"]
        if f["side"] != "buy" or d not in prev or d in sold[code]:
            continue
        p = prev[d]
        if not (held[p].get(code) and code in chosen.get(p, ()) and code in chosen.get(d, ())):
            continue
        checked += 1
        if not any(x <= d for x in short[code] if x >= _last_entry(code, d, held, days)):
            rows.append({"date": d, "code": code, "quantity": f["quantity"], "held_before": held[p][code]})
    if not checked:
        return None
    out = {"id": "repeated_adds", "checked": checked, "days": len(rows), "examples": rows[:5],
           "status": "flagged" if len(rows) >= least else "normal"}
    out["verdict"] = (f"{len(rows)} 次在已经持有、目标没变、之前的买单也都成交完的情况下又买入同一只：代码可能把目标数量当成每天的增量来下单"
                      if rows else "持有目标期间的追加买入，都发生在之前有买单没买够之后")
    return out


def _last_entry(code, d, held, days):
    """The date this holding of `code` began (first close of the current run held), for the top-up test."""
    i = days.index(d) if d in days else len(days)
    start = d
    for x in reversed(days[:i]):
        if not held[x].get(code):
            break
        start = x
    return start


def buy_before_sell(bundle, least=3, idle_share=IDLE_SHARE):
    by_day = defaultdict(list)
    for o in bundle.get("orders") or []:
        if _oid(o) is not None:
            by_day[o["date"]].append(o)
    if not by_day:
        return None
    snaps = {s["date"]: s for s in bundle.get("snapshots") or []}
    switch_days, hits = 0, []
    for d, orders in by_day.items():
        orders.sort(key=_oid)
        sells = [o for o in orders if o["side"] == "sell" and o.get("filled", 0) > 0]
        buys = [o for o in orders if o["side"] == "buy"]
        if not sells or not buys:
            continue
        pairs = [(b, s) for b in buys for s in sells if b["code"] != s["code"] and _near(b, s)]
        if not pairs:
            continue
        switch_days += 1
        early = [(b, s) for b, s in pairs if _oid(b) < _oid(s)]
        if early:
            snap = snaps.get(d) or {}
            share = snap["cash"] / snap["equity"] if snap.get("equity") else None
            # It matters only if the buy came up short, or the sale's cash was left idle at the close.
            short = any(b.get("filled", 0) < b["quantity"] for b, _ in early)
            idle = share is not None and share >= idle_share
            hits.append({"date": d, "buy": early[0][0]["code"], "sell": early[0][1]["code"], "close_cash_share": share,
                         "costly": short or idle})
    if not switch_days:
        return None
    shares = [h["close_cash_share"] for h in hits if h["close_cash_share"] is not None]
    costly = [h for h in hits if h["costly"]]
    out = {"id": "buy_before_sell", "switch_days": switch_days, "days": len(hits), "costly_days": len(costly),
           "examples": (costly or hits)[:5], "mean_close_cash_share": sum(shares) / len(shares) if shares else None,
           "status": "flagged" if len(costly) >= least and len(costly) >= .5 * len(hits) else "normal"}
    if hits:
        cash = out["mean_close_cash_share"]
        out["verdict"] = (f"{switch_days} 个同时卖出一只、买入另一只的交易日里，{len(hits)} 天买单先于卖单提交，"
                          f"其中 {len(costly)} 天买单没买够或收盘还剩 {idle_share:.0%} 以上现金：买入只能用卖出前的现金"
                          + (f"，这些天收盘平均 {cash:.0%} 的权益是现金" if cash is not None else ""))
    else:
        out["verdict"] = f"{switch_days} 个换仓日里，卖单都先于买单提交"
    return out


def t0_held_overnight(bundle, least=20):
    days = [s["date"] for s in bundle.get("snapshots") or []]
    nxt = dict(zip(days, days[1:]))
    fills = bundle.get("fills") or []
    sells = defaultdict(list)
    for f in fills:
        if f["side"] == "sell":
            sells[f["code"]].append(f)
    rows = []
    for code in sorted({f["code"] for f in fills if f["side"] == "buy"}):
        if same_day_resale(code) is not True:
            continue
        buys = [f for f in fills if f["side"] == "buy" and f["code"] == code]
        if len(buys) < least:
            continue
        same = sum(any(s["date"] == b["date"] and _clock(s.get("time")) > _clock(b.get("time")) for s in sells[code])
                   for b in buys)
        morning = sum(any(s["date"] == nxt.get(b["date"]) and _clock(s.get("time"))[:5] <= "10:00" for s in sells[code])
                      for b in buys)
        rows.append({"code": code, "buys": len(buys), "same_day": same, "next_morning": morning})
    if not rows:
        return None
    hit = [r for r in rows if r["same_day"] == 0 and r["next_morning"] >= .8 * r["buys"]]
    out = {"id": "t0_overnight", "names": rows, "status": "flagged" if hit else "normal"}
    if hit:
        r = hit[0]
        out["verdict"] = (f"{r['code']} 可以当天买当天卖（T+0），但 {r['buys']} 次买入没有一次当天卖出，"
                          f"{r['next_morning']} 次都在次日开盘后卖出：代码可能按 T+1 处理了这只基金")
    else:
        out["verdict"] = "T+0 品种的买入有当天卖出的，没有发现按 T+1 处理的迹象"
    return out


def _rank(v):
    order = sorted(range(len(v)), key=lambda i: v[i])
    r = [0.] * len(v)
    for k, i in enumerate(order):
        r[i] = float(k)
    return r


def _ic(a, b):
    ra, rb = _rank(a), _rank(b)
    n = len(ra)
    ma, mb = sum(ra) / n, sum(rb) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** .5
    return num / den if den else None


def _t(v):
    v = [x for x in v if x is not None]
    if len(v) < 20:
        return None
    m = sum(v) / len(v)
    sd = (sum((x - m) ** 2 for x in v) / (len(v) - 1)) ** .5
    if not sd:
        return float("inf") if m > 0 else float("-inf") if m < 0 else None   # identical IC every period
    return m / sd * len(v) ** .5


LOOKAHEAD_T = 4.


def lookahead_hint(bundle, t_bar=LOOKAHEAD_T):
    """Scores far more predictive of the next bar than any real signal, and of nothing after it."""
    by_day = defaultdict(dict)
    for s in bundle.get("scores") or []:
        if s.get("score") is not None:
            by_day[s["date"]][s["code"]] = s["score"]
    px = defaultdict(dict)
    for p in bundle.get("prices") or []:
        px[p["date"]][p["code"]] = p["close"]
    days = sorted(d for d in by_day if d in px)
    one, two = [], []
    for a, b, c in zip(days, days[1:], days[2:]):
        codes = [k for k in by_day[a] if all(k in px[x] and px[x][k] > 0 for x in (a, b, c))]
        if len(codes) < 3:
            continue
        score = [by_day[a][k] for k in codes]
        one.append(_ic(score, [px[b][k] / px[a][k] - 1 for k in codes]))
        two.append(_ic(score, [px[c][k] / px[b][k] - 1 for k in codes]))
    t1, t2 = _t(one), _t(two)
    if t1 is None:
        return None
    hit = t1 >= t_bar and (t2 is None or t2 < t1 / 3)
    out = {"id": "lookahead", "t_next": t1, "t_after": t2, "periods": len(one), "status": "flagged" if hit else "normal"}
    out["verdict"] = (f"打分对下一期收益的 IC t 值高达 {t1:.1f}，对再下一期只有 {(t2 or 0):.1f}：预测力全部集中在紧接着的一根 K 线上，"
                      "常见于打分用到了下一期的价格（未来数据）；也可能是真实的超短期信号，需要核对打分代码取数的日期"
                      if hit else f"打分对下一期收益的 IC t 值 {t1:.1f}，没有异常集中")
    return out


def no_exit_attempt(bundle, least=3):
    """An intraday pattern (most buys sold the same day) with closes still holding and no sell placed that day."""
    fills = bundle.get("fills") or []
    buys = [f for f in fills if f["side"] == "buy"]
    if len(buys) < 20:
        return None
    sold = defaultdict(set)
    for f in fills:
        if f["side"] == "sell":
            sold[f["code"]].add(f["date"])
    same = sum(1 for f in buys if f["date"] in sold[f["code"]])
    if same < .5 * len(buys):
        return None                                          # not an intraday pattern
    tried = defaultdict(set)
    for o in bundle.get("orders") or []:
        if o["side"] == "sell":
            tried[o["code"]].add(o["date"])
    held = defaultdict(set)
    for p in bundle.get("positions") or []:
        if p.get("quantity"):
            held[p["date"]].add(p["code"])
    rows = [{"date": d, "code": c} for d in sorted(held) for c in sorted(held[d])
            if d not in tried[c] and d not in sold[c]]
    nights = sum(len(v) for v in held.values())
    out = {"id": "no_exit_attempt", "buys": len(buys), "same_day": same, "nights": nights, "days": len(rows),
           "examples": rows[:5], "status": "flagged" if len(rows) >= least else "normal"}
    out["verdict"] = (f"{len(buys)} 次买入里 {same} 次当天卖出，是日内做法；但 {nights} 个收盘仍持仓的日子里，"
                      f"{len(rows)} 天当天根本没有下卖单：平仓条件在这些天没有触发" if rows else
                      f"日内做法下，收盘仍持仓的 {nights} 天都下过卖单")
    return out


def analyze(bundle, accounting, thresholds=None):
    if accounting.get("status") != "verified":
        return None
    parts = {"against_selection": against_selection(bundle), "selected_not_ordered": selected_not_ordered(bundle),
             "buy_before_sell": buy_before_sell(bundle, idle_share=float((thresholds or {}).get("T-IDLE-AFTER-SALE", IDLE_SHARE))),
             "t0_overnight": t0_held_overnight(bundle), "no_exit_attempt": no_exit_attempt(bundle),
             "repeated_adds": repeated_adds(bundle),
             "lookahead": lookahead_hint(bundle, t_bar=float((thresholds or {}).get("T-LOOKAHEAD-T", LOOKAHEAD_T)))}
    if not any(parts.values()):
        return None
    return {"id": "decisions", **parts,
            "status": "flagged" if any((p or {}).get("status") == "flagged" for p in parts.values()) else "normal",
            "scope": "只用委托编号、成交时间和策略自记的每日选择核对；不推断策略的意图"}
