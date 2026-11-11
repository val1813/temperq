# Process-local checks: never change the user's BLAS settings or hide native stderr.
function Invoke-TemperQPythonCheck {
    param([string]$Python, [string]$Code = "import lightgbm, qwb, ashare_tcost; print('lightgbm', lightgbm.__version__)")
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $Python
    # This helper accepts a Python statement, not shell commands.
    $info.Arguments = '-X utf8 -c "' + $Code.Replace('"', '\"') + '"'
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [System.Text.Encoding]::UTF8
    $info.StandardErrorEncoding = [System.Text.Encoding]::UTF8
    foreach ($name in @('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS')) {
        $info.EnvironmentVariables[$name] = '1'
    }
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        $process.WaitForExit()
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Output = ($stdout.Result + $stderr.Result).Trim()
        }
    } finally {
        $process.Dispose()
    }
}

function Test-TemperQMissingVCRuntime([string]$Message) {
    # A generic import error (including WinError 126) is not proof of a missing VC runtime.
    $dll = '(?i)\b(?:vcomp140|vcruntime140(?:_1)?|msvcp140)\.dll\b'
    $missing = '(?i)not found|could not find|cannot find|cannot load|could not load|failed to load|DLL load failed|\u627e\u4e0d\u5230|\u65e0\u6cd5\u52a0\u8f7d|\u672a\u627e\u5230'
    return ($Message -match $dll -and $Message -match $missing)
}
