"""API snapshots must preserve declared events across append and recovery."""
from copy import deepcopy
import json

import pandas as pd
import pytest

from qwb import daily_data, experiments as ex, full_market, pipeline
from qwb import paper_corporate_actions as ca
from test_daily_operation import DirectCache, responses
from test_paper_lifecycle import document


@pytest.fixture
def feed(tmp_path, monkeypatch):
    root = tmp_path/'feed'
    dates = pd.bdate_range('2026-10-19', '2026-10-20')
    codes = ['000001.SZ','600000.SH']
    rows = pd.DataFrame([dict(code=c,date=str(d.date()),open=10.,high=11.,low=9.,close=10.,
        amount=10000000.,volume=1000000.,adj_factor=1.,is_st=0) for d in dates for c in codes])
    natural = pd.date_range('2026-10-19','2026-11-10')
    cal = pd.DataFrame(dict(date=natural.strftime('%Y-%m-%d'),is_open=(natural.weekday<5).astype(int)))
    doc = document(codes)
    daily_data.update(root, rows.to_csv(index=False).encode(), cal.to_csv(index=False).encode(),
        actions_raw=json.dumps(doc).encode())
    monkeypatch.setattr(pipeline,'find_local_config',lambda *a:dict(data_dir=str(daily_data.current(root)),
        cache_dir=str(tmp_path/'cache'),tushare=dict(token_file='unused',url='unused')))
    monkeypatch.setattr(full_market,'load_token',lambda *a:'fixture-token')
    class Client:
        def __init__(self,*a,**kw): pass
        def call(self,api,**kw):
            if api == 'trade_cal':
                return cal.rename(columns={'date':'cal_date'}).assign(cal_date=natural.strftime('%Y%m%d'))
            tables = responses()
            frame = tables[api].copy()
            if 'trade_date' in frame: frame['trade_date'] = kw['trade_date']
            return frame
        def paged(self,api,*a,**kw): return self.call(api,**kw)
    monkeypatch.setattr(full_market,'TushareClient',Client)
    original = full_market.daily_tables
    monkeypatch.setattr(full_market,'daily_tables',lambda cli,cache,day:original(cli,DirectCache(),day,min_stocks=2))
    return root, doc


def update(root, day='2026-10-22', **kw):
    return full_market.update(root.parent,root,day,log=lambda *a,**kw:None,**kw)


def test_api_append_carries_registered_event_document_and_digest(feed):
    root, doc = feed
    before = daily_data.current(root)
    original = (before/ca.FILENAME).read_bytes()
    update(root)
    assert daily_data.current(root) != before
    assert ca.load(daily_data.current(root),required=True) == doc
    assert (before/ca.FILENAME).read_bytes() == original
    assert ex.read(daily_data.current(root)/'snapshot.json')['corporate_actions_digest'] == ex.object_digest(doc)


def test_missing_registered_events_cannot_publish_a_new_snapshot(feed):
    root, _ = feed
    (daily_data.current(root)/ca.FILENAME).unlink()
    before = (root/'current.json').read_bytes()
    with pytest.raises(ValueError,match='公司行动'):
        update(root)
    assert (root/'current.json').read_bytes() == before


def test_event_coverage_can_be_extended_without_new_market_rows(feed):
    root, doc = feed
    before = daily_data.current(root)
    new = deepcopy(doc)
    new.update(version=2,previous_digest=ex.object_digest(doc))
    new['coverage'][0]['through'] = '2026-12-01'
    result = update(root,'2026-10-20',actions_raw=json.dumps(new).encode())
    assert result['new_sessions'] == 0 and daily_data.current(root) != before
    assert ca.load(daily_data.current(root)) == new
    assert ca.load(before) == doc


def test_api_event_revision_rejected_without_changing_pointer(feed):
    root, doc = feed
    bad = deepcopy(doc)
    bad.update(version=2,previous_digest=ex.object_digest(doc))
    bad['actions'][0].update(gross_per_share=2.,ex_price_adjustment=1.)
    before = (root/'current.json').read_bytes()
    with pytest.raises(ValueError,match='修订'):
        update(root,actions_raw=json.dumps(bad).encode())
    assert (root/'current.json').read_bytes() == before


def test_event_only_update_is_idempotent_on_retry(feed):
    root, doc = feed
    newer = deepcopy(doc)
    newer.update(version=2, previous_digest=ex.object_digest(doc))
    newer['coverage'][0]['through'] = '2026-12-01'
    raw = json.dumps(newer).encode()
    first = update(root, '2026-10-20', actions_raw=raw)
    pointer = (root/'current.json').read_bytes()
    assert update(root, '2026-10-20', actions_raw=raw) == first
    assert (root/'current.json').read_bytes() == pointer


def test_corrupt_registered_events_rejected_even_with_replacement(feed):
    root, doc = feed
    (daily_data.current(root)/ca.FILENAME).write_bytes(b'{}')
    pointer = (root/'current.json').read_bytes()
    with pytest.raises(ValueError):
        update(root, actions_raw=json.dumps(doc).encode())
    assert (root/'current.json').read_bytes() == pointer


def test_api_historical_calendar_revision_rejected(feed, monkeypatch):
    root, _ = feed
    original = full_market.TushareClient
    class Revised(original):
        def call(self, api, **kw):
            frame = super().call(api, **kw)
            if api == 'trade_cal':
                frame.loc[frame.cal_date.eq('20261019'), 'is_open'] = 0
            return frame
    monkeypatch.setattr(full_market, 'TushareClient', Revised)
    pointer = (root/'current.json').read_bytes()
    with pytest.raises(ValueError, match='calendar revision'):
        update(root)
    assert (root/'current.json').read_bytes() == pointer
