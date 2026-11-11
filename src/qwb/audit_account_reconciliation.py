"""Read-only daily account comparison and independent ledger roll-forwards.

Single-currency long-only spot inventory and trade-date total cash. Explicit
scope, opening snapshot, complete dated calendar, fills and cash/stock events
prevent an empty or truncated submission from silently passing. No settlement,
cost-basis, corporate-action entitlement or source authentication is inferred.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, localcontext
from pathlib import Path

from . import audit_reconciliation as executions

IDENTITY = ("account_id", "currency", "date")
BALANCES = IDENTITY + ("cash", "market_value", "equity", "position_count")
POSITIONS = IDENTITY + ("code", "quantity", "price", "market_value")
FILLS = ("account_id", "currency") + executions.FIELDS
EVENTS = IDENTITY + ("event_id", "kind", "code", "cash_delta", "quantity_delta", "source_ref")
FILES = ("scope.json", "balances.csv", "positions.csv", "fills.csv", "events.csv")
SCOPE_KEYS = {"schema_version", "account_id", "currency", "start_date", "end_date", "cash_basis",
              "positions_complete", "fills_complete", "events_complete"}
NUMBER = re.compile(r"-?(?:0|[1-9][0-9]{0,15})(?:\.[0-9]{1,8})?\Z")
ZERO = Decimal(0)
MAX_BYTES = 8 * 1024 * 1024


def _day(value):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() == value:
            return result
    except (ValueError, TypeError):
        pass
    raise ValueError("日期须为 YYYY-MM-DD")


def _number(value, *, signed=False, integer=False, positive=False):
    if not isinstance(value, str) or not NUMBER.fullmatch(value):
        raise ValueError("数字须为普通十进制，最多16位整数及8位小数，不接受科学计数法")
    number = Decimal(value)
    if (not signed and number < 0) or (positive and number <= 0):
        raise ValueError("金额/数量符号不符合字段约定")
    if integer and (number != number.to_integral_value() or abs(number) > 10**12):
        raise ValueError("数量须为绝对值不超过一万亿的整数股/份")
    return number


def _table(raw, fields, label):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError(f"{label} 须为不超过8MB的文件")
    try:
        table = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")), strict=True)
        if table.fieldnames is None or len(table.fieldnames) != len(set(table.fieldnames)) or set(table.fieldnames) != set(fields):
            raise ValueError(f"{label} 需要唯一且完整的表头：{','.join(fields)}")
        rows = []
        for line, row in enumerate(table, 2):
            if line > 100001 or None in row or any(v is None for v in row.values()):
                raise ValueError(f"{label} 行数超限或第{line}行列数不完整")
            rows.append({k: v.strip() for k, v in row.items()})
        return rows
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ValueError(f"{label} 须为有效UTF-8 CSV") from exc


def _unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("scope.json 不允许重复字段")
        result[key] = value
    return result


def _scope(raw):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError("scope.json 须为不超过8MB的文件")
    try:
        scope = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_json)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("scope.json 须为UTF-8 JSON") from exc
    if not isinstance(scope, dict) or set(scope) != SCOPE_KEYS:
        raise ValueError("scope.json 字段不完整或包含未知字段")
    if scope["schema_version"] != "account_reconciliation_scope_v1":
        raise ValueError("scope.json schema_version 不支持")
    if not isinstance(scope['account_id'], str) or not scope['account_id'].strip() or scope['account_id'] != scope['account_id'].strip():
        raise ValueError("account_id 须为非空统一账户标识")
    if not isinstance(scope['currency'], str) or not re.fullmatch(r"[A-Z]{3}", scope['currency']):
        raise ValueError("currency 须为三位大写币种标识")
    if scope['currency'] != 'CNY':
        raise ValueError("当前仅支持CNY现货账本，不做币种或汇率转换")
    if scope['cash_basis'] != 'trade_date_total_cash':
        raise ValueError("只支持成交日总现金，不接受可用资金或结算现金混用")
    if any(scope[k] is not True for k in ('positions_complete', 'fills_complete', 'events_complete')):
        raise ValueError("须明确声明持仓、成交与事件记录完整；不能把未知缺失当作零")
    first, last = _day(scope['start_date']), _day(scope['end_date'])
    if not date.min < first <= last or (last - first).days > 3660:
        raise ValueError("日期范围须有可表示的期初日、起止有序且不超过3661个自然日")
    return scope


def load_directory(path):
    """Read only normalized named files; no archive extraction or user code."""
    folder = Path(path)
    result = {}
    for name in FILES:
        source = folder / name
        if source.stat().st_size > MAX_BYTES:
            raise ValueError(f"{name} 超过8MB")
        result[name] = source.read_bytes()
    return result


def _calendar(raw, scope):
    rows = _table(raw, ('date', 'is_open'), 'calendar')
    first, last = _day(scope['start_date']), _day(scope['end_date'])
    expected = {(first + timedelta(days=n)).isoformat() for n in range((last-first).days+1)}
    seen, sessions = set(), []
    for row in rows:
        _day(row['date'])
        if row['date'] in seen or row['date'] not in expected or row['is_open'] not in ('0', '1'):
            raise ValueError("calendar 日期重复、超范围或 is_open 不是0/1")
        seen.add(row['date'])
        if row['is_open'] == '1':
            sessions.append(row['date'])
    if seen != expected or not {scope['start_date'], scope['end_date']} <= set(sessions):
        raise ValueError("calendar 须完整覆盖每个自然日，且起止日均为开市快照日")
    return sorted(sessions)


def _read_bundle(files, scope, sessions, label):
    if set(files) != set(FILES):
        raise ValueError(f"{label} 需要全部五个规范文件")
    opening = (_day(scope['start_date']) - timedelta(days=1)).isoformat()
    snapshot_dates = {opening, *sessions}
    balances, positions, fills, events = {}, {}, [], {}
    tables = {name: _table(files[name], fields, f'{label}/{name}') for name, fields in
              [('balances.csv', BALANCES), ('positions.csv', POSITIONS), ('fills.csv', FILLS), ('events.csv', EVENTS)]}
    for name, rows in tables.items():
        for row in rows:
            if row['account_id'] != scope['account_id'] or row['currency'] != scope['currency']:
                raise ValueError(f"{label}/{name} 账户或币种不一致；不得跨账户抵消")
            _day(row['date'])
            if name in ('balances.csv', 'positions.csv'):
                if row['date'] not in snapshot_dates:
                    raise ValueError(f"{label}/{name} 快照日期不在期初及声明交易日内")
            elif not scope['start_date'] <= row['date'] <= scope['end_date']:
                raise ValueError(f"{label}/{name} 业务日期在声明区间之外")
            if name == 'balances.csv':
                key = row['date']
                if key in balances:
                    raise ValueError(f'{label} 资金快照重复')
                balances[key] = {k: _number(row[k], integer=k == 'position_count') for k in
                                 ('cash', 'market_value', 'equity', 'position_count')}
            elif name == 'positions.csv':
                if not row['code']:
                    raise ValueError(f'{label} 持仓证券标识不能为空')
                key = (row['date'], row['code'])
                if key in positions:
                    raise ValueError(f'{label} 持仓快照重复')
                positions[key] = {k: _number(row[k], integer=k == 'quantity', positive=k != 'market_value') for k in
                                  ('quantity', 'price', 'market_value')}
            elif name == 'fills.csv':
                if row['date'] not in sessions:
                    raise ValueError(f'{label} 非交易日不能有成交')
                # Existing parser validates side, identifiers and duplicate fills.
                _number(row['quantity'], integer=True, positive=True)
                _number(row['price'], positive=True)
                _number(row['fee'])
                fills.append(row)
            else:
                key = (row['date'], row['event_id'])
                if not row['event_id'] or not row['source_ref'] or key in events:
                    raise ValueError(f'{label} 事件编号/来源缺失或事件重复')
                cash = _number(row['cash_delta'], signed=True)
                quantity = _number(row['quantity_delta'], signed=True, integer=True)
                kind, code = row['kind'], row['code']
                valid = ((kind in ('deposit', 'interest') and cash > 0 and not quantity and not code) or
                         (kind in ('withdrawal', 'fee', 'tax') and cash < 0 and not quantity and not code) or
                         (kind == 'dividend' and cash > 0 and not quantity and bool(code)) or
                         (kind == 'split' and not cash and quantity != 0 and bool(code)))
                if not valid:
                    raise ValueError(f'{label} 事件类型、符号或证券字段不符合约定；不支持未知调整冲平差异')
                events[key] = dict(row, cash_delta=cash, quantity_delta=quantity)
    parsed_fills = executions.read(files['fills.csv'], f'{label}/fills.csv')
    return dict(balances=balances, positions=positions, fills=parsed_fills, events=events)


def _compare(left, right, fields, exact, tolerance):
    rows = []
    for key in sorted(left.keys() | right.keys()):
        a, b = left.get(key), right.get(key)
        gaps = {k: a[k] - b[k] for k in fields} if a is not None and b is not None else None
        state = ('missing_internal' if a is None else 'missing_broker' if b is None else
                 'matched' if all(abs(v) <= (ZERO if k in exact else tolerance) for k, v in gaps.items()) else 'mismatch')
        rows.append(dict(key=list(key) if isinstance(key, tuple) else [key], state=state,
                         internal=a, broker=b, internal_minus_broker=gaps))
    return rows


def _checks(bundle, scope, dates, tolerance):
    checks = []

    def record(kind, day, expected, actual, *, code=None, exact=False):
        gap = None if actual is None or expected is None else actual - expected
        checks.append(dict(check=kind, date=day, code=code, expected=expected, actual=actual,
                           actual_minus_expected=gap,
                           state='missing' if gap is None else 'matched' if abs(gap) <= (ZERO if exact else tolerance) else 'mismatch'))

    by_date = defaultdict(dict)
    for (day, code), position in bundle['positions'].items():
        by_date[day][code] = position
        record('position_mark', day, position['quantity'] * position['price'], position['market_value'], code=code)
    for day in dates:
        balance, positions = bundle['balances'].get(day), by_date[day]
        record('snapshot_present', day, Decimal(1), Decimal(1) if balance is not None else None, exact=True)
        if balance is not None:
            record('position_count', day, Decimal(len(positions)), balance['position_count'], exact=True)
            record('market_value_sum', day, sum((p['market_value'] for p in positions.values()), ZERO), balance['market_value'])
            record('equity_identity', day, balance['cash'] + balance['market_value'], balance['equity'])
    opening = bundle['balances'].get(dates[0])
    if opening is None:
        # No guessed zero-cash/empty opening inventory.
        return checks, 'insufficient'
    cash = opening['cash']
    inventory = {code: p['quantity'] for code, p in by_date[dates[0]].items()}
    fill_changes, event_changes = defaultdict(list), defaultdict(list)
    for fill in bundle['fills']:
        fill_changes[fill['date']].append(fill)
    for event in bundle['events'].values():
        event_changes[event['date']].append(event)
    transaction_dates = sorted(set(fill_changes) | set(event_changes))
    next_transaction = 0
    for day in dates[1:]:
        while next_transaction < len(transaction_dates) and transaction_dates[next_transaction] <= day:
            transaction_day = transaction_dates[next_transaction]
            next_transaction += 1
            for fill in fill_changes[transaction_day]:
                sign = 1 if fill['side'] == 'buy' else -1
                quantity = sign * fill['quantity']
                cash -= quantity * fill['price'] + fill['fee']
                inventory[fill['code']] = inventory.get(fill['code'], ZERO) + quantity
            for event in event_changes[transaction_day]:
                cash += event['cash_delta']
                if event['quantity_delta']:
                    inventory[event['code']] = inventory.get(event['code'], ZERO) + event['quantity_delta']
        balance, observed = bundle['balances'].get(day), by_date[day]
        record('cash_rollforward', day, cash, balance['cash'] if balance else None)
        for code in sorted(inventory.keys() | observed.keys()):
            predicted = inventory.get(code, ZERO)
            # Omitted security means zero only under the explicit complete-inventory declaration.
            record('quantity_rollforward', day, predicted, observed.get(code, {}).get('quantity', ZERO), code=code, exact=True)
            if predicted < 0:
                record('long_only_inventory', day, ZERO, predicted, code=code, exact=True)
        if cash < 0:
            record('nonnegative_total_cash', day, ZERO, cash, exact=True)
    return checks, 'checked'


def _serialize(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    return value


def reconcile(internal, broker, calendar_raw, *, source_kind, money_tolerance='0.01'):
    """Two full declared bundles, never a broker account assembled from backtests."""
    with localcontext() as context:
        context.prec = 80
        return _reconcile(internal, broker, calendar_raw, source_kind, money_tolerance)


def _reconcile(internal, broker, calendar_raw, source_kind, money_tolerance):
    if source_kind not in executions.SOURCE_KINDS:
        raise ValueError('须明确声明 broker_export/simulator_native/synthetic_fixture 来源')
    if set(internal) != set(FILES) or set(broker) != set(FILES):
        raise ValueError('双方均须提交scope、balances、positions、fills、events五个文件')
    scope = _scope(internal['scope.json'])
    if _scope(broker['scope.json']) != scope:
        raise ValueError('双方账户、币种、期间、现金及完整性声明必须一致')
    tolerance = _number(money_tolerance)
    if tolerance > Decimal('0.01'):
        raise ValueError('逐项金额容差不得超过0.01元；不能放宽容差抹平差异')
    sessions = _calendar(calendar_raw, scope)
    dates = [(_day(scope['start_date']) - timedelta(days=1)).isoformat(), *sessions]
    a, b = (_read_bundle(files, scope, sessions, label) for files, label in [(internal, 'internal'), (broker, 'broker')])
    balances = _compare(a['balances'], b['balances'], ('cash', 'market_value', 'equity', 'position_count'), {'position_count'}, tolerance)
    positions = _compare(a['positions'], b['positions'], ('quantity', 'price', 'market_value'), {'quantity', 'price'}, tolerance)
    event_rows = []
    for key in sorted(a['events'].keys() | b['events'].keys()):
        x, y = a['events'].get(key), b['events'].get(key)
        matched = (x is not None and y is not None and x['kind'] == y['kind'] and x['code'] == y['code'] and
                   x['quantity_delta'] == y['quantity_delta'] and abs(x['cash_delta'] - y['cash_delta']) <= tolerance)
        event_rows.append(dict(date=key[0], event_id=key[1], state='matched' if matched else
                               'missing_internal' if x is None else 'missing_broker' if y is None else 'mismatch', internal=x, broker=y))
    trade_rows = executions.reconcile(internal['fills.csv'], broker['fills.csv'], str(tolerance), str(tolerance), source_kind)
    # Empty complete transaction files are valid for an explicitly declared flat period.
    trade_breaks = trade_rows['breaks']
    checks_a, roll_a = _checks(a, scope, dates, tolerance)
    checks_b, roll_b = _checks(b, scope, dates, tolerance)
    breaks = sum(x['state'] != 'matched' for rows in (balances, positions, event_rows, checks_a, checks_b) for x in rows) + trade_breaks
    missing = any(day not in bundle['balances'] for bundle in (a, b) for day in dates)
    return _serialize(dict(schema_version='account_reconciliation_v1', source_kind=source_kind,
        source_independence='not_verified', source_authenticated=False, real_broker_validation=False,
        status='insufficient' if missing else 'breaks' if breaks else 'matched',
        scope=scope, expected_snapshot_dates=dates, money_tolerance=tolerance,
        quantity_tolerance='0', price_tolerance='0', breaks=breaks,
        same_input_bytes=all(internal[name] == broker[name] for name in FILES),
        input_sha256={label: {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}
                      for label, files in [('internal', internal), ('broker', broker)]},
        calendar_sha256=hashlib.sha256(calendar_raw).hexdigest(), calendar_source_authenticated=False,
        balances=balances, positions=positions, events=event_rows,
        executions=trade_rows, rollforward={'internal': dict(status=roll_a, checks=checks_a),
                                          'broker': dict(status=roll_b, checks=checks_b)},
        limitations=['仅核对单币种、只做多现货的成交日总现金及整数库存；不核验可用/冻结/结算资金、T+1、成本基础或盘中透支。',
                     '完整性、期初、现金及公司行动由来源声明；不认证文件独立性、事件权益/税率或行情真实性。',
                     '双方同错仍可能一致；逐日独立推演可查共同记账矛盾，不能发现双方共同遗漏且已一致改写的全部原始材料。',
                     '期初日为start_date前一自然日；起止日须为声明开市日，周末事件滚入下一快照。',
                     '不按价格猜测订单或事件映射，不抵消日期/证券/订单间差异；matched不是机构认证。']))
