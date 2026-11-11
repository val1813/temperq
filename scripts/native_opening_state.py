"""Opt-in, timestamped opening quotes for a pinned external LocalSMA adapter.

No author code is vendored or overwritten. The patch is generated in the new
probe output only. Quotes must be independently supplied, never inferred from
final daily volume or assigned a fictitious morning publication timestamp.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import io

ENGINE_SHA256 = "e2d192c076e7419f45f6ae7ca7419465b18dd5cf1a30618240eb236ec36f4d90"
FIELDS = ("date", "symbol", "observed_at", "available_at", "bid", "ask", "bid_size", "ask_size",
          "tradable", "up_limit", "down_limit", "source_ref")
SHANGHAI = timezone(timedelta(hours=8))


def read_opening_state(raw, expected_keys, *, max_age_seconds=60):
    """Validate one explicitly observed opening quote per requested instrument/day.

    Available time includes the source's publication/receipt delay, not just
    the market event time. A later archival API cannot be relabelled as a live
    morning feed. The input declaration is checked, not authenticated.
    """
    if not isinstance(max_age_seconds, int) or not 0 <= max_age_seconds <= 300:
        raise ValueError("Opening quote age budget must be an integer from 0 to 300 seconds")
    expected = set(expected_keys)
    if len(raw) > 8_000_000:
        raise ValueError("Opening state CSV exceeds 8MB")
    try:
        reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or not set(FIELDS) <= set(headers):
            raise ValueError("Opening state requires unique fields: " + ",".join(FIELDS))
        output = {}
        for line, row in enumerate(reader, 2):
            if None in row or any(not (row.get(k) or "").strip() for k in FIELDS):
                raise ValueError(f"Opening state row {line} has missing fields")
            row = {k: row[k].strip() for k in FIELDS}
            day = date.fromisoformat(row["date"])
            if day.isoformat() != row["date"]:
                raise ValueError("Opening state date must be YYYY-MM-DD")
            key = row["date"], row["symbol"]
            if key in output or key not in expected:
                raise ValueError("Duplicate or unexpected opening quote identity")
            execution = datetime.combine(day, datetime.min.time(), SHANGHAI).replace(hour=9, minute=30)
            clocks = []
            for k in ("observed_at", "available_at"):
                t = datetime.fromisoformat(row[k].replace("Z", "+00:00"))
                if t.tzinfo is None or t.utcoffset() is None:
                    raise ValueError("Opening observation and availability timestamps require a timezone")
                clocks.append(t)
            observed, available = clocks
            if not observed <= available <= execution:
                raise ValueError("Opening state must be observed <= available <= 09:30 execution")
            if (execution - observed).total_seconds() > max_age_seconds:
                raise ValueError("Opening quote is stale for the declared age budget")
            if row["tradable"] not in ("true", "false"):
                raise ValueError("tradable must be explicitly true/false")
            for k in ("bid", "ask", "up_limit", "down_limit", "bid_size", "ask_size"):
                try:
                    value = Decimal(row[k])
                except InvalidOperation:
                    raise ValueError("Opening price/size must be numeric") from None
                if not value.is_finite() or value < 0:
                    raise ValueError("Opening price/size must be finite and nonnegative")
                if k.endswith("size"):
                    if value != value.to_integral_value() or value > 1_000_000_000:
                        raise ValueError("Opening sizes must be integer shares, at most 1 billion")
                    row[k] = int(value)
                else:
                    if value <= 0 or value > 1_000_000 or value != value.quantize(Decimal("0.01")):
                        raise ValueError("Stock quote and limit prices must be positive 0.01 ticks")
                    row[k] = float(value)
            if row["bid"] > row["ask"] or row["down_limit"] > row["up_limit"]:
                raise ValueError("Crossed quote or inverted daily limits")
            if row["bid"] < row["down_limit"] or row["ask"] > row["up_limit"]:
                raise ValueError("Opening quote falls outside the supplied daily price limits")
            row["tradable"] = row["tradable"] == "true"
            output[key] = row
    except (UnicodeError, csv.Error):
        raise ValueError("Opening state must be valid UTF-8 CSV") from None
    if set(output) != expected:
        raise ValueError(f"Missing {len(expected - set(output))} opening quote(s); no daily-volume fallback")
    return output


def attach(frame, state):
    out = frame.copy()
    for name in ("bid", "ask", "bid_size", "ask_size", "tradable", "observed_at", "available_at", "source_ref"):
        out["opening_" + name] = [state[(str(r.date), r.symbol)][name] for r in out.itertuples()]
    for target, source in (("up_limit", "up_limit"), ("down_limit", "down_limit")):
        out[target] = [state[(str(r.date), r.symbol)][source] for r in out.itertuples()]
    return out


def patched_source(raw):
    if sha256(raw).hexdigest() != ENGINE_SHA256:
        raise ValueError("External adapter hash differs from the verified version; patch refused")
    source = raw.decode("utf-8-sig")
    patches = [
        ("price, row.get('up_limit'), row.get('down_limit'), row['volume'])",
         "price, row.get('up_limit'), row.get('down_limit'),\n"
         "                                     (row['opening_ask_size'] if side == 'BUY' else row['opening_bid_size'])\n"
         "                                     if row['opening_tradable'] else 0)"),
        ("        allowed, reason = allow_order(self.settings['market']", "        if side == 'SELL':\n"
         "            price = float(tick.bid_price)\n"
         "        allowed, reason = allow_order(self.settings['market']"),
        ("'side':side, 'qty':qty, 'allowed':allowed, 'reason':reason})",
         "'side':side, 'qty':qty, 'allowed':allowed, 'reason':reason,\n"
         "                               'opening_observed_at':row['opening_observed_at'],\n"
         "                               'opening_available_at':row['opening_available_at'],\n"
         "                               'opening_source_ref':row['opening_source_ref']})"),
        ("            ticks.append(QuoteTick(instruments[row['symbol']].id, price, price,\n"
         "                Quantity.from_int(100000000), Quantity.from_int(100000000), ts, ts))",
         "            bid = Price.from_str(f\"{row['opening_bid']:.2f}\") if col == 'open' else price\n"
         "            ask = Price.from_str(f\"{row['opening_ask']:.2f}\") if col == 'open' else price\n"
         "            bid_size = max(1, row['opening_bid_size']) if col == 'open' else 1\n"
         "            ask_size = max(1, row['opening_ask_size']) if col == 'open' else 1\n"
         "            ticks.append(QuoteTick(instruments[row['symbol']].id, bid, ask,\n"
         "                Quantity.from_int(bid_size), Quantity.from_int(ask_size), ts, ts))"),
    ]
    # Each replacement is pinned to the inspected code; no broad AST execution
    # or arbitrary module substitution is accepted.
    for old, new in patches:
        if source.count(old) != 1:
            raise ValueError("Pinned adapter patch context changed")
        source = source.replace(old, new, 1)
    compile(source, "opening_state_engine.py", "exec")
    return source.encode("utf-8")
