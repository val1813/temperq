"""Are the price series possible? One-day moves that A-share price limits rule out.

Candidate closes (`prices.csv`, often adjusted) and closing holdings (unadjusted) are checked for a move from
one trading day to the next larger than any daily limit allows (default 21%; 31% for Beijing-exchange
stocks). On an adjusted series such a move usually means a broken adjustment chain or a share conversion
spliced in wrongly; on holdings with an unchanged share count and no recorded corporate action it means the
valuation price is wrong. Scores, premise tests and attribution that use these prices may be contaminated.
The first five observations of each series are skipped (new listings trade without limits).
"""
from __future__ import annotations

from collections import defaultdict

SKIP_FIRST = 5


def _limit(code, base):
    return .31 if code[:1] in ("8", "4") or code.startswith("92") else base


def _outside(change, limit, n):
    """Is a move over n trading days beyond what n limit moves in a row allow?"""
    return change > (1 + limit) ** n - 1 or change < (1 - limit) ** n - 1


def analyze(bundle, thresholds=None):
    base = (thresholds or {}).get("T-PRICE-JUMP", .21)
    jumps, checked = [], 0
    days = [s["date"] for s in bundle.get("snapshots") or []]
    calendar = sorted(set(days) | {p["date"] for p in bundle.get("prices") or []})
    index = {d: i for i, d in enumerate(calendar)}           # trading days between two observations
    series = defaultdict(list)
    for p in bundle.get("prices") or []:
        series[p["code"]].append((p["date"], p["close"]))
    signal = defaultdict(dict)                                # the price the strategy itself used, one per day
    for s in bundle.get("scores") or []:
        if s.get("signal_price"):
            signal[s["code"]].setdefault(s["date"], s["signal_price"])
    calendar = sorted(set(calendar) | {d for v in signal.values() for d in v})
    index = {d: i for i, d in enumerate(calendar)}
    for source, data in (("prices", series), ("signal", {c: sorted(v.items()) for c, v in signal.items()})):
        for code, rows in data.items():
            rows.sort()
            for (d0, c0), (d1, c1) in list(zip(rows, rows[1:]))[SKIP_FIRST:]:
                checked += 1
                n = max(1, index[d1] - index[d0]) + (source == "signal")   # intraday prices span part of one more day
                if c0 > 0 and c1 > 0 and _outside(c1 / c0 - 1, _limit(code, base), n):
                    jumps.append({"source": source, "code": code, "date": d1, "before": c0, "after": c1,
                                  "change": c1 / c0 - 1, "trading_days": n})
    acts = {(a["date"], a["code"]) for a in bundle.get("actions") or []}
    held = defaultdict(dict)
    for p in bundle.get("positions") or []:
        if p.get("quantity") and p.get("price"):
            held[p["date"]][p["code"]] = p
    for d0, d1 in zip(days, days[1:]):
        for code, a in held.get(d0, {}).items():
            b = held.get(d1, {}).get(code)
            if not b:
                continue
            checked += 1
            if b["quantity"] == a["quantity"] and (d1, code) not in acts and _outside(b["price"] / a["price"] - 1, _limit(code, base), 1):
                jumps.append({"source": "holdings", "code": code, "date": d1, "before": a["price"], "after": b["price"],
                              "change": b["price"] / a["price"] - 1})
    if not checked:
        return None
    out = {"id": "prices", "checked": checked, "jumps": jumps[:20], "events": jumps, "count": len(jumps), "limit": base,
           "status": "flagged" if jumps else "normal",
           "scope": f"相邻两次观测之间的价格变化超出这段交易日内连续涨跌停的可能（单日按 {base:.0%}，北交所 31%）即列出；"
                    "每只标的序列的前 5 个观测不查（新股无涨跌幅限制）"}
    if jumps:
        j = jumps[0]
        where = {"prices": "候选池收盘价", "signal": "策略算信号用的价格", "holdings": "收盘持仓估值价"}[j["source"]]
        out["verdict"] = (f"{len(jumps)} 处相邻交易日价格变化超出 A 股单日涨跌幅可能，例如{where}里 {j['code']} 在 {j['date']} "
                          f"从 {j['before']:.4g} 变为 {j['after']:.4g}（{j['change']:+.1%}）：可能是复权或份额折算衔接错误，"
                          "用到这些价格的打分、前提检验和归因可能被污染")
    else:
        out["verdict"] = "价格序列里没有超出单日涨跌幅可能的变化"
    return out
