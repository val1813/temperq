"""Independent period audits, composed through the existing audit_grid engine.

No account concatenation, uploaded-code execution, automatic parameter selection,
or claim that an uploaded historical window is an untouched out-of-sample test.
"""
from __future__ import annotations

from collections import Counter
import uuid

from . import audit_grid, audit_input, audit_verdicts, experiments as ex
from . import strategy_audit as audit, strategy_audit_store as store
from . import strategy_imports as imports

SCHEMA = 'strategy_audit_batch_v1'
MAX_PERIODS = 5
MAX_UPLOAD_FILES = MAX_PERIODS * imports.MAX_FILES + 1
SUPPLEMENTS = ('fills', 'positions', 'snapshots', 'logs', 'scores', 'orders',
               'prices', 'minute_prices', 'limits', 'listings')


async def read_uploads(form):
    """Retain the form's period boundary; legacy `files` belongs to period 1."""
    groups = {i: [] for i in range(1, MAX_PERIODS + 1)}
    shared, total, count = [], 0, 0
    allowed = {'files', 'code', *SUPPLEMENTS, *(f'backtest{i}' for i in groups)}
    for key, upload in form.multi_items():
        if not hasattr(upload, 'read'):
            continue
        if key not in allowed:
            raise ValueError('未知的上传栏目，请刷新页面后重试')
        raw = await upload.read(imports.MAX_FILE_BYTES + 1)
        if not upload.filename and not raw:
            continue
        count += 1
        total += len(raw)
        if count > MAX_UPLOAD_FILES or len(raw) > imports.MAX_FILE_BYTES or total > imports.MAX_TOTAL_BYTES:
            raise ValueError('单文件最多8MB，全部时期合计最多24MB、81个上传文件；每期解压后最多16个文件')
        source = imports.SourceFile(upload.filename, raw)
        if key == 'code':
            if shared or not source.name.lower().endswith(('.py', '.txt')):
                raise ValueError('共享策略代码只接收一个 .py / .txt 文件')
            shared.append(source)
        elif key.startswith('backtest'):
            groups[int(key[-1])].append(source)
        elif key in SUPPLEMENTS:
            groups[1].append(source)
        else:
            groups[1].append(source)
    # Backwards compatible with older clients, without reintroducing the paste UI.
    code = str(form.get('code_text', '')).strip()
    if code:
        if shared:
            raise ValueError('策略代码重复')
        shared.append(imports.SourceFile('pasted_strategy.py', code.encode('utf-8')))
    return groups, shared


def prepare(groups, shared, form):
    name = str(form.get('name', '')).strip() or '未命名策略'
    if len(name) > 80:
        raise ValueError('策略名称最多80个字符')
    active = [i for i, files in groups.items() if files]
    if not active:
        if not shared:
            raise ValueError('请至少上传策略代码或回测数据')
        active = [1]
    prepared, unpacked_bytes = [], 0
    for index in active:
        try:
            files = imports.unpack_archives(groups[index])
            # A ZIP may already carry its own strategy version. Keep that version;
            # only omit an exact duplicate of the separately uploaded common code.
            own_code = []
            for source in files:
                if source.name.lower().endswith(('.py', '.txt')):
                    text, _ = imports.decode_source(source)
                    if not imports.is_platform_log(text):
                        own_code.append(source)
            if not own_code:
                files += shared
            unpacked_bytes += sum(len(f.data) for f in files)
            if unpacked_bytes > imports.MAX_UNPACKED_BYTES:
                raise ValueError('全部时期解压后合计不能超过40MB')
            cash = str(form.get(f'opening_cash_{index}', '')).strip()
            if index == 1 and not cash:
                cash = str(form.get('opening_cash', '')).strip()
            bundle = imports.normalize(files, adapter='auto', name=name, opening_cash=cash or None)
            bundle['metadata'] = {'style': {'value': '', 'source': 'unconfirmed'}}
            experiment = 'fresh_start' if cash else 'unknown'
            prepared.append({'slot': index, 'label': f'回测数据 {index}', 'bundle': bundle,
                             'files': files, 'experiment': experiment})
        except ValueError as exc:
            raise ValueError(f'回测数据 {index}：{exc}') from None
    return name, prepared


def analyze_one(bundle):
    review = audit_input.review(bundle)
    if not review['can_analyze']:
        return None
    prefills = {p['key']: p['value'] for p in review['parameters']}
    settings = {k: float(prefills[k]) for k in ('commission_bp', 'minimum_commission', 'max_weight')}
    settings.update(broker_confirmed=False, thresholds={})
    result = audit.analyze(bundle, settings)
    result['confirmation'] = {'same_run': False, 'settings': settings, 'source': 'single_page_defaults',
                              'scope': 'report_assumptions_only_not_live_rules'}
    result['result_digest'] = ex.object_digest({k: v for k, v in result.items() if k != 'result_digest'})
    return result


def material_result(bundle, error=''):
    """A durable report even when code is absent or analysis cannot complete."""
    from .audit_evidence import assess
    result = dict(schema_version=audit.VERSION, input_digest=ex.object_digest(bundle),
        mode='materials', modules={}, coverage=[], analysis_error=error,
        scope='submitted_material_inventory_not_completed_account_audit')
    result['evidence_assessment'] = assess(bundle, result)
    result['result_digest'] = ex.object_digest(result)
    return result


def run(workdir, name, prepared, reference_slot=None):
    """Prepare every result before appending files; retain failed/partial periods."""
    if reference_slot is None:
        # Coverage only; never select a reference by its return or conclusion.
        reference_slot = max(prepared, key=lambda item: (
            len(audit_input.available_dates(item['bundle'])), -item['slot']))['slot']
    outcomes = []
    for item in prepared:
        error, result = '', None
        try:
            result = analyze_one(item['bundle'])
        except (ValueError, KeyError, TypeError) as exc:
            error = str(exc)
        outcomes.append((item, result, error))
    rows, cells, date_counts = [], [], Counter()
    for item, result, error in outcomes:
        bundle, index = item['bundle'], item['slot']
        identity = store.save(workdir, bundle, item['files'])
        report_id = store.save_report(workdir, identity, result) if result else None
        dates = audit_input.available_dates(bundle)
        date_counts.update(dates)
        run_name = f'period-{index}'
        cell = audit_grid.build_cell({'strategy_id': 'uploaded-strategy', 'strategy_name': name,
            'window_id': run_name, 'run_id': run_name, 'group': 'uploaded-periods',
            'experiment_type': item['experiment'], 'is_reference': index == reference_slot,
            'seen_status': 'historical_already_seen', 'execution_contract_digest': None,
            'market_data_digest': None, 'strategy_state_digest': None},
            result or {'mode': 'materials', 'modules': {}}, bundle)
        # Missing-code periods must not look like duplicates merely because their
        # result has no digest; real duplicates use their parsed material digest.
        cell['input_digest'] = ex.object_digest(bundle)
        cells.append(cell)
        rows.append({'slot': index, 'label': item['label'], 'identity': identity, 'report_id': report_id,
            'input_digest': ex.object_digest(bundle), 'result_digest': result and result['result_digest'],
            'status': 'error' if error else (result['mode'] if result else 'materials'), 'error': error,
            'start': dates[0] if dates else None, 'end': dates[-1] if dates else None,
            'observed_dates': len(dates), 'files': len(bundle['sources']), 'fills': len(bundle['fills']),
            'experiment': item['experiment'], 'comparison_status': cell['status'],
            'comparison_reasons': cell.get('reasons', []),
            'return_reported': cell.get('return_reported'), 'return_recost': cell.get('return_recost'),
            'drawdown_reported': cell.get('drawdown_reported'),
            'baseline': cell.get('opening'), 'sessions': cell.get('sessions'),
            'conclusions': audit_verdicts.conclusions(bundle, result or {})})
    grid = audit_grid._clean(audit_grid.analyze(cells))
    batch = {'schema_version': SCHEMA, 'id': uuid.uuid4().hex, 'name': name, 'created_at': ex.now(),
        'periods': rows, 'reference_slot': reference_slot, 'grid': grid,
        'coverage': {'window_dates': sum(date_counts.values()), 'distinct_dates': len(date_counts),
                     'repeated_dates': sum(n > 1 for n in date_counts.values())},
        'scope': '各时期独立审计，不拼接账户或汇总为连续收益；历史窗口与重叠日期不构成独立样本外证据。',
        'selection_adjustment': {'status': 'not_computed',
            'reason': '缺少完整参数候选收益矩阵、失败试验及搜索次数记录；不计算PBO、DSR、Reality Check或SPA。'}}
    batch['digest'] = ex.object_digest(batch)
    directory = root(workdir, batch['id'])
    directory.mkdir(parents=True, exist_ok=False)
    ex.write(directory / 'batch.json', batch)
    return batch


def root(workdir, identity):
    if not ex.ID_RE.fullmatch(identity):
        raise ValueError('多时期报告编号无效')
    return workdir / 'runs/strategy_audit_batches' / identity


def read(workdir, identity):
    batch = ex.read(root(workdir, identity) / 'batch.json')
    if (batch['id'] != identity or batch['schema_version'] != SCHEMA
            or batch['digest'] != ex.object_digest({k: v for k, v in batch.items() if k != 'digest'})):
        raise ValueError('多时期报告摘要不一致')
    for row in batch['periods']:
        record, _ = store.read(workdir, row['identity'])
        if record['input_digest'] != row['input_digest']:
            raise ValueError('时期材料与报告不一致')
        if row['report_id']:
            result = store.report(workdir, row['identity'], row['report_id'])[2]
            if result['result_digest'] != row['result_digest']:
                raise ValueError('时期审计摘要不一致')
    return batch


def history(workdir):
    rows = []
    for path in (workdir / 'runs/strategy_audit_batches').glob('*/batch.json'):
        try:
            rows.append(read(workdir, path.parent.name))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return rows
