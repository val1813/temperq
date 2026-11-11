"""Portable material coverage from the existing review, not a new detector."""
from __future__ import annotations

from . import audit_input
from .experiments import object_digest


def markdown(bundle):
    review = audit_input.review(bundle)
    modes = {'static': '仅代码静态检查', 'partial': '部分材料核对', 'full': '完整账务核对候选'}

    def line(value):
        return ' '.join(str(value).splitlines())

    text = ['# TemperQ 材料核对清单', '', f'策略：{line(bundle["name"])}',
            f'平台：{review["platform"]}', f'当前材料范围：{modes[review["mode"]]}',
            f'输入摘要：{object_digest(bundle)}',
            f'覆盖日期：{review["actual_start"] or "未提供"} 至 {review["actual_end"] or "未提供"}', '',
            '## 现在可以先做什么', '']
    if not review['can_analyze']:
        text.append('先补原策略代码。当前材料尚不能启动审计；代码只做静态读取，不执行。')
    elif review['mode'] == 'static':
        text.append('先核对代码中的字面参数与策略说明；当前不能核验收益、费用或回撤。')
    elif review['fills_inferred']:
        text.append('可核对持仓推算的净变动和明确标注的价格情景；不能据此核验真实逐笔成交与费用。')
    elif review['mode'] == 'partial':
        text.append('可核对已有成交及费用情景；缺完整账户路径时，不据此给出完整盈亏归因或回撤。')
    else:
        text.append('可先运行成交、库存、现金与权益核对，再看归因。材料齐全不等于账目已通过，也不证明策略有效。')
    text += ['', '## 优先核对与补齐', '']
    pending = [c for c in review['checks'] if c['status'] != 'consistent']
    if not pending:
        text.append('当前输入核对没有列出待补项；仍须运行审计并阅读具体检查范围。')
    for check in pending:
        text += [f'### {line(check["title"])}', line(check['detail']),
                 f'下一步：{line(check["action"])}', '']
    text += ['', '## 按问题准备额外材料', '',
             '- 想解释信号：补每日打分、完整候选池及所需后续价格；只提供买入标的不能代替候选池。',
             '- 想核对成交时点或未成交：补完整委托、成交回报及对应分钟行情；日志行数不等于委托数量。',
             '- 想比较改版：原版与改版各导出一套同区间材料，保持初始资金、数据和费用口径一致，并说明具体改了什么。',
             '- 想检查两项改动能否叠加：准备原版、只改A、只改B、同时改AB四套材料。', '',
             '## 已识别文件', '']
    for source in review['source_summary']:
        text.append(f'- {line(source["name"])}：{line(source["kind"])}，{source["rows"]}行')
    text += ['', '## 交付范围', '',
             '本清单仅说明本次材料与待核事项，不批准修改或交易。补件与复跑仍须由用户在原平台完成。',
             '清单在本机生成；本操作不会提交材料、购买服务、执行代码或启动纸面账户。',
             '分享前请检查文件名和内容，移除账号、密钥及不希望公开的策略信息。', '']
    return '\n'.join(text)
