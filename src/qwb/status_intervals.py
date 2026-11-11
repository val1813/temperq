"""Convert supplied daily ST observations into intervals, without a data provider."""
from __future__ import annotations

import pandas as pd


def st_status(daily_st: pd.DataFrame, days: pd.DatetimeIndex, *, source: str) -> pd.DataFrame:
    """Collapse daily membership into [start, end); retain the existing 09:15 convention.

    The timestamp is the legacy dataset convention, not certified historical
    receipt time. Input provenance is supplied by the caller, never inferred.
    """
    pos = {d: i for i, d in enumerate(days)}
    df = daily_st.assign(i=pd.to_datetime(daily_st['date']).map(pos)).dropna(subset=['i'])
    df['i'] = df['i'].astype(int)
    df['value'] = df['name'].astype('string').str.contains('*ST', regex=False).map(
        {True: '*ST', False: 'ST'})
    df = df.sort_values(['code', 'i'])
    brk = ((df['i'].diff() != 1) | (df['code'] != df['code'].shift())
           | (df['value'] != df['value'].shift()))
    df['run'] = brk.cumsum()
    runs = df.groupby('run').agg(code=('code', 'first'), value=('value', 'first'),
                               i0=('i', 'min'), i1=('i', 'max')).reset_index(drop=True)
    last = len(days) - 1
    start = runs['i0'].map(lambda i: days[i])
    end = runs['i1'].map(lambda i: days[i + 1] if i < last else pd.NaT)
    return pd.DataFrame({
        'source': source,
        'record_id': runs['code'] + '|st|' + start.dt.strftime('%Y-%m-%d'),
        'revision': 0,
        'available_time': (pd.to_datetime(start) + pd.Timedelta(hours=9, minutes=15)).dt.tz_localize('Asia/Shanghai'),
        'code': runs['code'], 'field': 'st',
        'start': start.dt.strftime('%Y-%m-%d'),
        'end': end.dt.strftime('%Y-%m-%d').astype(object).where(end.notna(), None),
        'value': runs['value'],
    })
