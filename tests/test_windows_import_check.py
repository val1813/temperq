"""Exercise native stderr/exit handling on Windows without installing system software."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows installer check')
ROOT = Path(__file__).resolve().parents[1]


def check_script(tmp_path, body):
    helper = str(ROOT / 'scripts/windows_import_check.ps1').replace("'", "''")
    script = tmp_path / 'check.ps1'
    script.write_text("$ErrorActionPreference = 'Stop'\n. '" + helper + "'\n" + body, encoding='utf-8-sig')
    result = subprocess.run([shutil.which('powershell'), '-NoProfile', '-File', str(script)],
                            capture_output=True, encoding='utf-8', errors='replace', timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_import_check_captures_stderr_exit_and_bounds_threads_only_in_child(tmp_path):
    python = sys.executable.replace("'", "''")
    out = check_script(tmp_path, f"""
$env:OPENBLAS_NUM_THREADS = '7'
$result = Invoke-TemperQPythonCheck -Python '{python}' -Code "import os, sys; print(os.environ['OPENBLAS_NUM_THREADS'], os.environ['OMP_NUM_THREADS'], os.environ['MKL_NUM_THREADS']); print('MemoryError: cannot allocate', file=sys.stderr); sys.exit(9)"
@{{code=$result.ExitCode; output=$result.Output; parent=$env:OPENBLAS_NUM_THREADS; vc=(Test-TemperQMissingVCRuntime $result.Output)}} | ConvertTo-Json -Compress
""")
    assert out['code'] == 9 and out['parent'] == '7'
    assert '1 1 1' in out['output'] and 'MemoryError: cannot allocate' in out['output']
    assert out['vc'] is False


def test_only_identified_missing_vc_dlls_trigger_runtime_repair(tmp_path):
    out = check_script(tmp_path, """
@(
    (Test-TemperQMissingVCRuntime 'Could not find module vcomp140.dll'),
    (Test-TemperQMissingVCRuntime 'DLL load failed: VCRUNTIME140_1.dll not found'),
    (Test-TemperQMissingVCRuntime 'msvcp140.dll: failed to load'),
    (Test-TemperQMissingVCRuntime 'OpenBLAS error: Memory allocation still failed after 10 retries'),
    (Test-TemperQMissingVCRuntime 'ModuleNotFoundError: No module named qwb'),
    (Test-TemperQMissingVCRuntime 'OSError: [WinError 126] The specified module could not be found'),
    (Test-TemperQMissingVCRuntime 'vcomp140.dll loaded successfully')
) | ConvertTo-Json -Compress
""")
    assert out == [True, True, True, False, False, False, False]


def test_import_check_success_preserves_stdout(tmp_path):
    python = sys.executable.replace("'", "''")
    out = check_script(tmp_path, f"""
Invoke-TemperQPythonCheck -Python '{python}' -Code "print('check passed')" | ConvertTo-Json -Compress
""")
    assert out == {'ExitCode': 0, 'Output': 'check passed'}
