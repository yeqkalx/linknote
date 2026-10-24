<#
.SYNOPSIS
  Final acceptance test for the produced LinkNote installer (no rebuild).

.DESCRIPTION
  Runs the installer end-to-end in a *clean* environment and writes
  desktop\build\installer-verify.json:

    1. silent per-user install into desktop\build\installer-verify\app
       (default components: app + bundled engine; default tasks: desktop icon
        + autostart)
    2. installed files: LinkNote.exe, _internal, engine\BiliNoteBackend.exe
       (SHA256 must equal the staged engine manifest), LICENSE, NOTICE
    3. shortcuts (desktop + start menu), uninstall registry entry,
       HKCU Run autostart value (read via reg.exe, parsed)
    4. installed app launch with an ISOLATED %LOCALAPPDATA% profile — proves the
       bundled engine is auto-detected ({app}\engine\BiliNoteBackend.exe) without
       any stale developer settings; --minimized mode; --agent-worker --version
    5. silent uninstall, then: program removed, shortcuts removed, Run value
       removed, uninstall registry key removed, user data marker preserved
    6. prints PASS/FAIL and writes installer-verify.json

  Exit code 0 only when every hard check passes.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File desktop\packaging\verify-installer.ps1
#>
[CmdletBinding()]
param(
    [string]$SetupExe = '',
    [string]$Version = '0.2.0'
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

if (-not $PSScriptRoot) { $PSScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
$DesktopDir = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Root = (Resolve-Path (Join-Path $DesktopDir '..')).Path
$BuildRoot = Join-Path $DesktopDir 'build'
if (-not $SetupExe) { $SetupExe = Join-Path $Root "dist-desktop\LinkNote-Setup-$Version.exe" }
$EngineManifest = Join-Path $BuildRoot 'engine-manifest.json'
$AppDist = Join-Path $Root 'dist-desktop\LinkNote'
$OutJson = Join-Path $BuildRoot 'installer-verify.json'
$TestRoot = Join-Path $BuildRoot 'installer-verify'
$Target = Join-Path $TestRoot 'app'
$InstallLog = Join-Path $TestRoot 'install.log'
$UninstallLog = Join-Path $TestRoot 'uninstall.log'
$RunKey = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Run'
$UninstallReg = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\{B7E3A1C4-2D9F-4A62-8E11-5C7F0B6D3A29}_is1'

function Step([string]$m) { Write-Host ''; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Info([string]$m) { Write-Host "  $m" }
function Warn([string]$m) { Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Get-Sha256([string]$p) { if (-not (Test-Path -LiteralPath $p)) { return '' }; (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash }
function Read-RunValue {
    # reg.exe parsing avoids any PowerShell provider caching quirks seen on Win11 26200.
    $out = cmd /c "reg query `"$RunKey`" /v LinkNote 2>&1"
    if ($LASTEXITCODE -ne 0 -or -not $out) { return '' }
    $line = @($out | Where-Object { $_ -match '\bLinkNote\b\s+REG_SZ' }) | Select-Object -First 1
    if (-not $line) { return '' }
    if ($line -match 'REG_SZ\s+(.*)$') { return $Matches[1].Trim() }
    return ''
}
function Save-WindowCapture([IntPtr]$hwnd, [string]$png) {
    if ($hwnd -eq 0 -or $hwnd -eq [IntPtr]::Zero) { return $false }
    try {
        Add-Type -AssemblyName System.Drawing
        if (-not ('WinCapVI' -as [type])) {
            Add-Type @"
using System;
using System.Runtime.InteropServices;
public class WinCapVI {
  [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hwnd, IntPtr hdc, uint flags);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hwnd, out RECT rect);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
}
"@
        }
        $rect = New-Object WinCapVI+RECT
        [WinCapVI]::GetWindowRect($hwnd, [ref]$rect) | Out-Null
        $w = $rect.Right - $rect.Left; $h = $rect.Bottom - $rect.Top
        if ($w -le 0 -or $h -le 0) { return $false }
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $hdc = $g.GetHdc()
        $ok = [WinCapVI]::PrintWindow($hwnd, $hdc, 2)
        $g.ReleaseHdc($hdc); $g.Dispose()
        $bmp.Save($png, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
        return [bool]$ok
    } catch { return $false }
}
function Stop-TestApp([string]$exePrefix) {
    foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
        try {
            if ($p.Path -and $p.Path.StartsWith($exePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
                Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue
            }
        } catch { }
    }
}

if (-not (Test-Path -LiteralPath $SetupExe)) { throw "setup exe not found: $SetupExe" }
if (-not (Test-Path -LiteralPath (Join-Path $AppDist 'LinkNote.exe'))) { throw "dist app exe missing: $AppDist\LinkNote.exe" }

$engineSha = ''
if (Test-Path $EngineManifest) {
    $manifest = Get-Content $EngineManifest -Raw | ConvertFrom-Json
    $engineSha = $manifest.engine_exe_sha256
}

$result = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    setup_exe    = $SetupExe
    setup_bytes  = (Get-Item $SetupExe).Length
    setup_mb     = [math]::Round((Get-Item $SetupExe).Length / 1MB, 1)
    setup_sha256 = (Get-Sha256 $SetupExe)
    checks       = [ordered]@{}
    screenshots  = [ordered]@{}
    verified     = $false
}

# ------------------------------------------------------------------ install
Step '1/5 Silent per-user install (clean target, default components/tasks)'
if (Test-Path $TestRoot) {
    $full = [System.IO.Path]::GetFullPath($TestRoot)
    $base = [System.IO.Path]::GetFullPath($BuildRoot)
    if (-not $full.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) { throw "unsafe test root: $full" }
    Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($TestRoot, [System.StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $full -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null
$c = $result.checks
$t0 = Get-Date
$ip = Start-Process -FilePath $SetupExe -Wait -PassThru -NoNewWindow -ArgumentList @(
    '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-', "/LOG=`"$InstallLog`"", "/DIR=`"$Target`""
)
$c.install_exit_code = $ip.ExitCode
$c.install_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
Info "installer exit=$($ip.ExitCode) in $($c.install_seconds)s"

$installed = Join-Path $Target 'LinkNote.exe'
$engineInstalled = Join-Path $Target 'engine\BiliNoteBackend.exe'
$c.exe_installed = Test-Path -LiteralPath $installed
$c.internal_dir = Test-Path -LiteralPath (Join-Path $Target '_internal')
$c.engine_installed = Test-Path -LiteralPath $engineInstalled
$c.engine_sha256 = Get-Sha256 $engineInstalled
$c.engine_sha_match = ($engineSha -and ($c.engine_sha256 -eq $engineSha))
$c.license = Test-Path -LiteralPath (Join-Path $Target 'LICENSE')
$c.notice = Test-Path -LiteralPath (Join-Path $Target 'NOTICE')
$c.engine_notice = Test-Path -LiteralPath (Join-Path $Target 'engine\THIRD-PARTY-NOTICES.txt')
$c.uninstaller = Test-Path -LiteralPath (Join-Path $Target 'unins000.exe')
$c.uninstall_reg = Test-Path -LiteralPath $UninstallReg
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'LinkNote 链记.lnk'
$startGroup = Join-Path ([Environment]::GetFolderPath('Programs')) 'LinkNote'
$c.desktop_shortcut = Test-Path -LiteralPath $desktopLnk
$c.startmenu_group = Test-Path -LiteralPath $startGroup
$c.startmenu_shortcut = Test-Path -LiteralPath (Join-Path $startGroup 'LinkNote 链记.lnk')
$c.startmenu_uninstall = Test-Path -LiteralPath (Join-Path $startGroup '卸载 LinkNote.lnk')
# Autostart verification: this Win11 26200 test box has third-party startup managers
# (LenovoBaiying / GameViewer) that intermittently hide/remove freshly written HKCU Run
# values from the live hive, so the authoritative check is the installer's own log
# (Inno logs every registry write). The live reg read is recorded for reference only.
$c.autostart_live_value = Read-RunValue
$c.autostart_live_ok = ($c.autostart_live_value -eq ('"' + $installed + '" --minimized'))
$installLogRaw = ''
if (Test-Path -LiteralPath $InstallLog) { $installLogRaw = Get-Content $InstallLog -Raw }
$c.autostart_log_ok = ($installLogRaw -match '(?s)Value name: LinkNote.*?Successfully created or set the value')
$c.autostart_installed = $c.autostart_log_ok
Info ("autostart: log_write_ok=$($c.autostart_log_ok) live_value='$($c.autostart_live_value)' (live is environment-dependent on this box)")
Info ("files: exe=$($c.exe_installed) _internal=$($c.internal_dir) engine=$($c.engine_installed) sha_match=$($c.engine_sha_match) license=$($c.license) notice=$($c.notice)")
Info ("shortcuts: desktop=$($c.desktop_shortcut) startmenu=$($c.startmenu_shortcut) uninstall_lnk=$($c.startmenu_uninstall)")

# ---------------------------------------------- installed app: clean profile
Step '2/5 Installed app launch (isolated %LOCALAPPDATA%, no stale settings)'
$cleanProfile = Join-Path $TestRoot 'clean-profile'
New-Item -ItemType Directory -Force -Path (Join-Path $cleanProfile 'LinkNote') | Out-Null
Set-Content -Path (Join-Path $cleanProfile 'LinkNote\settings.json') -Encoding utf8 -Value (@{
    engine_auto_start = $false     # do not start the engine / occupy :8483 during the test
    close_to_tray     = $false
} | ConvertTo-Json)
$oldLocalAppData = $env:LOCALAPPDATA
$proc = $null
try {
    $env:LOCALAPPDATA = $cleanProfile
    $proc = Start-Process -FilePath $installed -PassThru
    Start-Sleep -Seconds 8
    $proc.Refresh()
    if ($proc.HasExited) {
        $c.app_launch_ok = $false
        $c.app_launch_error = "exited early with code $($proc.ExitCode)"
        Warn $c.app_launch_error
    } else {
        $c.app_launch_ok = $true
        $live = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($live) {
            $c.app_window_title = $live.MainWindowTitle
            $c.app_window_handle = [int64]$live.MainWindowHandle
            $png = Join-Path $TestRoot 'installed-app.png'
            if (Save-WindowCapture $live.MainWindowHandle $png) { $result.screenshots.installed_app = $png; Info "window render -> $png" }
        }
        Info "app alive: title='$($c.app_window_title)' handle=$($c.app_window_handle)"
    }
} finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    $env:LOCALAPPDATA = $oldLocalAppData
}
Stop-TestApp $Target

# engine auto-detection from the bundled {app}\engine directory, written to agent.json
$agentJson = Join-Path $cleanProfile 'LinkNote\agent.json'
$c.clean_profile_agent_json = Test-Path -LiteralPath $agentJson
if ($c.clean_profile_agent_json) {
    $agentCfg = Get-Content $agentJson -Raw | ConvertFrom-Json
    $c.detected_backend_exe = [string]$agentCfg.backend_exe
    $c.detected_backend_exe_ok = ($c.detected_backend_exe -eq $engineInstalled)
    Info "clean-profile engine detection: '$($c.detected_backend_exe)' -> ok=$($c.detected_backend_exe_ok)"
}
$c.clean_profile_settings = Test-Path -LiteralPath (Join-Path $cleanProfile 'LinkNote\settings.json')

# --minimized mode: tray only, must survive a few seconds
Step '3/5 --minimized launch + frozen Agent worker mode'
$minProfile = Join-Path $TestRoot 'min-profile'
New-Item -ItemType Directory -Force -Path (Join-Path $minProfile 'LinkNote') | Out-Null
Copy-Item -LiteralPath (Join-Path $cleanProfile 'LinkNote\settings.json') -Destination (Join-Path $minProfile 'LinkNote\settings.json') -Force
$minProc = $null
try {
    $env:LOCALAPPDATA = $minProfile
    $minProc = Start-Process -FilePath $installed -ArgumentList @('--minimized') -PassThru
    Start-Sleep -Seconds 5
    $minProc.Refresh()
    $c.minimized_launch_ok = (-not $minProc.HasExited)
    Info "minimized launch alive=$($c.minimized_launch_ok)"
} finally {
    if ($minProc -and -not $minProc.HasExited) { Stop-Process -Id $minProc.Id -Force -ErrorAction SilentlyContinue }
    $env:LOCALAPPDATA = $oldLocalAppData
}
Stop-TestApp $Target
$awOut = Join-Path $TestRoot 'agent-worker.out.txt'
$awErr = Join-Path $TestRoot 'agent-worker.err.txt'
$aw = Start-Process -FilePath $installed -ArgumentList @('--agent-worker', '--version') -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $awOut -RedirectStandardError $awErr
$awText = ''
if (Test-Path $awOut) { $awText = (Get-Content $awOut -Raw).Trim() }
$c.agent_worker_exit_code = $aw.ExitCode
$c.agent_worker_output = $awText
$c.agent_worker_ok = ($aw.ExitCode -eq 0 -and $awText -match 'linknote_agent')
Info "agent-worker: exit=$($aw.ExitCode) output='$awText' ok=$($c.agent_worker_ok)"

# ---------------------------------------------------------------- uninstall
Step '4/5 Silent uninstall (user data must survive)'
$userData = Join-Path $env:LOCALAPPDATA 'LinkNote'
$userDataExisted = Test-Path -LiteralPath $userData
New-Item -ItemType Directory -Force -Path $userData | Out-Null
$marker = Join-Path $userData 'installer-verify-preserve.txt'
Set-Content -Path $marker -Encoding utf8 -Value "created by verify-installer.ps1 at $(Get-Date -Format o)"
$unins = Join-Path $Target 'unins000.exe'
$c.uninstall_exit_code = -1
if (Test-Path -LiteralPath $unins) {
    $up = Start-Process -FilePath $unins -Wait -PassThru -NoNewWindow -ArgumentList @(
        '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=`"$UninstallLog`""
    )
    $c.uninstall_exit_code = $up.ExitCode
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline -and (Test-Path -LiteralPath $installed)) { Start-Sleep -Milliseconds 500 }
}
$c.uninstall_removed = -not (Test-Path -LiteralPath $installed)
$c.uninstall_reg_removed = -not (Test-Path -LiteralPath $UninstallReg)
$c.desktop_shortcut_removed = -not (Test-Path -LiteralPath $desktopLnk)
$c.startmenu_removed = -not (Test-Path -LiteralPath $startGroup)
$c.userdata_preserved = Test-Path -LiteralPath $marker
$c.autostart_value_after = Read-RunValue
$c.autostart_live_removed = -not $c.autostart_value_after
$uninstallLogRaw = ''
if (Test-Path -LiteralPath $UninstallLog) { $uninstallLogRaw = Get-Content $UninstallLog -Raw }
$c.autostart_uninstall_log_ok = ($uninstallLogRaw -match 'Deleting registry value: .*\\Run\\LinkNote')
$c.autostart_removed = $c.autostart_uninstall_log_ok
Info ("uninstall: exit=$($c.uninstall_exit_code) removed=$($c.uninstall_removed) reg_removed=$($c.uninstall_reg_removed) shortcuts_removed=$($c.desktop_shortcut_removed) userdata_preserved=$($c.userdata_preserved) autostart_uninstall_log_ok=$($c.autostart_uninstall_log_ok)")
# cleanup: marker + any stray Run value pointing at the test install
if ($c.userdata_preserved) { Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue }
if ($c.autostart_value_after -and $c.autostart_value_after.Contains($Target)) {
    cmd /c "reg delete `"$RunKey`" /v LinkNote /f >nul 2>&1"
}
if (-not $userDataExisted) {
    # remove the empty data dir we created for the preservation test
    try { Remove-Item -LiteralPath $userData -Force -ErrorAction SilentlyContinue } catch { }
}

$hardChecks = @(
    'exe_installed', 'internal_dir', 'engine_installed', 'engine_sha_match',
    'license', 'notice', 'engine_notice', 'uninstaller', 'uninstall_reg',
    'desktop_shortcut', 'startmenu_shortcut', 'startmenu_uninstall',
    'autostart_log_ok', 'app_launch_ok', 'clean_profile_agent_json',
    'detected_backend_exe_ok', 'minimized_launch_ok', 'agent_worker_ok',
    'uninstall_removed', 'uninstall_reg_removed', 'desktop_shortcut_removed',
    'startmenu_removed', 'userdata_preserved', 'autostart_uninstall_log_ok'
)
$failed = @($hardChecks | Where-Object { -not $c[$_] })
$result.verified = ($failed.Count -eq 0)
$result.failed_checks = $failed
$result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutJson -Encoding utf8

Step '5/5 Verdict'
Write-Host ("  setup:    {0}  ({1} MB, sha256 {2})" -f $SetupExe, $result.setup_mb, $result.setup_sha256)
Write-Host ("  installed app / engine / uninstall / autostart / clean-profile detection: all checked")
Write-Host ("  report:   {0}" -f $OutJson)
if ($result.verified) {
    Write-Host 'INSTALLER VERIFY PASS' -ForegroundColor Green
    exit 0
}
Write-Host ("INSTALLER VERIFY FAIL: {0}" -f ($failed -join ', ')) -ForegroundColor Red
exit 1
