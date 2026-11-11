"""Optional explicit evidence bundles, submitted by the user, never fetched."""
from __future__ import annotations

import json

SCHEMA = 'audit_advanced_inputs_v1'
KINDS = {'factor': {'scope', 'periods', 'weights', 'exposures', 'factor_returns'},
         'tca': {'orders', 'fills'}}


def parse(raw):
    def unique(pairs):
        d = {}
        for k, v in pairs:
            if k in d:
                raise ValueError('高级证据包含重复字段')
            d[k] = v
        return d
    try:
        data = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise ValueError('高级证据包须为UTF-8 JSON且字段不能重复') from exc
    if (not isinstance(data, dict) or data.get('schema_version') != SCHEMA or
            set(data) - {'schema_version', 'source_kind', *KINDS}):
        raise ValueError('高级证据包格式或版本不支持')
    if data.get('source_kind') not in {'broker_export', 'simulator_native', 'synthetic_fixture', 'historical_data', 'historical_returns_synthetic_exposures'}:
        raise ValueError('高级证据须声明 source_kind')
    if not any(k in data for k in KINDS):
        raise ValueError('高级证据包没有归因或执行材料')
    from .audit_factor_attribution import SOURCE_KINDS as FACTOR_SOURCES
    from .audit_reconciliation import SOURCE_KINDS as TCA_SOURCES
    for kind, allowed in [('factor', FACTOR_SOURCES), ('tca', TCA_SOURCES)]:
        if kind in data and data['source_kind'] not in allowed:
            raise ValueError(kind + ' 的 source_kind 不匹配，须为 ' + '/'.join(allowed))
    for kind, fields in KINDS.items():
        if kind in data and (not isinstance(data[kind], dict) or set(data[kind]) != fields or
                             any(not isinstance(v, str) for v in data[kind].values())):
            raise ValueError(f'{kind} 须包含完整的原始CSV/JSON文本：' + ','.join(sorted(fields)))
    return data


def analyze(bundle):
    data = bundle.get('advanced_inputs') or {}
    modules = {}
    if 'factor' in data:
        from . import audit_factor_attribution as factor
        d = data['factor']
        try:
            modules['factor_attribution'] = factor.analyze(*(d[k].encode('utf-8') for k in
                ('scope', 'periods', 'weights', 'exposures', 'factor_returns')), source_kind=data['source_kind'])
        except (ValueError, TypeError, KeyError) as exc:
            modules['factor_attribution'] = dict(status='invalid', error=str(exc))
    if 'tca' in data:
        from . import audit_tca
        d = data['tca']
        try:
            modules['implementation_shortfall'] = audit_tca.analyze(d['orders'].encode('utf-8'), d['fills'].encode('utf-8'), data['source_kind'])
        except (ValueError, TypeError, KeyError) as exc:
            modules['implementation_shortfall'] = dict(status='invalid', error=str(exc))
    # These are separate submitted evidence scopes, not automatically matched to
    # the main account. Do not silently add their money to the main P&L bridge.
    for value in modules.values():
        value['account_link_status'] = 'separate_submitted_scope_not_reconciled_to_main_account'
    return modules


def guide():
    from .audit_factor_attribution import FIELDS
    from .audit_tca import ORDER_FIELDS, FILL_FIELDS
    lines = ['高级证据与持有对照：仅使用你提交的数据', '',
        '不需要高级材料也可审计。平台不提供、下载或补齐行情、基准、因子、公司行动。', '',
        '一、同起点持有对照',
        '上传 prices.csv（date,code,close），覆盖首日持有标的每个账户日期的不复权收盘价；',
        '上传 listings.csv（code,listed_date,delisted_date）。',
        '确实满足以下事实时，才在 strategy_spec.csv（key,value）中逐项声明true：',
        'control_no_corporate_actions：整个区间无分红送转等公司行动；',
        'control_no_external_flows：整个区间无外部出入金；',
        'control_unadjusted_prices：全部价格为同币种不复权收盘价。',
        '有事件时不要为启用对照填写true；绝对分红金额不能套用到另一持仓路径。',
        '从首日收盘仓位和现金共同起算，原版保留实际后续操作，持有对照不再交易。',
        '现金零收益、末日不清仓；它是操作对照，不是完整资格退出或风险约束替代策略。', '',
        '二、行业风格或母订单执行证据',
        '将完整原始文本放入 audit_evidence.json，与对应时期文件一起上传。',
        'schema_version 固定为 audit_advanced_inputs_v1。',
        'source_kind：factor 使用 historical_data / synthetic_fixture / historical_returns_synthetic_exposures；',
        'tca 使用 broker_export / simulator_native / synthetic_fixture。两类来源不同时分别提交，不能混用。',
        'factor 对象字段：scope（JSON文本）、periods、weights、exposures、factor_returns（CSV文本）。',
        'tca 对象字段：orders、fills（CSV文本）；至少提供一个对象。不接收URL或本地路径代替原文。',
        'scope 包含 schema_version=factor_attribution_scope_v1、start_time、end_time、factors、',
        'max_exposure_age_days（0至366）、holdings_complete=true、cash_return=zero。',
        'factors 每项：name、kind（industry/style）、definition、unit（return_per_unit_exposure）。',
        '所有时间须含时区；暴露可得时间不得晚于形成时点；权重含portfolio/benchmark与CASH，合计1。']
    for name, fields in FIELDS.items():
        lines.append(name + '表头：' + ','.join(fields))
    lines += ['tca orders表头：' + ','.join(ORDER_FIELDS), 'tca fills表头：' + ','.join(FILL_FIELDS),
        '用项目 scripts/build_audit_evidence.py 将已有本地文本打包，例如：',
        'python scripts/build_audit_evidence.py --kind factor --source-kind historical_data --directory <用户材料目录> --out audit_evidence.json',
        '它不获取数据。独立范围结果不自动并入主账户；不推造行业暴露、母订单或参考价。', '',
        '三、研究档案', 'strategy_spec.csv 中 trials 为实际试验次数声明，不是完整多重检验认证。',
        '全部历史时期均会纳入；重复日期单列，不当作独立样本。保留失败、冻结条件与真实新验证区间。']
    return '\n'.join(lines) + '\n'
