"""Known multi-period answer, bitemporal revisions, incomplete materials and CLI."""
import copy
import csv
from decimal import Decimal
import io
import json

import pytest

from qwb import audit_factor_attribution as factor


START = '2025-01-02T15:00:00+08:00'
MID = '2025-01-03T15:00:00+08:00'
END = '2025-01-06T15:00:00+08:00'


def csv_bytes(name, rows):
    stream = io.StringIO(newline='')
    writer = csv.writer(stream, lineterminator='\n')
    writer.writerow(factor.FIELDS[name])
    writer.writerows(rows)
    return stream.getvalue().encode()


def fixture():
    scope = dict(schema_version='factor_attribution_scope_v1', start_time=START, end_time=END,
                 max_exposure_age_days=10, holdings_complete=True, cash_return='zero', factors=[
        dict(name='bank', kind='industry', definition='synthetic bank total return', unit='return_per_unit_exposure'),
        dict(name='tech', kind='industry', definition='synthetic tech total return', unit='return_per_unit_exposure'),
        dict(name='value', kind='style', definition='synthetic standardized value, industry adjusted', unit='return_per_unit_exposure')])
    periods = [['p1', START, MID, '.02', '.01', 'synthetic-nav-1'],
               ['p2', MID, END, '-.01', '.03', 'synthetic-nav-2']]
    # Ordinary decimal spelling is deliberate: no .02 shorthand in actual CSV.
    periods = [[('0' + v if v.startswith('.') else '-0' + v[1:] if v.startswith('-.') else v)
                for v in row] for row in periods]
    weights = []
    for pid, start in [('p1', START), ('p2', MID)]:
        for book, values in [('portfolio', [('A', '0.6'), ('B', '0.2'), ('CASH', '0.2')]),
                             ('benchmark', [('A', '0.5'), ('B', '0.5'), ('CASH', '0')])]:
            for code, weight in values:
                weights.append([pid, book, code, weight, start, start, 'synthetic-holdings'])
    exposures = []
    for code, values in [('A', [1, 0, 2]), ('B', [0, 1, -1])]:
        for name, value in zip(('bank', 'tech', 'value'), values):
            exposures.append([code, name, START, START, value, 'synthetic-exposure'])
    returns = []
    for pid, end, values in [('p1', MID, ['0.01', '0.02', '0.005']), ('p2', END, ['-0.02', '0.04', '-0.01'])]:
        for name, value in zip(('bank', 'tech', 'value'), values):
            returns.append([pid, name, value, end, 'synthetic-factor-return'])
    return {'scope': json.dumps(scope).encode(), 'periods': csv_bytes('periods', periods),
            'weights': csv_bytes('weights', weights), 'exposures': csv_bytes('exposures', exposures),
            'factor_returns': csv_bytes('factor_returns', returns)}


def run(data=None, **kwargs):
    data = data or fixture()
    return factor.analyze(*(data[k] for k in ('scope', 'periods', 'weights', 'exposures', 'factor_returns')),
                          source_kind=kwargs.pop('source_kind', 'synthetic_fixture'), **kwargs)


def table(data, name):
    return list(csv.DictReader(io.StringIO(data[name].decode())))


def set_rows(data, name, rows):
    data[name] = csv_bytes(name, ([row[k] for k in factor.FIELDS[name]] for row in rows))


def change(data, name, index, key, value):
    rows = table(data, name)
    rows[index][key] = value
    set_rows(data, name, rows)


def test_known_factor_residual_and_geometrically_linked_active_answer():
    result = run()
    assert result['status'] == 'computed' and result['source_authenticated'] is False
    p1, p2 = result['periods']
    assert p1['books']['portfolio']['known_factor_exposure'] == {'bank': '0.6', 'tech': '0.2', 'value': '1.0'}
    assert Decimal(p1['books']['portfolio']['modeled_return']) == Decimal('0.015')
    assert Decimal(p1['books']['portfolio']['residual']) == Decimal('0.005')
    assert Decimal(p2['books']['portfolio']['residual']) == Decimal('0.004')
    aggregate = result['aggregate']
    assert Decimal(aggregate['portfolio']['compounded_return']) == Decimal('0.0098')
    assert Decimal(aggregate['benchmark']['compounded_return']) == Decimal('0.0403')
    assert Decimal(aggregate['active']['compounded_return_difference']) == Decimal('-0.0305')
    assert Decimal(aggregate['portfolio']['linked_factor_contributions']['bank']) == Decimal('-0.00606')
    assert Decimal(aggregate['portfolio']['linked_residual']) == Decimal('0.00895')
    assert Decimal(aggregate['benchmark']['linked_residual']) == Decimal('0.017275')
    for book in ('portfolio', 'benchmark', 'active'):
        assert Decimal(aggregate[book]['closure_error']) == 0
    assert p1['books']['portfolio']['link_multiplier'] == '0.99'
    assert p1['books']['benchmark']['link_multiplier'] == '1.03'


def test_future_exposure_revision_does_not_change_earlier_attribution():
    data = fixture()
    earlier = run(data)
    rows = table(data, 'exposures')
    rows.append(dict(rows[2], available_at=MID, exposure='10', source_ref='synthetic-late-revision'))
    set_rows(data, 'exposures', rows)
    revised = run(data)
    assert revised['periods'][0] == earlier['periods'][0]
    assert revised['periods'][1]['books']['portfolio']['known_factor_exposure']['value'] == '5.8'
    # A still later unseen revision cannot change either period.
    rows.append(dict(rows[2], available_at=END, exposure='999', source_ref='synthetic-unseen-revision'))
    set_rows(data, 'exposures', rows)
    assert run(data)['periods'] == revised['periods']
    assert run(data)['unused_exposure_versions'] == 1


def test_latest_asof_precedes_late_revision_of_an_older_asof():
    data = fixture()
    rows = table(data, 'exposures')
    rows.extend([dict(rows[2], asof_time='2025-01-03T10:00:00+08:00', available_at='2025-01-03T11:00:00+08:00', exposure='3'),
                 dict(rows[2], available_at=MID, exposure='100')])
    set_rows(data, 'exposures', rows)
    result = run(data)
    assert result['periods'][1]['books']['portfolio']['known_factor_exposure']['value'] == '1.6'


def test_missing_or_late_exposure_is_insufficient_not_zero_filled():
    data = fixture()
    change(data, 'exposures', 0, 'available_at', MID)
    result = run(data)
    assert result['status'] == 'insufficient' and result['aggregate'] is None
    assert result['periods'][0]['books']['portfolio']['non_cash_coverage'] == '0.25'
    assert 'factor_contributions' not in result['periods'][0]['books']['portfolio']
    assert result['periods'][1]['status'] == 'computed'
    assert any(r['factor'] == 'bank' and r['reason'] == 'missing_or_not_yet_available' for r in result['missing'])


def test_staleness_uses_observation_age_and_does_not_roll_expiry_on_revision():
    data = fixture()
    scope = json.loads(data['scope'])
    scope['max_exposure_age_days'] = 0
    data['scope'] = json.dumps(scope).encode()
    result = run(data)
    assert result['periods'][0]['status'] == 'computed'
    assert result['periods'][1]['status'] == 'insufficient'
    assert {r['reason'] for r in result['missing']} == {'stale'}


@pytest.mark.parametrize('name', ['weights', 'exposures', 'factor_returns'])
def test_empty_materials_cannot_produce_complete_attribution(name):
    data = fixture()
    data[name] = csv_bytes(name, [])
    result = run(data)
    assert result['status'] == 'insufficient' and result['aggregate'] is None


def test_missing_cash_row_and_missing_factor_return_are_explicit():
    data = fixture()
    set_rows(data, 'weights', [r for r in table(data, 'weights') if r['book'] != 'benchmark' or r['code'] != 'CASH'])
    set_rows(data, 'factor_returns', table(data, 'factor_returns')[1:])
    result = run(data)
    assert {'missing_complete_weights_or_cash_row', 'missing_factor_return'} <= {r['reason'] for r in result['missing']}


@pytest.mark.parametrize('name', ['periods', 'weights', 'exposures', 'factor_returns'])
def test_duplicate_identities_are_rejected(name):
    data = fixture()
    rows = table(data, name)
    set_rows(data, name, rows + [rows[0]])
    with pytest.raises(ValueError, match='重复'):
        run(data)


@pytest.mark.parametrize('name,key,value', [
    ('periods', 'start_time', '2025-01-02T15:00:00'),
    ('periods', 'portfolio_return', 'NaN'), ('periods', 'benchmark_return', '-1'),
    ('periods', 'portfolio_return', '2e-2'), ('periods', 'portfolio_return', '20%'),
    ('weights', 'weight', '-0.1'), ('weights', 'weight', '1.1'), ('weights', 'weight', '0.61'),
    ('weights', 'book', 'other'), ('weights', 'period_id', 'unknown'),
    ('weights', 'asof_time', MID), ('weights', 'available_at', MID),
    ('weights', 'available_at', '2025-01-01T15:00:00+08:00'),
    ('exposures', 'exposure', '1.1'), ('exposures', 'exposure', '-0.1'), ('exposures', 'exposure', '0.9'),
    ('exposures', 'asof_time', END), ('exposures', 'factor', 'unknown'), ('exposures', 'code', 'CASH'),
    ('factor_returns', 'available_at', START), ('factor_returns', 'factor', 'unknown'),
    ('factor_returns', 'return', 'Infinity'), ('factor_returns', 'return', '101')])
def test_bad_numbers_identity_units_and_timing_rejected(name, key, value):
    data = fixture()
    change(data, name, 0, key, value)
    with pytest.raises(ValueError):
        run(data)


@pytest.mark.parametrize('key,value', [('holdings_complete', 1), ('cash_return', 'estimated'),
    ('max_exposure_age_days', True), ('max_exposure_age_days', 367), ('factors', []),
    ('start_time', '2025-01-03T15:00:00+08:00')])
def test_invalid_scope_is_not_implicitly_repaired(key, value):
    data = fixture()
    scope = json.loads(data['scope'])
    scope[key] = value
    data['scope'] = json.dumps(scope).encode()
    with pytest.raises(ValueError):
        run(data)


def test_missing_or_overlapping_periods_cannot_be_linked():
    data = fixture()
    change(data, 'periods', 1, 'start_time', '2025-01-04T15:00:00+08:00')
    with pytest.raises(ValueError, match='连续'):
        run(data)
    change(data, 'periods', 1, 'start_time', START)
    with pytest.raises(ValueError):
        run(data)


def test_timezone_equivalence_and_input_order_do_not_change_results():
    data = fixture()
    expected = run(data)
    for name in ('periods', 'weights', 'exposures', 'factor_returns'):
        rows = table(data, name)
        for row in rows:
            for key in ('start_time', 'end_time', 'asof_time', 'available_at'):
                if key in row:
                    row[key] = row[key].replace('15:00:00+08:00', '07:00:00+00:00')
        set_rows(data, name, list(reversed(rows)))
    result = run(data)
    assert result['aggregate'] == expected['aggregate']
    assert [r['books'] for r in result['periods']] == [r['books'] for r in expected['periods']]


def test_all_cash_weights_need_no_security_exposures_and_leave_cash_yield_in_residual():
    data = fixture()
    rows = [r for r in table(data, 'weights') if r['code'] == 'CASH']
    for row in rows:
        row['weight'] = '1'
    set_rows(data, 'weights', rows)
    data['exposures'] = csv_bytes('exposures', [])
    result = run(data)
    assert result['status'] == 'computed'
    assert all(Decimal(v) == 0 for v in result['aggregate']['portfolio']['linked_factor_contributions'].values())
    assert Decimal(result['aggregate']['portfolio']['linked_residual']) == Decimal('0.0098')


def test_json_duplicate_fields_and_csv_unknown_headers_rejected():
    data = fixture()
    data['scope'] = data['scope'].replace(b'{', b'{"cash_return":"zero",', 1)
    with pytest.raises(ValueError, match='重复'):
        run(data)
    data = fixture()
    data['weights'] = data['weights'].replace(b'source_ref', b'extra')
    with pytest.raises(ValueError, match='表头'):
        run(data)


def test_cli_known_answer_missing_material_exit_and_append_only_output(tmp_path, capsys):
    from qwb.cli import main
    data = fixture()
    argv = ['audit', 'factor-attribution']
    for name, raw in data.items():
        path = tmp_path / f'{name}.data'
        path.write_bytes(raw)
        argv += [f'--{name.replace("_", "-")}', str(path)]
    target = tmp_path / 'report.json'
    argv += ['--source-kind', 'synthetic_fixture', '--out', str(target), '--json']
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'computed'
    saved = target.read_bytes()
    assert main(argv) == 2
    capsys.readouterr()
    assert target.read_bytes() == saved
    (tmp_path / 'exposures.data').write_bytes(csv_bytes('exposures', []))
    assert main(argv[:-3] + ['--json']) == 1
    assert json.loads(capsys.readouterr().out)['status'] == 'insufficient'
