$ErrorActionPreference = 'Stop'
Unregister-ScheduledTask -TaskName 'Personal ITMO Calendar' -Confirm:$false
Write-Host 'Задание удалено. Локальное расписание и секреты не удалены.'
