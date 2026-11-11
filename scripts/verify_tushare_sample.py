"""Bounded production-client acceptance using private credentials and a new output."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from qwb import experiments as ex
from qwb.connectors.cache import Cache
from qwb.connectors.tushare import TushareClient, load_token


def main(args):
    endpoint = urlsplit(args.endpoint)
    if (endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username
            or endpoint.password or endpoint.query or endpoint.fragment):
        raise ValueError('A credential-free HTTPS endpoint is required')
    client = TushareClient(load_token(str(args.token_file)), url=args.endpoint,
                           min_interval=.5, timeout=20, retries=1)
    args.out.mkdir(parents=True, exist_ok=False)
    cache = Cache(args.out/'cache')
    common = dict(ts_code='000001.SZ', start_date='20250101', end_date='20250930')
    cases = [('dividend', dict(ts_code='000001.SZ')),
             *[(name, common) for name in ('daily', 'adj_factor', 'stk_limit')],
             ('trade_cal', dict(exchange='SSE', start_date='20250101', end_date='20250930'))]
    records = []
    for api, params in cases:
        record = dict(api=api, params=params, retrieved_at=datetime.now(timezone.utc).isoformat())
        try:
            frame = cache.get(api, params, lambda: client.call(api, **params))
            split = json.loads(frame.to_json(orient='split', force_ascii=False, double_precision=15))
            ex.write(args.out/(api+'.json'), dict(fields=split['columns'], items=split['data']))
            record.update(status='returned_rows' if len(frame) else 'empty_unestablished', rows=len(frame),
                          sha256=ex.digest(args.out/(api+'.json')))
        except Exception as exc:
            record.update(status='failed', error_type=type(exc).__name__)
        records.append(record)
        print(json.dumps({k:record[k] for k in ('api','status','rows','error_type') if k in record}), flush=True)
    passed = all(r['status']=='returned_rows' for r in records)
    ex.write(args.out/'manifest.json', dict(status='passed' if passed else 'failed', records=records,
        production_client=True, production_cache=True, source_kind='third_party_tushare_compatible',
        source_authenticated=False, historical_receipt_time_established=False,
        credentials_persisted=False, broker_reconciliation=False, actual_forward_intervals=0,
        institutional_readiness='not_established'))
    return 0 if passed else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    try:
        raise SystemExit(main(parser.parse_args()))
    except Exception as exc:
        print('Acceptance stopped: '+type(exc).__name__, file=sys.stderr)
        raise SystemExit(2)
