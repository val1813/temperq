"""Frozen, label-committed evaluation of action-card detectors on static exports.

Local commitments detect inconsistency, not independent custody or chronology.
No uploaded strategy is executed. Errors remain in conservative group gates.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path, PurePosixPath
import platform
import re
import sys

ID = re.compile(r'[A-Za-z0-9_-]{1,80}\Z')
SHA = re.compile(r'[0-9a-f]{64}\Z')
MAX_FILE = 8_000_000
MAX_CORPUS = 128_000_000
MAX_JSON = 24_000_000
SUFFIXES = {'.csv', '.txt', '.py', '.json'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def object_digest(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8'))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'JSON含重复字段')
        result[key] = value
    return result


def load(path):
    path = Path(path)
    require(path.stat().st_size <= MAX_JSON, 'JSON超过24MB')
    try:
        value = json.loads(path.read_bytes().decode('utf-8-sig'), object_pairs_hook=_pairs)
        object_digest(value)  # Also rejects NaN and infinity, including overflowing exponents.
        return value
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('须为有效的有限UTF-8 JSON') from exc


def _keys(value, keys, message):
    require(isinstance(value, dict) and set(value) == set(keys), message)


def _id(value):
    require(isinstance(value, str) and ID.fullmatch(value), '编号须为1至80位字母、数字、下划线或连字符')


def _ids(values):
    require(isinstance(values, list) and len(values) <= 1000, '编号列表无效或超过1000项')
    for value in values:
        _id(value)
    require(len(values) == len(set(values)), '编号列表重复')


def _number(value, low, high):
    require(type(value) in (int, float) and low <= value <= high and math.isfinite(value), '数字非有限或超出范围')


def _relative(name):
    require(isinstance(name, str) and 0 < len(name) <= 240 and '\\' not in name and ':' not in name, '材料路径须为相对POSIX路径')
    p = PurePosixPath(name)
    require(not p.is_absolute() and p.as_posix() == name and not any(x in ('.', '..') for x in name.split('/')), '材料路径越界或不规范')
    require(not any(ord(c) < 32 or c in '<>"|?*' for c in name) and len(p.name) <= 120, '材料路径含非法字符或文件名过长')
    require(all(not x.endswith((' ', '.')) for x in p.parts), '材料路径不能以空格或点结尾')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    require(all(x.split('.')[0].upper() not in reserved for x in p.parts), '材料路径含设备保留名')
    require(p.suffix.lower() in SUFFIXES, '只接受CSV/TXT/PY/JSON静态材料，不接受压缩包或二进制')
    return p


def validate_manifest(manifest):
    from . import audit_rules
    _keys(manifest, ('cases', 'settings'), '样本清单须只含cases和settings')
    settings = manifest['settings']
    _keys(settings, ('commission_bp', 'minimum_commission', 'max_weight', 'thresholds'), '审计设置须显式声明费用、权重及阈值')
    for key, low, high in [('commission_bp', 0, 1000), ('minimum_commission', 0, 10000), ('max_weight', 1e-12, 1)]:
        _number(settings[key], low, high)
    require(isinstance(settings['thresholds'], dict), 'thresholds须为对象')
    audit_rules.resolve(settings['thresholds'])
    cases = manifest['cases']
    require(isinstance(cases, list) and 1 <= len(cases) <= 1000, '样本数须为1至1000')
    ids, used_paths, fingerprints = set(), set(), {}
    for case in cases:
        _keys(case, ('id', 'group_id', 'source_kind', 'opening_cash', 'files'), '样本字段不完整或含标签字段')
        _id(case['id'])
        _id(case['group_id'])
        require(case['id'] not in ids, '样本编号重复')
        ids.add(case['id'])
        require(case['source_kind'] in ('synthetic_fixture', 'simulator_native', 'platform_export'), '样本来源声明无效')
        _number(case['opening_cash'], 1e-12, 1e15)
        require(isinstance(case['files'], dict) and 1 <= len(case['files']) <= 16, '每例须明确1至16份材料')
        basenames = {}
        for name, digest in case['files'].items():
            p = _relative(name)
            require(isinstance(digest, str) and SHA.fullmatch(digest), '材料SHA256无效')
            key = name.casefold()
            require(key not in used_paths, '材料路径不能在样本间复用')
            used_paths.add(key)
            require(p.name.casefold() not in basenames, '同例材料文件名冲突')
            basenames[p.name.casefold()] = digest
        fingerprint = object_digest(basenames)
        require(fingerprint not in fingerprints or fingerprints[fingerprint] == case['group_id'], '逐字相同的材料不得拆成多个独立组')
        fingerprints[fingerprint] = case['group_id']
    return manifest


def validate_protocol(protocol, manifest):
    _keys(protocol, ('cohort_kind', 'development_group_ids', 'defects', 'clean_failure_max', 'minimum_clean_groups'), '预注册协议字段不完整')
    require(protocol['cohort_kind'] in ('synthetic_development', 'external_holdout_declared'), '队列性质声明无效')
    _ids(protocol['development_group_ids'])
    require(not set(protocol['development_group_ids']) & {c['group_id'] for c in manifest['cases']}, '开发组与验证组重叠')
    require(isinstance(protocol['defects'], list) and 1 <= len(protocol['defects']) <= 30, '须预注册1至30类缺陷')
    seen = set()
    for defect in protocol['defects']:
        _keys(defect, ('id', 'card_ids', 'recall_min', 'minimum_groups'), '缺陷定义字段不完整')
        _id(defect['id'])
        require(defect['id'] not in seen, '缺陷编号重复')
        seen.add(defect['id'])
        _ids(defect['card_ids'])
        require(defect['card_ids'], '每类缺陷须预注册对应卡片编号')
        _number(defect['recall_min'], 0.01, 1)
        require(type(defect['minimum_groups']) is int and 1 <= defect['minimum_groups'] <= 1000, '最少缺陷组数无效')
    _number(protocol['clean_failure_max'], 0, .99)
    require(type(protocol['minimum_clean_groups']) is int and 1 <= protocol['minimum_clean_groups'] <= 1000, '最少干净组数无效')
    return protocol


def runtime_fingerprint():
    """Bind installed Python sources and reported versions, not binary attestation."""
    root = Path(__file__).resolve().parent
    sources = {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in sorted(root.rglob('*.py'))}
    packages = sorted((d.metadata['Name'], d.version) for d in importlib.metadata.distributions() if d.metadata['Name'])
    return dict(qwb_python_sources=sources, distribution_versions=packages,
                python=sys.version, platform=platform.platform(), machine=platform.machine())


def _seal(schema, **values):
    result = dict(schema=schema, created_at=datetime.now(timezone.utc).isoformat(), **values)
    result['digest'] = object_digest(result)
    return result


def _integrity(artifact, schema, keys):
    _keys(artifact, ('schema', 'created_at', 'digest', *keys), '证据字段无效')
    require(artifact['schema'] == schema, '证据版本无效')
    require(artifact['digest'] == object_digest({k: v for k, v in artifact.items() if k != 'digest'}), '证据摘要不一致')


def freeze(manifest, protocol, labels_sha256):
    from . import audit_rules
    validate_manifest(manifest)
    validate_protocol(protocol, manifest)
    require(isinstance(labels_sha256, str) and SHA.fullmatch(labels_sha256), '须提供独立保管的原始标签文件SHA256')
    return _seal('audit_validation_plan_v1', manifest=manifest, protocol=protocol,
                 labels_sha256=labels_sha256, rules=audit_rules.snapshot(audit_rules.resolve(manifest['settings']['thresholds'])),
                 runtime=runtime_fingerprint())


def verify_plan(plan):
    from . import audit_rules
    _integrity(plan, 'audit_validation_plan_v1', ('manifest', 'protocol', 'labels_sha256', 'rules', 'runtime'))
    validate_manifest(plan['manifest'])
    validate_protocol(plan['protocol'], plan['manifest'])
    require(isinstance(plan['labels_sha256'], str) and SHA.fullmatch(plan['labels_sha256']), '标签承诺无效')
    require(plan['rules'] == audit_rules.snapshot(audit_rules.resolve(plan['manifest']['settings']['thresholds'])), '规则快照变化')
    # JSON converts metadata tuples to lists. Compare the canonical representation.
    require(object_digest(plan['runtime']) == object_digest(runtime_fingerprint()), '源码或依赖环境变化，须另建冻结计划，旧证据保留')


def _materials(root, manifest):
    from .strategy_imports import SourceFile
    root = Path(root).resolve(strict=True)
    require(root.is_dir(), '材料根目录不存在')
    result, total = {}, 0
    for case in manifest['cases']:
        files = []
        # JSON object order is not part of the canonical commitment; evaluation
        # therefore also uses a fixed filename order.
        for name, expected in sorted(case['files'].items()):
            p = root.joinpath(*PurePosixPath(name).parts)
            require(p.resolve().is_relative_to(root) and not any(x.is_symlink() for x in [p, *p.parents] if x != root and root in x.parents), '材料不能为符号链接或越界')
            require(p.is_file() and p.stat().st_size <= MAX_FILE, '材料缺失或单文件超过8MB')
            raw = p.read_bytes()
            total += len(raw)
            require(len(raw) <= MAX_FILE and total <= MAX_CORPUS, '材料单文件超过8MB或全队列超过128MB')
            require(sha(raw) == expected, '材料SHA256与冻结清单不一致')
            files.append(SourceFile(PurePosixPath(name).name, raw))
        result[case['id']] = files
    return result


def _evaluate(case, sources, settings):
    from . import strategy_imports, strategy_audit, audit_actions
    bundle = strategy_imports.normalize(sources, opening_cash=case['opening_cash'], name=case['id'])
    result = strategy_audit.analyze(bundle, dict(settings))
    cards = audit_actions.cards(result, bundle)
    books = (result.get('modules', {}).get('accounting') or {}).get('status', 'missing')
    return dict(status='computed' if books == 'verified' else 'incomplete', accounting_status=books,
                cards=[{k: c[k] for k in ('id', 'state', 'evidence')} for c in cards], error_type=None)


def predict(plan, materials_root):
    verify_plan(plan)
    materials = _materials(materials_root, plan['manifest'])  # Refuse changed corpus before any evaluation.
    rows = []
    for case in plan['manifest']['cases']:
        try:
            output = _evaluate(case, materials[case['id']], plan['manifest']['settings'])
        except (ValueError, OSError, ArithmeticError) as exc:
            output = dict(status='error', accounting_status='not_verified', cards=[], error_type=type(exc).__name__)
        rows.append(dict(case_id=case['id'], group_id=case['group_id'],
                         material_digest=object_digest(case['files']), **output))
    verify_plan(plan)
    return _seal('audit_validation_predictions_v1', plan_digest=plan['digest'], rows=rows)


def write(artifact, path):
    # Serialize before creating the file; exclusive creation preserves failed/old evidence.
    body = json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    with Path(path).open('x', encoding='utf-8') as stream:
        stream.write(body)


def _interval(count, n, confidence=.95):
    """Clopper-Pearson, conditional on independent identically sampled groups."""
    if not n:
        return dict(count=0, n=0, rate=None, lower=None, upper=None, confidence=confidence)
    from scipy.stats import beta
    alpha = 1 - confidence
    return dict(count=count, n=n, rate=count/n,
                lower=0. if count == 0 else float(beta.ppf(alpha/2, count, n-count+1)),
                upper=1. if count == n else float(beta.ppf(1-alpha/2, count+1, n-count)), confidence=confidence)


def _predictions(plan, predictions):
    _integrity(predictions, 'audit_validation_predictions_v1', ('plan_digest', 'rows'))
    require(predictions['plan_digest'] == plan['digest'], '预测不属于此冻结计划')
    expected = {c['id']: c for c in plan['manifest']['cases']}
    require(isinstance(predictions['rows'], list) and len(predictions['rows']) == len(expected), '预测有缺例或多例')
    rows = {}
    for row in predictions['rows']:
        _keys(row, ('case_id', 'group_id', 'material_digest', 'status', 'accounting_status', 'cards', 'error_type'), '预测行字段无效')
        require(isinstance(row['case_id'], str) and row['case_id'] in expected and row['case_id'] not in rows, '预测样本编号未知或重复')
        case = expected[row['case_id']]
        require(row['group_id'] == case['group_id'] and row['material_digest'] == object_digest(case['files']), '预测材料或组编号与冻结清单不一致')
        require(row['status'] in ('computed', 'incomplete', 'error'), '预测状态无效')
        require((row['status'] == 'computed') == (row['accounting_status'] == 'verified'), '账务状态与预测状态冲突')
        require(isinstance(row['cards'], list), '预测卡片须为列表')
        ids = []
        for card in row['cards']:
            _keys(card, ('id', 'state', 'evidence'), '预测卡片字段无效')
            _id(card['id'])
            require(card['state'] in ('act', 'review', 'hold'), '预测卡片状态无效')
            require(isinstance(card['evidence'], str), '证据等级无效')
            ids.append(card['id'])
        require(len(ids) == len(set(ids)), '预测卡片编号重复')
        rows[row['case_id']] = row
    return rows


def score(plan, predictions, labels_raw):
    verify_plan(plan)
    rows = _predictions(plan, predictions)
    require(isinstance(labels_raw, bytes) and len(labels_raw) <= MAX_JSON and sha(labels_raw) == plan['labels_sha256'], '标签原始字节与预先承诺SHA256不一致')
    try:
        labels = json.loads(labels_raw.decode('utf-8-sig'), object_pairs_hook=_pairs)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('标签须为UTF-8 JSON') from exc
    _keys(labels, ('cases',), '标签须只含cases')
    require(isinstance(labels['cases'], list) and len(labels['cases']) == len(rows), '标签必须覆盖全部冻结样本')
    definitions = {d['id']: d for d in plan['protocol']['defects']}
    outcomes, seen = [], set()
    for label in labels['cases']:
        _keys(label, ('case_id', 'defect_id', 'manifested', 'acceptable_act_card_ids'), '标签字段不完整')
        case_id = label['case_id']
        require(isinstance(case_id, str) and case_id in rows and case_id not in seen, '标签样本未知或重复')
        seen.add(case_id)
        defect = label['defect_id']
        require(defect is None or isinstance(defect, str) and defect in definitions, '标签含未预注册缺陷')
        require(type(label['manifested']) is bool and (defect is not None or label['manifested'] is False), '干净样本不能标已显现缺陷')
        _ids(label['acceptable_act_card_ids'])
        row = rows[case_id]
        act = {c['id'] for c in row['cards'] if c['state'] == 'act'}
        review = {c['id'] for c in row['cards'] if c['state'] == 'review'}
        targets = set(definitions[defect]['card_ids']) if defect else set()
        valid = row['status'] == 'computed'
        outcomes.append(dict(case_id=case_id, group_id=row['group_id'], defect_id=defect,
            manifested=label['manifested'], operational_error=not valid,
            matched_act=sorted(act & targets) if valid else [], matched_review=sorted(review & targets) if valid else [],
            hit=bool((act | review) & targets) and valid, any_act=bool(act), any_review=bool(review),
            unexpected_act=sorted(act - set(label['acceptable_act_card_ids']))))
    protocol = plan['protocol']
    # Bonferroni does not require independence across metrics. Within each metric,
    # the declared strategy groups must still be independently and identically sampled.
    confidence = 1 - .05/(len(definitions) + 1)
    defects, gates = {}, []
    for name, definition in definitions.items():
        own = [r for r in outcomes if r['defect_id'] == name and r['manifested']]
        grouped = defaultdict(list)
        for row in own:
            grouped[row['group_id']].append(row)
        group_hits = {group: all(r['hit'] for r in rs) for group, rs in grouped.items()}
        interval = _interval(sum(group_hits.values()), len(group_hits), confidence)
        gate = 'insufficient' if len(grouped) < definition['minimum_groups'] else ('passed' if interval['lower'] >= definition['recall_min'] else 'failed')
        gates.append(gate)
        defects[name] = dict(manifested_cases=len(own), detected_cases=sum(r['hit'] for r in own),
            act_hits=sum(bool(r['matched_act']) for r in own), review_hits=sum(bool(r['matched_review']) for r in own),
            not_manifested_cases=sum(r['defect_id'] == name and not r['manifested'] for r in outcomes),
            group_all_cases_hit=group_hits, group_recall_interval=interval, gate=gate)
    clean = [r for r in outcomes if r['defect_id'] is None]
    grouped = defaultdict(list)
    for row in clean:
        grouped[row['group_id']].append(row)
    failures = {g: any(r['unexpected_act'] or r['operational_error'] for r in rs) for g, rs in grouped.items()}
    interval = _interval(sum(failures.values()), len(failures), confidence)
    clean_gate = 'insufficient' if len(grouped) < protocol['minimum_clean_groups'] else ('passed' if interval['upper'] <= protocol['clean_failure_max'] else 'failed')
    gates.append(clean_gate)
    clean_summary = dict(cases=len(clean), any_act_cases=sum(r['any_act'] for r in clean),
        any_review_cases=sum(r['any_review'] for r in clean), unexpected_act_cases=sum(bool(r['unexpected_act']) for r in clean),
        operational_error_cases=sum(r['operational_error'] for r in clean),
        group_failure_or_unexpected_act=failures, group_failure_interval=interval, gate=clean_gate)
    # A demonstrated failure is not hidden by another gate with too few observations.
    gate = 'failed' if 'failed' in gates else ('insufficient' if 'insufficient' in gates else 'passed')
    verify_plan(plan)
    return _seal('audit_validation_score_v1', plan_digest=plan['digest'], predictions_digest=predictions['digest'],
        labels_sha256=sha(labels_raw), numerical_gate=gate, cohort_kind=protocol['cohort_kind'],
        defects=defects, clean=clean_summary, rows=outcomes,
        operational_error_cases=sum(r['operational_error'] for r in outcomes),
        inference=dict(unit='declared_strategy_family_group', recall_group_success='all_manifested_cases_hit',
            clean_group_failure='any_unexpected_act_or_operational_error', nominal_family_confidence=.95,
            marginal_confidence=confidence, method='Bonferroni_adjusted_two_sided_Clopper_Pearson',
            independence='group_iid_sampling_and_representativeness_assumed_not_authenticated',
            hit_definition='predeclared_card_category_not_causal_confirmation',
            not_manifested_excluded_from_recall_not_counted_as_hits=True),
        independent_custody_authenticated=False, chronology_authenticated=False,
        institutional_readiness='not_established', thresholds_automatically_changed=False)
