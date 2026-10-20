# Guided initial connection. Every remote mutation requires explicit consent.
# Must run in an interactive Windows terminal, as the current user.
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Python = Join-Path $Root '.venv\Scripts\python.exe'
Set-Location $Root
if (-not (Test-Path $Python)) { throw 'Run SETUP.cmd first.' }
if (-not (Test-Path '.env')) { throw '.env is missing; run SETUP.cmd.' }

function Invoke-Calendar([string[]]$Parts) {
    & $Python -m itmo_local @Parts
    if ($LASTEXITCODE -ne 0) {
        throw 'The preceding step failed. Nothing further was automatically sent to iCloud.'
    }
}
function Read-CalendarJson([string[]]$Parts) {
    $Lines = & $Python -m itmo_local @Parts
    if ($LASTEXITCODE -ne 0) { throw 'The preceding read-only check failed.' }
    $Result = ($Lines -join "`n") | ConvertFrom-Json
    return $Result
}
function Confirm-Word([string]$Label, [string]$Word) {
    $Value = Read-Host $Label
    return $Value -ceq $Word
}

Write-Host ''
Write-Host 'ITMO Calendar Local - first-run wizard' -ForegroundColor Cyan
Write-Host 'Unofficial tool. Only your dedicated EMPTY iCloud calendar may be bound.'
Write-Host 'Do not send cookies, passwords, browser profile or .env to anyone.'
$Status = Read-CalendarJson -Parts @('status')
if ($Status.mode -ne 'myitmo' -or $Status.itmo_auth_method -ne 'browser') {
    throw 'Set MODE=myitmo and ITMO_AUTH_METHOD=browser in .env.'
}
if (-not $Status.icloud_apple_id_configured) {
    throw 'ICLOUD_APPLE_ID is empty in .env. Set it, then restart FIRST_RUN.cmd.'
}

if (-not $Status.icloud_credentials_saved) {
    Write-Host ''
    Write-Host '1/5 iCloud password'
    Write-Host 'Generate an app-specific password at https://account.apple.com/'
    Write-Host 'Enter it in the secure password prompt below; input is not displayed.'
    Invoke-Calendar -Parts @('icloud', 'set-password')
}
Write-Host ''
Write-Host '2/5 Checking the dedicated iCloud calendar...'
$Check = Read-CalendarJson -Parts @('icloud', 'check')
Write-Host ("Bound: {0}; events in selected calendar: {1}" -f $Check.bound, $Check.events_in_chosen_calendar)
if (-not $Check.bound) {
    if ([int]$Check.events_in_chosen_calendar -ne 0) {
        throw 'The selected iCloud calendar is NOT empty. Create a fresh empty calendar before binding.'
    }
    Write-Host 'The calendar must be yours, empty, and dedicated exclusively to ITMO.'
    if (-not (Confirm-Word 'Type BIND to authorize binding this empty calendar' 'BIND')) {
        throw 'Binding cancelled.'
    }
    Invoke-Calendar -Parts @('icloud', 'bind')
} else {
    Write-Host 'An existing binding was found. No new binding is attempted.'
}

Write-Host ''
Write-Host '3/5 Signing in at the official ITMO site...'
Write-Host 'An independent Edge/Chrome profile is used. Complete login and MFA yourself.'
$ShouldLogin = $true
if ($Status.browser_profile_exists) {
    $Answer = (Read-Host 'Reuse existing browser session without opening login window? [Y/n]').Trim()
    if ($Answer -eq '' -or $Answer -match '^[Yy]$') { $ShouldLogin = $false }
}
if ($ShouldLogin) { Invoke-Calendar -Parts @('browser', 'login') }
Write-Host 'Checking the complete date range without writing to the calendar...'
Invoke-Calendar -Parts @('browser', 'check')

Write-Host ''
Write-Host '4/5 Saving a verified timetable locally (SQLite only)...'
Invoke-Calendar -Parts @('sync-itmo')
Write-Host 'Getting a READ-ONLY preview of changes to iCloud...'
$Plan = Read-CalendarJson -Parts @('icloud', 'preview')
Write-Host ("CREATE={0} UPDATE={1} DELETE={2} UNCHANGED={3}" -f $Plan.create, $Plan.update, $Plan.delete, $Plan.unchanged)
if ([int]$Plan.delete -gt 0) {
    throw 'Preview contains deletions. First-run wizard refuses; review changes manually.'
}
Write-Host 'Compare the number of classes, dates and times with my.itmo.'
Write-Host 'Only the exact previously bound iCloud calendar will be modified.'
if (-not (Confirm-Word 'If everything matches, type PUBLISH to send changes to iCloud' 'PUBLISH')) {
    Write-Host 'Stopped safely at preview. iCloud remains unchanged.'
    exit 0
}
Invoke-Calendar -Parts @('icloud', 'push')
Write-Host 'Review your dedicated calendar on iPhone before enabling automation.'

Write-Host ''
Write-Host '5/5 Optional scheduled sync'
$Schedule = (Read-Host 'Enable run at Windows sign-in and four times per day? [y/N]').Trim()
if ($Schedule -match '^[Yy]$') {
    & (Join-Path $PSScriptRoot 'register-task.ps1')
} else {
    Write-Host 'You can enable later: powershell -File .\scripts\register-task.ps1'
}
Write-Host ''
Write-Host 'READY! Check classes in Apple Calendar on iPhone.' -ForegroundColor Green
Write-Host 'If the ITMO session expires, run: .\.venv\Scripts\python.exe -m itmo_local browser login'
