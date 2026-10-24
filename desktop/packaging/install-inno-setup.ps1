<#
.SYNOPSIS
  Resolve / install Inno Setup 6 so `ISCC.exe` is available to the LinkNote build.

.DESCRIPTION
  Resolution order (first hit wins, printed as `ISCC=<path>`):
    1. tools\innosetup\ISCC.exe              project-local portable install
    2. %LOCALAPPDATA%\Programs\Inno Setup 6  per-user winget install (preferred)
    3. %ProgramFiles(x86)%\Inno Setup 6      machine-wide install
    4. %ProgramFiles%\Inno Setup 6
    5. Registry InstallLocation of "Inno Setup 6" (HKLM + HKCU)
    6. ISCC.exe on PATH

  If nothing is found and -CheckOnly is not given, installs via
  `winget install --id JRSoftware.InnoSetup -e --scope user ...`; if winget is
  unavailable/fails, downloads the pinned official installer and runs it with
  /VERYSILENT /SUPPRESSMSGBOXES /DIR="<repo>\tools\innosetup" (no admin rights).

  Verified on this machine: Inno Setup 6.7.3 at
  C:\Users\<user>\AppData\Local\Programs\Inno Setup 6\ISCC.exe

.PARAMETER CheckOnly
  Only resolve/report; never install.

.PARAMETER ForceLocal
  Skip detection of existing installs and install a portable copy into
  tools\innosetup (useful for reproducible CI images).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File desktop\packaging\install-inno-setup.ps1
  powershell -ExecutionPolicy Bypass -File desktop\packaging\install-inno-setup.ps1 -CheckOnly
#>
[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$ForceLocal,
    [string]$Version = '6.7.3'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $PSScriptRoot) { $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
$DesktopDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Root = (Resolve-Path (Join-Path $DesktopDir '..')).Path
$LocalInno = Join-Path $Root 'tools\innosetup'
$LocalIscc = Join-Path $LocalInno 'ISCC.exe'
$Downloads = Join-Path $Root 'tools\downloads'
$SetupUrl = "https://files.jrsoftware.org/is/6/innosetup-$Version.exe"

function Step([string]$m) { Write-Host ''; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Info([string]$m) { Write-Host "  $m" }
function Warn([string]$m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }

function Find-Iscc {
    $candidates = New-Object System.Collections.Generic.List[string]
    $candidates.Add($LocalIscc)
    $candidates.Add((Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'))
    if (${env:ProgramFiles(x86)}) { $candidates.Add((Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')) }
    if ($env:ProgramFiles) { $candidates.Add((Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe')) }
    foreach ($key in @('HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
                       'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1',
                       'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1')) {
        try {
            $loc = (Get-ItemProperty -Path $key -Name InstallLocation -ErrorAction Stop).InstallLocation
            if ($loc) { $candidates.Add((Join-Path $loc 'ISCC.exe')) }
        } catch { }
    }
    $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates.Add($onPath.Source) }
    foreach ($c in $candidates) {
        if ($c -and (Test-Path -LiteralPath $c)) { return (Resolve-Path -LiteralPath $c).Path }
    }
    return $null
}

Step 'Resolve Inno Setup ISCC'
if ($ForceLocal -and -not (Test-Path $LocalIscc)) {
    Info "ForceLocal: installing portable Inno Setup under tools\innosetup"
} else {
    $found = Find-Iscc
    if ($found) {
        $ver = (Get-Item $found).VersionInfo.ProductVersion
        Info "ISCC: $found"
        Write-Host "ISCC=$found"
        exit 0
    }
}
if ($CheckOnly) {
    Warn 'ISCC not found (CheckOnly)'
    Write-Host 'ISCC='
    exit 1
}

Step "Install Inno Setup $Version"
$winget = Get-Command winget -ErrorAction SilentlyContinue
if ($winget -and -not $ForceLocal) {
    try {
        Info 'winget install JRSoftware.InnoSetup (per-user, silent) ...'
        & winget install --id JRSoftware.InnoSetup -e --scope user --silent `
            --accept-package-agreements --accept-source-agreements *>&1 | Out-Host
    } catch {
        Warn "winget failed: $($_.Exception.Message)"
    }
    $found = Find-Iscc
    if ($found) {
        Info "ISCC: $found"
        Write-Host "ISCC=$found"
        exit 0
    }
}

Info "downloading official installer: $SetupUrl"
New-Item -ItemType Directory -Force -Path $Downloads, $LocalInno | Out-Null
$setupExe = Join-Path $Downloads "innosetup-$Version.exe"
if (-not (Test-Path $setupExe)) {
    cmd /c "curl.exe -L --fail --retry 3 --connect-timeout 30 -o `"$setupExe`" `"$SetupUrl`" 2>&1" | Select-Object -Last 1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "failed to download $SetupUrl" }
}
Info 'running silent installer into tools\innosetup ...'
# The official installer is an Inno Setup package itself; /CURRENTUSER avoids admin.
$proc = Start-Process -FilePath $setupExe -Wait -PassThru -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
    '/CURRENTUSER', '/NOICONS', "/DIR=`"$LocalInno`""
)
Info "installer exit code: $($proc.ExitCode)"
$found = Find-Iscc
if (-not $found) { throw "Inno Setup installed but ISCC.exe not found (looked in $LocalInno and standard dirs)" }
Info "ISCC: $found"
Write-Host "ISCC=$found"
exit 0
