"""Exercise the real freeze/predict/score CLI on explicitly synthetic static exports."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from qwb import audit_benchmark, audit_validation as validation


def write_json(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def verify(out):
    out.mkdir(parents=True, exist_ok=False)
    materials = out/'materials'
    rows, labels = [], []
    original = {}
    marker = out/'STRATEGY_EXECUTED_UNEXPECTEDLY'
    for seed in [19001, 19002]:
        clean = audit_benchmark.simulate(seed, 'clean', 100)
        for fault in ['clean', 'price_break']:
            cid = f's{seed}_{fault}'
            files = dict(clean) if fault == 'clean' else audit_benchmark.simulate(seed, fault, 100)
            files['strategy.py'] += f'from pathlib import Path\nPath({str(marker)!r}).write_text("must not execute")\n'
            hashes = {}
            for name, text in files.items():
                path = materials/cid/name
                path.parent.mkdir(parents=True, exist_ok=True)
                raw = text.encode('utf-8')
                with path.open('xb') as stream:
                    stream.write(raw)
                hashes[cid+'/'+name] = validation.sha(raw)
                original[path] = raw
            rows.append(dict(id=cid, group_id='rotation_same_generator_family',
                source_kind='synthetic_fixture', opening_cash=100000., files=hashes))
            manifested = fault != 'clean' and files['prices.csv'] != clean['prices.csv']
            labels.append(dict(case_id=cid, defect_id='price_chain' if fault != 'clean' else None,
                manifested=manifested, acceptable_act_card_ids=[]))
    manifest = dict(cases=rows, settings=dict(commission_bp=3., minimum_commission=5., max_weight=1., thresholds={}))
    protocol = dict(cohort_kind='synthetic_development', development_group_ids=['previous_development'],
        defects=[dict(id='price_chain', card_ids=['price_jump'], recall_min=.9, minimum_groups=1)],
        clean_failure_max=.05, minimum_clean_groups=1)
    write_json(out/'manifest.json', manifest)
    write_json(out/'protocol.json', protocol)
    write_json(out/'labels.json', dict(cases=labels))
    labels_hash = validation.sha((out/'labels.json').read_bytes())
    stages = []
    env = dict(os.environ, PYTHONUTF8='1', PYTHONPATH=str(ROOT/'src'),
        OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')

    def cli(name, stage, options, expected, artifact):
        cmd = [sys.executable, '-X', 'utf8', '-m', 'qwb.cli', 'audit', 'validation', stage,
               *options, '--out', str(artifact), '--json']
        result = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True)
        for suffix, raw in [('stdout.txt', result.stdout), ('stderr.txt', result.stderr)]:
            with (out/(name+'.'+suffix)).open('xb') as stream:
                stream.write(raw)
        stages.append(dict(name=name, expected_exit_code=expected, exit_code=result.returncode,
                           passed=result.returncode == expected))
        if result.returncode != expected:
            raise ValueError(f'{name}: expected {expected}, got {result.returncode}; see preserved logs')

    cli('freeze', 'freeze', ['--manifest', str(out/'manifest.json'), '--protocol', str(out/'protocol.json'),
        '--labels-sha256', labels_hash], 0, out/'plan.json')
    cli('predict', 'predict', ['--plan', str(out/'plan.json'), '--materials-root', str(materials)], 0, out/'predictions.json')
    cli('score', 'score', ['--plan', str(out/'plan.json'), '--predictions', str(out/'predictions.json'),
        '--labels', str(out/'labels.json')], 1, out/'score.json')
    scored = validation.load(out/'score.json')
    assert scored['defects']['price_chain']['manifested_cases'] == 2
    assert scored['defects']['price_chain']['detected_cases'] == 2
    assert scored['defects']['price_chain']['group_recall_interval']['n'] == 1
    assert scored['clean']['cases'] == 2 and scored['clean']['group_failure_interval']['n'] == 1
    assert scored['numerical_gate'] == 'failed' and scored['institutional_readiness'] == 'not_established'
    assert not marker.exists()
    # Formatting changes also violate the original-byte label commitment.
    with (out/'changed_labels.json').open('xb') as stream:
        stream.write((out/'labels.json').read_bytes()+b'\n')
    cli('changed_labels_rejected', 'score', ['--plan', str(out/'plan.json'), '--predictions', str(out/'predictions.json'),
        '--labels', str(out/'changed_labels.json')], 2, out/'unexpected_changed_label_score.json')
    assert not (out/'unexpected_changed_label_score.json').exists()
    before = (out/'predictions.json').read_bytes()
    cli('overwrite_rejected', 'predict', ['--plan', str(out/'plan.json'), '--materials-root', str(materials)], 2, out/'predictions.json')
    assert (out/'predictions.json').read_bytes() == before
    changed = materials/'changed_copy'
    for path, raw in original.items():
        target = changed/path.relative_to(materials)
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as stream:
            stream.write(raw + (b'\n' if path.name == 'prices.csv' else b''))
    cli('changed_material_rejected', 'predict', ['--plan', str(out/'plan.json'), '--materials-root', str(changed)],
        2, out/'unexpected_changed_material_prediction.json')
    assert not (out/'unexpected_changed_material_prediction.json').exists()
    pred = validation.load(out/'predictions.json')
    for row in pred['rows']:
        if row['case_id'].endswith('clean'):
            row.update(status='error', accounting_status='not_verified', cards=[], error_type='ValueError')
    pred['digest'] = validation.object_digest({k: x for k, x in pred.items() if k != 'digest'})
    write_json(out/'injected_error_predictions.json', pred)
    cli('operational_error_scored', 'score', ['--plan', str(out/'plan.json'),
        '--predictions', str(out/'injected_error_predictions.json'), '--labels', str(out/'labels.json')], 1, out/'injected_error_score.json')
    injected = validation.load(out/'injected_error_score.json')
    assert injected['clean']['operational_error_cases'] == 2
    assert injected['clean']['group_failure_interval']['count'] == 1
    for path, raw in original.items():
        assert path.read_bytes() == raw
    report = dict(status='passed', stages=stages, synthetic_cases=4, declared_groups=1,
        detected_cases=2, manifested_cases=2, independent_blind_validation=False,
        external_label_custody=False, original_inputs_unchanged=True,
        source_not_executed=not marker.exists(), numerical_gate=scored['numerical_gate'],
        operational_error_injection='explicitly_modified_prediction_fixture_not_real_detector_output',
        implementation_sha256=validation.sha(Path(validation.__file__).read_bytes()),
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        institutional_readiness='not_established')
    write_json(out/'verification.json', report)
    print(json.dumps(dict(status='passed', stages=len(stages),cases=4,groups=1,
        numerical_gate=scored['numerical_gate'],independent_blind_validation=False)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', required=True, type=Path)
    verify(parser.parse_args().out.resolve())
