"""Known-answer benchmark: inject a known defect into a simulated strategy and check the audit finds it.

A small single-target ETF rotation is simulated day by day with the same market frictions in every variant
(sell rejected at a price limit, buy rejected for no volume, buy partly filled; drawn from a hash of seed, day,
code and side, so the clean and the faulty version meet exactly the same events). Each variant differs only in
one piece of strategy code, and is exported in RQAlpha's native format plus the TemperQ recorder files. The
audit runs on the exports exactly as on a user's backtest; each detector is a field of its result.

  clean            reads the account at every decision, tops up partial buys, keeps a fee margin
  state_on_request marks a buy as held when the order is sent (phantom holdings when it fails)
  exit_forgotten   marks a sell as done when the order is sent (a refused exit is never retried)
  partial_no_topup never tops up a buy that filled in part
  full_cash        sizes buys with all cash, no margin for the fee (near-miss rejections)
  price_break      candidate prices with a broken adjustment chain (the strategy itself is clean)

A fault manifests in a run when its exported trades, orders or holdings differ from the clean run of the same
seed; otherwise no audit of the exports could tell the two apart (e.g. no buy ever filled in part). Detection
rate = share of manifested runs in which the fault's own detector fires; false-positive rate = share of clean
runs in which any detector fires. The benchmark measures this audit on defects of these kinds; it does not
claim coverage of defects outside the list.
"""
from __future__ import annotations

import csv
import hashlib
import io
import random
from datetime import date, timedelta

CODES = ("510300.XSHG", "510500.XSHG", "159915.XSHE", "518880.XSHG", "513100.XSHG")
FAULTS = ("state_on_request", "exit_forgotten", "partial_no_topup", "full_cash", "price_break")
LOOK, LOT, CASH0 = 20, 100, 100_000.
P_SELL_REFUSED, P_BUY_REFUSED, P_PARTIAL = .07, .04, .05

# Each detector needs both the module's finding and the action card a user would see.
DETECTORS = {
    "state": lambda m: (m.get("state") or {}).get("status") == "flagged" and "state" in m["_cards"],
    "exit_abandoned": lambda m: ((m.get("activity") or {}).get("exits") or {}).get("unrenewed", 0) > 0
                                and "failed_exits" in m["_cards"],
    "partial": lambda m: (m.get("partial_entry") or {}).get("status") == "found" and "partial_entry" in m["_cards"],
    "cash_short": lambda m: (m.get("signal_mapping") or {}).get("cash_short_orders", 0) >= 3 and "cash_buffer" in m["_cards"],
    "price_jump": lambda m: (m.get("prices") or {}).get("status") == "flagged" and "price_jump" in m["_cards"],
}
EXPECTED = {"state_on_request": "state", "exit_forgotten": "exit_abandoned", "partial_no_topup": "partial",
            "full_cash": "cash_short", "price_break": "price_jump"}


def _draw(seed, *key):
    return int(hashlib.sha256("|".join(map(str, (seed,) + key)).encode()).hexdigest()[:12], 16) / 16 ** 12


def _fee(notional):
    return max(5., round(notional * .0003, 2))


def simulate(seed, fault="clean", days=260):
    """Exports of one simulated backtest: {file name: text}."""
    rng = random.Random(seed)
    dates, d = [], date(2024, 1, 2)
    while len(dates) < days + LOOK:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d += timedelta(days=1)
    px = {c: [round(rng.uniform(.8, 4.), 3)] for c in CODES}
    for _ in dates[1:]:
        for c in CODES:
            step = max(-.095, min(.095, rng.gauss(.0003, .013)))
            px[c].append(round(px[c][-1] * (1 + step), 3))
    cash, hold, believed, forgotten, target_qty = CASH0, {}, set(), set(), {}
    trades, positions, account, orders, scores, beliefs = [], [], [], [], [], []
    oid = 0

    def order(day, clock, code, side, qty, filled, status, message):
        nonlocal oid
        oid += 1
        orders.append([f"{day} {clock}", "EVENT.ORDER_UNSOLICITED_UPDATE", str(oid), code, "SIDE." + side.upper(),
                       qty, filled, "ORDER_STATUS." + status, message])
        return str(oid)

    def fill(day, clock, code, side, qty, price, order_id):
        nonlocal cash
        notional = qty * price
        fee = _fee(notional)
        cash += -notional - fee if side == "buy" else notional - fee
        hold[code] = hold.get(code, 0) + (qty if side == "buy" else -qty)
        if not hold[code]:
            del hold[code]
        trades.append([f"{day} {clock}", str(len(trades) + 1), order_id, code, side.upper(), qty, price, fee, 0., fee,
                       f"{day} {clock}", "OPEN" if side == "buy" else "CLOSE"])

    def buy(day, t, code, budget_share, want):
        price = px[code][t]
        budget = cash * budget_share
        qty = min(want, int(budget / price) // LOT * LOT) if want else int(budget / price) // LOT * LOT
        if qty <= 0:
            return 0, False
        need = qty * price + _fee(qty * price)
        if need > cash:
            order(day, "14:52:00", code, "buy", qty, 0, "REJECTED",
                  f"订单创建失败: 可用资金不足。当前资金: {cash:.2f}，{code} 下单所需资金: {need:.2f}")
            return qty, False
        u = _draw(seed, day, code, "buy")
        if u < P_BUY_REFUSED:
            order(day, "14:52:00", code, "buy", qty, 0, "CANCELLED", f"订单被撤销：{code} 当前无成交量")
            return qty, False
        got = qty if u >= P_BUY_REFUSED + P_PARTIAL or qty < 2 * LOT else qty // 2 // LOT * LOT
        status = "FILLED" if got == qty else "CANCELLED"
        i = order(day, "14:52:00", code, "buy", qty, got, status, "" if got == qty else f"订单取消：实际成交 {got}")
        fill(day, "14:53:00", code, "buy", got, price, i)
        return qty, got > 0

    for t in range(LOOK, len(dates)):
        day = dates[t]
        mom = {c: px[c][t] / px[c][t - LOOK] - 1 for c in CODES}
        ranked = sorted(CODES, key=lambda c: -mom[c])
        target = ranked[0]
        for r, c in enumerate(ranked, 1):
            scores.append([day, c, f"{mom[c]:.8f}", r, "true" if c == target else "false", f"{day} 14:50:00"])
        # What the strategy believes it holds when it decides.
        state = set(hold) if fault not in ("state_on_request", "exit_forgotten") else set(believed)
        if fault == "exit_forgotten":
            state = (set(hold) - forgotten)
        beliefs.append([day, "14:50:00", " ".join(sorted(state))])
        # Sells: everything the strategy believes it holds that is not the target.
        for c in sorted(state - {target}):
            qty = hold.get(c, 0)
            if not qty:
                order(day, "14:50:00", c, "sell", target_qty.get(c, LOT), 0, "REJECTED", f"订单创建失败：{c} 没有可卖的持仓")
                believed.discard(c)
                continue
            if _draw(seed, day, c, "sell") < P_SELL_REFUSED:
                order(day, "14:50:00", c, "sell", qty, 0, "REJECTED", f"订单被拒绝：{c} 跌停")
                if fault == "exit_forgotten":
                    forgotten.add(c)
            else:
                fill(day, "14:51:00", c, "sell", qty, px[c][t], order(day, "14:50:00", c, "sell", qty, qty, "FILLED", ""))
            believed.discard(c)
        forgotten.discard(target)
        share = 1. if fault == "full_cash" else .995
        if target not in state:
            want, ok = buy(day, t, target, share, 0)
            if want:
                target_qty[target] = want
                if fault == "state_on_request":
                    believed.add(target)                        # marked as held when the order is sent
        elif hold.get(target, 0) < target_qty.get(target, 0) and fault not in ("partial_no_topup", "state_on_request"):
            buy(day, t, target, share, target_qty[target] - hold.get(target, 0))
        if fault == "state_on_request":
            believed |= {c for c in hold if c == target}
        for c in sorted(hold):
            positions.append([day, c, c, hold[c], px[c][t], px[c][t], hold[c] * px[c][t]])
        mv = sum(hold[c] * px[c][t] for c in hold)
        fees = sum(row[9] for row in trades if row[0][:10] == day)
        account.append([day, cash, fees, mv, cash + mv])

    def text(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue()

    prices = [[dates[t], c, px[c][t]] for t in range(LOOK, len(dates)) for c in CODES]
    if fault == "price_break":                                  # a broken adjustment chain in the candidate prices
        mid = dates[LOOK + days // 2]
        prices = [[d, c, round(p * .4, 4) if c == CODES[2] and d >= mid else p] for d, c, p in prices]
    return {
        "trades.csv": text(["datetime", "exec_id", "order_id", "order_book_id", "side", "last_quantity", "last_price",
                            "commission", "tax", "transaction_cost", "trading_datetime", "position_effect"], trades),
        "stock_positions.csv": text(["date", "order_book_id", "symbol", "quantity", "last_price", "avg_price", "market_value"],
                                    positions),
        "stock_account.csv": text(["date", "cash", "transaction_cost", "market_value", "total_value"], account),
        "orders.csv": text(["time", "event", "order_id", "code", "side", "quantity", "filled_quantity", "status", "message"],
                           orders),
        "scores.csv": text(["date", "code", "score", "rank", "selected", "available_at"], scores),
        "prices.csv": text(["date", "code", "close"], prices),
        "believed.csv": text(["date", "time", "believed"], beliefs),
        "strategy.py": f"# TemperQ benchmark: single-target ETF rotation, variant {fault}, seed {seed}\n",
    }


def audit(files, thresholds=None):
    """Modules of the audit, plus `_cards`: the action-card ids a user would actually see."""
    from . import audit_actions, strategy_audit, strategy_imports
    sources = [strategy_imports.SourceFile(name, body.encode("utf-8")) for name, body in files.items()]
    bundle = strategy_imports.normalize(sources, opening_cash=CASH0)
    settings = {"commission_bp": 3., "minimum_commission": 5., "max_weight": 1.}
    if thresholds:
        settings["thresholds"] = dict(thresholds)
    result = strategy_audit.analyze(bundle, settings)
    cards = audit_actions.cards(result, bundle)
    return {**result["modules"], "_cards": {c["id"] for c in cards if c and c["state"] in ("act", "review")},
            "_act_cards": sorted(c["id"] for c in cards if c and c["state"] == "act")}


OBSERVED = ("trades.csv", "orders.csv", "stock_positions.csv", "prices.csv")


def run(seeds=10, days=260, recorder=True):
    """Detection matrix over FAULTS and clean runs. Without the recorder, believed.csv is withheld."""
    rows = []
    clean_files = {seed: simulate(seed, "clean", days) for seed in range(seeds)}
    for fault in ("clean",) + FAULTS:
        for seed in range(seeds):
            files = clean_files[seed] if fault == "clean" else simulate(seed, fault, days)
            shows = fault != "clean" and any(files[k] != clean_files[seed][k] for k in OBSERVED)
            if not recorder:
                files.pop("believed.csv")
            modules = audit(files)
            fired = sorted(k for k, test in DETECTORS.items() if test(modules))
            rows.append({"fault": fault, "seed": seed, "books": modules["accounting"]["status"], "fired": fired,
                         "manifested": shows,
                         "expected": EXPECTED.get(fault), "hit": EXPECTED.get(fault) in fired if fault != "clean" else None})
    summary = {}
    for fault in FAULTS:
        own = [r for r in rows if r["fault"] == fault and r["manifested"]]
        also = {}
        for r in own:
            for k in r["fired"]:
                if k != EXPECTED[fault]:
                    also[k] = also.get(k, 0) + 1
        summary[fault] = {"detector": EXPECTED[fault], "detected": sum(r["hit"] for r in own), "runs": len(own),
                          "not_manifested": sum(1 for r in rows if r["fault"] == fault and not r["manifested"]),
                          "also_fired": also}
    clean = [r for r in rows if r["fault"] == "clean"]
    false = {}
    for r in clean:
        for k in r["fired"]:
            false[k] = false.get(k, 0) + 1
    return {"seeds": seeds, "days": days, "recorder": recorder, "faults": summary,
            "clean": {"runs": len(clean), "any_fired": sum(bool(r["fired"]) for r in clean), "by_detector": false},
            "books_verified": sum(r["books"] == "verified" for r in rows), "runs": len(rows), "rows": rows}


# ---------------- family 2: fixed-weight allocation (like 90) ----------------
ALLOC = {"511010.XSHG": .6, "518880.XSHG": .2, "510880.XSHG": .1, "513100.XSHG": .1}
ALLOC_OLD = {"511010.XSHG": .2, "518880.XSHG": .2, "510880.XSHG": .3, "513100.XSHG": .3}
ALLOC_FAULTS = ("alloc_stale_weights", "alloc_never_rebalance")
ALLOC_EXPECTED = {"alloc_stale_weights": "alloc_gap", "alloc_never_rebalance": "alloc_gap"}


def _calendar(days):
    out, d = [], date(2024, 1, 2)
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def _texts(trades, positions, account, orders, extra):
    def text(header, rows):
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
        return buf.getvalue()
    out = {
        "trades.csv": text(["datetime", "exec_id", "order_id", "order_book_id", "side", "last_quantity", "last_price",
                            "commission", "tax", "transaction_cost", "trading_datetime", "position_effect"], trades),
        "stock_positions.csv": text(["date", "order_book_id", "symbol", "quantity", "last_price", "avg_price", "market_value"],
                                    positions),
        "stock_account.csv": text(["date", "cash", "transaction_cost", "market_value", "total_value"], account),
        "orders.csv": text(["time", "event", "order_id", "code", "side", "quantity", "filled_quantity", "status", "message"],
                           orders),
    }
    for name, (header, rows) in extra.items():
        out[name] = text(header, rows)
    return out


def simulate_allocation(seed, fault="clean", days=260):
    """Monthly rebalance to declared fixed weights.
    alloc_stale_weights: from a third of the way in, the code trades to an old weight set it no longer declares.
    alloc_never_rebalance: buys once and never trades again while the declared target stands."""
    rng = random.Random(seed + 7_000)
    dates = _calendar(days)
    codes = list(ALLOC)
    px = {c: [round(rng.uniform(1., 8.), 3)] for c in codes}
    drift = {"511010.XSHG": .0001, "518880.XSHG": .0008, "510880.XSHG": .0003, "513100.XSHG": .0009}
    vol = {"511010.XSHG": .002, "518880.XSHG": .011, "510880.XSHG": .01, "513100.XSHG": .015}
    for _ in dates[1:]:
        for c in codes:
            px[c].append(round(px[c][-1] * (1 + max(-.095, min(.095, rng.gauss(drift[c], vol[c])))), 3))
    cash, hold = CASH0, {}
    trades, positions, account, orders, targets = [], [], [], [], []
    for t, day in enumerate(dates):
        month_start = t == 0 or day[:7] != dates[t - 1][:7]
        if month_start:
            targets += [[day, c, w] for c, w in ALLOC.items()]
        if month_start and not (fault == "alloc_never_rebalance" and t > 0):
            weights = ALLOC_OLD if fault == "alloc_stale_weights" and t >= days // 3 else ALLOC
            equity = cash + sum(hold.get(c, 0) * px[c][t] for c in codes)
            plan = {c: int(equity * weights[c] * .995 / px[c][t]) // LOT * LOT for c in codes}
            for side in ("sell", "buy"):
                for c in codes:
                    delta = plan[c] - hold.get(c, 0)
                    if (side == "sell" and delta >= 0) or (side == "buy" and delta <= 0):
                        continue
                    qty = abs(delta) if side == "sell" else min(delta, int((cash - 50) / px[c][t]) // LOT * LOT)
                    if qty <= 0:
                        continue
                    notional = qty * px[c][t]
                    fee = _fee(notional)
                    oid = str(len(orders) + 1)
                    orders.append([f"{day} 14:50:00", "EVENT.ORDER_UNSOLICITED_UPDATE", oid, c, "SIDE." + side.upper(),
                                   qty, qty, "ORDER_STATUS.FILLED", ""])
                    cash += notional - fee if side == "sell" else -notional - fee
                    hold[c] = hold.get(c, 0) + (qty if side == "buy" else -qty)
                    trades.append([f"{day} 14:51:00", str(len(trades) + 1), oid, c, side.upper(), qty, px[c][t], fee, 0.,
                                   fee, f"{day} 14:51:00", "OPEN" if side == "buy" else "CLOSE"])
        positions += [[day, c, c, hold[c], px[c][t], px[c][t], hold[c] * px[c][t]] for c in codes if hold.get(c)]
        mv = sum(hold.get(c, 0) * px[c][t] for c in codes)
        account.append([day, cash, sum(r[9] for r in trades if r[0][:10] == day), mv, cash + mv])
    files = _texts(trades, positions, account, orders,
                   {"target_weights.csv": (["date", "code", "target_weight"], targets)})
    files["strategy.py"] = f"# TemperQ benchmark: fixed-weight allocation, variant {fault}, seed {seed}\n"
    return files


# ---------------- family 3: intraday on a T+0 ETF (like 91) ----------------
INTRA_CODE = "513100.XSHG"
INTRA_FAULTS = ("intraday_no_retry",)
INTRA_EXPECTED = {"intraday_no_retry": "state"}
P_CLOSE_REFUSED = .12


def simulate_intraday(seed, fault="clean", days=260):
    """Buy at the open on a signal day, sell everything at 14:55. A refused afternoon sell is retried next morning
    in the clean version. intraday_no_retry assumes it is flat every morning: it never retries, and its afternoon
    sell covers only what it bought that day, so the refused position is carried indefinitely."""
    rng = random.Random(seed + 9_000)
    dates = _calendar(days)
    close = [round(rng.uniform(1., 3.), 3)]
    for _ in dates[1:]:
        close.append(round(close[-1] * (1 + max(-.095, min(.095, rng.gauss(.0002, .012)))), 3))
    cash, held = 100_000., 0
    trades, positions, account, orders, scores, beliefs = [], [], [], [], [], []

    def deal(day, clock, side, qty, price, refused=False):
        nonlocal cash, held
        oid = str(len(orders) + 1)
        if refused:
            orders.append([f"{day} {clock}", "EVENT.ORDER_UNSOLICITED_UPDATE", oid, INTRA_CODE, "SIDE.SELL",
                           qty, 0, "ORDER_STATUS.REJECTED", f"订单被拒绝：{INTRA_CODE} 跌停"])
            return
        orders.append([f"{day} {clock}", "EVENT.ORDER_UNSOLICITED_UPDATE", oid, INTRA_CODE, "SIDE." + side.upper(),
                       qty, qty, "ORDER_STATUS.FILLED", ""])
        fee = _fee(qty * price)
        cash += qty * price - fee if side == "sell" else -qty * price - fee
        held += qty if side == "buy" else -qty
        trades.append([f"{day} {clock}", str(len(trades) + 1), oid, INTRA_CODE, side.upper(), qty, price, fee, 0., fee,
                       f"{day} {clock}", "OPEN" if side == "buy" else "CLOSE"])

    for t, day in enumerate(dates):
        open_ = close[t - 1] if t else close[0]
        go = _draw(seed, day, "signal") < .45
        scores.append([day, INTRA_CODE, "1" if go else "0", "true" if go else "false", f"{day} 09:30:00"])
        beliefs.append([day, "09:30:00", INTRA_CODE if held and fault != "intraday_no_retry" else ""])
        if held and fault != "intraday_no_retry":
            deal(day, "09:31:00", "sell", held, open_)                    # retry yesterday's refused exit
        bought = 0
        if go:
            bought = int((cash * .99) / open_) // LOT * LOT
            if bought > 0:
                deal(day, "09:31:00", "buy", bought, open_)
        exit_qty = held if fault != "intraday_no_retry" else min(held, bought)
        if exit_qty > 0:
            deal(day, "14:55:00", "sell", exit_qty, close[t], refused=_draw(seed, day, "refuse") < P_CLOSE_REFUSED)
        if held:
            positions.append([day, INTRA_CODE, INTRA_CODE, held, close[t], close[t], held * close[t]])
        account.append([day, cash, sum(r[9] for r in trades if r[0][:10] == day), held * close[t], cash + held * close[t]])
    files = _texts(trades, positions, account, orders, {
        "scores.csv": (["date", "code", "score", "selected", "available_at"], scores),
        "believed.csv": (["date", "time", "believed"], beliefs)})
    files["strategy.py"] = f"# TemperQ benchmark: intraday T+0 ETF, variant {fault}, seed {seed}\n"
    return files


FAMILIES = (
    ("rotation", simulate, FAULTS, EXPECTED),
    ("allocation", simulate_allocation, ALLOC_FAULTS, ALLOC_EXPECTED),
    ("intraday", simulate_intraday, INTRA_FAULTS, INTRA_EXPECTED),
)
DETECTORS["alloc_gap"] = lambda m: (m.get("allocation") or {}).get("status") == "gap" and "allocation" in m["_cards"]
DETECTORS["decisions"] = lambda m: (m.get("decisions") or {}).get("status") == "flagged" and "decisions" in m["_cards"]


def run(seeds=10, days=260, recorder=True, thresholds=None, seed_start=0):
    """Detection matrix over every family's faults and clean runs. Without the recorder, believed.csv is withheld.
    A fault counts only in runs where it changed what the exports show."""
    from . import audit_rules
    rules = audit_rules.snapshot(audit_rules.resolve(thresholds))
    rows = []
    for family, sim, faults, expected in FAMILIES:
        clean = {seed: sim(seed, "clean", days) for seed in range(seed_start, seed_start + seeds)}
        for fault in ("clean",) + faults:
            for seed in range(seed_start, seed_start + seeds):
                files = dict(clean[seed]) if fault == "clean" else sim(seed, fault, days)
                shows = fault != "clean" and any(files.get(k) != clean[seed].get(k) for k in OBSERVED)
                if not recorder:
                    files.pop("believed.csv", None)
                modules = audit(files, thresholds)
                fired = sorted(k for k, test in DETECTORS.items() if test(modules))
                rows.append({"family": family, "fault": fault, "seed": seed, "books": modules["accounting"]["status"],
                             "fired": fired, "act_cards": modules["_act_cards"], "manifested": shows, "expected": expected.get(fault),
                             "hit": expected.get(fault) in fired if fault != "clean" else None})
    summary = {}
    for family, _, faults, expected in FAMILIES:
        for fault in faults:
            own = [r for r in rows if r["fault"] == fault and r["manifested"]]
            also = {}
            for r in own:
                for k in r["fired"]:
                    if k != expected[fault]:
                        also[k] = also.get(k, 0) + 1
            summary[fault] = {"family": family, "detector": expected[fault], "detected": sum(r["hit"] for r in own),
                              "runs": len(own), "also_fired": also,
                              "not_manifested": sum(1 for r in rows if r["fault"] == fault and not r["manifested"])}
    clean, by_family = [r for r in rows if r["fault"] == "clean"], {}
    false = {}
    for r in clean:
        for k in r["fired"]:
            false[k] = false.get(k, 0) + 1
        f = by_family.setdefault(r["family"], {"runs": 0, "any_fired": 0})
        f["runs"] += 1
        f["any_fired"] += bool(r["fired"])
    for s in summary.values():
        s["detection_interval"] = binomial_interval(s["detected"], s["runs"])
    return {"schema_version": "audit_benchmark_v2", "rules": rules,
            "seeds": seeds, "seed_start": seed_start, "days": days, "recorder": recorder, "faults": summary,
            "scope": "同一合成生成器内的检出率；新随机种子不等于独立外部盲测，不能推广到真实机构策略。区间为逐项 Wilson 95%，重复记录/关联策略不构成独立样本。",
            "clean": {"runs": len(clean), "any_fired": sum(bool(r["fired"]) for r in clean), "by_detector": false,
                      "by_family": by_family,
                      "any_act": sum(bool(r["act_cards"]) for r in clean),
                      "false_alarm_interval": binomial_interval(sum(bool(r["fired"]) for r in clean), len(clean)),
                      "act_interval": binomial_interval(sum(bool(r["act_cards"]) for r in clean), len(clean))},
            "books_verified": sum(r["books"] == "verified" for r in rows), "runs": len(rows), "rows": rows}


def binomial_interval(successes, trials):
    """Wilson score interval; zero events is not evidence of a zero true rate."""
    if trials == 0:
        return {"n": 0, "count": 0, "rate": None, "lower": None, "upper": None, "confidence": .95}
    z = 1.959963984540054
    p = successes / trials
    denom = 1 + z * z / trials
    center = (p + z * z / (2 * trials)) / denom
    half = z * (p * (1 - p) / trials + z * z / (4 * trials * trials)) ** .5 / denom
    return {"n": trials, "count": successes, "rate": p, "lower": max(0., center - half),
            "upper": min(1., center + half), "confidence": .95}
