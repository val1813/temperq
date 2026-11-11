"""Append-only execution inputs and reproducible reports linked to an import.

The strategy link is context, not proof that an account belongs to a strategy.
Never read a backtest's trades as if they were an internal real-account ledger.
"""
from __future__ import annotations

from decimal import DecimalException
from hashlib import sha256
import json
import uuid

from . import experiments as ex, strategy_audit_store as strategies
from .audit_reconciliation import reconcile
from .audit_tca import analyze as tca

SCHEMA = "execution_evidence_v1"
ROLES = {"reconcile": ("internal", "broker"), "tca": ("orders", "fills")}
MAX_BYTES = 8_000_000


def calculate(mode, first, second, source_kind):
    if mode not in ROLES:
        raise ValueError("分析类型无效")
    if any(not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES for raw in (first, second)):
        raise ValueError("两个 CSV 均须有表头，单文件不能超过 8MB")
    try:
        return reconcile(first, second, source_kind=source_kind) if mode == "reconcile" else tca(first, second, source_kind)
    except DecimalException:
        raise ValueError("数字超出当前计算精度或范围，请核对数量和价格") from None


def root(workdir, strategy_id, evidence_id=None):
    base = strategies.root(workdir, strategy_id) / "execution_evidence"
    if evidence_id is not None:
        if not ex.ID_RE.fullmatch(evidence_id):
            raise ValueError("执行证据编号无效")
        base /= evidence_id
    return base


def save(workdir, strategy_id, *, input_digest, mode, first, second, source_kind, note=""):
    record, _ = strategies.read(workdir, strategy_id)
    if input_digest != record["input_digest"]:
        raise ValueError("策略材料已变更，请重新打开导入页")
    if not isinstance(note, str) or len(note) > 80:
        raise ValueError("账户/批次备注最多 80 字")
    result = calculate(mode, first, second, source_kind)
    evidence_id = uuid.uuid4().hex
    inputs = {role: {"stored": role + ".csv", "sha256": sha256(raw).hexdigest(), "bytes": len(raw)}
              for role, raw in zip(ROLES[mode], (first, second))}
    report = {"schema_version": SCHEMA, "id": evidence_id, "strategy_id": strategy_id,
              "strategy_input_digest": input_digest, "created_at": ex.now(), "mode": mode,
              "note": note.strip(), "source_kind": source_kind, "inputs": inputs, "result": result,
              "declaration": "用户声明同一账户、同一期间和币种、统一数量与价格口径；来源与完整性未认证。策略关联仅作上下文，不将回测成交当作实盘内部账。"}
    report["report_digest"] = ex.object_digest(report)
    directory = root(workdir, strategy_id, evidence_id)
    directory.mkdir(parents=True, exist_ok=False)
    for role, raw in zip(ROLES[mode], (first, second)):
        with (directory / (role + ".csv")).open("xb") as stream:
            stream.write(raw)
    # Write the complete report last. Interrupted inputs stay preserved, and
    # incomplete runs are not presented in successful report history.
    with (directory / "report.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return evidence_id


def read(workdir, strategy_id, evidence_id):
    record, _ = strategies.read(workdir, strategy_id)
    directory = root(workdir, strategy_id, evidence_id)
    report = ex.read(directory / "report.json")
    digest = ex.object_digest({k: v for k, v in report.items() if k != "report_digest"})
    if (report["schema_version"] != SCHEMA or report["id"] != evidence_id or report["strategy_id"] != strategy_id
            or report["strategy_input_digest"] != record["input_digest"] or report["report_digest"] != digest):
        raise ValueError("执行报告摘要或策略关联不一致")
    mode = report["mode"]
    if mode not in ROLES or set(report["inputs"]) != set(ROLES[mode]):
        raise ValueError("执行材料清单无效")
    raw_inputs = []
    for role in ROLES[mode]:
        item = report["inputs"][role]
        if item["stored"] != role + ".csv":
            raise ValueError("执行材料文件名无效")
        path = directory / item["stored"]
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("执行材料超出大小限制")
        raw = path.read_bytes()
        if len(raw) != item["bytes"] or sha256(raw).hexdigest() != item["sha256"]:
            raise ValueError("执行材料摘要不一致")
        raw_inputs.append(raw)
    if calculate(mode, *raw_inputs, report["source_kind"]) != report["result"]:
        raise ValueError("执行报告与原始材料重新计算的结果不一致")
    return report


def history(workdir, strategy_id):
    summaries = []
    for path in root(workdir, strategy_id).glob("*"):
        if not path.is_dir() or not ex.ID_RE.fullmatch(path.name):
            continue
        try:
            report = read(workdir, strategy_id, path.name)
            summaries.append({k: report[k] for k in ("id", "mode", "created_at", "source_kind", "note")}
                              | {"status": report["result"]["status"]})
        except (OSError, ValueError, TypeError, KeyError):
            summaries.append({"id": path.name, "mode": "invalid", "created_at": "", "source_kind": "unknown",
                              "note": "", "status": "integrity_failed"})
    return sorted(summaries, key=lambda r: (r["created_at"], r["id"]), reverse=True)
