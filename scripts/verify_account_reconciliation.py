"""Read-only bridge from an existing native simulator probe to account reconciliation.

Callback fills independently build one ledger. Native per-day cash/inventory
build the other. Both use the supplied closing marks and the same simulator;
this is never independent broker evidence or a new execution-model validation.
"""
from __future__ import annotations

import argparse
import copy
import csv
from datetime import date, timedelta
from decimal import Decimal, localcontext
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from qwb import audit_account_reconciliation as account


def encode(fields, rows=()):
    text = io.StringIO(newline='')
    writer = csv.writer(text, lineterminator='\n')
    writer.writerow(fields)
    writer.writerows(rows)
    return text.getvalue().encode('utf-8')


def rows(raw):
    return list(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))


def native_money(value):
    """Native CNY cash/valuation is cent-based; bound binary-float CSV residue."""
    number = Decimal(value)
    rounded = number.quantize(Decimal('0.01'))
    residue = number - rounded
    if abs(residue) > Decimal('0.00000001'):
        raise ValueError('Native money has a material sub-cent residual; do not silently round')
    return rounded, residue


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--probe', type=Path, required=True)
    p.add_argument('--calendar', type=Path, required=True, help='existing SSE fields/items trade_cal cache')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    source_names = ['probe.json', 'native/snapshots.csv', 'materials/prices.csv',
                    'recorded_executions.csv', 'native_executions.csv']
    source = {name: (args.probe / name).read_bytes() for name in source_names}
    before = {name: hashlib.sha256(raw).hexdigest() for name, raw in source.items()}
    manifest = json.loads(source['probe.json'])
    cfg = manifest['config']
    if (manifest['engine_version'] != '1.231.0' or manifest['source_independence'] != 'same_simulator_two_reporting_paths' or
            cfg['market'] != 'CN' or cfg['corporate_actions'] != 'reject' or manifest['data']['corporate_action_flags']):
        raise ValueError('Only the verified empty-opening, no-corporate-action CN simulator probe is supported')
    with localcontext() as context:
        context.prec = 80
        scope = dict(schema_version='account_reconciliation_scope_v1', account_id='simulator-probe', currency='CNY',
                     start_date=cfg['start'], end_date=cfg['end'], cash_basis='trade_date_total_cash',
                     positions_complete=True, fills_complete=True, events_complete=True)
        snapshots = rows(source['native/snapshots.csv'])
        marks = {}
        for row in rows(source['materials/prices.csv']):
            key = (row['date'], row['code'])
            if key in marks:
                raise ValueError('Duplicate source mark')
            marks[key] = Decimal(row['close'])
        balances = [[], []]
        positions = [[], []]
        cash, inventory = Decimal(str(manifest['opening_cash'])), {s: Decimal(0) for s in cfg['symbols']}
        opening = (date.fromisoformat(cfg['start']) - timedelta(days=1)).isoformat()
        identity = lambda day: [scope['account_id'], 'CNY', day]
        for ledger in balances:
            ledger.append(identity(opening) + [cash, 0, cash, 0])
        fills = rows(source['recorded_executions.csv'])
        normalization_residues = []
        for snapshot in snapshots:
            day = snapshot['date']
            for fill in fills:
                if fill['date'] != day:
                    continue
                signed = Decimal(fill['quantity']) * (1 if fill['side'] == 'buy' else -1)
                inventory[fill['code']] += signed
                cash -= signed * Decimal(fill['price']) + Decimal(fill['fee'])
            native_inventory = {code: Decimal(snapshot[code]) for code in cfg['symbols']}
            observed_cash, cash_residue = native_money(snapshot['cash'])
            observed_equity, equity_residue = native_money(snapshot['equity'])
            normalization_residues.append(dict(date=day, cash=str(cash_residue), equity=str(equity_residue)))
            for index, holdings, total_cash in [(0, inventory, cash), (1, native_inventory, observed_cash)]:
                value, count = Decimal(0), 0
                for code, quantity in holdings.items():
                    if quantity:
                        price = marks[(day, code)]
                        market_value = quantity * price
                        positions[index].append(identity(day) + [code, quantity, price, market_value])
                        value += market_value
                        count += 1
                # Broker-side equity remains the native observed value, not the reconstructed value.
                equity = total_cash + value if index == 0 else observed_equity
                balances[index].append(identity(day) + [total_cash, value, equity, count])
        bundles = []
        for index, name in enumerate(('recorded_executions.csv', 'native_executions.csv')):
            bundles.append({'scope.json': json.dumps(scope).encode(),
                'balances.csv': encode(account.BALANCES, balances[index]),
                'positions.csv': encode(account.POSITIONS, positions[index]),
                'fills.csv': encode(account.FILLS, ([scope['account_id'], 'CNY'] + [r[k] for k in account.executions.FIELDS]
                                                 for r in rows(source[name]))),
                'events.csv': encode(account.EVENTS)})
    raw_calendar = args.calendar.read_bytes()
    calendar_data = json.loads(raw_calendar)
    fields = calendar_data['fields']
    dates = []
    for values in calendar_data['items']:
        row = dict(zip(fields, values))
        if row['exchange'] != 'SSE':
            raise ValueError('Only SSE calendar cache is supported')
        value = str(row['cal_date'])
        day = value if '-' in value else f'{value[:4]}-{value[4:6]}-{value[6:]}'
        if cfg['start'] <= day <= cfg['end']:
            dates.append([day, row['is_open']])
    calendar = encode(('date', 'is_open'), sorted(dates))
    cal_path = args.output / 'calendar.csv'
    cal_path.write_bytes(calendar)
    stages = []
    env = dict(os.environ, PYTHONPATH=str(ROOT / 'src'), PYTHONUTF8='1', PYTHONDONTWRITEBYTECODE='1',
               OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1')

    def check(name, data, expected):
        folder = args.output / name
        folder.mkdir()
        for label, bundle in zip(('internal', 'broker'), data):
            dest = folder / label
            dest.mkdir()
            for filename, raw in bundle.items():
                (dest / filename).write_bytes(raw)
        command = [sys.executable, '-B', '-m', 'qwb.cli', 'audit', 'account-reconcile',
                   '--internal', str(folder / 'internal'), '--broker', str(folder / 'broker'),
                   '--calendar', str(cal_path), '--source-kind', 'simulator_native',
                   '--out', str(folder / 'report.json'), '--json']
        result = subprocess.run(command, env=env, cwd=ROOT, capture_output=True, encoding='utf-8', errors='replace')
        (folder / 'stdout.txt').write_text(result.stdout, encoding='utf-8')
        (folder / 'stderr.txt').write_text(result.stderr, encoding='utf-8')
        report = json.loads(result.stdout)
        assert result.returncode == (0 if expected == 'matched' else 1), name
        assert report['status'] == expected, name
        stages.append(dict(name=name, passed=True, status=expected, breaks=report['breaks']))
        print(f'PASS {name}', flush=True)
        return report

    positive = check('normal', bundles, 'matched')
    broken = copy.deepcopy(bundles)
    table = rows(broken[1]['balances.csv'])
    table[1]['cash'] = str(Decimal(table[1]['cash']) + 1)
    broken[1]['balances.csv'] = encode(account.BALANCES, ([r[k] for k in account.BALANCES] for r in table))
    check('cash_break', broken, 'breaks')
    broken = copy.deepcopy(bundles)
    for bundle in broken:
        table = rows(bundle['balances.csv'])
        table.pop(1)
        bundle['balances.csv'] = encode(account.BALANCES, ([r[k] for k in account.BALANCES] for r in table))
    check('common_missing_day', broken, 'insufficient')
    broken = copy.deepcopy(bundles)
    table = rows(broken[1]['positions.csv'])
    table[0]['quantity'] = str(Decimal(table[0]['quantity']) + 100)
    broken[1]['positions.csv'] = encode(account.POSITIONS, ([r[k] for k in account.POSITIONS] for r in table))
    check('position_break', broken, 'breaks')
    assert all(hashlib.sha256((args.probe / name).read_bytes()).hexdigest() == sha for name, sha in before.items())
    evidence = dict(status='passed', stages=stages, sessions=len(snapshots), fills=len(fills),
        scope=manifest['scope'], execution_model=manifest['execution_model'],
        source_independence='same_simulator_two_reporting_paths_shared_closing_marks',
        opening_state='declared_empty_from_probe_config', events='declared_absent_no_corporate_action_probe',
        native_daily_cash_and_inventory_checked=True, real_broker_validation=False,
        real_execution_validation=False, institutional_readiness='not_established',
        original_inputs_unchanged=True, input_sha256=before,
        source_calendar_sha256=hashlib.sha256(raw_calendar).hexdigest(),
        implementation_sha256=hashlib.sha256(Path(account.__file__).read_bytes()).hexdigest(),
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        native_money_normalization=dict(precision='0.01', max_abs_allowed_residue='0.00000001', residues=normalization_residues),
        expected_snapshots=len(positive['expected_snapshot_dates']))
    (args.output / 'verification.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(dict(status='passed', sessions=len(snapshots), fills=len(fills), real_broker_validation=False)))


if __name__ == '__main__':
    main()
