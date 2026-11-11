"""Translate completed diagnostics into bounded facts and original-platform tasks.

No external data, execution of uploaded code, or simulated strategy improvements.
Amounts and rankings below describe uploaded records; suggested experiments have
not been run and their acceptance conditions have not been frozen.
"""
from __future__ import annotations

import math

from . import audit_findings


def _finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def money(value):
    return f'{value / 10000:,.1f}万元' if abs(value) >= 10000 else f'¥{value:,.2f}'


def observations(columns):
    from .audit_institutional import checked_attribution
    items = []
    for index, column in enumerate(columns):
        bundle, result = column.get('bundle') or {}, column['result']
        modules, label = result.get('modules') or {}, column['label']
        checked = checked_attribution(result, bundle)
        identity = {'period': label, 'column': index}
        capacity = modules.get('capacity') or {}
        windows = [r for r in capacity.get('windows') or [] if _finite(r.get('participation'))]
        peak = max(windows, key=lambda r: r['participation']) if windows else None
        ceiling = capacity.get('ceiling')
        if peak and _finite(ceiling) and 0 < ceiling < peak['participation']:
            share = peak['participation']
            # A test size derived from the observed peak, never a capacity promise.
            fraction = min(.5, math.floor(ceiling / share * 100) / 100)
            fraction = max(.001, fraction)
            opening = bundle.get('opening') or {}
            initial = opening.get('cash') if opening.get('kind') == 'empty' else None
            if _finite(initial) and initial > 0 and initial * fraction >= 1:
                target = math.floor(initial * fraction)
                action = f'把回测本金从{money(initial)}降到{money(target)}，买卖两侧都限制大单成交，其他设置不变，再跑相同日期。'
            else:
                target = None
                action = (f'在原回测平台把本金缩到原来的{fraction:.1%}；带期初持仓时也按比例缩放，'
                          '买卖两侧都限制大单成交，其他设置不变，再跑相同日期。')
            items.append(dict(**identity, key='capacity', priority=90, amount=share,
                title='订单相对市场成交量偏大',
                fact=f'{label}：{peak["code"]}在{peak["date"]}成交附近5分钟的买卖量，占同时段市场成交量的{share:.1%}。',
                meaning='实盘可能无法按回测价格全部成交，账目对得上也需要检查这件事。',
                action=action, test_fraction=fraction, test_capital=target,
                scope=f'来自已上传成交与分钟量；已覆盖{capacity.get("fills", 0)}/{capacity.get("total_fills", 0)}笔。两表数量须同为股/份；{ceiling:.0%}只是待验证的测试门槛，不是安全容量。',
                acceptance='先确认数量单位一致。比较原版与缩资版的收益率、最大回撤、未成交订单和交易量占比；资金不同不比较绝对盈利金额。'))
        if checked and checked['verified']:
            drawdown = checked.get('drawdown') or {}
            worst = drawdown.get('worst') or []
            risk = ((modules.get('risk_analysis') or {}).get('max_drawdown') or {})
            negative = [r for r in risk.get('stocks') or []
                        if _finite(r.get('net_pnl')) and r['net_pnl'] < 0]
            total_losses = -sum(r['net_pnl'] for r in negative)
            if (worst and total_losses and drawdown.get('loss', 0) > 0 and _finite(drawdown.get('ratio'))
                    and _finite(drawdown.get('gap')) and abs(drawdown['gap']) <= 1):
                stock = min(worst, key=lambda r: r['net_pnl'])
                share = -stock['net_pnl'] / total_losses
                if share >= .5 or drawdown.get('ratio', 0) >= .2:
                    items.append(dict(**identity, key='drawdown', priority=80, amount=drawdown['ratio'],
                        title='下跌时，亏损集中在少数标的' if share >= .5 else '这段历史跌幅较大',
                        fact=f'{label}从高点跌了{drawdown["ratio"]:.1%}；这段下跌中，{stock["code"]}亏了{money(-stock["net_pnl"])}，占所有亏损标的金额的{share:.1%}。',
                        meaning='先关注这段亏损能否承受；降低手续费不能解决持仓下跌。',
                        action='保留原版，另做一个限制单只标的仓位的版本，只改这一项，再跑相同日期，比较跌幅和少赚了多少。',
                        scope='仅这段最大回撤区间的逐标的盈亏分解，不是全期亏损，也不证明某标的是唯一原因。',
                        acceptance='比较前写下可接受的最大跌幅及愿意牺牲的收益；限制仓位后若跌幅未改善且收益下降，保留原版。'))
        premise = modules.get('premise') or {}
        overall = premise.get('overall') or {}
        if premise.get('status') in {'not_supported', 'reversed'} and _finite(overall.get('t')):
            reversed_ = premise['status'] == 'reversed'
            items.append(dict(**identity, key='premise', priority=85, amount=abs(overall.get('mean') or 0),
                title='选得靠前的标的，随后反而表现较差' if reversed_ else '还没测出选股规则的稳定优势',
                fact=f'{label}的{overall.get("periods", 0)}个选股周期里，'
                     + ('排名越靠前的标的，随后收益反而越低。' if reversed_ else '还没测出“排名越靠前，随后赚得越多”的稳定关系。'),
                meaning='暂缓把选股规则当作扩大资金的依据；没有测出优势不等于已经证明随机选更好。',
                action='保留原选股版，再用同一候选池做一个简单等权对照，在原平台用相同日期、资金和交易成本回测后一起上传。',
                scope='已有排名与后续价格的探索性历史检查；没有运行随机选股实验，也没有认证未来选股能力。',
                acceptance='比较扣费后的收益率及最大回撤；若原选股版收益不高于简单对照且回撤也未改善，暂缓采纳该选股规则，保留对照和失败记录。'))
    return sorted(items, key=lambda r: (-r['priority'], -r['amount'], r['column']))


def tasks(columns):
    """At most two distinct experiments, with the strongest observed example."""
    chosen = []
    for item in observations(columns):
        if item['key'] not in {x['key'] for x in chosen}:
            chosen.append(item)
        if len(chosen) == 2:
            break
    return chosen


def refine(rows, columns, decision):
    """Retain account repair precedence while promoting completed economic facts."""
    from .audit_institutional import checked_attribution
    items = observations(columns)
    ready = [checked_attribution(c['result'], c.get('bundle')) for c in columns]
    if not columns or not all(a and a['verified'] for a in ready):
        return rows, decision
    answers = list(rows)
    capacity = next((r for r in items if r['key'] == 'capacity'), None)
    question, (state, lead, detail) = answers[0]
    detail += ' 这确认的是上传记录内部一致。'
    if capacity:
        detail += ' ' + capacity['fact'] + capacity['meaning']
        state = 'evidence'
    answers[0] = (question, (state, lead, detail))
    drawdowns = [r for r in items if r['key'] == 'drawdown']
    if drawdowns:
        worst = max(drawdowns, key=lambda r: r['amount'])
        question, (state, lead, detail) = answers[1]
        answers[1] = (question, ('adjust', lead, detail + ' ' + worst['fact'] + worst['meaning']))
    selected = tasks(columns)
    if selected:
        premise = next((r for r in items if r['key'] == 'premise'), None)
        detail = ' '.join(f'{"①" if i == 0 else "②"}{r["period"]}：{r["action"]}' for i, r in enumerate(selected))
        if premise:
            count = len({r['column'] for r in items if r['key'] == 'premise'})
            detail += f' 已检验的{count}期选股规则尚未显示正向排序优势，先验证，再把它作为扩大资金的依据。'
        # A measured fee inconsistency precedes experiments. Same-minute funding
        # dependence alone is not a confirmed funding error and stays in details.
        blockers = [(c['label'], r) for c in columns for r in audit_findings.summarize(c['result'])
                    if r['key'] == 'fee' and r['state'] == 'adjust']
        if blockers:
            label, blocker = blockers[0]
            detail = f'先处理{label}：逐笔核对回测代码的费率、订单收费方式与导出费用，修正后重跑。然后按任务单完成下面的对照。'
        answers[2] = ('先做什么', ('adjust', '先处理已发现的问题，再比较修改是否值得', detail))
        decision = '先保留原版，按任务单一次只改一项；回测后上传原版与修改版，再决定保留哪一版。'
    return answers, decision


def plan(columns):
    """Downloadable, reviewable instructions; no hidden data or automatic runs."""
    from .audit_institutional import checked_attribution
    from .experiments import object_digest
    lines = ['# 下一步复跑任务单', '',
        '只使用你在原平台已有的数据。本工具不提供或下载数据，不执行上传代码。',
        '这是尚未执行的实验建议；接受条件须在看修改版结果之前确认，本任务单本身不证明条件已事前冻结。', '',
        '## 先确认可以比较', '',
        '保留原版与全部失败结果。每次只改一项；使用同一平台、相同日期、成本及价格定义。',
        '有期初持仓时同步处理持仓与现金；资金不同只比较收益率、回撤等比例指标，不按绝对盈利金额评优。',
        '若平台、股票池、执行时刻或数据版本也有变化，逐项列出，不能把差额全归给本次修改。', '']
    ready = all((checked_attribution(c['result'], c.get('bundle')) or {}).get('verified') for c in columns)
    if not ready:
        lines += ['## 先补凭证或修复账目', '',
            '当前至少一期的账户或逐标的盈亏尚未核实。先按首屏补分红送转、资金流水或修复账户差额，再作版本比较。', '']
    for index, task in enumerate(tasks(columns), 1):
        column = columns[task['column']]
        result, bundle = column['result'], column.get('bundle') or {}
        dates = [r['date'] for r in bundle.get('snapshots') or []]
        lines += [f'## {index}. {task["period"]} · {task["title"]}', '',
            '已发现：' + task['fact'], '为什么要做：' + task['meaning'],
            '操作：' + task['action'], '判断办法：' + task['acceptance'],
            '计算范围：' + task['scope'],
            f'日期：{dates[0] if dates else "待补"} 至 {dates[-1] if dates else "待补"}',
            '原代码摘要：' + object_digest((bundle.get('code') or {}).get('source') or ''),
            '原报告摘要：' + str(result.get('result_digest') or '未完成'), '']
    if ready and not tasks(columns):
        lines += ['## 当前处理', '', '本次未生成上述专项复跑任务。保留已通过检查与原始版本；下一次改动先写清目标和接受条件。', '']
    lines += ['## 跑完后交回什么', '',
        '分别导出原版、修改版的代码、交易、每日持仓、每日账户、未成交订单及运行设置；保留数据版本声明。',
        '在完整报告的“上传复跑结果并比较”入口上传为新版本；原报告继续保留。',
        '核对日期、基线与改动范围后再比较。省手续费不能直接算作新增盈利；历史胜出也不证明未来会胜出。', '']
    return '\n'.join(lines)
