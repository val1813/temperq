"""Account/factor supplements beside a strategy import; source is never certified."""
from __future__ import annotations

from decimal import Decimal
import io
import json
import zipfile

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from . import audit_account_reconciliation as account, audit_factor_attribution as factor
from . import strategy_audit_store as strategies, validation_evidence as evidence
from .governance_review_ui import esc

MODES = {'account': '日终账户核对', 'factor': '行业／风格归因', 'risk': '风险估计与留出诊断'}
SOURCES = {'broker_export': '券商／账户导出（来源待核实）', 'simulator_native': '模拟引擎回报',
           'synthetic_fixture': '合成测试材料', 'historical_data': '历史因子材料（来源待核实）',
           'historical_returns_synthetic_exposures': '历史收益＋合成暴露'}
STATUSES = {'matched': '日终账户材料匹配', 'breaks': '日终账户存在差异', 'insufficient': '证据不足', 'computed': '给定因子模型分解已计算'}
LABELS = {'internal': '内部完整账本 ZIP', 'external': '独立外部完整账本 ZIP', 'calendar': '完整日期日历 CSV',
          'scope': '归因范围与模型 JSON', 'periods': '同区间收益 CSV', 'weights': '期初完整权重 CSV',
          'exposures': '带可得时间的暴露 CSV', 'factor_returns': '同区间因子收益 CSV', 'asset_returns': '证券总收益 CSV'}
RISK_LABELS = dict(LABELS, scope='风险范围与模型 JSON', periods='相邻收盘区间 CSV', weights='截止时点完整权重 CSV')
HEADERS = {'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'}
STATES = {'matched': '匹配', 'mismatch': '不一致', 'missing': '缺失', 'missing_internal': '内部缺项',
          'missing_broker': '外部缺项', 'insufficient': '证据不足', 'computed': '已计算'}
CHECKS = {'position_mark': '逐证券估值', 'snapshot_present': '快照是否齐全', 'position_count': '持仓数量',
          'market_value_sum': '持仓市值合计', 'equity_identity': '权益恒等式', 'cash_rollforward': '现金推演',
          'quantity_rollforward': '库存推演', 'long_only_inventory': '只做多库存约束', 'nonnegative_total_cash': '非负总现金约束'}
REASONS = {'missing_complete_weights_or_cash_row': '缺完整权重或现金行', 'missing_or_not_yet_available': '缺暴露或期初尚不可得',
           'stale': '暴露已过期', 'missing_factor_return': '缺同区间因子收益',
           'missing_cash_row': '缺截止时点现金行', 'missing_or_unavailable_training_return': '缺训练收益或截止时点尚不可得',
           'too_few_training_periods': '完整训练样本不足', 'missing_holdout_asset_return': '缺留出期证券收益'}


def _label(mode, role):
    return (RISK_LABELS if mode == 'risk' else LABELS)[role]


def _status(mode, status):
    if mode == 'risk' and status == 'computed':
        return '风险估计与留出诊断已计算'
    return STATUSES.get(status, '需重新验证：保存未完成、材料或计算版本变化，原文件保留')


def forms(workdir, strategy_id, token):
    record, _ = strategies.read(workdir, strategy_id)
    base = f'/strategy-audits/{strategy_id}/validation'
    body = '<section class="review-section" id="validation-evidence"><h2>账户、归因与风险证据</h2><p>将独立核对材料绑定到当前策略材料版本，保留原始输入与逐项结果。此处不更改原审计报告，也不证明账户属于该策略。</p>'
    for mode, label in MODES.items():
        body += f'<details class="material-editor" id="validation-{mode}"><summary>{label}</summary>'
        if mode == 'account':
            body += '<p>两份 ZIP 各含根目录下的 scope.json、balances.csv、positions.csv、fills.csv、events.csv。包括期初与全部开市日日终快照；只支持CNY、只做多现货及成交日总现金。日历须覆盖期间内所有自然日。</p>'
        elif mode == 'factor':
            body += '<p>分别提供完整范围、双方期初权重、当时已可得暴露及同区间因子收益。缺暴露不补零，残差不称alpha；本入口不估计协方差、特异风险或风险预算。</p>'
        else:
            body += '<p>提供风险范围、完整日历、相邻收盘区间、截止权重、历史暴露、因子与证券收益七份材料。训练仅用截止时点已可得的完整记录；留出收益不用于选参。固定权重每期无成本再平衡，结果不是真实策略净值；统计诊断不等于模型校准通过。</p>'
        body += f'<p><a href="{base}/templates/{mode}.zip">下载{label}空字段模板</a></p>'
        body += f'<form class="review-form" action="{base}" method="post" enctype="multipart/form-data"><input type="hidden" name="token" value="{token}"><input type="hidden" name="input_digest" value="{record["input_digest"]}"><input type="hidden" name="mode" value="{mode}">'
        for role, suffix in evidence.ROLES[mode].items():
            body += f'<label>{_label(mode,role)}<input type="file" name="{role}" accept="{suffix}" required></label>'
        body += '<label>材料来源<select name="source_kind" required><option value="">请选择</option>'+''.join(
            f'<option value="{source}">{SOURCES[source]}</option>' for source in evidence.SOURCES[mode])+'</select></label>'
        body += '<label>备注（可选，勿填密钥）<input name="note" maxlength="80"></label>'
        declaration = '双方账本属于同一账户、期间、币种及完整业务口径，数量为股／份、金额为元。' if mode == 'account' else '区间、期初权重、因子定义及单位一致；时间字段按真实可得时点声明，合成材料已标明。'
        if mode == 'risk':
            declaration = '模型定义、截止权重、完整日历及收益口径一致；发布时点据实声明，合成材料已标明。留出期未用于本次参数选择，回顾性诊断不冒充前向验证。'
        body += f'<label class="check"><input type="checkbox" name="scope_confirmed" value="yes" required>{declaration}</label><button>核对并保存{label}证据</button></form></details>'
    body += '<p class="mut">单输入最多8MB，合计24MB。匹配或公式闭合不代表来源认证、完整风险模型或机构准入。</p>'
    reports = evidence.history(workdir, strategy_id)
    if reports:
        body += '<h3>已有账户、归因与风险证据</h3><ul>'+''.join(
            f'<li><a href="{base}/{r["id"]}">{MODES.get(r["mode"], "证据记录")} · {esc(r["created_at"][:19])}</a> · {SOURCES.get(r["source_kind"], "来源未核对")} · {_status(r["mode"],r["status"])}</li>' for r in reports)+'</ul>'
    return body+'</section>'


def _percent(value):
    return '未计算' if value is None else f'{Decimal(value)*100:.4f}%'


def _account_values(item):
    if item is None:
        return '缺失'
    labels = {'cash': '现金', 'market_value': '市值', 'equity': '权益', 'position_count': '持仓数',
              'quantity': '数量', 'price': '估值价', 'kind': '业务类型', 'code': '证券',
              'cash_delta': '资金变化', 'quantity_delta': '股数变化'}
    return '；'.join(label+' '+str(item[key]) for key, label in labels.items() if key in item)


def display(report):
    result, mode = report['result'], report['mode']
    base = f'/strategy-audits/{report["strategy_id"]}/validation/{report["id"]}'
    body = f'<section><h1>{MODES[mode]}</h1><p><a href="/strategy-audits/{report["strategy_id"]}#validation-evidence">返回策略导入与证据</a></p>'
    body += f'<p class="notice">{SOURCES[report["source_kind"]]} · 来源未认证 · 机构准入未建立</p><h2>{_status(mode,result["status"])}</h2><p>{esc(report["declaration"])}</p>'
    if report['note']:
        body += '<p>备注：'+esc(report['note'])+'</p>'
    body += f'<p><a class="btn" href="{base}/report.json">下载完整证据 JSON</a></p>'
    if mode == 'account':
        body += f'<p>{len(result["expected_snapshot_dates"])} 个应有快照日期（含期初）；逐项差异／缺失共 {result["breaks"]} 项。数量与估值价精确一致，金额每项容差 {esc(result["money_tolerance"])} 元。</p>'
        if result['same_input_bytes']:
            body += '<p class="notice warn">双方输入字节完全相同，不能构成独立来源证明。</p>'
        rows = []
        for kind in ('balances', 'positions', 'events'):
            for item in result[kind]:
                if item['state'] != 'matched':
                    rows.append(({'balances': '资金', 'positions': '持仓', 'events': '事件'}[kind],
                                 ' / '.join(item.get('key', [item.get('date', ''), item.get('event_id', '')])), STATES[item['state']],
                                 _account_values(item['internal']), _account_values(item['broker'])))
        for side, details in result['rollforward'].items():
            for item in details['checks']:
                if item['state'] != 'matched':
                    rows.append(({'internal': '内部', 'broker': '外部'}[side]+' / '+CHECKS[item['check']],
                                 item['date']+' '+(item['code'] or ''), STATES[item['state']], item['expected'], item['actual']))
        body += '<h3>日终与推演差异</h3>'
        body += _table(('检查', '日期／证券', '状态', '内部或推演应有', '外部或实际记录'), rows)
        body += f'<p>订单层 {result["executions"]["orders"]} 单，异常 {result["executions"]["breaks"]} 单；完整成交对比、事件与推演结果见JSON。空业务的账户层匹配不代表订单层存在独立成交证据。</p>'
    elif mode == 'risk':
        body += _risk_display(result)
    else:
        aggregate = result['aggregate']
        if aggregate:
            body += '<p>累计组合收益 '+_percent(aggregate['portfolio']['compounded_return'])+'，基准 '+_percent(aggregate['benchmark']['compounded_return'])+'，主动累计差 '+_percent(aggregate['active']['compounded_return_difference'])+'（两个累计收益之差）。</p>'
            rows = [(name, _percent(aggregate['portfolio']['linked_factor_contributions'][name]),
                     _percent(aggregate['benchmark']['linked_factor_contributions'][name]), _percent(value))
                    for name, value in aggregate['active']['linked_factor_contributions'].items()]
            rows.append(('残差（不是alpha）', *[_percent(aggregate[book]['linked_residual']) for book in ('portfolio', 'benchmark', 'active')]))
            body += '<h3>链接后的贡献</h3>'+_table(('因子', '组合', '基准', '主动'), rows)
        else:
            body += '<p class="notice warn">材料有缺口，未生成完整多期归因；已知的部分暴露不能当作完整组合暴露。</p>'
        rows = []
        for item in result['periods']:
            a, b = item['books']['portfolio'], item['books']['benchmark']
            rows.append((item['period_id'], STATES[item['status']], _percent(a['non_cash_coverage']), _percent(b['non_cash_coverage']),
                         _percent(a.get('residual')), _percent(b.get('residual'))))
        body += '<h3>区间与覆盖</h3>'+_table(('区间', '状态', '组合非现金暴露覆盖', '基准非现金暴露覆盖', '组合残差', '基准残差'), rows)
        if result['missing']:
            body += '<h3>材料缺口</h3>'+_table(('区间', '组合', '证券', '因子', '原因'),
                [(r['period_id'], {'portfolio': '组合', 'benchmark': '基准'}.get(r.get('book'), ''), r.get('code', ''),
                  r.get('factor', ''), REASONS[r['reason']]) for r in result['missing']])
    body += '<details><summary>范围、原始输入与复算</summary><ul>'+''.join('<li>'+esc(item)+'</li>' for item in result['limitations'])+'</ul>'
    for role, item in report['inputs'].items():
        body += f'<p><a href="{base}/inputs/{role}">下载{_label(mode,role)}</a> · {item["bytes"]} 字节</p><p class="audit-literal">SHA-256：{item["sha256"]}</p>'
    body += '<p class="audit-literal">策略材料摘要：'+esc(report['strategy_input_digest'])+'</p><p class="audit-literal">证据报告摘要：'+esc(report['report_digest'])+'</p><p>每次打开及下载均核对计算代码、输入摘要并复算。代码版本变化后须重新验证；原记录保留。哈希不能认证外部来源。</p></details></section>'
    return body


def _scalar(value):
    return '未计算' if value is None else f'{float(value):.6g}'


def _risk_display(result):
    names = {'portfolio':'组合', 'benchmark':'基准', 'active':'主动'}
    scope = result['scope']
    body = '<p class="notice warn">模型未校准。回顾性固定截止诊断，不是预注册前向验证；固定权重每期无成本再平衡、现金收益为零，不是真实策略净值。</p>'
    body += '<p class="audit-literal">训练截止：'+esc(scope['cutoff_time'])+'</p>'
    body += f'<p>完整训练区间 {result["training_complete_periods"]} / {result["training_expected_periods"]}，留出应有区间 {result["holdout_expected_periods"]}。</p>'
    body += '<details><summary>给定模型定义（来源未认证）</summary><p>'+esc(scope['model_definition'])+'</p>'
    body += _table(('因子','类型','定义'),[(r['name'], '行业' if r['kind']=='industry' else '风格',r['definition']) for r in scope['factors']])+'</details>'
    forecast = result['forecast']
    if forecast is None:
        body += '<p class="notice warn">训练收益、期初暴露或截止材料不足，未生成风险预测，也未进行留出诊断。</p>'
    else:
        body += '<h3>风险预测（截止时点固定）</h3><p>波动、VaR及ES均为单区间收益率；VaR/ES使用零均值高斯假设，ES尚未独立回测。平方根年化另列，不保证跨期风险按此增长。</p>'
        body += _table(('组合','单期波动','年化波动','高斯VaR（'+_percent(scope['confidence_level'])+'）','高斯ES'),
            [(names[book],_percent(r['volatility']),_percent(r['annualized_volatility']),_percent(r['gaussian_var_zero_mean']),_percent(r['gaussian_es_zero_mean'])) for book,r in forecast.items()])
        body += '<h3>方差分解</h3><p>单位为收益率小数的平方；残差包含相关项，因子／残差交叉项不能忽略。闭合不证明预测有效。</p>'
        body += _table(('组合','总方差','因子方差','残差方差','因子／残差交叉方差'),
            [(names[book],*[_scalar(r[k]) for k in ('variance','factor_variance','residual_variance','factor_residual_cross_variance')]) for book,r in forecast.items()])
        body += f'<p>联合协方差维度 {result["covariance_dimension"]}，秩 {result["covariance_rank"]}。</p>'
        if result['rank_deficient']:
            body += '<p class="notice warn">协方差秩不足；未用额外方差下限掩盖样本或共线问题，不能据此认定估计充分。</p>'
        body += '<details><summary>证券波动贡献</summary><p>贡献允许为负，不能直接当作非负风险预算或自动限仓指令。</p>'
        assets = sorted(scope['assets'])
        body += _table(('证券','组合波动贡献','基准波动贡献','主动波动贡献'),
            [(code,*[_percent(forecast[book]['asset_volatility_contributions'][i]) if forecast[book]['asset_volatility_contributions'] is not None else '零方差，未计算' for book in names]) for i,code in enumerate(assets)])+'</details>'
        body += '<p class="audit-literal">预测模型摘要：'+esc(result['model_digest'])+'</p>'
        body += '<h3>留出诊断（回顾性）</h3>'
        if result['evaluation_status']=='insufficient':
            body += '<p class="notice warn">预测已保留；留出材料或样本不足，不对部分样本给出完整诊断结论。</p>'
        states = {'diagnostic_only':'仅供诊断', 'insufficient':'证据不足','degenerate_forecast':'零方差预测，无法据此判断校准'}
        body += _table(('组合','状态','完整期数','实际波动','实际／预测波动','超限次数','预期超限次数'),
            [(names[book],states[r['status']],r['periods'],_percent(r.get('observed_volatility')),_scalar(r.get('volatility_ratio')),r.get('exceedances','未计算'),_scalar(r.get('expected_exceedances'))) for book,r in result['evaluation'].items()])
        if any(r.get('tail_sample_status')=='too_few_expected_exceedances' for r in result['evaluation'].values()):
            body += '<p class="notice warn">预期尾部超限少于5次，样本不足以支持充分校准；大p值不能解释为模型通过。</p>'
        body += '<details><summary>尾部覆盖与聚集检验</summary><p>p值仅供诊断；二项、IID和渐近检验均有前提。缺状态变化时不输出独立性检验p值。完整转移数、序列和区间见JSON。</p>'
        body += _table(('组合','精确二项p值','覆盖率p值','超限独立性p值','条件覆盖p值'),
            [(names[book],_scalar(r.get('exact_binomial_p_value')),_scalar(r.get('kupiec_p_value')),_scalar(r.get('independence_test',{}).get('p_value')),_scalar(r.get('conditional_coverage_test',{}).get('p_value'))) for book,r in result['evaluation'].items()])
        if any(r.get('small_transition_sample') for r in result['evaluation'].values()):
            body += '<p class="notice warn">超限转移至少一类状态不足5次，渐近独立性检验尤其有限。</p>'
        body += '</details>'
    if result['missing']:
        body += '<h3>训练与截止材料缺口</h3>'+_table(('阶段','区间','证券／收益身份','因子','原因'),
            [({'training':'训练','forecast':'截止'}.get(r['stage'],r['stage']),r.get('period_id',''),r.get('code',r.get('identity','')),r.get('factor',''),REASONS.get(r['reason'],r['reason'])) for r in result['missing']])
    if result['holdout_missing']:
        body += '<h3>留出材料缺口</h3>'+_table(('区间','证券','原因'),
            [(r['period_id'],' / '.join(r['codes']),REASONS[r['reason']]) for r in result['holdout_missing']])
    return body


def _table(headers, rows):
    body = '<p class="mut">窄屏可左右滑动查看明细。</p><div class="tw" tabindex="0" role="region" aria-label="独立证据逐项明细"><table class="validation-details"><tr>'+''.join('<th>'+esc(h)+'</th>' for h in headers)+'</tr>'
    for row in rows[:100]:
        body += '<tr>'+''.join('<td>'+esc('缺失' if v is None else str(v))+'</td>' for v in row)+'</tr>'
    body += '</table></div>'
    return body+('<p>无此类差异。</p>' if not rows else '<p>页面最多显示100行，完整结果见JSON。</p>' if len(rows) > 100 else '')


def _zip(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    return buffer.getvalue()


def templates(mode):
    if mode == 'account':
        scope = dict(schema_version='account_reconciliation_scope_v1', account_id='', currency='CNY', start_date='', end_date='',
                     cash_basis='trade_date_total_cash', positions_complete=False, fills_complete=False, events_complete=False)
        raw = _zip({'scope.json': json.dumps(scope).encode(), **{name+'.csv': (','.join(fields)+'\n').encode() for name, fields in
                   [('balances', account.BALANCES), ('positions', account.POSITIONS), ('fills', account.FILLS), ('events', account.EVENTS)]}})
        files = {'internal.zip': raw, 'external.zip': raw, 'calendar.csv': b'date,is_open\n'}
    elif mode == 'factor':
        scope = dict(schema_version='factor_attribution_scope_v1', start_time='', end_time='', factors=[],
                     max_exposure_age_days=0, holdings_complete=False, cash_return='zero')
        files = {'scope.json': json.dumps(scope).encode(), **{name+'.csv': (','.join(fields)+'\n').encode() for name, fields in factor.FIELDS.items()}}
    elif mode == 'risk':
        from . import audit_risk_model as risk
        scope = dict(schema_version='risk_model_scope_v1',start_time='',cutoff_time='',end_time='',assets=[],factors=[],
                     model_definition='',holdings_complete=False,cash_return='zero',max_exposure_age_days=0,
                     min_train_periods=60,min_holdout_periods=60,shrinkage='0.1',confidence_level='0.95',periods_per_year=252)
        files = {'scope.json':json.dumps(scope).encode(),'calendar.csv':b'date,is_open\n',
                 **{name+'.csv':(','.join(fields)+'\n').encode() for name,fields in risk.FIELDS.items()}}
    else:
        raise ValueError('模板类型无效')
    files['README.txt'] = ('仅空字段模板，须填写真实范围、来源和完整性声明，不构成通过证据。\n'
        '账户两个ZIP内仅含根目录五文件，禁止嵌套目录；期初与完整日历不可省略。\n'
        '因子模板须填写定义（name,kind,definition,unit）、带时区的时点及可得时间，不倒填发布时间。\n'
        '风险模板另需完整日历、截止权重及证券总收益；样本/收缩/置信参数仅为待核对起点，不代表已校准。\n'
        '合成或模拟材料必须主动标明；账户、策略关系和外部来源未认证。\n').encode('utf-8')
    return _zip(files)


def register(app, workdir, *, page, guard, token):
    def load(identity, evidence_id):
        try:
            return evidence.read(workdir, identity, evidence_id)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, '证据不存在或需重新验证：材料/计算版本变化，原文件保留') from None

    @app.post('/strategy-audits/{identity}/validation')
    async def upload(identity: str, req: Request):
        from .strategy_audit_ui import upload_fields
        guard(req, token)
        try:
            strategies.read(workdir, identity)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, '策略材料不存在或核对失败') from None
        form = await upload_fields(req)
        try:
            guard(req, form.get('token'))
            mode = form.get('mode')
            if mode not in evidence.ROLES:
                raise ValueError('证据类型无效')
            allowed = {'token', 'mode', 'input_digest', 'source_kind', 'note', 'scope_confirmed'} | set(evidence.ROLES[mode])
            if set(form) - allowed or any(len(form.getlist(k)) != 1 for k in form):
                raise ValueError('表单含未知或重复字段')
            if form.get('scope_confirmed') != 'yes':
                raise ValueError('请核对期间、来源、完整性与单位口径')
            inputs = {}
            for role, suffix in evidence.ROLES[mode].items():
                item = form.get(role)
                if not hasattr(item, 'read') or not (item.filename or '').lower().endswith(suffix):
                    raise ValueError('请上传'+_label(mode,role))
                inputs[role] = await item.read(evidence.MAX_BYTES + 1)
            evidence_id = await run_in_threadpool(evidence.save, workdir, identity, input_digest=form.get('input_digest'),
                mode=mode, inputs=inputs, source_kind=form.get('source_kind'), note=form.get('note', ''))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            response = page('证据未保存', '<p class="notice warn" role="alert">'+esc(str(exc))+'</p><p><a href="/strategy-audits/'+identity+'#validation-evidence">返回证据材料，重新选择文件</a></p>', identity=identity, tab='import')
            response.status_code = 400
            response.headers.update(HEADERS)
            return response
        finally:
            await form.close()
        return RedirectResponse(f'/strategy-audits/{identity}/validation/{evidence_id}', 303)

    @app.get('/strategy-audits/{identity}/validation/templates/{mode}.zip')
    def template(identity: str, mode: str):
        try:
            strategies.read(workdir, identity)
            raw = templates(mode)
        except (OSError, ValueError, KeyError, TypeError):
            raise HTTPException(404, '策略或模板不存在') from None
        return Response(raw, media_type='application/zip', headers=HEADERS | {'Content-Disposition': f'attachment; filename="{mode}_validation_templates.zip"'})

    @app.get('/strategy-audits/{identity}/validation/{evidence_id}')
    def show(identity: str, evidence_id: str):
        report = load(identity, evidence_id)
        response = page(MODES[report['mode']], display(report), identity=identity, tab='import')
        response.headers.update(HEADERS)
        return response

    @app.get('/strategy-audits/{identity}/validation/{evidence_id}/report.json')
    def download(identity: str, evidence_id: str):
        report = load(identity, evidence_id)
        return Response(json.dumps(report, ensure_ascii=False, indent=2), media_type='application/json',
                        headers=HEADERS | {'Content-Disposition': 'attachment; filename="validation_evidence.json"'})

    @app.get('/strategy-audits/{identity}/validation/{evidence_id}/inputs/{role}')
    def source(identity: str, evidence_id: str, role: str):
        report = load(identity, evidence_id)
        if role not in report['inputs']:
            raise HTTPException(404, '输入不存在')
        item = report['inputs'][role]
        raw = (evidence.root(workdir, identity, evidence_id) / item['stored']).read_bytes()
        suffix = evidence.ROLES[report['mode']][role]
        return Response(raw, media_type={'.zip': 'application/zip', '.csv': 'text/csv', '.json': 'application/json'}[suffix],
                        headers=HEADERS | {'Content-Disposition': f'attachment; filename="{role}{suffix}"'})
