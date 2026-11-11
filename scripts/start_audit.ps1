param(
    [ValidateRange(1, 65535)][int]$Port = 8765,
    [switch]$NoBrowser
)
$ErrorActionPreference = 'Stop'
$auditRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$auditPython = Join-Path $auditRoot '.venv\Scripts\python.exe'
$auditUrl = "http://127.0.0.1:$Port/strategy-audits/new"

function Test-AuditPage {
    try {
        $auditResponse = Invoke-WebRequest -Uri $auditUrl -UseBasicParsing -TimeoutSec 3
        return ($auditResponse.StatusCode -eq 200 -and
            $auditResponse.Content.Contains('TemperQ') -and
            $auditResponse.Content.Contains('/strategy-audits/submit'))
    } catch { return $false }
}

if (-not (Test-Path -LiteralPath $auditPython)) {
    throw 'Project environment missing. Run install.bat first.'
}
if (-not (Test-AuditPage)) {
    $auditListeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    if ($auditListeners.Count -gt 0) {
        throw "Port $Port is occupied or the audit page is unhealthy. Existing processes were preserved."
    }
    $auditLogDir = Join-Path $auditRoot 'artifacts\audit-service'
    New-Item -ItemType Directory -Path $auditLogDir -Force | Out-Null
    $auditLogStamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $env:QWB_SKIP_PRELOAD = '1'
    $env:OPENBLAS_NUM_THREADS = '1'
    $env:OMP_NUM_THREADS = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $auditChild = Start-Process -FilePath $auditPython -ArgumentList @('-X', 'utf8', '-m', 'qwb.audit_app', '--no-browser', '--port', "$Port") -WorkingDirectory $auditRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $auditLogDir "$auditLogStamp.stdout.log") -RedirectStandardError (Join-Path $auditLogDir "$auditLogStamp.stderr.log") -PassThru
    $auditReady = $false
    for ($auditAttempt = 0; $auditAttempt -lt 45; $auditAttempt++) {
        if (Test-AuditPage) { $auditReady = $true; break }
        $auditChild.Refresh()
        if ($auditChild.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    if (-not $auditReady) {
        throw "Audit service did not become ready. Check logs in $auditLogDir."
    }
}
Write-Output "TemperQ ready: $auditUrl"
if (-not $NoBrowser) { Start-Process $auditUrl }
