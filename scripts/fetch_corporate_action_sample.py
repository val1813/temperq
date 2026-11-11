"""Bounded read-only vendor sample; credentials and vendor messages are never saved."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(args):
    endpoint = urllib.parse.urlsplit(args.endpoint)
    if endpoint.scheme != 'https' or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query:
        raise ValueError('A credential-free HTTPS endpoint is required')
    token = args.token_file.read_text(encoding='utf-8-sig').strip()
    if not token or len(token.split()) != 1:
        raise ValueError('A single local credential is required; never printed')
    args.out.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    common = dict(ts_code=args.code, start_date=args.start, end_date=args.end)
    cases = [
        ('dividend', dict(ts_code=args.code), 'ts_code,end_date,ann_date,div_proc,stk_div,stk_bo_rate,stk_co_rate,cash_div,cash_div_tax,record_date,ex_date,pay_date,div_listdate,imp_ann_date'),
        ('daily', common, 'ts_code,trade_date,open,high,low,close,pre_close,vol,amount'),
        ('adj_factor', common, 'ts_code,trade_date,adj_factor'),
        ('stk_limit', common, 'ts_code,trade_date,pre_close,up_limit,down_limit'),
        ('trade_cal', dict(exchange='SSE', start_date=args.start, end_date=args.end), 'exchange,cal_date,is_open,pretrade_date'),
    ]
    records = []
    for api, params, fields in cases:
        record = dict(api=api, params=params, requested_fields=fields,
            retrieved_at=datetime.now(timezone.utc).isoformat(), source_kind='third_party_tushare_compatible',
            source_authenticated=False, historical_receipt_time_established=False)
        body = json.dumps(dict(api_name=api, token=token, params=params, fields=fields)).encode()
        request = urllib.request.Request(args.endpoint, data=body, headers={'Content-Type':'application/json'})
        try:
            with opener.open(request, timeout=20) as response:
                raw = response.read(16_000_001)
            if len(raw) > 16_000_000 or token.encode() in raw:
                raise ValueError('Invalid response size or credential echo')
            payload = json.loads(raw)
            data = payload.get('data')
            if payload.get('code') != 0 or not isinstance(data, dict):
                record['status'] = 'vendor_error'
            elif (not isinstance(data.get('fields'), list) or not isinstance(data.get('items'), list)
                  or any(not isinstance(f, str) for f in data['fields'])
                  or any(not isinstance(row, list) or len(row) != len(data['fields']) for row in data['items'])):
                record['status'] = 'invalid_table'
            else:
                encoded = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode()
                if token.encode() in encoded:
                    raise ValueError('Credential echo')
                with (args.out/(api+'.json')).open('xb') as stream:
                    stream.write(encoded)
                record.update(status='returned_rows' if data['items'] else 'empty_unestablished',
                    rows=len(data['items']), sha256=hashlib.sha256(encoded).hexdigest())
        except Exception as exc:
            record.update(status='transport_or_validation_error', error_type=type(exc).__name__)
        records.append(record)
        print(json.dumps({k:record[k] for k in ('api','status','rows','error_type') if k in record}), flush=True)
    report = dict(records=records, credential_persisted=False, user_accounts_modified=False,
        independent_broker_reconciliation=False, historical_receipt_time_established=False,
        amount_unit='daily amount: CNY thousand; daily vol: hundred shares',
        institutional_readiness='not_established')
    with (args.out/'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--endpoint', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--code', default='000001.SZ')
    parser.add_argument('--start', default='20250101')
    parser.add_argument('--end', default='20250930')
    main(parser.parse_args())
