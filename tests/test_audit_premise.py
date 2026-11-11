"""Strategy premise: identified from code or the user, checked against candidate returns."""
import random

from qwb import audit_premise


CODE = '''"""Docstring mentions momentum but computes nothing."""
LOOKBACK = 20

def rebalance(context, bar_dict):
    scores = {}
    for code in POOL:
        closes = history_bars(code, LOOKBACK + 1, "1d", "close")
        scores[code] = closes[-1] / closes[0] - 1
    ranked = sorted(scores, key=scores.get, reverse=True)
'''


def bundle(edge, days=60, names=12, seed=1):
    """Scores that predict the next period's return by `edge` (0 = noise, negative = reversed)."""
    rng = random.Random(seed)
    codes = [f"60000{i:02d}.SH" for i in range(names)]
    price = {c: 10. for c in codes}
    scores, prices = [], []
    for d in range(days):
        day = f"20{20 + d // 30}-{1 + d // 30 % 12:02d}-{1 + d % 28:02d}"
        score = {c: rng.gauss(0, 1) for c in codes}
        top = sorted(codes, key=score.get, reverse=True)[:3]
        scores += [{"date": day, "code": c, "score": score[c], "selected": c in top} for c in codes]
        prices += [{"date": day, "code": c, "close": price[c]} for c in codes]
        for c in codes:
            price[c] *= 1 + .002 + edge * score[c] + rng.gauss(0, .02)
    return {"scores": scores, "prices": prices, "code": {"source": CODE}}


def test_factor_and_direction_come_from_code_not_docstring():
    found = audit_premise.from_code({"code": {"source": CODE}})
    assert found["direction"] == "higher" and found["lookback"] == 20
    assert found["factors"][0]["key"] == "momentum"
    assert found["factors"][0]["line"] == 8          # the computation, not the docstring on line 1


def test_embedded_original_strategy_is_read():
    wrapper = "SOURCE = " + repr("def MOM(etf):\n    y = 1\n    df.sort_values(by='score', ascending=False)\n") + "\n"
    found = audit_premise.from_code({"code": {"source": wrapper}})
    assert found["embedded"] and found["factors"][0]["key"] == "momentum" and found["direction"] == "higher"


def test_true_premise_holds_with_size_and_periods():
    result = audit_premise.analyze(bundle(.01))
    assert result["status"] == "held"
    assert result["source"] == "从代码和策略选股行为识别"
    assert result["premise"].startswith("过去20日动量越高")
    assert result["spread_mean"] > 0 and result["spread_t"] > 2
    assert {s["label"] for s in result["by_year"]} == {"2020年", "2021年"}
    assert [s["label"] for s in result["by_market"]] == ["候选池整体上涨的周期", "候选池整体下跌的周期"]
    # Each year has fewer than 30 signal periods: the IID t is shown as a diagnostic, but no HAC t and no verdict.
    assert all(s["t"] is None and s["iid_t"] > 2 and s["status"] == "too_few" for s in result["by_year"])
    assert result["held_years"] == []
    assert result["overall"]["hac_lag"] == 3 and result["overall"]["t"] > 2


def test_reversed_and_noise_are_told_apart():
    assert audit_premise.analyze(bundle(-.01))["status"] == "reversed"
    assert audit_premise.analyze(bundle(0., seed=2))["status"] == "not_supported"


def test_user_premise_overrides_and_code_alone_cannot_be_checked():
    result = audit_premise.analyze(bundle(.01), {"text": "低估值会修复", "direction": "lower"})
    assert result["source"] == "用户填写" and result["premise"] == "低估值会修复"
    assert result["status"] == "reversed"            # the user's direction is tested as stated
    only_code = audit_premise.analyze({"scores": [], "prices": [], "code": {"source": CODE}})
    assert only_code["status"] == "needs_scores"


def test_partition_segments_are_reported():
    data = bundle(.01)
    days = sorted({s["date"] for s in data["scores"]})
    part = {"status": "planned", "development_start": days[0], "development_end": days[39],
            "validation_start": days[40], "validation_end": days[-1]}
    result = audit_premise.analyze(data, partition=part)
    # The 40th development period's label ends inside validation, so it counts in neither segment.
    assert result["segments"]["development"]["periods"] == 39
    assert result["segments"]["development"]["boundary_excluded"] == 1
    assert result["segments"]["validation"]["label"] == "验证段"


def test_hac_keeps_missing_signal_periods_and_slices_are_exploratory():
    import math
    rows = [{"period_index": i, "ic": math.sin(i / 7) + .1} for i in range(70) if i not in (20, 21, 22)]
    values = audit_premise._period_values(rows)
    result = audit_premise._summary(values)
    assert len(values) == 70 and result["periods"] == 67 and result["missing_period_slots"] == 3
    from qwb.metrics import newey_west
    assert result["t"] == newey_west(values, result["hac_lag"])["t"]
    assert result["t"] != audit_premise._summary([r["ic"] for r in rows])["t"]
    diag = audit_premise.analyze(bundle(.01))
    assert diag["inference"]["role"] == "exploratory" and not diag["inference"]["formal_rejection"]


def test_attribution_page_answers_the_three_questions():
    from qwb import strategy_audit_ui as ui
    html = ui.premise_section({"modules": {"premise": audit_premise.analyze(bundle(.01))}})
    for words in ("对不对", "对多少", "哪段时间对", "按年份", "按市场涨跌", "折合每年"):
        assert words in html
    noise = ui.premise_section({"modules": {"premise": audit_premise.analyze(bundle(0., seed=2))}})
    assert "不宜折成年化" in noise


def test_exported_report_has_professional_sections_and_benchmark():
    from qwb import audit_report, strategy_audit as audit, strategy_audit_ui as view, strategy_imports as imp
    bundle = imp.normalize(view.sample_files(), name="Synthetic audit", opening_cash=10000)
    first = bundle["snapshots"][0]["equity"]
    bundle["platform_returns"] = [{"date": s["date"], "strategy_pct": (s["equity"] / first - 1) * 100,
                                   "benchmark_pct": i * .1} for i, s in enumerate(bundle["snapshots"])]
    result = audit.analyze(bundle, {"commission_bp": 3, "minimum_commission": 5, "max_weight": 1.})
    html = audit_report.render({"created_at": "2026-01-01T00:00:00"}, bundle, result)
    for title in ("一、结论摘要", "二、策略前提检验", "三、收益与风险", "四、盈亏归因", "五、成本与执行",
                  "六、发现与建议", "七、审计范围与限制", "附录 A", "基准收益", "超额收益"):
        assert title in html
    assert result["input_digest"] in html and "不构成投资建议" in html


def test_export_report_supports_candidate_scores_without_selection_flags():
    from qwb import audit_report, strategy_audit, strategy_audit_ui, strategy_imports
    data = strategy_imports.normalize(strategy_audit_ui.sample_files(), opening_cash=10000)
    result = strategy_audit.analyze(data, {"commission_bp": 3., "minimum_commission": 5., "max_weight": 1.})
    candidates = bundle(.01)
    for row in candidates["scores"]:
        row.pop("selected", None)
    result["modules"]["premise"] = audit_premise.analyze(candidates)
    assert result["modules"]["premise"]["pool_compound"] is not None
    assert result["modules"]["premise"]["selected_compound"] is None
    html = audit_report.render({"created_at": "2026-11-04T00:00:00"}, data, result)
    assert "未提供选择标记" in html and "收益来源" in html


def test_weak_sample_says_not_detected_is_not_wrong():
    result = audit_premise.analyze(bundle(0., seed=2))
    assert result["status"] == "not_supported" and result["power"]
    assert "不等于前提错了" in result["verdict"] and result["power"]["mde"] > 0
