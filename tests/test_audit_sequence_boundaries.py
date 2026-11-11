"""Known-answer regressions for audit accuracy, never strategy optimization."""
from copy import deepcopy

import pytest

from qwb import audit_actions, audit_sequence

DAY = '2024-01-03'


def fill(time, side, price, code='510300.SH', **extra):
    return {'date': DAY, 'time': time, 'side': side, 'quantity': 100,
            'price': price, 'fee': 5., 'code': code, **extra}


def account(cash, fills, **extra):
    return {'snapshots': [{'date': '2024-01-02', 'cash': cash}], 'fills': fills, **extra}


def test_earlier_sale_cash_must_prevent_false_same_minute_dependency():
    bundle = account(100., [fill('09:30', 'sell', 10), fill('09:31', 'sell', 10), fill('09:31', 'buy', 9, '510180.SH')])
    out = audit_sequence.funding(bundle)
    assert out['checked_switches'] == 1 and out['dependent'] == 0


def test_earlier_buy_and_fees_must_not_hide_later_funding_dependency():
    bundle = account(1000., [fill('09:30', 'buy', 8), fill('09:31', 'sell', 5), fill('09:31', 'buy', 6, '510180.SH')])
    out = audit_sequence.funding(bundle)
    assert out['dependent'] == 1
    assert out['rows'][0]['cash_before'] == 195.
    assert out['rows'][0]['from_sale'] == 410.
    shuffled = deepcopy(bundle)
    shuffled['fills'].reverse()
    assert audit_sequence.funding(shuffled) == out


def test_all_earlier_groups_include_both_buy_and_sale_fees():
    bundle = account(100., [fill('09:31', 'sell', 10), fill('09:31', 'buy', 9, '510180.SH'),
                             fill('10:00', 'sell', 10), fill('10:00', 'buy', 11, '510180.SH')])
    out = audit_sequence.funding(bundle)
    assert [r['cash_before'] for r in out['rows']] == [100., 190.]
    assert out['rows'][1]['from_sale'] == 915.


@pytest.mark.parametrize('change', ['untimed', 'cash_action', 'missing_opening'])
def test_unknown_intraday_cash_does_not_receive_clean_funding_verdict(change):
    bundle = account(100., [fill('09:31', 'sell', 10), fill('09:31', 'buy', 9, '510180.SH')])
    if change == 'untimed':
        bundle['fills'].append(fill('', 'buy', 1))
    elif change == 'cash_action':
        bundle['actions'] = [{'date': DAY, 'cash': 100., 'kind': 'cash_dividend'}]
    else:
        bundle['snapshots'] = [{'date': DAY, 'cash': 0.}]
    out = audit_sequence.funding(bundle)
    assert out['dependent'] == 0 and out['coverage_status'] == 'incomplete'
    assert out['unchecked_switches'] == 1
    assert '未覆盖部分不作无依赖判断' in out['verdict']
    card = audit_actions._sequence_card({'funding': out})
    assert card['state'] == 'review' and card['evidence'] == 'needs_data'


def test_unrelated_partial_sell_order_cannot_escalate_funding_warning():
    bundle = account(100., [fill('09:31', 'sell', 10, order_id='filled'),
                             fill('09:31', 'buy', 9, '510180.SH')],
                     orders=[{'date': DAY, 'time': '09:31', 'code': '510300.SH', 'side': 'sell',
                              'order_id': 'unrelated', 'quantity': 1000, 'filled': 100}])
    assert audit_sequence.funding(bundle)['partial_sell_days'] == 0
    bundle['orders'][0]['order_id'] = 'filled'
    assert audit_sequence.funding(bundle)['partial_sell_days'] == 1


@pytest.mark.parametrize(('available', 'late'), [
    ('2024-01-02 16:00:00', 0), ('2024-01-04 09:00:00', 1),
    ('2024-01-03T01:30:00Z', 0), ('2024-01-03T02:00:00+00:00', 1),
    ('09:35', 1), ('9:35:00', 1), ('09:30', 0),
])
def test_signal_comparison_preserves_date_timezone_and_local_clocks(available, late):
    bundle = {'scores': [{'date': DAY, 'code': '510300.SH', 'selected': True, 'available_at': available}],
              'fills': [fill('09:31', 'buy', 10)]}
    out = audit_sequence.signal_after_fill(bundle)
    assert out['checked'] == 1 and out['late'] == late and out['unmatched_buys'] == 0


@pytest.mark.parametrize('values', [['09:00', '10:00'], ['25:99'], [''], ['09:00', 'invalid']])
def test_conflicting_or_missing_signal_times_do_not_pass_or_depend_on_row_order(values):
    scores = [{'date': DAY, 'code': '510300.SH', 'selected': True, 'available_at': x} for x in values]
    bundle = {'scores': scores, 'fills': [fill('09:31', 'buy', 10)]}
    out = audit_sequence.signal_after_fill(bundle)
    assert out['checked'] == out['late'] == 0 and out['unmatched_buys'] == 1
    assert out['coverage_status'] == 'incomplete'
    scores.reverse()
    assert audit_sequence.signal_after_fill(bundle) == out


def test_code_hint_alone_must_not_claim_sale_funding_problem():
    card = audit_actions._sequence_card({'code_hints': {'hints': [{'line': 1}], 'verdict': '需核日线'}})
    assert card['state'] == 'review' and '日线' in card['headline']
    assert '依赖' not in card['headline']
