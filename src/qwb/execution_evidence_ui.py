"""Execution evidence lives beside materials; no live ledger is modified."""
from __future__ import annotations

import io
import json
import zipfile

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from . import execution_evidence as evidence, strategy_audit_store as strategies
from .audit_reconciliation import FIELDS
from .audit_tca import ORDER_FIELDS, FILL_FIELDS
from .governance_review_ui import esc

SOURCES = {"broker_export": "券商／账户导出（来源待核实）", "simulator_native": "模拟引擎回报", "synthetic_fixture": "合成测试材料"}
MODES = {"reconcile": "成交对账", "tca": "实现短缺分析"}
STATUSES = {"matched": "订单层全部匹配", "breaks": "存在订单差异", "insufficient": "证据不足", "computed": "分解已计算"}


def forms(workdir, strategy_id, token):
    record, _ = strategies.read(workdir, strategy_id)
    base = f"/strategy-audits/{strategy_id}/execution"
    body = ('<section class="review-section execution-evidence" id="execution-evidence"><h2>独立成交与执行材料</h2>'
            '<p>核对同一账户的两份成交记录，或根据母订单分析延迟、执行偏差和未完成量。'
            '材料另存为执行证据，原策略材料和报告保持原样。</p>')
    for mode, label in MODES.items():
        first, second = (("内部账户成交 CSV", "券商／第二来源成交 CSV") if mode == "reconcile" else
                         ("母订单与基准价格 CSV", "完整成交 CSV"))
        body += f'<details class="material-editor" id="execution-{mode}"><summary>{label}</summary>'
        if mode == "reconcile":
            body += '<p>两份文件按共同订单号、日期、证券和方向核对数量、金额及费用。每单金额与费用容差各为 0.01 元，数量须精确一致。不能把回测与实盘按相近价格强配。</p>'
        else:
            body += '<p>需明确决策、到达、截止时点及价格，保留未成交与取消的母订单。所有时间含时区；未完成量按声明截止价计机会成本。不要用成交价反填决策价。</p>'
        body += f'<p><a href="{base}/templates/{mode}.zip">下载{label}字段模板</a></p>'
        body += f'<form class="review-form" action="{base}" method="post" enctype="multipart/form-data">'
        body += f'<input type="hidden" name="token" value="{token}"><input type="hidden" name="input_digest" value="{record["input_digest"]}"><input type="hidden" name="mode" value="{mode}">'
        body += f'<label>{first}<input type="file" name="first" accept=".csv" required></label><label>{second}<input type="file" name="second" accept=".csv" required></label>'
        body += '<label>材料来源<select name="source_kind" required><option value="">请选择</option>'+''.join(
            f'<option value="{key}">{text}</option>' for key, text in SOURCES.items())+'</select></label>'
        body += '<label>账户／批次备注（可选，勿填密钥）<input name="note" maxlength="80"></label>'
        body += '<label class="check"><input type="checkbox" name="scope_confirmed" value="yes" required>两份材料属于同一账户、期间和币种，数量统一为股／份、金额为元，价格口径一致。</label>'
        body += f'<button>{label}并保存证据</button></form></details>'
    body += '<p class="mut">文件一致或公式闭合不能认证材料来源、完整生命周期或实盘表现；模拟与合成结果分别标识。</p>'
    reports = evidence.history(workdir, strategy_id)
    if reports:
        body += '<h3>已有执行证据</h3><ul>'+''.join(
            f'<li><a href="{base}/{r["id"]}">{MODES.get(r["mode"], "执行记录")} · {esc(r["created_at"][:19])}</a> · {SOURCES.get(r["source_kind"], "来源未核对")} · {STATUSES.get(r["status"], "保存未完成或复算核对失败，原文件保留")}</li>'
            for r in reports)+'</ul>'
    return body+'</section>'


def display(report):
    result, strategy_id = report["result"], report["strategy_id"]
    base = f'/strategy-audits/{strategy_id}/execution/{report["id"]}'
    body = f'<section class="execution-evidence"><h1>{MODES[report["mode"]]}</h1><p><a href="/strategy-audits/{strategy_id}#execution-evidence">返回策略导入与执行材料</a></p>'
    body += f'<p class="notice">{SOURCES[report["source_kind"]]} · 来源未认证</p><h2>{STATUSES[result["status"]]}</h2>'
    if report["note"]:
        body += '<p>账户／批次备注：'+esc(report["note"])+'</p>'
    body += '<p>'+esc(report["declaration"])+'</p>'
    body += f'<p><a class="btn" href="{base}/report.json">下载完整证据 JSON</a></p>'
    if report["mode"] == "reconcile":
        body += f'<p>内部 {result["internal_fills"]} 笔，第二来源 {result["broker_fills"]} 笔；{result["orders"]} 个订单中 {result["matched_orders"]} 个匹配，{result["breaks"]} 个异常。</p>'
        if result["same_input_bytes"]:
            body += '<p class="notice warn">两份输入字节完全相同；这不能构成独立来源核验。</p>'
        body += '<p>按订单金额容差 '+esc(result["tolerances"]["notional"])+' 元、费用容差 '+esc(result["tolerances"]["fee"])+' 元；数量须精确相等。</p>'
        rows = sorted(result["rows"], key=lambda r: r["state"] == "matched")
        body += '<p class="mut">窄屏可左右滑动查看逐单明细。</p><div class="tw" tabindex="0" role="region" aria-label="成交对账逐单明细"><table><tr><th>订单／证券</th><th>状态</th><th>内部 − 第二来源：数量</th><th>金额（元）</th><th>费用（元）</th></tr>'
        states = {"matched": "匹配", "mismatch": "不一致", "missing_internal": "内部缺单", "missing_broker": "第二来源缺单"}
        for r in rows[:100]:
            delta = r["internal_minus_broker"]
            body += '<tr><td>'+esc(r["date"]+' '+r["order_id"]+' '+r["code"]+' '+r["side"])+'</td><td>'+states[r["state"]]+'</td>'+''.join(
                '<td class="n">'+(esc(delta[k]) if delta else '—')+'</td>' for k in ("quantity", "notional", "fee"))+'</tr>'
    else:
        t = result["totals"]
        body += f'<p>{result["orders"]} 个母订单，{result["fills"]} 笔成交；正成本为不利，负成本为价格改善。</p>'
        body += '<div class="audit-split">'+''.join('<div><span>'+label+'</span><strong>'+esc(t[key])+' 元</strong></div>' for key, label in (
            ("delay_cost", "延迟"), ("execution_cost", "执行偏差"), ("opportunity_cost", "未完成量机会成本"), ("fees", "费用"), ("total_cost", "总实现短缺")))+'</div>'
        body += '<p>总成本 '+(esc(result["cost_bp"])+' bp' if result["cost_bp"] is not None else '未计算')+'，分母为全部母单的决策名义金额。执行偏差不能单独识别市场冲击。</p>'
        rows = result["rows"]
        body += '<p class="mut">窄屏可左右滑动查看逐单明细。</p><div class="tw" tabindex="0" role="region" aria-label="实现短缺逐单明细"><table><tr><th>母单／证券</th><th>已成交／目标量</th><th>未完成量</th><th>实现短缺（元）</th><th>分解残差（元）</th></tr>'
        for r in rows[:100]:
            body += '<tr><td>'+esc(r["order_id"]+' '+r["code"]+' '+r["side"])+'</td>'+''.join('<td class="n">'+esc(value)+'</td>' for value in (
                r["filled_quantity"]+' / '+r["quantity"], r["unfilled_quantity"], r["total_cost"], r["decomposition_residual"]))+'</tr>'
    body += '</table></div>'
    if len(rows) > 100:
        body += '<p>页面显示前 100 个订单；完整逐单明细保存在 JSON 中。</p>'
    body += '<p class="mut">'+esc(result["scope"])+'</p><details><summary>输入摘要与复算</summary>'
    for role, item in report["inputs"].items():
        body += f'<p><a href="{base}/inputs/{role}.csv">下载 {role} 输入 CSV</a> · {item["bytes"]} 字节</p><p class="audit-literal">SHA-256：{item["sha256"]}</p>'
    return body+'<p class="audit-literal">报告摘要：'+esc(report["report_digest"])+'</p><p>每次打开或下载均核对输入哈希并重新计算；该检查不认证外部来源。</p></details></section>'


def register(app, workdir, *, page, guard, token):
    def load(strategy_id, evidence_id):
        try:
            return evidence.read(workdir, strategy_id, evidence_id)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, "执行证据不存在或复算核对失败，原文件保留") from None

    @app.post('/strategy-audits/{identity}/execution')
    async def upload(identity: str, req: Request):
        from .strategy_audit_ui import upload_fields
        guard(req, token)
        try:
            strategies.read(workdir, identity)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, "策略材料不存在或核对失败") from None
        form = await upload_fields(req)
        try:
            guard(req, form.get('token'))
            allowed = {'token', 'mode', 'input_digest', 'scope_confirmed', 'source_kind', 'note', 'first', 'second'}
            if set(form) - allowed or any(len(form.getlist(k)) != 1 for k in form):
                raise ValueError("表单含未知或重复字段")
            if form.get('scope_confirmed') != 'yes':
                raise ValueError("请核对同账户、期间、币种及单位口径")
            raw_inputs = []
            for name in ('first', 'second'):
                item = form.get(name)
                if not hasattr(item, 'read') or not (item.filename or '').lower().endswith('.csv'):
                    raise ValueError("请分别上传两个 CSV 文件")
                raw = await item.read(evidence.MAX_BYTES + 1)
                if len(raw) > evidence.MAX_BYTES:
                    raise ValueError("单文件不能超过 8MB")
                raw_inputs.append(raw)
            report_id = await run_in_threadpool(evidence.save, workdir, identity,
                input_digest=form.get('input_digest'), mode=form.get('mode'), first=raw_inputs[0], second=raw_inputs[1],
                source_kind=form.get('source_kind'), note=form.get('note', ''))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            response = page('执行材料未保存', '<p class="notice warn" role="alert">'+esc(str(exc))+'</p><p><a href="/strategy-audits/'+identity+'#execution-evidence">返回执行材料，重新选择文件</a></p>', identity=identity, tab='import')
            response.status_code = 400
            response.headers['Cache-Control'] = 'no-store'
            return response
        finally:
            await form.close()
        return RedirectResponse(f'/strategy-audits/{identity}/execution/{report_id}', 303)

    @app.get('/strategy-audits/{identity}/execution/templates/{mode}.zip')
    def template(identity: str, mode: str):
        try:
            strategies.read(workdir, identity)
            if mode not in evidence.ROLES:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, "策略或模板不存在") from None
        schemas = (FIELDS, FIELDS) if mode == 'reconcile' else (ORDER_FIELDS, FILL_FIELDS)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            for role, fields in zip(evidence.ROLES[mode], schemas):
                archive.writestr(role+'.csv', ','.join(fields)+'\n')
            archive.writestr('README.txt', 'UTF-8 CSV；同账户、期间、币种和价格口径，数量股/份，金额元。\n'
                             '填写原始订单身份；不得按相近价格强配。母单时间必须含时区，并保留未成交/取消单。\n'
                             '模板只含字段，没有模拟成交；空模板不构成验收证据。\n')
        return Response(buffer.getvalue(), media_type='application/zip', headers={'Content-Disposition': f'attachment; filename="{mode}_templates.zip"', 'Cache-Control': 'no-store'})

    @app.get('/strategy-audits/{identity}/execution/{evidence_id}')
    def show(identity: str, evidence_id: str):
        report = load(identity, evidence_id)
        response = page(MODES[report['mode']], display(report), identity=identity, tab='import')
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/strategy-audits/{identity}/execution/{evidence_id}/report.json')
    def download(identity: str, evidence_id: str):
        report = load(identity, evidence_id)
        return Response(json.dumps(report, ensure_ascii=False, indent=2), media_type='application/json',
                        headers={'Content-Disposition': 'attachment; filename="execution_evidence.json"', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})

    @app.get('/strategy-audits/{identity}/execution/{evidence_id}/inputs/{role}.csv')
    def source(identity: str, evidence_id: str, role: str):
        report = load(identity, evidence_id)
        if role not in report['inputs']:
            raise HTTPException(404, '输入不存在')
        raw = (evidence.root(workdir, identity, evidence_id) / (role+'.csv')).read_bytes()
        return Response(raw, media_type='text/csv', headers={'Content-Disposition': f'attachment; filename="{role}.csv"', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
