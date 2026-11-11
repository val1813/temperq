"""Show the audit is worth something: restate a backtest under realistic costs, then compare versions.

Two layers:

1. Restated original: the same fills, re-costed with the confirmed commission, the stamp tax
   that applied on each trade date, and the one-tick spread scenario. Nothing is re-run.
2. Original vs optimized: the user re-runs the optimized code on the original platform and
   uploads the new export. Both versions are restated the same way and compared over the
   full period and over the internal validation segment, which the audit did not tune on.

A same-period backtest is still in-sample for the strategy's own design. The page says so and
lets "keep the original" be the outcome.
"""
from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import date
from itertools import accumulate


DEFAULT_ANNUAL_EFFECT = .02  # fixed planning target, never fitted to the observed gain
MIN_ANNUALIZATION_SESSIONS = 252
MIN_PLANNING_SESSIONS = 60  # variance planning is suppressed for very short histories


def paired_inference(rows, baseline, annual_effect=DEFAULT_ANNUAL_EFFECT, *, sensitivity=False):
    """HAC interval for the mean paired daily P&L, in constant initial-capital units.

    Newey-West/Bartlett lag rule, Student-t critical value (approximation).
    Power planning is conditional on a stable effect and long-run variance;
    historical selection and multiple trials are not adjusted here.
    """
    from scipy.stats import norm, t
    if not isinstance(annual_effect, (int, float)) or not math.isfinite(annual_effect) or annual_effect <= 0 or annual_effect > 1:
        raise ValueError("最小有意义效应须为 (0, 1] 的预设年收益比例，例如 0.02")
    n = len(rows)
    out = {"method": "Newey-West Bartlett HAC; approximate Student-t 95% CI",
           "sessions": n, "daily": rows, "confidence": .95, "power": .8,
           "sessions_per_year": 252, "baseline": baseline,
           "unit": "CNY_per_session_at_same_initial_capital",
           "interpretation": "model_sensitivity" if sensitivity else "paired_strategy_difference",
           "note": "历史同区间配对；要求差额近似平稳。未调整挑版本、多次检验和数据挖掘；不证明未来改善。"}
    if not n or baseline <= 0 or any(not math.isfinite(r["difference"]) for r in rows):
        return {**out, "status": "unavailable", "reason": "缺少有限的逐日配对差额或正期初资金"}
    values = [r["difference"] for r in rows]
    mean = statistics.mean(values)
    out.update(mean_daily=mean, total_difference=sum(values))
    if n >= MIN_ANNUALIZATION_SESSIONS and not sensitivity:
        out.update(annualized_difference=mean * 252, annualized_initial_capital_rate=mean * 252 / baseline)
    else:
        out["annualization_note"] = "少于252个配对交易日或属于模型敏感性实验，不展示观测差额年化。"
    if n < 30:
        return {**out, "status": "insufficient", "reason": "少于30个配对交易日，不给显著性或所需年数"}
    lag = min(n - 1, int(4 * (n / 100) ** (2 / 9)))
    def variance(bandwidth):
        centered = [v - mean for v in values]
        v = sum(x * x for x in centered) / n
        for k in range(1, bandwidth + 1):
            cov = sum(centered[i] * centered[i - k] for i in range(k, n)) / n
            v += 2 * (1 - k / (bandwidth + 1)) * cov
        return max(0., v) * n / (n - 1)
    long_var = variance(lag)
    out.update(lag=lag, long_run_variance=long_var,
               lag_sensitivity=[{"lag": k, "standard_error": math.sqrt(variance(k) / n)}
                                for k in sorted({lag, min(n - 1, max(1, lag * 2)), min(n - 1, 20)})])
    if long_var <= 1e-20:
        return {**out, "status": "degenerate", "reason": "样本差额恒定/方差为零；不据此声称未来确定或零年即可验证"}
    se = math.sqrt(long_var / n)
    width = float(t.ppf(.975, n - 1)) * se
    lo, hi = mean - width, mean + width
    out.update(status="estimated", standard_error=se, mean_daily_ci=[lo, hi],
               sample_horizon_difference_ci=[lo * n, hi * n])
    if not sensitivity:
        out["signal"] = "positive" if lo > 0 else "negative" if hi < 0 else "unresolved"
        if n >= MIN_ANNUALIZATION_SESSIONS:
            out["annualized_ci"] = [lo * 252, hi * 252]
    if sensitivity:
        out["planning_note"] = "模型敏感性仅描述设置差异，不输出改善信号或改善所需年数。"
        return out
    if n < MIN_PLANNING_SESSIONS:
        out["planning_note"] = "少于60个配对交易日，波动估计过短，不展示所需年数。"
        return out
    target = annual_effect * baseline / 252
    out["planning"] = {"effect_source": "prespecified_annual_initial_capital_rate",
        "daily_effect": target, "annual_effect_rate": annual_effect,
        "total_years": float(norm.ppf(.975) + norm.ppf(.8)) ** 2 * long_var / target ** 2 / 252,
        "note": "双侧5%显著性、80%检验力的近似总样本量，252日/年；假定效应与相关结构稳定，"
                "目标效应预先指定，未使用观测均值；不是保证何时显著，也不是还需追加的年数。"}
    return out


def uncertainty_text(info):
    if info.get("status") != "estimated":
        return info.get("reason", "未计算配对不确定性")
    lo, hi = info["mean_daily_ci"]
    signal = ("模型敏感性仅描述设置差异，不判策略改善" if info.get("interpretation") == "model_sensitivity" else
              {"positive": "区间高于零", "negative": "区间低于零", "unresolved": "区间包含零，尚不能分辨改善与噪声"}[info["signal"]])
    plan = info.get("planning")
    return (f"逐日配对差额均值 ¥{info['mean_daily']:+,.2f}/日，近似95%置信区间 [¥{lo:+,.2f}, ¥{hi:+,.2f}]/日；{signal}。"
            + (f"按预设最小有意义效应每年初始资金的 {plan['annual_effect_rate']:.1%}、当前差额波动，"
               f"双侧5%、80%检验力约需总计 {plan['total_years']:,.1f} 年（条件规划）。" if plan else info.get("planning_note", ""))
            + info["note"])


def _key(fill):
    ref = fill.get("ref") or {}
    return (ref.get("source"), ref.get("row"), fill["date"], fill["code"], fill["side"])


def eligibility(result):
    """Can this audited run enter a comparison at all? (eligible, [reason]) with every reason that blocks it.

    Unreconciled books, unordered or repeated dates, non-finite figures or a non-positive baseline block
    every comparison: a restated number built on them would be a number about the data error.
    """
    reasons = []
    modules = (result or {}).get("modules") or {}
    account = modules.get("accounting")
    if (result.get('evidence_assessment') or {}).get('attribution_status') == 'provisional':
        reasons.append('收益或逐股分配依赖推定事件，先补原始凭证')
    if (result or {}).get("mode", "full") != "full":
        reasons.append("不是完整审计（只有代码或部分材料）")
    if not account or not account.get("daily"):
        reasons.append("缺少逐日账目")
        return False, reasons
    if account.get("status") != "verified":
        reasons.append(f"账目未闭合（{account.get('status') or '状态缺失'}）：先核对差异，再比较")
    dates = [d.get("date") for d in account["daily"]]
    try:
        if any(not isinstance(d, str) or date.fromisoformat(d).isoformat() != d for d in dates):
            raise ValueError("invalid date")
    except (TypeError, ValueError):
        reasons.append("逐日账目日期缺失或格式无效")
        return False, reasons
    if len(set(dates)) != len(dates):
        reasons.append("逐日账目有重复日期")
    if dates != sorted(dates):
        reasons.append("逐日账目日期没有按时间排序")
    if any(not isinstance(d.get("net_pnl"), (int, float)) or not math.isfinite(d["net_pnl"]) for d in account["daily"]):
        reasons.append("逐日盈亏有缺失或非有限值")
    base = account.get("baseline_equity")
    if not isinstance(base, (int, float)) or not math.isfinite(base) or base <= 0:
        reasons.append("期初权益缺失或不为正")
    for row in account["daily"]:
        for field in ("external_flow", "external_cash_flow", "net_flow", "deposit", "withdrawal"):
            value = row.get(field)
            if value is not None and value != 0:
                reasons.append("存在出入金，但当前重述不支持完整份额/现金流收益口径")
        if "equity_change" in row and isinstance(row.get("net_pnl"), (int, float)):
            change = row["equity_change"]
            if not isinstance(change, (int, float)) or not math.isfinite(change) or abs(change - row["net_pnl"]) > .01:
                reasons.append("权益变动与净盈亏不闭合：可能有未处理的出入金或估值变化")
    if isinstance(base, (int, float)) and math.isfinite(base):
        equity = base
        for row in account["daily"]:
            if isinstance(row.get("net_pnl"), (int, float)):
                equity += row["net_pnl"]
                if equity <= 0:
                    reasons.append("权益路径不为正，不支持常规收益/回撤比较")
                    break
    fees, tick = modules.get("fee_analysis") or {}, modules.get("tick_analysis") or {}
    modeled = {_key(f): f.get("modeled_fee") for f in fees.get("fills", [])}
    for fill in tick.get("fills", []):
        if not fill.get("accounted_interval"):
            continue
        if any(not isinstance(v, (int, float)) or not math.isfinite(v)
               for v in (fill.get("fee"), fill.get("tick_scenario_cost"), modeled.get(_key(fill)))):
            reasons.append("成交所需费用/价差情景未完整覆盖或含非有限值")
            break
    return not reasons, reasons


def restate(result):
    """Daily realistic P&L for one audited backtest, or None when the run is not eligible (see eligibility)."""
    if not eligibility(result)[0]:
        return None
    modules = result.get("modules", {})
    account, fees, tick = modules.get("accounting"), modules.get("fee_analysis"), modules.get("tick_analysis")
    modeled = {_key(f): f.get("modeled_fee") for f in (fees or {}).get("fills", [])}
    fee_gap, spread, counted = defaultdict(float), defaultdict(float), defaultdict(int)
    unpriced = 0
    for fill in (tick or {}).get("fills", []):
        if not fill.get("accounted_interval"):
            continue  # baseline-day fills are outside the P&L bridge
        day = fill["date"]
        counted[day] += 1
        spread[day] += fill.get("tick_scenario_cost") or 0.
        model = modeled.get(_key(fill))
        if model is None:
            unpriced += 1
        else:
            fee_gap[day] += fill["fee"] - model   # positive: the backtest charged too much
    baseline = account["baseline_equity"]
    equity = {"reported": baseline, "realistic": baseline}
    peak = dict(equity)
    worst = {"reported": 0., "realistic": 0.}
    days = []
    for row in account["daily"]:
        d = row["date"]
        realistic = row["net_pnl"] + fee_gap[d] - spread[d]
        equity["reported"] += row["net_pnl"]; equity["realistic"] += realistic
        for k in equity:
            peak[k] = max(peak[k], equity[k])
            worst[k] = min(worst[k], equity[k] / peak[k] - 1)
        days.append({"date": d, "reported": row["net_pnl"], "fee_gap": fee_gap[d], "spread": spread[d],
                     "realistic": realistic, "fills": counted[d], "turnover": row.get("gross_turnover") or 0.})
    return {"baseline": baseline, "start": days[0]["date"] if days else None, "end": days[-1]["date"] if days else None,
            "days": days, "unpriced_fills": unpriced, "drawdown": worst}


def totals(restated, start=None, end=None):
    rows = [r for r in restated["days"] if (start is None or r["date"] >= start) and (end is None or r["date"] <= end)]
    out = {k: sum(r[k] for r in rows) for k in ("reported", "fee_gap", "spread", "realistic", "fills", "turnover")}
    out["sessions"] = len(rows)
    return out


def windows(result):
    part = (result.get("period_review") or {}).get("partition") or {}
    if part.get("status") != "planned":
        return None
    return {"development": (part["development_start"], part["development_end"]),
            "validation": (part["validation_start"], part["validation_end"])}


def _path(result):
    fills = (result.get("modules", {}).get("tick_analysis") or {}).get("fills") or []
    return [(f.get("date"), f.get("time"), f.get("code"), f.get("side"), f.get("quantity") or 0, f.get("price"))
            for f in fills]


def route_similarity(a, b):
    """identical (same dates, times, codes, sides, quantities, prices), similar (quantities within 2%), or different.

    "Similar" is a description of the routes only. It never says where a P&L difference came from.
    """
    if not a or len(a) != len(b):
        return "different"
    if a == b:
        return "identical"
    if all(x[:4] == y[:4] and x[4] and abs(x[4] - y[4]) <= .02 * max(x[4], y[4]) for x, y in zip(a, b)):
        return "similar"
    return "different"


def _fees_explain(original, optimized, a, b, start, end):
    """Is the P&L difference fully accounted for by fees? Needs identical fills and identical daily marks."""
    marks = lambda r: [(d["date"], round(d["net_pnl"] + (d.get("fees") or 0.), 6))
                       for d in r["modules"]["accounting"]["daily"] if start <= d["date"] <= end]
    if route_similarity(_path(original), _path(optimized)) != "identical":
        return False, "成交路径不完全相同"
    if marks(original) != marks(optimized):
        return False, "成交相同，但扣费前的逐日盈亏不同：估值、价格或账户动作有变化，不是费用"
    return True, "成交与扣费前逐日盈亏都相同，差额只来自费用"


def compare(original, optimized, *, annual_effect=DEFAULT_ANNUAL_EFFECT, sensitivity=False):
    """Restate both versions with one method; judge on realistic P&L and drawdown, same-period runs are in-sample."""
    from .audit_rules import defaults
    fee_mismatch = any((original.get("settings") or {}).get(key) != (optimized.get("settings") or {}).get(key)
                       for key in ("commission_bp", "minimum_commission"))
    code_a, code_b = original.get("strategy_code_digest"), optimized.get("strategy_code_digest")
    sensitivity = sensitivity or bool(code_a and code_a == code_b) or fee_mismatch
    tolerance = {**defaults(), **((original.get("rules") or {}).get("thresholds") or {})}["T-DD-TOLERANCE"]
    reasons = [f"{name}：{r}" for name, run in (("原版", original), ("改版", optimized)) for r in eligibility(run)[1]]
    if reasons:
        return {"comparable": False, "state": "invalid", "reason": "；".join(reasons)}
    a, b = restate(original), restate(optimized)
    if any(equity <= 0 for run in (a, b)
           for equity in accumulate((r["realistic"] for r in run["days"]), initial=run["baseline"])):
        return {"comparable": False, "state": "invalid", "reason": "成本情景下权益路径不为正"}
    if a["baseline"] != b["baseline"]:
        observations = None
        if [r['date'] for r in a['days']] == [r['date'] for r in b['days']]:
            observations = [{
                'baseline': run['baseline'],
                'reported_return': sum(r['reported'] for r in run['days']) / run['baseline'],
                'scenario_return': sum(r['realistic'] for r in run['days']) / run['baseline'],
                'reported_drawdown': run['drawdown']['reported'],
                'fills': sum(r['fills'] for r in run['days']),
            } for run in (a, b)]
        return {"comparable": False, "state": "scale_sensitivity", 'scale_observations': observations,
                "reason": (f"两版期初资金不同（¥{a['baseline']:,.0f} 与 ¥{b['baseline']:,.0f}）：金额不能直接比较，"
                           "可观察各自收益率和回撤，但不据此判哪一版策略更优。整手、最低佣金和成交量限制会随资金变化；"
                           "要判断规则修改效果，另用相同资金重跑")}
    dates_a, dates_b = ([r["date"] for r in run["days"]] for run in (a, b))
    if dates_a != dates_b:
        shared = sorted(set(dates_a) & set(dates_b))
        description = {}
        for name, run in (("original", a), ("optimized", b)):
            if shared:
                # Only a contiguous shared interval has a meaningful interval drawdown. Missing sessions
                # cannot be zipped away or replaced by the full-period drawdown.
                lo, hi = shared[0], shared[-1]
                interval = [r for r in run["days"] if lo <= r["date"] <= hi]
                equity_before = run["baseline"] + sum(r["realistic"] for r in run["days"] if r["date"] < lo)
                peak = equity = equity_before
                dd = 0.
                for r in interval:
                    equity += r["realistic"]
                    peak = max(peak, equity)
                    dd = min(dd, equity / peak - 1) if peak > 0 else dd
                description[name] = {"start": lo, "end": hi, "sessions": len(interval),
                                     "realistic_pnl": sum(r["realistic"] for r in interval),
                                     "drawdown": dd if [r["date"] for r in interval] == shared else None}
        return {"comparable": False, "state": "descriptive_only", "same_period": False,
                "reason": "完整交易日集合不同；共同覆盖仅作描述，不判改版更好或更差，也不沿用全期回撤",
                "common_dates": shared, "only_original_dates": sorted(set(dates_a) - set(dates_b)),
                "only_optimized_dates": sorted(set(dates_b) - set(dates_a)), "description": description}
    start, end = max(a["start"], b["start"]), min(a["end"], b["end"])
    if start > end:
        return {"comparable": False, "reason": "两次回测的日期没有重叠，无法比较；请用相同区间重新回测。"}
    span = windows(original)
    rows = {"full": (totals(a, start, end), totals(b, start, end))}
    if span:
        v0 = max(span["validation"][0], start)
        if v0 <= end:
            rows["validation"] = (totals(a, v0, end), totals(b, v0, end))
    same_period = a["start"] == b["start"] and a["end"] == b["end"]
    full_gain = rows["full"][1]["realistic"] - rows["full"][0]["realistic"]
    val = rows.get("validation")
    val_gain = val[1]["realistic"] - val[0]["realistic"] if val else None
    dd_a, dd_b = a["drawdown"]["realistic"], b["drawdown"]["realistic"]   # negative numbers
    deeper = dd_a - dd_b > tolerance
    route = route_similarity(_path(original), _path(optimized))
    same_trades = route == "identical"
    fee_explained, fee_note = _fees_explain(original, optimized, a, b, start, end)
    if fee_mismatch and not fee_explained:
        return {"comparable": False, "state": "invalid", "reason": "两版费用情景不同，需统一费率和最低佣金后再比较"}
    if full_gain > 0 and deeper:
        verdict, state = (f"收益更高，但最大回撤从 {-dd_a:.1%} 扩大到 {-dd_b:.1%}；有得有失，建议先保留原版", "mixed")
    elif full_gain > 0 and (val_gain is None or val_gain > 0):
        verdict, state = "该段历史按同一费率情景，改版净盈亏高于原版", "better"
        if val_gain is None:
            verdict += "；没有可用的验证段"
    elif full_gain > 0:
        verdict, state = "优化版只在前段更好，验证段没有赢过原版；建议先保留原版", "mixed"
    else:
        verdict, state = "按同一费率情景，优化版没有好于原版；建议保留原版", "worse"
    if sensitivity:
        verdict, state = "仅描述设置或费用敏感性，不按收益高低选择更真实的模型，不判断策略改版优劣", "model_sensitivity"
    verdict += "（同区间复跑，样本内，不代表样本外有效）"
    if fee_explained:
        verdict += "。两版成交完全相同、扣费前逐日盈亏也相同，差额只来自费用：这是费率敏感性，不是策略改进"
    elif route != "different":
        verdict += f"。两版成交路径{'完全相同' if same_trades else '相近'}，但差额不能只归到费用：{fee_note}"
    paired = [{"date": x["date"], "original": x["realistic"], "optimized": y["realistic"],
               "difference": y["realistic"] - x["realistic"]} for x, y in zip(a["days"], b["days"])]
    uncertainty = {"full": paired_inference(paired, a["baseline"], annual_effect, sensitivity=sensitivity)}
    if span and "validation" in rows:
        uncertainty["validation"] = paired_inference([r for r in paired if r["date"] >= v0], a["baseline"], annual_effect, sensitivity=sensitivity)
    verdict += "。" + uncertainty_text(uncertainty["full"])
    inference_state = uncertainty["full"].get("signal", uncertainty["full"]["status"])
    if state == "better" and inference_state != "positive":
        verdict += "。历史金额更高，但当前证据不足以确认稳定改善；继续保留原版作为基线"
    output = {"comparable": True, "start": start, "end": end, "same_period": same_period,
            "rows": rows, "full_gain": full_gain, "validation_gain": val_gain,
            "drawdown": (a["drawdown"]["realistic"], b["drawdown"]["realistic"]),
            "same_trades": same_trades, "route_similarity": route, "fee_explained": fee_explained,
            "drawdown_deeper": deeper, "evidence": "E4", "verdict": verdict, "state": state,
            "uncertainty": uncertainty, "inference_state": inference_state,
            "state_scope": "historical_pnl_and_drawdown_direction_only_not_statistical_acceptance"}
    if sensitivity:
        output.pop("inference_state", None)
        output["mode"] = "model_sensitivity"
    return output
