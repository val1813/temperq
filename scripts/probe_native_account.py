"""Opt-in historical probe of an existing QuantResearch/Nautilus installation.

Runs its installed LocalSMA adapter, never imported user strategy code. Source
data stay read-only. Outputs are new, isolated and explicitly simulated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import date
from importlib.metadata import version
from pathlib import Path


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument("--engine-root", type=Path, required=True)
    p.add_argument("--config", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--execution-profile", choices=("installed", "timestamped_opening"), default="installed")
    p.add_argument("--opening-state", type=Path, help="explicitly timestamped opening quote CSV; no daily-volume fallback")
    p.add_argument("--opening-source-kind", choices=("historical_quote", "synthetic_fixture"))
    p.add_argument("--market-source-kind", choices=("historical_market", "synthetic_fixture"), default="historical_market")
    args = p.parse_args()
    cfg = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if (cfg.get("market") != "CN" or cfg.get("corporate_actions") != "reject" or
            cfg.get("trade_after") or not cfg["start"] <= cfg["end"] < date.today().isoformat()):
        raise ValueError("Probe supports completed CN history, empty opening and reject-on-corporate-action only")
    if args.output.exists():
        raise ValueError("Use a new output directory")
    if version("nautilus-trader") != "1.231.0":
        raise ValueError("Probe requires the verified NautilusTrader 1.231.0 installation")
    sys.path.insert(0, str(args.engine_root.resolve()))
    import pandas as pd
    from quantlocal.data import indicators, load_market
    from quantlocal.engine import run_engine

    args.output.mkdir(parents=True)
    frame, provenance = load_market(cfg)
    if provenance["missing_sessions"]:
        raise ValueError("Cross-sectional sessions incomplete")
    model = {"profile": "installed", "opening_state_validation": "not_checked", "real_quote_validation": False}
    engine_file = args.engine_root / "quantlocal" / "engine.py"
    code_raw = engine_file.read_bytes()
    original_engine_sha256 = hashlib.sha256(code_raw).hexdigest()
    prepared = indicators(frame, cfg["lookback"])
    if args.execution_profile == "timestamped_opening":
        if not args.opening_state or not args.opening_source_kind:
            raise ValueError("timestamped_opening requires --opening-state and --opening-source-kind")
        from native_opening_state import attach, patched_source, read_opening_state
        import importlib.util
        raw = args.opening_state.read_bytes()
        state = read_opening_state(raw, set(zip(frame.date, frame.symbol)))
        prepared = attach(prepared, state)
        code_raw = patched_source(code_raw)
        generated = args.output / "opening_state_engine.py"
        generated.write_bytes(code_raw)
        spec = importlib.util.spec_from_file_location("temperq_opening_state_engine", generated)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        run_engine = module.run_engine
        (args.output / "opening_state.csv").write_bytes(raw)
        model = {"profile": "timestamped_opening", "opening_state_validation": "declared_times_checked",
                 "source_kind": args.opening_source_kind, "source_authenticated": False,
                 "real_quote_validation": False, "rows": len(state), "max_age_seconds": 60,
                 "opening_state_sha256": hashlib.sha256(raw).hexdigest(),
                 "opening_helper_sha256": hashlib.sha256((Path(__file__).parent / "native_opening_state.py").read_bytes()).hexdigest(),
                 "generated_adapter_sha256": hashlib.sha256(code_raw).hexdigest()}
    elif args.opening_state or args.opening_source_kind:
        raise ValueError("Opening state requires the explicit timestamped_opening execution profile")
    # Both adapters mark native stock prices at two decimals. Signals, sizing,
    # fees and order time stay the same; quote/eligibility assumptions are a
    # separately declared execution sensitivity, never an activated user rule.
    result = run_engine(cfg, prepared, args.output / "native")
    mat = args.output / "materials"
    mat.mkdir()
    fills, snaps = pd.DataFrame(result["fills"]), pd.DataFrame(result["snapshots"])
    trades = fills.rename(columns={"symbol": "code", "qty": "quantity"}).copy()
    if trades.empty:
        raise ValueError("Acceptance requires actual simulated fills, not an empty curve")
    trades["side"] = trades.side.str.lower()
    trades["time"] = pd.to_datetime(trades.timestamp, utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%H:%M:%S")
    trades[["date", "time", "code", "side", "quantity", "price", "fee", "order_id"]].to_csv(mat / "trades.csv", index=False)
    holdings = snaps.melt(id_vars="date", value_vars=cfg["symbols"], var_name="code", value_name="quantity")
    marks = frame.rename(columns={"symbol": "code", "close": "price"})[["date", "code", "price"]].copy()
    marks["price"] = marks.price.round(2)
    holdings = holdings[holdings.quantity > 0].merge(marks, on=["date", "code"], validate="one_to_one")
    holdings.to_csv(mat / "holdings.csv", index=False)
    snaps[["date", "cash", "equity"]].to_csv(mat / "account.csv", index=False)
    marks.rename(columns={"price": "close"}).to_csv(mat / "prices.csv", index=False)
    # Audit parses this source; it never imports or runs it.
    (mat / "strategy.py").write_bytes(code_raw)
    native = pd.read_csv(args.output / "native" / "fills_native.csv", dtype=str)
    if set(native.currency) != {"CNY"} or native.account_id.nunique() != 1:
        raise ValueError("Expected one native CNY account")
    norm = pd.DataFrame({
        "date": pd.to_datetime(native.ts_event, utc=True).dt.tz_convert("Asia/Shanghai").dt.strftime("%Y-%m-%d"),
        "order_id": native.client_order_id, "trade_id": native.trade_id,
        "code": native.instrument_id.str.rsplit(".", n=1).str[0], "side": native.order_side.str.lower(),
        "quantity": native.last_qty, "price": native.last_px,
        "fee": native.commission.str.replace(r" CNY$", "", regex=True)})
    recorded = trades.copy()
    recorded["trade_id"] = [f"recorded-row-{i + 2}" for i in range(len(recorded))]
    fields = ["date", "order_id", "trade_id", "code", "side", "quantity", "price", "fee"]
    norm[fields].to_csv(args.output / "native_executions.csv", index=False)
    recorded[fields].to_csv(args.output / "recorded_executions.csv", index=False)
    scope = ("synthetic_prices_and_quotes_simulated_account_not_market_or_broker_evidence" if args.market_source_kind == "synthetic_fixture"
             else "historical_real_prices_synthetic_opening_quotes_simulated_account_not_broker_or_forward" if args.opening_source_kind == "synthetic_fixture"
             else "historical_real_prices_simulated_account_not_broker_or_forward")
    manifest = {"scope": scope, "market_source_kind": args.market_source_kind,
                "engine_version": version("nautilus-trader"), "config": cfg, "data": provenance,
                "reconciliation": result["reconciliation"], "fills": len(fills), "sessions": len(snaps),
                "opening_cash": cfg["capital"], "source_independence": "same_simulator_two_reporting_paths",
                "execution_model": model,
                "execution_model_limitations": ([
                    "Timestamped opening quote declarations are checked but not authenticated; historical archival data cannot be relabelled as live morning availability.",
                    "One displayed quote and IOC fills are not queue position, replenishment, latency or calibrated market impact.",
                    "Partial fills retain the installed strategy behavior: an existing position is not automatically topped up.",
                ] if args.execution_profile == "timestamped_opening" else [
                    "Synthetic zero-spread quotes from daily open/close, fixed quote size; not observed L1 or queue/impact calibration.",
                    "Installed LocalSMA consults same-day final volume at 09:30; this replay does not certify point-in-time execution eligibility.",
                ]) + [
                    "Fixed selected symbols; no historical universe, delisting or survivorship-bias validation.",
                ],
                "engine_source_sha256": {f.name: hashlib.sha256(f.read_bytes()).hexdigest()
                                         for f in sorted((args.engine_root / "quantlocal").glob("*.py"))}}
    if hashlib.sha256(engine_file.read_bytes()).hexdigest() != original_engine_sha256:
        raise ValueError("External adapter changed while probing")
    (args.output / "probe.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "fills": len(fills), "sessions": len(snaps),
                      "scope": manifest["scope"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
