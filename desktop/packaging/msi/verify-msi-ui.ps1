# Real UI click test for the MSI Finish dialog "Launch LinkNote" checkbox.
#
# Runs the MSI with its FULL UI, drives the wizard with Win32 messages
# (BM_CLICK on the actual Installer buttons/checkbox), leaves the
# "Launch LinkNote" checkbox checked, clicks Finish, and verifies that the
# installed LinkNote.exe actually starts.
#
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\msi\verify-msi-ui.ps1
#
# Output: desktop\build\msi\msi-ui-verify.json + screenshots + PASS/FAIL exit code.
[CmdletBinding()]
param([string]$Msi = '', [string]$Version = '')
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $Version) {
    $m = Select-String -Path (Join-Path $repo 'desktop\pyproject.toml') -Pattern '^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' | Select-Object -First 1
    $Version = if ($m) { $m.Matches[0].Groups[1].Value } else { '0.2.1' }
}
if (-not $Msi) { $Msi = Join-Path $repo "dist-desktop\LinkNote-Setup-$Version.msi" }
if (-not (Test-Path $Msi)) { throw "MSI not found: $Msi" }
$WorkDir = Join-Path $repo 'desktop\build\msi\verify-ui'
$OutJson = Join-Path $repo 'desktop\build\msi\msi-ui-verify.json'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\LinkNote'
$AppExe = Join-Path $InstallDir 'LinkNote.exe'
if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null

Add-Type -AssemblyName System.Drawing
Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public class MsiUi {
    public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumWindows(EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumWindowsProc cb, IntPtr lParam);
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint pid);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr hWnd, StringBuilder text, int max);
    [DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr hWnd, StringBuilder text, int max);
    [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern IntPtr SendMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr hWnd, IntPtr hdc, uint flags);
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    public static string Text(IntPtr h) { var sb = new StringBuilder(512); GetWindowText(h, sb, 512); return sb.ToString(); }
    public static string Cls(IntPtr h) { var sb = new StringBuilder(256); GetClassName(h, sb, 256); return sb.ToString(); }
}
"@

function Get-TopWindows([int]$procId) {
    $list = New-Object System.Collections.ArrayList
    $cb = [MsiUi+EnumWindowsProc]{
        param($h, $l)
        $pid2 = 0
        [void][MsiUi]::GetWindowThreadProcessId($h, [ref]$pid2)
        if ($pid2 -eq $procId -and [MsiUi]::IsWindowVisible($h)) {
            [void]$list.Add($h)
        }
        return $true
    }
    [void][MsiUi]::EnumWindows($cb, [IntPtr]::Zero)
    return $list.ToArray()
}
function Get-Controls([IntPtr]$parent) {
    $list = New-Object System.Collections.ArrayList
    $cb = [MsiUi+EnumWindowsProc]{
        param($h, $l)
        $text = [MsiUi]::Text($h)
        $cls = [MsiUi]::Cls($h)
        if ([MsiUi]::IsWindowVisible($h)) {
            [void]$list.Add([pscustomobject]@{ Hwnd = $h; Class = $cls; Text = $text; Enabled = [MsiUi]::IsWindowEnabled($h) })
        }
        return $true
    }
    [void][MsiUi]::EnumChildWindows($parent, $cb, [IntPtr]::Zero)
    return $list.ToArray()
}
function Save-Window([IntPtr]$hwnd, [string]$path) {
    try {
        $r = New-Object MsiUi+RECT
        [void][MsiUi]::GetWindowRect($hwnd, [ref]$r)
        $w = $r.Right - $r.Left; $h = $r.Bottom - $r.Top
        if ($w -le 0 -or $h -le 0) { return $false }
        $bmp = New-Object System.Drawing.Bitmap $w, $h
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        $hdc = $g.GetHdc()
        [void][MsiUi]::PrintWindow($hwnd, $hdc, 2)
        $g.ReleaseHdc($hdc); $g.Dispose()
        $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png); $bmp.Dispose()
        return $true
    } catch { return $false }
}
function Click-Control([IntPtr]$hwnd) {
    [void][MsiUi]::SendMessage($hwnd, 0x00F5, [IntPtr]::Zero, [IntPtr]::Zero)   # BM_CLICK
}
function Get-Check([IntPtr]$hwnd) {
    return [int]([MsiUi]::SendMessage($hwnd, 0x00F0, [IntPtr]::Zero, [IntPtr]::Zero))  # BM_GETCHECK
}
function Set-Check([IntPtr]$hwnd, [int]$state) {
    [void][MsiUi]::SendMessage($hwnd, 0x00F1, [IntPtr]$state, [IntPtr]::Zero)   # BM_SETCHECK
}
function Uninstall-LinkNote {
    foreach ($rk in @('HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall')) {
        if (-not (Test-Path $rk)) { continue }
        foreach ($k in @(Get-ChildItem $rk -ErrorAction SilentlyContinue)) {
            try {
                $dn = (Get-ItemProperty $k.PSPath -Name DisplayName -ErrorAction Stop).DisplayName
                if ($dn -eq 'LinkNote' -and $k.PSChildName -match '^\{.+\}$') {
                    Start-Process -FilePath 'msiexec.exe' -Wait -ArgumentList @('/x', $k.PSChildName, '/qn', '/norestart') | Out-Null
                }
            } catch { }
        }
    }
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline -and (Test-Path -LiteralPath $AppExe)) { Start-Sleep -Milliseconds 500 }
}
function Stop-LinkNote {
    foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
        try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
}

$result = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    msi          = $Msi
    checks       = [ordered]@{}
    screenshots  = [ordered]@{}
    verified     = $false
}
$c = $result.checks

Write-Host '=== 0/4 Clean state ==='
Stop-LinkNote
Uninstall-LinkNote
$c.clean_state = (-not (Test-Path -LiteralPath $AppExe))
Write-Host "  clean: $($c.clean_state)"

Write-Host '=== 1/4 Launch MSI full UI ==='
$uiLog = Join-Path $WorkDir 'ui-install.log'
$proc = Start-Process -FilePath 'msiexec.exe' -PassThru -ArgumentList @('/i', "`"$Msi`"", '/l*v', "`"$uiLog`"")
Write-Host "  msiexec pid=$($proc.Id)"

$finishClicked = $false
$checkboxSeen = $false
$checkboxWasChecked = $false
$seenControls = New-Object System.Collections.ArrayList
$deadline = (Get-Date).AddSeconds(150)
while ((Get-Date) -lt $deadline) {
    $proc.Refresh()
    $top = @(Get-TopWindows $proc.Id)
    if ($top.Count -eq 0) {
        # msiexec may host the UI in a child client process; find any visible
        # Windows Installer dialog whose title mentions LinkNote.
        $all = New-Object System.Collections.ArrayList
        $cb = [MsiUi+EnumWindowsProc]{
            param($h, $l)
            if ([MsiUi]::IsWindowVisible($h) -and [MsiUi]::Cls($h) -eq '#32770') {
                $t = [MsiUi]::Text($h)
                if ($t -match 'LinkNote') { [void]$all.Add($h) }
            }
            return $true
        }
        [void][MsiUi]::EnumWindows($cb, [IntPtr]::Zero)
        $top = @($all.ToArray())
    }
    foreach ($dlg in $top) {
        [void][MsiUi]::SetForegroundWindow($dlg)
        $controls = Get-Controls $dlg
        $buttons = @($controls | Where-Object { $_.Class -eq 'Button' -and $_.Text })
        if (-not $finishClicked -and @($buttons | Where-Object { $_.Text -match 'Finish' }).Count -gt 0) {
            $finish = @($buttons | Where-Object { $_.Text -match 'Finish' })[0]
            $cb = @($buttons | Where-Object { $_.Text -match 'Launch' }) | Select-Object -First 1
            $checkboxSeen = [bool]$cb
            if ($cb) {
                $state = Get-Check $cb.Hwnd
                $checkboxWasChecked = ($state -eq 1)
                Set-Check $cb.Hwnd 1
                $c.checkbox_found = $true
                $c.checkbox_text = $cb.Text
                $c.checkbox_default_checked = $checkboxWasChecked
                $c.checkbox_checked_after_set = ((Get-Check $cb.Hwnd) -eq 1)
            } else {
                $c.checkbox_found = $false
            }
            $shot = Join-Path $WorkDir 'finish-dialog.png'
            if (Save-Window $dlg $shot) { $result.screenshots.finish_dialog = $shot; Write-Host "  finish dialog screenshot -> $shot" }
            Start-Sleep -Milliseconds 300
            Click-Control $finish.Hwnd
            $finishClicked = $true
            Write-Host "  clicked Finish (checkbox found=$checkboxSeen defaultChecked=$checkboxWasChecked)"
            break
        } elseif (-not $finishClicked) {
            foreach ($b in $buttons) {
                [void]$seenControls.Add($b.Text)
                if ($b.Text -match 'accept' -or $b.Text -match 'I &accept') {
                    Click-Control $b.Hwnd; Start-Sleep -Milliseconds 300; break
                }
            }
            $next = @($buttons | Where-Object { ($_.Text -match 'Next|Install') -and $_.Enabled }) | Select-Object -First 1
            if ($next) { Click-Control $next.Hwnd; Write-Host "  clicked '$($next.Text)'" }
        }
    }
    if ($finishClicked) { break }
    Start-Sleep -Milliseconds 600
}
$c.finish_clicked = $finishClicked
$c.wizard_buttons_seen = (@($seenControls) | Select-Object -Unique) -join ' | '

Write-Host '=== 2/4 Wait for LinkNote launch (Finish -> LaunchLinkNote) ==='
$launched = $null
$deadline = (Get-Date).AddSeconds(20)
while ((Get-Date) -lt $deadline) {
    $launched = Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launched) { break }
    Start-Sleep -Milliseconds 500
}
$c.app_launched_after_finish = [bool]$launched
if ($launched) {
    $c.launched_pid = $launched.Id
    try { $c.launched_path = $launched.Path } catch { }
    $c.launched_from_install_dir = ($c.launched_path -eq $AppExe)
    Start-Sleep -Seconds 2
    $p2 = Get-Process -Id $launched.Id -ErrorAction SilentlyContinue
    if ($p2) {
        $shot2 = Join-Path $WorkDir 'launched-app.png'
        if (Save-Window $p2.MainWindowHandle $shot2) { $result.screenshots.launched_app = $shot2 }
    }
}
Write-Host "  launched=$($c.app_launched_after_finish) path=$($c.launched_path)"

Write-Host '=== 3/4 Installer finished + files present ==='
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $AppExe)) { Start-Sleep -Milliseconds 500 }
$c.app_installed = Test-Path -LiteralPath $AppExe
$c.desktop_shortcut = Test-Path -LiteralPath (Join-Path ([Environment]::GetFolderPath('Desktop')) 'LinkNote.lnk')
Stop-LinkNote
Start-Sleep -Seconds 2
Write-Host "  installed=$($c.app_installed) shortcut=$($c.desktop_shortcut)"

Write-Host '=== 4/4 Cleanup: silent uninstall ==='
Uninstall-LinkNote
$c.program_removed = (-not (Test-Path -LiteralPath $AppExe))
Write-Host "  removed: $($c.program_removed)"

$hardChecks = @('clean_state', 'finish_clicked', 'checkbox_found', 'checkbox_checked_after_set',
                'app_launched_after_finish', 'launched_from_install_dir', 'app_installed',
                'desktop_shortcut', 'program_removed')
$failed = @($hardChecks | Where-Object { -not $c[$_] })
$result.verified = ($failed.Count -eq 0)
$result.failed_checks = $failed
$result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutJson -Encoding utf8
Write-Host ''
Write-Host ("  report: {0}" -f $OutJson)
if ($result.verified) { Write-Host 'MSI UI CLICK VERIFY PASS' -ForegroundColor Green; exit 0 }
Write-Host ("MSI UI CLICK VERIFY FAIL: {0}" -f ($failed -join ', ')) -ForegroundColor Red
exit 1

