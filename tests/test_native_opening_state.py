import csv
import importlib.util
import io
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('native_opening_state', Path(__file__).parents[1] / 'scripts/native_opening_state.py')
opening = importlib.util.module_from_spec(spec)
spec.loader.exec_module(opening)


def row(**changes):
    return dict(date='2025-01-09', symbol='000001.SZ', observed_at='2025-01-09T09:29:59+08:00',
                available_at='2025-01-09T09:30:00+08:00', bid='11.49', ask='11.50', bid_size='500', ask_size='300',
                tradable='true', up_limit='12.00', down_limit='10.00', source_ref='synthetic-known-quote') | changes


def raw(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=opening.FIELDS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


def check(rows):
    return opening.read_opening_state(raw(rows), {('2025-01-09', '000001.SZ')})


def test_opening_availability_is_inclusive_and_zero_size_is_an_observation_not_missing():
    result = check([row(ask_size='0')])
    assert result[('2025-01-09', '000001.SZ')]['ask_size'] == 0
    assert result[('2025-01-09', '000001.SZ')]['bid_size'] == 500


@pytest.mark.parametrize('change', [
    {'available_at': '2025-01-09T20:00:00+08:00'},
    {'observed_at': '2025-01-09T09:30:01+08:00'},
    {'observed_at': '2025-01-09T09:28:59+08:00'},
    {'available_at': '2025-01-09T09:28:00+08:00'},
    {'available_at': '2025-01-09T09:30:00'},
    {'ask_size': '-1'}, {'ask_size': '1.1'}, {'ask_size': 'NaN'},
    {'ask': 'Infinity'}, {'ask': '11.491'}, {'ask': '10.00'},
    {'tradable': 'unknown'}, {'source_ref': ''}, {'symbol': 'OTHER'},
    {'ask': '12.01'}, {'bid': '9.99'}, {'ask': '1e100000'},
])
def test_future_or_stale_observations_bad_units_and_unknown_sources_rejected(change):
    with pytest.raises(ValueError):
        check([row(**change)])


def test_opening_schema_requires_complete_unique_identity_and_pinned_engine():
    with pytest.raises(ValueError, match='Missing'):
        check([])
    with pytest.raises(ValueError, match='Duplicate'):
        check([row(), row()])
    with pytest.raises(ValueError, match='hash differs'):
        opening.patched_source(b'print("untrusted code")')
