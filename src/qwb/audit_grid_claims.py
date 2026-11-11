"""Local, immutable review of grid claims. Reviewing a claim never activates a strategy rule.

A confirmation adopts the existing interpretation, not an economic endorsement. Any edit invalidates
the old evaluation: free text/changed thresholds cannot inherit an earlier supported verdict. A future
typed evaluator may re-evaluate edited protocols; this version deliberately retains them as insufficient.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "claim_review_v1"
EDITABLE = {"text", "estimand", "unit", "direction", "comparison", "conditions", "tolerances", "primary"}


def review_template(grid_path):
    path = Path(grid_path)
    grid = json.loads(path.read_text(encoding="utf-8"))
    return {"schema": SCHEMA, "parent_grid_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "reviewer": "", "note": "", "actions": [],
            "instructions": "从 available_claims 选择；actions 每项填 claim_id、parent_revision、action=confirm/revise/revoke；"
                            "revise 另填 changes（任何修改都会清空旧结论）。不需要复核的主张不要放入 actions。",
            "available_claims": [{"claim_id": c["claim_id"], "parent_revision": c["revision"], "text": c["text"],
                                  "status": c["status"]} for s in grid["strategies"] for c in s["claims"]]}


def review(grid_path, request_path, out):
    from . import audit_grid
    path, req_path = Path(grid_path), Path(request_path)
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    grid = json.loads(raw)
    req = json.loads(req_path.read_text(encoding="utf-8"))
    if req.get("schema") != SCHEMA or req.get("parent_grid_sha256") != digest:
        raise ValueError("主张复核协议版本或父报告 SHA256 不匹配；请从当前报告生成请求")
    if not isinstance(req.get("reviewer"), str) or not req["reviewer"].strip():
        raise ValueError("reviewer 不能为空；确认必须记录实际复核者")
    actions = req.get("actions")
    if not isinstance(actions, list) or not actions:
        raise ValueError("actions 不能为空；模板不会自动确认任何主张")
    claims = {c["claim_id"]: c for s in grid["strategies"] for c in s["claims"]}
    seen = set()
    stamp = datetime.now(timezone.utc).isoformat()
    for action in actions:
        if not isinstance(action, dict):
            raise ValueError("每条 action 必须是对象")
        key = action.get("claim_id")
        if key not in claims or key in seen:
            raise ValueError(f"未知或重复主张：{key}")
        seen.add(key)
        c = claims[key]
        if action.get("parent_revision") != c["revision"]:
            raise ValueError(f"{key} 的父版本不匹配")
        verb = action.get("action")
        if verb not in ("confirm", "revise", "revoke"):
            raise ValueError("action 只能为 confirm / revise / revoke")
        changes = action.get("changes") or {}
        if not isinstance(changes, dict) or set(changes) - EDITABLE:
            raise ValueError("只能修改主张解释/协议字段，不能修改证据、逐格结论或已见状态")
        if (verb == "revise") != bool(changes):
            raise ValueError("revise 必须提供 changes；confirm/revoke 不能夹带修改")
        for field, value in changes.items():
            expected = bool if field == "primary" else dict if field in ("comparison", "conditions", "tolerances") else str
            if not isinstance(value, expected) or (expected is str and not value.strip()):
                raise ValueError(f"{field} 的类型或内容无效")
        # Reject NaN/Infinity in protocols, including nested tolerances.
        try:
            json.dumps(changes, allow_nan=False)
        except (ValueError, TypeError) as exc:
            raise ValueError("主张协议包含非有限值") from exc
        old = copy.deepcopy({k: v for k, v in c.items() if k != "history"})
        c.setdefault("history", []).append(old)
        c["parent_claim_revision"] = c["revision"]
        c["revision"] += 1
        c["diagnostic_state"] = c.get("diagnostic_state", c["claim_state"])
        if verb == "revise":
            c.update(changes)
            c["previous_evaluation"] = {"diagnostic_state": old.get("diagnostic_state", old["claim_state"]),
                                        "per_cell": old["per_cell"]}
            c["diagnostic_state"] = "insufficient"
            c["per_cell"] = [{**e, "state": "insufficient", "effect": None,
                               "eligible_reason": "主张协议已修改，旧证据尚未按新协议重新评估"} for e in old["per_cell"]]
            c["unknowns"] = list(c.get("unknowns") or []) + ["修改后的主张需要新协议评估，不能继承旧支持/反驳结论"]
            c["exceptions"] = []
        c["confirmed"] = verb == "confirm"
        c["status"] = "confirmed" if c["confirmed"] else "candidate"
        c["claim_state"] = c["diagnostic_state"] if c["confirmed"] else "unconfirmed"
        c["claim_state_text"] = audit_grid.CLAIM_WORDS[c["claim_state"]]
        c["confirmation"] = {"action": verb, "reviewer": req["reviewer"].strip(), "at": stamp,
                             "note": str(action.get("note") or req.get("note") or ""),
                             "parent_grid_sha256": digest,
                             "request_sha256": hashlib.sha256(req_path.read_bytes()).hexdigest()}
    grid["claim_review"] = {"schema": SCHEMA, "parent_grid_sha256": digest, "parent_grid": str(path.resolve()),
                             "reviewer": req["reviewer"].strip(), "at": stamp, "reviewed_claims": sorted(seen),
                             "scope": "确认解释或修订协议；不改变策略、规则、账户、原始输入或历史报告"}
    files = audit_grid.write(grid, out)
    (Path(out) / "claim_review_request.json").write_bytes(req_path.read_bytes())
    return {"status": "done", "out": str(out), "reviewed_claims": sorted(seen),
            "parent_grid_sha256": digest, "files": files + ["claim_review_request.json"]}
