"""Known-answer benchmark for the grid audit: does it tell execution-path differences from signal change?

A trusted generator builds a market, a top-1 rotation strategy and its accounts; no imported code runs.
Each seed yields one market with independent random streams per (seed, date, asset, kind), so turning a
fault on does not redraw the rest of the market. Five scenarios per seed:

* healthy            stable signal, no volume cap
* implementation     stable signal; whole-order volume cap (orders above 10% of the day's volume are rejected)
* signal_decay       signal reverses from the middle of the year, no cap
* mixed              signal reverses and the cap applies
* missing_evidence   the implementation scenario with scores, orders and selection records hidden
* late_decay         signal reverses only in the last quarter, no cap; reported separately (declared after the
                     development run showed a mid-year reversal is visible to the single-window audit, and before
                     any evaluation seed was run)

Each scenario is audited as a grid: a full-year reference and four three-month blocks, every run from an
empty 1,000,000 account. Truth labels are fixed before any method runs and come from the generator:

* An execution fault counts only if it manifested: some block's fresh-minus-continuous gap moves by more
  than T-GRID-PATH-GAP against the same seed's no-cap twin. Unmanifested seeds are scored as healthy and
  their count is reported.
* missing_evidence is built only from manifested implementation seeds; its correct answer is insufficient.

Three fixed methods read the same materials:

* A  single-window audit of the reference run: premise status and whether any order was rejected
* B  plain multi-window reading: a losing block or a restart gap beyond tolerance is called signal change
* C  this grid audit: claims, five-layer pairing and causes

Development seeds (0..19) and evaluation seeds (1000..) are reported separately. The thresholds below were
set before the evaluation seeds were run and are not changed after seeing them.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import Counter

from . import audit_grid

CODES = tuple(f"E{i:02d}.SH" for i in range(10))
SESSIONS, BLOCKS = 240, 4
EDGE, NOISE = .15, .01               # next-day return = EDGE * score * 1% + NOISE * N(0,1)
VOLUME_MEDIAN, VOLUME_SIGMA, PARTICIPATION = 2_000_000, .5, .10
FEE_BP, MIN_FEE, LOT = 2., 5., 100
CAPITAL = 1_000_000.
LABELS = ("healthy", "implementation", "signal_change", "mixed", "insufficient")
DEV_SEEDS = tuple(range(20))
EVAL_BASE = 1000


def _gauss(seed, *key):
    h = hashlib.sha256(json.dumps([seed, *key]).encode()).digest()
    return random.Random(int.from_bytes(h[:8], "big")).gauss(0, 1)


def _dates():
    out = []
    import datetime as dt
    d = dt.date(2023, 1, 2)
    while len(out) < SESSIONS:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


DATES = _dates()


DECAY_FROM = {"half": SESSIONS // 2, "late": 3 * SESSIONS // 4}


def market(seed, decay):
    """Scores, closes and volumes. decay=None, "half" or "late": the edge flips sign from that session onward."""
    score = [{c: _gauss(seed, d, c, "score") for c in CODES} for d in DATES]
    close, price = [], {c: 10. for c in CODES}
    for t, d in enumerate(DATES):
        if t:
            edge = -EDGE if decay and t - 1 >= DECAY_FROM[decay] else EDGE
            for c in CODES:
                price[c] *= 1 + edge * score[t - 1][c] * .01 + NOISE * _gauss(seed, d, c, "noise")
        close.append(dict(price))
    volume = [{c: VOLUME_MEDIAN * math.exp(VOLUME_SIGMA * _gauss(seed, d, c, "volume")) for c in CODES} for d in DATES]
    return {"score": score, "close": close, "volume": volume}


def _fee(notional):
    return max(MIN_FEE, notional * FEE_BP / 1e4)


def account(mkt, start, end, cap):
    """Hold the top-scored asset, trading at each close; a capped order above 10% of volume is rejected whole."""
    cash, qty, prev = CAPITAL, {}, CAPITAL
    daily, snaps, positions, orders = [], [], [], []
    for t in range(start, end):
        d, px, top = DATES[t], mkt["close"][t], max(CODES, key=mkt["score"][t].get)
        fees = 0.
        limit = lambda c: PARTICIPATION * mkt["volume"][t][c] if cap else float("inf")
        for c in [c for c, q in qty.items() if q and c != top]:
            q = qty[c]
            if q > limit(c):
                orders.append({"date": d, "time": "15:00", "code": c, "side": "sell", "quantity": q, "filled": 0,
                               "status": "rejected", "reason": "超过当根成交量上限"})
                continue
            f = _fee(q * px[c])
            cash += q * px[c] - f
            fees += f
            qty[c] = 0
            orders.append({"date": d, "time": "15:00", "code": c, "side": "sell", "quantity": q, "filled": q,
                           "status": "filled", "reason": ""})
        if not qty.get(top):
            q = int((cash - _fee(cash)) / px[top] / LOT) * LOT
            if q > 0:
                if q > limit(top):
                    orders.append({"date": d, "time": "15:00", "code": top, "side": "buy", "quantity": q, "filled": 0,
                                   "status": "rejected", "reason": "超过当根成交量上限"})
                else:
                    f = _fee(q * px[top])
                    cash -= q * px[top] + f
                    fees += f
                    qty[top] = q
                    orders.append({"date": d, "time": "15:00", "code": top, "side": "buy", "quantity": q, "filled": q,
                                   "status": "filled", "reason": ""})
        mv = {c: q * px[c] for c, q in qty.items() if q}
        equity = cash + sum(mv.values())
        daily.append({"date": d, "net_pnl": equity - prev, "fees": fees})
        snaps.append({"date": d, "equity": equity, "cash": cash})
        positions += [{"date": d, "code": c, "quantity": qty[c], "market_value": v} for c, v in mv.items()]
        prev = equity
    return {"daily": daily, "snapshots": snaps, "positions": positions, "orders": orders}


def _windows():
    size = SESSIONS // BLOCKS
    return [("W00", "reference", 0, SESSIONS)] + [(f"B{i + 1}", "blocks", i * size, (i + 1) * size) for i in range(BLOCKS)]


def _cell(sid, wid, group, mkt, start, end, cap, hide=False):
    acc = account(mkt, start, end, cap)
    dates = DATES[start:end]
    bundle = {"code": {"source": "# synthetic grid benchmark strategy\n"}, "snapshots": acc["snapshots"],
              "positions": acc["positions"], "orders": [] if hide else acc["orders"]}
    if not hide:
        bundle["scores"] = [{"date": d, "code": c, "score": mkt["score"][t][c],
                             "selected": c == max(CODES, key=mkt["score"][t].get)}
                            for t, d in zip(range(start, end), dates) for c in CODES]
        bundle["prices"] = [{"date": d, "code": c, "close": mkt["close"][t][c]} for t, d in zip(range(start, end), dates) for c in CODES]
    result = {"mode": "full", "input_digest": f"sha256:{sid}:{wid}:{cap}:{hide}",
              "settings": {"commission_bp": FEE_BP, "minimum_commission": MIN_FEE},
              "modules": {"accounting": {"status": "verified", "baseline_equity": CAPITAL, "daily": acc["daily"]}}}
    selections = None if hide else {
        (d, "get_rank", json.dumps(list(CODES))): {"output": [max(CODES, key=mkt["score"][t].get)], "ref": f"syn:{t}"}
        for t, d in zip(range(start, end), dates)}
    overlay = {"status": "loaded" if selections else "empty", "targets": None, "selections": selections, "instructions": None}
    identity = {"strategy_id": sid, "strategy_name": "", "window_id": wid, "group": group, "run_id": f"{sid}/{wid}",
                "experiment_type": "fresh_start", "is_reference": group == "reference", "seen_status": "synthetic",
                "execution_contract_digest": f"cap={cap}", "market_data_digest": "synthetic", "strategy_state_digest": None}
    return audit_grid.build_cell(identity, result, bundle, overlay)


def scenario(seed, name):
    decay = "half" if name in ("signal_decay", "mixed") else "late" if name == "late_decay" else None
    cap = name in ("implementation", "mixed", "missing_evidence")
    mkt = market(seed, decay)
    sid = f"s{seed}-{name}"
    cells = [_cell(sid, w, g, mkt, a, b, cap, hide=name == "missing_evidence") for w, g, a, b in _windows()]
    return audit_grid.analyze(cells)["strategies"][0]


def _gaps(strategy):
    return {p["fresh_run"].split("/")[-1]: p["fresh_minus_continuous_pp"] for p in strategy["pairs"] if p["status"] == "compared"}


def manifested(capped, twin, threshold):
    a, b = _gaps(capped), _gaps(twin)
    return any(abs(a[k] - b[k]) > threshold * 100 for k in a if k in b)


# ---------------------------------------------------------------- the three methods

def method_a(s):
    """Single-window audit of the reference run only."""
    ref = next(c for c in s["cells"] if c.get("is_reference"))
    premise = (ref.get("premise") or {}).get("status")
    rejected = ref.get("rejected_orders", 0) > 0
    if premise is None or premise == "needs_scores":
        return "implementation" if rejected else "insufficient"
    signal = premise in ("reversed", "not_supported")
    return "mixed" if signal and rejected else "signal_change" if signal else "implementation" if rejected else "healthy"


def method_b(s, threshold):
    """Plain multi-window reading: a losing block or a restart gap beyond tolerance means the signal is unstable."""
    for g in s["groups"]:
        p = g.get("partition")
        if p and (p["losing"] or abs(p["restart_vs_continuous_gap"]) > threshold):
            return "signal_change"
    return "healthy"


def _signal_change(s):
    blocks = [c for c in s["cells"] if c["group"] == "blocks" and c["status"] == "ok"]
    status = [(c.get("premise") or {}).get("status") for c in blocks]
    if not status or all(x in (None, "needs_scores") for x in status):
        return None
    # Only a significant reversal in some block counts: with 60 periods per block, "held" next to
    # "not significant" is ordinary noise (fixed on the development seeds).
    return "reversed" in status


def method_c(s):
    """This grid audit: block-level premise claims plus the localized causes of path differences."""
    if s["check_state"] == "invalid":
        return "insufficient"
    signal = _signal_change(s)
    causes = {x for p in s["pairs"] for x in p.get("causes") or []}
    execution = "observed_execution_path_difference" in causes
    unlocalized = bool(causes & {"insufficient_to_localize", "mixed_or_confounded", "target_mapping_or_unobserved_signal"})
    if signal is None:
        return "insufficient"
    if signal and execution:
        return "mixed"
    if signal:
        return "signal_change"
    if execution:
        return "implementation"
    if unlocalized:
        return "insufficient"
    return "healthy"     # opening-entry differences alone are structural, not a failure


# ---------------------------------------------------------------- localization

def true_divergence(seed, decay, cap):
    """Ground truth straight from the generator, without the audit pipeline: for each block, the first session
    where the same intent (date, code, side) was filled in one account and not in the other."""
    mkt = market(seed, decay)
    ref = account(mkt, 0, SESSIONS, cap)["orders"]
    out = {}
    for wid, group, a, b in _windows()[1:]:
        fresh = account(mkt, a, b, cap)["orders"]
        days = set(DATES[a:b])
        outcome = lambda orders: {(o["date"], o["code"], o["side"]): o["filled"] > 0 for o in orders if o["date"] in days}
        x, y = outcome(fresh), outcome(ref)
        diff = sorted(k[0] for k in set(x) & set(y) if x[k] != y[k])
        out[wid] = diff[0] if diff else None
    return out


def localization(strategy, truth):
    """Date-level: does the audit's first execution-outcome difference match the generator's, block by block?"""
    got = {p["fresh_run"].split("/")[-1]: p.get("first_execution_outcome_difference")
           for p in strategy["pairs"] if p["status"] == "compared"}
    rows = [{"block": w, "truth": t, "found": got.get(w)} for w, t in truth.items()]
    return {"blocks": len(rows), "with_event": sum(1 for r in rows if r["truth"]),
            "exact": sum(1 for r in rows if r["truth"] and r["found"] == r["truth"]),
            "false_event": sum(1 for r in rows if not r["truth"] and r["found"]),
            "missed": sum(1 for r in rows if r["truth"] and not r["found"]), "rows": rows}


# ---------------------------------------------------------------- scoring

def wilson(k, n, z=1.96):
    if not n:
        return None
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0., centre - half), min(1., centre + half)]


def _rate(k, n):
    return {"count": k, "of": n, "rate": k / n if n else None, "wilson95": wilson(k, n)}


def clustered_rate(records, draws=2000):
    """Ratio of counts with whole-seed resampling; records are (seed, numerator, denominator).

    All windows and scenarios sharing a generator seed stay together. Percentile bootstrap intervals
    are descriptive at 30 paths and degenerate at 0/100%; the separate seed-any-event Wilson interval
    describes the chance a path has at least one counted event, not an independent-window error rate.
    """
    import random
    by_seed = {}
    for seed, k, n in records:
        a, b = by_seed.get(seed, (0, 0))
        by_seed[seed] = (a + k, b + n)
    units = [v for _, v in sorted(by_seed.items()) if v[1] > 0]
    k, n = sum(v[0] for v in units), sum(v[1] for v in units)
    result = {"count": k, "of": n, "rate": k / n if n else None, "unit": "seed/generated_path",
              "seeds": len(units), "cluster_percentile95": None,
              "seed_any_numerator": _rate(sum(a > 0 for a, _ in units), len(units)),
              "caution": "路径整体重采样；零误报/全命中时区间会退化，不能解释成零风险或跨生成器保证"}
    if units:
        rng, ratios = random.Random(20261104), []
        for _ in range(draws):
            sample = [rng.choice(units) for _ in units]
            denominator = sum(v[1] for v in sample)
            ratios.append(sum(v[0] for v in sample) / denominator)
        ratios.sort()
        result["cluster_percentile95"] = [ratios[int(.025 * (draws - 1))], ratios[int(.975 * (draws - 1))]]
        # A binomial interval is appropriate only when the metric has exactly one trial per seed.
        if all(b == 1 for _, b in units):
            result["wilson95"] = wilson(k, n)
    return result


def score(rows):
    out = {}
    for m in ("A", "B", "C"):
        cm = {t: Counter() for t in LABELS}
        for r in rows:
            cm[r["truth"]][r[m]] += 1
        impl = [r for r in rows if r["truth"] == "implementation"]
        decay = [r for r in rows if r["truth"] == "signal_change"]
        healthy = [r for r in rows if r["truth"] == "healthy"]
        problem = [r for r in rows if r["truth"] not in ("healthy", "insufficient")]
        hidden = [r for r in rows if r["truth"] == "insufficient"]
        covered = [r for r in rows if r[m] != "insufficient"]
        out[m] = {
            "confusion": {t: dict(c) for t, c in cm.items() if c},
            "misdiagnosis_execution_as_signal": clustered_rate([(r["seed"], int(r[m] in ("signal_change", "mixed")), 1) for r in impl]),
            "missed_signal_change": clustered_rate([(r["seed"], int(r[m] == "healthy"), 1) for r in decay]),
            "false_greenlight": clustered_rate([(r["seed"], int(r[m] == "healthy"), 1) for r in problem]),
            "false_alarm": clustered_rate([(r["seed"], int(r[m] not in ("healthy", "insufficient")), 1) for r in healthy]),
            "abstention": clustered_rate([(r["seed"], int(r[m] == "insufficient"), 1) for r in rows]),
            "abstained_when_evidence_hidden": clustered_rate([(r["seed"], int(r[m] == "insufficient"), 1) for r in hidden]),
            "accuracy_when_answering": clustered_rate([(r["seed"], int(r[m] == r["truth"]), 1) for r in covered]),
            "accuracy_overall": clustered_rate([(r["seed"], int(r[m] == r["truth"]), 1) for r in rows])}
    return out


def run(eval_seeds=30, thresholds=None):
    from .audit_rules import defaults
    t = {**defaults(), **(thresholds or {})}
    gap = t["T-GRID-PATH-GAP"]
    splits = {"development": list(DEV_SEEDS), "evaluation": list(range(EVAL_BASE, EVAL_BASE + eval_seeds))}
    report = {"suite": "grid_claims", "method": audit_grid.METHOD, "thresholds": {"T-GRID-PATH-GAP": gap},
              "generator": {"codes": len(CODES), "sessions": SESSIONS, "blocks": BLOCKS, "edge": EDGE, "noise": NOISE,
                            "volume_median": VOLUME_MEDIAN, "volume_sigma": VOLUME_SIGMA, "participation": PARTICIPATION,
                            "capital": CAPITAL},
              "definitions": {
                  "manifested": "同一种子加量限后，至少一个分块的“独立起跑减连续路径”之差相对无量限孪生改变超过 T-GRID-PATH-GAP",
                  "misdiagnosis_execution_as_signal": "已显现的纯执行情景被判为信号变化（signal_change 或 mixed）",
                  "missed_signal_change": "纯信号变化情景被判为健康",
                  "false_greenlight": "真实有问题（执行、信号或混合）却被判为健康",
                  "false_alarm": "健康情景被判为任何问题",
                  "abstention": "输出证据不足"},
              "methods": {"A": "固定单窗基线：前提状态与是否有拒单（不是完整业内标准）", "B": "简单热力图启发式基线：分块亏损或分段重启差超过容差即判为信号变化（不代表业内多窗方法）",
                          "C": "本网格审计：分块前提主张 + 五层配对原因"},
              "scope": ("合成生成器上的已知答案测试；只说明在这个生成器和这些阈值下的区分能力，"
                        "不是实盘误报率，也不能推广到其他策略家族；单元为种子，不把重叠窗口当独立样本")}
    for split, seeds in splits.items():
        rows, late_rows, unmanifested, loc, loc_seeds = [], [], Counter(), Counter(), []
        for seed in seeds:
            runs = {n: scenario(seed, n) for n in ("healthy", "implementation", "signal_decay", "mixed")}
            impl_on = manifested(runs["implementation"], runs["healthy"], gap)
            mixed_on = manifested(runs["mixed"], runs["signal_decay"], gap)
            truth = {"healthy": "healthy", "implementation": "implementation" if impl_on else "healthy",
                     "signal_decay": "signal_change", "mixed": "mixed" if mixed_on else "signal_change"}
            if not impl_on:
                unmanifested["implementation"] += 1
            if not mixed_on:
                unmanifested["mixed"] += 1
            if impl_on:
                runs["missing_evidence"] = scenario(seed, "missing_evidence")
                truth["missing_evidence"] = "insufficient"
            for name, s in runs.items():
                rows.append({"seed": seed, "scenario": name, "truth": truth[name],
                             "A": method_a(s), "B": method_b(s, gap), "C": method_c(s)})
            for name, decay, cap in (("implementation", None, True), ("signal_decay", "half", False)):
                got = localization(runs[name], true_divergence(seed, decay, cap))
                loc_seeds.append({"seed": seed, "scenario": name, **got})
                for k in ("blocks", "with_event", "exact", "false_event", "missed"):
                    loc[f"{name}.{k}"] += got[k]
            late = scenario(seed, "late_decay")
            late_rows.append({"seed": seed, "scenario": "late_decay", "truth": "signal_change",
                              "A": method_a(late), "B": method_b(late, gap), "C": method_c(late)})
        report[split] = {"seeds": len(seeds), "rows": rows, "unmanifested": dict(unmanifested),
                         "scenario_counts": dict(Counter(r["truth"] for r in rows)), "metrics": score(rows),
                         "localization": {
                             "method": "C 的 first_execution_outcome_difference 对生成器直接算出的分块首个执行分岔日（不经审计管线）",
                             "A_B": "单窗与普通多窗口不输出分岔日期，无法定位",
                             "per_seed": loc_seeds,
                             "implementation": {"exact": clustered_rate([(r["seed"], r["exact"], r["with_event"])
                                                                          for r in loc_seeds if r["scenario"] == "implementation"]),
                                                "all_event_dates_correct_per_seed": _rate(
                                                    sum(r["exact"] == r["with_event"] for r in loc_seeds
                                                        if r["scenario"] == "implementation" and r["with_event"]),
                                                    sum(bool(r["with_event"]) for r in loc_seeds if r["scenario"] == "implementation")),
                                                "missed": loc["implementation.missed"], "false_event": loc["implementation.false_event"],
                                                "blocks": loc["implementation.blocks"]},
                             "signal_decay_false_event": clustered_rate([(r["seed"], r["false_event"], r["blocks"])
                                                                          for r in loc_seeds if r["scenario"] == "signal_decay"])},
                         "late_decay": {"rows": late_rows, "detected": {m: _rate(sum(r[m] in ("signal_change", "mixed")
                                                                                   for r in late_rows), len(late_rows))
                                                                       for m in ("A", "B", "C")}}}
    return report
