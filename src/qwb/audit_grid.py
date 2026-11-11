"""One strategy, many windows: which claims hold across start dates, where they stop, and what to test next.

Input is a manifest of audited runs (`qwb audit run` outputs), optionally with source overlays written by
`audit_grid_sources` (policy targets, selections, order instructions, each with its real meaning). Nothing
is re-run and no strategy code is executed.

Per strategy:

* Comparison eligibility (audit_verification.eligibility) and identity: what was checked to be the same,
  what differs, and what is unknown. Fresh starts and derived slices have different opening equity by design.
* Each fresh window is paired with the reference run's continuous slice over the same sessions. The slice is
  a derived observation, not a new backtest. Five layers are compared separately: selection, policy target,
  instruction, execution outcome, account. Each layer reports its own first observed difference.
* Divergence episodes (holdings sets differ) and execution events (same intent, different outcome) are all
  listed; material ones are filtered by pre-declared thresholds. The money bridge restates both paths on a
  common display capital: a linear re-expression of history, never a same-capital counterfactual.
* Claims are evaluated per cell from their own evidence. A claim is a candidate until the user confirms it.
  check_state (were the listed checks triggered) is never a claim verdict and never an economic pass.

Reported actual paths are primary; the audit re-cost scenario is listed separately. Window dates overlap:
coverage repeat is not an effective sample size. All of it is the same history re-run (E4), already seen.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from collections import defaultdict
from pathlib import Path

from . import audit_grid_sources as sources
from .audit_rules import cite, defaults
from .audit_verification import eligibility, restate

METHOD = "audit_grid_v4"
NUMERIC_TOL = 1e-10          # numeric equality of recorded vectors; not an economic threshold
DISPLAY_CAPITAL = 1_000_000.
REQUIRED = ("strategy_id", "window_id", "audit_dir")
EXPERIMENTS = {"fresh_start": "空仓独立起跑", "continuous_slice": "连续路径观察切片（派生，不是新回测）",
               "checkpoint_continuation": "断点续跑（完整账户与策略状态）", "walk_forward_oos": "滚动训练后的外层测试",
               "unknown": "未知"}
CHECK_WORDS = {"not_triggered": "所列检查未触发", "triggered": "触发复核", "insufficient": "材料不足", "invalid": "材料无效"}
CLAIM_WORDS = {"supported_within_scope": "范围内支持", "contradicted_within_scope": "范围内反驳",
               "insufficient": "证据不足", "not_applicable": "不适用", "unconfirmed": "候选，未经确认"}
GRID_RULES = ("T-GRID-BLOCK-SHARE", "T-GRID-TOP-DAYS", "T-GRID-MIN-SESSIONS", "T-GRID-PATH-GAP",
              "T-GRID-WEIGHT-L1", "T-GRID-RETURN-BP", "T-GRID-EPISODE-SHARE", "M-GRID-PAIR")


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest() if text else None


def _finite(x):
    return x if isinstance(x, (int, float)) and math.isfinite(x) else None


# ---------------------------------------------------------------- manifest and cells

def read_manifest(path):
    """manifest_v2 (run_id, experiment_type, reference, overlay_dir, *_digest, seen_status, ...) or the old
    strategy_id / window_id / audit_dir / group form; the old form maps to fresh_start, group=reference."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError(f"{path} 没有任何网格行")
    missing = [c for c in REQUIRED if c not in rows[0]]
    if missing:
        raise ValueError(f"{path} 缺少列：{'、'.join(missing)}")
    seen = set()
    for r in rows:
        key = (r["strategy_id"].strip(), r["window_id"].strip())
        if key in seen:
            raise ValueError(f"网格里 {key[0]} × {key[1]} 出现两次")
        seen.add(key)
        kind = (r.get("experiment_type") or r.get("experiment") or "fresh_start").strip()
        if kind not in EXPERIMENTS:
            raise ValueError(f"{key[0]} × {key[1]} 的实验类型 {kind} 不认识；可用：{'、'.join(EXPERIMENTS)}")
        r["experiment_type"] = kind
        r["run_id"] = (r.get("run_id") or f"{key[0]}/{key[1]}").strip()
        ref = (r.get("reference") or "").strip().lower()
        r["is_reference"] = ref in ("1", "true", "yes", "y") if ref else (r.get("group") or "").strip() == "reference"
        r["audit_dir"] = str((path.parent / r["audit_dir"]).resolve())
        if (r.get("overlay_dir") or "").strip():
            r["overlay_dir"] = str((path.parent / r["overlay_dir"].strip()).resolve())
    return rows


def load_cell(row, thresholds=None):
    """One cell from a manifest row: its audit output on disk, plus the source overlay if given."""
    identity = {"strategy_id": row["strategy_id"].strip(), "strategy_name": (row.get("strategy_name") or "").strip(),
                "window_id": row["window_id"].strip(), "group": (row.get("group") or "").strip(), "run_id": row["run_id"],
                "experiment_type": row["experiment_type"], "is_reference": row["is_reference"],
                "seen_status": (row.get("seen_status") or "historical_already_seen").strip(),
                "execution_contract_digest": (row.get("execution_contract_digest") or row.get("engine_digest") or "").strip() or None,
                "market_data_digest": (row.get("market_data_digest") or row.get("data_digest") or "").strip() or None,
                "strategy_state_digest": (row.get("strategy_state_digest") or "").strip() or None}
    path = Path(row["audit_dir"]) / "evidence.json"
    if not path.exists():
        return {**identity, "status": "missing", "reasons": ["没有 evidence.json：这一格还没有审计输出"]}
    evidence = json.loads(path.read_text(encoding="utf-8"))
    overlay = row.get("overlay_dir")
    return build_cell(identity, evidence["result"], evidence.get("input") or {},
                      _overlay(Path(overlay)) if overlay else {"status": "not_provided"},
                      hashlib.sha256(path.read_bytes()).hexdigest(), thresholds)


def build_cell(identity, result, bundle, overlay=None, evidence_sha256=None, thresholds=None):
    """One cell from an audit result held in memory (the benchmark uses this path too)."""
    from .audit_evidence import projection
    result = projection(bundle, result)
    settings = result.get("settings") or {}
    cell = {**identity, "input_digest": result.get("input_digest"),
            "code_digest": _digest(((bundle.get("code") or {}).get("source")) or ""),
            "cost": [settings.get("commission_bp"), settings.get("minimum_commission")],
            "evidence_sha256": evidence_sha256}
    overlay = overlay or {"status": "not_provided"}
    cell["parent_bundle_digest"] = result.get("input_digest")
    cell["overlay_digest"] = _digest(json.dumps(overlay.get("source_files", []), sort_keys=True)) if overlay.get("source_files") else None
    cell["audit_bundle_digest"] = (_digest(json.dumps([cell["parent_bundle_digest"], cell["overlay_digest"]]))
                                     if cell["overlay_digest"] else cell["parent_bundle_digest"])
    cell["normalized_target_weights"] = overlay.get("normalized_target_weights", [])
    ok, reasons = eligibility(result)
    if not ok:
        return {**cell, "status": "invalid", "reasons": reasons}
    series = _series(result, bundle, thresholds or {})
    if series.get("status") == "incomplete":
        return {**cell, **series}
    cell.update(series)
    cell["status"] = "ok"
    cell["overlay"] = overlay or {"status": "not_provided"}
    return cell


def _series(result, bundle, thresholds):
    acc = result["modules"]["accounting"]
    dates = [d["date"] for d in acc["daily"]]
    snap = {s["date"]: s for s in bundle.get("snapshots") or []}
    lost = [d for d in dates if d not in snap]
    if lost:
        return {"status": "incomplete", "reasons": [f"{len(lost)} 个账目日期没有账户快照"]}
    held, weights = defaultdict(set), defaultdict(dict)
    for p in bundle.get("positions") or []:
        if (p.get("quantity") or 0) > 0 and p["date"] in snap:
            held[p["date"]].add(p["code"])
            eq = snap[p["date"]]["equity"]
            if eq:
                weights[p["date"]][p["code"]] = (p.get("market_value") or 0.) / eq
    for d, s in snap.items():
        if s.get("equity"):
            weights[d]["CASH"] = (s.get("cash") or 0.) / s["equity"]
    orders = defaultdict(list)
    for o in bundle.get("orders") or []:
        orders[o["date"]].append({"time": o.get("time"), "code": o.get("code"), "side": o.get("side"),
                                  "quantity": o.get("quantity"), "filled": o.get("filled"), "status": o.get("status"),
                                  "reason": o.get("reason"), "row": (o.get("ref") or {}).get("row")})
    restated = restate(result)
    real = [acc["baseline_equity"]]
    for d in restated["days"]:
        real.append(real[-1] + d["realistic"])
    from . import audit_premise
    premise = audit_premise.analyze(bundle, None, None, thresholds) if bundle.get("scores") else None
    return {"dates": dates, "equity": [snap[d]["equity"] for d in dates], "opening": acc["baseline_equity"],
            "equity_recost": real[1:], "held": {d: frozenset(v) for d, v in held.items()}, "weights": dict(weights),
            "orders": dict(orders), "has_scores": bool(bundle.get("scores")),
            "rejected_orders": sum(1 for o in bundle.get("orders") or [] if o.get("status") == "rejected"),
            "premise": premise and {k: premise.get(k) for k in ("status", "verdict", "premise", "source", "direction", "inference")},
            "premise_overall": premise and {k: (premise.get("overall") or {}).get(k) for k in ("periods", "mean", "t", "iid_t", "hac_lag")},
            "start": dates[0], "end": dates[-1], "sessions": len(dates),
            "return_reported": snap[dates[-1]]["equity"] / acc["baseline_equity"] - 1,
            "return_recost": real[-1] / acc["baseline_equity"] - 1,
            "drawdown_reported": _drawdown([acc["baseline_equity"]] + [snap[d]["equity"] for d in dates])}


def _drawdown(path):
    peak, worst = path[0], 0.
    for x in path:
        peak = max(peak, x)
        worst = min(worst, x / peak - 1)
    return worst


def _overlay(folder):
    out = {"status": "loaded", "dir": str(folder)}
    out["source_files"] = [{"path": str(p), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in sorted(folder.iterdir()) if p.is_file()]
    for name, reader in (("targets", sources.read_targets), ("selections", sources.read_selections),
                         ("instructions", sources.read_instructions)):
        f = folder / {"targets": "target_snapshots.csv", "selections": "selections.csv", "instructions": "instructions.csv"}[name]
        out[name] = reader(f) if f.exists() else None
    if not any(out[k] for k in ("targets", "selections", "instructions")):
        out["status"] = "empty"
    target_path = folder / "target_snapshots.csv"
    if target_path.exists():
        from .audit_target_records import metadata
        with target_path.open(encoding="utf-8-sig", newline="") as f:
            out["normalized_target_weights"] = [{"date": r["date"], "code": r["code"],
                                                  "target_weight": float(r["target_weight"]), **metadata(r),
                                                  "ref": {"source": str(target_path), "row": i}}
                                                 for i, r in enumerate(csv.DictReader(f), 2)]
    return out


# ---------------------------------------------------------------- identity and coverage

def _identity(cells):
    """Two lists: dimensions checked (same / different, with evidence) and dimensions still unknown."""
    ready = [c for c in cells if c["status"] == "ok" or c.get("code_digest")]
    fresh = [c for c in ready if c.get("experiment_type") == "fresh_start"]
    verified, unknown, issues = [], [], []
    checks = [("strategy_source", "源码", "code_digest", ready, "源码文本 SHA256"),
              ("cost_scenario", "费用情景", "cost", ready, "审计设置里的佣金与最低佣金"),
              ("opening_equity", "期初资金（独立起跑格）", "opening", fresh, "账目期初权益"),
              ("execution_contract", "成交约定与引擎版本", "execution_contract_digest", ready, "清单声明的引擎 SHA256"),
              ("market_data", "行情数据版本", "market_data_digest", ready, "清单声明的数据引用 SHA256")]
    for key, word, field, pool, method in checks:
        values = [c.get(field) for c in pool]
        if not pool or any(v is None for v in values):
            unknown.append({"dimension": key, "label": word, "reason": "部分或全部格没有这项记录",
                            "required_evidence": method})
            continue
        distinct = {json.dumps(v) for v in values}
        same = len(distinct) == 1
        verified.append({"dimension": key, "label": word, "status": "same" if same else "different",
                         "evidence": f"{len(pool)} 格，{len(distinct)} 种值", "checked_method": method})
        if not same:
            issues.append(f"{word}不一致（{len(distinct)} 种）")
    unknown.append({"dimension": "strategy_state", "label": "策略内部状态", "reason": "导出材料没有策略记忆、止损状态的快照",
                    "required_evidence": "每格期初与分岔日的策略内部状态导出"})
    digests = [c["input_digest"] for c in cells if c.get("input_digest")]
    dupes = sorted({d for d in digests if digests.count(d) > 1})
    if dupes:
        names = [c["window_id"] for c in cells if c.get("input_digest") in dupes]
        issues.append(f"{'、'.join(names)} 的输入完全相同：同一份材料被当成了不同窗口")
    return issues, verified, unknown


def _coverage(cells, pairs):
    ok = [c for c in cells if c["status"] == "ok"]
    total = sum(c["sessions"] for c in ok)
    distinct = len({d for c in ok for d in c["dates"]})
    return {"run_count": len(cells), "usable_runs": len(ok),
            "derived_slice_count": sum(1 for p in pairs if p["status"] == "compared"),
            "window_sessions": total, "distinct_sessions": distinct,
            "repeat": total / distinct if distinct else None,
            "note": "各窗口交易日之和对不同交易日数：日期被重复覆盖的倍数，不是有效样本量、置信度或校正因子"}


# ---------------------------------------------------------------- pairing

def _returns(equity, start):
    out, prev = [], start
    for e in equity:
        out.append(e / prev - 1 if prev else 0.)
        prev = e
    return out


def _spans(flags, dates):
    """Contiguous runs of True as (start_index, end_index)."""
    out, j = [], None
    for i, f in enumerate(flags):
        if f and j is None:
            j = i
        if not f and j is not None:
            out.append((j, i - 1)); j = None
    if j is not None:
        out.append((j, len(flags) - 1))
    return out


def _l1(a, b):
    return sum(abs((a or {}).get(k, 0.) - (b or {}).get(k, 0.)) for k in set(a or {}) | set(b or {}))


def _compare_selections(a, b, dates):
    if a is None or b is None:
        return {"state": "incomplete", "reason": "至少一边没有选择记录"}
    days = set(dates)
    la = {k: v for k, v in a.items() if k[0] in days}
    lb = {k: v for k, v in b.items() if k[0] in days}
    shared = sorted(set(la) & set(lb))
    unsafe = [k for k in shared if any(x.get("ambiguous") or not x.get("input_complete", True) for x in (la[k], lb[k]))]
    comparable = [k for k in shared if k not in unsafe]
    every = [k for k in comparable if la[k]["output"] != lb[k]["output"]]
    # On a fresh start's first session the two accounts necessarily hold different things; a step whose output
    # depends on current holdings (not recorded as an input) differs there by construction, not by signal.
    # Only the controlled adapter's known holding-dependent function gets this exception. A different
    # ranking on the opening day must still be reported; the date alone is not an explanation.
    opening = [k for k in every if k[0] == dates[0] and k[1] == "evaluate_etf_worth"]
    differ = [k for k in every if k not in opening]
    state = "observed_different" if differ else ("incomplete" if unsafe else
            ("opening_state_only" if opening else ("observed_equal" if shared else "incomplete")))
    return {"state": state, "opening_session_differences": len(opening),
            "opening_note": "差异只出现在独立起跑首日：两边期初持仓必然不同，记录里没有“当前是否持有”这一输入" if opening else None,
            "matched": len(comparable), "differing": len(differ), "ambiguous_or_incomplete_inputs": len(unsafe),
            "unsafe_examples": [{"date": k[0], "step": k[1], "fresh_records": la[k].get("records"),
                                 "slice_records": lb[k].get("records"),
                                 "reason": "重复决策身份或缺少分支输入，不能用最后一次输出替代完整调用"} for k in unsafe[:3]],
            "missing_left": len(set(lb) - set(la)),
            "missing_right": len(set(la) - set(lb)), "first_difference": every[0][0] if every else None,
            "examples": [{"date": k[0], "step": k[1], "fresh": la[k]["output"], "slice": lb[k]["output"],
                          "refs": [la[k]["ref"], lb[k]["ref"]]} for k in (differ or opening)[:3]]}


def _compare_targets(a, b, dates):
    if a is None or b is None:
        return {"state": "not_applicable" if a is None and b is None else "incomplete",
                "reason": "没有组合目标记录（排名或选择不是完整目标向量）"}
    days = set(dates)
    ga, gb = defaultdict(list), defaultdict(list)
    for s in a:
        if s["date"] in days:
            ga[(s["date"], s["time"])].append(s)
    for s in b:
        if s["date"] in days:
            gb[(s["date"], s["time"])].append(s)
    shared = sorted(set(ga) & set(gb))
    ambiguous = [k for k in shared if not k[1] or len(ga[k]) > 1 or len(gb[k]) > 1]
    rows, differ, worst = [], [], 0.
    for k in shared:
        if k in ambiguous:
            continue
        x, y = ga[k][0], gb[k][0]
        if not (x["complete"] and y["complete"]) or set(x["weights"]) != set(y["weights"]):
            rows.append({"date": k[0], "time": k[1], "state": "incomplete", "reason": "向量不完整或资产池不同，不补零"})
            continue
        d_inf = max(abs(x["weights"][c] - y["weights"][c]) for c in x["weights"])
        d_l1 = sum(abs(x["weights"][c] - y["weights"][c]) for c in x["weights"])
        worst = max(worst, d_inf)
        rows.append({"date": k[0], "time": k[1], "d_inf": d_inf, "d_l1": d_l1,
                     "refs": [x["decision_id"], y["decision_id"]]})
        if d_inf > NUMERIC_TOL:
            differ.append(k)
    compared = [r for r in rows if "d_inf" in r]
    incomplete = bool(ambiguous or set(ga) != set(gb) or len(compared) != len(shared))
    return {"state": "observed_different" if differ else ("observed_equal" if compared and not incomplete else "incomplete"),
            "shared_observed_equal": bool(compared) and not differ,
            "matched": len(compared), "differing": len(differ), "ambiguous": len(ambiguous),
            "missing_left": len(set(gb) - set(ga)), "missing_right": len(set(ga) - set(gb)),
            "max_d_inf": worst if compared else None, "first_difference": differ[0][0] if differ else None,
            "rows": rows}


def _intents(orders, dates):
    out = {}
    for d in dates:
        for o in orders.get(d, []):
            out.setdefault((d, o["code"], o["side"]), []).append(o)
    return out


def _outcome(rows):
    filled = sum(r.get("filled") or 0 for r in rows)
    return "filled" if filled > 0 else "not_filled"


def _pair(cell, ref, t):
    pos = {d: i for i, d in enumerate(ref["dates"])}
    base = {"status": "not_comparable", "fresh_run": cell["run_id"], "slice_of": ref["run_id"]}
    if any(d not in pos for d in cell["dates"]):
        return {**base, "reason": "这一格有参考窗口里没有的交易日，不能按同日期配对"}
    idx = [pos[d] for d in cell["dates"]]
    if idx != list(range(idx[0], idx[-1] + 1)):
        return {**base, "reason": "这一格在参考窗口同期内缺少交易日，不能压缩缺口后配对"}
    i0, dates = idx[0], cell["dates"]
    ref_start = ref["equity"][i0 - 1] if i0 > 0 else ref["opening"]
    ref_start_rc = ref["equity_recost"][i0 - 1] if i0 > 0 else ref["opening"]
    eb = [ref["equity"][i] for i in idx]
    fresh = cell["equity"][-1] / cell["opening"] - 1
    cont = eb[-1] / ref_start - 1
    fresh_rc = cell["equity_recost"][-1] / cell["opening"] - 1
    cont_rc = ref["equity_recost"][idx[-1]] / ref_start_rc - 1
    diff = [DISPLAY_CAPITAL * (x / cell["opening"] - y / ref_start) for x, y in zip(cell["equity"], eb)]
    ra, rb = _returns(cell["equity"], cell["opening"]), _returns(eb, ref_start)
    ret_gap_bp = [(x - y) * 1e4 for x, y in zip(ra, rb)]
    held_differ = [cell["held"].get(d, frozenset()) != ref["held"].get(d, frozenset()) for d in dates]
    weight_l1 = [_l1(cell["weights"].get(d), ref["weights"].get(d)) for d in dates]
    ia, ib = _intents(cell["orders"], dates), _intents(ref["orders"], dates)
    exec_events = []
    for k in sorted(set(ia) & set(ib)):
        oa, ob = _outcome(ia[k]), _outcome(ib[k])
        if oa != ob:
            exec_events.append({"date": k[0], "code": k[1], "side": k[2], "fresh_outcome": oa, "slice_outcome": ob,
                                "fresh_orders": ia[k], "slice_orders": ib[k]})
    instr_diff = [d for d in dates if sorted((o["code"], o["side"], o["quantity"]) for o in cell["orders"].get(d, []))
                  != sorted((o["code"], o["side"], o["quantity"]) for o in ref["orders"].get(d, []))]
    ov_a, ov_b = cell.get("overlay") or {}, ref.get("overlay") or {}
    sel = _compare_selections(ov_a.get("selections"), ov_b.get("selections"), dates)
    tgt = _compare_targets(ov_a.get("targets"), ov_b.get("targets"), dates)
    firsts = {"first_observed_signal_difference": sel.get("first_difference"),
              "first_target_difference": tgt.get("first_difference"),
              "first_instruction_difference": instr_diff[0] if instr_diff else None,
              "first_execution_outcome_difference": exec_events[0]["date"] if exec_events else None,
              "first_position_difference": dates[held_differ.index(True)] if any(held_differ) else None,
              "first_return_difference": next((d for d, g in zip(dates, ret_gap_bp) if abs(g) > t["T-GRID-RETURN-BP"]), None)}
    episodes = _episodes(cell, ref, dates, held_differ, weight_l1, ret_gap_bp, diff, exec_events, t)
    out = {"status": "compared", "fresh_run": cell["run_id"], "slice_of": ref["run_id"], "start": dates[0], "end": dates[-1],
           "sessions": len(dates), "slice_start_equity": ref_start, "fresh_start_equity": cell["opening"],
           "fresh_return": fresh, "continuous_return": cont, "fresh_minus_continuous_pp": 100 * (fresh - cont),
           "recost": {"scenario_id": "audit_recost_tick_spread", "fresh_return": fresh_rc, "continuous_return": cont_rc,
                      "fresh_minus_continuous_pp": 100 * (fresh_rc - cont_rc)},
           "raw_pnl": {"fresh": cell["equity"][-1] - cell["opening"], "slice": eb[-1] - ref_start},
           "bridge": {"display_capital": DISPLAY_CAPITAL, "total": diff[-1], "first_day": diff[0],
                      "after_first_day": diff[-1] - diff[0],
                      "closes": abs(diff[-1] - DISPLAY_CAPITAL * (fresh - cont)) < 1e-6,
                      "label": "标准化历史路径金额：两条实际路径各按自己的起点换算到同一展示本金，不是同资金反事实"},
           "layers": {"selection": sel, "target": tgt,
                      "instruction": {"state": "observed_different" if instr_diff else "observed_equal",
                                      "differing_dates": len(instr_diff), "first_difference": instr_diff[0] if instr_diff else None},
                      "execution": {"state": "observed_different" if exec_events else "observed_equal",
                                    "matched_intents": len(set(ia) & set(ib)), "differing": len(exec_events),
                                    "missing_left": len(set(ib) - set(ia)), "missing_right": len(set(ia) - set(ib))},
                      "account": {"positions_differ_sessions": sum(held_differ),
                                  "weight_l1_over_threshold": sum(1 for x in weight_l1 if x > t["T-GRID-WEIGHT-L1"]),
                                  "return_over_threshold": sum(1 for g in ret_gap_bp if abs(g) > t["T-GRID-RETURN-BP"]),
                                  "max_daily_gap_bp": max((abs(g) for g in ret_gap_bp), default=0.)}},
           **firsts}
    candidates = [v for v in firsts.values() if v]
    out["global_first_difference"] = min(candidates) if candidates else None
    out["episodes"] = episodes
    out["material_episodes"] = [e["id"] for e in episodes if e["material"]]
    out["execution_events"] = [{k: e[k] for k in ("date", "code", "side", "fresh_outcome", "slice_outcome")} for e in exec_events]
    out["mechanism"], out["target_state"], out["account_state"] = _layer_states(out, t)
    out["causes"] = _causes(out, cell, ref)
    out["next_falsification"] = _next_tests(out)
    out["paired_daily"] = [{"date": d, "fresh_return_bp": a * 1e4, "slice_return_bp": b * 1e4, "gap_bp": g,
                            "scaled_cumulative_gap": x, "holdings_differ": h, "weight_l1": w}
                           for d, a, b, g, x, h, w in zip(dates, ra, rb, ret_gap_bp, diff, held_differ, weight_l1)]
    return out


def _episodes(cell, ref, dates, held_differ, weight_l1, ret_gap_bp, diff, exec_events, t):
    """Every span where the two accounts hold different securities, with the execution events around it."""
    out = []
    for n, (j, k) in enumerate(_spans(held_differ, dates), start=1):
        before = diff[j - 1] if j > 0 else 0.
        linked = [e for e in exec_events if (dates[j - 1] if j > 0 else dates[0]) <= e["date"] <= dates[k]]
        cond = _condition_span(cell, ref, dates, linked)
        rows = lambda run, a, b: [{**o, "date": d} for d in dates[a:b + 1] for o in run["orders"].get(d, [])]
        lo = max(0, j - 1)
        during = diff[k] - before
        out.append({"id": f"{cell['run_id']}~{ref['run_id']}#E{n}", "kind": "asset_set_divergence",
                    "start": dates[j], "end": dates[k], "sessions": k - j + 1, "right_censored": k == len(dates) - 1,
                    "fresh_holds": sorted(cell["held"].get(dates[j], ())), "slice_holds": sorted(ref["held"].get(dates[j], ())),
                    "weight_l1_max": max(weight_l1[j:k + 1]), "return_divergence_days":
                        sum(1 for g in ret_gap_bp[j:k + 1] if abs(g) > t["T-GRID-RETURN-BP"]),
                    "gap_before": before, "gap_during": during, "gap_after": diff[-1] - diff[k],
                    "label": "分岔期间观察到的损益差（标准化历史路径金额），不是某一笔拒单造成的损失",
                    "execution_events": [{k2: e[k2] for k2 in ("date", "code", "side", "fresh_outcome", "slice_outcome")} for e in linked],
                    "execution_condition_span": cond,
                    "fresh_orders": rows(cell, lo, k), "slice_orders": rows(ref, lo, k),
                    "material": bool(linked) or abs(during) >= t["T-GRID-EPISODE-SHARE"] * DISPLAY_CAPITAL})
    return out


def _condition_span(cell, ref, dates, events):
    """For each execution event: every date in the pair window on which each run had a rejected order
    on the same code and side. Listed per run; retries without a parent id are not linked as one order."""
    rejected = lambda run, e: sorted(d for d in dates for o in run["orders"].get(d, [])
                                     if o["code"] == e["code"] and o["side"] == e["side"] and o.get("status") == "rejected")
    return [{"code": e["code"], "side": e["side"], "event_date": e["date"],
             "fresh_rejected_dates": rejected(cell, e), "slice_rejected_dates": rejected(ref, e),
             "link": "同代码同方向的请求按日期列出；没有父单编号，不推断为同一笔委托的重试"} for e in events]


def _layer_states(p, t):
    sel, tgt = p["layers"]["selection"]["state"], p["layers"]["target"]["state"]
    decision = tgt if tgt in ("observed_equal", "observed_different") else sel
    if decision in ("observed_equal", "opening_state_only"):
        mech = "behavioral_consistency_only"
    elif decision == "observed_different":
        mech = "observed_difference"
    else:
        mech = "insufficient"
    target = tgt if tgt != "incomplete" or sel == "incomplete" else "incomplete"
    account = "matched_within_tolerance" if abs(p["fresh_return"] - p["continuous_return"]) <= t["T-GRID-PATH-GAP"] \
        else "observed_path_difference"
    return mech, target, account


def _causes(p, cell, ref):
    """Explanations the observations cannot rule out; evidence elimination, not calibrated probabilities."""
    if p["account_state"] == "matched_within_tolerance":
        return []
    decided = p["mechanism"]
    exec_diff = p["layers"]["execution"]["state"] == "observed_different"
    pos_diff = p["layers"]["account"]["positions_differ_sessions"] > 0
    first_share = abs(p["bridge"]["first_day"]) / abs(p["bridge"]["total"]) if p["bridge"]["total"] else 0.
    if decided == "behavioral_consistency_only" and exec_diff and pos_diff:
        causes = ["observed_execution_path_difference"]
    elif decided == "observed_difference":
        causes = ["mixed_or_confounded"] if exec_diff else (
            ["target_mapping_or_unobserved_signal"] if not (cell["has_scores"] and ref["has_scores"]) else ["observed_target_difference"])
    elif not pos_diff and first_share >= .5:
        causes = ["opening_entry_difference"]
    else:
        causes = ["insufficient_to_localize"]
    p["first_day_share"] = first_share
    return causes


NEXT = {
    "observed_execution_path_difference": {
        "test": "从分岔前一天的完整账户与策略状态出发，在整单全成/全拒与允许部分成交两种执行模型下各重放一次",
        "competing_explanations": ["整单成交门槛下的执行路径差", "资金规模不同导致的数量差", "策略内部状态差"],
        "required_inputs": ["分岔前一天的 checkpoint（持仓、可卖量、现金、应收、策略记忆）", "部分成交参考模型（P1）"],
        "expected_discriminating_observation": "部分成交下两条路径是否在若干日内收敛",
        "expense_tier": "P1 受控重放", "why_this_first": "目标已相同，下一步只改执行模型这一条解释轴"},
    "opening_entry_difference": {
        "test": "核对独立起跑首日的建仓价与连续账户上一日收盘价之差，并做同日起跑、继承持仓的断点续跑",
        "competing_explanations": ["空仓起跑必须在首日按盘中价建仓", "首日之后的路径差"],
        "required_inputs": ["首日成交价与上一日收盘价（已有）", "checkpoint 续跑（P1）"],
        "expected_discriminating_observation": "去掉首日后两条路径的差是否落入容差",
        "expense_tier": "低：先用已有成交和收盘价复核", "why_this_first": "不需要新回测，用已有材料就能排除或确认"},
    "target_mapping_or_unobserved_signal": {
        "test": "导出决策前完整目标和每日打分",
        "competing_explanations": ["目标映射依赖账户状态", "未记录的信号差异"],
        "required_inputs": ["决策前完整目标快照", "每日打分"], "expected_discriminating_observation": "同日打分是否一致",
        "expense_tier": "低：补导出", "why_this_first": "缺材料时先补材料，不建议加模型"},
    "observed_target_difference": {
        "test": "从第一次目标不同的那天，用完整账户与策略状态续跑",
        "competing_explanations": ["策略记忆（止损、持仓天数）", "资金与持仓不同导致的规则分支"],
        "required_inputs": ["分岔日的策略内部状态导出"], "expected_discriminating_observation": "续跑后目标是否重合",
        "expense_tier": "P1 断点续跑", "why_this_first": "打分已一致，只剩状态这一条轴"},
    "mixed_or_confounded": {
        "test": "先补齐各层记录，再分别冻结目标或执行做对照",
        "competing_explanations": ["目标差异", "执行差异"], "required_inputs": ["完整目标快照", "checkpoint"],
        "expected_discriminating_observation": "冻结一层后差异是否消失", "expense_tier": "P1",
        "why_this_first": "两层同时不同，单独看任何一层都不能定位"},
    "insufficient_to_localize": {
        "test": "导出决策记录与 checkpoint，而不是再跑更多重叠窗口",
        "competing_explanations": ["未记录的目标差异", "执行差异", "期初状态"],
        "required_inputs": ["决策记录", "checkpoint"], "expected_discriminating_observation": "哪一层先出现差异",
        "expense_tier": "低：补导出", "why_this_first": "现有材料定位不了，先补能区分的材料"},
}


def _next_tests(p):
    return [{"cause": c, **NEXT[c]} for c in p["causes"]]


# ---------------------------------------------------------------- partitions and concentration (descriptive)

def _groups(cells, reference):
    out = []
    ref_dates = set(reference["dates"]) if reference else set()
    for group in sorted({c["group"] for c in cells if c["group"] and not c["is_reference"] and c["group"] != "reference"}):
        members = sorted((c for c in cells if c["group"] == group), key=lambda c: (c.get("start") or "", c["window_id"]))
        ready = [c for c in members if c["status"] == "ok"]
        row = {"group": group, "windows": [c["window_id"] for c in members], "not_ready": [c["window_id"] for c in members if c["status"] != "ok"]}
        dates = [d for c in ready for d in c["dates"]]
        if row["not_ready"]:
            row["kind"] = "incomplete"
        elif len(dates) != len(set(dates)):
            row["kind"] = "overlapping"
        elif ref_dates and set(dates) == ref_dates:
            row["kind"] = "partition"
        else:
            row["kind"] = "gaps"
        out.append(row)
    return out


def _partition(ready, reference, t):
    pnls = [c["return_reported"] for c in ready]
    positive = [p for p in pnls if p > 0]
    best = max(ready, key=lambda c: c["return_reported"])
    share = best["return_reported"] / sum(positive) if positive and best["return_reported"] > 0 else None
    chained = math.prod(1 + c["return_reported"] for c in ready) - 1
    gap = reference["return_reported"] - chained
    return {"blocks": len(ready), "losing": [c["window_id"] for c in ready if c["return_reported"] < 0],
            "best": best["window_id"], "best_share": share, "chained_return": chained,
            "reference_return": reference["return_reported"], "restart_vs_continuous_gap": gap,
            "start_state_gap": gap, "start_state_gap_deprecated": "旧字段名，下一版删除；同一个数",
            "restart_vs_continuous_gap_label": ("分段重启与连续路径之差（描述性）：参考窗口收益减各段独立起跑收益连乘，"
                                                "正值表示连续路径更高；混合了资金规模、持仓、策略记忆和执行路径，"
                                                "连乘是合成摘要，不是按增长资金重跑的可投资账户"),
            "concentrated": bool(share is not None and len(ready) >= 3 and share >= t["T-GRID-BLOCK-SHARE"])}


def _top_days(reference, t):
    n = int(t["T-GRID-TOP-DAYS"])
    daily = [b - a for a, b in zip([reference["opening"]] + reference["equity"][:-1], reference["equity"])]
    total = sum(daily)
    ranked = sorted(zip(daily, reference["dates"]), reverse=True)
    best, worst = ranked[:n], ranked[-n:] if len(ranked) > 2 * n else []
    trimmed = total - sum(p for p, _ in best) - sum(p for p, _ in worst)
    return {"count": n, "best_days": sorted(d for _, d in best), "worst_days": sorted(d for _, d in worst),
            "total": total, "without_best": total - sum(p for p, _ in best), "trimmed": trimmed,
            "concentrated": bool(worst) and total > 0 and trimmed <= 0,
            "label": ("事后收益集中度诊断：按记录盈亏做金额相减，不重新交易；趋势和凸性策略可能本来就依赖少数上行日，"
                      "不能单凭这一项给经济否决")}


# ---------------------------------------------------------------- claims

def _claim(sid, key, claim_type, text, estimand, unit, conditions, tolerances, primary=True):
    return {"claim_id": f"{sid}.{key}", "revision": 1, "status": "candidate", "confirmed": False,
            "origin": "deterministic_template", "claim_type": claim_type, "text": text, "estimand": estimand,
            "unit": unit, "comparison": {"reference": f"{sid}/参考窗口", "others": "declared_manifest"},
            "conditions": conditions, "tolerances": tolerances, "primary": primary,
            "seen_status": "historical_already_seen", "per_cell": [], "exceptions": [], "unknowns": [],
            "next_falsification": [], "confirmation": None, "parent_claim_revision": None}


def _settle(claim, applicable=True):
    states = [c["state"] for c in claim["per_cell"]]
    if not applicable:
        claim["claim_state"] = "not_applicable"
    elif "contradicted" in states and "supported" not in states:
        claim["claim_state"] = "contradicted_within_scope"
    elif "contradicted" in states:
        claim["claim_state"] = "contradicted_within_scope"
        claim["exceptions"] = [c["cell_id"] for c in claim["per_cell"] if c["state"] == "contradicted"]
    elif states and all(s == "supported" for s in states):
        claim["claim_state"] = "supported_within_scope"
    else:
        claim["claim_state"] = "insufficient"
    claim["diagnostic_state"] = claim["claim_state"]
    if not claim["confirmed"]:
        claim["claim_state"] = "unconfirmed"
    claim["claim_state_text"] = CLAIM_WORDS[claim["claim_state"]]
    return claim


def _claims(sid, own, reference, pairs, t):
    cap = f"期初 ¥{reference['opening']:,.0f}" if reference else "期初未知"
    base_cond = {"experiment_type": ["fresh_start"], "execution_model": "原回测引擎：整单全成或全拒",
                 "scope": f"同一段历史、样本内；{cap}"}
    out = []
    c1 = _claim(sid, "signal.premise", "signal_premise", "策略自己的排序前提：分数越高，随后收益越高",
                "rank_ic_mean_hac_t", "IC", {**base_cond, "test": "HAC t（M-PREMISE-HAC）"}, {"t_bar": 2.})
    premise_directions = {(c.get("premise") or {}).get("direction") for c in own if c.get("premise")}
    c1["direction"] = next(iter(premise_directions)) if len(premise_directions) == 1 else "unknown"
    c1["text"] = "策略自己的排序前提：" + ({"higher": "分数越高，随后收益越高", "lower": "分数越低，随后收益越高"}.get(
        c1["direction"], "分数方向尚未统一，需核对后检验"))
    c1["inference_role"] = "exploratory_historical_diagnostic"
    c1["primary"] = False
    c1["unknowns"] = ["本批窗口和方向未在观察历史前预声明；确认文本也不把这些历史转为确认性检验"]
    for c in own:
        if c["status"] != "ok":
            c1["per_cell"].append({"cell_id": c["run_id"], "state": "insufficient", "eligible_reason": c.get("reasons")})
            continue
        status = (c.get("premise") or {}).get("status")
        state = {"held": "supported", "reversed": "contradicted"}.get(status, "insufficient") if len(premise_directions) == 1 else "insufficient"
        c1["per_cell"].append({"cell_id": c["run_id"], "state": state, "premise_status": status or "needs_scores",
                               "effect": c.get("premise_overall")})
    c1["next_falsification"] = [{"test": "冻结评分、候选池与标签，在未参与选择的新历史上确认",
                                 "why_this_first": "现有窗口都是已看过的同一段历史"}]
    out.append(_settle(c1, applicable=any(c.get("has_scores") for c in own if c["status"] == "ok")))

    has_targets = any((c.get("overlay") or {}).get("targets") for c in own if c["status"] == "ok")
    layer = "target" if has_targets else "selection"
    c2 = _claim(sid, f"{layer}_policy.same_shared_dates", "target_policy_invariance" if has_targets else "selection_invariance",
                "同一天的组合目标不随回测起点改变" if has_targets else "同一天的排序和选择不随回测起点改变",
                "max_abs_weight_difference_on_shared_decisions" if has_targets else "selection_output_equality",
                "weight_fraction" if has_targets else "exact_match",
                {**base_cond, "policy_source": "run_optimization.weights" if has_targets else "traces 排名/筛选输出",
                 "complete_vector_required": has_targets}, {"numeric_abs": NUMERIC_TOL, "economic": None})
    for p in pairs:
        if p["status"] != "compared":
            c2["per_cell"].append({"cell_id": p.get("fresh_run"), "state": "insufficient", "eligible_reason": p.get("reason")})
            continue
        L = p["layers"][layer]
        state = {"observed_equal": "supported", "observed_different": "contradicted"}.get(L["state"], "insufficient")
        if L["state"] == "opening_state_only":
            c2["exceptions"].append({"cell_id": p["fresh_run"], "note": L["opening_note"]})
        c2["per_cell"].append({"cell_id": p["fresh_run"], "state": state, "matched": L.get("matched"),
                               "differing": L.get("differing"), "effect": L.get("max_d_inf")})
    c2["unknowns"] = ["相同目标不证明信号预测能力不变；只说明观测范围内的行为一致"]
    out.append(_settle(c2, applicable=any((c.get("overlay") or {}).get(k) for c in own for k in ("targets", "selections"))))

    c2b = _claim(sid, "mechanism.declared_inputs", "mechanism_uses_declared_inputs",
                 "规则确实使用了所声明的信息和参数；改变它会（或不会）改变目标", "target_change_under_input_transform",
                 "weight_fraction", base_cond, {"numeric_abs": NUMERIC_TOL})
    c2b["unknowns"] = ["本批只有原版，没有输入变换或消融对照；不能由“目标一致”推出机制成立"]
    c2b["next_falsification"] = [{"test": "做输入变换（如均值信号反号）或消融对照，比较同日目标",
                                  "required_inputs": ["消融或变换后的回测"], "expense_tier": "P1"}]
    out.append(_settle(c2b))

    c3a = _claim(sid, "account.books_reconcile", "account_reconciliation", "用已提交的成交、价格、费用和事件能重建该区间账户",
                 "accounting_status", "status", base_cond, {})
    for c in own:
        c3a["per_cell"].append({"cell_id": c["run_id"], "state": "supported" if c["status"] == "ok" else
                                ("contradicted" if c["status"] == "invalid" else "insufficient"),
                                "eligible_reason": c.get("reasons")})
    out.append(_settle(c3a))

    c3c = _claim(sid, "account.cross_start_path", "cross_start_path",
                 "换一个启动日期和期初状态，账户结果仍在容差内", "fresh_minus_continuous_return", "fraction",
                 {**base_cond, "comparison": "独立起跑 vs 参考窗口同期连续切片"}, {"abs": t["T-GRID-PATH-GAP"]})
    for p in pairs:
        if p["status"] != "compared":
            c3c["per_cell"].append({"cell_id": p.get("fresh_run"), "state": "insufficient", "eligible_reason": p.get("reason")})
            continue
        gap = p["fresh_return"] - p["continuous_return"]
        c3c["per_cell"].append({"cell_id": p["fresh_run"], "state": "supported" if abs(gap) <= t["T-GRID-PATH-GAP"] else "contradicted",
                                "effect": gap, "causes": p["causes"], "global_first_difference": p["global_first_difference"]})
    c3c["unknowns"] = ["这是描述性迁移，不是等条件复现；反驳它不等于反驳账目（C3a）或信号前提（C1）"]
    out.append(_settle(c3c))

    c4 = _claim(sid, "rule.marginal_effect", "rule_marginal_effect",
                "相同基线和条件下，保留某条规则比去掉它在净收益或风险上至少好一个经济门槛",
                "x11-x10-x01+x00", "fraction", base_cond, {"economic": None}, primary=False)
    c4["unknowns"] = ["本批只有原版；缺少同条件的消融格，不能给规则边际作用结论，也不能把其他年份的四格结果复制到每个窗口"]
    c4["next_falsification"] = [{"test": "对每条规则做四格（原版、只去A、只去B、都去），在同一组窗口、同一执行模型下重跑",
                                 "required_inputs": ["消融回测"], "expense_tier": "P1 受控实验"}]
    out.append(_settle(c4))
    by_run = {c["run_id"]: c for c in own}
    ref_id = reference and reference["run_id"]
    for claim in out:
        claim["comparison"]["reference"] = ref_id
        claim.setdefault("direction", {"account_reconciliation": "reconstruct",
                              "cross_start_path": "absolute_difference_within_tolerance",
                              "rule_marginal_effect": "improvement_above_threshold"}.get(claim["claim_type"], "invariance"))
        claim["evidence_refs"] = [{"run_id": c["run_id"], "evidence_sha256": c.get("evidence_sha256"),
                                   "input_digest": c.get("input_digest"),
                                   "overlay_files": (c.get("overlay") or {}).get("source_files", [])} for c in own]
        for item in claim["per_cell"]:
            ids = [item["cell_id"]] + ([ref_id] if ref_id and claim["claim_type"] not in
                                      ("signal_premise", "account_reconciliation") else [])
            item["source_refs"] = [r for r in claim["evidence_refs"] if r["run_id"] in ids]
            item["interval"] = None
            item["observed_conditions"] = {"experiment_type": by_run[item["cell_id"]].get("experiment_type"),
                                            "seen_status": by_run[item["cell_id"]].get("seen_status")}
        claim["history"] = []
    return out


# ---------------------------------------------------------------- strategy summary

def _check_state(s):
    if s["identity_issues"] or any(c["status"] == "invalid" for c in s["cells"]):
        return "invalid"
    if not s["reference"]:
        return "insufficient"
    triggered = any(p.get("account_state") == "observed_path_difference" for p in s["pairs"]) or \
        any(g.get("partition", {}).get("losing") or g.get("partition", {}).get("concentrated") for g in s["groups"]) or \
        s["top_days"].get("concentrated")
    if triggered:
        return "triggered"
    if any(c["status"] != "ok" for c in s["cells"]):
        return "insufficient"
    return "not_triggered"


def _verdict(s):
    if s["check_state"] == "invalid":
        bad = [f"{c['window_id']}：{'；'.join(c.get('reasons') or [])}" for c in s["cells"] if c["status"] == "invalid"]
        return "材料无效：" + "；".join(s["identity_issues"] + bad)
    if not s["reference"]:
        return "没有唯一的参考窗口（清单里需要恰好一格 reference）：不做配对比较"
    notes = []
    moved = [p for p in s["pairs"] if p.get("account_state") == "observed_path_difference"]
    if moved:
        worst = max(moved, key=lambda p: abs(p["fresh_minus_continuous_pp"]))
        notes.append(f"{len(moved)} 个窗口独立起跑与连续路径之差超过容差，最大是 {worst['fresh_run']}"
                     f"（{worst['fresh_minus_continuous_pp']:+.2f} 个百分点；可能原因：{'、'.join(worst['causes'])}）")
    for g in s["groups"]:
        p = g.get("partition")
        if p and p["losing"]:
            notes.append(f"{g['group']} 里 {'、'.join(p['losing'])} 亏损")
    if s["top_days"].get("concentrated"):
        notes.append("参考窗口去掉最好和最差各几天后不再盈利（事后集中度诊断）")
    if not notes:
        notes.append("所列检查未触发；这只说明这些描述性检查没有报警，不是经济上的通过，也不是样本外验证")
    return "；".join(notes)


def analyze(cells, thresholds=None):
    """Grid findings per strategy; nothing ranks one strategy against another."""
    t = {**defaults(), **(thresholds or {})}
    strategies = []
    for sid in sorted({c["strategy_id"] for c in cells}):
        own = sorted((c for c in cells if c["strategy_id"] == sid), key=lambda c: c["window_id"])
        refs = [c for c in own if c["is_reference"] and c["status"] == "ok"]
        reference = refs[0] if len(refs) == 1 else None
        issues, verified, unknown = _identity(own)
        pairs = []
        if reference:
            for c in own:
                if c is reference or c.get("experiment_type") != "fresh_start":
                    continue
                pairs.append(_pair(c, reference, t) if c["status"] == "ok" else
                             {"status": "not_comparable", "fresh_run": c["run_id"], "reason": "；".join(c.get("reasons") or [])})
        s = {"strategy_id": sid, "strategy_name": next((c["strategy_name"] for c in own if c["strategy_name"]), ""),
             "reference": reference and reference["run_id"],
             "reference_status": "ok" if reference else ("ambiguous" if len(refs) > 1 else "unavailable"),
             "identity_issues": issues, "verified_dimensions": verified, "unknown_dimensions": unknown,
             "pairs": pairs, "coverage": _coverage(own, pairs)}
        s["groups"] = _groups(own, reference)
        for g in s["groups"]:
            if g["kind"] == "partition":
                g["partition"] = _partition([c for c in own if c["group"] == g["group"]], reference, t)
        s["top_days"] = _top_days(reference, t) if reference else {"concentrated": False}
        s["claims"] = _claims(sid, own, reference, pairs, t)
        s["cells"] = [_public(c, t) for c in own]
        s["check_state"] = _check_state(s)
        s["check_state_text"] = CHECK_WORDS[s["check_state"]]
        s["verdict"] = _verdict(s)
        strategies.append(s)
    return {"method": METHOD, "evidence": "E4", "strategies": strategies, "experiments": EXPERIMENTS,
            "rules": cite(*GRID_RULES), "thresholds": {k: t[k] for k in GRID_RULES if k.startswith("T-")},
            "numeric_tolerance": NUMERIC_TOL,
            "scope": ("同一段历史按窗口重跑，样本内，已看过。连续切片从参考窗口派生，不是新回测；重叠窗口不是独立重复。"
                      "“所列检查未触发”不是经济上的通过；主张在用户确认前都是候选。不做跨策略排名。"
                      "报告的收益是回测实际路径；审计重算情景单列。")}


INTERNAL = ("dates", "equity", "equity_recost", "held", "weights", "orders", "overlay")


def _public(c, t):
    out = {k: v for k, v in c.items() if k not in INTERNAL}
    out["overlay_status"] = (c.get("overlay") or {}).get("status")
    ret = c.get("return_reported")
    out["annual_reported"] = (1 + ret) ** (252 / c["sessions"]) - 1 if ret is not None and c.get("sessions") and ret > -1 else None
    out["short_window"] = bool(c.get("sessions") and c["sessions"] < t["T-GRID-MIN-SESSIONS"])
    return out


# ---------------------------------------------------------------- outputs

CELL_COLUMNS = ("strategy_id", "strategy_name", "window_id", "run_id", "group", "experiment_type", "is_reference",
                "status", "start", "end", "sessions", "opening", "return_reported", "return_recost", "annual_reported",
                "short_window", "drawdown_reported", "overlay_status", "input_digest", "code_digest", "evidence_sha256")
PAIR_COLUMNS = ("strategy_id", "fresh_run", "slice_of", "status", "start", "end", "sessions", "fresh_return",
                "continuous_return", "fresh_minus_continuous_pp", "recost_fresh_minus_continuous_pp", "mechanism",
                "target_state", "account_state", "causes", "global_first_difference", "first_observed_signal_difference",
                "first_target_difference", "first_instruction_difference", "first_execution_outcome_difference",
                "first_position_difference", "first_return_difference", "material_episodes", "reason")


def write(result, out):
    """All grid outputs; the directory must be new or empty."""
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"{out} 已存在且不为空；网格审计输出不覆盖旧结果")
    out.mkdir(parents=True, exist_ok=True)
    clean = lambda o: _clean(o)
    (out / "grid.json").write_text(json.dumps(clean(result), ensure_ascii=False, indent=1), encoding="utf-8")
    strategies = result["strategies"]
    _csv(out / "cells.csv", CELL_COLUMNS, [c for s in strategies for c in s["cells"]])
    pairs = []
    for s in strategies:
        for p in s["pairs"]:
            pairs.append({**p, "strategy_id": s["strategy_id"], "causes": "、".join(p.get("causes") or []),
                          "material_episodes": "、".join(p.get("material_episodes") or []),
                          "recost_fresh_minus_continuous_pp": (p.get("recost") or {}).get("fresh_minus_continuous_pp")})
    _csv(out / "pair_summaries.csv", PAIR_COLUMNS, pairs)
    _csv(out / "paired_daily.csv", ("strategy_id", "fresh_run", "date", "fresh_return_bp", "slice_return_bp", "gap_bp",
                                    "scaled_cumulative_gap", "holdings_differ", "weight_l1"),
         [{"strategy_id": s["strategy_id"], "fresh_run": p["fresh_run"], **d} for s in strategies for p in s["pairs"]
          for d in p.get("paired_daily") or []])
    with (out / "divergence_events.jsonl").open("w", encoding="utf-8") as f:
        for s in strategies:
            for p in s["pairs"]:
                for e in p.get("episodes") or []:
                    f.write(json.dumps(clean({"strategy_id": s["strategy_id"], "pair": [p["fresh_run"], p["slice_of"]], **e}),
                                       ensure_ascii=False) + "\n")
    _csv(out / "pnl_bridges.csv", ("strategy_id", "fresh_run", "episode", "start", "end", "gap_before", "gap_during",
                                   "gap_after", "total", "label"),
         [{"strategy_id": s["strategy_id"], "fresh_run": p["fresh_run"], "episode": e["id"], "start": e["start"],
           "end": e["end"], "gap_before": e["gap_before"], "gap_during": e["gap_during"], "gap_after": e["gap_after"],
           "total": p["bridge"]["total"], "label": e["label"]} for s in strategies for p in s["pairs"] for e in p.get("episodes") or []])
    claims = [c for s in strategies for c in s["claims"]]
    (out / "claim_records.json").write_text(json.dumps(clean(claims), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "normalized_target_weights.json").write_text(json.dumps(clean([
        {"run_id": c["run_id"], "parent_bundle_digest": c.get("parent_bundle_digest"),
         "overlay_digest": c.get("overlay_digest"), "audit_bundle_digest": c.get("audit_bundle_digest"),
         "target_weights": c.get("normalized_target_weights", [])}
        for s in strategies for c in s["cells"]]), ensure_ascii=False, indent=1), encoding="utf-8")
    _csv(out / "claim_evidence.csv", ("claim_id", "claim_state", "diagnostic_state", "cell_id", "state", "effect", "detail"),
         [{"claim_id": c["claim_id"], "claim_state": c["claim_state"], "diagnostic_state": c.get("diagnostic_state"),
           "cell_id": e.get("cell_id"), "state": e.get("state"),
           "effect": e.get("effect") if not isinstance(e.get("effect"), dict) else json.dumps(clean(e["effect"]), ensure_ascii=False),
           "detail": json.dumps(clean({k: v for k, v in e.items() if k not in ("cell_id", "state", "effect")}), ensure_ascii=False)}
          for c in claims for e in c["per_cell"]])
    nexts = [{"strategy_id": s["strategy_id"], "fresh_run": p["fresh_run"], **n} for s in strategies for p in s["pairs"]
             for n in p.get("next_falsification") or []]
    (out / "next_experiments.json").write_text(json.dumps(clean(nexts), ensure_ascii=False, indent=1), encoding="utf-8")
    relations = [{"strategy_id": s["strategy_id"], "runs": [c["run_id"] for c in s["cells"]],
                  "derived_slices": [{"slice_of": p["slice_of"], "dates_of": p["fresh_run"]} for p in s["pairs"] if p["status"] == "compared"],
                  "groups": [{k: g[k] for k in ("group", "kind", "windows")} for g in s["groups"]], "coverage": s["coverage"]}
                 for s in strategies]
    (out / "experiment_relations.json").write_text(json.dumps(clean(relations), ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "eligibility.json").write_text(json.dumps(clean([{"run_id": c["run_id"], "status": c["status"], "reasons": c.get("reasons")}
                                                            for s in strategies for c in s["cells"]]), ensure_ascii=False, indent=1),
                                          encoding="utf-8")
    (out / "source_semantics.json").write_text(json.dumps(sources.SEMANTICS, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "method_versions.json").write_text(json.dumps({"grid": METHOD, "sources": sources.ADAPTER,
                                                          "thresholds": result["thresholds"], "numeric_tolerance": NUMERIC_TOL},
                                                         ensure_ascii=False, indent=1), encoding="utf-8")
    from .audit_grid_claims import review_template
    (out / "claim_review_template.json").write_text(json.dumps(review_template(out / "grid.json"), ensure_ascii=False, indent=1),
                                                  encoding="utf-8")
    (out / "report.html").write_text(_html(result), encoding="utf-8")
    return sorted(p.name for p in out.iterdir())


def _clean(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set, frozenset)):
        return [_clean(v) for v in (sorted(o) if isinstance(o, (set, frozenset)) else o)]
    return o


def _csv(path, columns, rows):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r[k]) for k in columns})


def _html(result):
    from html import escape as e
    pct = lambda v: "" if v is None else f"{v:.2%}"
    pp = lambda v: "" if v is None else f"{v:+.2f}"
    body = [f"<h1>多窗口审计</h1><p>{e(result['scope'])}</p>"]
    for s in result["strategies"]:
        cov = s["coverage"]
        body.append(f"<h2>{e(s['strategy_id'])} {e(s['strategy_name'])}</h2>"
                    f"<p><b>{e(s['check_state_text'])}</b>：{e(s['verdict'])}</p>"
                    f"<p>{cov['run_count']} 个回测，{cov['derived_slice_count']} 个派生切片；窗口交易日 {cov['window_sessions']}、"
                    f"不同交易日 {cov['distinct_sessions']}。{e(cov['note'])}</p>"
                    "<p>已核：" + e("；".join(f"{d['label']} {'相同' if d['status'] == 'same' else '不同'}" for d in s["verified_dimensions"]) or "无")
                    + "。未知：" + e("、".join(d["label"] for d in s["unknown_dimensions"]) or "无") + "</p>")
        body.append("<h3>主张与独立诊断</h3><p>在 claim_review_template.json 中选择要复核的主张，填写确认或修改动作，再用 audit claim-review 生成新版本；原报告保留。</p>"
                    "<table border=1 cellpadding=4><tr><th>主张</th><th>确认状态 / 诊断</th>"
                    "<th>支持</th><th>反驳</th><th>不足</th><th>未知</th></tr>")
        for c in s["claims"]:
            by = lambda st: "、".join(x["cell_id"].split("/")[-1] for x in c["per_cell"] if x["state"] == st)
            diag = CLAIM_WORDS.get(c.get('diagnostic_state'), '证据不足')
            body.append(f"<tr><td>{e(c['text'])}</td><td>{e(c['claim_state_text'])} / {e(diag)}（v{c['revision']}）</td><td>{e(by('supported'))}</td>"
                        f"<td>{e(by('contradicted'))}</td><td>{e(by('insufficient'))}</td><td>{e('；'.join(c['unknowns']))}</td></tr>")
        body.append("</table><h3>独立起跑 vs 连续路径切片</h3><table border=1 cellpadding=4><tr><th>窗口</th><th>独立起跑</th>"
                    "<th>连续切片</th><th>差（百分点）</th><th>目标/选择</th><th>账户</th><th>可能原因</th><th>最早差异</th><th>下一项检验</th></tr>")
        for p in s["pairs"]:
            if p["status"] != "compared":
                body.append(f"<tr><td>{e(str(p.get('fresh_run')))}</td><td colspan=8>{e(p.get('reason') or '')}</td></tr>")
                continue
            nxt = "；".join(n["test"] for n in p["next_falsification"])
            body.append(f"<tr><td>{e(p['fresh_run'].split('/')[-1])}</td><td>{pct(p['fresh_return'])}</td><td>{pct(p['continuous_return'])}</td>"
                        f"<td>{pp(p['fresh_minus_continuous_pp'])}</td><td>{e(p['target_state'])}</td><td>{e(p['account_state'])}</td>"
                        f"<td>{e('、'.join(p['causes']))}</td><td>{e(p['global_first_difference'] or '')}</td><td>{e(nxt)}</td></tr>")
        body.append("</table>")
        for p in s["pairs"]:
            for ep in p.get("episodes") or []:
                if not ep["material"]:
                    continue
                ev = "；".join(f"{x['date']} {x['code']} {x['side']}：独立起跑 {x['fresh_outcome']}，连续 {x['slice_outcome']}"
                              for x in ep["execution_events"])
                body.append(f"<p><b>{e(ep['id'])}</b> {ep['start']} 至 {ep['end']}（{ep['sessions']} 日"
                            f"{'，至窗口末未收敛' if ep['right_censored'] else ''}）；持仓 {e('、'.join(ep['fresh_holds']))} 对 "
                            f"{e('、'.join(ep['slice_holds']))}；{e(ep['label'])}：期间 ¥{ep['gap_during']:,.0f}。{e(ev)}</p>")
    return ('<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>多窗口审计</title></head><body>'
            + "".join(body) + "</body></html>")
