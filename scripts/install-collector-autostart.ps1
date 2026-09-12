[CmdletBinding(SupportsShouldProcess)]
param(
    [switch]$StartNow,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$Identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

$Services = @(
    @{ Name = "Wameiji-Xianyu Local API"; Script = "scripts\start-local.ps1"; ExtraArgs = @("-Port", "9890", "-Database", "data/local/dual-market.db") },
    @{ Name = "Wameiji-Xianyu Replica Publisher"; Script = "scripts\start-replica.ps1"; ExtraArgs = @("-Database", "data/local/dual-market.db") },
    @{ Name = "Wameiji-Xianyu Discovery Worker"; Script = "scripts\start-discovery.ps1"; ExtraArgs = @("-Database", "data/local/dual-market.db") }
)

if ($Remove) {
    foreach ($service in $Services) {
        if (Get-ScheduledTask -TaskName $service.Name -ErrorAction SilentlyContinue) {
            if ($PSCmdlet.ShouldProcess($service.Name, "remove scheduled collector task")) {
                Unregister-ScheduledTask -TaskName $service.Name -Confirm:$false
            }
        }
    }
    return
}

foreach ($service in $Services) {
    $scriptPath = Join-Path $ProjectRoot $service.Script
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Missing service script: $scriptPath"
    }

    $argumentParts = @(
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-WindowStyle", "Hidden",
        "-File", ('"{0}"' -f $scriptPath)
    ) + $service.ExtraArgs
    $action = New-ScheduledTaskAction -Execute $PowerShell -Argument ($argumentParts -join " ") -WorkingDirectory $ProjectRoot
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $Identity
    $principal = New-ScheduledTaskPrincipal -UserId $Identity -LogonType Interactive -RunLevel Limited
    $settingsArgs = @{
        AllowStartIfOnBatteries = $true
        DontStopIfGoingOnBatteries = $true
        StartWhenAvailable = $true
        ExecutionTimeLimit = [TimeSpan]::Zero
        MultipleInstances = "IgnoreNew"
        RestartCount = 999
        RestartInterval = New-TimeSpan -Minutes 1
    }
    $settings = New-ScheduledTaskSettingsSet @settingsArgs

    if ($PSCmdlet.ShouldProcess($service.Name, "register interactive logon collector task")) {
        $registerArgs = @{
            TaskName = $service.Name
            Action = $action
            Trigger = $trigger
            Settings = $settings
            Principal = $principal
            Description = "Starts the local Wameiji-Xianyu collector service after interactive logon and restarts it if it exits."
            Force = $true
        }
        Register-ScheduledTask @registerArgs | Out-Null
    }

    if ($StartNow -and $PSCmdlet.ShouldProcess($service.Name, "start collector task now")) {
        Start-ScheduledTask -TaskName $service.Name
    }
}

Get-ScheduledTask |
    Where-Object { $_.TaskName -like "Wameiji-Xianyu *" } |
    Select-Object TaskName, State
