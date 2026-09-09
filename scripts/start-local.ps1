param(
    [ValidateRange(1, 65535)]
    [int]$Port = 9890,
    [string]$Database = 'data/local/dual-market.db'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw 'Project environment missing. Run uv venv .venv; uv pip install --python .venv/Scripts/python.exe -e ".[dev]" in the project folder first.'
}
if (-not [System.IO.Path]::IsPathRooted($Database)) {
    $Database = Join-Path $projectRoot $Database
}
$Database = [System.IO.Path]::GetFullPath($Database)

$localSettings = @{
    PYTHONUTF8 = '1'
    PYTHONPATH = (Join-Path $projectRoot 'src')
    CD_MONITOR_SCHEDULER = '0'
    BROWSER_ENABLED = 'false'
    CD_MONITOR_RUN_LIVE_TESTS = '0'
}
$savedSettings = @{}
foreach ($key in $localSettings.Keys) {
    $savedSettings[$key] = [System.Environment]::GetEnvironmentVariable($key, 'Process')
    [System.Environment]::SetEnvironmentVariable($key, $localSettings[$key], 'Process')
}

Push-Location $projectRoot
try {
    Write-Host "Local UI: http://127.0.0.1:$Port"
    Write-Host "Database: $Database"
    Write-Host 'Background scheduler is disabled. Press Ctrl+C to stop.'
    & $pythonPath -m cd_monitor.cli web --db $Database --host 127.0.0.1 --port $Port --static-dir (Join-Path $projectRoot 'web')
    if ($LASTEXITCODE -ne 0) {
        throw "Local server exited with code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
    foreach ($key in $savedSettings.Keys) {
        [System.Environment]::SetEnvironmentVariable($key, $savedSettings[$key], 'Process')
    }
}
