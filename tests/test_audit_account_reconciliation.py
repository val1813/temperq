"""Known ledger answers and failures invisible to order-only reconciliation."""
import copy
import csv
import io
import json

import pytest

from qwb import audit_account_reconciliation as account


def csv_bytes(fields, rows=()):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(fields)
    writer.writerows(rows)
    return stream.getvalue().encode('utf-8')


def fixture():
    scope = dict(schema_version='account_reconciliation_scope_v1', account_id='synthetic-account', currency='CNY',
                 start_date='2025-01-03', end_date='2025-01-07', cash_basis='trade_date_total_cash',
                 positions_complete=True, fills_complete=True, events_complete=True)
    ident = lambda day: ['synthetic-account', 'CNY', day]
    balances = [ident('2025-01-02') + [10000, 1000, 11000, 1],
                ident('2025-01-03') + [8995, 2100, 11095, 1],
                ident('2025-01-06') + [9780, 1925, 11705, 1],
                ident('2025-01-07') + [10731, 0, 10731, 0]]
    positions = [ident('2025-01-02') + ['A', 100, 10, 1000],
                 ident('2025-01-03') + ['A', 200, '10.5', 2100],
                 ident('2025-01-06') + ['A', 350, '5.5', 1925]]
    fills = [['synthetic-account', 'CNY', '2025-01-03', 'o1', 't1', 'A', 'buy', 100, 10, 5],
             ['synthetic-account', 'CNY', '2025-01-06', 'o2', 't2', 'A', 'sell', 50, '5.4', 5],
             ['synthetic-account', 'CNY', '2025-01-07', 'o3', 't3', 'A', 'sell', 350, '5.6', 5]]
    events = [ident('2025-01-04') + ['e1', 'deposit', '', 500, 0, 'synthetic-bank-in'],
              ident('2025-01-05') + ['e2', 'dividend', 'A', 20, 0, 'synthetic-dividend'],
              ident('2025-01-06') + ['e3', 'split', 'A', 0, 200, 'synthetic-split'],
              ident('2025-01-07') + ['e4', 'withdrawal', '', -1000, 0, 'synthetic-bank-out'],
              ident('2025-01-07') + ['e5', 'interest', '', 1, 0, 'synthetic-interest'],
              ident('2025-01-07') + ['e6', 'tax', '', -2, 0, 'synthetic-tax'],
              ident('2025-01-07') + ['e7', 'fee', '', -3, 0, 'synthetic-fee']]
    bundle = {'scope.json': json.dumps(scope).encode(), 'balances.csv': csv_bytes(account.BALANCES, balances),
              'positions.csv': csv_bytes(account.POSITIONS, positions), 'fills.csv': csv_bytes(account.FILLS, fills),
              'events.csv': csv_bytes(account.EVENTS, events)}
    calendar = csv_bytes(('date', 'is_open'), [('2025-01-03', 1), ('2025-01-04', 0), ('2025-01-05', 0),
                                             ('2025-01-06', 1), ('2025-01-07', 1)])
    return bundle, copy.deepcopy(bundle), calendar


def run(a=None, b=None, calendar=None, **kwargs):
    left, right, cal = fixture()
    return account.reconcile(a or left, b or right, cal if calendar is None else calendar,
                             source_kind='synthetic_fixture', **kwargs)


def change(bundle, name, row, field, value):
    table = csv.DictReader(io.StringIO(bundle[name].decode()))
    fields = table.fieldnames
    rows = list(table)
    rows[row][field] = str(value)
    bundle[name] = csv_bytes(fields, ([r[k] for k in fields] for r in rows))


def remove_row(bundle, name, row):
    table = csv.reader(io.StringIO(bundle[name].decode()))
    fields, *rows = table
    rows.pop(row)
    bundle[name] = csv_bytes(fields, rows)


def test_known_cash_inventory_and_weekend_events_close_without_authentication_claim():
    result = run()
    assert result['status'] == 'matched' and result['breaks'] == 0
    assert result['same_input_bytes'] is True and result['source_authenticated'] is False
    assert result['real_broker_validation'] is False
    assert len(result['expected_snapshot_dates']) == 4
    checks = result['rollforward']['internal']['checks']
    assert all(r['state'] == 'matched' for r in checks)
    assert [(r['date'], r['expected']) for r in checks if r['check'] == 'cash_rollforward'] == [
        ('2025-01-03', '8995'), ('2025-01-06', '9780.0'), ('2025-01-07', '10731.0')]
    assert result['executions']['matched_orders'] == 3


def test_fragmented_broker_fills_keep_ledger_answer():
    a, b, cal = fixture()
    raw = b['fills.csv'].decode().replace('o1,t1,A,buy,100,10,5',
        'o1,b1,A,buy,40,10,2\nsynthetic-account,CNY,2025-01-03,o1,b2,A,buy,60,10,3')
    b['fills.csv'] = raw.encode()
    result = run(a, b, cal)
    assert result['status'] == 'matched' and result['same_input_bytes'] is False
    assert result['executions']['broker_fills'] == 4


def test_order_match_cannot_hide_cash_snapshot_difference():
    a, b, _ = fixture()
    change(b, 'balances.csv', 1, 'cash', 8996)
    result = run(a, b)
    assert result['status'] == 'breaks'
    assert result['executions']['status'] == 'matched'
    assert result['balances'][1]['internal_minus_broker']['cash'] == '-1'
    assert any(r['check'] == 'cash_rollforward' and r['state'] == 'mismatch' for r in result['rollforward']['broker']['checks'])


def test_common_missing_event_detected_by_both_rollforwards_despite_matching_snapshots():
    a, b, _ = fixture()
    remove_row(a, 'events.csv', 0)
    remove_row(b, 'events.csv', 0)
    result = run(a, b)
    assert result['status'] == 'breaks'
    assert all(r['state'] == 'matched' for r in result['balances'])
    for side in ('internal', 'broker'):
        assert any(r['check'] == 'cash_rollforward' and r['actual_minus_expected'] == '500.0'
                   for r in result['rollforward'][side]['checks'])


def test_common_missing_split_detected_and_security_differences_do_not_net():
    a, b, _ = fixture()
    remove_row(a, 'events.csv', 2)
    remove_row(b, 'events.csv', 2)
    result = run(a, b)
    assert result['status'] == 'breaks'
    assert any(r['check'] == 'long_only_inventory' and r['state'] == 'mismatch' for r in result['rollforward']['broker']['checks'])
    a, b, _ = fixture()
    change(b, 'positions.csv', 1, 'code', 'B')
    result = run(a, b)
    assert result['status'] == 'breaks'
    assert {r['state'] for r in result['positions'] if r['key'][0] == '2025-01-03'} == {'missing_broker', 'missing_internal'}


def test_mark_and_aggregate_identity_checked_even_when_both_sources_match():
    a, b, _ = fixture()
    for bundle in (a, b):
        change(bundle, 'positions.csv', 1, 'price', 11)
        change(bundle, 'balances.csv', 2, 'position_count', 2)
        change(bundle, 'balances.csv', 2, 'market_value', 1926)
    result = run(a, b)
    failures = {r['check'] for r in result['rollforward']['internal']['checks'] if r['state'] != 'matched'}
    assert {'position_mark', 'position_count', 'market_value_sum', 'equity_identity'} <= failures


@pytest.mark.parametrize('row', [0, 1, 3])
def test_both_sources_missing_same_snapshot_is_insufficient(row):
    a, b, _ = fixture()
    remove_row(a, 'balances.csv', row)
    remove_row(b, 'balances.csv', row)
    result = run(a, b)
    assert result['status'] == 'insufficient'
    assert any(r['check'] == 'snapshot_present' and r['state'] == 'missing' for r in result['rollforward']['internal']['checks'])


def test_declared_flat_period_is_valid_but_empty_balances_are_insufficient():
    a, b, _ = fixture()
    for bundle in (a, b):
        bundle['positions.csv'] = csv_bytes(account.POSITIONS)
        bundle['fills.csv'] = csv_bytes(account.FILLS)
        bundle['events.csv'] = csv_bytes(account.EVENTS)
        bundle['balances.csv'] = csv_bytes(account.BALANCES, [['synthetic-account', 'CNY', day, 10000, 0, 10000, 0]
            for day in ('2025-01-02', '2025-01-03', '2025-01-06', '2025-01-07')])
    result = run(a, b)
    assert result['status'] == 'matched' and result['executions']['status'] == 'insufficient'
    a['balances.csv'] = b['balances.csv'] = csv_bytes(account.BALANCES)
    assert run(a, b)['status'] == 'insufficient'


@pytest.mark.parametrize('field,value', [('account_id', 'other'), ('currency', 'USD'), ('cash_basis', 'available_cash'),
                                         ('fills_complete', False), ('positions_complete', 1), ('events_complete', None),
                                         ('start_date', '20250103'), ('end_date', '2025-01-02')])
def test_scope_mismatch_incomplete_or_unsupported_basis_rejected(field, value):
    a, b, _ = fixture()
    scope = json.loads(b['scope.json'])
    scope[field] = value
    b['scope.json'] = json.dumps(scope).encode()
    with pytest.raises(ValueError):
        run(a, b)


@pytest.mark.parametrize('name,field,value', [('fills.csv', 'account_id', 'other'), ('events.csv', 'currency', 'USD'),
    ('fills.csv', 'quantity', '1.5'), ('fills.csv', 'quantity', '1e2'), ('positions.csv', 'quantity', '0'),
    ('balances.csv', 'cash', 'NaN'), ('balances.csv', 'cash', '-1'), ('balances.csv', 'cash', '1e100000'),
    ('positions.csv', 'price', '0'), ('fills.csv', 'date', '2025-01-04'), ('events.csv', 'kind', 'adjustment'),
    ('events.csv', 'cash_delta', '-500'), ('events.csv', 'source_ref', '')])
def test_invalid_values_rejected_before_comparison(name, field, value):
    a, b, _ = fixture()
    change(b, name, 0, field, value)
    with pytest.raises(ValueError):
        run(a, b)


@pytest.mark.parametrize('name', ['balances.csv', 'positions.csv', 'fills.csv', 'events.csv'])
def test_duplicate_rows_are_rejected(name):
    a, b, _ = fixture()
    lines = b[name].splitlines(keepends=True)
    b[name] += lines[1]
    with pytest.raises(ValueError, match='重复'):
        run(a, b)


@pytest.mark.parametrize('calendar', [b'date,is_open\n', b'date,is_open\n2025-01-03,1\n',
    b'date,is_open\n2025-01-03,1\n2025-01-03,1\n', b'date,is_open\n2025-01-03,true\n'])
def test_missing_or_duplicate_calendar_days_do_not_shrink_scope(calendar):
    with pytest.raises(ValueError):
        run(calendar=calendar)


def test_quantities_and_prices_are_exact_tolerance_cannot_mask_business_errors():
    a, b, _ = fixture()
    change(b, 'balances.csv', 1, 'cash', '8995.01')
    assert run(a, b)['status'] == 'matched'
    assert run(a, b, money_tolerance='0')['status'] == 'breaks'
    change(b, 'positions.csv', 1, 'price', '10.50000001')
    assert run(a, b)['status'] == 'breaks'
    for tolerance in ('1', '-1', 'NaN', '0.01000001'):
        with pytest.raises(ValueError):
            run(money_tolerance=tolerance)


def test_unknown_headers_missing_files_and_duplicate_scope_fields_rejected():
    a, b, _ = fixture()
    b['events.csv'] = b['events.csv'].replace(b'source_ref', b'unknown')
    with pytest.raises(ValueError):
        run(a, b)
    a, b, _ = fixture()
    b.pop('events.csv')
    with pytest.raises(ValueError):
        run(a, b)
    a, b, _ = fixture()
    b['scope.json'] = b['scope.json'].replace(b'{', b'{"currency":"CNY",', 1)
    with pytest.raises(ValueError, match='重复'):
        run(a, b)


def test_event_identifiers_require_explicit_mapping_and_do_not_net_across_dates():
    a, b, _ = fixture()
    change(b, 'events.csv', 0, 'event_id', 'different')
    result = run(a, b)
    assert result['status'] == 'breaks'
    assert {r['state'] for r in result['events'] if r['date'] == '2025-01-04'} == {'missing_internal', 'missing_broker'}
    a, b, _ = fixture()
    change(b, 'events.csv', 0, 'cash_delta', 501)
    change(b, 'events.csv', 1, 'cash_delta', 19)
    result = run(a, b)
    assert len([r for r in result['events'] if r['state'] == 'mismatch']) == 2


def test_cli_actual_entrypoint_preserves_inputs_and_existing_evidence(tmp_path, capsys):
    from qwb.cli import main
    a, b, calendar = fixture()
    folders = [tmp_path / 'internal', tmp_path / 'broker']
    for folder, files in zip(folders, (a, b)):
        folder.mkdir()
        for name, raw in files.items():
            (folder / name).write_bytes(raw)
    cal, out = tmp_path / 'calendar.csv', tmp_path / 'report.json'
    cal.write_bytes(calendar)
    argv = ['audit', 'account-reconcile', '--internal', str(folders[0]), '--broker', str(folders[1]),
            '--calendar', str(cal), '--source-kind', 'synthetic_fixture', '--out', str(out), '--json']
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'matched'
    saved = out.read_bytes()
    assert main(argv) == 2
    capsys.readouterr()
    assert saved == out.read_bytes()
    change(b, 'balances.csv', 1, 'cash', 8996)
    (folders[1] / 'balances.csv').write_bytes(b['balances.csv'])
    assert main(argv[:-3] + ['--json']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'breaks'
    for name, raw in a.items():
        assert (folders[0] / name).read_bytes() == raw


@pytest.mark.parametrize('value', ['995804.4299999999', '984024.3200000001', '10000.00'])
def test_native_bridge_bounds_float_residue_without_changing_business_amounts(value):
    import importlib.util
    from pathlib import Path
    from decimal import Decimal
    path = Path(__file__).resolve().parents[1] / 'scripts/verify_account_reconciliation.py'
    spec = importlib.util.spec_from_file_location('account_bridge', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    rounded, residue = module.native_money(value)
    assert rounded + residue == Decimal(value)
    assert abs(residue) <= Decimal('0.00000001')
    with pytest.raises(ValueError, match='sub-cent'):
        module.native_money('995804.429')
