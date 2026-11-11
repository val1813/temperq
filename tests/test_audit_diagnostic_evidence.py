"""Audit findings must expose their proof and must not hide missing/tail data."""
from datetime import date, timedelta

from qwb import audit_actions, audit_capacity, audit_exports
from qwb import strategy_imports as imports


def test_participation_p95_cannot_hide_one_extreme_window():
    bundle = {'fills': [], 'minute_prices': []}
    for i in range(100):
        day = (date(2024, 1, 1) + timedelta(days=i)).isoformat()
        bundle['fills'].append({'date': day, 'code': '510300.SH', 'time': '10:00',
                                'quantity': 750000 if i == 0 else 1000, 'side': 'buy'})
        bundle['minute_prices'].extend({'date': day, 'code': '510300.SH', 'time': f'09:{m:02d}', 'volume': 100000}
                                       for m in [58, 59])
        bundle['minute_prices'].extend({'date': day, 'code': '510300.SH', 'time': f'10:{m:02d}', 'volume': 100000}
                                       for m in [0, 1, 2])
    result = audit_capacity.analyze(bundle, {'status': 'verified'})
    assert result['p95'] == .002 and result['max_participation'] == 1.5
    assert result['exceeding_windows'] == 1 and result['status'] == 'spikes'
    card = audit_actions._capacity_card(result)
    assert card['state'] == 'review' and card['evidence'] == 'scenario'
    assert '不能据分位数写成全部正常' in card['finding']


def test_capacity_missing_and_conflicting_rows_identify_exact_required_material():
    bundle = {'fills': [{'date': '2024-01-02', 'code': '510300.SH', 'time': '09:31', 'quantity': 100, 'side': 'buy'}],
              'minute_prices': [{'date': '2024-01-02', 'code': '510300.SH', 'time': f'09:{m}', 'volume': 1000}
                                for m in range(31, 35)]}
    result = audit_capacity.analyze(bundle, {'status': 'verified'})
    assert result['uncovered'][0]['missing_minutes'] == ['09:35']
    assert result['uncovered'][0]['date'] == '2024-01-02'
    bundle['minute_prices'].append({**bundle['minute_prices'][0], 'volume': 5000})
    result = audit_capacity.analyze(bundle, {'status': 'verified'})
    assert result['uncovered'][0]['conflicting_minutes'] == ['09:31']
    table = audit_exports.table({'modules': {'capacity': result}}, 'capacity-missing')
    assert table[0] == [dict(row, evidence_status='not_assessed', source_authenticated=False)
                        for row in result['uncovered']]
    assert {'missing_minutes', 'evidence_status', 'source_authenticated'} <= set(table[1])
    assert 'evidence_status' not in result['uncovered'][0]  # exporting preserves the calculation


def test_sequence_exports_include_measurement_inputs_and_unchecked_records():
    row = {'date': '2024-01-02', 'time': '09:31', 'cash_before': 100., 'buy': 200.,
           'from_sale': 100., 'sold': 150., 'net_sale_proceeds': 145., 'sell_partial': False}
    modules = {'sequence': {'funding': {'rows': [row], 'unchecked': [{'date': '2024-01-03', 'time': '09:31', 'reason': '缺时间'}]},
                             'signal_after_fill': {'rows': [], 'unchecked': [{'date': '2024-01-04', 'code': '510300.SH', 'reason': '缺信号可用时刻'}]}}}
    rows, columns = audit_exports.table({'modules': modules}, 'funding-events')
    assert rows == [dict(row, evidence_status='not_assessed', source_authenticated=False)]
    assert {'cash_before', 'buy', 'net_sale_proceeds', 'evidence_status', 'source_authenticated'} <= set(columns)
    assert 'evidence_status' not in row
    missing, _ = audit_exports.table({'modules': modules}, 'sequence-unchecked')
    assert {r['check'] for r in missing} == {'funding', 'signal_after_fill'}
    from qwb.strategy_audit_ui import diagnostic_evidence_links
    links = diagnostic_evidence_links({'modules': modules}, '/strategy-audits/x/reports/y')
    assert '/strategy-audits/x/reports/y/funding-events.csv' in links
    assert '时序未覆盖与补件明细' in links
    assert 'capacity-missing' not in links


def test_ranking_import_preserves_explicit_signal_timestamp_over_row_datetime():
    raw = ('datetime,rank,order_book_id,selected,available_at\n'
           '2024-01-03 09:00:00,1,510300.XSHG,true,2024-01-04T01:00:00Z\n').encode()
    bundle = imports.normalize([imports.SourceFile('rankings.csv', raw)], adapter='auto', name='时间证据')
    assert bundle['scores'][0]['available_at'] == '2024-01-04T01:00:00Z'
    fallback = raw.replace(b'2024-01-04T01:00:00Z', b'')
    bundle = imports.normalize([imports.SourceFile('rankings.csv', fallback)], adapter='auto', name='时间证据')
    assert bundle['scores'][0]['available_at'] == '2024-01-03 09:00:00'
