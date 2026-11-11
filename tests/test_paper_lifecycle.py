"""Declared event data reaches both daily account paths without rewriting v1."""
from copy import deepcopy
from dataclasses import asdict
import json

import pandas as pd
import pytest

from qwb import paper_corporate_actions as ca, experiments as ex, paper, paper_verification, strategy_workspace as w
from qwb.portfolio.lifecycle import BonusShares, Dividend, Split
from test_strategy_workspace import setup


def document(codes):
    ex_at = '2026-10-21T09:00:00+08:00'
    return dict(schema_version='paper_corporate_actions_v1', version=1, previous_digest=None,
        source=dict(kind='synthetic_fixture', reference='known-answer synthetic event fixture'),
        coverage=[dict(code=c, start='2026-07-01', through='2026-11-06') for c in codes],
        actions=[asdict(Dividend('div', '000001.SZ', '2026-10-19T12:00:00+08:00',
            '2026-10-20T15:00:00+08:00', ex_at, '2026-10-23T09:00:00+08:00', 1., 0., .5, 'tax_exempt')),
            asdict(Split('split', '000001.SZ', '2026-10-19T12:00:00+08:00', ex_at, 2, 1, 'reject'))],
        factor_changes=[dict(code='000001.SZ', date='2026-10-21', before=1., after=20/9,
            event_ids=['split', 'div'])])


def attach(path, doc):
    metadata = ex.read(path/'snapshot.json') if (path/'snapshot.json').is_file() else {'data_snapshot_id':'fixture'}
    metadata['corporate_actions_digest'] = ex.object_digest(doc)
    ex.write(path/'snapshot.json', metadata)
    ex.write(path/ca.FILENAME, doc)


def prepare(setup, bonus=False):
    root, run, feed, panel = setup
    code = '000001.SZ'
    for field in ('open', 'close', 'high', 'low'):
        panel[field][code] = 10.
        panel[field].loc['2026-10-21':, code] = 4.5
    panel['up_limit'][code] = panel['close'][code]*1.2
    panel['down_limit'][code] = panel['close'][code]*.8
    panel['adj_factor'][code] = 1.
    panel['adj_factor'].loc['2026-10-21':, code] = 20/9
    doc = document(list(panel['close'].columns))
    if bonus:
        doc['actions'][1] = asdict(BonusShares('split', code, '2026-10-19T12:00:00+08:00',
            '2026-10-20T15:00:00+08:00', '2026-10-21T09:00:00+08:00',
            '2026-10-23T09:00:00+08:00', 1, 1, 'reject'))
    attach(feed, doc)
    attach(root/'data', doc)
    return root, run, feed, panel, doc


def start_account(setup, kind, *, bonus=False):
    root, run, feed, panel, doc = prepare(setup, bonus=bonus)
    if kind == 'paper':
        config = paper.initialize(root, 'life', run, 100000, [], replay_start='2026-10-19')
        assert config['schema'] == 'paper_daily_v2'
        tick = lambda day: paper.tick(root, 'life', now=day+'T18:00:00+08:00')
        records = lambda: paper.inspect(root, 'life')['sessions']
        account = paper.location(root, 'life')
    else:
        sid, vid = w.create(root, run, feed, name='lifecycle', cash=100000, preset='direct',
            cost_model=kind, overrides=dict(n_hold=1, hold_until_rank=1, max_weight=1.))
        account, spec = w.revision(root, sid, vid)
        assert spec['accounting_profile'] == ca.PROFILE
        evaluation = w.evaluate(root, sid, vid, start='2026-10-19', end='2026-10-23')
        historical = ex.read(evaluation/'selected.json')
        w.start(root, sid, vid, historical_start='2026-10-19')
        tick = lambda day: w.tick(root, sid, vid, now=day+'T18:00:00+08:00')
        records = lambda: w.inspect(root, sid, vid)['records']
    return root, doc, account, tick, records, (None if kind == 'paper' else historical)


@pytest.mark.parametrize('kind', ['paper', 'commission_only', 'struct_v1'])
def test_dividend_split_payment_and_restart_share_one_event_path(setup, kind):
    root, doc, account, tick, records, historical = start_account(setup, kind)
    tick('2026-10-20')
    before = records()
    entitled = next(p['quantity'] for p in before[-1]['state']['positions'] if p['asset_id']=='000001.SZ')
    assert before[-1]['state']['entitlements'][0]['quantity'] == entitled
    assert before[-1]['state']['entitlements'][0]['recognized'] is False
    tick('2026-10-21')
    after = records()[-1]
    state_before, state_after = map(lambda r: ca.state_from_dict(r['state']), (before[-1], after))
    assert state_after.equity == pytest.approx(state_before.equity)
    assert state_after.cash == state_before.cash
    assert state_after.receivable_equity == entitled
    assert next(p.quantity for p in state_after.positions if p.asset_id=='000001.SZ') == 2*entitled
    tick('2026-10-23')
    final = records()
    paid = ca.state_from_dict(final[-1]['state'])
    assert paid.receivable_equity == 0 and paid.entitlements == ()
    assert paid.cash == pytest.approx(state_after.cash+entitled)
    assert paid.equity == pytest.approx(state_after.equity)
    tick('2026-10-23')
    assert records() == final
    assert all(r['scope']=='historical_replay' for r in final)
    assert ca.archived(account, final[-1]['corporate_actions_snapshot']) == doc
    if historical is not None:
        assert [r['state'] for r in final] == [r['state'] for r in historical['positions']]
        assert [r['path'] for r in final] == historical['path']
        assert [r['corporate_actions'] for r in final] == [r['corporate_actions'] for r in historical['positions']]
    if kind == 'struct_v1':
        fills = [f for r in final for f in r['fills']]
        assert fills and all(f['cost_model']=='struct_v1' and f['struct_spread']>=0 for f in fills)


@pytest.mark.parametrize('kind', ['paper', 'commission_only', 'struct_v1'])
def test_bonus_share_registration_and_delayed_sellability_match_history(setup, kind):
    root, _, _, tick, records, historical = start_account(setup, kind, bonus=True)
    tick('2026-10-20')
    quantity = records()[-1]['state']['positions'][0]['quantity']
    tick('2026-10-21')
    issued = records()[-1]['state']['positions'][0]
    assert issued['quantity'] == 2*quantity and issued['sellable_quantity'] == quantity
    assert issued['unsettled'] == [dict(quantity=quantity, sellable_after='2026-10-23T09:00:00+08:00')]
    tick('2026-10-23')
    final = records()[-1]['state']['positions'][0]
    assert final['quantity'] == final['sellable_quantity'] == 2*quantity
    assert final['unsettled'] == []
    if historical is not None:
        assert [r['state'] for r in records()] == [r['state'] for r in historical['positions']]


@pytest.mark.parametrize('tamper', [None, 'archive', 'digest', 'phase', 'claim'])
def test_v2_readonly_verification_checks_event_archive_and_state_without_writes(setup, tamper):
    root, _, account, tick, records, _ = start_account(setup, 'paper')
    tick('2026-10-23')
    if tamper == 'archive':
        next((account/'ca_inputs').glob('*.json')).unlink()
    elif tamper:
        with paper.connection(account) as con:
            row = records()[2]
            if tamper == 'digest': row['corporate_actions_digest'] = 'changed'
            if tamper == 'phase': row['audit']['corporate_actions'] = []
            if tamper == 'claim': row['state']['entitlements'][0]['amount'] += 1
            con.execute('UPDATE sessions SET payload=? WHERE day=?', (json.dumps(row), row['day']))
    before = {p.relative_to(account):p.read_bytes() for p in account.rglob('*') if p.is_file()}
    from test_paper_verification import calendar
    result = paper_verification.verify(root, 'life', calendar(), through='2026-10-23', source_kind='synthetic_fixture')
    assert result['status'] == ('matched' if tamper is None else 'failed'), result['issues']
    assert result['locally_eligible_forward_return_intervals'] == 0
    assert result['corporate_action_entitlements_verified'] is False
    assert result['corporate_action_state_semantics_verified'] == (tamper is None)
    assert before == {p.relative_to(account):p.read_bytes() for p in account.rglob('*') if p.is_file()}


def test_synthetic_event_source_cannot_be_relabelled_as_actual_forward_evidence(setup):
    root, _, account, tick, _, _ = start_account(setup, 'paper')
    tick('2026-10-23')
    from test_paper_verification import as_forward, calendar
    as_forward(account)
    result = paper_verification.verify(root, 'life', calendar(), through='2026-10-23',
        source_kind='local_paper', minimum_forward_intervals=1)
    assert result['status'] == 'failed'
    assert result['locally_eligible_forward_return_intervals'] == 0
    assert 'synthetic_corporate_actions_relabelled_as_forward' in {i['code'] for i in result['issues']}


@pytest.mark.parametrize('kind', ['paper', 'commission_only', 'struct_v1'])
def test_coverage_gap_pauses_and_extending_same_events_recovers(setup, kind):
    root, doc, account, tick, records, _ = start_account(setup, kind)
    tick('2026-10-20')
    saved = deepcopy(records())
    limited = deepcopy(doc)
    for row in limited['coverage']:
        row['through'] = '2026-10-20'
    for path in (root/'data', root/'data/daily'):
        attach(path, limited)
    with pytest.raises(ValueError, match='公司行动'):
        tick('2026-10-23')
    assert records() == saved
    for path in (root/'data', root/'data/daily'):
        attach(path, doc)
    tick('2026-10-23')
    assert len(records()) == 5
    assert ca.state_from_dict(records()[-1]['state']).receivable_equity == 0


@pytest.mark.parametrize('kind', ['paper', 'commission_only'])
def test_known_event_revision_never_rewrites_saved_prefix(setup, kind):
    root, doc, _, tick, records, _ = start_account(setup, kind)
    tick('2026-10-20')
    saved = deepcopy(records())
    changed = deepcopy(doc)
    changed['actions'][0]['gross_per_share'] = 2.
    changed['actions'][0]['ex_price_adjustment'] = 1.
    for path in (root/'data', root/'data/daily'):
        attach(path, changed)
    with pytest.raises(ValueError, match='修订|重算不一致'):
        tick('2026-10-23')
    assert records() == saved


def test_v1_account_is_not_silently_upgraded_by_later_event_file(setup):
    root, run, feed, panel = setup
    paper.initialize(root, 'legacy', run, 100000, [], replay_start='2026-10-19')
    prepare(setup)
    paper.tick(root, 'legacy', now='2026-10-20T18:00:00+08:00')
    with pytest.raises(ValueError, match='公司行动'):
        paper.tick(root, 'legacy', now='2026-10-23T18:00:00+08:00')
    assert paper.inspect(root, 'legacy')['config']['schema'] == 'paper_daily_v1'


@pytest.mark.parametrize('mutation', ['missing_link', 'wrong_factor', 'wrong_event'])
def test_factor_changes_require_matching_declared_events(setup, mutation):
    root, run, _, _, doc = prepare(setup)
    if mutation == 'missing_link':
        doc['factor_changes'] = []
    elif mutation == 'wrong_factor':
        doc['factor_changes'][0]['after'] = 2.
    else:
        doc['factor_changes'][0]['event_ids'] = ['split']
    attach(root/'data', doc)
    paper.initialize(root, 'bad', run, 100000, [], replay_start='2026-10-19')
    with pytest.raises(ValueError, match='复权变化'):
        paper.tick(root, 'bad', now='2026-10-23T18:00:00+08:00')
    assert paper.inspect(root, 'bad')['sessions'] == []
