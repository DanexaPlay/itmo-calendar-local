# Register tasks for the CURRENT Windows user. No administrator privileges.
# Windows DPAPI ties credentials to that user; do not run as SYSTEM.
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $Python)) { throw 'Python venv missing; run scripts/install.ps1 first.' }
$User = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$Action = New-ScheduledTaskAction -Execute $Python -Argument '-m itmo_local run' -WorkingDirectory $Root
$Triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User $User),
    (New-ScheduledTaskTrigger -Daily -At '07:00'),
    (New-ScheduledTaskTrigger -Daily -At '12:00'),
    (New-ScheduledTaskTrigger -Daily -At '18:00'),
    (New-ScheduledTaskTrigger -Daily -At '23:00')
)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
$Principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'Personal ITMO Calendar' -Action $Action `
    -Trigger $Triggers -Settings $Settings -Principal $Principal -Force | Out-Null
Write-Host 'Зарегистрировано: при входе в Windows и в 07:00/12:00/18:00/23:00 (если доступно).'
Write-Host 'Задание запускается только в сессии этого Windows-пользователя.'
