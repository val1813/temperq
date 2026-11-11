"""Optional decision identity and clocks on target-weight imports; legacy rows stay time-unknown."""
from datetime import datetime

TEXT_FIELDS = ("snapshot_id", "decision_id", "run_id", "decision_time", "available_at", "time_precision",
               "explicit_zero_policy", "target_scope", "source_path", "source_sha256", "valid_from", "valid_until",
               "persistence_policy", "confidence_kind", "adapter")


def portfolio_targets(records):
    """Keep portfolio policy records, excluding explicit incomplete order instructions.

    Legacy date-only imports retain their existing interpretation. CASH is handled by
    each consumer as cash, never as a security needing a market price.
    """
    return [row for row in records if row.get("complete") is not False
            and row.get("target_scope") != "order_instruction"]


def metadata(row):
    out = {k: str(row[k]).strip() for k in TEXT_FIELDS if row.get(k) is not None and str(row[k]).strip()}
    # Do not invent midnight or localize a timestamp with no declared timezone.
    out["availability_status"] = "unknown"
    stamp = out.get("available_at")
    if stamp:
        try:
            parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if parsed.tzinfo is not None and out.get("time_precision") != "date_only":
                out["availability_status"] = "recorded"
        except ValueError:
            pass
    flag = str(row.get("complete") or "").strip().lower()
    if flag not in ("", "true", "false", "1", "0"):
        raise ValueError("目标 complete 只能是 true/false/1/0")
    out["complete"] = flag in ("true", "1") if flag else None
    if str(row.get("record_index") or "").strip():
        out["record_index"] = int(row["record_index"])
    return out
