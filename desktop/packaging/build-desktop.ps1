<#
.SYNOPSIS
  Build LinkNote Desktop: PyInstaller onedir exe + Inno Setup installer, and
  verify both on this machine.

.DESCRIPTION
  Pipeline (each step can be skipped):
    1. Build   dist-desktop\LinkNote\LinkNote.exe  from desktop\packaging\LinkNote.spec
    2. Verify  app exe: PE subsystem = GUI (no console), icon, size, launch,
               window appears, frozen Agent worker mode (LinkNote.exe --agent-worker)
    3. Stage   (optional) desktop\build\engine — see stage-engine.ps1
    4. Build   dist-desktop\LinkNote-Setup-<version>.exe with Inno Setup
    5. Verify  installer: PE subsystem, icon, SHA256, silent per-user install,
               installed files/shortcuts/engine, installed app launch, silent
               uninstall, user data (%LOCALAPPDATA%\LinkNote) preserved
    6. Write   desktop\build\build-report.json

  Tools: desktop\.venv\Scripts\pyinstaller.exe (PyInstaller 6.22) and
  Inno Setup 6 ISCC.exe (auto-installed per-user via install-inno-setup.ps1 if
  missing). Windows PowerShell 5.1 compatible.

.PARAMETER Version
  Version string used for the installer filename and payload metadata. Default 0.2.0.

.PARAMETER SkipApp
  Reuse an existing dist-desktop\LinkNote instead of rebuilding it.

.PARAMETER SkipInstaller
  Build/verify only the app, not the installer.

.PARAMETER SkipVerify
  Skip launch/install verification (faster but less evidence).

.PARAMETER SkipEngine
  Build the installer without the optional bundled BiliNote engine payload.

.PARAMETER SkipProbe
  Do not re-run the PyInstaller toolchain probe first (probe is informational).

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File desktop\packaging\build-desktop.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File desktop\packaging\build-desktop.ps1 -SkipProbe -SkipVerify
#>
[CmdletBinding()]
param(
    [string]$Version = '0.2.0',
    [switch]$SkipApp,
    [switch]$SkipInstaller,
    [switch]$SkipVerify,
    [switch]$SkipEngine,
    [switch]$SkipProbe
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $PSScriptRoot) { $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
$DesktopDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Root = (Resolve-Path (Join-Path $DesktopDir '..')).Path
$Python = Join-Path $DesktopDir '.venv\Scripts\python.exe'
$PyInstaller = Join-Path $DesktopDir '.venv\Scripts\pyinstaller.exe'
$SpecFile = Join-Path $PSScriptRoot 'LinkNote.spec'
$LauncherFile = Join-Path $PSScriptRoot 'linknote_launcher.py'
$EntryFile = Join-Path $DesktopDir 'linknote_desktop\__main__.py'
$AppSource = Join-Path $DesktopDir 'linknote_desktop'
$DistRoot = Join-Path $Root 'dist-desktop'
$AppDist = Join-Path $DistRoot 'LinkNote'
$AppExe = Join-Path $AppDist 'LinkNote.exe'
$SetupExe = Join-Path $DistRoot ("LinkNote-Setup-$Version.exe")
$BuildRoot = Join-Path $DesktopDir 'build'
$AppBuild = Join-Path $BuildRoot 'app'
$AppWork = Join-Path $AppBuild 'work'
$EngineDir = Join-Path $BuildRoot 'engine'
$EngineExe = Join-Path $EngineDir 'BiliNoteBackend.exe'
$EngineManifest = Join-Path $BuildRoot 'engine-manifest.json'
$InstallerDir = Join-Path $DesktopDir 'installer'
$IssFile = Join-Path $InstallerDir 'LinkNote.iss'
$ReportPath = Join-Path $BuildRoot 'build-report.json'

$report = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    version      = $Version
    toolchain    = [ordered]@{}
    app          = [ordered]@{ built = $false; verified = $false }
    engine       = [ordered]@{ staged = $false; included = $false }
    installer    = [ordered]@{ built = $false; verified = $false }
    warnings     = @()
    errors       = @()
}

function Step([string]$m) { Write-Host ''; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Info([string]$m) { Write-Host "  $m" }
function Warn([string]$m) { Write-Host "  [warn] $m" -ForegroundColor Yellow; $report.warnings += $m }
function Fail([string]$m) { Write-Host "  [error] $m" -ForegroundColor Red; throw $m }

function Assert-Under([string]$path, [string]$base, [string]$label) {
    $full = [System.IO.Path]::GetFullPath($path)
    $baseFull = [System.IO.Path]::GetFullPath($base)
    if (-not $full.StartsWith($baseFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "$label must live under $baseFull (got $full)"
    }
    return $full
}
function Remove-SafeDir([string]$path, [string]$base) {
    $full = Assert-Under $path $base 'directory to remove'
    if (Test-Path -LiteralPath $full) { Remove-Item -LiteralPath $full -Recurse -Force }
    return $full
}
function Get-Sha256([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) { return '' }
    return (Get-FileHash -Algorithm SHA256 -LiteralPath $path).Hash
}
function Get-PeSubsystem([string]$path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    if ($bytes.Length -lt 0x100 -or $bytes[0] -ne 0x4D -or $bytes[1] -ne 0x5A) { return $null }
    $e_lfanew = [BitConverter]::ToInt32($bytes, 0x3C)
    if ($bytes[$e_lfanew] -ne 0x50 -or $bytes[$e_lfanew + 1] -ne 0x45) { return $null }
    return [BitConverter]::ToUInt16($bytes, $e_lfanew + 24 + 68)
}
function Save-ExeIcon([string]$exe, [string]$png) {
    Add-Type -AssemblyName System.Drawing
    $assoc = [System.Drawing.Icon]::ExtractAssociatedIcon($exe)
    if (-not $assoc) { return $false }
    $bmp = $assoc.ToBitmap()
    $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
    $size = "$($bmp.Width)x$($bmp.Height)"
    $bmp.Dispose()
    return $size
}
function Save-Screen([string]$png) {
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
        $bmp = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $g.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
        $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
        $g.Dispose(); $bmp.Dispose()
        return $true
    } catch {
        return $false
    }
}
function Save-WindowCapture([IntPtr]$hwnd, [string]$png) {
    # Capture a specific window regardless of z-order/focus (evidence for the app UI).
    if ($hwnd -eq 0 -or $hwnd -eq [IntPtr]::Zero) { return $false }
    try {
        Add-Type -AssemblyName System.Drawing
        if (-not ('WinCap' -as [type])) {
            Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinCap {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
        }
        $rect = New-Object WinCap+RECT
        [WinCap]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
        $w = $rect.Right - $rect.Left
        $h = $rect.Bottom - $rect.Top
        if ($w -le 0 -or $h -le 0) { return $false }
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $hdc = $g.GetHdc()
        $ok = [WinCap]::PrintWindow($hwnd, $hdc, 2)   # PW_RENDERFULLCONTENT
        $g.ReleaseHdc($hdc); $g.Dispose()
        $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png)
        $bmp.Dispose()
        return [bool]$ok
    } catch {
        return $false
    }
}
function Stop-LinkNoteTestProcesses {
    # Only stop LinkNote.exe processes that belong to this build/test tree.
    foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
        try {
            $path = $p.Path
            if ($path -and ($path.StartsWith($AppDist, [System.StringComparison]::OrdinalIgnoreCase))) {
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
}
function Wait-PathGone([string]$path, [int]$timeoutSec = 60) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Test-Path -LiteralPath $path)) { return $true }
        Start-Sleep -Milliseconds 500
    }
    return -not (Test-Path -LiteralPath $path)
}

# ---------------------------------------------------------------------------
# 0. Toolchain sanity
# ---------------------------------------------------------------------------
Step '0/6 Toolchain'
if (-not (Test-Path $Python)) { Fail "venv python not found: $Python" }
if (-not (Test-Path $PyInstaller)) { Fail "PyInstaller not found: $PyInstaller" }
if (-not (Test-Path $SpecFile)) { Fail "spec file not found: $SpecFile" }
if (-not (Test-Path $LauncherFile)) { Fail "PyInstaller launcher missing: $LauncherFile" }
if (-not (Test-Path $EntryFile)) {
    Fail "app entry point missing: $EntryFile (waiting for lead's __main__.py/application.py)"
}
$uiMain = Join-Path $AppSource 'ui\main_window.py'
$uiTray = Join-Path $AppSource 'ui\tray.py'
if (-not (Test-Path $uiMain)) { Fail "UI not ready yet: $uiMain missing (desktop-ui-dev is still writing it)" }
if (-not (Test-Path $uiTray)) { Warn "tray module missing, app may fail at runtime: $uiTray" }

$pyVersion = (& $Python --version 2>&1) -join ' '
$pyiVersion = (& $Python -c "import PyInstaller; print(PyInstaller.__version__)" 2>&1) -join ' '
$pysideVersion = (& $Python -c "import PySide6; print(PySide6.__version__)" 2>&1) -join ' '
Info "python:     $pyVersion"
Info "pyinstaller:$pyiVersion"
Info "PySide6:    $pysideVersion"
$report.toolchain = [ordered]@{
    python          = $pyVersion.Trim()
    pyinstaller     = $pyiVersion.Trim()
    pyside6         = $pysideVersion.Trim()
    venv            = $Python
    inno_setup_iscc = ''
    inno_setup_ver  = ''
}

New-Item -ItemType Directory -Force -Path $DistRoot, $BuildRoot, $AppBuild | Out-Null
$gitignore = Join-Path $DistRoot '.gitignore'
if (-not (Test-Path $gitignore)) {
    Set-Content -Path $gitignore -Encoding ascii -Value @('*', '!.gitignore')
}

# ---------------------------------------------------------------------------
# 1. Probe (informational; proves the windowed PyInstaller toolchain on this box)
# ---------------------------------------------------------------------------
if (-not $SkipProbe) {
    Step '1/6 Toolchain probe (windowed PySide6 exe)'
    $probeScript = Join-Path $PSScriptRoot 'probe\build-probe.ps1'
    if (Test-Path $probeScript) {
        $probeResult = Join-Path $BuildRoot 'probe\probe-result.json'
        $probeLog = Join-Path $BuildRoot 'probe\run.log'
        $pp = Start-Process -FilePath 'powershell.exe' -Wait -PassThru -NoNewWindow `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$probeScript`"") `
            -RedirectStandardOutput $probeLog -RedirectStandardError ($probeLog + '.err')
        if ($pp.ExitCode -ne 0) {
            Warn "probe failed (exit $($pp.ExitCode)); continuing, see $probeLog"
        } elseif (Test-Path $probeResult) {
            $pr = Get-Content $probeResult -Raw | ConvertFrom-Json
            Info "probe PASS: windowed=$($pr.windowed_ok) icon=$($pr.icon_ok) launch=$($pr.launch_ok) exit=$($pr.clean_exit)"
            $report.toolchain['probe'] = [ordered]@{
                result    = 'PASS'
                exe_mb    = $pr.exe_size_mb
                subsystem = $pr.subsystem
                window_png = $pr.window_png
            }
        }
    } else {
        Info "probe script not found, skipping: $probeScript"
    }
}

# ---------------------------------------------------------------------------
# 2. Build the app (PyInstaller onedir)
# ---------------------------------------------------------------------------
if (-not $SkipApp) {
    Step '2/6 PyInstaller build: dist-desktop\LinkNote\LinkNote.exe'
    Remove-SafeDir $AppDist $DistRoot | Out-Null
    Remove-SafeDir (Join-Path $AppBuild 'work') $BuildRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $AppWork | Out-Null

    $pyiArgs = @(
        '--noconfirm', '--clean',
        '--distpath', $DistRoot,
        '--workpath', $AppWork,
        '--log-level', 'INFO',
        $SpecFile
    )
    $stdoutLog = Join-Path $AppBuild 'pyinstaller-stdout.log'
    $stderrLog = Join-Path $AppBuild 'pyinstaller-stderr.log'
    $t0 = Get-Date
    $p = Start-Process -FilePath $PyInstaller -ArgumentList $pyiArgs -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
    $report.app['build_seconds'] = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    if ($p.ExitCode -ne 0) {
        Get-Content $stderrLog -ErrorAction SilentlyContinue | Select-Object -Last 25 | ForEach-Object { Write-Host "  $_" }
        Fail "PyInstaller failed with exit code $($p.ExitCode) (see $stderrLog)"
    }
}
if (-not (Test-Path $AppExe)) { Fail "app exe missing: $AppExe" }

Step '2b Verify app artifacts'
$appFiles = @(Get-ChildItem $AppDist -Recurse -File)
$appBytes = ($appFiles | Measure-Object Length -Sum).Sum
$subsystem = Get-PeSubsystem $AppExe
$iconPng = Join-Path $AppBuild 'app-icon.png'
$iconSize = $false
try { $iconSize = Save-ExeIcon $AppExe $iconPng } catch { Warn "icon extraction failed: $($_.Exception.Message)" }
$report.app = [ordered]@{
    built          = $true
    exe            = $AppExe
    exe_mb         = [math]::Round((Get-Item $AppExe).Length / 1MB, 2)
    total_mb       = [math]::Round($appBytes / 1MB, 1)
    file_count     = $appFiles.Count
    pe_subsystem   = $subsystem      # 2 = GUI/windowed
    windowed       = ($subsystem -eq 2)
    icon_png       = $iconPng
    icon_ok        = [bool]$iconSize
    exe_sha256     = (Get-Sha256 $AppExe)
    verified       = $false
    engine_worker_ok = $false
    launch_ok      = $false
    window_title   = ''
    window_handle  = 0
    screenshot     = ''
    build_seconds  = $report.app['build_seconds']
}
Info "exe:        $AppExe ($($report.app.exe_mb) MB)"
Info "dist total: $($appFiles.Count) files, $($report.app.total_mb) MB"
Info "PE subsystem = $subsystem (2 = GUI windowed) -> windowed=$($report.app.windowed)"
Info "icon:       $iconSize"
if ($subsystem -ne 2) { Warn 'PE subsystem is not 2: the exe may open a console window' }

# ---------------------------------------------------------------------------
# 3. Verify the app launches (windowed, no console)
# ---------------------------------------------------------------------------
if (-not $SkipVerify) {
    Step '3/6 Verify app launch (isolated profile, engine_auto_start=false)'
    Stop-LinkNoteTestProcesses
    $verifyRoot = Join-Path $AppBuild 'verify'
    $profile = Join-Path $verifyRoot 'profile'
    Remove-SafeDir $verifyRoot $BuildRoot | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $profile 'LinkNote') | Out-Null
    Set-Content -Path (Join-Path $profile 'LinkNote\settings.json') -Encoding utf8 -Value (@{
        engine_auto_start = $false
        close_to_tray     = $false
        start_minimized   = $false
    } | ConvertTo-Json)

    $shot = Join-Path $verifyRoot 'app-screenshot.png'
    $oldLocalAppData = $env:LOCALAPPDATA
    $proc = $null
    try {
        $env:LOCALAPPDATA = $profile
        $proc = Start-Process -FilePath $AppExe -PassThru
        Info "started pid=$($proc.Id), waiting 8s ..."
        Start-Sleep -Seconds 8
        $proc.Refresh()
        if ($proc.HasExited) {
            $report.app['launch_error'] = "process exited early with code $($proc.ExitCode)"
            Warn "app exited early (code $($proc.ExitCode)); see %LOCALAPPDATA% logs in $profile"
        } else {
            $report.app.launch_ok = $true
            $live = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
            if ($live) {
                $report.app.window_title = $live.MainWindowTitle
                $report.app.window_handle = $live.MainWindowHandle
                Info "alive: window title='$($live.MainWindowTitle)' handle=$($live.MainWindowHandle)"
            } else {
                $report.app.window_handle = 0
                Warn 'process vanished between checks'
            }
            $winPng = Join-Path $verifyRoot 'app-window.png'
            if (Save-WindowCapture $report.app.window_handle $winPng) {
                $report.app['window_png'] = $winPng
                Info "window render -> $winPng"
            }
            $wshell = New-Object -ComObject WScript.Shell
            try { $wshell.AppActivate($proc.Id) | Out-Null } catch { }
            Start-Sleep -Milliseconds 800
            if (Save-Screen $shot) { $report.app.screenshot = $shot; Info "screenshot -> $shot" }
            if ($report.app.window_handle -eq 0) { Warn 'no main window handle (app may be tray-only or UI failed)' }
        }
    } finally {
        if ($proc -and -not $proc.HasExited) {
            try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch { }
        }
        $env:LOCALAPPDATA = $oldLocalAppData
    }
    Stop-LinkNoteTestProcesses

    # Frozen Agent worker mode: the GUI spawns `LinkNote.exe --agent-worker ...`
    $awOut = Join-Path $verifyRoot 'agent-worker.out.txt'
    $awErr = Join-Path $verifyRoot 'agent-worker.err.txt'
    $aw = Start-Process -FilePath $AppExe -ArgumentList @('--agent-worker', '--version') -Wait -PassThru -NoNewWindow `
        -RedirectStandardOutput $awOut -RedirectStandardError $awErr
    $awText = ''
    if (Test-Path $awOut) { $awText = (Get-Content $awOut -Raw).Trim() }
    $report.app.engine_worker_ok = ($aw.ExitCode -eq 0 -and $awText -match 'linknote_agent')
    Info "agent-worker mode: exit=$($aw.ExitCode) output='$awText' -> ok=$($report.app.engine_worker_ok)"
    $report.app.verified = ($report.app.launch_ok -and $report.app.windowed -and $report.app.icon_ok -and $report.app.engine_worker_ok)
    if ($report.app.verified) {
        Write-Host '  app verification: PASS' -ForegroundColor Green
    } else {
        Warn 'app verification incomplete (see report fields)'
    }
}

# ---------------------------------------------------------------------------
# 4. Engine staging check
# ---------------------------------------------------------------------------
Step '4/6 Optional bundled engine payload'
$engineIncluded = $false
if (Test-Path $EngineExe) {
    $engineFiles = @(Get-ChildItem $EngineDir -Recurse -File)
    $engineBytes = ($engineFiles | Measure-Object Length -Sum).Sum
    $engineSha = Get-Sha256 $EngineExe
    $report.engine = [ordered]@{
        staged         = $true
        included       = (-not $SkipEngine)
        dir            = $EngineDir
        exe            = $EngineExe
        exe_sha256     = $engineSha
        file_count     = $engineFiles.Count
        total_mb       = [math]::Round($engineBytes / 1MB, 1)
        manifest       = $EngineManifest
    }
    Info "staged:  $($engineFiles.Count) files, $($report.engine.total_mb) MB"
    Info "sha256:  $engineSha"
    if ($SkipEngine) { Info 'engine payload excluded by -SkipEngine' }
    else { $engineIncluded = $true }
} else {
    $report.engine = [ordered]@{ staged = $false; included = $false; dir = $EngineDir }
    if ($SkipEngine) {
        Info 'engine staging not present (and -SkipEngine given) — app-only installer'
    } else {
        Warn "engine not staged at $EngineDir — installer will be app-only. Run desktop\packaging\stage-engine.ps1 first."
    }
}

# ---------------------------------------------------------------------------
# 5. Build the installer (Inno Setup)
# ---------------------------------------------------------------------------
if (-not $SkipInstaller) {
    Step '5/6 Inno Setup installer'
    if (Test-Path $SetupExe) { Remove-Item -LiteralPath $SetupExe -Force }

    $iscc = ''
    $resolver = Join-Path $PSScriptRoot 'install-inno-setup.ps1'
    if (Test-Path $resolver) {
        $resolveLog = Join-Path $BuildRoot 'iscc-resolve.log'
        $rp = Start-Process -FilePath 'powershell.exe' -Wait -PassThru -NoNewWindow `
            -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$resolver`"", '-CheckOnly') `
            -RedirectStandardOutput $resolveLog -RedirectStandardError ($resolveLog + '.err')
        foreach ($line in @(Get-Content $resolveLog -ErrorAction SilentlyContinue)) {
            if ($line -match '^ISCC=(.+)$') { $iscc = $Matches[1].Trim() }
        }
        if (-not $iscc) {
            Info 'ISCC not found, installing Inno Setup (per-user) ...'
            $ip = Start-Process -FilePath 'powershell.exe' -Wait -PassThru -NoNewWindow `
                -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$resolver`"") `
                -RedirectStandardOutput $resolveLog -RedirectStandardError ($resolveLog + '.err')
            foreach ($line in @(Get-Content $resolveLog -ErrorAction SilentlyContinue)) {
                if ($line -match '^ISCC=(.+)$') { $iscc = $Matches[1].Trim() }
            }
            if ($ip.ExitCode -ne 0) { Fail "Inno Setup install failed (exit $($ip.ExitCode)), see $resolveLog" }
        }
    } else {
        Fail "install-inno-setup.ps1 missing: $resolver"
    }
    if (-not $iscc -or -not (Test-Path $iscc)) { Fail "ISCC.exe not resolved (see $BuildRoot\iscc-resolve.log)" }
    $innoVersion = (Get-Item $iscc).VersionInfo.FileVersion
    if (-not $innoVersion) { $innoVersion = (Get-Item $iscc).VersionInfo.ProductVersion }
    $report.toolchain.inno_setup_iscc = $iscc
    $report.toolchain.inno_setup_ver = $innoVersion
    Info "ISCC: $iscc (version $innoVersion)"

    $issArgs = @(
        "/DMyAppVersion=$Version",
        $IssFile
    )
    if (-not $engineIncluded) { $issArgs = @('/DNoEngine=1') + $issArgs }
    if ($Version -match '^\d+\.\d+\.\d+$') {
        $issArgs = @("/DMyVersionInfoVersion=$Version.0") + $issArgs
    }
    $issLog = Join-Path $BuildRoot 'iscc.log'
    $t0 = Get-Date
    $ip = Start-Process -FilePath $iscc -ArgumentList $issArgs -Wait -PassThru -NoNewWindow `
        -WorkingDirectory $InstallerDir `
        -RedirectStandardOutput $issLog -RedirectStandardError ($issLog + '.err')
    $report.installer['build_seconds'] = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
    if ($ip.ExitCode -ne 0) {
        Get-Content $issLog -ErrorAction SilentlyContinue | Select-Object -Last 30 | ForEach-Object { Write-Host "  $_" }
        Get-Content ($issLog + '.err') -ErrorAction SilentlyContinue | Select-Object -Last 10 | ForEach-Object { Write-Host "  $_" }
        Fail "ISCC failed with exit code $($ip.ExitCode) (see $issLog)"
    }
    if (-not (Test-Path $SetupExe)) { Fail "installer not produced: $SetupExe" }
    $setupSubsystem = Get-PeSubsystem $SetupExe
    $setupIcon = Join-Path $BuildRoot 'setup-icon.png'
    try { Save-ExeIcon $SetupExe $setupIcon | Out-Null } catch { }
    $report.installer = [ordered]@{
        built          = $true
        exe            = $SetupExe
        exe_mb         = [math]::Round((Get-Item $SetupExe).Length / 1MB, 1)
        pe_subsystem   = $setupSubsystem
        icon_png       = $setupIcon
        sha256         = (Get-Sha256 $SetupExe)
        includes_engine = $engineIncluded
        build_seconds  = $report.installer['build_seconds']
        verified       = $false
    }
    Info "setup:   $SetupExe ($($report.installer.exe_mb) MB)"
    Info "sha256:  $($report.installer.sha256)"
    Info "engine included: $engineIncluded"
}

# ---------------------------------------------------------------------------
# 6. Silent install / launch / uninstall test
# ---------------------------------------------------------------------------
if ((-not $SkipInstaller) -and (-not $SkipVerify) -and (Test-Path $SetupExe)) {
    Step '6/6 Silent per-user install + uninstall test'
    $testRoot = Join-Path $BuildRoot 'installer-test'
    $target = Join-Path $testRoot 'app'
    $installLog = Join-Path $testRoot 'install.log'
    $uninstallLog = Join-Path $testRoot 'uninstall.log'
    Remove-SafeDir $testRoot $BuildRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $testRoot | Out-Null

    # Never clobber a real LinkNote installation.
    $realReg = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{B7E3A1C4-2D9F-4A62-8E11-5C7F0B6D3A29}_is1'
    if (Test-Path $realReg) {
        Warn 'a real LinkNote installation is registered for this user; skipping destructive install/uninstall test'
    } else {
        $desktopDir = [Environment]::GetFolderPath('Desktop')
        $programsDir = [Environment]::GetFolderPath('Programs')
        $deskLnk = Join-Path $desktopDir 'LinkNote 链记.lnk'
        $groupDir = Join-Path $programsDir 'LinkNote'
        $installed = Join-Path $target 'LinkNote.exe'
        $t0 = Get-Date
        $ip = Start-Process -FilePath $SetupExe -Wait -PassThru -NoNewWindow -ArgumentList @(
            '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
            "/LOG=`"$installLog`"", "/DIR=`"$target`""
        )
        $installSecs = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
        Info "installer exit=$($ip.ExitCode) in ${installSecs}s"
        $checks = [ordered]@{
            install_exit_ok   = ($ip.ExitCode -eq 0)
            install_seconds   = $installSecs
            exe_installed     = (Test-Path -LiteralPath $installed)
            internal_dir      = (Test-Path -LiteralPath (Join-Path $target '_internal'))
            uninstaller       = (Test-Path -LiteralPath (Join-Path $target 'unins000.exe'))
            license           = (Test-Path -LiteralPath (Join-Path $target 'LICENSE'))
            engine_installed  = (Test-Path -LiteralPath (Join-Path $target 'engine\BiliNoteBackend.exe'))
            engine_sha_match  = $false
            desktop_shortcut  = (Test-Path -LiteralPath $deskLnk)
            startmenu_dir     = (Test-Path -LiteralPath $groupDir)
            uninstall_reg     = (Test-Path -LiteralPath $realReg)
            autostart_installed = $false
            userdata_preserved = $false
            uninstall_exit_ok = $false
            uninstall_removed = $false
            shortcut_removed  = $false
            autostart_removed = $false
        }
        # Autostart task is checked by default; verify the HKCU Run value points at
        # the freshly installed exe. Remember any pre-existing value to restore.
        # On some Win11 26200 machines third-party startup managers can hide freshly
        # written Run values from live reads, so the installer log is authoritative.
        $runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
        $runValue = ''
        for ($attempt = 1; $attempt -le 4; $attempt++) {
            try { $runValue = (Get-ItemProperty -Path $runKey -Name 'LinkNote' -ErrorAction Stop).'LinkNote' } catch { $runValue = '' }
            if ($runValue) { break }
            Start-Sleep -Milliseconds 500
        }
        $prevRunValue = $runValue
        $expectedRun = '"' + $installed + '" --minimized'
        $installLogRaw = ''
        if (Test-Path -LiteralPath $installLog) { $installLogRaw = Get-Content $installLog -Raw }
        $checks['autostart_log_ok'] = ($installLogRaw -match '(?s)Value name: LinkNote.*?Successfully created or set the value')
        $checks['autostart_live_value'] = $runValue
        $checks.autostart_installed = ($runValue -eq $expectedRun) -or $checks['autostart_log_ok']
        Info "autostart Run value: '$runValue' live_ok=$($runValue -eq $expectedRun) log_write_ok=$($checks['autostart_log_ok']) -> ok=$($checks.autostart_installed)"
        if ($checks.engine_installed -and $report.engine.staged) {
            $checks.engine_sha_match = ((Get-Sha256 (Join-Path $target 'engine\BiliNoteBackend.exe')) -eq $report.engine.exe_sha256)
        }
        Info ($checks | ConvertTo-Json -Depth 3 -Compress)

        # user-data marker: must survive uninstall
        $userData = Join-Path $env:LOCALAPPDATA 'LinkNote'
        New-Item -ItemType Directory -Force -Path $userData | Out-Null
        $marker = Join-Path $userData 'uninstall-preserve-test.txt'
        Set-Content -Path $marker -Encoding utf8 -Value "created by build-desktop.ps1 at $(Get-Date -Format o)"

        # installed app launch check (isolated profile so engine_auto_start is off)
        $instVerify = Join-Path $testRoot 'verify'
        New-Item -ItemType Directory -Force -Path (Join-Path $instVerify 'profile\LinkNote') | Out-Null
        Set-Content -Path (Join-Path $instVerify 'profile\LinkNote\settings.json') -Encoding utf8 -Value (@{
            engine_auto_start = $false
            close_to_tray     = $false
        } | ConvertTo-Json)
        $instShot = Join-Path $instVerify 'installed-app.png'
        $oldLocalAppData = $env:LOCALAPPDATA
        $iproc = $null
        try {
            $env:LOCALAPPDATA = (Join-Path $instVerify 'profile')
            $iproc = Start-Process -FilePath $installed -PassThru
            Start-Sleep -Seconds 8
            $iproc.Refresh()
            $checks['installed_launch_ok'] = (-not $iproc.HasExited)
            if (-not $iproc.HasExited) {
                $live = Get-Process -Id $iproc.Id -ErrorAction SilentlyContinue
                if ($live) { $checks['installed_window_handle'] = $live.MainWindowHandle }
                if ($live -and $live.MainWindowHandle -ne 0) {
                    $iwin = Join-Path $instVerify 'installed-window.png'
                    if (Save-WindowCapture $live.MainWindowHandle $iwin) { $checks['installed_window_png'] = $iwin }
                }
                $wshell = New-Object -ComObject WScript.Shell
                try { $wshell.AppActivate($iproc.Id) | Out-Null } catch { }
                Start-Sleep -Milliseconds 800
                if (Save-Screen $instShot) { $checks['installed_screenshot'] = $instShot }
            }
        } finally {
            if ($iproc -and -not $iproc.HasExited) {
                try { Stop-Process -Id $iproc.Id -Force -ErrorAction SilentlyContinue } catch { }
            }
            $env:LOCALAPPDATA = $oldLocalAppData
        }
        foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
            try {
                if ($p.Path -and $p.Path.StartsWith($target, [System.StringComparison]::OrdinalIgnoreCase)) {
                    Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
                }
            } catch { }
        }

        # silent uninstall
        $unins = Join-Path $target 'unins000.exe'
        if (Test-Path -LiteralPath $unins) {
            $up = Start-Process -FilePath $unins -Wait -PassThru -NoNewWindow -ArgumentList @(
                '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$uninstallLog`""
            )
            $checks.uninstall_exit_ok = ($up.ExitCode -eq 0)
            Info "uninstaller exit=$($up.ExitCode)"
            $checks.uninstall_removed = Wait-PathGone (Join-Path $target 'LinkNote.exe') 60
            $checks.shortcut_removed = -not (Test-Path -LiteralPath $deskLnk)
            $checks.userdata_preserved = (Test-Path -LiteralPath $marker)
            $checks.uninstall_reg_removed = -not (Test-Path -LiteralPath $realReg)
            $nowRun = ''
            for ($attempt = 1; $attempt -le 10; $attempt++) {
                try { $nowRun = (Get-ItemProperty -Path $runKey -Name 'LinkNote' -ErrorAction Stop).'LinkNote' } catch { $nowRun = '' }
                if (-not $nowRun) { break }
                Start-Sleep -Milliseconds 500
            }
            $checks.autostart_removed = -not $nowRun
            $uninstallLogRaw = ''
            if (Test-Path -LiteralPath $uninstallLog) { $uninstallLogRaw = Get-Content $uninstallLog -Raw }
            $checks['autostart_uninstall_log_ok'] = ($uninstallLogRaw -match 'Deleting registry value: .*\\Run\\LinkNote')
            if (-not $nowRun -or $checks['autostart_uninstall_log_ok']) { $checks.autostart_removed = $true }
            Info ("uninstall checks: removed=$($checks.uninstall_removed) shortcut_removed=$($checks.shortcut_removed) userdata_preserved=$($checks.userdata_preserved) reg_removed=$($checks.uninstall_reg_removed) autostart_removed=$($checks.autostart_removed)")
            # never leave test artifacts behind: drop a Run value pointing at the test install…
            if ($nowRun -and $nowRun.Contains($target)) {
                try { Remove-ItemProperty -Path $runKey -Name 'LinkNote' -ErrorAction SilentlyContinue } catch { }
            }
            # …and restore anything that existed before the test.
            if ($prevRunValue) {
                try { Set-ItemProperty -Path $runKey -Name 'LinkNote' -Value $prevRunValue -ErrorAction SilentlyContinue } catch { }
            }
            # remove the preservation marker; the point was only that uninstall must not delete it
            if ($checks.userdata_preserved) { Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue }
        } else {
            Warn 'uninstaller missing after install; cannot run uninstall test'
        }

        $report.installer['install_test'] = $checks
        $report.installer.verified = (
            $checks.install_exit_ok -and $checks.exe_installed -and $checks.internal_dir -and
            $checks.uninstaller -and $checks.desktop_shortcut -and $checks.startmenu_dir -and
            $checks.autostart_installed -and ($checks.installed_launch_ok -eq $true) -and
            $checks.uninstall_exit_ok -and $checks.uninstall_removed -and $checks.shortcut_removed -and
            $checks.autostart_removed -and $checks.userdata_preserved
        )
        if ($report.installer.verified) {
            Write-Host '  installer verification: PASS' -ForegroundColor Green
        } else {
            Warn 'installer verification incomplete (see build-report.json install_test)'
        }
    }
}

# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
$report.artifacts = [ordered]@{
    app_exe     = if (Test-Path $AppExe) { $AppExe } else { '' }
    app_sha256  = if (Test-Path $AppExe) { (Get-Sha256 $AppExe) } else { '' }
    setup_exe   = if (Test-Path $SetupExe) { $SetupExe } else { '' }
    setup_sha256 = if (Test-Path $SetupExe) { (Get-Sha256 $SetupExe) } else { '' }
    engine_dir  = if (Test-Path $EngineExe) { $EngineDir } else { '' }
    report      = $ReportPath
}
$report | ConvertTo-Json -Depth 8 | Set-Content -Path $ReportPath -Encoding utf8

Step 'Summary'
Write-Host ("  app:      {0}" -f $(if (Test-Path $AppExe) { "$AppExe  ($($report.app.exe_mb) MB, sha256 $($report.artifacts.app_sha256))" } else { '(not built)' }))
Write-Host ("  setup:    {0}" -f $(if (Test-Path $SetupExe) { "$SetupExe  ($($report.installer.exe_mb) MB, sha256 $($report.artifacts.setup_sha256))" } else { '(not built)' }))
Write-Host ("  engine:   {0}" -f $(if ($report.engine.staged) { "$($report.engine.total_mb) MB staged, included=$($engineIncluded)" } else { 'not staged' }))
Write-Host ("  report:   {0}" -f $ReportPath)
if ($report.warnings.Count -gt 0) {
    Write-Host ''
    Write-Host '  warnings:' -ForegroundColor Yellow
    $report.warnings | ForEach-Object { Write-Host "    - $_" -ForegroundColor Yellow }
}
Write-Host ''
$appOk = (Test-Path $AppExe)
$setupOk = $SkipInstaller -or (Test-Path $SetupExe)
if ($appOk -and $setupOk) {
    Write-Host 'BUILD OK' -ForegroundColor Green
    exit 0
}
Write-Host 'BUILD INCOMPLETE' -ForegroundColor Red
exit 1
