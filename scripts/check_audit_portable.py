"""Relocate and exercise the installed audit edition, with external traffic blocked."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request
import zipfile

from check_windows_portable import audit_workflow


GUARD = '''import importlib.abc, sys
from pathlib import Path
blocked = {'qwb.ui','qwb.strategy','qwb.daily','qwb.pipeline','qwb.jobs','qwb.worker','qwb.paper_auto','qwb.full_market','qwb.fetch','qwb.connectors.tushare','qwb.connectors.cn_a'}
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in blocked:
            print('ISOLATION_VIOLATION module', fullname, flush=True)
            raise ImportError('Module excluded from audit edition')
def offline(event, args):
    if event in {'socket.connect','socket.connect_ex'} and isinstance(args[1],tuple) and args[1][0] in {'127.0.0.1','::1'}:
        return
    if event in {'socket.connect','socket.connect_ex','socket.getaddrinfo','socket.sendto'}:
        print('ISOLATION_VIOLATION external_network', event, flush=True)
        raise RuntimeError('External traffic blocked')
sys.meta_path.insert(0,Guard())
sys.addaudithook(offline)
from qwb.audit_app import serve
serve(Path.cwd(), port=int(sys.argv[1]), open_browser=False)
'''


def check(archive, destination):
    archive, destination = Path(archive).resolve(), Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            if not (destination / item.filename).resolve().is_relative_to(destination):
                raise ValueError('Unsafe archive path')
        bundle.extractall(destination)
    app = destination / 'TemperQ'
    manifest = json.loads((app / 'MANIFEST.json').read_text(encoding='utf-8'))
    assert manifest['edition'] == 'audit'
    for name, digest in manifest['files_sha256'].items():
        assert hashlib.sha256((app / name).read_bytes()).hexdigest() == digest, name
    python = app / 'runtime/python.exe'
    probe = "import importlib.util as u; assert all(u.find_spec(n) is None for n in ('qwb.ui','qwb.strategy','qwb.fetch','qwb.full_market','qwb.paper_auto','lightgbm','baostock','torch')); from qwb.audit_app import create_app; print('installed audit edition verified')"
    env = dict(os.environ, PYTHONHOME='invalid', PYTHONPATH='invalid', PYTHONNOUSERSITE='1')
    subprocess.run([str(python), '-I', '-B', '-c', probe], cwd=app, env=env, check=True)
    cli = subprocess.run([str(python), '-I', '-B', '-m', 'qwb.audit_entry', '--help'],
                         cwd=app, env=env, check=True, capture_output=True, text=True)
    assert '{audit}' in cli.stdout and 'train' not in cli.stdout
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    guard = destination / 'offline_start.py'
    guard.write_text(GUARD, encoding='utf-8')
    log = destination / 'server.log'
    with log.open('wb') as stream:
        process = subprocess.Popen([str(python), '-I', '-B', '-X', 'utf8', str(guard), str(port)],
            cwd=app, env=env, stdout=stream, stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        base = f'http://127.0.0.1:{port}'
        try:
            for attempt in range(60):
                if process.poll() is not None:
                    raise RuntimeError('Installed audit server exited; see server.log')
                try:
                    with opener.open(base, timeout=1) as response:
                        markup = response.read().decode('utf-8')
                    break
                except (OSError, urllib.error.URLError):
                    time.sleep(.5)
            else:
                raise RuntimeError('Installed audit server did not start')
            assert '上传策略，开始审计' in markup and 'api_token' not in markup
            workflow = audit_workflow(opener, base, destination)
            for path in ('/train','/run','/data','/data/tushare','/data/free','/paper','/jobs','/start','/models'):
                for payload in (None, b''):
                    try:
                        opener.open(urllib.request.Request(base + path, data=payload), timeout=10)
                        raise AssertionError('Legacy route is available: ' + path)
                    except urllib.error.HTTPError as error:
                        assert error.code == 404, (path, error.code)
            assert not (app / 'runs/jobs').exists() and not (app / 'runs/paper').exists()
        finally:
            process.terminate()
            process.wait(timeout=15)
    assert 'ISOLATION_VIOLATION' not in log.read_text(encoding='utf-8')
    result = {'status': 'passed', 'edition': 'audit', 'verified_package_files': len(manifest['files_sha256']),
              'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
              'installed_runtime': True, 'external_network_blocked': True, 'workflow': workflow,
              'training_and_data_routes_absent': True, 'account_automation_started': False}
    (destination / 'acceptance.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    check(args.archive, args.destination)
