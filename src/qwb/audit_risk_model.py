"""Frozen-cutoff joint factor/residual covariance and retrospective holdout diagnostics.

The supplied factor model is not fitted here. Residual correlations and their
cross-covariance with factors are retained. Inputs are declarations, not source
authentication; a retrospective split is not a preregistered forward trial.
"""
from __future__ import annotations

import csv
from datetime import timedelta
from decimal import Decimal
import hashlib
import io
import json
import math
from pathlib import Path
import platform
from statistics import NormalDist
from zoneinfo import ZoneInfo

import numpy as np
import scipy
from scipy.stats import binomtest, chi2

from . import audit_account_reconciliation as account
from . import audit_factor_attribution as factor

SOURCE_KINDS = factor.SOURCE_KINDS
ROLES = ('scope', 'calendar', 'periods', 'weights', 'exposures', 'factor_returns', 'asset_returns')
FIELDS = {
    'periods': ('period_id', 'start_time', 'end_time', 'source_ref'),
    'weights': ('book', 'code', 'weight', 'asof_time', 'available_at', 'source_ref'),
    'exposures': factor.FIELDS['exposures'],
    'factor_returns': factor.FIELDS['factor_returns'],
    'asset_returns': ('period_id', 'code', 'return', 'available_at', 'source_ref'),
}
BASE_KEYS = {'start_time', 'end_time', 'factors', 'max_exposure_age_days', 'holdings_complete', 'cash_return'}
EXTRA_KEYS = {'schema_version', 'cutoff_time', 'assets', 'min_train_periods', 'min_holdout_periods',
              'shrinkage', 'confidence_level', 'periods_per_year', 'model_definition'}


def _table(raw, name):
    try:
        reader = csv.DictReader(io.StringIO(factor._text(raw, name)), strict=True)
        headers = reader.fieldnames or []
        if len(headers) != len(set(headers)) or set(headers) != set(FIELDS[name]):
            raise ValueError(f'{name}须有唯一完整表头：{",".join(FIELDS[name])}')
        rows = []
        for line, row in enumerate(reader, 2):
            if line > 100001 or None in row or any(v is None or not v.strip() for v in row.values()):
                raise ValueError(f'{name}行数超限或第{line}行字段缺失')
            if any(len(v.strip()) > (1024 if k == 'source_ref' else 128) for k, v in row.items()):
                raise ValueError(f'{name}字段长度超限')
            rows.append({k: v.strip() for k, v in row.items()})
        return rows
    except csv.Error as exc:
        raise ValueError(f'{name} CSV格式错误') from exc


def _scope(raw):
    try:
        scope = json.loads(factor._text(raw, 'scope'), object_pairs_hook=factor._unique)
    except json.JSONDecodeError:
        raise ValueError('scope须为有效JSON') from None
    if not isinstance(scope, dict) or set(scope) != BASE_KEYS | EXTRA_KEYS or scope['schema_version'] != 'risk_model_scope_v1':
        raise ValueError('风险scope字段或版本无效')
    base = {k: scope[k] for k in BASE_KEYS}
    base['schema_version'] = 'factor_attribution_scope_v1'
    factor._scope(json.dumps(base).encode())
    assets = scope['assets']
    if (not isinstance(assets, list) or not 1 <= len(assets) <= 100 or
            any(not isinstance(a, str) or not a.strip() or a != a.strip() or len(a) > 128 or a == 'CASH' for a in assets) or
            len(set(assets)) != len(assets) or len(scope['factors']) > 20):
        raise ValueError('须声明1至100个不同非现金证券及最多20个因子')
    if not factor._time(scope['start_time']) < factor._time(scope['cutoff_time']) < factor._time(scope['end_time']):
        raise ValueError('训练起点须早于截止时点，截止时点须早于留出期终点')
    for key, low, high in [('min_train_periods', 3, 2000), ('min_holdout_periods', 2, 2000), ('periods_per_year', 1, 366)]:
        if type(scope[key]) is not int or not low <= scope[key] <= high:
            raise ValueError(f'{key}须为{low}至{high}整数')
    factor._number(scope['shrinkage'], Decimal(0), Decimal(1))
    factor._number(scope['confidence_level'], Decimal('0.9'), Decimal('0.999'))
    definition = scope['model_definition']
    if not isinstance(definition, str) or not definition.strip() or len(definition) > 4096:
        raise ValueError('model_definition须明确模型、收益、残差及历史版本来源，最多4096字')
    return scope


def _close(stamp):
    local = stamp.astimezone(ZoneInfo('Asia/Shanghai'))
    if (local.hour, local.minute, local.second, local.microsecond) != (15, 0, 0, 0):
        raise ValueError('当前风险接口只支持中国市场相邻开市日15:00收盘到收盘区间')
    return local.date().isoformat()


def _digest(value):
    encoded = json.dumps(factor._serialize(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _selected(histories, code, names, stamp, age, industries):
    choices, missing = {}, []
    for name in names:
        eligible = [r for r in histories.get((code, name), []) if r['available_at'] <= stamp]
        choice = max(eligible, key=lambda r: (r['asof_time'], r['available_at'])) if eligible else None
        reason = ('missing_or_not_yet_available' if choice is None else
                  'stale' if stamp-choice['asof_time'] > timedelta(days=age) else None)
        if reason:
            missing.append(dict(code=code, factor=name, reason=reason))
        else:
            choices[name] = choice
    if not missing and industries and abs(sum((choices[f]['exposure'] for f in industries), Decimal(0))-1) > Decimal('0.000000000001'):
        raise ValueError('每个证券的完整行业暴露之和须为1；不补未知行业')
    return choices, missing


def _matrix(rows):
    values = np.asarray(rows, dtype=float)
    if not np.isfinite(values).all():
        raise ValueError('风险输入不能含非有限数值')
    return values


def _covariance(matrix, shrinkage):
    centered = matrix-matrix.mean(axis=0)
    sample = centered.T @ centered/(len(matrix)-1)
    result = (1-shrinkage)*sample + shrinkage*np.diag(np.diag(sample))
    if not np.isfinite(result).all():
        raise ValueError('协方差数值不可计算')
    eigenvalues = np.linalg.eigvalsh(result)
    if eigenvalues.min() < -1e-10*max(1e-300, float(np.max(np.diag(result)))):
        raise ValueError('风险协方差未通过半正定数值检查')
    return result, eigenvalues


def _risk(vector, covariance, n_factors, confidence, annualizer):
    marginal = covariance @ vector
    variance = float(vector @ marginal)
    scale = float(np.sum(np.abs(np.outer(vector,vector)*covariance)))
    if variance < -1e-12*max(scale, 1e-30):
        raise ValueError('组合方差为负，不能静默裁剪实质错误')
    roundoff_adjustment = -variance if variance < 0 else 0.0
    variance = max(variance, 0.0)
    sigma = math.sqrt(variance)
    factor_var = float(vector[:n_factors] @ covariance[:n_factors, :n_factors] @ vector[:n_factors])
    residual_var = float(vector[n_factors:] @ covariance[n_factors:, n_factors:] @ vector[n_factors:])
    cross = float(2*vector[:n_factors] @ covariance[:n_factors, n_factors:] @ vector[n_factors:])
    z = NormalDist().inv_cdf(confidence)
    components = vector*marginal
    return dict(variance=variance, variance_roundoff_adjustment=roundoff_adjustment,
                volatility=sigma, annualized_volatility=sigma*math.sqrt(annualizer),
                factor_variance=factor_var, residual_variance=residual_var, factor_residual_cross_variance=cross,
                variance_closure_error=variance-factor_var-residual_var-cross,
                variance_contributions=components.tolist(),
                volatility_contributions=(components/sigma).tolist() if sigma else None,
                variance_shares=(components/variance).tolist() if variance else None,
                gaussian_var_zero_mean=z*sigma,
                gaussian_es_zero_mean=math.exp(-z*z/2)/math.sqrt(2*math.pi)/(1-confidence)*sigma,
                zero_variance=sigma == 0)


def _evaluate(observed, forecast, confidence, minimum):
    n = len(observed)
    result = dict(periods=n, status='insufficient' if n < minimum else 'diagnostic_only',
                  calibrated=False, model_selection_on_holdout=False)
    if n < minimum:
        result['reason'] = 'missing_or_too_few_complete_holdout_periods'
        return result
    values = _matrix(observed)
    sigma, threshold = forecast['volatility'], forecast['gaussian_var_zero_mean']
    hits = (-values > threshold)
    count, probability = int(hits.sum()), 1-confidence
    mean = float(values.mean())
    sd = float(values.std(ddof=1))
    result.update(observed_mean=mean, observed_volatility=sd,
                  observed_rms=float(np.sqrt(np.mean(values**2))), exceedances=count,
                  exceedance_rate=count/n, expected_exceedances=n*probability,
                  tail_sample_status='too_few_expected_exceedances' if n*probability < 5 else 'diagnostic_only',
                  observations=values.tolist(), exceedance_flags=hits.tolist())
    if sigma == 0:
        result.update(status='degenerate_forecast', observed_nonzero_count=int((values != 0).sum()),
                      volatility_ratio=None, standardized_mean=None, standardized_rms=None,
                      kupiec_lr=None, kupiec_p_value=None, exact_binomial_p_value=None,
                      volatility_ratio_ci95_iid_gaussian=None)
        return result
    # Stable endpoint conventions for zero/all breaches: 0*log(0) = 0.
    actual_p = count/n
    ll_null = count*math.log(probability) + (n-count)*math.log1p(-probability)
    ll_fit = (count*math.log(actual_p) if count else 0) + ((n-count)*math.log1p(-actual_p) if count < n else 0)
    lr = max(0.0, 2*(ll_fit-ll_null))
    result.update(volatility_ratio=sd/sigma, standardized_mean=mean/sigma,
                  standardized_rms=float(np.sqrt(np.mean(values**2)))/sigma,
                  kupiec_lr=lr, kupiec_p_value=float(chi2.sf(lr, 1)),
                  exact_binomial_p_value=float(binomtest(count, n, probability).pvalue),
                  volatility_ratio_ci95_iid_gaussian=[math.sqrt((n-1)*sd*sd/chi2.ppf(.975,n-1))/sigma,
                                                      math.sqrt((n-1)*sd*sd/chi2.ppf(.025,n-1))/sigma])
    transitions = np.zeros((2,2),dtype=int)
    for a,b in zip(hits[:-1],hits[1:]):
        transitions[int(a),int(b)] += 1
    result['exceedance_transitions'] = transitions.tolist()
    result['small_transition_sample'] = bool(transitions.sum(axis=1).min()<5)
    if not transitions.sum(axis=1).all():
        result['independence_test'] = dict(status='insufficient_hit_variation',lr=None,p_value=None)
        result['conditional_coverage_test'] = dict(status='insufficient_hit_variation',lr=None,p_value=None)
    else:
        def log_likelihood(zero,one,p):
            return (zero*math.log1p(-p) if zero else 0)+(one*math.log(p) if one else 0)
        common_p = float(transitions[:,1].sum()/transitions.sum())
        common = log_likelihood(int(transitions[:,0].sum()),int(transitions[:,1].sum()),common_p)
        separate = sum(log_likelihood(int(row[0]),int(row[1]),float(row[1]/row.sum())) for row in transitions)
        independent_lr = max(0.0,2*(separate-common))
        result['independence_test'] = dict(status='diagnostic_only',lr=independent_lr,p_value=float(chi2.sf(independent_lr,1)))
        result['conditional_coverage_test'] = dict(status='diagnostic_only',lr=lr+independent_lr,p_value=float(chi2.sf(lr+independent_lr,2)))
    return result


def analyze(scope_raw, calendar_raw, periods_raw, weights_raw, exposures_raw, factor_returns_raw, asset_returns_raw, *, source_kind):
    if source_kind not in SOURCE_KINDS:
        raise ValueError('须主动声明风险材料来源')
    inputs = dict(zip(ROLES, (scope_raw, calendar_raw, periods_raw, weights_raw, exposures_raw, factor_returns_raw, asset_returns_raw)))
    if any(not isinstance(raw, bytes) or len(raw) > factor.MAX_BYTES for raw in inputs.values()) or sum(map(len, inputs.values())) > 48*1024*1024:
        raise ValueError('单个风险输入最多8MB，合计最多48MB')
    scope = _scope(scope_raw)
    names = [f['name'] for f in scope['factors']]
    assets = sorted(scope['assets'])
    industries = {f['name'] for f in scope['factors'] if f['kind'] == 'industry'}
    start, cutoff, end = (factor._time(scope[k]) for k in ('start_time','cutoff_time','end_time'))
    first, last = _close(start), _close(end)
    _close(cutoff)
    if (end-start).days > 3660:
        raise ValueError('风险样本范围最多3660天')
    sessions = account._calendar(calendar_raw, dict(start_date=first, end_date=last))
    periods = {}
    for row in _table(periods_raw, 'periods'):
        a, b = factor._time(row['start_time']), factor._time(row['end_time'])
        if row['period_id'] in periods or not start <= a < b <= end:
            raise ValueError('风险区间身份重复或超范围')
        _close(a)
        _close(b)
        periods[row['period_id']] = dict(row, start_time=a, end_time=b)
    ordered = sorted(periods.values(), key=lambda r: r['start_time'])
    if not ordered or len(ordered) > 2000 or len(ordered) != len(sessions)-1:
        raise ValueError('风险区间须覆盖日历全部相邻开市日，最多2000期；不能跨缺失区间拼收益')
    if (len(ordered)+1)*len(assets)*len(names) > 250000:
        raise ValueError('风险区间×证券×因子及期末快照合计最多250000项，须缩小显式模型范围')
    for i, period in enumerate(ordered):
        if _close(period['start_time']) != sessions[i] or _close(period['end_time']) != sessions[i+1]:
            raise ValueError('风险区间须为日历相邻收盘，不能跳日或重叠')
    if cutoff not in {r['end_time'] for r in ordered[:-1]}:
        raise ValueError('训练截止须落在完整区间边界且留出后续区间')
    weights = {}
    for row in _table(weights_raw, 'weights'):
        key = (row['book'],row['code'])
        a, available = factor._time(row['asof_time']), factor._time(row['available_at'])
        if row['book'] not in ('portfolio','benchmark') or row['code'] not in assets+['CASH'] or key in weights:
            raise ValueError('权重book/证券身份无效或重复')
        if a != cutoff or available != cutoff:
            raise ValueError('固定权重须为截止时点状态且已于该时点可得')
        weights[key] = dict(row, asof_time=a, available_at=available,
                            weight=factor._number(row['weight'], Decimal(0), Decimal(1)))
    missing = []
    for book in ('portfolio','benchmark'):
        if (book,'CASH') not in weights:
            missing.append(dict(stage='forecast',book=book,reason='missing_cash_row'))
        if abs(sum((r['weight'] for (b,_),r in weights.items() if b == book),Decimal(0))-1) > Decimal('0.000000000001'):
            raise ValueError('每book完整权重之和须为1，不自动归一化')
    histories, versions = {}, set()
    for row in _table(exposures_raw, 'exposures'):
        a, available = factor._time(row['asof_time']), factor._time(row['available_at'])
        key = (row['code'],row['factor'],a,available)
        if row['code'] not in assets or row['factor'] not in names or key in versions or available < a:
            raise ValueError('风险暴露证券/因子/版本或可得时间无效')
        versions.add(key)
        exposure = factor._number(row['exposure'], Decimal(0) if row['factor'] in industries else Decimal(-1000),
                                  Decimal(1) if row['factor'] in industries else Decimal(1000))
        histories.setdefault(key[:2],[]).append(dict(row,asof_time=a,available_at=available,exposure=exposure))
    returns = {}
    for label, identity, universe in [('factor_returns','factor',names),('asset_returns','code',assets)]:
        parsed = {}
        for row in _table(inputs[label],label):
            key = (row['period_id'],row[identity])
            available = factor._time(row['available_at'])
            if row['period_id'] not in periods or row[identity] not in universe or key in parsed:
                raise ValueError(f'{label}身份无效或重复；修订须先形成可复核时点快照')
            if available < periods[row['period_id']]['end_time']:
                raise ValueError('完整区间收益不能在区间结束前可得')
            low = Decimal(-100) if label == 'factor_returns' else Decimal('-0.999999999999')
            value = factor._number(row['return'], low, Decimal(100))
            parsed[key] = dict(row,available_at=available,**{'return':value})
        returns[label] = parsed
    training, training_evidence, holdout = [], [], []
    holdout_missing = []
    for period in ordered:
        pid = period['period_id']
        is_train = period['end_time'] <= cutoff
        if not is_train:
            absent = [code for code in assets if (pid,code) not in returns['asset_returns']]
            if absent:
                holdout_missing.append(dict(period_id=pid,reason='missing_holdout_asset_return',codes=absent))
            else:
                holdout.append(dict(period_id=pid,returns=[float(returns['asset_returns'][(pid,c)]['return']) for c in assets]))
            continue
        selected, problems = {}, []
        for code in assets:
            chosen, issues = _selected(histories,code,names,period['start_time'],scope['max_exposure_age_days'],industries)
            selected[code] = chosen
            problems.extend(issues)
        for label, universe in [('factor_returns',names),('asset_returns',assets)]:
            for identity in universe:
                row = returns[label].get((pid,identity))
                if row is None or row['available_at'] > cutoff:
                    problems.append(dict(input=label,identity=identity,reason='missing_or_unavailable_training_return'))
        if problems:
            missing.extend(dict(stage='training',period_id=pid,**issue) for issue in problems)
            continue
        f = [float(returns['factor_returns'][(pid,n)]['return']) for n in names]
        r = [float(returns['asset_returns'][(pid,c)]['return']) for c in assets]
        b = [[float(selected[c][n]['exposure']) for n in names] for c in assets]
        residual = _matrix(r)-_matrix(b) @ _matrix(f)
        training.append(f+residual.tolist())
        training_evidence.append(dict(period=period,selected_exposures=selected,
            factor_returns=[returns['factor_returns'][(pid,n)] for n in names],
            asset_returns=[returns['asset_returns'][(pid,c)] for c in assets]))
    forecast_exposures = {}
    for code in assets:
        chosen, issues = _selected(histories,code,names,cutoff,scope['max_exposure_age_days'],industries)
        forecast_exposures[code] = chosen
        missing.extend(dict(stage='forecast',**issue) for issue in issues)
    if len(training) < scope['min_train_periods']:
        missing.append(dict(stage='training',reason='too_few_training_periods',complete=len(training),minimum=scope['min_train_periods']))
    implementation = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
                      (Path(__file__), Path(factor.__file__), Path(account.__file__))}
    numerical_environment = dict(numpy=np.__version__,scipy=scipy.__version__,python=platform.python_version(),machine=platform.machine())
    report = dict(schema_version='risk_model_v1',status='insufficient' if missing else 'computed',source_kind=source_kind,
        source_authenticated=False,institutional_readiness='not_established',risk_model_calibrated=False,
        evaluation_design='retrospective_fixed_cutoff_holdout_not_preregistered_forward',
        portfolio_path='each_session_rebalanced_to_cutoff_weights_zero_cost_zero_cash_return',
        scope=scope,missing=missing,holdout_missing=holdout_missing,forecast=None,evaluation=None,
        forecast_status='insufficient' if missing else 'computed',evaluation_status='insufficient',
        training_complete_periods=len(training),training_expected_periods=sum(r['end_time'] <= cutoff for r in ordered),
        holdout_expected_periods=sum(r['start_time'] >= cutoff for r in ordered),
        input_sha256={k:hashlib.sha256(v).hexdigest() for k,v in inputs.items()},calculator_sha256=implementation,
        numerical_environment=numerical_environment,
        limitations=['输入的来源、发布时间、复权、分红、历史因子定义及冻结声明未经认证。',
                     '残差由当期初可得暴露和给定因子收益计算；不是独立特异风险、alpha或因果归因。',
                     '联合协方差保留残差之间及因子与残差交叉项；不假设特异风险独立。',
                     '收缩系数由输入固定，不用留出期选参；未估计最优收缩、波动聚集或尾部依赖。',
                     '零均值高斯VaR/ES与平方根年化依赖分布和时间独立假设；置信区间及检验只作诊断。',
                     '固定权重每日无成本再平衡是模型组合，不是实际策略净值、买入持有路径或真实TCA。',
                     '留出期不进入预测计算；回顾性划分不证明预注册、独立盲测或前向部署有效。',
                     '不以不拒绝检验视为校准通过；预期尾部次数不足5时明确提示。'])
    if missing:
        return factor._serialize(report)
    matrix = _matrix(training)
    covariance, eigenvalues = _covariance(matrix,float(scope['shrinkage']))
    b = _matrix([[float(forecast_exposures[c][n]['exposure']) for n in names] for c in assets])
    projection = np.concatenate((b,np.eye(len(assets))),axis=1)
    asset_covariance = projection @ covariance @ projection.T
    weight_vectors = {book:_matrix([float(weights.get((book,c),{}).get('weight',0)) for c in assets]) for book in ('portfolio','benchmark')}
    weight_vectors['active'] = weight_vectors['portfolio']-weight_vectors['benchmark']
    labels = [dict(kind='factor',name=n) for n in names]+[dict(kind='residual',code=c) for c in assets]
    forecast = {}
    for book,w in weight_vectors.items():
        vector = np.concatenate((w @ b,w))
        forecast[book] = _risk(vector,covariance,len(names),float(scope['confidence_level']),scope['periods_per_year'])
        forecast[book].update(factor_exposures=(w @ b).tolist(),asset_weights=w.tolist())
        asset_contributions = w*(asset_covariance @ w)
        sigma = forecast[book]['volatility']
        forecast[book].update(asset_variance_contributions=asset_contributions.tolist(),
                              asset_volatility_contributions=(asset_contributions/sigma).tolist() if sigma else None)
    model = dict(cutoff_time=cutoff,assets=assets,factors=scope['factors'],model_definition=scope['model_definition'],
                 max_exposure_age_days=scope['max_exposure_age_days'],shrinkage=scope['shrinkage'],
                 confidence_level=scope['confidence_level'],periods_per_year=scope['periods_per_year'],
                 weights=[weights[k] for k in sorted(weights)],training=training_evidence,
                 forecast_exposures=forecast_exposures,calculator_sha256=implementation,numerical_environment=numerical_environment)
    report.update(forecast=forecast,model_digest=_digest(model),model_inputs=factor._serialize(model),
                  joint_covariance=covariance.tolist(),projected_asset_covariance=asset_covariance.tolist(),
                  joint_sample_means=matrix.mean(axis=0).tolist(),
                  covariance_ddof=1,component_labels=labels,covariance_min_eigenvalue=float(eigenvalues.min()),
                  covariance_rank=int(np.linalg.matrix_rank(covariance)),covariance_dimension=len(labels),
                  rank_deficient=bool(np.linalg.matrix_rank(covariance)<len(labels)),
                  forecast_exposure_versions=factor._serialize(forecast_exposures))
    evaluation = {}
    for book,w in weight_vectors.items():
        observed = [float(_matrix(row['returns']) @ w) for row in holdout]
        if holdout_missing:
            evaluation[book] = dict(status='insufficient',periods=len(observed),calibrated=False,
                                    reason='incomplete_holdout_no_partial_calibration',observations=None)
        else:
            evaluation[book] = _evaluate(observed,forecast[book],float(scope['confidence_level']),scope['min_holdout_periods'])
        evaluation[book]['period_ids'] = [r['period_id'] for r in holdout]
    report['evaluation'] = evaluation
    evaluation_complete = not holdout_missing and len(holdout) >= scope['min_holdout_periods']
    report['evaluation_status'] = 'diagnostic_only' if evaluation_complete else 'insufficient'
    report['status'] = 'computed' if evaluation_complete else 'insufficient'
    # JSON strictness is checked here rather than quietly emitting NaN/Infinity.
    serialized = factor._serialize(report)
    json.dumps(serialized,allow_nan=False)
    return serialized
