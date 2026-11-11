"""Evidence provenance shared by calculations, reports and exported tables.

Consistency of submitted records never authenticates their source. Legacy
results are projected for display without rewriting their stored digest.
"""
from __future__ import annotations

from copy import deepcopy


def assess(bundle, result=None):
    bundle, result = bundle or {}, result or {}
    actions = bundle.get('actions') or []
    inferred = [a for a in actions if a.get('inferred')]
    allocated = [a for a in actions if a.get('allocation_inferred') or a.get('source') == 'receivable_dividend']
    count = max(len(inferred), (bundle.get('quality', {}).get('inferred_actions') or {}).get('count', 0))
    account = result.get('modules', {}).get('accounting') or {}
    balanced = account.get('status') in {'verified', 'provisional'} and account.get('internally_balanced', True)
    state = ('missing' if not account else 'needs_reconciliation' if not balanced else
             'provisional' if count else 'internally_consistent')
    return dict(schema_version='audit_evidence_v1', status=state,
        attribution_status='provisional' if count or allocated else state,
        source_authenticated=False, data_policy='user_supplied_only',
        inferred_count=count, inferred_cash=sum(a.get('cash', 0.) for a in inferred),
        inferred_dates=sorted({a['date'] for a in inferred}), allocated_count=len(allocated),
        adjusted_notional_count=sum(f.get('notional_source') == 'account_cash' for f in bundle.get('fills') or []),
        dependencies=[dict(date=a['date'], code=a.get('code'), cash=a.get('cash', 0.),
            quantity_change=a.get('quantity_change', 0), ref=a.get('ref'),
            reason='account_difference' if a.get('inferred') else 'name_allocation') for a in inferred + allocated],
        meaning='核对用户提交记录的内部一致性；未认证来源，不补充任何行情、基准或事件数据。')


def projection(bundle, result):
    """A current interpretation of a saved result, preserving its archived digest."""
    out = deepcopy(result)
    evidence = assess(bundle, result)
    out['evidence_assessment'] = evidence
    modules = out.get('modules', {})
    modules['evidence'] = evidence
    account = modules.get('accounting') or {}
    if evidence['status'] == 'provisional' and account.get('status') == 'verified':
        account.update(status='provisional', internally_balanced=True)
    attr = modules.get('attribution') or {}
    if evidence['attribution_status'] == 'provisional' and attr.get('status') == 'verified':
        attr['status'] = 'provisional'
    # Coverage must describe this run, rather than a generic module inventory.
    if account and modules.get('governance'):
        from .audit_catalog import coverage
        out['coverage'] = coverage(bundle, out)
    return out


def export_status(result):
    evidence = result.get('evidence_assessment') or {}
    status = evidence.get('attribution_status')
    if status:
        return status
    return (result.get('modules', {}).get('accounting') or {}).get('status', 'not_assessed')
