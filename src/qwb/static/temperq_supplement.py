# -*- coding: utf-8 -*-
"""TemperQ 补材料脚本 · 聚宽 / 米筐研究环境

回测跑完后，在研究环境运行本文件，补齐审计需要、但平台不默认导出的五份材料：

  temperq_prices.csv   候选池收盘价（检验策略前提用）：打分里出现过的每只标的、每个打分日
  temperq_minutes.csv  成交日分钟价：对照时点、每笔成交前 2 分钟及成交后 10 分钟
  temperq_ranges.csv   成交日全天最高最低价（检验成交价是否专挑当天低点买、高点卖）
  temperq_limits.csv   成交日涨跌停价（检验是否在封板时成交）
  temperq_listings.csv 出现过的标的上市、退市日期（检验候选池是否用了未来名单）

需要的输入（放在研究环境同一目录）：
  temperq_scores.csv   由 temperq_recorder.py 在回测里记下（没有则跳过候选池收盘价）
  trades.csv           成交记录，至少有 date、code 两列，有 time 列更好（TemperQ 标准格式即可；没有则跳过分钟价）

只读行情，不改任何文件以外的东西。
"""
import csv
import os

TIMES = ['09:31', '09:35', '09:45', '10:00', '10:30', '11:00', '13:30', '14:00', '14:30', '14:50']
AFTER = 10          # minutes after each fill, for the worst-price floor


def window(clock, n=AFTER):
    """HH:MM of the fill and the n minutes after it, skipping the lunch break."""
    if not clock:
        return set()
    h, m = int(clock[:2]), int(clock[3:5])
    out = set()
    for k in range(n + 1):
        t = h * 60 + m + k
        if 11 * 60 + 30 < t < 13 * 60 + 1:
            t += 90
        out.add('%02d:%02d' % (t // 60, t % 60))
    return out


def _rows(name):
    if not os.path.exists(name):
        return []
    with open(name, encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def platform_code(code):
    """600000.SH -> 600000.XSHG；聚宽、米筐都用 XSHG/XSHE。"""
    code = code.strip().upper()
    if code.endswith('.SH'):
        return code[:-3] + '.XSHG'
    if code.endswith('.SZ'):
        return code[:-3] + '.XSHE'
    return code


def needed(scores, trades):
    """(打分日 × 标的) 与 (成交日 × 标的)。纯函数，便于离线测试。"""
    price_keys = sorted({(r['date'][:10], platform_code(r['code'])) for r in scores})
    minute_keys = sorted({(r['date'][:10], platform_code(r['code'])) for r in trades})
    return price_keys, minute_keys


def fill_clocks(trades):
    """Fill timing, gross participation (±2 minutes), and post-fill price windows.

    These are requested labels only. main() exports actual returned rows; absent
    bars remain absent, including session boundaries and lunch breaks.
    """
    out = {}
    for r in trades:
        stamp = (r.get('time') or r.get('datetime') or '').strip()
        clock = stamp[11:16] if len(stamp) >= 16 else stamp[:5]
        if len(clock) == 5 and clock[2] == ':':
            wanted = out.setdefault((r['date'][:10], platform_code(r['code'])), set())
            wanted.update(window(clock))
            minute = int(clock[:2]) * 60 + int(clock[3:5])
            wanted.update('%02d:%02d' % (t // 60, t % 60)
                          for t in range(minute - 2, minute + 3) if 0 <= t < 24 * 60)
    return out


def _limits(code, day):
    """(涨停价, 跌停价)；平台不提供时返回 (None, None)。"""
    try:                                                    # 聚宽
        frame = get_price(code, start_date=day, end_date=day, frequency='daily',
                          fields=['high_limit', 'low_limit'], skip_paused=False, fq=None)
        row = frame.iloc[0]
        return row['high_limit'], row['low_limit']
    except TypeError:                                       # 米筐
        frame = get_price(code, start_date=day, end_date=day, frequency='1d',
                          fields=['limit_up', 'limit_down'], adjust_type='none')
        row = frame.iloc[0]
        return row['limit_up'], row['limit_down']
    except Exception:
        return None, None


def _listing(code):
    """(上市日期, 退市日期)；查不到返回 None。"""
    try:                                                    # 聚宽
        info = get_security_info(code)
        end = str(info.end_date)[:10]
        return str(info.start_date)[:10], ('' if end.startswith('2200') else end)
    except NameError:
        pass
    except Exception:
        return None
    try:                                                    # 米筐
        info = instruments(code)
        end = str(getattr(info, 'de_listed_date', '') or '')[:10]
        return str(info.listed_date)[:10], ('' if end.startswith('0000') or end.startswith('2999') else end)
    except Exception:
        return None


def _get_price(code, start, end, frequency):
    """聚宽 get_price 与米筐 get_price 的参数不同，这里统一。"""
    try:                                                    # 聚宽
        return get_price(code, start_date=start, end_date=end, frequency=frequency,
                         fields=['open', 'high', 'low', 'close', 'volume'], skip_paused=False, fq='post' if frequency == 'daily' else None)
    except TypeError:                                       # 米筐
        return get_price(code, start_date=start, end_date=end, frequency='1d' if frequency == 'daily' else '1m',
                         fields=['open', 'high', 'low', 'close', 'volume'], adjust_type='post' if frequency == 'daily' else 'none')


def main():
    scores, trades = _rows('temperq_scores.csv'), _rows('trades.csv') or _rows('temperq_trades.csv')
    price_keys, minute_keys = needed(scores, trades)
    if price_keys:
        by_code = {}
        for day, code in price_keys:
            by_code.setdefault(code, []).append(day)
        with open('temperq_prices.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['date', 'code', 'close'])
            for code, days in sorted(by_code.items()):
                frame = _get_price(code, min(days), max(days), 'daily')
                wanted = set(days)
                for stamp, row in frame.iterrows():
                    day = str(stamp)[:10]
                    if day in wanted and row['close'] == row['close']:
                        w.writerow([day, code, row['close']])
        print('temperq_prices.csv', len(price_keys), '个打分日×标的')
    else:
        print('没有 temperq_scores.csv，跳过候选池收盘价')
    if minute_keys:
        around = fill_clocks(trades)
        with open('temperq_minutes.csv', 'w', newline='', encoding='utf-8') as f,                 open('temperq_ranges.csv', 'w', newline='', encoding='utf-8') as g:
            w = csv.writer(f); w.writerow(['date', 'time', 'code', 'close', 'open', 'high', 'low', 'volume'])
            r = csv.writer(g); r.writerow(['date', 'code', 'high', 'low'])
            for day, code in minute_keys:
                # Unadjusted minute bars: the day's range must be in the same price terms as the fills.
                frame = _get_price(code, day + ' 09:30:00', day + ' 15:00:00', '1m')
                wanted = set(TIMES) | around.get((day, code), set())
                live = frame[frame['volume'] > 0]
                if len(live):
                    r.writerow([day, code, live['high'].max(), live['low'].min()])
                for stamp, row in frame.iterrows():
                    clock = str(stamp)[11:16]
                    if clock in wanted:
                        w.writerow([day, clock, code, row['close'], row['open'] if clock == '09:31' else '',
                                    row['high'], row['low'], row['volume']])
        print('temperq_minutes.csv / temperq_ranges.csv', len(minute_keys), '个成交日×标的')
        with open('temperq_limits.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['date', 'code', 'limit_up', 'limit_down'])
            kept = 0
            for day, code in minute_keys:
                up, down = _limits(code, day)
                if up is not None and up == up:
                    w.writerow([day, code, up, down]); kept += 1
        print('temperq_limits.csv', kept, '个成交日×标的')
    codes = sorted({c for _, c in price_keys} | {c for _, c in minute_keys})
    if codes:
        with open('temperq_listings.csv', 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['code', 'listed_date', 'delisted_date'])
            kept = 0
            for code in codes:
                row = _listing(code)
                if row:
                    w.writerow([code, row[0], row[1]]); kept += 1
        print('temperq_listings.csv', kept, '只标的')
    else:
        print('没有成交记录，跳过成交日分钟价')


if 'get_price' in globals():          # only inside a platform research environment
    main()
