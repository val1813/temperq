"""Run actual factor-attribution CLI against known synthetic and negative cases."""
from __future__ import annotations

import argparse
import copy
import csv
from decimal import Decimal
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qwb import audit_factor_attribution as factor


def table(raw):
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))


def encode(name, rows):
    text = io.StringIO(newline='')
    writer = csv.DictWriter(text, fieldnames=factor.FIELDS[name], lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    return text.getvalue().encode()


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    example = ROOT / 'examples/factor_attribution_synthetic'
    names = ('scope', 'periods', 'weights', 'exposures', 'factor_returns')
    source = {name: (example / f'{name}.{"json" if name == "scope" else "csv"}').read_bytes() for name in names}
    hashes = {name: sha256(raw).hexdigest() for name, raw in source.items()}
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')
    stages = []

    def call(name, data, expected):
        folder = args.output / name
        folder.mkdir()
        command = [sys.executable, '-B', '-m', 'qwb.cli', 'audit', 'factor-attribution']
        for key, raw in data.items():
            path = folder / f'{key}.{"json" if key == "scope" else "csv"}'
            path.write_bytes(raw)
            command += ['--' + key.replace('_', '-'), str(path)]
        command += ['--source-kind', 'synthetic_fixture', '--out', str(folder / 'report.json'), '--json']
        result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace')
        (folder / 'stdout.txt').write_text(result.stdout, encoding='utf-8')
        (folder / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
        report = json.loads(result.stdout)
        assert result.returncode == {'computed': 0, 'insufficient': 1, 'error': 2}[expected], name
        assert report['status'] == expected, name
        stages.append(dict(name=name, passed=True, status=expected))
        print(f'PASS {name}', flush=True)
        return report

    normal = call('known_answer', source, 'computed')
    assert Decimal(normal['aggregate']['active']['compounded_return_difference']) == Decimal('-0.0305')
    assert Decimal(normal['aggregate']['portfolio']['linked_residual']) == Decimal('0.00895')
    assert all(Decimal(v['closure_error']) == 0 for v in normal['aggregate'].values())
    late = copy.deepcopy(source)
    exposures = table(late['exposures'])
    exposures.append(dict(exposures[2], available_at='2025-01-03T15:00:00+08:00', exposure='10', source_ref='synthetic-late-revision'))
    late['exposures'] = encode('exposures', exposures)
    revised = call('late_revision', late, 'computed')
    assert normal['periods'][0] == revised['periods'][0]
    assert normal['periods'][1]['books'] != revised['periods'][1]['books']
    exposures.append(dict(exposures[2], available_at='2025-01-06T15:00:00+08:00', exposure='999', source_ref='synthetic-future-revision'))
    late['exposures'] = encode('exposures', exposures)
    future = call('future_revision', late, 'computed')
    assert future['periods'] == revised['periods']
    missing = copy.deepcopy(source)
    missing['exposures'] = encode('exposures', table(missing['exposures'])[1:])
    report = call('missing_exposure', missing, 'insufficient')
    assert report['aggregate'] is None and report['missing']
    bad = copy.deepcopy(source)
    weights = table(bad['weights'])
    weights[0]['available_at'] = '2025-01-03T15:00:00+08:00'
    bad['weights'] = encode('weights', weights)
    call('future_weight', bad, 'error')
    bad = copy.deepcopy(source)
    periods = table(bad['periods'])
    periods[1]['start_time'] = '2025-01-04T15:00:00+08:00'
    bad['periods'] = encode('periods', periods)
    call('missing_interval', bad, 'error')
    assert all(sha256((example / f'{name}.{"json" if name == "scope" else "csv"}').read_bytes()).hexdigest() == hashes[name] for name in names)
    evidence = dict(status='passed', stages=stages, scope='synthetic_factor_attribution_not_historical_risk_model_validation',
        input_sha256=hashes, original_examples_unchanged=True,
        implementation_sha256=sha256(Path(factor.__file__).read_bytes()).hexdigest(),
        verifier_sha256=sha256(Path(__file__).read_bytes()).hexdigest(),
        known_portfolio_compounded_return='0.0098', known_benchmark_compounded_return='0.0403',
        known_active_compounded_difference='-0.0305', known_portfolio_linked_residual='0.00895',
        real_factor_data_validation=False, calibrated_risk_model=False, institutional_readiness='not_established')
    (args.output / 'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(status='passed', stages=len(stages), real_factor_data_validation=False)))


if __name__ == '__main__':
    main()
