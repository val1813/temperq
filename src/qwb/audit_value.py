"""Economic decisions using submitted data only; no market-data acquisition."""
from __future__ import annotations

from collections import defaultdict


def holding_control(bundle, context):
    """First-close holdings carried unchanged. No fictitious dividends or prices.

    Explicit no-event/no-flow and unadjusted-price declarations are necessary:
    an actual portfolio's absolute dividends cannot be reused for another path.
    """
    spec, snaps = bundle.get('spec') or {}, bundle.get('snapshots') or []
    reasons = []
    if context.get('accounting', {}).get('status') != 'verified':
        reasons.append('先核实账户与事件，才能比较操作净价值')
    declarations = {'control_no_corporate_actions': '本区间无分红送转等公司行动',
                    'control_no_external_flows': '本区间无外部出入金',
                    'control_unadjusted_prices': '对照价格为同一币种的不复权收盘价'}
    for key, label in declarations.items():
        if str(spec.get(key, '')).lower() != 'true':
            reasons.append('需在 strategy_spec.csv 确认：' + label)
    if any(a.get('cash') or a.get('quantity_change') for a in bundle.get('actions') or []):
        reasons.append('本区间有公司行动；需按对照持仓重新计算事件权益，当前不套用原策略绝对金额')
    if len(snaps) < 2:
        reasons.append('需要至少两个每日账户快照')
    if reasons:
        return dict(status='insufficient', reasons=reasons, data_policy='user_supplied_only')
    first, last = snaps[0], snaps[-1]
    holdings = [p for p in bundle.get('positions') or [] if p['date'] == first['date']]
    if not holdings:
        return dict(status='insufficient', reasons=['首日收盘没有持仓；全现金持有不作为选股策略的同任务对照'])
    marks = defaultdict(dict)
    for p in bundle.get('prices') or []:
        day = marks[p['date']]
        if p['code'] in day and abs(day[p['code']] - p['close']) > 1e-8:
            return dict(status='insufficient', reasons=['同日同证券价格冲突，不能择一计算'])
        day[p['code']] = p['close']
    missing = [dict(date=s['date'], code=p['code']) for s in snaps for p in holdings
               if marks[s['date']].get(p['code'], 0) <= 0]
    if missing:
        return dict(status='insufficient', reasons=[f'补首日持有标的全区间收盘价：缺{len(missing)}个证券日期'], missing=missing)
    if any(abs(marks[first['date']][p['code']] - p['price']) > .00001 for p in holdings):
        return dict(status='insufficient', reasons=['首日对照价格与持仓估值不一致，先核对复权和单位'])
    listed = {r['code']: r for r in bundle.get('listings') or []}
    if any(p['code'] not in listed for p in holdings):
        return dict(status='insufficient', reasons=['补首日持有标的的上市退市日期，核对能否持有到末日'])
    if any(listed[p['code']]['listed_date'] > first['date'] or
           (listed[p['code']].get('delisted_date') and listed[p['code']]['delisted_date'] <= last['date']) for p in holdings):
        return dict(status='insufficient', reasons=['对照标的生命周期不覆盖全区间；需要资格退出与退市支付规则'])
    fees_by_day = defaultdict(float)
    for f in bundle.get('fills') or []:
        if f['date'] > first['date']:
            fees_by_day[f['date']] += f['fee']
    rows, accrued = [], 0.
    for s in snaps:
        accrued += fees_by_day[s['date']]
        hold_equity = first['cash'] + sum(p['quantity'] * marks[s['date']][p['code']] for p in holdings)
        rows.append(dict(date=s['date'], actual_equity=s['equity'], hold_equity=hold_equity,
                         extra_fees=accrued, net_difference=s['equity'] - hold_equity))
    def dd(values):
        peak, worst = values[0], 0.
        for v in values:
            peak = max(peak, v)
            worst = max(worst, 1 - v / peak)
        return worst
    net, fees = rows[-1]['net_difference'], rows[-1]['extra_fees']
    actual_dd, hold_dd = (dd([r[k] for r in rows]) for k in ('actual_equity', 'hold_equity'))
    return dict(status='computed', start=first['date'], end=last['date'], baseline=first['equity'],
        actual_net=last['equity']-first['equity'], hold_net=rows[-1]['hold_equity']-first['equity'],
        net_difference=net, extra_fees=fees, gross_path_difference=net+fees,
        actual_drawdown=actual_dd, hold_drawdown=hold_dd, rows=rows,
        decision='retain_original' if net <= 0 else 'historical_advantage',
        verdict='本段后续操作没有增加净收益；先保留现有版本，别据此追加复杂调仓。' if net <= 0 else
                '本段后续操作增加了净收益；冻结规则后继续验证，不把历史差额当未来承诺。',
        scope='同首日收盘仓位和现金起点，持有对照不再交易、现金零收益、不作末日清仓；原版计实际已扣费用。'
              '仅适用于已声明无公司行动和出入金的区间；不代替资格退出或风险再平衡政策。')


def analyze(bundle, context):
    account = context.get('accounting') or {}
    inferred = (context.get('evidence') or {}).get('inferred_count', 0)
    control = holding_control(bundle, context)
    if inferred:
        decision, title = 'verify_events', '先核实推定事件，再引用投资盈利'
    elif account and account.get('status') != 'verified':
        decision, title = 'repair_account', '先修复现金、库存或估值差额'
    elif not account:
        decision, title = 'complete_account', '先补同次回测的账户、成交与持仓'
    elif control['status'] == 'computed':
        decision, title = control['decision'], control['verdict']
    else:
        decision, title = 'freeze_and_compare', '账目核对后，先做同条件比较再改策略'
    return dict(status='computed', decision=decision, title=title, holding_control=control,
        data_policy='user_supplied_only',
        research_record=dict(code_digest_present=bool((bundle.get('code') or {}).get('source')),
            trial_count_declared=(bundle.get('spec') or {}).get('trials'),
            seen_status='uploaded_history_not_independent_holdout',
            selection_adjustment='not_certified',
            next_step='冻结一个改动与接受条件，在原回测平台复跑；保留失败结果和完整参数记录。'))
