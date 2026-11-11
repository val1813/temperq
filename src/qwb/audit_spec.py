"""Did the backtest keep the promises the user declared for the strategy?

Some defects are invisible in the exports alone: a 5% cash buffer typed for 0.5%, a stale equity snapshot,
a missed rebalance day all look like a legitimate, slightly different strategy. Once the user declares what
the strategy promises (`strategy_spec.csv`, two columns key,value), each promise is checked against the
account. Nothing is checked that was not declared, and an undeclared key is listed as unchecked.

Keys:
  cash_buffer    target cash share right after a rebalance, e.g. 0.005
  max_weight     single-name cap at the time it is bought, e.g. 0.3
  max_names      most names held at a close
  flat_overnight true when the strategy promises to hold nothing at the close
  rebalance      month_start | month_end | week_start: days on which orders must be placed
  tolerance      allowed slack for cash_buffer and max_weight (default 0.02)
  trials         how many variants were tried before this one (for the multiple-testing check)

A breach that a failed sell or a refused buy explains is reported apart and not counted against the promise:
the promise was kept by the code and broken by the market.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date

KNOWN = {"cash_buffer", "max_weight", "max_names", "flat_overnight", "rebalance", "tolerance", "trials"}
LOT = 100


def _num(spec, key):
    try:
        return float(str(spec[key]).strip().rstrip("%")) / (100 if str(spec[key]).strip().endswith("%") else 1)
    except (KeyError, ValueError):
        return None


def _days(bundle):
    return [s["date"] for s in bundle.get("snapshots") or []]


def check_cash(bundle, spec, tol):
    want = _num(spec, "cash_buffer")
    if want is None:
        return None
    snaps = {s["date"]: s for s in bundle.get("snapshots") or []}
    short_buy = {o["date"] for o in bundle.get("orders") or [] if o["side"] == "buy" and o.get("filled", 0) < o.get("quantity", 0)}
    buys = defaultdict(list)
    for f in bundle.get("fills") or []:
        if f["side"] == "buy":
            buys[f["date"]].append(f)
    held = defaultdict(list)
    for p in bundle.get("positions") or []:
        if p.get("quantity") and p.get("price"):
            held[p["date"]].append(p["price"])
    rows = []
    for d, fs in sorted(buys.items()):
        s = snaps.get(d)
        if not s or not s.get("equity") or d in short_buy:
            continue                                         # a buy that came up short: the market, not the code
        # Whole lots leave up to one lot of cash per name held, so the allowance is one lot for each.
        prices = held.get(d) or [f["price"] for f in fs]
        lot = sum(LOT * x / s["equity"] for x in prices)
        rows.append({"date": d, "cash_share": s["cash"] / s["equity"], "allowed": want + tol + lot})
    if len(rows) < 5:
        return {"id": "cash_buffer", "status": "insufficient", "verdict": f"声明现金缓冲 {want:.1%}，但完整成交的买入日不足 5 个，未判断"}
    over = [r for r in rows if r["cash_share"] > r["allowed"]]
    med = statistics.median(r["cash_share"] for r in rows)
    out = {"id": "cash_buffer", "declared": want, "days": len(rows), "over": len(over), "median_cash": med,
           "status": "breach" if len(over) >= .5 * len(rows) else "kept"}
    out["verdict"] = (f"声明调仓后留 {want:.1%} 现金；{len(rows)} 个买单全部成交的买入日，收盘现金中位数 {med:.1%}，"
                      f"{len(over)} 天超过声明值加一手和 {tol:.0%} 的余量" + ("：实际留的现金比声明多" if out["status"] == "breach" else ""))
    return out


def check_weight(bundle, spec, tol):
    cap = _num(spec, "max_weight")
    if cap is None:
        return None
    snaps = {s["date"]: s for s in bundle.get("snapshots") or []}
    pos = {(p["date"], p["code"]): p for p in bundle.get("positions") or []}
    rows = []
    for f in bundle.get("fills") or []:
        p, s = pos.get((f["date"], f["code"])), snaps.get(f["date"])
        if f["side"] != "buy" or not p or not s or not s.get("equity"):
            continue
        w = p["market_value"] / s["equity"]
        if w > cap + tol + LOT * f["price"] / s["equity"]:
            rows.append({"date": f["date"], "code": f["code"], "weight": w})
    out = {"id": "max_weight", "declared": cap, "breaches": len(rows), "examples": rows[:5],
           "status": "breach" if rows else "kept"}
    out["verdict"] = (f"声明单只上限 {cap:.0%}；{len(rows)} 次买入当天收盘权重超过上限加一手和 {tol:.0%} 的余量" if rows
                      else f"声明单只上限 {cap:.0%}；买入当天的收盘权重都在上限内")
    return out


def _failed_sells(bundle):
    out = defaultdict(set)
    for o in bundle.get("orders") or []:
        if o["side"] == "sell" and o.get("filled", 0) < o.get("quantity", 0):
            out[o["date"]].add(o["code"])
    return out


def check_names(bundle, spec):
    most = _num(spec, "max_names")
    if most is None:
        return None
    held = defaultdict(set)
    for p in bundle.get("positions") or []:
        if p.get("quantity"):
            held[p["date"]].add(p["code"])
    failed = _failed_sells(bundle)
    over = [d for d in sorted(held) if len(held[d]) > most]
    explained = [d for d in over if failed.get(d)]
    rest = [d for d in over if not failed.get(d)]
    out = {"id": "max_names", "declared": int(most), "days": len(over), "explained": len(explained), "unexplained": len(rest),
           "examples": rest[:5], "status": "breach" if len(rest) >= 3 else "kept"}
    out["verdict"] = (f"声明最多持有 {int(most)} 只；{len(over)} 个收盘超过，其中 {len(explained)} 天当天有卖单没卖掉（市场原因），"
                      f"{len(rest)} 天没有可解释的卖出失败")
    return out


def check_flat(bundle, spec):
    if str(spec.get("flat_overnight", "")).strip().lower() not in ("true", "1", "yes", "是"):
        return None
    held = defaultdict(set)
    for p in bundle.get("positions") or []:
        if p.get("quantity"):
            held[p["date"]].add(p["code"])
    failed = _failed_sells(bundle)
    nights = sorted(held)
    rest = [d for d in nights if not held[d] <= failed.get(d, set())]
    out = {"id": "flat_overnight", "nights": len(nights), "explained": len(nights) - len(rest), "unexplained": len(rest),
           "examples": rest[:5], "status": "breach" if len(rest) >= 3 else "kept"}
    out["verdict"] = (f"声明不留隔夜仓；{len(nights)} 个收盘仍有持仓，其中 {len(nights) - len(rest)} 天当天有卖单没卖掉，"
                      f"{len(rest)} 天持有的标的当天没有卖出失败可解释")
    return out


def check_rebalance(bundle, spec):
    rule = str(spec.get("rebalance", "")).strip().lower()
    if rule not in ("month_start", "month_end", "week_start"):
        return None
    days = _days(bundle)
    if rule == "month_start":
        due = [d for i, d in enumerate(days) if i == 0 or d[:7] != days[i - 1][:7]]
    elif rule == "month_end":
        due = [d for i, d in enumerate(days) if i == len(days) - 1 or d[:7] != days[i + 1][:7]][:-1]
    else:
        wk = lambda d: date.fromisoformat(d).isocalendar()[:2]
        due = [d for i, d in enumerate(days) if i == 0 or wk(d) != wk(days[i - 1])]
    ordered = {o["date"] for o in bundle.get("orders") or []} | {f["date"] for f in bundle.get("fills") or []}
    missed = [d for d in due if d not in ordered]
    out = {"id": "rebalance", "declared": rule, "due": len(due), "missed": len(missed), "examples": missed[:5],
           "status": "breach" if len(missed) >= 2 and len(missed) >= .2 * len(due) else "kept"}
    names = {"month_start": "每月第一个交易日", "month_end": "每月最后一个交易日", "week_start": "每周第一个交易日"}
    out["verdict"] = (f"声明在{names[rule]}调仓；{len(due)} 个调仓日里 {len(missed)} 天没有任何委托"
                      + ("：调仓日的判断可能和声明不一致（例如按日历日而非交易日）" if out["status"] == "breach" else
                         "（没有委托也可能是持仓已在目标附近，少量缺失不算违约）"))
    return out


def check_trials(bundle, spec):
    """Deflated Sharpe ratio (Bailey and Lopez de Prado, 2014) given the declared number of variants tried."""
    n = _num(spec, "trials")
    if n is None or n < 1:
        return None
    eq = [s["equity"] for s in bundle.get("snapshots") or [] if s.get("equity")]
    r = [b / a - 1 for a, b in zip(eq, eq[1:]) if a > 0]
    if len(r) < 60 or statistics.pstdev(r) == 0:
        return {"id": "trials", "status": "insufficient", "verdict": "交易日不足 60 个或收益没有波动，未做多重检验校正"}
    T = len(r)
    m, sd = statistics.mean(r), statistics.pstdev(r)
    sr = m / sd
    skew = sum((x - m) ** 3 for x in r) / T / sd ** 3
    kurt = sum((x - m) ** 4 for x in r) / T / sd ** 4
    nd = statistics.NormalDist()
    g = .5772156649
    if n > 1:
        sr0 = math.sqrt(1 / T) * ((1 - g) * nd.inv_cdf(1 - 1 / n) + g * nd.inv_cdf(1 - 1 / (n * math.e)))
    else:
        sr0 = 0.
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    dsr = nd.cdf((sr - sr0) * math.sqrt(T - 1) / math.sqrt(denom)) if denom > 0 else None
    out = {"id": "trials", "declared": int(n), "sharpe_annual": sr * math.sqrt(252), "threshold_annual": sr0 * math.sqrt(252),
           "dsr": dsr, "status": "insufficient" if dsr is None else "weak" if dsr < .95 else "kept",
           "scope": "近似Deflated Sharpe（Bailey 与 López de Prado，2014）；搜索次数为用户声明，各候选夏普方差未知，以1/T代理，未校准候选相关性、时间依赖与未披露搜索；不保证校正保守"}
    out["verdict"] = (f"声明试过 {int(n)} 个版本；年化夏普 {sr * math.sqrt(252):.2f}，挑最好一个时纯靠运气就能达到约 "
                      f"{sr0 * math.sqrt(252):.2f}；近似选择校正统计量 DSR={dsr:.3f}（不是策略有效或未来盈利的概率）"
                      + ("：在这么多次尝试下，这个夏普不足以排除运气" if out["status"] == "weak" else "") if dsr is not None
                      else f"声明试过 {int(n)} 个版本；收益分布偏度峰度过大，未能计算")
    return out


def analyze(bundle, accounting, thresholds=None):
    spec = bundle.get("spec") or {}
    if not spec or accounting.get("status") != "verified":
        return None
    tol = _num(spec, "tolerance")
    tol = .02 if tol is None else tol
    parts = {"cash_buffer": check_cash(bundle, spec, tol), "max_weight": check_weight(bundle, spec, tol),
             "max_names": check_names(bundle, spec), "flat_overnight": check_flat(bundle, spec),
             "rebalance": check_rebalance(bundle, spec), "trials": check_trials(bundle, spec)}
    unknown = sorted(set(spec) - KNOWN)
    breached = [k for k, v in parts.items() if (v or {}).get("status") == "breach"]
    return {"id": "spec", **parts, "unknown": unknown, "breached": breached,
            "status": "breach" if breached else "kept",
            "scope": "只检查用户声明的承诺；没有声明的不检查。卖出失败、买单没买够等市场原因造成的偏离单列，不算违约"}
