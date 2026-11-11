"""Read-only checks of an existing frozen paper ledger, never a broker certificate.

Local timestamps and scopes are evidence to check, not authenticated time or
source. No account is opened, ticked, migrated, resumed or modified here.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
from datetime import date, datetime, time, timedelta
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from zoneinfo import ZoneInfo

from . import audit_account_reconciliation as account, experiments, paper, strategy
from . import paper_corporate_actions as corporate_actions
from .portfolio.contracts import AccountState

TZ = ZoneInfo('Asia/Shanghai')
SCOPES = ('forward_paper', 'late_reconstruction', 'historical_replay')


def _timestamp(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None:
            return result.astimezone(TZ)
    except (TypeError, ValueError):
        pass
    raise ValueError('时间戳须为含时区的ISO时间')


def _at(day, hour, minute=0):
    return datetime.combine(date.fromisoformat(day), time(hour, minute), TZ)


def _finite(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _same(left, right):
    return _finite(left) and _finite(right) and math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-8)


def _calendar(raw, start, through):
    rows = account._table(raw, ('date', 'is_open'), 'calendar')
    seen, sessions = set(), []
    for row in rows:
        day = account._day(row['date'])
        if row['date'] in seen or row['is_open'] not in ('0', '1'):
            raise ValueError('交易日历重复或开市标记无效')
        seen.add(row['date'])
        if row['is_open'] == '1':
            sessions.append(day.isoformat())
    expected = {(start+timedelta(days=i)).isoformat() for i in range(max(0, (through-start).days+1))}
    if not expected <= seen:
        raise ValueError('核验日历须覆盖启用日至核验日之间每个自然日')
    return sorted(sessions)


def _read(root):
    config_path, ledger = root/'strategy.json', root/'ledger.sqlite3'
    if not config_path.is_file() or not ledger.is_file() or config_path.stat().st_size > 8_000_000:
        raise ValueError('须为已有的规范纸面账户，核验不会初始化或修复账户')
    config_raw = config_path.read_bytes()
    try:
        config = json.loads(config_raw.decode('utf-8'), object_pairs_hook=account._unique_json)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('账户配置不是有效UTF-8 JSON') from exc
    # mode=ro prevents an absent file creating a new ledger. The read transaction
    # pins one SQLite view while another process may be recording a new session.
    try:
        with closing(sqlite3.connect(ledger.resolve().as_uri()+'?mode=ro', uri=True, timeout=10)) as con:
            con.execute('PRAGMA query_only=ON')
            con.execute('BEGIN')
            metadata = dict(con.execute('SELECT key,value FROM metadata'))
            raw_rows = con.execute('SELECT day,payload FROM sessions ORDER BY day LIMIT 10001').fetchall()
    except sqlite3.Error as exc:
        raise ValueError('无法只读取得规范账本快照') from exc
    if any(not isinstance(payload, str) for _, payload in raw_rows):
        raise ValueError('账本记录须为JSON文本')
    if len(raw_rows) > 10000 or sum(len(payload) for _, payload in raw_rows) > 128_000_000:
        raise ValueError('账本超过10000条或128MB核验上限')
    return config, metadata, raw_rows, hashlib.sha256(config_raw).hexdigest()


def verify(workdir, name, calendar_raw, *, through=None, minimum_forward_intervals=60,
           source_kind='local_paper', now=None):
    if type(minimum_forward_intervals) is not int or not 1 <= minimum_forward_intervals <= 3660:
        raise ValueError('最少前向收益区间数须为1至3660')
    if source_kind not in ('local_paper', 'synthetic_fixture'):
        raise ValueError('纸面来源只能声明local_paper或synthetic_fixture，不认证券商来源')
    stamp = datetime.now(TZ) if now is None else _timestamp(now)
    completed = stamp.date()-timedelta(days=int(stamp.timetz().replace(tzinfo=None) < time(15, 30)))
    end = completed if through is None else account._day(through)
    if end > completed:
        raise ValueError('不能核验尚未完成的日期')
    root = paper.location(workdir, name)
    config, metadata, raw_rows, config_sha = _read(root)
    if not isinstance(config, dict) or config.get('schema') not in ('paper_daily_v1', 'paper_daily_v2'):
        raise ValueError('不支持的纸面账户配置版本')
    lifecycle = config.get('schema') == 'paper_daily_v2'
    start = account._day(config.get('start_date'))
    if (end-start).days > 3660:
        raise ValueError('核验期间不能超过3661个自然日')
    sessions = _calendar(calendar_raw, start, end)
    expected = [d for d in sessions if start.isoformat() <= d <= end.isoformat()]
    issues, warnings, rows = [], [], []

    def issue(code, day=None):
        issues.append(dict(code=code, day=day))

    if metadata.get('config_hash') != experiments.object_digest(config):
        issue('frozen_configuration_digest_mismatch')
    if config.get('scope') not in ('forward_paper', 'historical_replay'):
        issue('unknown_account_scope')
    data = config.get('data')
    if not isinstance(data, dict):
        issue('invalid_data_source_declaration')
    if config.get('scope') == 'forward_paper' and isinstance(data, dict) and data.get('source') == 'synthetic':
        issue('synthetic_model_relabelled_as_forward')
    if config.get('scope') == 'forward_paper' and config.get('corporate_action_source_kind') == 'synthetic_fixture':
        issue('synthetic_corporate_actions_relabelled_as_forward')
    if config.get('implementation_hash') != paper.implementation_hash():
        issue('frozen_execution_implementation_changed')
    try:
        activated = _timestamp(config.get('activated_at'))
        if activated > stamp:
            issue('activation_after_verification_time')
    except ValueError:
        activated = None
        issue('invalid_activation_timestamp')
    try:
        if strategy.model_identity(root/'final_model') != config.get('model_hash'):
            issue('frozen_model_digest_mismatch')
        meta = experiments.read(root/'final_model/meta.json')
        _, recipe_hash = strategy.locked_recipe(meta)
        if recipe_hash != config.get('protocol_hash') or experiments.object_digest(config.get('recipe')) != config.get('protocol_hash'):
            issue('frozen_protocol_digest_mismatch')
    except (ValueError, KeyError, TypeError, OSError):
        issue('frozen_model_or_protocol_unreadable')
    parsed = {}
    action_documents = {}
    action_archive_verified = False
    if lifecycle:
        if config.get('corporate_action_source_kind') not in ('provider_archive', 'user_declared', 'synthetic_fixture'):
            issue('invalid_corporate_action_source_declaration')
        try:
            prior_document = None
            for _, payload in raw_rows:
                row = json.loads(payload, object_pairs_hook=account._unique_json)
                if row.get('day', '') > end.isoformat():
                    continue
                digest = row.get('corporate_actions_snapshot')
                if not isinstance(digest, str):
                    raise ValueError('missing corporate action archive reference')
                if digest not in action_documents:
                    action_documents[digest] = corporate_actions.archived(root, digest)
                doc = action_documents[digest]
                corporate_actions.validate_calendar(doc, sessions, start, end)
                if prior_document is not None:
                    corporate_actions.successor(prior_document, doc)
                prior_document = doc
                if doc['source']['kind'] != config.get('corporate_action_source_kind'):
                    raise ValueError('corporate action source kind changed')
            action_archive_verified = bool(action_documents)
        except (ValueError, TypeError, AttributeError, KeyError, OSError):
            issue('corporate_action_archive_unreadable')
    global_freeze_valid = not issues
    scopes = Counter()
    for sql_day, payload in raw_rows:
        try:
            row = json.loads(payload, object_pairs_hook=account._unique_json)
            if not isinstance(row, dict) or row.get('day') != sql_day:
                raise ValueError('SQL day differs from payload')
            account._day(sql_day)
        except (ValueError, TypeError):
            issue('invalid_session_payload_or_day')
            continue
        parsed[sql_day] = row
        if sql_day <= end.isoformat():
            scope = row.get('scope')
            scopes[scope if scope in SCOPES else 'invalid'] += 1
    missing = [d for d in expected if d not in parsed]
    for day in missing:
        issue('missing_expected_session', day)
    previous = None
    eligible_returns = 0
    for day, row in sorted(parsed.items()):
        if day > end.isoformat():
            continue
        before = len(issues)
        if day not in expected:
            issue('session_outside_declared_open_calendar', day)
        try:
            recorded = _timestamp(row.get('recorded_at'))
            if recorded > stamp or activated is None or recorded < activated:
                issue('recorded_before_activation_or_after_verification', day)
        except ValueError:
            recorded = None
            issue('invalid_recorded_timestamp', day)
        if row.get('scope') not in SCOPES:
            issue('unknown_session_scope', day)
        if config.get('scope') == 'historical_replay' and row.get('scope') != 'historical_replay':
            issue('historical_account_relabelled_as_forward', day)
        for key in ('model_hash', 'protocol_hash'):
            if row.get(key) != config.get(key):
                issue('session_'+key+'_mismatch', day)
        try:
            state = (corporate_actions.state_from_dict(row.get('state')) if lifecycle
                     else AccountState.from_dict(row.get('state')))
            if not _same(row.get('equity'), state.equity):
                issue('state_equity_mismatch', day)
            if _timestamp(state.observed_at) != _at(day, 15, 30):
                issue('state_observed_time_mismatch', day)
            if row.get('input_snapshot_id') != state.data_snapshot_id:
                issue('session_snapshot_id_mismatch', day)
            if lifecycle:
                from .portfolio.lifecycle import parse_actions, phases, validate_initial
                if state.schema_version != 'account_state_v2':
                    issue('corporate_action_state_schema_mismatch', day)
                action_doc = action_documents.get(row.get('corporate_actions_snapshot'))
                if not isinstance(row.get('corporate_actions_digest'), str):
                    issue('missing_corporate_action_decision_digest', day)
                elif action_doc is not None:
                    expected_digest = corporate_actions.decision_digest(action_doc, _at(day, 15, 30))
                    if row.get('corporate_actions_digest') != expected_digest:
                        issue('corporate_action_decision_digest_mismatch', day)
                audit_rows = (row.get('initial_corporate_actions') if previous is None else
                              row.get('audit', {}).get('corporate_actions') if isinstance(row.get('audit'), dict) else None)
                if not isinstance(audit_rows, list) or any(not isinstance(item, dict) for item in audit_rows):
                    issue('invalid_corporate_action_phase_audit', day)
                elif action_doc is not None:
                    actions = parse_actions(action_doc['actions'])
                    try:
                        validate_initial(state, actions)
                    except ValueError:
                        issue('corporate_action_entitlement_or_processed_phase_mismatch', day)
                    lo = _at(day, 15) if previous is None else _at(previous[0], 15, 30)
                    scheduled = [(key, phase, at, event.asset_id) for at, key, phase, event in phases(actions)
                                 if (at == lo if previous is None else lo < at <= _at(day, 15, 30))]
                    if [(item.get('phase_id'), item.get('phase'), _timestamp(item.get('at')), item.get('asset_id'))
                            for item in audit_rows] != scheduled:
                        issue('corporate_action_phase_schedule_mismatch', day)
                    if any(not _same(item.get('accounting_residual'), 0) or not _same(
                            item.get('equity_after'), item.get('equity_before', float('nan'))+item.get('corporate_pnl', float('nan')))
                           for item in audit_rows):
                        issue('corporate_action_phase_accounting_mismatch', day)
        except (ValueError, TypeError, KeyError, OverflowError):
            state = None
            issue('invalid_account_state', day)
        weights = row.get('next_weights')
        if weights is not None:
            if (not isinstance(weights, dict) or
                any(not isinstance(k, str) or not _finite(v) or not 0 <= v <= 1 for k, v in weights.items()) or
                sum(weights.values()) > 1+1e-6):
                issue('invalid_decision_weights', day)
            next_sessions = [d for d in sessions if d > day]
            if not next_sessions or row.get('next_execution_day') != next_sessions[0]:
                issue('decision_execution_day_not_next_session', day)
        elif row.get('next_execution_day') is not None:
            issue('execution_day_without_decision', day)
        if previous is None:
            if row.get('daily_return') is not None or row.get('audit') is not None:
                issue('initial_snapshot_has_return_or_execution', day)
            if state is not None:
                if not _same(state.cash, config.get('cash')):
                    issue('initial_cash_differs_from_declared_account', day)
                holdings = config.get('holdings')
                try:
                    initial = {p['code']: p['quantity'] for p in holdings}
                    actual = {p.asset_id: p.quantity for p in state.positions}
                    if initial != actual:
                        issue('initial_inventory_differs_from_declared_account', day)
                except (TypeError, KeyError):
                    issue('invalid_declared_initial_holdings', day)
        else:
            prev_day, prev_row, prev_eligible = previous
            prev_equity = prev_row.get('equity')
            if not (_finite(prev_equity) and prev_equity > 0 and _finite(row.get('equity')) and
                    _same(row.get('daily_return'), row['equity']/prev_equity-1)):
                issue('daily_return_does_not_match_adjacent_equity', day)
        eligible = (global_freeze_valid and len(issues) == before and row.get('scope') == 'forward_paper' and
                    config.get('scope') == 'forward_paper' and source_kind == 'local_paper')
        if row.get('scope') == 'forward_paper':
            if recorded is None or not _at(day, 15, 30) <= recorded < _at(day, 0)+timedelta(days=1):
                issue('forward_record_not_saved_on_session_after_close', day)
                eligible = False
            if previous is not None:
                prev_day, prev_row, prev_eligible = previous
                if prev_row.get('next_weights') is not None:
                    try:
                        if (_timestamp(prev_row.get('recorded_at')) >= _at(day, 9, 30) or
                                prev_row.get('next_execution_day') != day or
                                prev_row.get('decision_recorded_before_execution') is not True):
                            issue('forward_decision_not_recorded_before_execution', day)
                            eligible = False
                    except ValueError:
                        issue('forward_decision_timestamp_invalid', day)
                        eligible = False
        interval = False
        if previous is not None and eligible and previous[2]:
            preceding = [d for d in sessions if d < day]
            interval = bool(preceding and preceding[-1] == previous[0])
            eligible_returns += int(interval)
        rows.append(dict(day=day, scope=row.get('scope') if row.get('scope') in SCOPES else 'invalid', locally_eligible_forward=eligible,
                         locally_eligible_forward_return_interval=interval, issue_count=len(issues)-before))
        previous = (day, row, eligible)
    if source_kind == 'synthetic_fixture':
        warnings.append('synthetic_fixture_never_counts_as_forward_observation')
    if config.get('scope') == 'historical_replay':
        warnings.append('historical_replay_never_counts_as_forward_observation')
    status = 'failed' if issues else ('matched' if expected else 'insufficient')
    coverage = 'failed' if issues else ('passed' if eligible_returns >= minimum_forward_intervals else 'insufficient')
    result = dict(schema_version='paper_readonly_verification_v1', created_at=stamp.isoformat(),
        evaluation_clock='local_system' if now is None else 'caller_supplied_simulation',
        through=end.isoformat(), source_kind=source_kind, status=status,
        scope='local_frozen_paper_timing_calendar_and_state_consistency_not_independent_reconciliation',
        config_sha256=config_sha, config_digest=experiments.object_digest(config),
        calendar_sha256=hashlib.sha256(calendar_raw).hexdigest(),
        calculator_sha256={str(p.relative_to(Path(__file__).parent)).replace('\\', '/'):
            hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__), Path(paper.__file__),
            Path(strategy.__file__), Path(account.__file__), Path(corporate_actions.__file__),
            Path(__file__).parent/'portfolio/lifecycle.py', Path(__file__).parent/'portfolio/contracts.py']},
        ledger_snapshot_digest=experiments.object_digest(dict(metadata=metadata, sessions=raw_rows)),
        expected_sessions=len(expected), observed_sessions=len(rows), missing_sessions=missing,
        scope_counts=dict(scopes), locally_eligible_forward_sessions=sum(r['locally_eligible_forward'] for r in rows),
        locally_eligible_forward_return_intervals=eligible_returns,
        minimum_forward_intervals=minimum_forward_intervals, local_forward_coverage_gate=coverage,
        issues=issues[:1000], issue_count=len(issues), issues_truncated=len(issues)>1000, warnings=warnings, rows=rows,
        account_records_written=False, account_activated_or_ticked=False,
        source_authenticated=False, clock_authenticated=False, forward_evidence_independently_authenticated=False,
        independent_execution_reconciliation=False,
        corporate_actions_present=lifecycle,
        corporate_action_archive_verified=action_archive_verified,
        corporate_action_phase_audits_verified=bool(lifecycle and action_archive_verified and not issues),
        corporate_action_entitlements_verified=False,
        corporate_action_state_semantics_verified=bool(lifecycle and action_archive_verified and not issues),
        institutional_readiness='not_established')
    result['report_digest'] = experiments.object_digest(result)
    return result
