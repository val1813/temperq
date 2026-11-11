"""Reader-facing presentation of completed diagnostics; no new audit calculations."""
from __future__ import annotations

from .governance_review_ui import esc


CHAPTERS = (
    ('report-conclusions', '一、结论摘要'), ('report-premise', '二、策略前提检验'),
    ('report-performance', '三、收益与风险'), ('report-attribution', '四、盈亏归因'),
    ('report-costs', '五、成本与执行'), ('report-findings', '六、发现与建议'),
    ('report-scope', '七、审计范围与限制'),
)

STYLE = """
.tq-report{--ink:#1c2432;--mut:#5b6575;--line:#d9dee6;--soft:#f4f6f9;--pass:#237a51;--adjust:#b3261e;--evidence:#9a6400;color:var(--ink);font:15px/1.8 'DM Sans',system-ui,'Microsoft YaHei',sans-serif;overflow-wrap:anywhere}
.tq-report *{box-sizing:border-box}.tq-report h1{font-size:28px;line-height:1.4;margin:0 0 12px}.tq-report h2{font-size:20px;line-height:1.5;margin:36px 0 16px;border-bottom:2px solid #263c5a;padding-bottom:10px;scroll-margin-top:20px}.tq-report h3{font-size:16px;margin:22px 0 8px}.tq-report p{margin:8px 0}.tq-report .mut{color:var(--mut);font-size:13px}.tq-report .cover{padding:0 0 22px;border-bottom:1px solid var(--line)}.tq-report .kicker{font-size:12px;color:var(--mut);letter-spacing:.06em}.tq-report .meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 22px;font-size:12px;margin-top:18px}.tq-report .meta span{color:var(--mut)}.tq-report code{font-size:12px;overflow-wrap:anywhere}.tq-report .reader-intro{margin:22px 0 12px}.tq-report .reader-intro h2{font-size:20px;border:0;margin:0 0 6px;padding:0}.tq-report .answers{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:16px 0 20px;align-items:start}.tq-report .answer{border:1px solid var(--line);border-top:3px solid currentColor;border-radius:7px;padding:16px 18px;background:#fff;min-width:0}.tq-report .answer h3{font-size:16px;display:flex;align-items:center;justify-content:space-between;gap:8px;margin:0 0 12px;color:var(--ink)}.tq-report .answer .lead{font-size:17px;font-weight:650;line-height:1.6;color:var(--ink)}.tq-report .answer ul{padding-left:18px;color:var(--ink);font-size:14px;margin:10px 0}.tq-report .answer li{margin:7px 0}.tq-report .answer a{font-size:12px}.tq-report .pass{color:var(--pass)}.tq-report .evidence{color:var(--evidence)}.tq-report .adjust,.tq-report .neg{color:var(--adjust)}.tq-report .tag{display:inline-block;font-size:11px;border:1px solid currentColor;border-radius:4px;padding:1px 5px;white-space:nowrap;margin-right:5px}.tq-report .answer .tag{margin:0;font-weight:400}.tq-report .reader-basis{padding:12px 16px;border-left:3px solid #2959d9;background:#eef3ff;font-size:13px;color:#344866}.tq-report .reader-links{display:flex;flex-wrap:wrap;gap:12px 22px;font-size:13px;margin:18px 0}.tq-report .reader-nav{display:flex;flex-wrap:wrap;gap:8px 20px;border-block:1px solid var(--line);padding:14px 0;margin:24px 0;font-size:13px}.tq-report .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(145px,1fr));gap:12px;margin:18px 0}.tq-report .kpis>div{padding:12px 14px;background:var(--soft);border-radius:6px}.tq-report .kpis span{font-size:12px;color:var(--mut);display:block}.tq-report .kpis strong{font-size:22px;font-variant-numeric:tabular-nums}.tq-report .tw{max-width:100%;overflow:auto;margin:12px 0}.tq-report table{border-collapse:collapse;width:100%;font-size:13px;line-height:1.8}.tq-report th,.tq-report td{padding:9px 10px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;overflow-wrap:anywhere}.tq-report th{background:var(--soft);color:#465365}.tq-report td.n,.tq-report th.n{text-align:right;font-variant-numeric:tabular-nums}.tq-report .card{border:1px solid var(--line);border-radius:6px;padding:16px 18px;margin:16px 0;break-inside:avoid}.tq-report .card h3{margin:0 0 10px}.tq-report details{margin:12px 0}.tq-report summary{cursor:pointer;color:#2959d9;font-size:13px}.tq-report .findings{padding-left:22px}.tq-report .findings li{margin:10px 0}.tq-report .glossary{display:grid;grid-template-columns:max-content 1fr;gap:8px 20px;font-size:13px}.tq-report .glossary dd{margin:0}.tq-report footer{margin-top:32px;padding-top:16px;border-top:1px solid var(--line);font-size:12px;color:var(--mut)}
.tq-report .reader-waterfall{padding:8px 0 18px}.tq-report .reader-waterfall>div{display:grid;grid-template-columns:180px 1fr 150px;align-items:center;gap:20px;margin:14px 0;font-size:14px}.tq-report .reader-bar{height:16px;background:#e9edf4;border-radius:4px;overflow:hidden}.tq-report .reader-bar i{display:block;height:100%;background:#2b52db}.tq-report .reader-bar i.negative{background:#b98a31}.tq-report .reader-waterfall strong{text-align:right;font-variant-numeric:tabular-nums}
@media(max-width:760px){.tq-report .reader-waterfall>div{grid-template-columns:1fr 115px;gap:6px;font-size:12px}.tq-report .reader-bar{grid-column:1;grid-row:2}.tq-report .reader-waterfall strong{grid-column:2;grid-row:1/3}}
@media(max-width:760px){.tq-report .answers{grid-template-columns:1fr}.tq-report .meta{grid-template-columns:1fr 1fr}.tq-report h1{font-size:24px}.tq-report .kpis{grid-template-columns:1fr 1fr}.tq-report .glossary{grid-template-columns:1fr}.tq-report .glossary dd{margin-bottom:8px}}
@media(prefers-color-scheme:dark){.tq-report{background:#fff;color-scheme:light;padding:24px;border-radius:8px}}
@media print{.tq-report{font-size:11px;color:#1c2432}.tq-report .reader-links,.tq-report .reader-nav,.tq-report .answer a{display:none}.tq-report .answers{grid-template-columns:repeat(3,minmax(0,1fr))}.tq-report .answer{padding:10px}.tq-report .answer .lead{font-size:13px}.tq-report .answer ul{font-size:11px}.tq-report .answer h3{font-size:12px}.tq-report h2{break-after:avoid}.tq-report .card,.tq-report tr{break-inside:avoid}.tq-report .tw{overflow:visible}.tq-report .reader-statistics:not([open]){display:none}}
"""


def premise_answer(premise):
    status = (premise or {}).get('status')
    return {
        'held': ('pass', '这段历史里，排序前提得到支持', '这是样本内结果，还要看后续独立时期能否重复。'),
        'reversed': ('adjust', '这段历史里，排序关系与前提相反', '先核对打分方向和数据日期；历史上的反向关系不保证未来继续。'),
        'not_supported': ('evidence', '还没证实这套选法有优势', '本次未测出分数与随后涨跌的稳定关系；这不等于前提已被证明错误。'),
        'insufficient': ('evidence', '样本还不足以判断选法', '有效观察期太少，本次不下支持或否定的结论。'),
        'incomplete_prices': ('evidence', '候选价格不完整，暂不能判断', '先补齐候选池和相应日期的价格，再检验打分与随后收益的关系。'),
        'needs_scores': ('evidence', '缺少每日打分，本次尚未检验', '补覆盖全部候选的每日打分与候选池价格，再检验选股依据。'),
        'needs_prices': ('evidence', '缺少候选价格，本次尚未检验', '补同一回测的候选池价格，再检验打分后的涨跌关系。'),
    }.get(status, ('evidence', '策略前提尚未完成检验', '查看第二章的缺项说明；没有完成检验，不能当作前提成立。'))


def summary(bundle, result, perf, premise, cards):
    from .audit_report import answers
    from .audit_institutional import inferred_events
    original = answers(bundle, result, perf, premise, cards)
    modules = result.get('modules') or {}
    account = modules.get('accounting') or {}
    events = inferred_events(bundle)
    verified = account.get('status') == 'verified' and not events['count']
    trust = original[0]['tone']
    if verified:
        lead = '账目对得上，执行仍要核查'
        notes = [f'{len(bundle.get("fills") or [])} 笔成交、{len(bundle.get("snapshots") or [])} 个账户快照已读入；已核对 {len(account.get("daily") or [])} 个日期。']
        exits = (modules.get('activity') or {}).get('exits') or {}
        if exits.get('failed'):
            notes.append(f'{exits["sell_orders"]} 笔卖出委托中，{exits["failed"]} 笔未成交完，收盘仍持有；最长连续 {exits.get("longest_run", 0)} 个交易日。')
        if len(notes) == 1:
            notes.append('核账通过只说明上传记录内部一致；还须检查成交条件与未覆盖项目。')
        if trust == 'adjust':
            lead = '账目可复算，仍有问题需处理'
    elif events['count']:
        trust, lead = 'evidence', '有推定资金变化，收益待核实'
        notes = [f'{events["count"]} 条分红或股数变化由账户差额推定，尚缺原始凭证。', '先补分红送转和出入金记录；账面平衡不能证明这些差额就是投资收益。']
    else:
        trust, lead = 'adjust', '账目尚未闭合，收益先别引用'
        notes = ['逐日核对现金、持仓和权益，先修复差额或补齐材料。']
    p_tone, p_lead, p_note = premise_answer(premise)
    p_notes = [p_note]
    overall = (premise or {}).get('overall') or {}
    if overall.get('periods'):
        p_notes.append(f'本次有效观察 {overall["periods"]} 期；具体因子、时间分段和统计依据见第二章。')
    choices = [c for c in cards if c['state'] in ('act', 'review')][:3]
    next_notes = [c['headline'] for c in choices]
    next_lead = '先处理问题，再复跑比较' if choices else '保留原版，按已发现事项核对'
    if not verified:
        next_lead = '先补凭证或修复账目'
        next_notes = ['账目或资金来源核实后，再按任务单比较原版与修改版。']
    if not next_notes:
        next_notes = ['本次未生成专项修改建议；继续保留原版与材料缺项。']
    rows = [dict(q='收益能信吗', tone=trust, lead=lead, notes=notes, anchor='report-costs'),
            dict(q='你的想法对吗', tone=p_tone, lead=p_lead, notes=p_notes, anchor='report-premise'),
            dict(q='该改什么', tone='adjust' if any(c['state'] == 'act' for c in choices) else 'evidence',
                 lead=next_lead, notes=next_notes, anchor='report-findings')]
    words = {'pass': '记录可核对', 'adjust': '需要处理', 'evidence': '待验证'}
    body = '<div class="reader-intro"><h2>先看这三个问题</h2><p class="mut">先读结论，再按章节看依据与处理办法。</p></div><section class="answers" id="ir-checkup" aria-label="结论摘要">'
    for row in rows:
        body += (f'<div class="answer {row["tone"]}"><h3>{row["q"]}<span class="tag {row["tone"]}">{words[row["tone"]]}</span></h3>'
                 f'<p class="lead">{esc(row["lead"])}</p><ul>' + ''.join(f'<li>{esc(n)}</li>' for n in row['notes'])
                 + f'</ul><a href="#{row["anchor"]}">查看依据与处理 →</a></div>')
    return body + '</section><p class="reader-basis">核账一致只说明上传记录内部一致。历史诊断、成交压力情景与修改后的效果分别判断；修改效果须回原平台复跑。</p>'


def links(base=''):
    root = base + '/' if base else ''
    body = f'<nav class="reader-links" aria-label="报告配套文件"><a href="{root}checkup.html">保存一页体检单</a><a href="{root}next-steps.md" download>下一步复跑任务单</a>'
    if base:
        identity = base.split('/')[2]
        body += f'<a href="/strategy-audits/new?mode=compare&amp;original={esc(identity)}">上传修改版并比较</a><a href="{base}/report.html?download=1">下载完整报告 HTML</a>'
    body += f'<a href="{root}evidence.json">完整证据 JSON</a></nav>'
    return body


def navigation():
    return '<nav class="reader-nav" aria-label="报告章节">' + ''.join(f'<a href="#{key}">{title}</a>' for key, title in CHAPTERS) + '</nav>'


def reconciliation(result):
    account = (result.get('modules') or {}).get('accounting') or {}
    labels = dict(equity_identity='权益 = 现金 + 市值', market_value_total='逐标的市值合计',
                  position_valuation='股数 × 收盘价', fill_notional='成交金额', inventory='库存变动及公司行动',
                  cash_flow='现金变动及费用', pnl_bridge='逐标的盈亏与权益变动', prior_inventory_for_sales='卖出库存必要条件')
    body = '<section id="reconciliation"><h3>账户核对</h3><p class="mut">核对的是提交记录内部一致性；未核实资金来源不因差额为零而升级。</p><div class="tw"><table><tr><th>核对项</th><th>检查数</th><th>异常数</th><th>最大差额</th></tr>'
    for row in account.get('summary') or []:
        body += f'<tr><td>{esc(labels.get(row["key"], row["key"]))}</td><td>{row["count"]}</td><td>{row["failures"]}</td><td>{row["max_gap"]:.4f}</td></tr>'
    body += '</table></div>'
    if account.get('exceptions'):
        body += '<p>有账户异常，先按证据 JSON 中的日期、标的和检查项目修复。</p>'
    return body + '</section>'


def attribution_chart(attr):
    from .audit_institutional import yuan
    rows = attr.get('waterfall') or []
    peak = max((abs(row['value']) for row in rows), default=0) or 1
    body = '<section id="attribution-waterfall"><h3>钱从哪里来，费用扣了多少</h3><div class="reader-waterfall">'
    for row in rows:
        width = abs(row['value']) / peak * 100
        tone = 'negative' if row['value'] < 0 else ''
        body += (f'<div><span>{esc(row["label"])}</span><span class="reader-bar"><i class="{tone}" style="width:{width:.2f}%"></i></span>'
                 f'<strong>{yuan(row["value"])}</strong></div>')
    return body + '</div><p class="mut">价差为额外的压力情景，未扣在原回测收益里；并非实测成本或可保证省下的金额。</p></section>'


def governance(result, base=''):
    gov = (result.get('modules') or {}).get('governance') or {}
    if not gov:
        return ''
    body = '<section id="governance-audit"><h3>治理诊断与候选动作</h3>'
    for item in gov.get('items') or []:
        value = item.get('observed')
        observed = '—' if value is None else f'{value:.1%}' if item['key'] == 'turnover' else f'{value:,.2f}'
        body += f'<p><strong>{esc(item["title"])}：{observed} {esc(item["unit"])}</strong><br>{esc(item["action"])}</p>'
    body += '<p class="mut">候选动作需确认条件并回原平台复跑；费用情景不代表可以实现的节省，也未输出最优收益结论。</p>'
    if base:
        body += f'<p><a href="{base.split("/reports/")[0]}/plan?report_id={base.rsplit("/", 1)[-1]}">带入期末持仓，准备账户方案 →</a></p>'
    return body + '</section>'
