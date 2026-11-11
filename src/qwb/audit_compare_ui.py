"""比较原版与修改版：一次上传，直接出对比报告，没有中间页。"""
from __future__ import annotations

from .governance_review_ui import esc, pct, money
from . import audit_verification, strategy_audit_store as store

DATA_ACCEPT = '.csv,.xlsx,.zip,.json,.txt'


def mode_switch(current):
    tabs = (('audit', '审计一份回测', '/strategy-audits/new'),
            ('compare', '比较原版与修改版', '/strategy-audits/new?mode=compare'))
    body = '<nav class="mode-switch" aria-label="审计模式">'
    for key, label, url in tabs:
        on = ' aria-current="page" class="on"' if key == current else ''
        body += f'<a href="{url}"{on}>{label}</a>'
    return body + '</nav>'


def _slot(key, title, hint, accept, multiple=True):
    return (f'<section class="slot"><div class="slot-heading"><h3>{esc(title)}</h3></div>'
            f'<p class="mut" id="hint-{key}">{esc(hint)}</p>'
            f'<label class="slot-button" for="upload-{key}"><span aria-hidden="true">＋</span> 选择文件</label>'
            f'<input class="slot-input" id="upload-{key}" type="file" name="{key}" accept="{accept}" data-slot="{key}"'
            f' aria-describedby="hint-{key} name-{key}"' + (' multiple' if multiple else '') + '>'
            f'<p class="slot-name" id="name-{key}" data-for="{key}" aria-live="polite">未选择文件</p>'
            f'<button class="slot-clear" type="button" data-clear="{key}" hidden>清除选择</button></section>')


def compare_form(token, original=None, error=''):
    body = ('<div class="audit-intro upload-intro"><p class="review-kicker">TEMPERQ · 策略审计</p>'
            '<h1>比较原版与修改版</h1>'
            '<p>改了一项设置，想知道值不值得？上传改动前后同一时期的两次回测，直接得到对比报告。</p></div>')
    body += mode_switch('compare')
    if error:
        body += f'<p role="alert" class="notice warn">{esc(error)}</p>'
    body += (f'<form class="single-upload" method="post" action="/strategy-audits/compare" enctype="multipart/form-data">'
             f'<input type="hidden" name="token" value="{esc(token)}">')
    body += '<section class="upload-section"><div class="slot-grid compare-grid">'
    if original:
        record = original
        body += (f'<section class="slot slot-filled"><div class="slot-heading"><h3>原版回测</h3></div>'
                 f'<p class="mut">沿用已审计的「{esc(record.get("name") or "未命名策略")}」</p>'
                 f'<input type="hidden" name="original" value="{esc(record["id"])}">'
                 f'<p class="slot-name">已选定 · <a href="/strategy-audits/new?mode=compare">改为上传</a></p></section>')
    else:
        body += _slot('original_files', '原版回测', '改动之前的回测导出，可多选或 ZIP', DATA_ACCEPT)
    body += _slot('modified_files', '修改版回测', '只改了一项设置、同一时期重新回测的导出', DATA_ACCEPT)
    body += _slot('code', '修改版策略代码（选填）', '.py / .txt；ZIP 里已有代码可不传', '.py,.txt', multiple=False)
    body += ('</div></section><div class="slot-submit"><div><button class="primary" type="submit">开始比较 <span aria-hidden="true">→</span></button>'
             '<p>两版按同一方法重算成本，并排比较净收益、回撤和交易笔数。</p></div></div></form>')
    body += ('<section class="upload-section"><h2>怎样做一次有效的比较</h2><ol class="compare-steps">'
             '<li>一次只改一项设置，比如调仓频率、持股数或单票上限。</li>'
             '<li>保持同一回测时期、同一初始资金、同一费用设置，在原平台重新回测。</li>'
             '<li>两次导出都上传到这里。历史上更好不等于未来更好，结果用来决定下一步，不是收益承诺。</li></ol></section>')
    return body + '<script src="/static/audit-upload.js" defer></script>'


def compare_report(record_a, result_a, record_b, result_b, *, base_a, base_b):
    name = record_b.get('name') or record_a.get('name') or '未命名策略'
    body = (f'<div class="review-heading"><div><p class="review-kicker">TemperQ / 版本对比报告</p>'
            f'<h1>{esc(name)} · 原版与修改版</h1></div><a href="/strategy-audits/new?mode=compare">再比较一组</a></div>')
    cmp = audit_verification.compare(result_a, result_b) if result_a and result_b else None
    if not cmp:
        body += ('<p class="notice warn">两版中至少有一版缺少完整的交易与持仓记录，无法用同一方法重算。'
                 '请确认两次导出都包含交易、每日持仓和账户汇总。</p>')
    elif not cmp['comparable']:
        body += f'<p class="notice warn">这两次回测不能直接比较：{esc(cmp["reason"])}</p>'
    else:
        a, b = cmp['rows']['full']
        diff = b['realistic'] - a['realistic']
        state = {'better': ('修改版更好', 'pass'), 'worse': ('修改版更差', 'adjust')}.get(cmp['state'], ('差别不明显', 'evidence'))
        body += (f'<section class="review-section ir-section"><h2>结论</h2><div class="ir-answer"><div>'
                 f'<span class="ir-status {state[1]}">{state[0]}</span></div><div><strong>{esc(cmp["verdict"])}</strong>'
                 f'<p>按同一方法重算成本后，修改版净盈亏比原版{"多" if diff >= 0 else "少"} ¥{money(abs(diff))}；'
                 f'最大回撤从 {pct(-cmp["drawdown"][0])} 变为 {pct(-cmp["drawdown"][1])}。</p></div></div>'
                 f'<p class="mut">比较区间 {cmp["start"]} 至 {cmp["end"]}'
                 + ('' if cmp['same_period'] else '；两次回测区间不同，只比较重叠部分') + '。同一段历史上的对比，更好不等于未来一定更好。</p></section>')
        body += ('<section class="review-section"><h2>并排对比</h2><div class="tw"><table><tr><th></th><th>原版</th><th>修改版</th><th>差额</th></tr>'
                 + _row('原平台报告净盈亏', a['reported'], b['reported'])
                 + _row('买卖价差情景', -a['spread'], -b['spread'])
                 + _row('按同一成本重算后', a['realistic'], b['realistic'])
                 + f'<tr><td>成交笔数</td><td>{a["fills"]}</td><td>{b["fills"]}</td><td>{b["fills"] - a["fills"]:+d}</td></tr>'
                 + f'<tr><td>最大回撤</td><td>{pct(-cmp["drawdown"][0])}</td><td>{pct(-cmp["drawdown"][1])}</td><td></td></tr>'
                 + '</table></div>')
        info = cmp.get('uncertainty', {}).get('full')
        if info:
            body += f'<p class="mut">{esc(audit_verification.uncertainty_text(info))}</p>'
        body += '</section>'
    body += (f'<section class="review-section"><h2>两版各自的完整报告</h2><div class="ir-links">'
             f'<a href="{base_a}">原版审计报告</a><a href="{base_b}">修改版审计报告</a></div></section>')
    return body


def _row(label, a, b):
    return f'<tr><td>{label}</td><td>¥{money(a)}</td><td>¥{money(b)}</td><td>¥{money(b - a)}</td></tr>'
