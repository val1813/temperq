"""End-to-end native account -> execution reconciliation -> TemperQ -> injected break.

Explicit external paths only. Uses a new output directory; no real accounts,
brokers, schedulers, upgrades or writes to original datasets/strategies.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check_calendar(raw, actual_dates, start, end):
    """Check the whole requested date range, not just available price rows.

    Accepts a credential-free trade_cal cache with fields/items. The declared
    provider remains unverified; this is an independent calendar comparison,
    not data-source authentication.
    """
    payload = json.loads(raw.decode("utf-8-sig"))
    fields = payload["fields"]
    if len(fields) != len(set(fields)) or not {"exchange", "cal_date", "is_open"} <= set(fields):
        raise ValueError("Calendar needs unique exchange/cal_date/is_open fields")
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError("Calendar start exceeds end")
    observed = {}
    for values in payload["items"]:
        if len(values) != len(fields):
            raise ValueError("Malformed calendar row")
        row = dict(zip(fields, values))
        if row["exchange"] != "SSE":
            raise ValueError("Acceptance requires the SSE trading calendar")
        day = date.fromisoformat(str(row["cal_date"]))
        if not first <= day <= last:
            continue
        if day in observed or row["is_open"] not in (0, 1, "0", "1"):
            raise ValueError("Duplicate calendar date or invalid open flag")
        observed[day] = str(row["is_open"]) == "1"
    requested = {first + timedelta(days=i) for i in range((last - first).days + 1)}
    if set(observed) != requested:
        raise ValueError("Calendar cache does not cover every requested calendar date")
    actual = [date.fromisoformat(d) for d in actual_dates]
    if len(actual) != len(set(actual)):
        raise ValueError("Duplicate account snapshot date")
    expected = {d for d, opened in observed.items() if opened}
    missing, unexpected = expected - set(actual), set(actual) - expected
    return {"status": "matched" if not missing and not unexpected else "different",
            "expected_sessions": len(expected), "observed_sessions": len(actual),
            "missing_sessions": sorted(d.isoformat() for d in missing),
            "unexpected_sessions": sorted(d.isoformat() for d in unexpected),
            "calendar_sha256": hashlib.sha256(raw).hexdigest(),
            "provider_declared": payload.get("provider"), "source_authenticated": False}


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--engine-python", type=Path, required=True)
    p.add_argument("--engine-root", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--calendar", type=Path, help="credential-free trade_cal JSON cache; validates SSE session completeness")
    p.add_argument("--execution-profile", choices=("installed", "timestamped_opening"), default="timestamped_opening",
                   help="default requires timestamped opening quotes; installed explicitly reproduces the legacy accounting-only replay")
    p.add_argument("--opening-state", type=Path)
    p.add_argument("--opening-source-kind", choices=("historical_quote", "synthetic_fixture"))
    p.add_argument("--market-source-kind", choices=("historical_market", "synthetic_fixture"), default="historical_market")
    args = p.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
               PYTHONDONTWRITEBYTECODE="1", OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", MKL_NUM_THREADS="1")
    results = []

    def call(name, cmd, expected=0):
        r = subprocess.run(list(map(str, cmd)), cwd=out, env=env, capture_output=True, encoding="utf-8", errors="replace")
        (out / f"{name}.stdout.txt").write_text(r.stdout, encoding="utf-8")
        (out / f"{name}.stderr.txt").write_text(r.stderr, encoding="utf-8")
        results.append({"stage": name, "exit_code": r.returncode, "expected": expected,
                        "passed": r.returncode == expected})
        if r.returncode != expected:
            raise RuntimeError(f"{name} failed; inspect its saved logs")
        print(f"PASS {name}", flush=True)
        return r

    probe = out / "native_probe"
    try:
        probe_cmd = [args.engine_python, "-B", ROOT / "scripts/probe_native_account.py",
                     "--engine-root", args.engine_root, "--config", args.config, "--output", probe,
                     "--execution-profile", args.execution_profile, "--market-source-kind", args.market_source_kind]
        if args.opening_state:
            probe_cmd += ["--opening-state", args.opening_state]
        if args.opening_source_kind:
            probe_cmd += ["--opening-source-kind", args.opening_source_kind]
        call("native", probe_cmd)
        native = json.loads((probe / "probe.json").read_text(encoding="utf-8"))
        calendar = {"status": "not_checked"}
        if args.calendar:
            with (probe / "native/snapshots.csv").open(encoding="utf-8-sig", newline="") as f:
                actual_dates = [row["date"] for row in csv.DictReader(f)]
            calendar = check_calendar(args.calendar.read_bytes(), actual_dates,
                                      native["config"]["start"], native["config"]["end"])
            (out / "calendar_check.json").write_text(json.dumps(calendar, ensure_ascii=False, indent=2), encoding="utf-8")
            results.append({"stage": "calendar", "passed": calendar["status"] == "matched"})
            if calendar["status"] != "matched":
                raise RuntimeError("Account dates differ from the supplied trading calendar")
            print("PASS calendar", flush=True)
        tq = [sys.executable, "-B", "-m", "qwb.cli", "audit"]
        rec = call("native_reconcile", [*tq, "reconcile", "--internal", probe / "recorded_executions.csv",
                   "--broker", probe / "native_executions.csv", "--source-kind", "simulator_native",
                   "--out", out / "execution_reconciliation.json", "--json"])
        assert json.loads(rec.stdout)["status"] == "matched"
        account_reconciliation = {"status": "not_checked", "reason": "requires supplied complete trading calendar"}
        if args.calendar:
            call("account_reconcile", [sys.executable, "-B", ROOT / "scripts/verify_account_reconciliation.py",
                 "--probe", probe, "--calendar", args.calendar, "--output", out / "account_reconciliation"])
            account_reconciliation = json.loads((out / "account_reconciliation/verification.json").read_text(encoding="utf-8"))
            assert account_reconciliation["status"] == "passed"
        check = call("import_check", [*tq, "check", probe / "materials", "--opening-cash", native["opening_cash"], "--json"])
        assert json.loads(check.stdout)["books"] == "verified"
        report_name = ("合成行情与报价验收" if args.market_source_kind == "synthetic_fixture" else
                       "历史行情＋合成开盘报价模拟验收" if args.opening_source_kind == "synthetic_fixture" else "历史行情模拟账户验收")
        call("audit", [*tq, "run", probe / "materials", "--out", out / "audit", "--opening-cash", native["opening_cash"],
                       "--name", report_name, "--commission-bp", native["config"]["fee_bps"], "--min-commission", native["config"]["minimum_fee"], "--json"])
        summary = json.loads((out / "audit/summary.json").read_text(encoding="utf-8"))
        assert summary["books"] == "verified"
        # A meaningful negative check: a broken fill must be caught, even if the
        # normal two-report comparison passed. Never mutate the native output.
        table = csv.DictReader(io.StringIO((probe / "recorded_executions.csv").read_text(encoding="utf-8")))
        fields, rows = table.fieldnames, list(table)
        from decimal import Decimal
        rows[0]["quantity"] = str(Decimal(rows[0]["quantity"]) + 100)
        with (out / "injected_quantity_break.csv").open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        bad = call("injected_break", [*tq, "reconcile", "--internal", out / "injected_quantity_break.csv",
                   "--broker", probe / "native_executions.csv", "--source-kind", "simulator_native", "--json"], expected=1)
        assert json.loads(bad.stdout)["breaks"] == 1
        final = {"status": "passed", "stages": results, "native": native,
                 "calendar": calendar,
                 "account_reconciliation": account_reconciliation,
                 "audit_books": summary["books"], "scope": native["scope"],
                 "execution_model": native.get("execution_model"),
                 "institutional_readiness": "not_established", "real_execution_validation": False,
                 "report": "audit/report.html", "real_broker_validation": False,
                 "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                 "probe_sha256": hashlib.sha256((ROOT / "scripts/probe_native_account.py").read_bytes()).hexdigest()}
    except Exception as exc:
        final = {"status": "failed", "stages": results, "error": str(exc)}
        raise
    finally:
        (out / "acceptance.json").write_text(json.dumps(final, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: final[k] for k in ("status", "audit_books", "scope", "real_broker_validation")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
