"""Paper event ingestion, lineage, calendar and immutable archive contracts."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from qwb import paper_corporate_actions as ca, daily_data, experiments as ex
from test_paper_lifecycle import document


def encoded(doc):
    return json.dumps(doc).encode()


@pytest.mark.parametrize('mutation', ['source', 'code', 'date', 'ratio', 'fields', 'duplicate', 'lineage', 'nan'])
def test_malformed_event_input_fails_with_value_error(mutation):
    doc = document(['000001.SZ'])
    if mutation == 'source': doc['source'] = []
    if mutation == 'code': doc['coverage'][0]['code'] = []
    if mutation == 'date': doc['coverage'][0]['start'] = '2026-02-30'
    if mutation == 'ratio': doc['actions'][1]['numerator'] = True
    if mutation == 'fields': doc['actions'][0]['extra'] = 1
    if mutation == 'duplicate': doc['actions'].append(deepcopy(doc['actions'][0]))
    if mutation == 'lineage': doc['version'] = 2
    if mutation == 'nan': doc['factor_changes'][0]['after'] = float('nan')
    with pytest.raises(ValueError): ca.parse(encoded(doc))


def test_duplicate_json_keys_and_unknown_complex_actions_rejected():
    with pytest.raises(ValueError): ca.parse(b'{"version":1,"version":2}')
    doc = document(['000001.SZ'])
    doc['actions'][0]['kind'] = 'rights_issue'
    with pytest.raises(ValueError): ca.parse(encoded(doc))


def test_distinct_ids_cannot_count_one_economic_dividend_twice():
    doc = document(['000001.SZ'])
    duplicate = deepcopy(doc['actions'][0])
    duplicate['event_id'] = 'another-announcement-for-same-dividend'
    doc['actions'].append(duplicate)
    with pytest.raises(ValueError, match='重复或有歧义'): ca.parse(encoded(doc))


@pytest.mark.parametrize('mutation', ['delete', 'change', 'coverage', 'factor', 'parent', 'version', 'source'])
def test_successor_rejects_historical_rewrite(mutation):
    old = document(['000001.SZ'])
    new = deepcopy(old)
    new.update(version=2, previous_digest=ex.object_digest(old))
    if mutation == 'delete': new['actions'].pop()
    if mutation == 'change': new['actions'][0]['gross_per_share'] = 2
    if mutation == 'coverage': new['coverage'][0]['through'] = '2026-10-20'
    if mutation == 'factor': new['factor_changes'] = []
    if mutation == 'parent': new['previous_digest'] = 'sha256:'+'0'*64
    if mutation == 'version': new['version'] = 3
    if mutation == 'source': new['source']['kind'] = 'provider_archive'
    with pytest.raises(ValueError): ca.successor(old, new)


def test_coverage_extension_is_versioned_and_prior_decisions_stay_identical():
    old = document(['000001.SZ'])
    new = deepcopy(old)
    new.update(version=2, previous_digest=ex.object_digest(old))
    new['coverage'][0]['through'] = '2026-12-01'
    ca.successor(old, new)
    assert ca.decision_digest(old, '2026-10-20T15:30:00+08:00') == ca.decision_digest(new, '2026-10-20T15:30:00+08:00')


def test_weekend_payment_is_explicitly_unsupported_in_daily_adapter():
    doc = document(['000001.SZ'])
    doc['actions'][0]['pay_at'] = '2026-10-24T09:00:00+08:00'
    with pytest.raises(ValueError, match='开市日'):
        ca.validate_calendar(doc, pd.bdate_range('2026-10-19', '2026-10-26'), '2026-10-19', '2026-10-26')


def test_archive_uses_short_path_but_checks_full_digest_and_contents(tmp_path):
    doc = document(['000001.SZ'])
    digest = ca.archive(tmp_path, doc)
    assert ca.archived(tmp_path, digest) == doc
    assert ca.archive(tmp_path, doc) == digest
    path = next((tmp_path/'ca_inputs').iterdir())
    assert len(path.name) == 37
    changed = deepcopy(doc)
    changed['source']['reference'] = 'tampered'
    ex.write(path, changed)
    with pytest.raises(ValueError, match='摘要'): ca.archived(tmp_path, digest)
    with pytest.raises(ValueError, match='修改'): ca.archive(tmp_path, doc)


def test_data_ingestion_freezes_document_and_requires_lineage_for_extension(tmp_path):
    dates = pd.bdate_range('2026-10-19', '2026-10-23')
    rows = pd.DataFrame(dict(code='000001.SZ', date=dates.strftime('%Y-%m-%d'), open=10.,
        high=11., low=9., close=10., amount=10000000., adj_factor=1.))
    natural = pd.date_range('2026-10-19', '2026-11-06')
    calendar = pd.DataFrame(dict(date=natural.strftime('%Y-%m-%d'), is_open=(natural.weekday<5).astype(int))).to_csv(index=False).encode()
    doc = document(['000001.SZ'])
    feed = tmp_path/'feed'
    daily_data.update(feed, rows.iloc[:2].to_csv(index=False).encode(), calendar, actions_raw=encoded(doc))
    original = daily_data.current(feed)
    assert ca.load(original) == doc
    original_bytes = (original/ca.FILENAME).read_bytes()
    new = deepcopy(doc)
    new.update(version=2, previous_digest=ex.object_digest(doc))
    new['coverage'][0]['through'] = '2026-12-01'
    daily_data.update(feed, rows.to_csv(index=False).encode(), calendar, actions_raw=encoded(new))
    assert ca.load(daily_data.current(feed)) == new
    assert (original/ca.FILENAME).read_bytes() == original_bytes
    before = (feed/'current.json').read_bytes()
    with pytest.raises(ValueError):
        daily_data.update(feed, rows.to_csv(index=False).encode(), calendar, actions_raw=encoded(doc))
    assert (feed/'current.json').read_bytes() == before
    ex.write(daily_data.current(feed)/ca.FILENAME, doc)
    with pytest.raises(ValueError, match='摘要'): ca.load(daily_data.current(feed))
