"""Re-audit existing native simulation exports in fresh evidence directories.

Explicit source roots are read-only. Optional minute bars must already be
observed data; neither daily marks nor interpolation supplies missing bars.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--materials-root", type=Path, required=True)
    p.add_argument("--native-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--observed-minutes", type=Path)
    p.add_argument("--ids", nargs="+", required=True)
    args = p.parse_args()
    from qwb.audit_reconciliation import FIELDS, reconcile
    from qwb.audit import _code
    import pandas as pd

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    minutes = None
    if args.observed_minutes:
        minutes = pd.read_parquet(args.observed_minutes).rename(columns={"order_book_id": "code"})
        clock = pd.to_datetime(minutes.datetime)
        minutes["date"], minutes["time"] = clock.dt.strftime("%Y-%m-%d"), clock.dt.strftime("%H:%M:%S")
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    records = []
    for identity in args.ids:
        if Path(identity).name != identity:
            raise ValueError("Run identities must be directory names")
        item = out / identity
        item.mkdir()
        try:
            source, native = args.materials_root / identity, args.native_root / identity
            files = sorted(f for f in source.iterdir() if f.is_file())
            hashes = {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
            mat = item / "materials"
            mat.mkdir()
            for f in files:
                shutil.copyfile(f, mat / f.name)
            if minutes is not None:
                snapshots = pd.read_csv(mat / "account.csv", dtype=str)
                code_set = set(pd.read_csv(native / "fills.csv", dtype=str).code)
                observed = minutes[minutes.date.isin(snapshots.date) & minutes.code.isin(code_set)]
                observed[["date", "time", "code", "open", "high", "low", "close", "volume"]].to_csv(mat / "minutes.csv", index=False)
            # Native engine output and callback recording share a simulated
            # origin. Verify both reporting paths without calling either broker.
            a = pd.read_csv(native / "fills.csv", dtype=str)
            b = pd.read_csv(native / "fills_native.csv", dtype=str)
            if b.account_id.nunique() != 1 or set(b.currency) != {"CNY"}:
                raise ValueError("Expected a single CNY native account")
            a["side"] = a.side.str.lower()
            a["trade_id"] = [f"callback-row-{i + 2}" for i in range(len(a))]
            a["code"] = a.code.map(_code)
            norm = pd.DataFrame({"date": pd.to_datetime(b.ts_event, utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"),
                                 "order_id": b.client_order_id, "trade_id": b.trade_id,
                                 "code": b.instrument_id.str.replace(r"\.XETF$", "", regex=True).map(_code),
                                 "side": b.order_side.str.lower(), "quantity": b.last_qty, "price": b.last_px,
                                 "fee": b.commission.str.replace(r" CNY$", "", regex=True)})
            left, right = a[list(FIELDS)].to_csv(index=False).encode(), norm[list(FIELDS)].to_csv(index=False).encode()
            rec = reconcile(left, right, source_kind="simulator_native")
            (item / "execution_reconciliation.json").write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
            if rec["status"] != "matched":
                raise ValueError("Native fills differ from callback recording")
            summary = json.loads((native / "summary.json").read_text(encoding="utf-8"))
            cfg = summary["config"]
            cash = cfg["capital"]
            cmd = [sys.executable, "-B", "-m", "qwb.cli", "audit", "run", str(mat), "--out", str(item / "audit"),
                   "--opening-cash", str(cash), "--commission-bp", "2", "--min-commission", "5", "--json"]
            r = subprocess.run(cmd, env=env, cwd=out, capture_output=True, encoding="utf-8", errors="replace")
            (item / "stdout.txt").write_text(r.stdout, encoding="utf-8")
            (item / "stderr.txt").write_text(r.stderr, encoding="utf-8")
            if r.returncode:
                raise ValueError("Audit command failed; see preserved logs")
            result = json.loads((item / "audit/evidence.json").read_text(encoding="utf-8"))["result"]
            if result["modules"]["accounting"]["status"] != "verified":
                raise ValueError("Audit books do not close")
            unchanged = all(hashlib.sha256((source / name).read_bytes()).hexdigest() == sha for name, sha in hashes.items())
            if not unchanged:
                raise ValueError("Source material changed during verification")
            capacity = result["modules"].get("capacity") or {}
            records.append({"id": identity, "passed": True, "books": "verified", "execution_match": rec["status"],
                            "fills": rec["internal_fills"], "input_hashes": hashes, "source_unchanged": unchanged,
                            "capacity_status": capacity.get("status"), "fill_coverage": capacity.get("fill_coverage"),
                            "result_digest": result["result_digest"]})
            print(f"PASS {identity}: {rec['internal_fills']} fills, capacity {capacity.get('status')}", flush=True)
        except Exception as exc:
            records.append({"id": identity, "passed": False, "error": str(exc)})
            print(f"FAILED {identity}: {exc}", flush=True)
        finally:
            report = {"scope": "existing_historical_real_data_simulation_reaudit_not_new_backtest_or_broker_evidence",
                      "all_passed": all(x["passed"] for x in records), "runs": records}
            (out / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
