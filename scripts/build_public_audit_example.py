"""Generate the public example with the production engine and synthetic inputs only."""
from pathlib import Path
import contextlib
import hashlib
import io
import json
import sys
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from qwb.audit_entry import main
from qwb.strategy_audit_ui import sample_files


def build():
    target = ROOT / 'website/public/examples'
    target.mkdir(parents=True, exist_ok=True)
    files = {source.name: source.data for source in sample_files()}
    with tempfile.TemporaryDirectory(prefix='temperq-public-example-') as directory:
        root = Path(directory)
        materials = root / 'materials'
        materials.mkdir()
        for name, content in files.items():
            (materials / name).write_bytes(content)
        output = root / 'audit'
        with contextlib.redirect_stdout(io.StringIO()):
            code = main(['audit', 'run', str(materials), '--out', str(output), '--opening-cash', '10000',
                         '--name', '合成示例：两日账户核对（非真实策略）', '--json'])
        summary = json.loads((output / 'summary.json').read_text('utf-8'))
        assert code == 0 and summary['books'] == 'verified'
        evidence = json.loads((output / 'evidence.json').read_text('utf-8'))
        assert abs(evidence['result']['modules']['attribution']['net_pnl'] - 183.9) < 1e-6
        report = (output / 'report.html').read_text('utf-8')
        banner = ('<aside style="padding:18px 24px;background:#edf4e8;border-bottom:1px solid #ccd9c2;'
                  'font:14px/1.8 sans-serif;color:#244b3b"><strong>合成示例 · 仅展示产品与核算口径</strong><br>'
                  '由TemperQ审计引擎读取内置两日合成材料生成，不是客户案例、实盘记录或未来收益证明。'
                  '短样本不能用于推断长期效果。<br>'
                  '<a href="/examples/temperq-synthetic-materials.zip">下载同一份合成材料</a> · '
                  '<a href="/">返回TemperQ</a></aside>')
        assert '<body>' in report
        report = report.replace('<body>', '<body>' + banner, 1)
        report_path = target / 'temperq-synthetic-report.html'
        report_path.write_text(report, encoding='utf-8')
        with zipfile.ZipFile(target / 'temperq-synthetic-materials.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        # Controlled omission: keep the same equity curve, deliberately drop one buy.
        bad_files = dict(files)
        trade_lines = files['trades.csv'].decode('utf-8').splitlines(keepends=True)
        dropped = [row for row in trade_lines if '000001.SZ' in row]
        assert len(dropped) == 1
        bad_files['trades.csv'] = ''.join(row for row in trade_lines if row not in dropped).encode('utf-8')
        bad_materials = root / 'missing-fill'
        bad_materials.mkdir()
        for name, content in bad_files.items():
            (bad_materials / name).write_bytes(content)
        bad_output = root / 'missing-fill-audit'
        with contextlib.redirect_stdout(io.StringIO()):
            bad_code = main(['audit', 'run', str(bad_materials), '--out', str(bad_output), '--opening-cash', '10000',
                             '--name', '合成反例：刻意遗漏一笔买入（非真实策略）', '--json'])
        bad_summary = json.loads((bad_output / 'summary.json').read_text('utf-8'))
        assert bad_code == 0 and bad_summary['books'] == 'needs_reconciliation'
        bad_report = (bad_output / 'report.html').read_text('utf-8')
        bad_banner = banner.replace('仅展示产品与核算口径', '刻意遗漏一笔买入的对照')
        bad_banner = bad_banner.replace('temperq-synthetic-materials.zip', 'temperq-synthetic-missing-fill-materials.zip')
        bad_report_path = target / 'temperq-synthetic-missing-fill.html'
        bad_report_path.write_text(bad_report.replace('<body>', '<body>' + bad_banner, 1), 'utf-8')
        with zipfile.ZipFile(target / 'temperq-synthetic-missing-fill-materials.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
            for name, content in sorted(bad_files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                archive.writestr(info, content)
        manifest = {'source_kind': 'synthetic_fixture', 'generator': 'scripts/build_public_audit_example.py',
                    'opening_cash': 10000, 'accounted_net_pnl': 183.9, 'books': summary['books'],
                    'input_digest': summary['input_digest'], 'result_digest': summary['result_digest'],
                    'inputs': {name: hashlib.sha256(content).hexdigest() for name, content in sorted(files.items())},
                    'negative_control': {'change': 'deliberately omit the 000001.SZ buy; all other inputs unchanged',
                                         'books': bad_summary['books'], 'input_digest': bad_summary['input_digest'],
                                         'inputs': {name: hashlib.sha256(content).hexdigest() for name, content in sorted(bad_files.items())}},
                    'outputs': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in
                                (report_path, target / 'temperq-synthetic-materials.zip', bad_report_path,
                                 target / 'temperq-synthetic-missing-fill-materials.zip')}}
        (target / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', 'utf-8')
        print('Public synthetic report and matching materials generated; books verified, net P&L 183.90 CNY.')


if __name__ == '__main__':
    build()
