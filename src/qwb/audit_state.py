"""Did the strategy know what it held? Its own recorded belief against the account at the moment it decided.

The strategy records, when a decision starts, which names it believes it holds (`temperq_state.csv` from the
TemperQ recorder, or any `date,time,believed` table). The account's actual holdings at that moment are the
previous close plus that day's fills before the record's time. A difference means the decision ran on a
position that did not exist (phantom), or ignored one that did (orphan). The books close on every such day:
accounting checks cash and shares, never what the strategy thought it had. Measured from two records;
nothing is inferred about why they differ.
"""
from __future__ import annotations

from collections import defaultdict


def _clock(text):
    text = str(text or "").strip()
    if len(text) >= 16 and text[10] in " T":
        text = text[11:]
    return text[:8] if len(text) >= 5 and text[2] == ":" else ""


def analyze(bundle, accounting, thresholds=None):
    believed = bundle.get("believed") or []
    if not believed or accounting.get("status") != "verified":
        return None
    least = (thresholds or {}).get("T-STATE-DAYS", 3)
    days = [s["date"] for s in bundle.get("snapshots") or []]
    prior = {d: p for p, d in zip(days, days[1:])}
    close = defaultdict(lambda: defaultdict(int))
    for p in bundle.get("positions") or []:
        if p.get("quantity"):
            close[p["date"]][p["code"]] = p["quantity"]
    fills = defaultdict(list)
    for f in bundle.get("fills") or []:
        fills[f["date"]].append(f)
    contrib = defaultdict(float)
    for c in accounting.get("contributions") or []:
        contrib[(c["date"], c["code"])] += c["net_pnl"]
    first = {}
    for b in sorted(believed, key=lambda b: (b["date"], _clock(b.get("time")))):
        first.setdefault(b["date"], b)                       # the first decision of the day
    rows, checked, run, best, prev_bad = [], 0, 0, 0, False
    phantom_days = orphan_days = 0
    orphan_pnl = 0.
    for d in days:
        b = first.get(d)
        at = _clock(b.get("time")) if b else ""
        if b is None or (at and d not in prior):
            prev_bad = False
            continue
        checked += 1
        if at:                                               # a decision-time record: previous close + earlier fills
            holding = dict(close[prior[d]])
            for f in fills[d]:
                if _clock(f.get("time")) and _clock(f["time"]) < at:
                    holding[f["code"]] = holding.get(f["code"], 0) + (f["quantity"] if f["side"] == "buy" else -f["quantity"])
        else:                                                # no time: an end-of-day record, against the close
            holding = dict(close[d])
        actual = {c for c, q in holding.items() if q > 0}
        phantom, orphan = set(b["codes"]) - actual, actual - set(b["codes"])
        if phantom or orphan:
            rows.append({"date": d, "time": at, "believed": sorted(b["codes"]), "held": sorted(actual),
                         "phantom": sorted(phantom), "orphan": sorted(orphan)})
            phantom_days += bool(phantom)
            orphan_days += bool(orphan)
            orphan_pnl += sum(contrib[(d, c)] for c in orphan)
            run = run + 1 if prev_bad else 1
            best = max(best, run)
            prev_bad = True
        else:
            prev_bad = False
    out = {"id": "state", "checked_days": checked, "mismatch_days": len(rows), "phantom_days": phantom_days,
           "orphan_days": orphan_days, "longest_run": best, "orphan_pnl": orphan_pnl, "examples": rows[:5], "rows": rows,
           "status": "flagged" if len(rows) >= least else "normal",
           "scope": "比较策略决策时自记的持仓与账户当时的实际持仓（前一日收盘加当天此前的成交）；不一致说明内部状态没有跟上成交，不推断具体原因"}
    if rows:
        out["verdict"] = (f"{checked} 个有策略状态记录的决策日里，{len(rows)} 天策略以为的持仓与账户实际持仓不同（最长连续 {best} 天）："
                          f"{phantom_days} 天策略以为持有、账户里没有；{orphan_days} 天账户持有、策略不知道，"
                          f"这些没被管理的持仓当天盈亏合计 {orphan_pnl:+,.0f} 元。账目闭合发现不了这种不一致")
    else:
        out["verdict"] = f"{checked} 个有策略状态记录的决策日里，策略以为的持仓与账户实际持仓都一致"
    return out
