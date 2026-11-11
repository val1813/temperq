"""What each artifact of a local grid run means, and which uses it allows.

Plain JSON/CSV parsing only: no imported strategy code is executed and nothing is re-run.

The local grid engine writes `native_grid/targets.csv` from the account after fills
(held quantity x price / total value). Whatever its file name, it is an observed position weight and
must never stand in for a policy target. Policy targets come only from a decision record whose
contract says so; for this engine that is the `run_optimization` trace, whose `weights` follow the
order of its `input` assets. Rank outputs (`get_rank` and similar) are selection intents, not target
vectors. `requests.csv` holds single-asset order instructions, which may repeat or sell to zero and are
not a complete vector either.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path

ADAPTER = "local_grid_native_v3"
POLICY_STEPS = {"run_optimization"}
TIMEZONE = "+08:00"     # grid_engine converts event times to Asia/Shanghai before recording them

SEMANTICS = [
    {"artifact": "native_grid/targets.csv", "semantic_kind": "observed_position_weight",
     "evidence_pointer": "grid_engine.py：targets.append(held[c] * prices[c] / total_value)，成交后的实际持仓",
     "allowed_uses": ["与实际持仓复核"], "forbidden_uses": ["作为目标权重", "作为主张真值", "改名后当作目标"]},
    {"artifact": "native_grid/traces.json#run_optimization", "semantic_kind": "policy_target_output",
     "evidence_pointer": "trace.input 为资产顺序，trace.weights 为同顺序目标权重",
     "allowed_uses": ["同日目标政策比较", "目标与执行的分层"], "forbidden_uses": ["把 output 的任意数组当作权重"]},
    {"artifact": "native_grid/traces.json#other", "semantic_kind": "selection_or_signal_record",
     "evidence_pointer": "排名、筛选等函数的输入与输出",
     "allowed_uses": ["同日选择意图比较"], "forbidden_uses": ["当作完整目标向量"]},
    {"artifact": "native_grid/requests.csv", "semantic_kind": "single_asset_order_instruction",
     "evidence_pointer": "每行一笔下单请求：target_value、方向、数量、拦截原因",
     "allowed_uses": ["指令比较", "执行结局比较"], "forbidden_uses": ["当作同一时点的完整目标权重"]},
]


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _when(date, time):
    if not time:
        return "", "date_only"
    hhmm = str(time)[:5]
    return f"{date}T{hhmm}:00{TIMEZONE}", "minute"


def target_snapshots(traces, run_id, source_path, source_sha, tolerance=1e-9):
    """One row per asset per policy decision, plus the residual cash; invalid vectors are listed, never repaired."""
    rows, invalid = [], []
    for index, trace in enumerate(traces):
        if trace.get("function") not in POLICY_STEPS:
            continue
        assets, weights = trace.get("input"), trace.get("weights")
        problem = None
        if not isinstance(assets, list) or not isinstance(weights, list) or not assets or len(assets) != len(weights):
            problem = "资产列表与权重长度不一致"
        elif any(not isinstance(c, str) or not c.strip() for c in assets):
            problem = "资产代码无效"
        elif len(set(assets)) != len(assets):
            problem = "资产重复"
        elif any(not isinstance(w, (int, float)) or not math.isfinite(w) for w in weights):
            problem = "权重有缺失或非有限值"
        elif any(w < -tolerance for w in weights):
            problem = "权重为负"
        elif sum(weights) > 1 + tolerance:
            problem = "权重之和超过 1"
        if problem:
            invalid.append({"record_index": index, "date": trace.get("date"), "reason": problem})
            continue
        when, precision = _when(trace["date"], trace.get("time"))
        decision = f"{run_id}#traces:{index}"
        common = {"snapshot_id": decision, "decision_id": decision, "run_id": run_id, "date": trace["date"],
                  "decision_time": when, "available_at": when, "time_precision": precision,
                  "complete": True, "explicit_zero_policy": "input_assets_only",
                  "source_path": str(source_path), "source_sha256": source_sha, "record_index": index,
                  "valid_from": when, "valid_until": "", "persistence_policy": "unknown",
                  "confidence_kind": "adapter_derived", "adapter": ADAPTER}
        for code, weight in zip(assets, weights):
            rows.append({**common, "code": code, "target_weight": float(weight), "target_scope": "optimization_policy_output"})
        rows.append({**common, "code": "CASH", "target_weight": max(0., 1. - float(sum(weights))), "target_scope": "residual_cash"})
    return rows, invalid


def selections(traces, run_id, source_path, source_sha):
    rows = []
    for index, trace in enumerate(traces):
        if trace.get("function") in POLICY_STEPS:
            continue
        when, precision = _when(trace.get("date"), trace.get("time"))
        rows.append({"decision_id": f"{run_id}#traces:{index}", "run_id": run_id, "date": trace.get("date"),
                     "time": trace.get("time") or "", "available_at": when, "time_precision": precision,
                     "step": trace.get("function"), "input": json.dumps(trace.get("input"), ensure_ascii=False),
                     "input_complete": trace.get("function") != "evaluate_etf_worth",
                     "missing_inputs": "is_hold (the recorder captured args[0] only)" if trace.get("function") == "evaluate_etf_worth" else "",
                     "output": json.dumps(trace.get("output"), ensure_ascii=False),
                     "source_sha256": source_sha, "record_index": index, "adapter": ADAPTER})
    return rows


def instructions(path, run_id):
    source_sha = sha256(path)
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for line, row in enumerate(csv.DictReader(f), start=2):
            rows.append({"request_ref": f"{source_sha[:16]}:{line}", "run_id": run_id, "date": row["date"],
                         "time": (row.get("time") or "")[:5], "code": _code(row["code"]), "side": row["side"].lower(),
                         "quantity": float(row["quantity"] or 0), "target_value": float(row["target_value"] or 0),
                         "reason": row.get("reason") or "", "order_id": row.get("order_id") or "",
                         "source_sha256": source_sha, "row": line})
    return rows


def _code(code):
    """XSHG/XSHE suffixes as the audit input writes them (SH/SZ)."""
    return code.replace(".XSHG", ".SH").replace(".XSHE", ".SZ")


FIELDS = {
    "target_snapshots": ["snapshot_id", "decision_id", "run_id", "date", "decision_time", "available_at", "time_precision",
                         "code", "target_weight", "target_scope", "complete", "explicit_zero_policy", "source_path",
                         "source_sha256", "record_index", "valid_from", "valid_until", "persistence_policy",
                         "confidence_kind", "adapter"],
    "selections": ["decision_id", "run_id", "date", "time", "available_at", "time_precision", "step", "input", "input_complete", "missing_inputs", "output",
                   "source_sha256", "record_index", "adapter"],
    "instructions": ["request_ref", "run_id", "date", "time", "code", "side", "quantity", "target_value", "reason",
                     "order_id", "source_sha256", "row"],
}


def _write(path, rows, fields):
    with Path(path).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else (str(r[k]).replace(".XSHG", ".SH").replace(".XSHE", ".SZ")
                                                       if k == "code" else r[k])) for k in fields})


def write_overlay(native_dir, run_id, out_dir):
    """Overlay files for one run, next to (never inside) its original materials. Returns what was written."""
    native, out = Path(native_dir), Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    traces_path = native / "traces.json"
    written = {}
    provenance = {"adapter": ADAPTER, "run_id": run_id, "sources": {}}
    if traces_path.exists():
        traces = json.loads(traces_path.read_text(encoding="utf-8"))
        sha = sha256(traces_path)
        provenance["sources"]["traces.json"] = sha
        targets, invalid = target_snapshots(traces, run_id, traces_path, sha)
        if targets:
            _write(out / "target_snapshots.csv", targets, FIELDS["target_snapshots"])
            written["target_snapshots"] = len({r["snapshot_id"] for r in targets})
        if invalid:
            written["invalid_targets"] = invalid
        chosen = selections(traces, run_id, traces_path, sha)
        if chosen:
            _write(out / "selections.csv", chosen, FIELDS["selections"])
            written["selections"] = len(chosen)
    requests = native / "requests.csv"
    if requests.exists():
        rows = instructions(requests, run_id)
        provenance["sources"]["requests.csv"] = rows[0]["source_sha256"] if rows else sha256(requests)
        _write(out / "instructions.csv", rows, FIELDS["instructions"])
        written["instructions"] = len(rows)
    observed = native / "targets.csv"
    if observed.exists():
        provenance["sources"]["targets.csv"] = sha256(observed)
    (out / "source_semantics.json").write_text(json.dumps(SEMANTICS, ensure_ascii=False, indent=1), encoding="utf-8")
    provenance["written"] = written
    (out / "provenance.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=1), encoding="utf-8")
    return written


def read_targets(path):
    """target_snapshots.csv -> [{decision_id, date, time, available_at, complete, weights{code: w}}] in file order."""
    snaps = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            s = snaps.setdefault(row["snapshot_id"], {"decision_id": row["decision_id"], "date": row["date"],
                                                      "time": row["decision_time"][11:16], "available_at": row["available_at"],
                                                      "complete": row["complete"] == "True",
                                                      "zero_policy": row["explicit_zero_policy"], "weights": {}, "cash": None,
                                                      "source": {"sha256": row["source_sha256"], "record_index": int(row["record_index"])}})
            if row["target_scope"] == "residual_cash":
                s["cash"] = float(row["target_weight"])
            else:
                s["weights"][row["code"]] = float(row["target_weight"])
    return list(snaps.values())


def read_selections(path):
    out = {}
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            key = (row["date"], row["step"], row["input"])
            record = {"output": json.loads(row["output"]), "ref": f"{row['source_sha256']}:{row['record_index']}",
                      "decision_id": row["decision_id"], "time": row.get("time"), "available_at": row.get("available_at")}
            complete = row.get("input_complete", "").lower() not in ("false", "0") and row["step"] != "evaluate_etf_worth"
            entry = out.setdefault(key, {"output": record["output"], "ref": record["ref"], "records": [],
                                         "ambiguous": False, "input_complete": complete})
            entry["records"].append(record)
            if len(entry["records"]) > 1:
                entry["ambiguous"] = True
                entry["output"] = None   # never silently choose the last of two potentially different decisions
    return out


def read_instructions(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return [{**row, "quantity": float(row["quantity"]), "target_value": float(row["target_value"]), "row": int(row["row"])}
                for row in csv.DictReader(f)]
