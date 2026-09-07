param(
    [ValidateRange(15, 3600)]
    [int]$PollSeconds = 60,
    [string]$Database = "data/local/takeover.db"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$LogFile = Join-Path $ProjectRoot "data\local\discovery-worker.log"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    throw "Missing .env. Complete the Render deployment configuration first."
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "Missing project virtual environment: $Python"
}

# Load simple KEY=VALUE lines from the ignored local env file.  Browser login
# profiles remain on this collector computer and never go to GitHub or Render.
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) { continue }
    $parts = $line.Split("=", 2)
    if ($parts.Count -eq 2 -and $parts[0].Trim()) {
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1], "Process")
    }
}

if (-not [Environment]::GetEnvironmentVariable("WAMEIJI_PROFILE_DIR", "Process")) {
    [Environment]::SetEnvironmentVariable(
        "WAMEIJI_PROFILE_DIR", (Join-Path $ProjectRoot "data\browser_profiles\wameiji"), "Process"
    )
}
# The interactive Xianyu login command exports an isolated storage state.  Do
# not silently replace it with the default empty persistent profile: when both
# are present, the browser capture correctly prioritizes the profile and would
# reopen an unsigned-in Goofish window.  A user who intentionally maintains a
# dedicated persistent profile can still set XIANYU_PROFILE_DIR in .env.
$ExistingXianyuState = [Environment]::GetEnvironmentVariable("XIANYU_STATE_FILE", "Process")
if (-not $ExistingXianyuState -and -not [Environment]::GetEnvironmentVariable("GOOFISH_STATE_FILE", "Process")) {
    $DefaultXianyuState = Join-Path $ProjectRoot "data\xianyu_state.json"
    if (Test-Path -LiteralPath $DefaultXianyuState -PathType Leaf) {
        [Environment]::SetEnvironmentVariable("XIANYU_STATE_FILE", $DefaultXianyuState, "Process")
    }
}
[Environment]::SetEnvironmentVariable("BROWSER_ENABLED", "true", "Process")
[Environment]::SetEnvironmentVariable("PYTHONUTF8", "1", "Process")
[Environment]::SetEnvironmentVariable("PYTHONUNBUFFERED", "1", "Process")
[Environment]::SetEnvironmentVariable("PYTHONPATH", (Join-Path $ProjectRoot "src"), "Process")

if (-not [System.IO.Path]::IsPathRooted($Database)) {
    $Database = Join-Path $ProjectRoot $Database
}
$Database = [System.IO.Path]::GetFullPath($Database)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LogFile) | Out-Null

Push-Location $ProjectRoot
try {
    "$(Get-Date -Format s) discovery worker started; polling every $PollSeconds seconds" | Add-Content -LiteralPath $LogFile
    & $Python -m cd_monitor.cli discovery-worker --db $Database --snapshot-dir (Join-Path $ProjectRoot "data\snapshots") --poll-seconds $PollSeconds *>> $LogFile
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
