# Install the local dashboard helper for the current Windows user; run once.
$ErrorActionPreference = 'Stop'
$taskName = 'KrumpFinderUsageDashboard'
$repoPath = Split-Path -Parent $PSScriptRoot
$usageScript = Join-Path $PSScriptRoot 'usage.py'
$pythonPath = (Get-Command python.exe -ErrorAction Stop).Source
$pythonwPath = Join-Path (Split-Path -Parent $pythonPath) 'pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonwPath)) {
    throw "pythonw.exe was not found beside $pythonPath"
}

$existing = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $taskName
}
& $pythonPath $usageScript --prepare-auto-refresh
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the dashboard.' }

$userName = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$action = New-ScheduledTaskAction -Execute $pythonwPath -Argument ('"{0}" --serve' -f $usageScript) -WorkingDirectory $repoPath
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $userName
$principal = New-ScheduledTaskPrincipal -UserId $userName -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Refresh the private Krump usage dashboard when it is opened; listens on 127.0.0.1 only.' -Force | Out-Null
Start-ScheduledTask -TaskName $taskName
Write-Output "Auto-refresh installed. Open $repoPath\build\usage\index.html"
