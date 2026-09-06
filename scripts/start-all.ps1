param(
    [ValidateRange(1, 65535)]
    [int]$Port = 9890
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$LocalScript = Join-Path $ProjectRoot "scripts\start-local.ps1"
$ReplicaScript = Join-Path $ProjectRoot "scripts\start-replica.ps1"

if (-not (Test-Path -LiteralPath $LocalScript -PathType Leaf)) {
    throw "Missing local server script: $LocalScript"
}
if (-not (Test-Path -LiteralPath $ReplicaScript -PathType Leaf)) {
    throw "Missing replica script: $ReplicaScript"
}

# Do not open duplicate local servers when the launcher is clicked twice.
$localListening = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $localListening) {
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $ProjectRoot -WindowStyle Normal -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $LocalScript, "-Port", $Port
    )
}

# The publisher is a Python child of its PowerShell window. Reuse it when it
# is already running so the same database is not uploaded twice.
$replicaRunning = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match "publish-replica\.py" }
if (-not $replicaRunning) {
    Start-Process -FilePath "powershell.exe" -WorkingDirectory $ProjectRoot -WindowStyle Normal -ArgumentList @(
        "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $ReplicaScript
    )
}

Start-Process "http://127.0.0.1:$Port"
