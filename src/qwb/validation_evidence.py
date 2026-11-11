"""Append-only account/factor/risk inputs bound to an imported material version.

The link is context, never proof of account/strategy ownership or institutional
approval. Inputs are separately provided; no backtest is turned into real books.
ZIP members are read in memory under exact schemas, never extracted/executed.
"""
from __future__ import annotations

from decimal import DecimalException
from hashlib import sha256
import io
import json
import uuid
import zipfile
import zlib

from . import audit_account_reconciliation as account
from . import audit_factor_attribution as factor
from . import audit_reconciliation as executions
from . import audit_risk_model as risk
from . import experiments as ex, strategy_audit_store as strategies

SCHEMA = 'validation_evidence_v1'
MAX_BYTES = 8_000_000
MAX_TOTAL_BYTES = 24_000_000
MAX_EXPANDED_BYTES = 32_000_000
ROLES = {'account': {'internal': '.zip', 'external': '.zip', 'calendar': '.csv'},
         'factor': {'scope': '.json', 'periods': '.csv', 'weights': '.csv', 'exposures': '.csv', 'factor_returns': '.csv'},
         'risk': {name: '.json' if name == 'scope' else '.csv' for name in risk.ROLES}}
SOURCES = {'account': executions.SOURCE_KINDS, 'factor': factor.SOURCE_KINDS, 'risk': risk.SOURCE_KINDS}


def calculator_digest(mode):
    from pathlib import Path
    if mode not in ROLES:
        raise ValueError('证据类型无效')
    paths = {'account': [__file__, account.__file__, executions.__file__],
             'factor': [__file__, factor.__file__],
             'risk': [__file__, risk.__file__, factor.__file__, account.__file__]}[mode]
    result = {Path(p).name: sha256(Path(p).read_bytes()).hexdigest() for p in paths}
    if mode == 'risk':
        environment = dict(numpy=risk.np.__version__, scipy=risk.scipy.__version__,
                           python=risk.platform.python_version(), machine=risk.platform.machine())
        result['numerical_environment'] = sha256(json.dumps(environment,sort_keys=True).encode()).hexdigest()
    return result


def account_zip(raw):
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)) or set(names) != set(account.FILES):
                raise ValueError('账户ZIP根目录必须且只能包含scope.json、balances.csv、positions.csv、fills.csv、events.csv，不允许子目录或重复文件')
            if (sum(x.file_size for x in infos) > MAX_EXPANDED_BYTES or
                    any(x.file_size > MAX_BYTES or x.flag_bits & 1 or x.is_dir() or
                        x.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED) for x in infos)):
                raise ValueError('账户ZIP大小、压缩方式或加密状态不支持；展开最多32MB，单文件最多8MB')
            result = {}
            for info in infos:
                with archive.open(info) as stream:
                    content = stream.read(MAX_BYTES + 1)
                if len(content) != info.file_size or len(content) > MAX_BYTES:
                    raise ValueError('账户ZIP成员大小不一致或超限')
                result[info.filename] = content
            return result
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, OSError, EOFError, zlib.error) as exc:
        raise ValueError('账户材料须为有效、未加密ZIP，不执行或解压其中代码') from exc


def calculate(mode, inputs, source_kind):
    if mode not in ROLES or not isinstance(inputs, dict) or set(inputs) != set(ROLES[mode]):
        raise ValueError('证据类型或输入文件角色不完整')
    if source_kind not in SOURCES[mode]:
        raise ValueError('须主动选择与材料一致的来源类型')
    if any(not isinstance(raw, bytes) or not raw or len(raw) > MAX_BYTES for raw in inputs.values()):
        raise ValueError('各输入须为非空文件且不超过8MB')
    if sum(len(raw) for raw in inputs.values()) > MAX_TOTAL_BYTES:
        raise ValueError('全部输入合计不能超过24MB')
    try:
        if mode == 'account':
            return account.reconcile(account_zip(inputs['internal']), account_zip(inputs['external']), inputs['calendar'], source_kind=source_kind)
        if mode == 'risk':
            return risk.analyze(*(inputs[k] for k in risk.ROLES), source_kind=source_kind)
        return factor.analyze(*(inputs[k] for k in ROLES['factor']), source_kind=source_kind)
    except DecimalException:
        raise ValueError('数字超出当前计算范围，请核对输入单位及精度') from None


def root(workdir, strategy_id, evidence_id=None):
    directory = strategies.root(workdir, strategy_id) / 'validation_evidence'
    if evidence_id is not None:
        if not ex.ID_RE.fullmatch(evidence_id):
            raise ValueError('证据编号无效')
        directory /= evidence_id
    return directory


def save(workdir, strategy_id, *, input_digest, mode, inputs, source_kind, note=''):
    record, _ = strategies.read(workdir, strategy_id)
    if input_digest != record['input_digest']:
        raise ValueError('策略材料版本已变化，请重新打开导入页')
    if not isinstance(note, str) or len(note) > 80:
        raise ValueError('备注最多80字，请勿填写密钥')
    calculation_version = calculator_digest(mode)
    result = calculate(mode, inputs, source_kind)
    evidence_id = uuid.uuid4().hex
    report = dict(schema_version=SCHEMA, id=evidence_id, strategy_id=strategy_id,
        strategy_input_digest=input_digest, created_at=ex.now(), mode=mode, source_kind=source_kind,
        note=note.strip(), calculator_sha256=calculation_version, institutional_readiness='not_established',
        declaration='用户声明输入属于明确的同一范围与口径；来源、完整性和账户/策略关系未认证。此关联只绑定策略材料上下文，不写账户或启用规则。',
        inputs={role: dict(stored=role+suffix, bytes=len(inputs[role]), sha256=sha256(inputs[role]).hexdigest())
                for role, suffix in ROLES[mode].items()}, result=result)
    if calculator_digest(mode) != calculation_version:
        raise ValueError('计算代码在运行期间变化，请重新验证')
    report['report_digest'] = ex.object_digest(report)
    directory = root(workdir, strategy_id, evidence_id)
    directory.mkdir(parents=True, exist_ok=False)
    for role, info in report['inputs'].items():
        with (directory / info['stored']).open('xb') as stream:
            stream.write(inputs[role])
    with (directory / 'report.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    return evidence_id


def read(workdir, strategy_id, evidence_id):
    record, _ = strategies.read(workdir, strategy_id)
    directory = root(workdir, strategy_id, evidence_id)
    report = ex.read(directory / 'report.json')
    if (report['schema_version'] != SCHEMA or report['strategy_id'] != strategy_id or report['id'] != evidence_id or
            report['strategy_input_digest'] != record['input_digest'] or
            report['report_digest'] != ex.object_digest({k: v for k, v in report.items() if k != 'report_digest'}) or
            report['institutional_readiness'] != 'not_established'):
        raise ValueError('报告摘要、策略材料版本或准入标识不一致')
    mode = report['mode']
    if mode not in ROLES or set(report['inputs']) != set(ROLES[mode]):
        raise ValueError('证据材料角色无效')
    if report['calculator_sha256'] != calculator_digest(mode):
        raise ValueError('计算代码版本已变化，须重新验证；原证据保留')
    inputs = {}
    for role, suffix in ROLES[mode].items():
        item = report['inputs'][role]
        if item['stored'] != role+suffix:
            raise ValueError('证据文件名无效')
        path = directory / item['stored']
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('证据输入超出大小限制')
        raw = path.read_bytes()
        if len(raw) != item['bytes'] or sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('原始输入摘要不一致')
        inputs[role] = raw
    if calculate(mode, inputs, report['source_kind']) != report['result']:
        raise ValueError('原始输入重新计算结果与报告不一致')
    return report


def history(workdir, strategy_id):
    summaries = []
    for directory in root(workdir, strategy_id).glob('*'):
        if not directory.is_dir() or not ex.ID_RE.fullmatch(directory.name):
            continue
        try:
            report = read(workdir, strategy_id, directory.name)
            summaries.append({k: report[k] for k in ('id', 'mode', 'created_at', 'source_kind')}
                             | dict(status=report['result']['status']))
        except (OSError, ValueError, TypeError, KeyError):
            summaries.append(dict(id=directory.name, mode='invalid', created_at='', source_kind='unknown', status='requires_revalidation'))
    return sorted(summaries, key=lambda r: (r['created_at'], r['id']), reverse=True)
