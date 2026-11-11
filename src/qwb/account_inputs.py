"""Shared account input rules without model training or market downloads."""
from __future__ import annotations

from copy import deepcopy
import io
import re

import pandas as pd

from . import experiments, rules
from .portfolio.reference import ExecutionBar

HOLDING_COLUMNS = {'code', 'quantity', 'sellable_quantity'}


def protocol():
    value = deepcopy(rules.load('daily_strategy_v1'))
    return value, experiments.object_digest(value)


def stock_code(value):
    code = str(value).strip().upper()
    if re.fullmatch(r'(SH|SZ)\.\d{6}', code):
        code = code[3:] + '.' + code[:2]
    if re.fullmatch(r'\d{6}', code):
        code += '.SH' if code.startswith('6') else '.SZ'
    if not re.fullmatch(r'60\d{4}\.SH|68[89]\d{3}\.SH|00\d{4}\.SZ|30\d{4}\.SZ', code):
        raise ValueError(f'不支持的证券 {code}：仅支持沪深主板、创业板与科创板 A 股，北交所等暂不支持')
    return code


def read_holdings(source):
    if source is None or source == b'':
        return []
    if isinstance(source, bytes):
        source = io.BytesIO(source)
    frame = pd.read_csv(source, dtype=str, encoding='utf-8-sig', keep_default_na=False)
    if set(frame.columns) != HOLDING_COLUMNS:
        raise ValueError('持仓 CSV 只能有 code,quantity,sellable_quantity 三列；公司行动、特殊订单等不支持')
    result = []
    for row in frame.to_dict('records'):
        code = stock_code(row['code'])
        for field in ('quantity', 'sellable_quantity'):
            if not re.fullmatch(r'\d+', str(row[field]).strip()):
                raise ValueError(f'{code} 的 {field} 必须是非负整数')
        quantity, sellable = int(row['quantity']), int(row['sellable_quantity'])
        if quantity <= 0 or not 0 <= sellable <= quantity:
            raise ValueError(f'{code} 数量需大于零，且可卖数量不能超过持仓')
        result.append({'code': code, 'quantity': quantity, 'sellable_quantity': sellable})
    if len({r['code'] for r in result}) != len(result):
        raise ValueError('持仓代码重复，请先合并')
    return sorted(result, key=lambda row: row['code'])


def session_bar(code, *, open_price, close_price, upper, lower, suspended=False):
    code = stock_code(code)
    quantity = protocol()[0]['quantity_rules'][rules.cn_board(code)]
    return ExecutionBar(code, open_price, close_price, upper, lower, suspended=suspended,
                        buy_lot=quantity['buy_step'], sell_lot=quantity['sell_step'],
                        minimum_buy=quantity['minimum_buy'], minimum_sell=quantity['minimum_sell'])
