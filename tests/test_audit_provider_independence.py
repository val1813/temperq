"""Exercise real audit CLI exports with the market connectors unavailable."""
import json
from pathlib import Path
import subprocess
import sys


PROGRAM = r'''
import contextlib
import importlib.abc
import io
import json
from pathlib import Path
import sys

root, output = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / 'src'))
unavailable = {'qwb.connectors.tushare', 'qwb.connectors.cn_a', 'qwb.fetch', 'qwb.full_market',
               'qwb.ui', 'qwb.strategy', 'qwb.daily', 'qwb.pipeline', 'qwb.jobs', 'qwb.paper_auto'}
attempts = []

class NoMarketProvider(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in unavailable:
            attempts.append(fullname)
            raise ImportError('Market connector deliberately unavailable')

def offline(event, args):
    if event in {'socket.connect', 'socket.connect_ex'} and isinstance(args[1], tuple) and args[1][0] in {'127.0.0.1', '::1'}:
        return  # Windows asyncio implements its local wakeup pipe with a loopback socket.
    if event in {'socket.connect', 'socket.connect_ex', 'socket.getaddrinfo', 'socket.sendto'}:
        attempts.append(event)
        raise RuntimeError('External network deliberately unavailable')

sys.meta_path.insert(0, NoMarketProvider())
sys.addaudithook(offline)
from qwb.audit_entry import main

results = {}
for sample in sorted((root / 'tests/fixtures/formats/samples').iterdir()):
    if not sample.is_dir():
        continue
    destination = output / sample.name
    with contextlib.redirect_stdout(io.StringIO()) as captured:
        code = main(['audit', 'run', str(sample), '--out', str(destination), '--json'])
    response = json.loads(captured.getvalue())
    expected = {'bigquant': 'verified', 'generic': 'provisional', 'joinquant': 'provisional',
                'ricequant_local': 'verified', 'ricequant_web': 'provisional'}
    assert code == 0 and response['books'] == expected[sample.name], response
    for name in ('report.html', 'summary.json', 'evidence.json', 'stocks.csv', 'daily.csv'):
        assert (destination / name).is_file(), name
    results[sample.name] = response['books']

assert not attempts, attempts
assert not unavailable.intersection(sys.modules)
from qwb.audit_app import create_app
from fastapi.testclient import TestClient
app = create_app(output / 'web')
with TestClient(app) as client:
    assert client.get('/').status_code == 200
    sample = client.get('/strategy-audits/sample.zip').content
    response = client.post('/strategy-audits/import', data={'token': app.state.token,
        'name': 'Offline synthetic audit', 'adapter': 'auto', 'opening_empty': 'yes', 'opening_cash': '10000'},
        files={'files': ('sample.zip', sample)}, follow_redirects=False)
    assert response.status_code == 303
    path = response.headers['location']
    from qwb.strategy_audit_store import read
    from qwb.experiments import object_digest
    bundle = read(output / 'web', path.rsplit('/', 1)[-1])[1]
    response = client.post(path + '/analyze', data={'token': app.state.token, 'input_digest': object_digest(bundle),
        'same_run': 'yes', 'commission_bp': '3', 'minimum_commission': '5', 'max_weight': '50'})
    assert response.status_code == 200
    assert client.get(response.url.path + '/report.html').status_code == 200
assert not attempts, attempts
assert not unavailable.intersection(sys.modules)
print(json.dumps({'samples': results, 'connector_and_network_attempts': attempts}))
'''


def test_five_export_formats_audit_and_render_without_market_providers(tmp_path):
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, '-B', '-c', PROGRAM, str(root), str(tmp_path)],
                            capture_output=True, text=True, encoding='utf-8', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    evidence = json.loads(result.stdout)
    assert set(evidence['samples']) == {'bigquant', 'generic', 'joinquant', 'ricequant_local', 'ricequant_web'}
    assert evidence['samples'] == {'bigquant': 'verified', 'generic': 'provisional', 'joinquant': 'provisional',
                                    'ricequant_local': 'verified', 'ricequant_web': 'provisional'}
    assert evidence['connector_and_network_attempts'] == []
