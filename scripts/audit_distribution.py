"""Explicit source boundary for the offline audit distribution."""
from pathlib import PurePosixPath

CORE = {'__init__', 'account_inputs', 'code_parse', 'corporate_actions', 'experiments',
        'metrics', 'netcost', 'review_store', 'securities', 'struct_costs', 'package', 'issues'}
PREFIXES = ('audit_', 'strategy_audit', 'strategy_import', 'governance_review',
            'execution_evidence', 'validation_evidence')
STATIC = {'audit-shell.css', 'governance-review.css', 'governance-review.js',
          'strategy-audit.css', 'experience.js', 'dm-sans.ttf', 'OFL.txt', 'favicon.svg',
          'joinquant_export.py'}


def include_source(name):
    if name.startswith('scripts/'):
        return PurePosixPath(name).name in {'build_windows_portable.py', 'audit_distribution.py',
            'check_audit_portable.py', 'check_windows_portable.py', 'repository_files.py', 'check_repository.py'}
    if name.startswith('examples/'):
        return name.startswith(('examples/account_reconciliation_synthetic/',
            'examples/factor_attribution_synthetic/', 'examples/risk_model_synthetic/'))
    if name.startswith('docs/'):
        return PurePosixPath(name).name.startswith(('AUDIT_', 'STRATEGY_AUDIT', 'ACCOUNT_RECONCILIATION',
            'FACTOR_ATTRIBUTION', 'RISK_MODEL_VALIDATION', 'FROZEN_VALIDATION', 'VALIDATION_EVIDENCE'))
    if not name.startswith('src/qwb/'):
        return True
    part = PurePosixPath(name.removeprefix('src/qwb/'))
    if len(part.parts) == 1:
        return part.suffix == '.py' and (part.stem in CORE or part.stem.startswith(PREFIXES))
    if part.parts[0] == 'rules':
        return part.suffix in {'.py', '.yaml'}
    if part.parts[0] == 'portfolio':
        return len(part.parts) == 2 and part.name in {'__init__.py', 'contracts.py', 'reference.py'}
    if part.parts[0] == 'static':
        return part.name in STATIC or (part.name.startswith('temperq_') and part.suffix == '.py')
    return False


PROJECT = '''[project]
name = "temperq"
version = "0.0.1"
description = "TemperQ offline strategy audit"
readme = "README.md"
license = {text = "AGPL-3.0-or-later"}
requires-python = ">=3.10"
dependencies = ["pandas>=2.0", "scipy>=1.11", "pyyaml>=6.0", "pyarrow>=14", "fastapi>=0.110", "pydantic>=2.0", "uvicorn>=0.29", "python-multipart>=0.0.9"]
[project.scripts]
temperq = "qwb.audit_entry:main"
temperq-audit = "qwb.audit_app:main"
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"
[tool.setuptools.packages.find]
where = ["src"]
[tool.setuptools.package-data]
"qwb.rules" = ["*.yaml"]
"qwb" = ["static/*"]
'''

README = '''# TemperQ · 策略审计版

1. 解压全部文件到可写文件夹，双击 start.bat。
2. 选择同一次回测的 CSV、代码或 ZIP，点击“上传并核对”。
3. 核对材料及佣金等参数，确认来源后点击“一键审计”。

代码必填；财务审计还需成交记录，聚宽可从持仓推算净变动但不能替代真实成交费用。
每日持仓、账户汇总、日志、订单、打分及补充行情为选填；缺项在报告中逐项说明。
只有代码时可做静态检查。文件保存在本机 runs/；备份后再升级，勿覆盖已有记录。

Windows 10/11 x64，内含独立 Python，无需安装 Python、Node、git 或行情插件。
不需要行情 token，不提供联网取数、模型训练、自动账户任务或下单。代码只静态读取。
需要补充行情时，使用自己的原平台导出或标准模板；材料授权和口径由用户核对。

命令行：temperq.bat audit run <材料目录> --out <新报告目录> --json
完整工作台是另一个发行版本，原有账户和安装目录不会被本包迁移或修改。
关闭启动窗口停止服务。本版使用独立应用，无纸面任务调度器。

源代码在 src/，AGPL-3.0-or-later，见 LICENSE；成本库与依赖许可见 THIRD_PARTY_NOTICES.md。
审计结论只覆盖提交的材料和已实施的检查，不认证策略收益、真实成交能力或机构准入。
'''
