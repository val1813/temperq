"""Evidence tables an audit result can be exported as; shared by the web page and `qwb audit`."""
from __future__ import annotations


def _impact_rows(modules):
    impacts = modules["monetary_impact"]
    primary = impacts.get("partial_entry")
    return [{"card": identity, "kind": item["kind"], "scope": item["scope"], **row}
            for identity, item in impacts.items()
            if item.get("kind") != "reference"
            and not (identity == "cash_buffer" and primary is not None
                     and all(item.get(k) == primary.get(k) for k in ("kind", "amount", "rows")))
            for row in item["rows"]]


# kind -> (module that must exist, how to get the rows, columns)
TABLES = {
    'holding-control': ('economic_review', lambda m: m['economic_review']['holding_control'].get('rows'),
                        ['date', 'actual_equity', 'hold_equity', 'extra_fees', 'net_difference']),
    'evidence-dependencies': ('evidence', lambda m: m['evidence'].get('dependencies'),
                            ['date', 'code', 'cash', 'quantity_change', 'reason', 'ref']),
    'implementation-shortfall': ('implementation_shortfall', lambda m: m['implementation_shortfall'].get('rows'),
                                 ['order_id', 'code', 'side', 'quantity', 'filled_quantity', 'unfilled_quantity',
                                  'delay_cost', 'execution_cost', 'opportunity_cost', 'fees', 'total_cost']),
    'factor-contributions': ('factor_attribution', lambda m: [dict(period_id=p['period_id'], book=book, factor=factor, contribution=amount,
        residual=p['books'][book]['residual']) for p in m['factor_attribution'].get('periods', []) if p['status'] == 'computed'
        for book in ('portfolio', 'benchmark') for factor, amount in p['books'][book]['factor_contributions'].items()],
        ['period_id', 'book', 'factor', 'contribution', 'residual']),
    "impact-money": ("monetary_impact", _impact_rows,
        ["card", "kind", "date", "end", "code", "entry_date", "exit_date", "marked_at", "quantity",
         "quantity_before", "quantity_after", "start_price", "end_price", "gross_pnl", "fees", "net_pnl", "scope"]),
    "fill-quality": ("fill_quality", lambda m: (m["fill_quality"].get("floor") or {}).get("observations"),
        ["date", "time", "code", "side", "quantity", "original_price", "stress_price", "net_pnl",
         "observed_minutes", "required_minutes", "status", "reason"]),
    "capacity-windows": ("capacity", lambda m: m["capacity"].get("windows"),
                         ["date", "code", "minute", "window_start", "window_end", "session_boundary_shifted",
                          "gross_quantity", "market_volume", "participation", "center_fills"]),
    'capacity-missing': ('capacity', lambda m: m['capacity'].get('uncovered'),
                         ['date', 'code', 'time', 'reason', 'fill_count', 'missing_minutes', 'conflicting_minutes']),
    'funding-events': ('sequence', lambda m: (m['sequence'].get('funding') or {}).get('rows'),
                       ['date', 'time', 'cash_before', 'buy', 'from_sale', 'sold', 'net_sale_proceeds', 'sell_partial']),
    'signal-timing': ('sequence', lambda m: (m['sequence'].get('signal_after_fill') or {}).get('rows'),
                      ['date', 'code', 'fill_time', 'signal_time']),
    'sequence-unchecked': ('sequence', lambda m: [dict(r, check=check) for check in ('funding', 'signal_after_fill')
                                                  for r in (m['sequence'].get(check) or {}).get('unchecked', [])],
                           ['check', 'date', 'time', 'code', 'reason']),
    "stocks": ("attribution", lambda m: m["attribution"]["stocks"],
               ["code", "gross_pnl", "fees", "net_pnl", "initial_capital_bp"]),
    "daily": ("accounting", lambda m: m["accounting"]["contributions"],
              ["date", "code", "gross_pnl", "fees", "net_pnl"]),
    "fees": ("fee_analysis", lambda m: m["fee_analysis"]["fills"],
             ["date", "code", "side", "order_id", "fill_id", "charge_basis", "charge_confirmed", "quantity", "price",
              "notional", "fee", "modeled_commission", "modeled_stamp_tax", "modeled_fee", "reported_minus_modeled"]),
    "fee-units": ("fee_analysis", lambda m: m["fee_analysis"].get("charge_units"),
                  ["date", "code", "side", "order_id", "basis", "declared_basis", "status", "evidence", "fill_count",
                   "notional", "reported_fee", "modeled_fee", "reported_minus_modeled"]),
    "risk": ("risk_analysis", lambda m: m["risk_analysis"]["daily"],
             ["date", "equity", "cash_weight", "invested_weight", "equity_hhi", "invested_hhi", "effective_names",
              "max_weight", "holdings_count"]),
    "tick-costs": ("tick_analysis", lambda m: m["tick_analysis"]["fills"],
                   ["date", "code", "side", "price", "quantity", "notional", "tick_half_spread_bp", "tick_scenario_cost",
                    "price_band", "accounted_interval", "execution_context"]),
    "holding-lots": ("holding_analysis", lambda m: m["holding_analysis"]["closed"] + m["holding_analysis"]["open"],
                     ["code", "entry_date", "exit_date", "marked_at", "quantity", "entry_price", "exit_or_mark_price",
                      "calendar_days", "snapshot_intervals", "gross_pnl", "fees", "net_pnl", "tick_scenario_cost",
                      "net_after_tick_scenario", "right_censored"]),
}


def table(result, kind):
    """(rows, columns) for one evidence table, or None when this audit did not produce it."""
    if kind not in TABLES:
        return None
    module, rows, columns = TABLES[kind]
    if module not in result.get("modules", {}):
        return None
    found = rows(result["modules"])
    if found is None:
        return None
    from .audit_evidence import export_status
    status = export_status(result)
    if kind in {'implementation-shortfall', 'factor-contributions'}:
        status = result['modules'][module].get('status', 'not_assessed') + '_separate_scope'
    return ([dict(r, evidence_status=status, source_authenticated=False) for r in found],
            [*columns, 'evidence_status', 'source_authenticated'])


def available(result):
    return [k for k in TABLES if table(result, k) is not None]
