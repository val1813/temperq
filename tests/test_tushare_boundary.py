"""Provider responses must not persist credentials or ambiguous table shapes."""
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread

import pytest

from qwb.connectors import tushare
from qwb.connectors.tushare import ApiError, TushareClient

TOKEN = 'fixture-' * 4


def client(payload):
    return TushareClient(TOKEN,transport=lambda *args:payload,min_interval=0,retries=0)


def test_vendor_error_echo_never_exposes_credential():
    with pytest.raises(ApiError) as error:
        client(dict(code=40001,msg='permission denied token='+TOKEN,data=None)).call('daily')
    assert TOKEN not in str(error.value)


def test_successful_credential_echo_cannot_reach_the_raw_response_cache():
    with pytest.raises(ApiError):
        client(dict(code=0,data=dict(fields=['ts_code'],items=[[TOKEN]]))).call('daily')


@pytest.mark.parametrize('data', [dict(fields=['x','x'],items=[[1,2]]),
    dict(fields=['x'],items=[[1,2]]),dict(fields=['x']),dict(fields=['x'],items='bad'),
    dict(fields=[{'unexpected':'field'}],items=[])])
def test_malformed_or_ambiguous_table_is_rejected_as_api_error(data):
    with pytest.raises(ApiError): client(dict(code=0,data=data)).call('daily')


@pytest.fixture
def provider():
    response = dict(status=200, headers={}, body=b'', requests=0)
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            response['requests'] += 1
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert payload['token'] == TOKEN and payload['api_name'] == 'daily'
            self.send_response(response['status'])
            for name, value in response['headers'].items(): self.send_header(name, value)
            self.end_headers()
            self.wfile.write(response['body'])
        def do_GET(self):
            response['requests'] += 1
            self.send_response(500)
            self.end_headers()
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = 'http://127.0.0.1:'+str(server.server_port)
    try:
        yield TushareClient(TOKEN, url=url, min_interval=0, retries=0), response, url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_production_http_decodes_gzip(provider):
    cli, response, _ = provider
    response.update(body=gzip.compress(json.dumps(dict(code=0,data=dict(fields=['close'],items=[[10.5]]))).encode()),
                    headers={'Content-Encoding':'gzip'})
    assert cli.call('daily').close.tolist() == [10.5]


@pytest.mark.parametrize('compressed', [False, True])
def test_production_http_bounds_wire_and_decompressed_response(provider, monkeypatch, compressed):
    cli, response, _ = provider
    monkeypatch.setattr(tushare, 'MAX_RESPONSE_BYTES', 256)
    raw = b' ' * 1024
    response['body'] = gzip.compress(raw) if compressed else raw
    with pytest.raises(ApiError, match='size limit'): cli.call('daily')


def test_production_http_does_not_follow_redirects(provider):
    cli, response, url = provider
    response.update(status=307, headers={'Location':url+'/other'})
    with pytest.raises(ApiError): cli.call('daily')
    assert response['requests'] == 1


@pytest.mark.parametrize('body', [b'not json', b'\xff', b'\x1f\x8binvalid gzip', gzip.compress(b'truncated')[:-5]])
def test_production_http_malformed_encoding_is_safe_api_error(provider, body):
    cli, response, _ = provider
    response['body'] = body
    with pytest.raises(ApiError) as error: cli.call('daily')
    assert TOKEN not in str(error.value)


@pytest.mark.parametrize('code', [False, [], {'unexpected':'value'}])
def test_invalid_status_code_does_not_escape_as_raw_provider_value(code):
    with pytest.raises(ApiError): client(dict(code=code,data=dict(fields=[],items=[]))).call('daily')
