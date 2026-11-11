"""Order of events: was each trade possible in the order the backtest assumed?

1. Funding between legs. Compare buys with cash just before their minute, rolling
   earlier fills and fees forward from the opening/prior close. Unknown intraday
   timing or cash-action timing prevents a conclusion for that day.
2. Signal after fill. When the daily scores carry the time they became available, a buy of a
   selected name at an earlier time on the same day used a signal that did not exist yet.
3. Code hint. Daily-frequency history whose end is the current moment, inside a function scheduled
   before 10:00, can include today's bar. Read from the code, never run: a hint to check by hand,
   not a finding.
"""
from __future__ import annotations

import ast
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal


def _clock(text):
    text = str(text or "").strip()
    if len(text) >= 16 and text[10] in " T":
        text = text[11:]
    return text[:8] if len(text) >= 5 and text[2] == ":" else ""


def _stamp(day, text):
    """Parse local clocks or full ISO timestamps; compare dates and offsets too."""
    text = str(text or '').strip()
    if re.fullmatch(r'\d{1,2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?', text):
        hour, rest = text.split(':', 1)
        text = f'{day}T{hour.zfill(2)}:{rest}'
    elif not re.match(r'^\d{4}-\d{2}-\d{2}[T ]', text):
        return None
    try:
        stamp = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    local = timezone(timedelta(hours=8))
    return stamp.replace(tzinfo=local) if stamp.tzinfo is None else stamp.astimezone(local)


def funding(bundle):
    fills = bundle.get("fills") or []
    snaps = bundle.get("snapshots") or []
    if not fills or not snaps:
        return None
    days = [s["date"] for s in snaps]
    cash = {s["date"]: s["cash"] for s in snaps}
    opening = (bundle.get("opening") or {}).get("cash")
    groups, invalid_days = defaultdict(list), set()
    for f in fills:
        stamp = _stamp(f['date'], f.get('time'))
        if stamp is None or stamp.date().isoformat() != f['date']:
            invalid_days.add(f['date'])
        else:
            groups[(f['date'], stamp.strftime('%H:%M'))].append(f)
    action_days = {a['date'] for a in bundle.get('actions') or [] if a.get('cash')}
    partial_sells = [o for o in bundle.get("orders") or []
                     if o["side"] == "sell" and 0 < o["filled"] < o["quantity"]]
    rows, unchecked, running = [], [], {}
    checked = 0
    for (day, clock), fs in sorted(groups.items()):
        sells = [f for f in fs if f["side"] == "sell"]
        buys = [f for f in fs if f["side"] == "buy"]
        if day not in running:
            prior = max((d for d in days if d < day), default=None)
            start = cash[prior] if prior else opening
            running[day] = Decimal(str(start)) if start is not None else None
        reason = ('成交时间缺失或日期不一致' if day in invalid_days else
                  '当日有现金公司行动但缺少到账时刻' if day in action_days else
                  '缺少期初或上一收盘现金' if running[day] is None else '')
        if reason:
            if sells and buys:
                unchecked.append({'date': day, 'time': clock, 'reason': reason})
            continue
        held = running[day]
        need = sum((Decimal(str(f['quantity'])) * Decimal(str(f['price'])) + Decimal(str(f.get('fee', 0))) for f in buys), Decimal(0))
        sold = sum((Decimal(str(f['quantity'])) * Decimal(str(f['price'])) for f in sells), Decimal(0))
        proceeds = sold - sum((Decimal(str(f.get('fee', 0))) for f in sells), Decimal(0))
        if sells and buys:
            checked += 1
            if need > held + Decimal('.01'):
                def matched_partial(fill):
                    for order in partial_sells:
                        if order['date'] != day or order['code'] != fill['code']:
                            continue
                        if fill.get('order_id') and order.get('order_id'):
                            if fill['order_id'] == order['order_id']:
                                return True
                        else:
                            at = _stamp(day, order.get('time'))
                            if at and at.date().isoformat() == day and at.strftime('%H:%M') == clock:
                                return True
                    return False
                rows.append({'date': day, 'time': clock, 'buy': float(need), 'cash_before': float(held),
                             'from_sale': float(need - held), 'sold': float(sold), 'net_sale_proceeds': float(proceeds),
                             'sell_partial': any(matched_partial(f) for f in sells)})
        running[day] += proceeds - need
    buys = sum(f["side"] == "buy" for f in fills)
    out = {"id": "funding", "method_version": "intraday_cash_v2", "same_minute_switches": sum(1 for fs in groups.values()
                                                       if {f["side"] for f in fs} == {"buy", "sell"}),
           "dependent": len(rows), "buy_fills": buys, "rows": rows,
           "partial_sell_days": sum(r["sell_partial"] for r in rows),
           "checked_switches": checked, 'unchecked_switches': len(unchecked), 'unchecked': unchecked,
           'untimed_fill_days': sorted(invalid_days), 'cash_action_days': sorted(action_days),
           'coverage_status': 'incomplete' if unchecked or invalid_days else 'complete',
           'scope': '从期初或上一收盘现金滚入当日更早成交及费用，比较本分钟买入额；同分钟内部先后未识别。现金公司行动无到账时刻的日期不判定，不将应收股息当可用现金。'}
    if not rows:
        out["verdict"] = "已核对的同分钟买卖中，没有买入依赖该分钟卖出回款"
    else:
        out["verdict"] = (f"{len(rows)} 次同分钟买卖的买入额超过本分钟前现金（最多差 {max(r['from_sale'] for r in rows):,.0f} 元），依赖本分钟卖出回款；"
                          "需核对卖单实际回报与买单顺序，这一依赖本身不证明违规、成交错误或策略无效")
        if out["partial_sell_days"]:
            out["verdict"] += f"；其中 {out['partial_sell_days']} 次的卖单只成交了一部分"
    if out['coverage_status'] != 'complete':
        out['verdict'] += f"；另有{len(unchecked)}个同分钟买卖组无法核对，需补成交时间、期初现金或现金到账时刻；未覆盖部分不作无依赖判断"
    return out


def signal_after_fill(bundle):
    scores = [s for s in bundle.get("scores") or [] if s.get("selected")]
    if not scores:
        return None
    records = defaultdict(list)
    for s in scores:
        records[(s['date'], s['code'])].append(_stamp(s['date'], s.get('available_at')))
    known = {key: times[0] for key, times in records.items() if None not in times and len(set(times)) == 1}
    rows, unchecked, checked, same = [], [], 0, 0
    for f in bundle.get("fills") or []:
        if f['side'] != 'buy':
            continue
        clock = _stamp(f['date'], f.get('time'))
        at = known.get((f["date"], f["code"]))
        if not clock or not at or clock.date().isoformat() != f['date']:
            unchecked.append({'date': f['date'], 'code': f['code'], 'reason': '缺少或冲突的可用时间/成交时间'})
            continue
        checked += 1
        same += clock.replace(second=0, microsecond=0) == at.replace(second=0, microsecond=0)
        if at > clock:
            rows.append({"date": f["date"], "code": f["code"], "fill_time": clock.isoformat(), "signal_time": at.isoformat()})
    return {"id": "signal_after_fill", 'method_version': 'dated_signal_time_v2', "checked": checked,
            'unmatched_buys': len(unchecked), 'unchecked': unchecked, 'coverage_status': 'incomplete' if unchecked else 'complete',
            "late": len(rows), "same_minute": same, "rows": rows,
            "verdict": (f"{len(rows)} 笔买入的成交时间早于它所依据的打分可用时间：用了下单时还不存在的信号" if rows else
                        "已匹配记录中，没有买入早于它所依据的打分可用时间")
                       + (f"；另有 {same} 笔打分和成交在同一分钟，需确认打分没有用到这一分钟的价格" if same else "")
                       + (f'；{len(unchecked)}笔买入缺少可比时间，未覆盖部分不作通过判断' if unchecked else '')}


OPEN_BEFORE = "10:00"
# Fields of today's daily bar that are already fixed at the open; reading only these is not look-ahead.
KNOWN_AT_OPEN = {"open", "pre_close", "paused", "high_limit", "low_limit", "is_st"}
_SCHEDULE = re.compile(r"run_(?:daily|weekly|monthly)\s*\(\s*(\w+)[^)]*?(?:time\s*=\s*)?['\"](\d{1,2}):(\d{2})['\"]")


def code_hints(bundle):
    """Daily history ending now inside an open-time function: a hint, never a finding."""
    from .code_parse import parse
    source = (bundle.get("code") or {}).get("source") or ""
    if not source.strip():
        return None
    from .audit_premise import _code_texts
    text = _code_texts(source)[-1]
    tree, _ = parse(text)
    if tree is None:
        return None
    early = {m.group(1) for m in _SCHEDULE.finditer(text) if f"{int(m.group(2)):02d}:{m.group(3)}" < OPEN_BEFORE}
    guarded = bool(re.search(r"avoid_future_data['\"]?\s*,\s*True", text))
    hints = []
    functions = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in early:
        fn = functions.get(name)
        if not fn:
            continue
        reach = {name} | {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                          and c.func.id in functions}
        for part in reach:
            for call in ast.walk(functions[part]):
                if not (isinstance(call, ast.Call) and getattr(call.func, "id", getattr(call.func, "attr", "")) in
                        ("get_price", "history", "get_bars", "history_bars")):
                    continue
                kw = {k.arg: ast.unparse(k.value) for k in call.keywords if k.arg}
                freq = kw.get("frequency", kw.get("unit", "'daily'")).strip("'\"")
                ends_now = "current_dt" in kw.get("end_date", "") or kw.get("include_now", "") == "True" \
                    or kw.get("end_dt", "").endswith("current_dt")
                fields = set(re.findall(r"['\"](\w+)['\"]", kw.get("fields", "")))
                if freq in ("daily", "1d") and ends_now and not (fields and fields <= KNOWN_AT_OPEN):
                    hints.append({"line": call.lineno, "function": part, "code": ast.unparse(call)[:160]})
    return {"id": "code_hints", "early_functions": sorted(early), "guarded": guarded, "hints": hints[:10],
            "verdict": (f"{len(hints)} 处在 {OPEN_BEFORE} 前运行的函数里读日线、截止时间是当前时刻，可能读到当天的数据，需人工核"
                        + ("（代码开启了防未来函数，平台可能已拦截）" if guarded else "") if hints else
                        "没有在开盘时段读取截止到当前时刻的日线")}


def analyze(bundle, accounting, thresholds=None):
    parts = {"funding": funding(bundle) if accounting.get("status") == "verified" else None,
             "signal_after_fill": signal_after_fill(bundle), "code_hints": code_hints(bundle)}
    parts = {k: v for k, v in parts.items() if v}
    if not parts:
        return None
    flagged = ((parts.get("signal_after_fill") or {}).get("late", 0) > 0
               or (parts.get("funding") or {}).get("partial_sell_days", 0) > 0)
    return {"id": "sequence", "status": "flagged" if flagged else "noted", **parts,
            "scope": "只看成交、账户、打分时间和代码文字；卖单是否先成交、信号何时算出，以平台撮合和日志为准"}
