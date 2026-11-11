"""Point-in-time declared factor exposures and ex-post return decomposition.

No estimated factor returns, guessed missing exposures, calibrated risk model,
or causal alpha. Cash has zero factor exposure. All returns are decimal simple
returns on the exact input intervals; aggregate contributions use separate
portfolio/benchmark geometric linking, never a sum of daily active returns.
"""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import io
import json
import re

MAX_BYTES = 8 * 1024 * 1024
SOURCE_KINDS = ('historical_data', 'synthetic_fixture', 'historical_returns_synthetic_exposures')
FIELDS = {
    'periods': ('period_id', 'start_time', 'end_time', 'portfolio_return', 'benchmark_return', 'source_ref'),
    'weights': ('period_id', 'book', 'code', 'weight', 'asof_time', 'available_at', 'source_ref'),
    'exposures': ('code', 'factor', 'asof_time', 'available_at', 'exposure', 'source_ref'),
    'factor_returns': ('period_id', 'factor', 'return', 'available_at', 'source_ref'),
}
ZERO, ONE = Decimal(0), Decimal(1)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('JSON字段重复')
        result[key] = value
    return result


def _time(value):
    try:
        if not isinstance(value, str) or len(value) > 40:
            raise ValueError()
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is not None and timestamp.utcoffset() is not None:
            return timestamp.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        pass
    raise ValueError('时间须为带时区的ISO时间')


def _number(value, low, high):
    if not isinstance(value, str) or not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,9})(?:\.[0-9]{1,12})?', value):
        raise ValueError('数字须为普通十进制，最多10位整数及12位小数')
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError('无效数字') from None
    if not low <= number <= high:
        raise ValueError('权重、暴露或收益超出声明接口范围')
    return number


def _text(raw, label):
    if not isinstance(raw, bytes) or len(raw) > MAX_BYTES:
        raise ValueError(f'{label}须为不超过8MB的UTF-8文件')
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise ValueError(f'{label}须为UTF-8') from None


def _table(raw, name):
    try:
        table = csv.DictReader(io.StringIO(_text(raw, name)), strict=True)
        headers = table.fieldnames or []
        if len(headers) != len(set(headers)) or set(headers) != set(FIELDS[name]):
            raise ValueError(f'{name}须有唯一且完整表头：{",".join(FIELDS[name])}')
        rows = []
        for line, row in enumerate(table, 2):
            if line > 100001 or None in row or any(v is None or not v.strip() for v in row.values()):
                raise ValueError(f'{name}行数超限或第{line}行字段缺失')
            if any(len(v.strip()) > (1024 if k == 'source_ref' else 128) for k, v in row.items()):
                raise ValueError(f'{name}字段长度超限：source_ref最多1024字，其余字段最多128字')
            rows.append({k: v.strip() for k, v in row.items()})
        return rows
    except csv.Error as exc:
        raise ValueError(f'{name} CSV格式错误') from exc


def _scope(raw):
    try:
        scope = json.loads(_text(raw, 'scope'), object_pairs_hook=_unique)
    except json.JSONDecodeError:
        raise ValueError('scope须为有效JSON') from None
    keys = {'schema_version', 'start_time', 'end_time', 'factors', 'max_exposure_age_days', 'holdings_complete', 'cash_return'}
    if not isinstance(scope, dict) or set(scope) != keys or scope['schema_version'] != 'factor_attribution_scope_v1':
        raise ValueError('scope字段不完整、未知或版本不支持')
    if scope['holdings_complete'] is not True or scope['cash_return'] != 'zero':
        raise ValueError('须明确声明完整期初权重与零现金因子收益约定')
    age = scope['max_exposure_age_days']
    if type(age) is not int or not 0 <= age <= 366:
        raise ValueError('max_exposure_age_days须为0至366整数')
    if not _time(scope['start_time']) < _time(scope['end_time']):
        raise ValueError('scope起止时间无序')
    factors = scope['factors']
    if not isinstance(factors, list) or not 1 <= len(factors) <= 50:
        raise ValueError('须声明1至50个因子')
    seen = set()
    for factor in factors:
        if (not isinstance(factor, dict) or set(factor) != {'name', 'kind', 'definition', 'unit'} or
                any(not isinstance(v, str) or not v.strip() or v != v.strip() for v in factor.values()) or
                factor['kind'] not in ('industry', 'style') or factor['unit'] != 'return_per_unit_exposure' or
                factor['name'] in seen):
            raise ValueError('因子须有唯一name、industry/style、非空definition及return_per_unit_exposure单位')
        if len(factor['name']) > 128 or len(factor['definition']) > 4096:
            raise ValueError('因子name最多128字，definition最多4096字')
        seen.add(factor['name'])
    return scope


def _serialize(value):
    if isinstance(value, Decimal):
        return format(value, 'f')
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_serialize(v) for v in value]
    return value


def analyze(scope_raw, periods_raw, weights_raw, exposures_raw, factor_returns_raw, *, source_kind):
    with localcontext() as context:
        context.prec = 80
        return _analyze(scope_raw, periods_raw, weights_raw, exposures_raw, factor_returns_raw, source_kind)


def _analyze(scope_raw, periods_raw, weights_raw, exposures_raw, factor_returns_raw, source_kind):
    if source_kind not in SOURCE_KINDS:
        raise ValueError('须主动声明historical_data、synthetic_fixture或historical_returns_synthetic_exposures')
    scope = _scope(scope_raw)
    factors = [f['name'] for f in scope['factors']]
    industries = {f['name'] for f in scope['factors'] if f['kind'] == 'industry'}
    periods, weights, exposures, returns = {}, {}, {}, {}
    grouped_weights = {}
    for row in _table(periods_raw, 'periods'):
        if row['period_id'] in periods:
            raise ValueError('period_id重复')
        start, end = _time(row['start_time']), _time(row['end_time'])
        if not start < end:
            raise ValueError('区间起止时间无序')
        for key in ('portfolio_return', 'benchmark_return'):
            row[key] = _number(row[key], -ONE, Decimal(100))
            if row[key] == -ONE:
                raise ValueError('暂不支持归零净值及其后的收益率')
        periods[row['period_id']] = dict(row, start_time=start, end_time=end)
    ordered = sorted(periods.values(), key=lambda r: r['start_time'])
    if len(ordered) > 5000:
        raise ValueError('最多5000个区间')
    if ordered:
        if ordered[0]['start_time'] != _time(scope['start_time']) or ordered[-1]['end_time'] != _time(scope['end_time']):
            raise ValueError('区间未覆盖scope的完整起止时间')
        if any(a['end_time'] != b['start_time'] for a, b in zip(ordered, ordered[1:])):
            raise ValueError('区间必须连续不重叠；不能跨缺失区间拼收益')
    for row in _table(weights_raw, 'weights'):
        key = (row['period_id'], row['book'], row['code'])
        if row['period_id'] not in periods or row['book'] not in ('portfolio', 'benchmark') or key in weights:
            raise ValueError('权重的区间、book或证券身份无效/重复')
        asof, available = _time(row['asof_time']), _time(row['available_at'])
        if asof != periods[row['period_id']]['start_time'] or available != asof:
            raise ValueError('权重须为区间期初状态且已于期初可得；不允许收盘权重回填期初')
        weights[key] = dict(row, weight=_number(row['weight'], ZERO, ONE), asof_time=asof, available_at=available)
        grouped_weights.setdefault((row['period_id'], row['book']), []).append(weights[key])
    for row in _table(exposures_raw, 'exposures'):
        if row['factor'] not in factors or row['code'] == 'CASH':
            raise ValueError('暴露因子未声明或CASH不应提供证券暴露')
        asof, available = _time(row['asof_time']), _time(row['available_at'])
        if asof > available:
            raise ValueError('暴露available_at不得早于asof_time')
        key = (row['code'], row['factor'])
        version = (asof, available)
        choices = exposures.setdefault(key, {})
        if version in choices:
            raise ValueError('证券/因子/时点/版本重复；不能静默去重')
        value = _number(row['exposure'], ZERO if row['factor'] in industries else Decimal(-1000),
                        ONE if row['factor'] in industries else Decimal(1000))
        choices[version] = dict(row, asof_time=asof, available_at=available, exposure=value)
    for row in _table(factor_returns_raw, 'factor_returns'):
        key = (row['period_id'], row['factor'])
        if row['period_id'] not in periods or row['factor'] not in factors or key in returns:
            raise ValueError('因子收益的区间/因子无效或重复')
        available = _time(row['available_at'])
        if available < periods[row['period_id']]['end_time']:
            raise ValueError('完整区间因子收益的available_at不得早于区间结束；本入口是事后归因')
        returns[key] = dict(row, available_at=available, **{'return': _number(row['return'], Decimal(-100), Decimal(100))})
    # Build eligible histories incrementally; an arbitrarily late revision of
    # an older asof must never displace a newer asof already known at the time.
    histories = {key: sorted(values.values(), key=lambda r: r['available_at']) for key, values in exposures.items()}
    cursors, selected = {}, {}
    output, missing = [], []
    for period in ordered:
        period_id, start = period['period_id'], period['start_time']
        required_keys = {(r['code'], f) for book in ('portfolio', 'benchmark')
                         for r in grouped_weights.get((period_id, book), [])
                         if r['code'] != 'CASH' and r['weight'] for f in factors}
        for key in sorted(required_keys):
            history = histories.get(key, [])
            cursor = cursors.get(key, 0)
            choice = selected.get(key)
            while cursor < len(history) and history[cursor]['available_at'] <= start:
                candidate = history[cursor]
                if choice is None or (candidate['asof_time'], candidate['available_at']) > (choice['asof_time'], choice['available_at']):
                    choice = candidate
                cursor += 1
            cursors[key] = cursor
            if choice is not None:
                selected[key] = choice
        books, used = {}, []
        period_missing = []
        for book in ('portfolio', 'benchmark'):
            book_weights = sorted(grouped_weights.get((period_id, book), []), key=lambda r: r['code'])
            if not book_weights or not any(r['code'] == 'CASH' for r in book_weights):
                period_missing.append(dict(period_id=period_id, book=book, reason='missing_complete_weights_or_cash_row'))
            elif abs(sum((r['weight'] for r in book_weights), ZERO) - ONE) > Decimal('0.000000000001'):
                raise ValueError('每期每book含CASH的权重之和须为1；不得自动归一化')
            exposure_totals = {factor: ZERO for factor in factors}
            total_non_cash, known_weight = ZERO, ZERO
            for weight in book_weights:
                if weight['code'] == 'CASH' or not weight['weight']:
                    continue
                total_non_cash += weight['weight']
                chosen = {}
                for factor in factors:
                    choice = selected.get((weight['code'], factor))
                    reason = ('missing_or_not_yet_available' if choice is None else 'stale' if
                              start - choice['asof_time'] > timedelta(days=scope['max_exposure_age_days']) else None)
                    if reason:
                        period_missing.append(dict(period_id=period_id, book=book, code=weight['code'], factor=factor, reason=reason))
                    else:
                        chosen[factor] = choice
                        exposure_totals[factor] += weight['weight'] * choice['exposure']
                        used.append(dict(choice, book=book, weight=weight['weight']))
                if len(chosen) == len(factors):
                    if industries and abs(sum((chosen[f]['exposure'] for f in industries), ZERO) - ONE) > Decimal('0.000000000001'):
                        raise ValueError('证券的行业暴露之和须为1；缺行业或重叠分类不能自动修补')
                    known_weight += weight['weight']
            books[book] = dict(known_factor_exposure=exposure_totals, non_cash_weight=total_non_cash,
                               complete_exposure_weight=known_weight,
                               non_cash_coverage=known_weight/total_non_cash if total_non_cash else ONE)
        for factor in factors:
            if (period_id, factor) not in returns:
                period_missing.append(dict(period_id=period_id, factor=factor, reason='missing_factor_return'))
        detail = dict(period_id=period_id, start_time=start, end_time=period['end_time'],
                      source_ref=period['source_ref'], status='insufficient' if period_missing else 'computed',
                      books=books, selected_exposures=used, missing=period_missing,
                      factor_returns={f: returns[(period_id, f)] for f in factors if (period_id, f) in returns})
        if not period_missing:
            for book in ('portfolio', 'benchmark'):
                contributions = {f: books[book]['known_factor_exposure'][f] * returns[(period_id, f)]['return'] for f in factors}
                observed = period[f'{book}_return']
                predicted = sum(contributions.values(), ZERO)
                books[book].update(factor_contributions=contributions, modeled_return=predicted,
                                   observed_return=observed, residual=observed-predicted,
                                   closure_error=observed - (predicted + (observed-predicted)))
            detail['active'] = dict(exposures={f: books['portfolio']['known_factor_exposure'][f] - books['benchmark']['known_factor_exposure'][f] for f in factors},
                factor_contributions={f: books['portfolio']['factor_contributions'][f] - books['benchmark']['factor_contributions'][f] for f in factors},
                observed_return=period['portfolio_return']-period['benchmark_return'],
                residual=books['portfolio']['residual']-books['benchmark']['residual'])
        output.append(detail)
        missing.extend(period_missing)
    aggregate = None
    if output and not missing:
        aggregate = {}
        for book in ('portfolio', 'benchmark'):
            linked = {f: ZERO for f in factors}
            residual, suffix = ZERO, ONE
            for row in reversed(output):
                values = row['books'][book]
                values['link_multiplier'] = suffix
                for factor in factors:
                    linked[factor] += values['factor_contributions'][factor] * suffix
                residual += values['residual'] * suffix
                suffix *= ONE + values['observed_return']
            total = suffix - ONE
            aggregate[book] = dict(compounded_return=total, linked_factor_contributions=linked,
                                   linked_residual=residual, closure_error=total-sum(linked.values(), ZERO)-residual)
        a, b = aggregate['portfolio'], aggregate['benchmark']
        active_factors = {f: a['linked_factor_contributions'][f] - b['linked_factor_contributions'][f] for f in factors}
        active_total, active_residual = a['compounded_return']-b['compounded_return'], a['linked_residual']-b['linked_residual']
        aggregate['active'] = dict(compounded_return_difference=active_total, linked_factor_contributions=active_factors,
                                   linked_residual=active_residual, closure_error=active_total-sum(active_factors.values(), ZERO)-active_residual)
    return _serialize(dict(schema_version='factor_attribution_v1', status='computed' if output and not missing else 'insufficient',
        source_kind=source_kind, source_authenticated=False, source_independence='not_verified',
        scope=scope, periods=output, missing=missing, aggregate=aggregate,
        input_sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in [('scope', scope_raw), ('periods', periods_raw),
                      ('weights', weights_raw), ('exposures', exposures_raw), ('factor_returns', factor_returns_raw)]},
        unused_exposure_versions=sum(len(h) for h in histories.values()) - len({(r['code'], r['factor'], r['asof_time'], r['available_at'])
                                                                                 for row in output for r in row['selected_exposures']}),
        limitations=['期初权重及暴露均由来源声明，时间字段只校验、不认证；不得把归档事件时间当发布时间。',
                     '这是给定因子模型的事后收益分解，因子收益也须有定义；未估计风险协方差、特异风险、VaR或风险预算。',
                     '残差包含模型遗漏、盘中调仓、费用、现金收益及估值口径等，不能称策略alpha、选股因果效应或冲击成本。',
                     '行业暴露可为穿透后的份额，但必须显式提交全部行业；不从当前分类或ETF名称推造历史暴露。',
                     '多期贡献分别按组合和基准实际收益后缀链接后相减；不是累计相对净值比率，不直接累加每日主动收益。',
                     '缺任何正权重证券的因子暴露或任何区间因子收益时，不给完整归因或多期总归因；CASH的因子暴露为零。',
                     '闭合是恒等分解的数值检查，不证明模型正确、来源真实、外部验证通过或达到机构准入。']))
