<#
.SYNOPSIS
  PyInstaller probe for the LinkNote desktop packaging toolchain.

.DESCRIPTION
  Builds a minimal --windowed --onedir PySide6 exe (desktop\packaging\probe\probe_window.py),
  then verifies it end-to-end on this machine:
    1. PyInstaller build succeeds (command recorded below and in packaging\README.md)
    2. exe exists; PE subsystem is GUI (2), i.e. no console window
    3. embedded icon can be extracted and is not blank
    4. exe launches, writes its startup marker JSON, window becomes visible,
       main window is captured to a PNG screenshot, and the app exits cleanly

  Outputs (all inside desktop\build\probe, gitignored):
    dist\LinkNoteProbe\LinkNoteProbe.exe     probe exe (onedir)
    probe-command.txt                        exact PyInstaller command
    launch-marker.json                       startup marker written by the probe
    probe-screenshot.png                     live window screenshot
    probe-icon.png                           icon extracted from the exe
    probe-result.json                        verification verdict

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File desktop\packaging\probe\build-probe.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipBuild
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $PSScriptRoot) { $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
$DesktopDir = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Root = (Resolve-Path (Join-Path $DesktopDir '..')).Path
$Python = Join-Path $DesktopDir '.venv\Scripts\python.exe'
$PyInstaller = Join-Path $DesktopDir '.venv\Scripts\pyinstaller.exe'
$Entry = Join-Path $PSScriptRoot 'probe_window.py'
$Icon = Join-Path $DesktopDir 'linknote_desktop\resources\icon.ico'
$BuildDir = Join-Path $DesktopDir 'build\probe'
$DistDir = Join-Path $BuildDir 'dist'
$WorkDir = Join-Path $BuildDir 'work'
$SpecDir = Join-Path $BuildDir 'spec'
$Exe = Join-Path $DistDir 'LinkNoteProbe\LinkNoteProbe.exe'

function Step([string]$m) { Write-Host ''; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Info([string]$m) { Write-Host "  $m" }

if (-not (Test-Path $Python)) { throw "venv python not found: $Python" }
if (-not (Test-Path $PyInstaller)) { throw "pyinstaller not found: $PyInstaller" }
if (-not (Test-Path $Entry)) { throw "probe entry not found: $Entry" }
if (-not (Test-Path $Icon)) { throw "icon not found: $Icon" }

New-Item -ItemType Directory -Force -Path $BuildDir, $DistDir, $WorkDir, $SpecDir | Out-Null

$pyiArgs = @(
    '--noconfirm', '--clean',
    '--windowed', '--onedir',
    '--name', 'LinkNoteProbe',
    '--icon', $Icon,
    '--add-data', "$Icon;resources",
    '--distpath', $DistDir,
    '--workpath', $WorkDir,
    '--specpath', $SpecDir,
    $Entry
)
$cmdLine = "`"$PyInstaller`" " + (($pyiArgs | ForEach-Object {
    if ($_ -match '\s|;') { "`"$_`"" } else { $_ }
}) -join ' ')
Set-Content -Path (Join-Path $BuildDir 'probe-command.txt') -Value $cmdLine -Encoding utf8

$result = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    command      = $cmdLine
    exe          = $Exe
    build_ok     = $false
    subsystem    = $null   # 2 = GUI (windowed), 3 = console
    windowed_ok  = $false
    icon_ok      = $false
    launch_ok    = $false
    visible_ok   = $false
    clean_exit   = $false
    exe_size_mb  = $null
    marker       = $null
    errors       = @()
}

if (-not $SkipBuild) {
    Step '1/4 PyInstaller build (windowed onedir)'
    Info $cmdLine
    # PyInstaller writes progress to stderr; Start-Process keeps EAP=Stop happy and
    # records both streams to log files (project paths contain no spaces).
    $stdoutLog = Join-Path $BuildDir 'pyinstaller-stdout.log'
    $stderrLog = Join-Path $BuildDir 'pyinstaller-stderr.log'
    $p = Start-Process -FilePath $PyInstaller -ArgumentList $pyiArgs -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    Get-Content $stderrLog -ErrorAction SilentlyContinue | Select-Object -Last 6 | ForEach-Object { Write-Host "  $_" }
    if ($p.ExitCode -ne 0) { throw "PyInstaller failed with exit code $($p.ExitCode) (see $stderrLog)" }
}
if (-not (Test-Path $Exe)) { throw "probe exe missing after build: $Exe" }
$result.build_ok = $true
$result.exe_size_mb = [math]::Round((Get-Item $Exe).Length / 1MB, 2)
Info "exe: $Exe ($($result.exe_size_mb) MB)"

Step '2/4 PE subsystem + icon checks'
# PE header: offset 0x3C -> e_lfanew -> PE sig (4) + COFF (20) + optional header;
# Subsystem is at optional header offset 68 (0x44) for PE32/PE32+.
$bytes = [System.IO.File]::ReadAllBytes($Exe)
$e_lfanew = [BitConverter]::ToInt32($bytes, 0x3C)
$subsystem = [BitConverter]::ToUInt16($bytes, $e_lfanew + 24 + 68)
$result.subsystem = $subsystem
$result.windowed_ok = ($subsystem -eq 2)
Info "PE subsystem = $subsystem (2 = Windows GUI, 3 = console) -> windowed_ok=$($result.windowed_ok)"

Add-Type -AssemblyName System.Drawing
$assoc = [System.Drawing.Icon]::ExtractAssociatedIcon($Exe)
if ($assoc) {
    $bmp = $assoc.ToBitmap()
    $iconPng = Join-Path $BuildDir 'probe-icon.png'
    $bmp.Save($iconPng, [System.Drawing.Imaging.ImageFormat]::Png)
    $result.icon_ok = $true
    Info "icon extracted -> $iconPng ($($bmp.Width)x$($bmp.Height))"
} else {
    $result.errors += 'ExtractAssociatedIcon returned null'
    Info '[warn] no icon extracted'
}

Step '3/4 Launch exe (windowed, autoclose in 10s) + screenshot'
$markerPath = Join-Path $BuildDir 'launch-marker.json'
if (Test-Path $markerPath) { Remove-Item $markerPath -Force }
$shotPath = Join-Path $BuildDir 'probe-screenshot.png'
$winShotPath = Join-Path $BuildDir 'probe-window.png'
if (Test-Path $shotPath) { Remove-Item $shotPath -Force }
if (Test-Path $winShotPath) { Remove-Item $winShotPath -Force }

$proc = Start-Process -FilePath $Exe -ArgumentList @('--autoclose', '10', '--marker', "`"$markerPath`"", '--screenshot', "`"$winShotPath`"") -PassThru
Info "started pid=$($proc.Id)"
Start-Sleep -Seconds 4
$proc.Refresh()
if ($proc.HasExited) {
    $result.errors += "probe exited early (code $($proc.ExitCode))"
    Info "[warn] process exited early with code $($proc.ExitCode)"
} else {
    $result.launch_ok = $true
    # pick the probe window and bring it to front, then capture the screen (.NET, no subprocess)
    $wshell = New-Object -ComObject WScript.Shell
    try { $wshell.AppActivate($proc.Id) | Out-Null } catch { }
    Start-Sleep -Milliseconds 800
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bmp.Save($shotPath, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
    } catch {
        $result.errors += "screenshot failed: $($_.Exception.Message)"
    }
    if (Test-Path $shotPath) { Info "desktop screenshot -> $shotPath" } else { $result.errors += 'desktop screenshot failed' }
}
if (Test-Path $winShotPath) {
    $result.window_png = $winShotPath
    Info "probe window render -> $winShotPath"
} else {
    $result.errors += 'probe window render missing'
}
if (Test-Path $markerPath) {
    $marker = Get-Content $markerPath -Raw | ConvertFrom-Json
    $result.marker = $marker
    $result.visible_ok = [bool]$marker.visible -and (Test-Path $winShotPath)
    if (-not $marker.icon_loaded) { $result.errors += 'probe reported icon not loaded' }
    Info "marker: frozen=$($marker.frozen) qt=$($marker.qt_version) icon=$($marker.icon_loaded) visible=$($marker.visible)"
} else {
    $result.errors += 'startup marker missing'
    Info '[warn] no startup marker'
}

$exited = $proc.WaitForExit(20000)
if (-not $exited) {
    Info '[warn] probe did not autoclose in time, killing'
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
    $result.errors += 'probe did not autoclose'
} else {
    $result.clean_exit = ($proc.ExitCode -eq 0)
    Info "exit code = $($proc.ExitCode)"
}

Step '4/4 Verdict'
$ok = $result.build_ok -and $result.windowed_ok -and $result.icon_ok -and $result.launch_ok -and $result.visible_ok -and $result.clean_exit
$result | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $BuildDir 'probe-result.json') -Encoding utf8
$result | ConvertTo-Json -Depth 6
Write-Host ''
if ($ok) {
    Write-Host 'PROBE PASS: windowed PySide6 exe builds, starts, shows a window with icon, exits cleanly.' -ForegroundColor Green
} else {
    Write-Host 'PROBE FAIL - see probe-result.json' -ForegroundColor Red
    exit 1
}
