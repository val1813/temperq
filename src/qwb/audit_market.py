"""Is the return the strategy's own, or the market's and a few days'?

1. Market exposure. With the platform's benchmark net value, daily account returns are regressed on the
   benchmark's: beta, the annualised intercept (alpha) and its t statistic. A return mostly explained by beta
   came from being invested, not from selection. The benchmark is whatever the platform used; the audit does
   not pick one, and says so.
2. Concentration in time. How much of the total log return the best 1% of days carry, and what is left
   without the best five days. Strong concentration is common in trend strategies; it is reported as
   fragility, not as an error.

Both are descriptive: no action card says the strategy is wrong.
"""
from __future__ import annotations

import math
import statistics


def _ols(y, x):
    n = len(y)
    mx, my = sum(x) / n, sum(y) / n
    sxx = sum((a - mx) ** 2 for a in x)
    if sxx <= 0:
        return None
    beta = sum((a - mx) * (b - my) for a, b in zip(x, y)) / sxx
    alpha = my - beta * mx
    resid = [b - alpha - beta * a for a, b in zip(x, y)]
    s2 = sum(r * r for r in resid) / (n - 2) if n > 2 else None
    se = math.sqrt(s2 * (1 / n + mx * mx / sxx)) if s2 else None
    ss_tot = sum((b - my) ** 2 for b in y)
    r2 = 1 - sum(r * r for r in resid) / ss_tot if ss_tot else None
    return {"alpha": alpha, "beta": beta, "alpha_t": alpha / se if se else None, "r2": r2}


def exposure(bundle, least=60):
    snaps = bundle.get("snapshots") or []
    bench = {r["date"]: 1 + r["benchmark_pct"] / 100 for r in bundle.get("platform_returns") or []
             if r.get("benchmark_pct") is not None}
    days = [s for s in snaps if s["date"] in bench and s.get("equity")]
    # Dropping a missing benchmark date would silently turn a daily return into
    # a multi-day return and regress mixed horizons as if they were comparable.
    if len(days) != len(snaps):
        return None
    if len(days) < least + 1:
        return None
    y, x = [], []
    for a, b in zip(days, days[1:]):
        if bench[a["date"]] > 0 and a["equity"] > 0:
            y.append(b["equity"] / a["equity"] - 1)
            x.append(bench[b["date"]] / bench[a["date"]] - 1)
    if len(y) < least:
        return None
    fit = _ols(y, x)
    if not fit:
        return None
    total = days[-1]["equity"] / days[0]["equity"] - 1
    btotal = bench[days[-1]["date"]] / bench[days[0]["date"]] - 1
    try:
        annual_alpha = math.expm1(252 * math.log1p(fit["alpha"])) if fit["alpha"] > -1 else None
    except OverflowError:
        annual_alpha = None
    out = {"id": "exposure", "days": len(y), "beta": fit["beta"], "alpha_annual": annual_alpha, "alpha_t": fit["alpha_t"],
           "r2": fit["r2"], "total": total, "benchmark_total": btotal,
           "scope": "基准用平台导出的基准净值，缺日期不跨日拼接；换基准会改变β和截距。t值为常规OLS，未作自相关/异方差校正；β乘累计基准收益仅为描述性近似，不是精确收益归因"}
    t = fit["alpha_t"]
    sig = t is not None and abs(t) >= 2
    # The benchmark's share of the result: beta times the benchmark's own return, against the strategy's.
    from_beta = fit["beta"] * btotal
    out["from_beta"] = from_beta
    led = (not sig and fit["beta"] >= .5 and (fit["r2"] or 0) >= .3 and total > 0 and from_beta >= .5 * total)
    r2_text = f"{fit['r2']:.0%}" if fit['r2'] is not None else "不可计算"
    alpha_text = f"{annual_alpha:+.1%}" if annual_alpha is not None else "不可表示"
    t_text = f"{t:.1f}" if t is not None else "不可计算"
    text = (f"相对平台基准，β 为 {fit['beta']:.2f}，基准解释了日收益波动的 {r2_text}；"
            f"扣除 β 后年化截距 {alpha_text}（t = {t_text}）")
    if t is None:
        text += "。残差方差或标准误不足以计算t值，不能据此判断截距显著性"
    elif led:
        text += (f"，与零没有可靠区别。同期基准涨 {btotal:+.1%}，按 β 折算约 {from_beta:+.1%}，占策略累计 {total:+.1%} 的大半："
                 "这段收益主要来自跟随基准")
    elif not sig:
        text += "，与零没有可靠区别：样本内看不出稳定超额，但收益也不是靠跟随这个基准得来"
    out["verdict"] = text
    out["status"] = "market" if led else "normal"
    return out


def concentration(bundle, least=120):
    snaps = bundle.get("snapshots") or []
    eq = [s["equity"] for s in snaps if s.get("equity")]
    if len(eq) < least:
        return None
    logs = [math.log(b / a) for a, b in zip(eq, eq[1:]) if a > 0 and b > 0]
    total = sum(logs)
    ranked = sorted(logs, reverse=True)
    k = max(1, round(.01 * len(logs)))
    top = sum(ranked[:k])
    without5 = math.exp(total - sum(ranked[:5])) - 1
    out = {"id": "time_concentration", "days": len(logs), "top_share_days": k, "top_log": top, "total_log": total,
           "total": math.exp(total) - 1, "without_best5": without5,
           "scope": "按日对数收益；集中在少数日子是趋势类策略的常态，说明结果脆弱，不说明有错"}
    if total > 0:
        out["share"] = top / total
        out["verdict"] = (f"最好的 {k} 天（占 {len(logs)} 个交易日的 1%）贡献了全部对数收益的 {top / total:.0%}；"
                          f"去掉最好的 5 天，累计收益从 {out['total']:+.1%} 变为 {without5:+.1%}")
        out["status"] = "fragile" if without5 <= 0 < out["total"] else "normal"
    else:
        out["verdict"] = f"累计收益 {out['total']:+.1%}，没有正收益可供拆分"
        out["status"] = "normal"
    return out


def analyze(bundle, accounting, thresholds=None):
    if accounting.get("status") != "verified":
        return None
    parts = {"exposure": exposure(bundle), "time_concentration": concentration(bundle)}
    if not any(parts.values()):
        return None
    return {"id": "market", **parts}
