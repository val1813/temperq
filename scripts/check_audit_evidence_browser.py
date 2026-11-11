"""Exercise advanced evidence forms in the installed offline audit edition.

Requires explicitly declared synthetic account/risk fixtures. Creates an isolated
workspace and blocks external network and excluded workbench modules. Does not
modify the installed application, its saved reports, or original fixtures.
"""
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

from check_audit_portable import GUARD

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ('qa_execution_evidence.mjs', 'qa_validation_evidence.mjs', 'qa_risk_evidence.mjs')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(folder):
    declaration = json.loads((folder / 'fixture.json').read_text(encoding='utf-8'))
    if declaration['source_kind'] != 'synthetic_fixture':
        raise ValueError('Only explicitly synthetic fixtures are allowed')
    hashes = declaration['input_sha256']
    for name, digest in hashes.items():
        candidate = (folder / name).resolve()
        if not candidate.is_relative_to(folder) or not candidate.is_file() or sha(candidate) != digest:
            raise ValueError(f'Invalid or changed synthetic fixture: {name}')
    return hashes


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('--app', type=Path, required=True, help='Relocated TemperQ directory containing runtime and MANIFEST.json')
    parser.add_argument('--validation-fixture', type=Path, required=True)
    parser.add_argument('--risk-fixture', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app, out = args.app.resolve(), args.output.resolve()
    fixtures = {kind: folder.resolve() for kind, folder in (
        ('validation', args.validation_fixture), ('risk', args.risk_fixture))}
    initial = {kind: inputs(folder) for kind, folder in fixtures.items()}
    manifest = json.loads((app / 'MANIFEST.json').read_text(encoding='utf-8'))
    if manifest['edition'] != 'audit' or manifest['working_tree_changes']:
        raise ValueError('Require a committed audit edition')
    matched = {}
    for name, digest in manifest['files_sha256'].items():
        if name.startswith('src/qwb/') and name.endswith('.py'):
            local = ROOT / name
            runtime = app / 'runtime/Lib/site-packages' / name.removeprefix('src/')
            if sha(local) != digest or sha(runtime) != digest:
                raise ValueError(f'Installed production Python differs from current repository: {name}')
            matched[name] = digest
    if not matched:
        raise ValueError('No installed Python sources matched')
    out.mkdir(parents=True, exist_ok=False)
    workspace = out / 'workspace'
    workspace.mkdir()
    guard = out / 'offline_start.py'
    guard.write_text(GUARD, encoding='utf-8')
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        port = listener.getsockname()[1]
    base = f'http://127.0.0.1:{port}'
    env = dict(os.environ, PYTHONHOME='invalid', PYTHONPATH='invalid', PYTHONNOUSERSITE='1',
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1',
               TEMPERQ_INPUT_QA_URL=base, TEMPERQ_VALIDATION_FIXTURE=str(fixtures['validation']),
               TEMPERQ_RISK_FIXTURE=str(fixtures['risk']))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    stages = []
    try:
        with (out / 'server.log').open('xb') as log:
            process = subprocess.Popen([str(app / 'runtime/python.exe'), '-I', '-B', '-X', 'utf8',
                str(guard), str(port)], cwd=workspace, env=env, stdout=log, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            try:
                deadline = time.monotonic() + 45
                while True:
                    if process.poll() is not None:
                        raise RuntimeError('Audit server exited; inspect server.log')
                    try:
                        with opener.open(base, timeout=2) as response:
                            if response.status == 200:
                                break
                    except (OSError, urllib.error.URLError):
                        if time.monotonic() >= deadline:
                            raise TimeoutError('Audit server did not start')
                        time.sleep(.25)
                for name in SCRIPTS:
                    target = out / Path(name).stem
                    suite_env = dict(env, TEMPERQ_QA_OUTPUT=str(target))
                    with (out / (name + '.log')).open('xb') as suite_log:
                        run = subprocess.run(['node', str(ROOT / 'scripts' / name)], cwd=ROOT,
                            env=suite_env, stdout=suite_log, stderr=subprocess.STDOUT, timeout=240)
                    result = json.loads((target / 'result.json').read_text(encoding='utf-8')) if run.returncode == 0 else {}
                    stages.append({'suite': name, 'exit_code': run.returncode, 'passed': result.get('passed') is True,
                                   'script_sha256': sha(ROOT / 'scripts' / name)})
                    if run.returncode or not stages[-1]['passed']:
                        raise RuntimeError(f'{name} failed; inspect saved log and screenshots')
                    print(f'PASS {name}', flush=True)
            finally:
                if process.poll() is None:
                    process.terminate()
                process.wait(timeout=15)
        if 'ISOLATION_VIOLATION' in (out / 'server.log').read_text(encoding='utf-8'):
            raise RuntimeError('Excluded module or external network attempted')
        assert not (workspace / 'runs/jobs').exists() and not (workspace / 'runs/paper').exists()
        assert initial == {kind: inputs(folder) for kind, folder in fixtures.items()}
        record = {'status': 'passed', 'source_kind': 'synthetic_fixture', 'stages': stages,
                  'runtime_source_commit': manifest['source_commit'], 'installed_python_sha256': matched,
                  'harness_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                  'browser_helpers_sha256': sha(ROOT / 'scripts/qa_evidence_helpers.mjs'),
                  'external_network_blocked': True, 'training_and_data_imports_blocked': True,
                  'original_fixtures_unchanged': True, 'widths': [1440, 390, 320],
                  'real_broker_validation': False, 'risk_model_calibrated': False,
                  'institutional_readiness': 'not_established'}
        with (out / 'acceptance.json').open('x', encoding='utf-8') as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
    except Exception as error:
        with (out / 'failure.json').open('x', encoding='utf-8') as handle:
            json.dump({'status': 'failed', 'stages': stages, 'error': str(error)}, handle, ensure_ascii=False, indent=2)
        raise


if __name__ == '__main__':
    main()
