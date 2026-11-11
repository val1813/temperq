"""Concrete completed checks, with observations kept separate from scenarios."""
from __future__ import annotations


def summarize(result):
    modules, rows = result.get('modules', {}), []

    def add(key, title, label, text, action, scope, state='pass'):
        rows.append(dict(key=key, title=title, label=label, text=text,
                         action=action, scope=scope, state=state))

    account, attr = modules.get('accounting') or {}, modules.get('attribution') or {}
    if account.get('status') == 'verified' and attr.get('status') == 'verified':
        add('account', '账户与盈亏', '内部一致',
            f'{len(account["daily"])} 个核账日期闭合；净盈亏 ¥{attr["net_pnl"]:,.2f}，费用 ¥{attr["fees"]:,.2f}。',
            '可用这份闭合账目继续检查收益来源、成交和风险。',
            '核对导出范围内的现金、库存、估值和盈亏桥；不认证策略未来收益。')
    elif account.get('status') == 'provisional':
        add('account', '账户与盈亏', '推定闭合', '使用推定事件后账面闭合，投资盈利仍待原始凭证核实。',
            '先补对应日期的分红送转与出入金流水。', '推定现金不能独立区分分红与入金。', 'evidence')
    elif account:
        add('account', '账户与盈亏', '账目需修复',
            f'发现 {len(account.get("exceptions") or [])} 条账户异常；不把未闭合盈亏作为已验证结果。',
            '先按单期报告的对账明细修复账户材料。', '仅基于本次提交的账户材料。', 'adjust')
    fees = (modules.get('fee_model') or {}).get('self_check') or {}
    if fees.get('priced_fills'):
        consistent = fees.get('status') == 'consistent'
        add('fee', '原代码费用自洽', '已核实' if consistent else '发现差异',
            f'{fees["priced_fills"]} 笔已核价成交；导出费用与原代码计费重算差额 ¥{fees["gap"]:,.2f}。',
            '原代码费用在该范围内自洽，无需据此改费率。' if consistent else '逐笔核对代码费率、收费单位及费用明细。',
            '核对代码声明与回测费用的一致性；券商实际收费需另有交割依据。',
            'pass' if consistent else 'adjust')
    selection = (modules.get('decisions') or {}).get('against_selection') or {}
    if selection.get('checked'):
        bad = selection.get('against', 0)
        add('selection', '选股到实际买入', '发现偏离' if bad else '已核实',
            f'{selection["checked"]} 笔买入可对照当天选择，其中 {bad} 笔买入了未选中的标的。',
            '核对目标版本与下单分支。' if bad else '本次已匹配买入与记录的选股一致，保留这条已通过检查。',
            '只核对已匹配记录；不替代完整目标权重和数量检查。', 'adjust' if bad else 'pass')
    quality = modules.get('fill_quality') or {}
    price = quality.get('range') or {}
    if price.get('fills'):
        suspicious = bool(price.get('outside') or price.get('status') == 'suspicious')
        add('price', '成交价格检查', '需核对' if suspicious else '已检查',
            f'{price["fills"]} 笔覆盖日内区间；{price["outside"]} 笔超出区间，最有利一成价格占 {price["best_share"]:.1%}。',
            '核对价格复权口径及撮合条件。' if suspicious else '已覆盖成交未触发本项价格异常检查。',
            '按已提供的全天区间描述，不证明排队或盘口可成交。', 'adjust' if suspicious else 'pass')
    listings = modules.get('listings') or {}
    if listings.get('checked'):
        bad = len(listings.get('before_listing') or []) + len(listings.get('after_delisting') or [])
        add('listings', '上市日期检查', '发现异常' if bad else '已检查', listings['verdict'],
            '按单期明细核对历史证券身份。' if bad else '保留已核对日期；历史股票池形成过程另看原策略记录。',
            '使用提交的上市退市记录，不将当前存续名单当作历史全市场证明。', 'adjust' if bad else 'pass')
    premise = modules.get('premise') or {}
    overall = premise.get('overall') or {}
    if overall.get('periods') and overall.get('mean') is not None:
        status = premise.get('status')
        usable = status in {'held', 'reversed', 'not_supported'}
        conclusion = {'held': '本段测出正向关系', 'reversed': '本段测出反向关系',
                      'not_supported': '本段未测出明确排序优势'}.get(status, '本次有效样本或价格覆盖不足')
        t = overall.get('t')
        add('premise', '排名与后续收益', '历史诊断' if usable else '覆盖不足',
            f'{conclusion}；{overall["periods"]} 个信号期，平均 IC {overall["mean"]:+.3f}'
            + (f'，HAC t={t:+.2f}。' if t is not None else '。'),
            '冻结排名规则后在后续日期复验；不能由账户盈利直接推断选股有优势。',
            premise.get('scope', '') + '；探索性检验，未做多次搜索校正。', 'evidence')
    capacity = modules.get('capacity') or {}
    if capacity.get('window_count') and capacity.get('p95') is not None:
        exceeded = capacity.get('exceeding_windows', 0)
        add('capacity', '成交量参与率', '超过情景门槛' if exceeded else '已核算',
            f'{capacity["fills"]}/{capacity["total_fills"]} 笔有完整量窗；95% 分位 {capacity["p95"]:.2%}，'
            f'{exceeded} 个窗口超过 {capacity["ceiling"]:.0%} 情景门槛。',
            '先核对数量单位；在原引擎限制参与率或降低资金重跑，比较未成交、收益和成本。' if exceeded else
            '本次已覆盖窗口未超情景门槛；扩大资金前需另做撮合与冲击检查。',
            capacity['scope'], 'adjust' if exceeded else 'pass')
    funding = (modules.get('sequence') or {}).get('funding') or {}
    if funding.get('checked_switches'):
        dependent = funding.get('dependent', 0)
        add('sequence', '换仓资金顺序', '存在资金依赖' if dependent else '已检查',
            f'{funding["checked_switches"]} 次同分钟换仓中，{dependent} 次买入依赖同分钟卖出回款。',
            '核对卖单成交回报；以实际到账资金约束后续买单，并在原引擎复跑。' if dependent else
            '已核对记录未触发同分钟回款依赖。', funding['scope'] + '；资金依赖本身不证明成交错误。',
            'adjust' if dependent else 'pass')
    stress = quality.get('floor') or {}
    if account.get('status') == 'verified' and stress.get('fills') and stress.get('net_after') is not None:
        add('stress', '成交后极端价压力', '压力情景',
            f'覆盖成交额 {stress["coverage"]:.1%}；逐笔换为后 {stress["window"]} 分钟极端价，'
            f'净盈亏变为 ¥{stress["net_after"]:,.2f}（变化 ¥{stress["change"]:+,.2f}）。',
            '据此确定需要复跑的执行压力，保持数量、资金和后续信号联动重新回测。', stress['scope'], 'evidence')
    return rows
