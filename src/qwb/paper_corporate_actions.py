"""Declared event coverage for raw-share paper and governance accounts.

Independent of corporate_actions, which infers proposals from imported exports.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date
import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import experiments
from .portfolio.contracts import AccountState
from .portfolio.lifecycle import (BonusShares, Dividend, LifecycleAccountState, Split, apply_phase,
    parse_actions, phases, validate_initial)

FILENAME = 'corporate_actions.json'
PROFILE = 'raw_share_lifecycle_v2'


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('公司行动JSON字段重复')
        result[key] = value
    return result


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('公司行动日期须为YYYY-MM-DD')
    return value


def parse(raw):
    try:
        return _parse(raw)
    except (TypeError, KeyError, AttributeError, OverflowError) as exc:
        raise ValueError('公司行动字段类型或数值无效') from exc


def _parse(raw):
    if not isinstance(raw, bytes) or len(raw) > 8_000_000:
        raise ValueError('公司行动输入须为不超过8MB的UTF-8 JSON')
    try:
        doc = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('公司行动输入不是有效UTF-8 JSON') from exc
    required = {'schema_version', 'version', 'previous_digest', 'source', 'coverage', 'actions', 'factor_changes'}
    if not isinstance(doc, dict) or set(doc) != required or doc['schema_version'] != 'paper_corporate_actions_v1':
        raise ValueError('公司行动输入版本或字段无效')
    if type(doc['version']) is not int or not 1 <= doc['version'] <= 1_000_000:
        raise ValueError('公司行动版本须为正整数')
    previous = doc['previous_digest']
    if previous is not None and (not isinstance(previous, str) or len(previous) != 71
            or not previous.startswith('sha256:') or any(c not in '0123456789abcdef' for c in previous[7:])):
        raise ValueError('公司行动前版摘要无效')
    if (doc['version'] == 1) != (previous is None):
        raise ValueError('初版不得引用前版；后续版本必须引用前版摘要')
    source = doc['source']
    if (not isinstance(source, dict) or set(source) != {'kind', 'reference'}
            or source['kind'] not in ('provider_archive', 'user_declared', 'synthetic_fixture')
            or not isinstance(source['reference'], str) or not 1 <= len(source['reference'].strip()) <= 256):
        raise ValueError('公司行动须声明来源类别与引用，不认证独立来源')
    coverage = doc['coverage']
    if not isinstance(coverage, list) or not 1 <= len(coverage) <= 10000:
        raise ValueError('公司行动须提供证券及日期覆盖')
    seen = set()
    from .strategy import stock_code
    for row in coverage:
        if not isinstance(row, dict) or set(row) != {'code', 'start', 'through'}:
            raise ValueError('公司行动覆盖字段无效')
        stock_code(row['code'])
        if row['code'] in seen or _day(row['start']) > _day(row['through']):
            raise ValueError('公司行动覆盖证券重复或日期颠倒')
        seen.add(row['code'])
    if not isinstance(doc['actions'], list) or len(doc['actions']) > 10000:
        raise ValueError('公司行动事件数量无效')
    actions = parse_actions(doc['actions'])
    economic_keys = set()
    for event in actions:
        effective = event.ex_at if isinstance(event, (Dividend, BonusShares)) else event.effective_at
        economic_key = (event.kind, event.asset_id, pd.Timestamp(effective))
        if economic_key in economic_keys:
            raise ValueError('同证券同类同日公司行动重复或有歧义，须核对公告后合并为唯一事件')
        economic_keys.add(economic_key)
    for at, _, phase, event in phases(actions):
        stock_code(event.asset_id)
        if event.asset_id not in seen or event.kind == 'terminal':
            raise ValueError('纸面主线只支持有覆盖的普通分红与整数拆并股，终止事项须独立处理')
        expected = (15, 0, 0) if phase == 'record' else (9, 0, 0)
        local = at.tz_convert('Asia/Shanghai')
        if (local.hour, local.minute, local.second) != expected or local.microsecond or local.nanosecond:
            raise ValueError('日频公司行动须为09:00事件或15:00登记，不自动移动日期')
    changes = doc['factor_changes']
    if not isinstance(changes, list) or len(changes) > 20000:
        raise ValueError('复权变化关联数量无效')
    by_id = {a.event_id: a for a in actions}
    keys = set()
    for row in changes:
        if not isinstance(row, dict) or set(row) != {'code', 'date', 'before', 'after', 'event_ids'}:
            raise ValueError('复权变化须显式关联原始事件')
        key = (row['code'], _day(row['date']))
        if key in keys or row['code'] not in seen:
            raise ValueError('复权变化关联重复或无覆盖')
        keys.add(key)
        for field in ('before', 'after'):
            if type(row[field]) not in (int, float) or not np.isfinite(row[field]) or row[field] <= 0:
                raise ValueError('复权变化值须为正的有限数值')
        ids = row['event_ids']
        if not isinstance(ids, list) or not ids or any(not isinstance(k, str) for k in ids) or len(set(ids)) != len(ids):
            raise ValueError('复权变化须关联唯一事件ID')
        for ident in ids:
            event = by_id.get(ident)
            effective = (event.ex_at if isinstance(event, (Dividend, BonusShares))
                         else event.effective_at if isinstance(event, Split) else None)
            if event is None or event.asset_id != row['code'] or str(pd.Timestamp(effective).tz_convert('Asia/Shanghai').date()) != row['date']:
                raise ValueError('复权变化关联的证券、生效日期或事件类型不符')
    return doc


def load(snapshot, *, required=False):
    path = Path(snapshot)/FILENAME
    metadata_path = Path(snapshot)/'snapshot.json'
    metadata = experiments.read(metadata_path) if metadata_path.is_file() else {}
    if not path.is_file():
        if required or metadata.get('corporate_actions_digest') is not None:
            raise ValueError('公司行动覆盖文件缺失，停止记账；补齐不可变数据快照后重试')
        return None
    if path.is_symlink() or path.stat().st_size > 8_000_000:
        raise ValueError('公司行动输入路径或大小无效')
    doc = parse(path.read_bytes())
    if metadata.get('corporate_actions_digest') != experiments.object_digest(doc):
        raise ValueError('公司行动文件与行情快照登记摘要不一致，停止记账')
    return doc


def successor(old, new):
    if old == new:
        return
    if new is None or old is None or new['version'] != old['version']+1 or new['previous_digest'] != experiments.object_digest(old):
        raise ValueError('公司行动修订须递增版本并关联前版摘要；新增事件配置请另建数据源')
    before = {r['event_id']: r for r in old['actions']}
    after = {r['event_id']: r for r in new['actions']}
    if any(after.get(key) != row for key, row in before.items()):
        raise ValueError('既有公司行动被修订或删除，请显式建立新数据源及账户版本，不能改写旧账')
    if new['source']['kind'] != old['source']['kind']:
        raise ValueError('公司行动来源类别改变，请建立新数据源')
    coverage = {r['code']: r for r in new['coverage']}
    for row in old['coverage']:
        newer = coverage.get(row['code'])
        if newer is None or newer['start'] > row['start'] or newer['through'] < row['through']:
            raise ValueError('公司行动声明覆盖不能缩减')
    changes = {(r['code'], r['date']): r for r in new['factor_changes']}
    if any(changes.get((r['code'], r['date'])) != r for r in old['factor_changes']):
        raise ValueError('既有复权事件关联改变，请建立新数据源')


def validate_day(doc, panel, day, assets):
    key = str(pd.Timestamp(day).date())
    coverage = {r['code']: r for r in doc['coverage']}
    if any(c not in coverage or not coverage[c]['start'] <= key <= coverage[c]['through'] for c in assets):
        raise ValueError('持仓或候选证券缺少本日公司行动覆盖，停止记账')
    index = panel['close'].index.get_loc(day)
    changes = {r['code']: r for r in doc['factor_changes'] if r['date'] == key}
    effective = {}
    for event in parse_actions(doc['actions']):
        at = event.ex_at if isinstance(event, (Dividend, BonusShares)) else event.effective_at
        if str(pd.Timestamp(at).tz_convert('Asia/Shanghai').date()) == key:
            effective.setdefault(event.asset_id, set()).add(event.event_id)
    for code in assets:
        if index == 0:
            if code in effective:
                raise ValueError('公司行动生效日需要上一交易日原始行情和复权因子')
            continue
        factors = panel['adj_factor'][code].iloc[index-1:index+1].to_numpy()
        if not np.isfinite(factors).all() or (factors <= 0).any():
            raise ValueError('公司行动核验缺少有效复权因子')
        changed = not np.isclose(*factors, rtol=1e-10, atol=0)
        link = changes.get(code)
        if changed or code in effective or link is not None:
            if (link is None or set(link['event_ids']) != effective.get(code, set())
                    or not np.isclose(link['before'], factors[0], rtol=1e-10, atol=0)
                    or not np.isclose(link['after'], factors[1], rtol=1e-10, atol=0)):
                raise ValueError('复权变化与已声明公司行动不一致，停止记账；不能仅凭因子推算事件')


def validate_calendar(doc, calendar, start, through):
    """Daily adapters support phases on declared open sessions only."""
    sessions = {str(pd.Timestamp(d).date()) for d in calendar}
    first, last = str(pd.Timestamp(start).date()), str(pd.Timestamp(through).date())
    actions = parse_actions(doc['actions'])
    for at, _, _, event in phases(actions):
        key = str(at.tz_convert('Asia/Shanghai').date())
        if first <= key <= last and key not in sessions:
            raise ValueError('公司行动阶段不在已声明开市日；日频主线不自动移动到账或生效日期')
        if isinstance(event, BonusShares):
            ex = str(pd.Timestamp(event.ex_at).tz_convert('Asia/Shanghai').date())
            if first <= ex <= last:
                prior = sorted(d for d in sessions if d < ex)
                record = str(pd.Timestamp(event.record_at).tz_convert('Asia/Shanghai').date())
                if not prior or prior[-1] != record:
                    raise ValueError('送转登记须为除权前一开市日；跨交易日权利变动尚不支持')


def initialize(state, doc):
    actions = parse_actions(doc['actions'])
    close = pd.Timestamp(state.observed_at).tz_convert('Asia/Shanghai').normalize()+pd.Timedelta(hours=15)
    held = {p.asset_id for p in state.positions}
    for event in actions:
        if (isinstance(event, Dividend) and event.asset_id in held
                and pd.Timestamp(event.record_at) < close < pd.Timestamp(event.pay_at)):
            raise ValueError('初始持仓可能含既有分红权利，须提供完整期初权益状态，不能推定为零')
        if (isinstance(event, BonusShares) and event.asset_id in held
                and pd.Timestamp(event.record_at) < close < pd.Timestamp(event.sellable_at)):
            raise ValueError('初始持仓可能含送转权益或未上市股份，须提供完整期初权益状态')
    prior = tuple(key for at, key, _, _ in phases(actions) if at < close)
    state = LifecycleAccountState(str(close), state.data_snapshot_id, state.cash,
        tuple(replace(p, mark_time=str(close)) for p in state.positions), state.pending_orders,
        processed_events=prior)
    audits = []
    for at, key, phase, event in phases(actions):
        if at == close:
            state, audit = apply_phase(state, at, key, phase, event)
            audits.append(audit)
    state = replace(state, observed_at=str(close+pd.Timedelta(minutes=30)))
    validate_initial(state, actions)
    return state, audits


def state_from_dict(value):
    if not isinstance(value, dict):
        raise ValueError('账户状态须为JSON对象')
    cls = LifecycleAccountState if value.get('schema_version') == 'account_state_v2' else AccountState
    return cls.from_dict(value)


def budget_weights(state, weights, *, cash_floor=0):
    room = max(0.0, 1-state.receivable_equity/state.equity-cash_floor)
    total = sum(weights.values())
    return {c: w*room/total for c, w in weights.items()} if total > room and total else weights


def decision_digest(doc, at):
    stamp = pd.Timestamp(at)
    known = [asdict(a) for a in parse_actions(doc['actions']) if pd.Timestamp(a.available_at) <= stamp]
    return experiments.object_digest(sorted(known, key=lambda r: r['event_id']))


def validate_history(root, records, doc):
    archived_docs = {}
    for row in records:
        digest = row.get('corporate_actions_snapshot')
        if digest not in archived_docs:
            archived_docs[digest] = archived(root, digest)
        at = pd.Timestamp(row['day']).tz_localize('Asia/Shanghai')+pd.Timedelta(hours=15, minutes=30)
        if (row.get('corporate_actions_digest') != decision_digest(archived_docs[digest], at)
                or row.get('corporate_actions_digest') != decision_digest(doc, at)):
            raise ValueError('已记账决策时可得的公司行动发生修订，须建立新数据源及账户版本')
    if records:
        successor(archived_docs[records[-1]['corporate_actions_snapshot']], doc)


def archive(root, doc):
    digest = experiments.object_digest(doc)
    folder = Path(root)/'ca_inputs'
    if folder.is_symlink():
        raise ValueError('公司行动存档目录不能为符号链接')
    folder.mkdir(exist_ok=True)
    # Bound Windows path length; every read still checks the full SHA-256.
    path = folder/(digest[7:39]+'.json')
    if path.exists():
        if path.is_symlink() or parse(path.read_bytes()) != doc:
            raise ValueError('公司行动存档被修改')
    else:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(doc, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return digest


def archived(root, digest):
    if (not isinstance(digest, str) or len(digest) != 71 or not digest.startswith('sha256:')
            or any(c not in '0123456789abcdef' for c in digest[7:])):
        raise ValueError('公司行动存档摘要无效')
    folder = Path(root)/'ca_inputs'
    path = folder/(digest[7:39]+'.json')
    if folder.is_symlink() or path.is_symlink() or path.stat().st_size > 8_000_000:
        raise ValueError('公司行动存档路径或大小无效')
    doc = parse(path.read_bytes())
    if experiments.object_digest(doc) != digest:
        raise ValueError('公司行动存档摘要不一致')
    return doc
