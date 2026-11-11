"""Observed gross participation, not a fill or impact model.

Both buys and sells consume liquidity. Splitting an order must not reduce its
participation. Future bars are used only for an ex-post description.
"""
from __future__ import annotations

import math
from collections import defaultdict


def _minute(t):
    t = str(t or "").strip()
    if len(t) >= 16 and t[10] in " T":
        t = t[11:]
    try:
        h, m = int(t[:2]), int(t[3:5])
        return h * 60 + m if len(t) >= 5 and t[2] == ":" and 0 <= h < 24 and 0 <= m < 60 else None
    except (ValueError, TypeError):
        return None


def _q(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, max(0, round(p * (len(v) - 1))))]


def _window(code, minute):
    """Five right-labelled continuous-trading bars, respecting SH/SZ sessions.

    09:31 and 13:01 are the first one-minute bars; no auction-volume bars are
    invented. Outside recognized stock/fund sessions use the original centered
    window, whose missing data remain missing.
    """
    from .securities import asset_class
    if str(code).endswith(('.SH', '.SZ')) and asset_class(code) in ('stock', 'fund'):
        for start, end in ((571, 690), (781, 900)):
            if start <= minute <= end:
                first = min(max(minute - 2, start), end - 4)
                return range(first, first + 5)
    return range(minute - 2, minute + 3)


def analyze(bundle, accounting, thresholds=None):
    if accounting.get("status") != "verified":
        return None
    fills = bundle.get("fills") or []
    if not fills or not bundle.get("minute_prices"):
        return None
    ceiling = (thresholds or {}).get("T-PARTICIPATION", .1)
    vol, conflicts = defaultdict(dict), set()
    for m in bundle["minute_prices"]:
        t, v = _minute(m.get("time")), m.get("volume")
        if t is None or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0:
            continue
        key = (m["date"], m["code"])
        if t in vol[key] and vol[key][t] != v:
            conflicts.add((*key, t))
        vol[key][t] = v
    grouped, uncovered = defaultdict(list), []
    for f in fills:
        t = _minute(f.get("time"))
        if t is not None:
            grouped[(f["date"], f["code"], t)].append(f)
        else:
            uncovered.append({'date': f['date'], 'code': f['code'], 'time': f.get('time', ''),
                              'reason': 'invalid_fill_time', 'fill_count': 1,
                              'missing_minutes': [], 'conflicting_minutes': []})
    qty = {key: sum(f["quantity"] for f in fs) for key, fs in grouped.items()}
    rows, covered = [], 0
    for (day, code, t), fs in sorted(grouped.items()):
        bars = vol.get((day, code), {})
        window = _window(code, t)
        # Missing bars are not zero-volume bars; conflicting duplicates cannot
        # receive an arbitrary last-row-wins interpretation.
        missing = [f'{k//60:02d}:{k%60:02d}' for k in window if k not in bars]
        clashing = [f'{k//60:02d}:{k%60:02d}' for k in window if (day, code, k) in conflicts]
        if missing or clashing:
            uncovered.append({'date': day, 'code': code, 'time': f'{t//60:02d}:{t%60:02d}',
                              'reason': 'missing_or_conflicting_volume', 'fill_count': len(fs),
                              'missing_minutes': missing, 'conflicting_minutes': clashing})
            continue
        market = sum(bars[k] for k in window)
        if market <= 0:
            uncovered.append({'date': day, 'code': code, 'time': f'{t//60:02d}:{t%60:02d}',
                              'reason': 'observed_zero_window_volume', 'fill_count': len(fs),
                              'missing_minutes': [], 'conflicting_minutes': []})
            continue
        gross = sum(qty.get((day, code, k), 0) for k in window)
        rows.append({"date": day, "code": code, "minute": t,
                     "window_start": window.start, "window_end": window.stop - 1,
                     "session_boundary_shifted": window.start != t - 2,
                     "gross_quantity": gross, "market_volume": market,
                     "participation": gross / market, "center_fills": len(fs)})
        covered += len(fs)
    shares = [r["participation"] for r in rows]
    p50, p95 = (_q(shares, .5), _q(shares, .95)) if shares else (None, None)
    complete, enough = covered == len(fills), len(rows) >= 10
    peak = max(shares) if shares else None
    exceeding = sum(v > ceiling for v in shares)
    status = ("insufficient" if not enough else "over" if p95 > ceiling else
              "incomplete" if not complete else "spikes" if exceeding else "normal")
    multiple = ceiling / p95 if p95 and complete and enough else None
    out = {"id": "capacity", "method_version": "gross_session_window_v4", "fills": covered,
           "total_fills": len(fills), "window_count": len(rows), "windows": rows,
           "fill_coverage": covered / len(fills), "missing_fills": len(fills) - covered,
           "conflicting_volume_bars": len(conflicts), "median": p50, "p95": p95,
           'max_participation': peak, 'exceeding_windows': exceeding, 'uncovered': uncovered,
           "ceiling": ceiling, "multiple": multiple, "status": status,
           "scope": f"成交附近五个连续分钟，本账户全部买卖成交股数之和 / 同窗口市场成交股数；"
                    "沪深股票/基金按右端标记分钟处理，上午09:31—11:30、下午13:01—15:00，"
                    "接近时段边界时整体平移五分钟窗口，不跨午休、不补竞价量；其余情况仍取前后各两分钟。"
                    f"上限 {ceiling:.0%} 是未校准经验值，输入量须同为股/份。重叠窗口不独立，"
                    "使用事后行情，不证明下单时可成交；倍数只是固定路径同比放大的参与率情景，"
                    "不认证策略容量，不估计冲击、排队或实现短缺"}
    observed = (f"已覆盖窗口参与率中位数 {p50:.2%}、95% 分位 {p95:.2%}" if shares else "没有完整的五分钟成交量窗口")
    prefix = f"{len(fills)} 笔成交中 {covered} 笔有完整窗口（{len(rows)} 个中心时点）；{observed}"
    if not enough:
        out["verdict"] = prefix + "；有效中心时点不足 10 个，容量证据不足，不给放大倍数"
    elif p95 > ceiling:
        out["verdict"] = prefix + f"，已覆盖部分超过 {ceiling:.0%} 参与率门槛，应核对成交假设"
    elif not complete:
        out["verdict"] = prefix + "；其余成交缺少有效窗口，不能由已覆盖部分推断总体容量或放大倍数"
    elif exceeding:
        out['verdict'] = prefix + f'；95%分位未超过门槛，但{exceeding}个窗口超过{ceiling:.0%}，峰值{peak:.2%}，不能据分位数写成全部正常'
    else:
        out["verdict"] = prefix + f"；固定成交路径同比放大约 {multiple:.1f} 倍时，95% 分位达到 {ceiling:.0%}，不代表真实可用容量"
    if uncovered:
        out['verdict'] += '；缺项按日期、证券和分钟列入补件明细'
    return out
