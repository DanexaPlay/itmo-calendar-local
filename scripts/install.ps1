# ITMO Calendar Local: install for the CURRENT Windows user. No admin rights.
# Keeps an existing .env, venv and all state in %LOCALAPPDATA% unchanged.
param([switch]$NonInteractive)
$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$PythonPath = Join-Path $Root '.venv\Scripts\python.exe'
$EnvFile = Join-Path $Root '.env'
$Template = Join-Path $Root '.env.example'
Set-Location $Root

function Write-EnvSetting([string[]]$Lines, [string]$Key, [string]$Value) {
    if ($Value -match '[\r\n]') { throw 'Invalid configuration value.' }
    $prefix = "$Key="
    for ($i = 0; $i -lt $Lines.Length; $i++) {
        if ($Lines[$i].StartsWith($prefix, [StringComparison]::Ordinal)) {
            $Lines[$i] = $prefix + $Value
            return
        }
    }
    throw "Setting $Key missing from .env.example."
}

if (-not (Test-Path $PythonPath)) {
    $Candidates = @(
        @{ Name = 'py'; Flags = @('-3.12') },
        @{ Name = 'py'; Flags = @('-3.13') },
        @{ Name = 'python'; Flags = @() }
    )
    $Selected = $null
    $VersionCheck = 'import sys; assert sys.version_info[:2] in ((3, 12), (3, 13)) and sys.maxsize > 2**32'
    foreach ($Candidate in $Candidates) {
        if (-not (Get-Command $Candidate.Name -ErrorAction SilentlyContinue)) { continue }
        try {
            $Flags = @($Candidate.Flags)
            & ($Candidate.Name) @Flags -c $VersionCheck 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $Selected = $Candidate
                break
            }
        } catch { continue }
    }
    if ($null -eq $Selected) {
        throw 'Python 3.12 or 3.13 (64-bit) not found. Install it from https://www.python.org/downloads/windows/ with Add Python to PATH, then run SETUP.cmd again.'
    }
    $Flags = @($Selected.Flags) + @('-m', 'venv', (Join-Path $Root '.venv'))
    Write-Host 'Creating isolated Python environment...'
    & ($Selected.Name) @Flags
    if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv.' }
}

if (-not (Test-Path $PythonPath)) { throw 'Expected .venv Python executable not found.' }
& $PythonPath -c 'import sys; assert sys.version_info[:2] in ((3, 12), (3, 13)) and sys.maxsize > 2**32'
if ($LASTEXITCODE -ne 0) { throw 'Existing .venv must be Python 3.12/3.13 64-bit; back up and recreate it manually.' }

Write-Host 'Installing dependencies in .venv (internet access required)...'
& $PythonPath -m pip install --disable-pip-version-check -e $Root
if ($LASTEXITCODE -ne 0) { throw 'pip installation failed. Check internet/proxy and Python.' }

if (-not (Test-Path $EnvFile)) {
    $Lines = [string[]][System.IO.File]::ReadAllLines($Template, [System.Text.Encoding]::UTF8)
    if (-not $NonInteractive) {
        Write-Host ''
        Write-Host 'Configuration: your Apple ID is an email address, NOT a password.'
        Write-Host 'Nothing is uploaded until the explicit first-run confirmation.'
        do {
            $AppleId = (Read-Host 'Apple Account email for iCloud Calendar').Trim()
            if ($AppleId -notmatch '^[^\s=@]+@[^\s=@]+$') { Write-Host 'Enter a valid Apple Account email.' }
        } until ($AppleId -match '^[^\s=@]+@[^\s=@]+$')
        $CalendarName = (Read-Host 'Exact name of your new EMPTY iCloud calendar [ITMO]').Trim()
        if (-not $CalendarName) { $CalendarName = 'ITMO' }
        if ($CalendarName.Length -gt 100 -or $CalendarName -match '[\r\n]') {
            throw 'Calendar name is too long or invalid.'
        }
        $Browser = (Read-Host 'Browser channel: msedge or chrome [msedge]').Trim().ToLowerInvariant()
        if (-not $Browser) { $Browser = 'msedge' }
        if ($Browser -notin @('msedge', 'chrome')) { throw 'Use msedge or chrome.' }
        Write-EnvSetting $Lines 'MODE' 'myitmo'
        Write-EnvSetting $Lines 'ITMO_AUTH_METHOD' 'browser'
        Write-EnvSetting $Lines 'ICLOUD_APPLE_ID' $AppleId
        Write-EnvSetting $Lines 'ICLOUD_CALENDAR_NAME' $CalendarName
        Write-EnvSetting $Lines 'BROWSER_CHANNEL' $Browser
    }
    # UTF-8 WITHOUT BOM for external editors; Python supports BOM too.
    [System.IO.File]::WriteAllLines($EnvFile, $Lines, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host '.env created. The .env file is ignored by Git.'
} else {
    Write-Host 'Existing .env found and preserved. No settings were overwritten.'
}

Write-Host ''
Write-Host 'Installation complete.' -ForegroundColor Green
if ($NonInteractive) {
    Write-Host 'No interactive configuration was performed.'
} else {
    Write-Host 'Next: create a separate EMPTY calendar in https://www.icloud.com/calendar/'
    Write-Host 'Then start FIRST_RUN.cmd to connect my.itmo and iCloud, review and confirm changes.'
}
