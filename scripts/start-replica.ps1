param(
    [int]$IntervalSeconds = 60,
    [string]$Database = "data/local/dual-market.db"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $ProjectRoot ".env"
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Publisher = Join-Path $ProjectRoot "scripts\publish-replica.py"

if (-not (Test-Path $EnvFile)) {
    throw "Missing .env. Complete the Render deployment configuration first."
}
if (-not (Test-Path $Python)) {
    throw "Missing project virtual environment: $Python"
}

# Load simple KEY=VALUE lines from the ignored local env file.
foreach ($line in Get-Content -LiteralPath $EnvFile) {
    if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) { continue }
    $parts = $line.Split("=", 2)
    if ($parts.Count -eq 2 -and $parts[0].Trim()) {
        [Environment]::SetEnvironmentVariable($parts[0].Trim(), $parts[1], "Process")
    }
}

$dbPath = Join-Path $ProjectRoot $Database
& $Python $Publisher --db $dbPath --interval-seconds $IntervalSeconds
exit $LASTEXITCODE
