"""Read-only re-audit of explicit historical exports, checking money bridges.

Each --case has NAME MATERIALS PREVIOUS_AUDIT. No strategies are executed,
credentials read, source files changed or broker evidence inferred.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--case', nargs=3, action='append', required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    from qwb import audit_cli, audit_impact, audit_actions
    parser = argparse.ArgumentParser()
    audit_cli.add_parser(parser.add_subparsers(dest='cmd', required=True))
    records = []
    for name, material_dir, previous_dir in args.case:
        if Path(name).name != name:
            raise ValueError('Case name must be one path component')
        source, previous = Path(material_dir), Path(previous_dir)
        old = json.loads((previous / 'evidence.json').read_text(encoding='utf-8'))
        files = [f for f in source.iterdir() if f.is_file()]
        before = {f.name: sha(f) for f in files}
        flags = ['audit', 'run', str(source), '--out', str(args.output / name), '--json',
                 '--name', name, '--commission-bp', str(old['result']['settings']['commission_bp']),
                 '--min-commission', str(old['result']['settings']['minimum_commission'])]
        opening = old['input'].get('opening') or {}
        if opening.get('kind') == 'empty':
            flags += ['--opening-cash', str(opening['cash'])]
        run = audit_cli.run(parser.parse_args(flags))
        new = json.loads((args.output / name / 'evidence.json').read_text(encoding='utf-8'))
        modules = new['result']['modules']
        assert run['books'] == 'verified', name
        old_pnl = sum(r['net_pnl'] for r in old['result']['modules']['accounting']['daily'])
        pnl = sum(r['net_pnl'] for r in modules['accounting']['daily'])
        assert abs(old_pnl - pnl) < .01, (name, old_pnl, pnl)
        impacts = modules['monetary_impact']
        shared = impacts.get('cash_buffer') or {}
        assert shared.get('amount') is None and not shared.get('rows'), name
        assert shared.get('reference_to') == 'partial_entry', name
        floor = (modules.get('fill_quality') or {}).get('floor') or {}
        if floor.get('change') is not None:
            assert abs(sum(r['net_pnl'] for r in floor['rows']) - floor['change']) < .01, name
            for r in floor['rows']:
                assert r['observed_minutes'] == r['required_minutes'] == 10, name
                delta = (r['original_price'] - r['stress_price']) * r['quantity']
                assert abs((delta if r['side'] == 'buy' else -delta) - r['net_pnl']) < .01, name
        checks = []
        for identity, item in impacts.items():
            if item['amount'] is None:
                continue
            assert abs(sum(row['net_pnl'] for row in item['rows']) - item['amount']) < .01, (name, identity)
            if item['kind'] == 'counterfactual':
                independent = sum(r['quantity'] * (r['end_price'] - r['start_price']) for r in item['rows'])
                assert abs(independent - item['amount']) < .01
            if identity in ('limits', 'listings'):
                for r in item['rows']:
                    # Original notional, including source cash-rounding, is authoritative.
                    assert abs(r['exit_or_mark_value'] - r['entry_notional'] - r['fees'] - r['net_pnl']) < .01
            if identity == 'off_target_pnl':
                assert abs(item['amount'] - modules['fulfilment']['by_target']['outside']) < .01
            checks.append({'card': identity, 'amount': item['amount'], 'kind': item['kind'],
                           'coverage': item['coverage'], 'rows': len(item['rows'])})
        for f in files:
            assert sha(f) == before[f.name], f.name
        assert '改完有什么帮助' in (args.output / name / 'report.html').read_text(encoding='utf-8')
        cards = audit_actions.cards(new['result'], new['input'])
        records.append({'case': name, 'status': 'passed', 'source_unchanged': True,
                        'source_hashes': before, 'books': run['books'], 'net_pnl': pnl,
                        'historical_pnl_unchanged': True, 'money_checks': checks,
                        'quantified_cards': [c['id'] for c in cards if c['impact'].get('amount') is not None],
                        'input_digest': new['result']['input_digest'], 'result_digest': new['result']['result_digest'],
                        'scope': 'historical_simulated_platform_exports_not_broker_evidence'})
        print(f'PASS {name}: {len(checks)} monetary bridges; {len(cards)} cards; source unchanged', flush=True)
    report = {'status': 'passed', 'cases': records}
    (args.output / 'acceptance.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
