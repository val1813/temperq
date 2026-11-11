"""Common review front matter: decisions, reconciled performance and attribution.

Presentation only. Every amount comes from existing audited modules, with
historical observations, price scenarios and wider governance scope kept distinct.
"""
from __future__ import annotations

from . import audit_attribution, audit_findings
from .governance_review_ui import esc


STYLE = """
.ir{color:#202c3c;line-height:1.75;--ir-line:#dfe5ed;--ir-muted:#657387}.ir *{box-sizing:border-box}
.ir-cover{padding:24px 0 28px;border-bottom:3px solid #233e61}.ir-kicker{font-size:11px;letter-spacing:.13em;color:#657387;margin:0 0 10px}.ir h1{font-size:28px;line-height:1.4;margin:0 0 16px;letter-spacing:-.3px}.ir h2{font-size:19px;margin:0 0 16px;color:#233e61}.ir h3{font-size:14px;margin:18px 0 8px}.ir p{overflow-wrap:anywhere}.ir-meta{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px 24px;font-size:12px}.ir-meta span{display:block;color:var(--ir-muted)}.ir-meta code{font-size:11px;overflow-wrap:anywhere}
.ir-nav{display:flex;flex-wrap:wrap;gap:8px 24px;padding:16px 0;border-bottom:1px solid var(--ir-line);font-size:12px}.ir-section{padding:28px 0;border-bottom:1px solid var(--ir-line);scroll-margin-top:20px}.ir-decision{background:#edf3fa;border-left:4px solid #355b86;padding:16px 20px}.ir-decision strong{font-size:17px}.ir-decision p{font-size:13px;margin:6px 0 0}.ir-note{color:var(--ir-muted);font-size:12px;margin:10px 0}.ir-status{font-size:11px;display:inline-block;padding:2px 7px;border-radius:4px;background:#e9eef4;color:#45566d}.ir-status.pass{background:#e8f3ed;color:#216644}.ir-status.adjust{background:#faeae7;color:#a4372c}.ir-status.evidence{background:#fff2d8;color:#89601e}.ir-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin:18px 0}.ir-kpi{border:1px solid var(--ir-line);background:#fff;padding:16px;border-radius:6px;min-width:0}.ir-kpi span{display:block;font-size:11px;color:var(--ir-muted)}.ir-kpi strong{font-size:23px;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.ir .tw{max-width:100%;overflow-x:auto}.ir table{border-collapse:collapse;width:100%;font-size:12px;line-height:1.7}.ir th,.ir td{padding:11px 12px;border-bottom:1px solid var(--ir-line);text-align:left;vertical-align:top;white-space:normal;overflow-wrap:anywhere}.ir thead th{background:#eef2f7;color:#4b5c73;font-weight:600}.ir td.num,.ir th.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.ir .ir-total{font-weight:650;background:#f0f4f8}.ir .ir-negative{color:#9e3f35}.ir small{color:var(--ir-muted)}.ir-findings{table-layout:fixed;min-width:660px}.ir-findings th:first-child{width:120px}.ir-findings td:first-child{width:100px}.ir-findings details{margin:5px 0}.ir-links{display:flex;flex-wrap:wrap;gap:10px 22px;font-size:12px;margin-top:16px}.ir-appendix{border:1px solid var(--ir-line);background:#fff;margin:24px 0;border-radius:6px}.ir-appendix>summary{padding:15px 18px;color:#354f70;font-size:13px;font-weight:600}.ir-appendix>div{padding:0 18px 20px}.ir-appendix .review-heading{display:none}.ir-appendix h1{font-size:20px}.ir-appendix .review-resources{font-size:12px}.ir .ir-bridge{min-width:540px}.ir-bridge small{display:block}.ir-scope{padding:14px 18px;background:#f0f3f7;font-size:12px}.ir-section details>summary{font-size:12px}.ir .ir-period-table{min-width:640px;table-layout:fixed}.ir-matrix-label{font-size:12px;color:var(--ir-muted)}
@media(max-width:640px){.ir h1{font-size:23px}.ir-meta{grid-template-columns:1fr 1fr;gap:12px}.ir-grid{grid-template-columns:1fr}.ir-kpi{display:flex;justify-content:space-between;gap:12px;align-items:center}.ir-kpi strong{font-size:21px}.ir-section{padding:24px 0}.ir-appendix>div{padding-inline:10px}}
@media print{body{background:white!important}.top,.foot,.ir-nav,.ir-links,.workspace-help,.help-trigger{display:none!important}main{max-width:none!important;padding:0!important}.ir{color:#202c3c}.ir-section{padding:18px 0}.ir h2,.ir h3{break-after:avoid}.ir tr,.ir-kpi,.ir-decision{break-inside:avoid}.ir .tw{overflow:visible}.ir table{min-width:0!important}.ir-appendix:not([open]){display:none}.ir a{color:inherit;text-decoration:none}}
"""


def yuan(value):
    return '—' if value is None else f'¥{0 if abs(value) < .005 else value:,.2f}'


def ratio(value):
    return '—' if value is None else f'{value:.2%}'


def local_time(value):
    if not value:
        return '见报告存档'
    from datetime import datetime, timedelta, timezone
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is not None:
            return stamp.astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') + '（北京时间）'
    except ValueError:
        pass
    return value[:19].replace('T', ' ')


def inferred_events(bundle):
    """A balancing proposal is not independent evidence of an actual cash/share event."""
    bundle = bundle or {}
    rows = [a for a in bundle.get('actions') or [] if a.get('inferred')]
    count = max(len(rows), (bundle.get('quality', {}).get('inferred_actions') or {}).get('count', 0))
    return {'count': count, 'cash': sum(a.get('cash', 0.) for a in rows),
            'dates': len({a['date'] for a in rows})}


def checked_attribution(result, bundle=None):
    if (result.get('modules', {}).get('accounting') or {}).get('status') != 'verified':
        return None
    if inferred_events(bundle)['count'] or (result.get('evidence_assessment') or {}).get('attribution_status') == 'provisional':
        return None
    return audit_attribution.build(result)


def checkup(bundle, result, *, base=''):
    """The same three answers for full, partial and code-only reports."""
    account = result.get('modules', {}).get('accounting') or {}
    events = inferred_events(bundle)
    attr = checked_attribution(result, bundle)
    observed = not bundle.get('quality', {}).get('fills_inferred')
    fills = bundle.get('fills') or []
    fees = sum(f['fee'] for f in fills) if observed and fills else None
    if attr and attr['verified']:
        # Keep the cost numerator and profit denominator on identical intervals.
        fees = attr['fees']
        trust = ('pass', '账目对得上', f'{len(account.get("daily") or [])} 个日期的成交、持仓、现金和权益已核对。')
        if attr['gross'] > 0:
            costs = ('evidence', f'费用吃掉费用前盈利的 {attr["fees"] / attr["gross"]:.1%}',
                     f'同一核账区间：费用前赚 {yuan(attr["gross"])}，已扣费用 {yuan(attr["fees"])}，剩下 {yuan(attr["net"])}。')
        else:
            costs = ('evidence', f'已扣交易费用 {yuan(fees)}', '费用前已无正盈利；减少手续费本身不能扭转全部持仓损失。')
        issues = [r for r in audit_findings.summarize(result) if r['state'] == 'adjust']
        issue = next((r for r in issues if r['key'] in ('account', 'fee', 'capacity', 'sequence')), None)
        step = ('adjust' if issue else 'evidence', issue['title'] if issue else '先做一次固定条件的执行复跑',
                issue['action'] if issue else '保持信号和费率不变，检验成交价、资金到账和成交量约束后，比较净收益与回撤。')
        if issue and issue['key'] == 'sequence':
            step = ('adjust', '先确认卖出到账，再买入', '确认卖单实际成交、资金到账后再发买单；在原回测平台重跑，检查收益和未成交记录。')
        elif issue and issue['key'] == 'capacity':
            step = ('adjust', '先检查单笔订单是否过大', '先核对成交量单位；限制单笔订单占成交量的比例，在原回测平台重跑，比较未成交订单、收益和费用。')
        decision = '可以继续研究这份回测；先完成上面的执行核对，再决定是否采用策略。'
    else:
        if account.get('status') in {'verified', 'provisional'} and events['count']:
            trust = ('evidence', '使用推定事件后账面闭合，收益待核实',
                     f'{events["count"]} 条分红或股数变化由账户差额推定；其中现金差额合计 {yuan(events["cash"])}，尚缺原始事件凭证。')
            step = ('evidence', '先补分红送转与出入金记录',
                    '核对这些现金和股数变化来自分红、送转还是出入金；补原平台公司行动和资金流水。按市值分摊的推定分红不能证明每只股票赚了多少。')
        elif account.get('status') == 'verified' and (result.get('evidence_assessment') or {}).get('allocated_count'):
            trust = ('evidence', '账户总额闭合，逐股分红分配待核实', '应收股息总额来自账户；按市值分摊到各股票的金额仍是推算。')
            step = ('evidence', '先补逐股分红明细', '补每只标的的分红权益，才能确认逐股贡献；总额不能证明分配。')
        elif account:
            trust = ('adjust', '账目有差异，先别照着做', f'账户核对发现 {len(account.get("exceptions") or [])} 条异常，先修复再引用收益。')
            step = ('adjust', '先修复账户差额', '打开下方完整核对明细，按日期核对现金、持仓数量和费用，修复后重新提交。')
        elif not bundle.get('code'):
            trust = ('evidence', '材料已收到，尚未完成账户核对', f'已收到 {len(fills)} 条交易记录，还需要策略代码才能进入本工具的完整审计。')
            step = ('evidence', '先补同一次回测的策略代码', '上传原始 .py 或 .txt 文件，再与已收到的交易、持仓和账户一起核对。')
        elif fills:
            trust = ('evidence', '交易已读入，收益还未核实', f'已收到 {len(fills)} 条交易记录；缺少可闭合的每日账户与持仓，收益曲线还不能与资金变化对上。')
            step = ('evidence', '先补每日账户详情', '从同一次回测导出每日总权益和可用现金，与逐日持仓一起提交；米筐可上传“账户详情.csv”。')
        else:
            trust = ('evidence', '目前只能读代码或持仓，尚未核实收益', '需要实际交易和每日账户，才能核对策略赚了多少钱。')
            step = ('evidence', '先上传同一次回测的交易与账户', '保留现有材料，再补交易详情、每日持仓和账户详情；代码会继续只做静态检查。')
        if fees is None:
            costs = ('evidence', '实际手续费尚未知', '现有记录不能确认实际扣费；先补交易详情中的费用列。')
        else:
            costs = ('evidence', f'交易记录已扣 {yuan(fees)}', '这笔费用已记录；收益未核实，暂不计算它占盈利的比例。')
            small = [f for f in fills if f['notional'] < 1000]
            if small:
                costs = (costs[0], costs[1], costs[2] + f'其中 {len(small)} 笔成交额不到1,000元，合计费用 {yuan(sum(f["fee"] for f in small))}。')
        decision = '先完成上面这一步，再判断是否采用策略；当前材料足以核对部分费用，还不足以核实整体收益。'
    rows = [('收益能信吗', trust), ('钱花在哪了', costs), ('先做什么', step)]
    from .audit_reader import refine
    rows, decision = refine(rows, [{'label': '本期', 'bundle': bundle, 'result': result}], decision)
    body = '<section class="ir-checkup" id="ir-checkup" aria-label="体检结论卡"><h2>先看这三件事</h2>'
    for question, (state, lead, detail) in rows:
        word = {'pass': '核对一致', 'adjust': '需处理', 'evidence': '先核对'}[state]
        body += (f'<div class="ir-answer"><div><span class="ir-status {state}">{word}</span><b>{question}</b></div>'
                 f'<div><strong>{esc(lead)}</strong><p>{esc(detail)}</p></div></div>')
    body += f'<p class="ir-next"><b>下一步：</b>{esc(decision)}</p>'
    if base:
        body += f'<div class="ir-links"><a href="{base}/checkup.html" target="_blank" rel="noopener">保存一页体检单（打印为 PDF）</a></div>'
        if result.get('mode', 'full') == 'full':
            identity = base.split('/')[2]
            body += f'<div class="ir-links"><a href="{base}/next-steps.md">下载下一步复跑任务单</a><a href="/strategy-audits/new?mode=compare&amp;original={esc(identity)}">上传修改版并比较</a></div>'
    return body + '</section>'


def batch_checkup(columns, *, base=''):
    event_columns = [c for c in columns if inferred_events(c.get('bundle'))['count']]
    checked = [checked_attribution(c['result'], c.get('bundle')) for c in columns]
    verified = sum(bool(a and a['verified']) for a in checked)
    fee_lines = []
    steps = []
    for c in columns:
        a = checked_attribution(c['result'], c.get('bundle'))
        if a and a['verified']:
            fee_lines.append(c['label'] + '费用 ' + yuan(a['fees']) + (f'，占费用前盈利 {a["fees"] / a["gross"]:.1%}' if a['gross'] > 0 else '，费用前无正盈利'))
        else:
            fills = c.get('bundle', {}).get('fills') or []
            inferred = c.get('bundle', {}).get('quality', {}).get('fills_inferred')
            fee_lines.append(c['label'] + (f'交易记录已扣 {yuan(sum(f["fee"] for f in fills))}，待核整体盈亏' if fills and not inferred else '尚未完成盈亏闭合'))
        for r in audit_findings.summarize(c['result']):
            if r['state'] == 'adjust' and r['action'] not in steps:
                steps.append(r['action'])
    missing_account = verified < len(columns)
    provisional = sum((c['result'].get('modules', {}).get('accounting') or {}).get('status') == 'provisional' for c in columns)
    trust_lead = f'{verified}/{len(columns)} 期账目与逐标的归因核对一致'
    if provisional:
        trust_lead += f'；{provisional} 期依赖推定事件，待凭证核实'
    step = ('先补推定事件对应的分红送转与出入金记录' if event_columns else
            '先修复未闭合时期的账户材料' if missing_account else steps[0] if steps else '保持规则和费率不变，做执行约束复跑后再决定采用。')
    fee_lead = '先看已扣费用，再看盈亏来源'
    fractions = [a['fees'] / a['gross'] for a in checked if a and a['verified'] and a['gross'] > 0]
    if len(fractions) == len(columns):
        fee_lead = f'各期费用占费用前盈利的{min(fractions):.1%}至{max(fractions):.1%}'
        fee_lines = ['费用已经从回测收益中扣除；各期具体金额见下方盈亏表']
    rows = [('收益能信吗', ('evidence' if missing_account else 'pass', trust_lead, '每期独立核对，不把多个账户合并成一条收益。')),
            ('钱花在哪了', ('evidence', fee_lead, '；'.join(fee_lines) + '。')),
            ('先做什么', ('adjust' if missing_account or steps else 'evidence', '优先处理会影响收益可信度的事项', step))]
    from .audit_reader import refine, tasks
    rows, decision = refine(rows, columns, '按“先做什么”完成一次核对或复跑，再逐期比较净收益、回撤与费用。')
    body = '<section class="ir-checkup" id="ir-checkup"><h2>先看这三件事</h2>'
    for question, (state, lead, detail) in rows:
        word = {'pass': '核对一致', 'adjust': '需处理', 'evidence': '先核对'}[state]
        body += f'<div class="ir-answer"><div><span class="ir-status {state}">{word}</span><b>{question}</b></div><div><strong>{esc(lead)}</strong><p>{esc(detail)}</p></div></div>'
    body += f'<p class="ir-next"><b>下一步：</b>{esc(decision)}</p>'
    if base:
        body += f'<div class="ir-links"><a href="{base}/next-steps.md">下载下一步复跑任务单</a>'
        for index in sorted({t['column'] for t in tasks(columns)}):
            c = columns[index]
            if c.get('identity'):
                body += f'<a href="/strategy-audits/new?mode=compare&amp;original={esc(c["identity"])}">{esc(c["label"])} · 上传修改版并比较</a>'
        body += '</div>'
    return body + '</section>'


STYLE += """
.ir-checkup{padding:24px 0;border-bottom:1px solid #dfe5ed}.ir-answer{display:grid;grid-template-columns:130px 1fr;gap:18px;padding:16px 0;border-bottom:1px solid #e6ebf1}.ir-answer>div:first-child{display:flex;flex-direction:column;gap:6px;align-items:flex-start;font-size:13px}.ir-answer strong{font-size:16px}.ir-answer p{font-size:13px;margin:5px 0;color:#536279}.ir-next{font-size:13px;padding:13px 16px;background:#edf3fa;border-radius:6px}.ir-print{font:14px system-ui;max-width:920px;margin:24px auto}.ir-print .ir-cover{padding-top:0}.ir-print .ir-meta{grid-template-columns:repeat(3,1fr)}.ir-print button{padding:10px 16px;cursor:pointer}
@media(max-width:640px){.ir-answer{grid-template-columns:1fr;gap:6px}.ir-answer>div:first-child{flex-direction:row;align-items:center}.ir-print{padding:16px}}
@media print{@page{size:A4;margin:15mm}.ir-print{margin:0;padding:0;font-size:11px}.ir-print button,.ir-print .ir-nav,.ir-print .ir-links{display:none}.ir-print .ir-checkup{padding:12px 0}.ir-print .ir-answer{grid-template-columns:110px 1fr;padding:12px 0;break-inside:avoid}.ir-print .ir-answer strong{font-size:14px}.ir-print .ir-answer p{font-size:11px}.ir-print h1{font-size:21px}.ir-print h2{font-size:16px}}
"""


def cover(name, *, period, identity='', created='', count=1):
    return (f'<header class="ir-cover"><p class="ir-kicker">TEMPERQ / 策略回测审阅报告</p><h1>{esc(name)}</h1>'
            '<div class="ir-meta">'
            f'<div><span>审阅范围</span>{esc(period)}</div><div><span>报告类型</span>{"多时期独立账户审阅" if count > 1 else "单时期回测材料审阅"}</div>'
            f'<div><span>纳入时期</span>{count} 个</div><div><span>报告编号</span><code>{esc(identity or "见证据清单")}</code></div>'
            f'<div><span>材料存档时间</span>{esc(local_time(created))}</div>'
            '<div><span>编制方式</span>本地自动核算与规则检查</div></div></header>'
            '<nav class="ir-nav" aria-label="报告章节"><a href="#ir-checkup">01 体检结论</a>'
            '<a href="#ir-performance">02 业绩与风险</a><a href="#ir-attribution">03 盈亏归因</a>'
            '<a href="#ir-actions">04 发现与处理</a><a href="#ir-evidence">05 范围与证据</a></nav>')


def opinion(columns):
    verified = sum((c['result'].get('modules', {}).get('accounting') or {}).get('status') == 'verified' for c in columns)
    findings = [r for c in columns for r in audit_findings.summarize(c['result'])]
    issues = sum(r['state'] == 'adjust' for r in findings)
    title = ('先修复账户材料，再引用收益' if verified < len(columns) else
             '账目已核实，优先核对执行条件' if issues else '账目已核实，按下列证据范围使用结果')
    body = (f'<section class="ir-section" id="ir-opinion"><h2>01 / 审阅结论</h2><div class="ir-decision"><strong>{title}</strong>'
            f'<p>{verified}/{len(columns)} 个时期账户核对通过；专项检查有 {issues} 项需处理或核对（按时期计数）。'
            '各项结论分别成立，不能用账户闭合替代成交真实性或预测能力验证。</p></div>')
    body += '<h3>本次可以确认什么</h3><ul>'
    for c in columns:
        a = checked_attribution(c['result'], c.get('bundle'))
        if a and a['verified']:
            body += f'<li>{esc(c["label"])}：净盈亏 {yuan(a["net"])} = 费用前盈亏 {yuan(a["gross"])} − 已扣费用 {yuan(a["fees"])}；逐标的贡献合计闭合。</li>'
        else:
            body += f'<li>{esc(c["label"])}：账目或归因尚未闭合，暂算数值不能作为已核实业绩。</li>'
    body += ('</ul><p class="ir-note">本报告依据提交材料自动编制；结论不具有独立第三方鉴证效力。历史盈利、规则检查通过和未来可盈利是不同命题。</p></section>')
    return body


def findings(columns):
    rows = [(c['label'], r) for c in columns for r in audit_findings.summarize(c['result']) if r['state'] == 'adjust']
    body = '<section class="ir-section" id="ir-actions"><h2>04 / 主要发现与处理顺序</h2>'
    if not rows:
        body += '<p>已完成的专项检查没有触发需调整项；未覆盖的检查在范围与证据中单列。</p>'
    else:
        body += '<p class="ir-note">先处理账目问题，再核对执行约束；下表不将问题涉及金额当成可追回收益。</p><div class="tw"><table class="ir-findings"><thead><tr><th>时期 / 检查</th><th>发现与数值</th><th>建议处理</th></tr></thead><tbody>'
        order = {'account': 0, 'fee': 1, 'selection': 2, 'price': 3, 'capacity': 4, 'sequence': 5}
        for label, item in sorted(rows, key=lambda r: order.get(r[1]['key'], 10)):
            body += (f'<tr><th>{esc(label)}<br>{esc(item["title"])}</th><td>{esc(item["text"])}</td>'
                     f'<td>{esc(item["action"])}<details><summary>依据范围</summary><p>{esc(item["scope"])}</p></details></td></tr>')
        body += '</tbody></table></div>'
    signals = [(c['label'], r) for c in columns for r in audit_findings.summarize(c['result']) if r['key'] == 'premise']
    if signals:
        body += '<h3>选股依据是否得到支持</h3><ul>' + ''.join(f'<li>{esc(label)}：{esc(r["text"].split("；", 1)[0])}。<details><summary>查看信号统计</summary><p>{esc(r["text"])}</p></details></li>' for label, r in signals) + '</ul>'
    stresses = [(c['label'], r) for c in columns for r in audit_findings.summarize(c['result']) if r['key'] == 'stress']
    if stresses:
        body += '<details><summary>成交价变差时，涉及多少钱？</summary>'
        body += ''.join(f'<p>{esc(label)}：{esc(r["text"])}</p>' for label, r in stresses)
        body += '<p class="ir-note">这是保持原交易数量不变的价格压力测试；要判断改策略能多赚多少，仍需让成交、资金和后续交易一起复跑。</p></details>'
    body += ('<h3>改动如何验收</h3><p>每次只冻结一个待验证改动，保持区间、数据、初始资金与费用同口径复跑，'
             '并排比较净收益、回撤、费用和未成交记录。事前写明接受条件，保留失败结果；省费或价格压力情景不能直接等同于收益改善。</p></section>')
    return body


def attribution(columns, base=''):
    body = ('<section class="ir-section" id="ir-attribution"><h2>03 / 盈亏归因</h2>'
            '<p class="ir-note">本节为账户盈亏分解：按标的、已扣费用、持有批次和回撤区间解释资金变化。'
            '行业/风格/因子超额归因须另有同期权重、分类、暴露与基准证据，本次不由证券代码推断。</p>')
    data = [(c, checked_attribution(c['result'], c.get('bundle'))) for c in columns]
    for c, _ in data:
        events = inferred_events(c.get('bundle'))
        if events['count']:
            body += (f'<p class="ir-scope">{esc(c["label"])}：{events["count"]} 条公司行动由账面差额推定，现金差额合计 {yuan(events["cash"])}。'
                     '补原始分红送转与出入金记录前，账户净增量不能直接确认为投资盈利；逐标的分红分配和盈亏贡献暂不作为已核实归因。'
                     '原有推算数值保留在技术附录。</p>')
    body += '<div class="tw"><table class="ir-bridge"><thead><tr><th>盈亏桥（元）</th>'
    body += ''.join(f'<th class="num">{esc(c["label"])}</th>' for c, _ in data) + '</tr></thead><tbody>'
    for key, label in [('gross', '费用前盈亏'), ('fees', '减：已扣佣金与税费'), ('net', '账户净盈亏')]:
        body += f'<tr{" class=ir-total" if key == "net" else ""}><th>{label}</th>'
        body += ''.join(f'<td class="num">{yuan(a[key]) if a and a["verified"] else "未核实"}</td>' for _, a in data) + '</tr>'
    body += '</tbody></table></div><h3>逐标的净贡献</h3>'
    # Use the full audited stock set; do not lose mid-ranked securities from the bridge.
    indexed = []
    for c, a in data:
        stocks = (c['result'].get('modules', {}).get('attribution') or {}).get('stocks', [])
        indexed.append({s['code']: s for s in stocks} if a and a['verified'] else None)
    codes = sorted({code for ix in indexed if ix is not None for code in ix})
    visible = codes
    if len(codes) > 10:
        extremes = set()
        for ix in indexed:
            if ix:
                ordered = sorted(ix.values(), key=lambda s: s['net_pnl'], reverse=True)
                extremes.update(s['code'] for s in ordered[:3] + ordered[-3:])
        visible = sorted(extremes)
    def stock_table(selected, *, remainder=False):
        table = '<div class="tw"><table class="ir-bridge"><thead><tr><th>标的</th>' + ''.join(f'<th class="num">{esc(c["label"])}<small>净贡献 / 占起点权益</small></th>' for c, _ in data) + '</tr></thead><tbody>'
        for code in selected:
            table += f'<tr><th>{esc(code)}</th>'
            for ix in indexed:
                s = ix.get(code) if ix is not None else None
                if ix is None:
                    value = '未核实'
                elif s is None:
                    value = '¥0.00<small>本期无贡献</small>'
                else:
                    bp = s.get('initial_capital_bp')
                    value = yuan(s['net_pnl']) + (f'<small>{bp / 100:+.2f}%</small>' if bp is not None else '')
                table += f'<td class="num">{value}</td>'
            table += '</tr>'
        if remainder:
            table += f'<tr><th>其余 {len(codes) - len(selected)} 只合计</th>' + ''.join(f'<td class="num">{yuan(sum(s["net_pnl"] for code, s in ix.items() if code not in selected)) if ix is not None else "未核实"}</td>' for ix in indexed) + '</tr>'
        return table + '<tr class="ir-total"><th>贡献合计</th>' + ''.join(f'<td class="num">{yuan(a["net"]) if a and a["verified"] else "未核实"}</td>' for _, a in data) + '</tr></tbody></table></div>'
    body += stock_table(visible, remainder=len(visible) < len(codes))
    if len(visible) < len(codes):
        body += f'<p class="ir-note">先列各期贡献最高和最低的各3只，其余单列合计。</p><details><summary>查看全部 {len(codes)} 只标的贡献</summary>' + stock_table(codes) + '</details>'
    for c, a in data:
        if not a or not a['verified']:
            continue
        dd = a['drawdown']
        if dd:
            body += (f'<h3>{esc(c["label"])} · 最大回撤贡献</h3><p>峰值至谷值：{esc(dd["peak"])} → {esc(dd["trough"])}；'
                     f'回撤 {ratio(abs(dd["ratio"]))}，权益减少 {yuan(dd["loss"])}。</p>')
            body += '<p class="ir-note">主要贡献：' + esc('；'.join(f'{s["code"]} {yuan(s["net_pnl"])}' for s in dd['worst'])) + f'。全部贡献闭合差 {yuan(dd["gap"])}。</p>'
        if a['by_holding']:
            body += f'<details><summary>{esc(c["label"])} · 持有期分组（已平仓，FIFO）</summary><div class="tw"><table><thead><tr><th>持有期</th><th class="num">匹配笔数</th><th class="num">净盈亏</th></tr></thead><tbody>'
            for r in a['by_holding']:
                body += f'<tr><td>{esc(r["label"])}</td><td class="num">{r["matches"]}</td><td class="num">{yuan(r["net"])}</td></tr>'
            body += '</tbody></table></div><p class="ir-note">仅含已平仓批次；逐标的贡献同时含已平仓与期末未实现盈亏，两者范围不同。</p></details>'
    if base:
        body += f'<div class="ir-links"><a href="{base}/stocks.csv">逐标的贡献 CSV</a><a href="{base}/daily.csv">每日贡献 CSV</a><a href="{base}/holding-lots.csv">持仓批次 CSV</a></div>'
        if any(inferred_events(c.get('bundle'))['count'] for c in columns):
            body += f'<div class="ir-links"><a href="{base}/evidence-dependencies.csv">下载待核事件与原始行号</a></div>'
    return body + '</section>'


def single(bundle, result, *, identity='', report_id='', created=''):
    from . import audit_report
    from .audit_evidence import projection
    result = projection(bundle, result)
    columns = [{'label': '本期', 'result': result, 'bundle': bundle}]
    account = result['modules'].get('accounting') or {}
    verified = account.get('status') == 'verified' and not inferred_events(bundle)['count']
    perf = audit_report.performance(bundle)
    dates = bundle.get('snapshots') or []
    period = f'{dates[0]["date"]} — {dates[-1]["date"]}' if dates else '材料中未提供账户区间'
    base = f'/strategy-audits/{identity}/reports/{report_id}' if identity and report_id else ''
    body = f'<style>{STYLE}</style><article class="ir">' + cover(bundle['name'], period=period, identity=report_id or result.get('result_digest', '')[7:23], created=created)
    body += checkup(bundle, result, base=base)
    body += '<section class="ir-section" id="ir-performance"><h2>02 / 业绩与风险</h2>'
    if perf:
        cells = [('区间收益' + ('' if verified else '（暂算）'), ratio(perf['total'])), ('最大回撤', ratio(abs(perf['max_drawdown']))), ('已核账日期', str(len(account.get('daily') or [])))]
        body += '<div class="ir-grid">' + ''.join(f'<div class="ir-kpi"><span>{esc(k)}</span><strong>{esc(v)}</strong></div>' for k, v in cells) + '</div>'
        body += f'<p class="ir-note">起点：{esc(perf["basis"])} {yuan(perf["start_equity"])}；已扣导出费用，未扣额外审计价差情景。'
        if account.get('baseline') == 'first_exported_close':
            body += '本段从首日收盘之后起算；首日交易及费用已包含在起点权益中，因此本段金额与整份交易表总额可能不同。'
        if perf.get('benchmark_total') is not None:
            body += f'同期导出基准 {ratio(perf["benchmark_total"])}；区间收益差 {ratio(perf["excess_total"])}。'
        body += '</p>'
    else:
        body += '<p>账户快照不足，未计算区间业绩。</p>'
    body += '</section>' + value_section(columns) + attribution(columns, base) + findings(columns)
    body += '<section class="ir-section" id="ir-evidence"><h2>05 / 范围与证据</h2>'
    body += '<div class="ir-scope">上传代码仅做静态解析。账户与费用一致性、历史信号诊断及执行压力情景分别解释。缺目标权重、完整状态或研究试验档案时，相应结论仍受限。</div>'
    body += '<h3>补了能多知道什么</h3>' + supplements(bundle)
    if base:
        body += f'<div class="ir-links"><a href="{base}/report.html" target="_blank" rel="noopener">打印 / 下载版报告</a><a href="{base}/report.html?download=1">下载 HTML</a><a href="{base}/evidence.json">完整证据 JSON</a></div>'
    return body + '</section>'


def supplements(bundle):
    kinds = {s['kind'] for s in bundle.get('sources') or []}
    suggestions = []
    if inferred_events(bundle)['count']:
        suggestions.append('补原始分红送转与出入金记录：核实推定现金和股数变化，才能确认整体投资盈利及逐标的贡献。')
    if not bundle.get('snapshots'):
        suggestions.append('补每日账户详情（总权益、可用现金）：才能把回测收益与资金变化核对起来。')
    if not bundle.get('positions'):
        suggestions.append('补每日持仓与收盘估值：才能算出每只标的贡献了多少盈亏。')
    if 'scores' not in kinds:
        suggestions.append('补每日排名与候选池收盘价：可以检查选股依据有没有帮助。')
    if 'minute_prices' not in kinds:
        suggestions.append('补成交日分钟价：可以检查换一个成交时点会损失多少收益。')
    return '<ul>' + ''.join(f'<li>{esc(s)}</li>' for s in suggestions[:3]) + '</ul>' if suggestions else '<p>基础材料已收到；更深的检查所需证据见完整明细。</p>'


def value_section(columns):
    computed = any(((c['result'].get('modules', {}).get('economic_review') or {}).get('holding_control') or {}).get('status') == 'computed'
                   or any((c['result'].get('modules', {}).get(k) or {}).get('status') == 'computed' for k in ('factor_attribution', 'implementation_shortfall')) for c in columns)
    body = '<section class="ir-section" id="ir-value"><h2>复杂操作值得吗</h2>'
    for c in columns:
        r = c['result']
        value = r.get('modules', {}).get('economic_review')
        if value is None:
            from .audit_value import analyze
            value = analyze(c.get('bundle') or {}, r.get('modules') or {})
        control = value['holding_control']
        body += f'<h3>{esc(c["label"])}</h3>'
        if control['status'] == 'computed':
            body += ('<div class="tw"><table><thead><tr><th>同一首日收盘起点</th><th class="num">原版后续操作</th><th class="num">原仓位继续持有</th></tr></thead><tbody>'
                     f'<tr><th>区间净盈亏</th><td class="num">{yuan(control["actual_net"])}</td><td class="num">{yuan(control["hold_net"])}</td></tr>'
                     f'<tr><th>路径最大回撤</th><td class="num">{ratio(control["actual_drawdown"])}</td><td class="num">{ratio(control["hold_drawdown"])}</td></tr></tbody></table></div>'
                     f'<p>后续操作的净差额 {yuan(control["net_difference"])} = 毛持仓路径变化 {yuan(control["gross_path_difference"])} − 多付费用 {yuan(control["extra_fees"])}。</p>'
                     f'<p><b>{esc(control["verdict"])}</b></p><details><summary>比较条件与风险交换</summary><p>{esc(control["scope"])}</p>'
                     '<p>回撤变化单独评价；该持有对照不是已批准替代策略，也没有证明某项改动的因果效果。</p></details>')
        else:
            body += '<details><summary>持有对照所需材料</summary><ul>' + ''.join(f'<li>{esc(x)}</li>' for x in control['reasons']) + '</ul></details>'
        evidence = r.get('evidence_assessment') or {}
        if evidence.get('dependencies'):
            grouped = {}
            for item in evidence['dependencies']:
                row = grouped.setdefault(item['date'], {'cash': 0., 'names': set()})
                row['cash'] += item['cash']
                row['names'].add(item['code'])
            priority = sorted(grouped.items(), key=lambda x: (-abs(x[1]['cash']), x[0]))[:3]
            body += '<h3>先核对这些日期</h3><ul>' + ''.join(
                f'<li>{esc(day)}：待核现金来源或逐股分配 {yuan(row["cash"])}，涉及 {len(row["names"])} 个标的。</li>' for day, row in priority) + '</ul>'
            body += '<p class="ir-note">按待核现金绝对金额列前三日，帮助定位凭证；金额不是已确认错误，也不是可追回收益。</p>'
        cost_stress = (r.get('modules', {}).get('fee_analysis') or {}).get('cost_stress') or {}
        if evidence.get('status') == 'internally_consistent' and cost_stress.get('positive_profit_exhausted_at_extra_bp') is not None:
            body += f'<details><summary>原盈利能承受多少额外交易成本</summary><p>保持原成交数量不变，每侧额外成本达到 {cost_stress["positive_profit_exhausted_at_extra_bp"]:.2f} bp 时，本段原正净增量会被耗尽。</p>'
            body += '<p>这没有重算未成交、资金和后续目标，不能作为策略容量或实盘盈利认证。</p></details>'
        body += '<p class="ir-note">只使用本次上传材料。历史时期不作为未见样本；每次改动先冻结目标、风险容忍和接受条件，再保留原平台复跑的全部结果。</p>'
        for key, title in [('factor_attribution', '行业风格归因'), ('implementation_shortfall', '订单执行损失')]:
            advanced = r.get('modules', {}).get(key)
            if not advanced:
                continue
            body += f'<details><summary>{title} · 用户提交的独立范围</summary>'
            if advanced.get('status') == 'computed':
                if key == 'implementation_shortfall':
                    body += '<p>' + esc('；'.join(f'{label} {yuan(float(advanced["totals"][k]))}' for k, label in
                        [('delay_cost', '延迟'), ('execution_cost', '成交偏差'), ('opportunity_cost', '未完成机会成本'), ('fees', '费用'), ('total_cost', '总实现短缺')])) + '</p>'
                else:
                    active = (advanced.get('aggregate') or {}).get('active')
                    if active:
                        body += f'<p>区间组合与基准收益差 {ratio(float(active["compounded_return_difference"]))}；残差 {ratio(float(active["linked_residual"]))}。</p>'
                        body += '<ul>' + ''.join(f'<li>{esc(k)}：{ratio(float(v))}</li>' for k, v in active['linked_factor_contributions'].items()) + '</ul>'
            else:
                body += f'<p>本项未完成：{esc(advanced.get("error") or "输入覆盖不足，详见证据JSON")}</p>'
            body += '<p>这些材料的账户范围尚未与主账户独立对齐；不加到主账户盈亏桥。残差不称为选股能力，执行偏差不单独认定为市场冲击。</p></details>'
    body += '</section>'
    return body if computed else '<details class="ir-optional"><summary>尚未完成的对照与补充检查</summary>' + body + '</details>'


def partial(bundle, result, *, base=''):
    body = f'<style>{STYLE}</style><article class="ir"><header class="ir-cover"><p class="ir-kicker">TEMPERQ / 回测体检</p><h1>{esc(bundle["name"])}</h1><p class="ir-note">依据本次已收到的材料，先告诉你能查什么、下一步做什么。</p></header>'
    body += checkup(bundle, result, base=base)
    body += '<section class="ir-section"><h2>补了能多知道什么</h2>' + supplements(bundle) + '</section>'
    return body


def checkup_document(bundle, result, *, created=''):
    from .audit_evidence import projection
    result = projection(bundle, result)
    return (f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(bundle["name"])} · 一页体检单</title><style>{STYLE}</style></head>'
            '<body class="ir-print"><button onclick="window.print()">保存为 PDF / 打印</button><article class="ir"><header class="ir-cover"><p class="ir-kicker">TEMPERQ / 一页回测体检单</p>'
            f'<h1>{esc(bundle["name"])}</h1><p class="ir-note">材料存档：{esc(local_time(created))} · 报告编号 {esc(result.get("result_digest", "")[7:23])}</p></header>'
            + checkup(bundle, result) + '<p class="ir-note">依据提交材料自动核算；具体成交约束及未覆盖项目以完整报告为准。</p></article></body></html>')
