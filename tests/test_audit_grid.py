"""`qwb audit grid`: claims across start dates, five-layer pairing, and what the evidence cannot say.

Every run here is a synthetic audit output written to disk; nothing executes strategy code.
"""
import csv
import json

import pytest

from qwb import audit_grid, audit_grid_sources as sources
from qwb.cli import main

DAYS = [f"2024-01-{d:02d}" for d in (2, 3, 4, 5, 8, 9, 10, 11)]


def write_run(root, name, dates, equity, opening, holds, orders=(), traces=None, requests=(), status="verified",
              digest=None, code="x = 1\n"):
    """An audited run on disk: evidence.json, plus a native folder turned into a source overlay."""
    audit, native, overlay = root / name / "audit", root / name / "native", root / name / "overlay"
    audit.mkdir(parents=True)
    native.mkdir()
    prev, daily, snaps, positions = opening, [], [], []
    for d, e in zip(dates, equity):
        daily.append({"date": d, "net_pnl": e - prev, "fees": 0.})
        mv = sum(v for _, v in holds.get(d, {}).values())
        snaps.append({"date": d, "equity": e, "cash": e - mv})
        positions += [{"date": d, "code": c, "quantity": q, "market_value": v} for c, (q, v) in holds.get(d, {}).items()]
        prev = e
    result = {"mode": "full", "input_digest": digest or f"sha256:{name}",
              "settings": {"commission_bp": 3., "minimum_commission": 5.},
              "modules": {"accounting": {"status": status, "baseline_equity": opening, "daily": daily}}}
    bundle = {"code": {"source": code}, "snapshots": snaps, "positions": positions,
              "orders": [{"time": "09:31", "reason": "", **o, "ref": {"source": "orders", "row": i + 2}} for i, o in enumerate(orders)]}
    (audit / "evidence.json").write_text(json.dumps({"input": bundle, "result": result}), encoding="utf-8")
    if traces is not None:
        (native / "traces.json").write_text(json.dumps(traces), encoding="utf-8")
    with (native / "requests.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "time", "code", "target_value", "side", "quantity", "price", "mid", "reason", "cash_before", "order_id"])
        for r in requests:
            w.writerow(r)
    sources.write_overlay(native, name, overlay)
    return audit, overlay


def manifest(root, rows):
    path = root / "manifest.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy_id", "window_id", "group", "reference", "experiment_type", "audit_dir", "overlay_dir"])
        for r in rows:
            w.writerow(r)
    return path


def grid(root, rows):
    return audit_grid.analyze([audit_grid.load_cell(r) for r in audit_grid.read_manifest(manifest(root, rows))])


OPT = lambda day, weights: {"date": day, "time": "09:30", "function": "run_optimization", "input": ["A.SH", "B.SH"],
                            "output": weights, "weights": weights}


def execution_case(root, with_targets=True):
    """Same policy target; the reference account's sell of A is rejected, the fresh account's is filled."""
    ref_holds = {d: {"A.SH": (1000, 1e6)} for d in DAYS}
    ref, ref_ov = write_run(root, "s1/W00", DAYS, [1e6] * 8, 1e6, ref_holds,
                            orders=[{"date": DAYS[5], "code": "A.SH", "side": "sell", "quantity": 1000, "filled": 0, "status": "rejected",
                                     "reason": "超过当根成交量上限"}],
                            traces=[OPT(DAYS[0], [1., 0.]), OPT(DAYS[5], [0., 1.])] if with_targets else None)
    fresh_eq = [1e6, 1e6, 1.01e6, 1.0201e6]
    fresh_holds = {DAYS[4]: {"A.SH": (1000, 1e6)}, **{d: {"B.SH": (1000, e)} for d, e in zip(DAYS[5:], fresh_eq[1:])}}
    fresh, fresh_ov = write_run(root, "s1/W01", DAYS[4:], fresh_eq, 1e6, fresh_holds,
                                orders=[{"date": DAYS[4], "code": "A.SH", "side": "buy", "quantity": 1000, "filled": 1000, "status": "filled"},
                                        {"date": DAYS[5], "code": "A.SH", "side": "sell", "quantity": 1000, "filled": 1000, "status": "filled"},
                                        {"date": DAYS[5], "code": "B.SH", "side": "buy", "quantity": 1000, "filled": 1000, "status": "filled"}],
                                traces=[OPT(DAYS[5], [0., 1.])] if with_targets else None)
    return grid(root, [["s1", "W00", "reference", "true", "fresh_start", ref, ref_ov],
                       ["s1", "W01", "later", "false", "fresh_start", fresh, fresh_ov]])


# ---------------------------------------------------------------- sources (A06, A07)

def test_observed_weights_never_become_targets(tmp_path):
    native = tmp_path / "native"
    native.mkdir()
    (native / "targets.csv").write_text("date,A.SH\n2024-01-02,0.4\n", encoding="utf-8")
    written = sources.write_overlay(native, "r/W", tmp_path / "ov")
    assert "target_snapshots" not in written and not (tmp_path / "ov" / "target_snapshots.csv").exists()
    kinds = {s["artifact"]: s for s in json.loads((tmp_path / "ov" / "source_semantics.json").read_text(encoding="utf-8"))}
    assert kinds["native_grid/targets.csv"]["semantic_kind"] == "observed_position_weight"
    assert "作为目标权重" in kinds["native_grid/targets.csv"]["forbidden_uses"]


def test_policy_target_comes_from_the_optimization_trace_with_its_clock(tmp_path):
    native = tmp_path / "native"
    native.mkdir()
    (native / "traces.json").write_text(json.dumps([OPT("2023-11-01", [.45, .55]),
                                                    {**OPT("2023-11-02", [.7, .7])}]), encoding="utf-8")
    written = sources.write_overlay(native, "r/W", tmp_path / "ov")
    snaps = sources.read_targets(tmp_path / "ov" / "target_snapshots.csv")
    assert written["target_snapshots"] == 1 and snaps[0]["weights"] == {"A.SH": .45, "B.SH": .55}
    assert snaps[0]["available_at"] == "2023-11-01T09:30:00+08:00" and snaps[0]["cash"] == pytest.approx(0.)
    assert written["invalid_targets"][0]["reason"] == "权重之和超过 1"      # listed, never normalised


def test_target_overlay_enters_normalized_model_and_has_a_new_bundle_identity(tmp_path):
    from qwb import strategy_imports
    native = tmp_path / "native"
    native.mkdir()
    (native / "traces.json").write_text(json.dumps([{**OPT(DAYS[0], [.45, .55]),
                                                    "input": ["510300.SH", "159915.SZ"]}]), encoding="utf-8")
    sources.write_overlay(native, "r/W", tmp_path / "ov")
    overlay = audit_grid._overlay(tmp_path / "ov")
    rows = overlay["normalized_target_weights"]
    assert len(rows) == 3 and rows[0]["decision_id"] == "r/W#traces:0"
    assert rows[0]["available_at"] == DAYS[0] + "T09:30:00+08:00" and rows[0]["complete"]
    # Verify the public importer uses the same metadata contract, rather than merely generating a CSV.
    files = [strategy_imports.SourceFile("strategy.py", b"x=1\n"),
             strategy_imports.SourceFile("target_snapshots.csv", (tmp_path / "ov/target_snapshots.csv").read_bytes())]
    bundle = strategy_imports.normalize(files, adapter="generic")
    assert len(bundle["target_weights"]) == 3
    assert bundle["target_weights"][0]["available_at"] == rows[0]["available_at"]
    assert bundle["target_weights"][0]["decision_id"] == rows[0]["decision_id"]
    assert bundle["target_weights"][-1]["code"] == "CASH"
    case_root = tmp_path / "case"
    case_root.mkdir()
    cell = execution_case(case_root)["strategies"][0]["cells"][0]
    assert cell["audit_bundle_digest"] != cell["parent_bundle_digest"] and cell["overlay_digest"]
    assert cell["normalized_target_weights"]


def test_legacy_targets_remain_clock_unknown_and_distinct_decisions_are_not_overwritten():
    from qwb import strategy_imports
    raw = b"date,code,target_weight,decision_id\n2024-01-02,510300.SH,0.2,first\n2024-01-02,510300.SH,0.3,second\n"
    b = strategy_imports.normalize([strategy_imports.SourceFile("strategy.py", b"x=1\n"),
                                    strategy_imports.SourceFile("target_weights.csv", raw)], adapter="generic")
    assert [r["decision_id"] for r in b["target_weights"]] == ["first", "second"]
    assert all(r["availability_status"] == "unknown" and r["complete"] is None for r in b["target_weights"])


def test_ambiguous_target_decisions_do_not_become_equality():
    s = {"date": DAYS[0], "time": "09:30", "decision_id": "a", "complete": True, "weights": {"A.SH": 1.}}
    layer = audit_grid._compare_targets([s, {**s, "decision_id": "b"}], [s], DAYS)
    assert layer["state"] == "incomplete" and layer["ambiguous"] == 1


def test_opening_day_does_not_excuse_a_ranking_difference():
    key = (DAYS[0], "get_rank", "same_input")
    layer = audit_grid._compare_selections({key: {"output": ["A.SH"], "ref": "a"}},
                                          {key: {"output": ["B.SH"], "ref": "b"}}, DAYS)
    assert layer["state"] == "observed_different" and layer["first_difference"] == DAYS[0]


def test_missing_target_clock_is_unknown_and_never_filled_in_as_midnight():
    rows, invalid = sources.target_snapshots([{**OPT(DAYS[0], [.4, .6]), "time": ""}], "r/W", "traces.json", "hash")
    assert not invalid and rows[0]["available_at"] == "" and rows[0]["time_precision"] == "date_only"
    assert rows[0]["persistence_policy"] == "unknown" and rows[0]["valid_until"] == ""
    s = {"date": DAYS[0], "time": "", "complete": True, "weights": {"A.SH": 1.}}
    assert audit_grid._compare_targets([s], [s], DAYS)["state"] == "incomplete"


def test_repeated_selection_calls_are_preserved_and_incomplete_branch_inputs_block_equality(tmp_path):
    native = tmp_path / "native"
    native.mkdir()
    traces = [{"date": DAYS[0], "time": "09:30", "function": "evaluate_etf_worth", "input": "518880.XSHG", "output": x}
              for x in (0, 1)]
    (native / "traces.json").write_text(json.dumps(traces), encoding="utf-8")
    sources.write_overlay(native, "r/W", tmp_path / "ov")
    observed = sources.read_selections(tmp_path / "ov/selections.csv")
    entry = next(iter(observed.values()))
    assert [r["output"] for r in entry["records"]] == [0, 1]
    assert entry["ambiguous"] and not entry["input_complete"] and entry["output"] is None
    layer = audit_grid._compare_selections(observed, observed, DAYS)
    assert layer["state"] == "incomplete" and layer["matched"] == 0
    assert layer["ambiguous_or_incomplete_inputs"] == 1 and layer["unsafe_examples"]


# ---------------------------------------------------------------- pairing (A08, A09, A13)

def test_same_target_different_execution_is_located_layer_by_layer(tmp_path):
    s = execution_case(tmp_path)["strategies"][0]
    p = s["pairs"][0]
    assert p["continuous_return"] == pytest.approx(0.) and p["fresh_return"] == pytest.approx(.0201)
    assert p["fresh_minus_continuous_pp"] == pytest.approx(2.01)
    assert p["target_state"] == "observed_equal" and p["mechanism"] == "behavioral_consistency_only"
    assert p["first_execution_outcome_difference"] == DAYS[5] and p["first_position_difference"] == DAYS[5]
    # The earliest observed difference is the fresh account's opening buy, not the key episode.
    assert p["global_first_difference"] == DAYS[4] == p["first_instruction_difference"]
    assert p["causes"] == ["observed_execution_path_difference"]
    ep = p["episodes"][0]
    assert ep["start"] == DAYS[5] and ep["right_censored"] and ep["material"]
    assert ep["execution_events"][0] == {"date": DAYS[5], "code": "A.SH", "side": "sell",
                                         "fresh_outcome": "filled", "slice_outcome": "not_filled"}
    assert "不是某一笔拒单造成的损失" in ep["label"]
    assert p["bridge"]["closes"] and p["bridge"]["total"] == pytest.approx(20100.)
    assert "部分成交" in p["next_falsification"][0]["test"]


def test_hiding_the_target_record_widens_the_cause_instead_of_guessing(tmp_path):
    p = execution_case(tmp_path, with_targets=False)["strategies"][0]["pairs"][0]
    assert p["target_state"] != "observed_equal" and p["mechanism"] == "insufficient"
    assert p["causes"] == ["insufficient_to_localize"]


def test_an_opening_entry_gap_is_not_called_an_execution_failure(tmp_path):
    ref_eq = [1e6, 1e6, 1e6, 1e6, 1.02e6, 1.02e6, 1.03e6, 1.03e6]
    holds = lambda dates, eq: {d: {"A.SH": (1000, e)} for d, e in zip(dates, eq)}
    ref, ro = write_run(tmp_path, "s1/W00", DAYS, ref_eq, 1e6, holds(DAYS, ref_eq))
    fresh_eq = [1e6, 1e6, 1e6 * 1.03 / 1.02, 1e6 * 1.03 / 1.02]
    fresh, fo = write_run(tmp_path, "s1/W01", DAYS[4:], fresh_eq, 1e6, holds(DAYS[4:], fresh_eq),
                          orders=[{"date": DAYS[4], "code": "A.SH", "side": "buy", "quantity": 1000, "filled": 1000, "status": "filled"}])
    p = grid(tmp_path, [["s1", "W00", "reference", "true", "fresh_start", ref, ro],
                        ["s1", "W01", "later", "false", "fresh_start", fresh, fo]])["strategies"][0]["pairs"][0]
    assert p["account_state"] == "observed_path_difference" and p["layers"]["account"]["positions_differ_sessions"] == 0
    assert p["causes"] == ["opening_entry_difference"]


# ---------------------------------------------------------------- states, claims, coverage (A10, A11, A14, A16-A18)

def matched_case(root):
    holds = {d: {"A.SH": (1000, 1e6)} for d in DAYS}
    ref, ro = write_run(root, "s1/W00", DAYS, [1e6] * 8, 1e6, holds)
    fresh, fo = write_run(root, "s1/W01", DAYS[4:], [1e6] * 4, 1e6, {d: holds[d] for d in DAYS[4:]})
    return [["s1", "W00", "reference", "true", "fresh_start", ref, ro], ["s1", "W01", "later", "false", "fresh_start", fresh, fo]]


def test_nothing_triggered_is_not_an_economic_pass(tmp_path):
    s = grid(tmp_path, matched_case(tmp_path))["strategies"][0]
    assert s["check_state"] == "not_triggered" and "不是经济上的通过" in s["verdict"]
    assert s["pairs"][0]["account_state"] == "matched_within_tolerance" and s["pairs"][0]["causes"] == []


def test_claims_are_unconfirmed_candidates_and_rule_effects_need_ablations(tmp_path):
    s = grid(tmp_path, matched_case(tmp_path))["strategies"][0]
    claims = {c["claim_type"]: c for c in s["claims"]}
    assert all(c["status"] == "candidate" and not c["confirmed"] and "候选" in c["claim_state_text"] for c in s["claims"])
    assert all(c["claim_state"] == "unconfirmed" for c in claims.values())
    assert claims["rule_marginal_effect"]["diagnostic_state"] == "insufficient"
    assert claims["mechanism_uses_declared_inputs"]["diagnostic_state"] == "insufficient"
    assert claims["account_reconciliation"]["diagnostic_state"] == "supported_within_scope"


def test_coverage_counts_runs_slices_and_repeated_dates(tmp_path):
    cov = grid(tmp_path, matched_case(tmp_path))["strategies"][0]["coverage"]
    assert (cov["run_count"], cov["derived_slice_count"], cov["window_sessions"], cov["distinct_sessions"]) == (2, 1, 12, 8)
    assert cov["repeat"] == pytest.approx(1.5) and "不是有效样本量" in cov["note"]


def test_row_order_does_not_change_the_result(tmp_path):
    rows = matched_case(tmp_path)
    a = grid(tmp_path, rows)
    b = grid(tmp_path, list(reversed(rows)))
    assert json.dumps(audit_grid._clean(a), sort_keys=True) == json.dumps(audit_grid._clean(b), sort_keys=True)


def test_the_same_material_under_two_window_names_is_invalid(tmp_path):
    holds = {d: {"A.SH": (1000, 1e6)} for d in DAYS}
    ref, ro = write_run(tmp_path, "s1/W00", DAYS, [1e6] * 8, 1e6, holds, digest="sha256:same")
    copy, co = write_run(tmp_path, "s1/W09", DAYS, [1e6] * 8, 1e6, holds, digest="sha256:same")
    s = grid(tmp_path, [["s1", "W00", "reference", "true", "fresh_start", ref, ro],
                        ["s1", "W09", "x", "false", "fresh_start", copy, co]])["strategies"][0]
    assert s["check_state"] == "invalid" and "同一份材料" in s["verdict"]


def test_unreconciled_books_block_the_cell(tmp_path):
    rows = matched_case(tmp_path)
    bad, bo = write_run(tmp_path, "s1/W02", DAYS[4:], [1e6] * 4, 1e6, {}, status="needs_reconciliation")
    s = grid(tmp_path, rows + [["s1", "W02", "later", "false", "fresh_start", bad, bo]])["strategies"][0]
    cell = next(c for c in s["cells"] if c["window_id"] == "W02")
    assert cell["status"] == "invalid" and s["check_state"] == "invalid"
    assert not any(p.get("fresh_run") == "s1/W02" and p.get("status") == "compared" for p in s["pairs"])


def test_two_references_are_not_silently_resolved(tmp_path):
    rows = matched_case(tmp_path)
    rows[1][3] = "true"
    s = grid(tmp_path, rows)["strategies"][0]
    assert s["reference_status"] == "ambiguous" and s["pairs"] == [] and s["check_state"] == "insufficient"


def test_old_manifest_form_still_reads_and_cli_writes_every_output(tmp_path, capsys):
    rows = matched_case(tmp_path)
    old = tmp_path / "old.csv"
    with old.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["strategy_id", "window_id", "group", "audit_dir"])
        w.writerow(["s1", "W00", "reference", rows[0][5]])
        w.writerow(["s1", "W01", "later", rows[1][5]])
    out = tmp_path / "out"
    assert main(["audit", "grid", str(old), "--out", str(out), "--json"]) == 0
    brief = json.loads(capsys.readouterr().out)
    assert brief["strategies"][0]["check_state"] == "not_triggered"
    for name in ("grid.json", "cells.csv", "pair_summaries.csv", "paired_daily.csv", "claim_records.json",
                 "claim_evidence.csv", "next_experiments.json", "experiment_relations.json", "source_semantics.json"):
        assert (out / name).exists(), name
    assert "NaN" not in (out / "grid.json").read_text(encoding="utf-8")
    assert main(["audit", "grid", str(old), "--out", str(out), "--json"]) == 2
