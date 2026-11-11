"""Clean upload workspace and source-grounded multi-period comparison views."""
from __future__ import annotations

from .governance_review_ui import esc, pct, money
from . import audit_grid, strategy_audit_store as store
from .audit_findings import summarize


def findings_section(columns):
    """Show completed diagnostics before the broader governance checklist."""
    headings = {}
    for column in columns:
        for item in column['findings']:
            headings.setdefault(item['key'], item['title'])
    if not headings:
        return ''
    body = ('<section class="review-section" id="audit-findings"><h2>本次审计新增结果</h2>'
            '<p class="mut">已核实的账目、历史诊断和压力情景分别标注；展开可查看依据和下一步处理方法。窄屏可左右滑动表格。</p>'
            f'<div class="tw"><table class="period-comparison findings-table" style="min-width:{max(600, 160 + 210 * len(columns))}px"><thead><tr><th>检查项目</th>')
    body += ''.join(f'<th><a href="{c["link"]}">{esc(c["label"])}</a></th>' for c in columns)
    body += '</tr></thead><tbody>'
    indexed = [{item['key']: item for item in c['findings']} for c in columns]
    for key, title in headings.items():
        body += f'<tr><th scope="row">{esc(title)}</th>'
        for findings in indexed:
            item = findings.get(key)
            if item:
                body += (f'<td><span class="matrix-state state-{item["state"]}">{esc(item["label"])}</span>'
                         f'<p>{esc(item["text"])}</p><details><summary>依据与下一步</summary>'
                         f'<p><b>处理：</b>{esc(item["action"])}</p><p class="mut">{esc(item["scope"])}</p></details></td>')
            else:
                body += '<td class="mut">本期未完成此项检验，见单期报告的材料与检查范围。</td>'
        body += '</tr>'
    return body + '</tbody></table></div></section>'


def upload_form(token, slots, error=''):
    body = ('<div class="audit-intro upload-intro"><p class="review-kicker">TEMPERQ · 策略审计</p>'
            '<h1>策略治理<span class="intro-tag">支持多时期审计</span></h1>'
            '<p>把不同回测时期放在各自的格子里，一次提交，逐期审计、并排比较。</p>'
            '<div class="intro-meta"><span>最多 5 个时期</span><span>ZIP 无需解压</span><span>材料只保存在本机</span></div></div>')
    from .audit_compare_ui import mode_switch
    body += mode_switch('audit')
    if error:
        body += f'<p role="alert" class="notice warn">{esc(error)}</p>'
    body += f'<form class="single-upload" method="post" action="/strategy-audits/submit" enctype="multipart/form-data"><input type="hidden" name="token" value="{esc(token)}">'
    body += ('<section class="upload-section"><div class="section-title"><span class="step-index">01</span><div><h2>策略代码</h2>'
             '<p>各期共用一份代码；时期 ZIP 内附有代码时，以该期代码为准。</p></div></div><div class="code-layout">')
    body += slot_card(slots[0], code=True)
    body += ('<div class="code-note"><h3>提交后会看到什么</h3><p>各时期的完整审计报告，以及收益、回撤和治理结论的横向对照。</p>'
             '<p class="mut">只审你上传的材料，平台不提供或补充行情、基准与公司行动数据。代码只读解析，不执行。</p>'
             '<a href="#platform-export-help">查看各平台导出方法 ↓</a></div></div></section>')
    body += ('<section class="upload-section"><div class="section-title"><span class="step-index">02</span><div><h2>回测时期</h2>'
             '<p>每个格子接收同一次回测的多份文件或 ZIP；日期从数据中读取。一个时期也可以审计。</p></div></div><div class="period-grid">')
    for slot in slots[1:6]:
        body += slot_card(slot, period=int(slot[0][-1]))
    body += ('</div></section>'
             '<section class="upload-section"><div class="section-title"><span class="step-index">03</span><div><h2>补齐材料<span class="optional-label">选填</span></h2>'
             '<p>补充给“回测数据 1”。已在 ZIP 里的材料不用重复上传；多个时期的补件，请直接放进对应时期的格子。</p></div></div>'
             '<div class="slot-grid">')
    for slot in slots[6:]:
        body += slot_card(slot)
    body += ('</div></section><section class="upload-section upload-settings"><div class="section-title"><span class="step-index">04</span>'
             '<div><h2>策略名称<span class="optional-label">选填</span></h2><p>名称留空时使用“未命名策略”。</p></div></div>'
             '<div class="slot-fields"><label>策略名称<input name="name" maxlength="80" placeholder="未命名策略"></label></div></section>'
             '<div class="slot-submit"><div><button class="primary" type="submit">提交审计 <span aria-hidden="true">→</span></button>'
             '<p>所有已上传时期一起审计，并生成一份多时期对比报告。</p>'
             '<p class="mut" id="upload-progress" role="status" aria-live="polite">尚未选择文件</p></div>'
             '<a href="/strategy-audits/sample.zip">下载合成格式示例</a>'
             '<p class="upload-limits">单文件 8MB · 上传合计 24MB · 每期解压后最多 16 个文件</p></div></form>')
    from .audit_export_help import render
    return body + '<details class="upload-section"><summary>比较调仓价值、行业风格与订单执行，需要什么材料</summary><p>这些检查只使用你提交的完整证据。<a href="/strategy-audits/advanced-guide.md" target="_blank" rel="noopener">查看字段、打包方式与比较条件</a></p></details>' + render() + '<script src="/static/audit-upload.js" defer></script>'


def slot_card(slot, *, period=None, code=False):
    key, title, hint, accept, _ = slot
    title = title.replace('（选填）', '')
    if period:
        hint = '同一次回测的文件，可多选' if period == 1 else '选填 · 不同时期的回测数据，可多选'
    return (f'<section class="slot{" period-slot" if period else ""}"><div class="slot-heading"><h3>{esc(title)}</h3>'
            + (f'<span class="period-number">0{period}</span>' if period else '') + '</div>'
            f'<p class="mut" id="hint-{key}">{esc(hint)}</p>'
            f'<label class="slot-button" for="upload-{key}"><span aria-hidden="true">＋</span> 选择文件</label>'
            f'<input class="slot-input" id="upload-{key}" type="file" name="{key}" accept="{accept}" data-slot="{key}" '
            f'aria-label="上传{esc(title)}" aria-describedby="hint-{key} name-{key}"' + (' multiple' if period else '') + '>'
            f'<p class="slot-name" id="name-{key}" data-for="{key}" aria-live="polite">未选择文件</p>'
            f'<button class="slot-clear" type="button" data-clear="{key}" hidden>清除选择</button>'
            + '</section>')


def batch_body(batch, workdir):
    from . import audit_institutional as institution
    base = '/strategy-audit-batches/' + batch['id']
    periods, coverage = batch['periods'], batch['coverage']
    body = f'<style>{institution.STYLE}</style><article class="ir">' + institution.cover(
        batch['name'], period='各期观测区间见业绩表', identity=batch['id'], created=batch['created_at'], count=len(periods))
    body += f'<p class="ir-note">{len(periods)} 个时期已全部纳入本次审计 · {coverage["distinct_dates"]} 个不同观测日期 · {coverage["repeated_dates"]} 个重复覆盖日期。</p>'
    from .strategy_audit_ui import MISSING_IMPACT, missing_materials_section
    missing, findings, columns = [], [], []
    for row in periods:
        _, bundle = store.read(workdir, row['identity'])
        result = store.report(workdir, row['identity'], row['report_id'])[2] if row['report_id'] else {}
        from .audit_evidence import projection
        result = projection(bundle, result or {})
        columns.append({'label': row['label'], 'result': result or {}, 'bundle': bundle, 'identity': row['identity']})
        findings.append({'label': row['label'], 'link': f'{base}/periods/{row["slot"]}',
                         'findings': summarize(result)})
        kinds = {s['kind'] for s in bundle['sources']}
        if bundle.get('snapshots'):
            kinds.add('snapshots')
        labels = [label for key, label, _ in MISSING_IMPACT if key not in kinds]
        if labels:
            missing.append(esc(row['label']) + '：' + esc('、'.join(labels)))
    body += institution.batch_checkup(columns, base=base)
    inferred_slots = {row['slot'] for row, column in zip(periods, columns)
                      if institution.inferred_events(column['bundle'])['count']}
    strategy = batch['grid']['strategies'][0]
    if strategy['identity_issues']:
        body += '<p class="notice warn">比较口径需复核：' + esc('；'.join(strategy['identity_issues'])) + '</p>'
    body += ('<section class="review-section ir-section" id="ir-performance"><h2>02 / 各时期指标对照</h2><p class="mut">收益按各期自身基准权益计算；金额、账户和持仓保持独立。'
             '账目未闭合或材料不足的时期不展示可比较收益。</p><div class="tw"><table class="period-comparison"><thead><tr>'
             '<th>指标</th>' + ''.join(f'<th><a href="{base}/periods/{r["slot"]}">{esc(r["label"])}</a></th>' for r in periods) + '</tr></thead><tbody>')
    metrics = [
        ('观测区间', lambda r: f'{r["start"] or "无日期"}<br>{r["end"] or ""}'),
        ('回测方式（声明）', lambda r: esc(audit_grid.EXPERIMENTS[r['experiment']])),
        ('材料 / 成交', lambda r: f'{r["files"]} 份 / {r["fills"]} 笔'),
        ('已核算日期', lambda r: str(r['sessions']) if r['sessions'] is not None else '—'),
        ('基准权益', lambda r: '¥' + money(r['baseline']) if r['baseline'] is not None else '—'),
        ('回测路径收益', lambda r: (pct(r['return_reported']) + ('（含推定事件，待核实）' if r['slot'] in inferred_slots else '')) if r['return_reported'] is not None else '—'),
        ('审计成本情景收益', lambda r: (pct(r['return_recost']) + ('（待核实）' if r['slot'] in inferred_slots else '')) if r['return_recost'] is not None else '—'),
        ('路径最大回撤', lambda r: pct(abs(r['drawdown_reported'])) if r['drawdown_reported'] is not None else '—'),
        ('可比性', lambda r: '使用推定事件后闭合，先补原始凭证' if r['slot'] in inferred_slots else '账目闭合，可作描述性比较' if r['comparison_status'] == 'ok' else esc('；'.join(r['comparison_reasons']) or r['error'] or '材料不足')),
    ]
    for label, formatter in metrics:
        body += '<tr><th scope="row">' + label + '</th>' + ''.join('<td>' + formatter(r) + '</td>' for r in periods) + '</tr>'
    body += ('</tbody></table></div><p class="mut">成本情景沿用现有审计的逐笔重计口径，不是重新回测。'
             '首日基准之前的盈亏不补算；不同长度收益不作高低排名。</p></section>')
    body += institution.value_section(columns) + institution.attribution(columns) + institution.findings(columns)
    body += ('<section class="ir-section" id="ir-evidence"><h2>05 / 范围与证据</h2><div class="ir-scope">'
             + esc(batch['scope']) + '</div><div class="ir-links">'
             + f'<a href="{base}/evidence.json">下载全部审计证据 JSON</a>'
             + ''.join(f'<a href="{base}/periods/{r["slot"]}">{esc(r["label"])} · 完整报告与下载</a>' for r in periods)
             + '</div>')
    if missing:
        body += '<h3>补了能多知道什么</h3>' + institution.supplements(columns[0]['bundle'])
    body += '</section><details class="ir-appendix" open><summary>详细报告列表</summary><div>'
    body += findings_section(findings)
    body += ('<section class="review-section"><h2>治理结论矩阵</h2><details><summary>展开 15 项完整治理覆盖</summary>'
             '<p class="mut">以下是对完整治理环节的判断，范围比上面的专项检验更广；缺少其他证据不会使已核实结果失效。点击各期查看具体依据及缺项。</p><div class="tw"><table class="period-matrix"><tr><th>审计项目</th>')
    body += ''.join(f'<th><a href="{base}/periods/{r["slot"]}">{esc(r["label"])}</a></th>' for r in periods) + '</tr>'
    for index, finding in enumerate(periods[0]['conclusions']):
        body += '<tr><th scope="row">' + esc(finding['title']) + '</th>'
        for row in periods:
            conclusion = row['conclusions'][index]
            body += f'<td><span class="matrix-state state-{conclusion["state"]}">{esc(conclusion["label"])}</span><details><summary>依据</summary><p>{esc(conclusion["reason"])}</p></details></td>'
        body += '</tr>'
    body += '</table></div></details></section>'
    def friendly(text):
        import re
        return re.sub(r'period-(\d+)', lambda m: '回测数据 ' + m[1], str(text)).replace('这一格有参考窗口里没有的交易日', '两段日期不同，不能逐日配对；各期单独核算仍然有效')

    body += '<details class="period-path-details"><summary>各期来源与起跑方式（技术明细）</summary><section class="review-section"><h2>跨时期一致性与路径检查</h2><p>' + esc(friendly(strategy['verdict'])) + '</p>'
    for dimension in strategy['verified_dimensions']:
        body += f'<p><b>{esc(dimension["label"])}</b>：{"相同" if dimension["status"] == "same" else "不同"} · {esc(dimension["evidence"])}</p>'
    body += ('<p class="mut">提交的源码一致只表示文件内容相同，不能证明原平台实际运行版本一致。尚缺：'
             + esc('、'.join(d['label'] for d in strategy['unknown_dimensions'])) + '。</p>')
    for pair in strategy['pairs']:
        if pair['status'] == 'compared':
            body += (f'<p>{esc(friendly(pair["fresh_run"]))} 与参考路径同期切片：独立起跑收益差 '
                     f'{pair["fresh_minus_continuous_pp"]:+.2f} 个百分点；'
                     f'首次持仓差异：{esc(pair.get("first_position_difference") or "未观测到")}。</p>')
        else:
            body += f'<p>{esc(friendly(pair["fresh_run"]))}：{esc(friendly(pair.get("reason", "无法配对")))}</p>'
    body += ('<p class="mut">只有声明为空仓独立起跑、账目闭合且日期可配对的时期，才与参考路径切片比较。'
             '缺少目标、指令与状态时，不推断差异的因果来源。</p></section></details>')
    body += '<section class="review-section" id="period-missing-details"><h2>各期缺少什么材料</h2>'
    for row in periods:
        _, bundle = store.read(workdir, row['identity'])
        body += f'<details class="period-missing"><summary>{esc(row["label"])}</summary>'
        if row['error']:
            body += f'<p class="notice warn">审计未完成：{esc(row["error"])}</p>'
        body += missing_materials_section(bundle, '-' + str(row['slot'])) or '<p>页面列出的基础材料均已收到；更深的检验仍以各项证据要求为准。</p>'
        body += '</details>'
    body += ('</section><section class="review-section"><h2>如何理解多时期结果</h2>'
             '<p>重复覆盖日期不能增加独立样本量。独立起跑、连续路径切片、断点续跑和滚动训练测试分别保留，不能互相替代。</p>'
             '<p>' + esc(batch['selection_adjustment']['reason']) + '</p>'
             '<p>方法依据：<a href="https://doi.org/10.21314/jcf.2016.322" target="_blank" rel="noopener">回测过拟合概率（PBO）</a> · '
             '<a href="https://doi.org/10.3905/jpm.2014.40.5.094" target="_blank" rel="noopener">经选择偏差调整的夏普比率（DSR）</a> · '
             '<a href="https://doi.org/10.1111/1468-0262.00152" target="_blank" rel="noopener">数据窥探 Reality Check</a></p></section>')
    return body + '</div></details></article>'
