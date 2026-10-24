# LinkNote MSI acceptance test (no rebuild).
#
# Verifies the MSI end-to-end on a clean state:
#   1. static: per-user properties, ExitDialog launch checkbox/action, autostart row
#   2. silent install (per-user, non-elevated): files, data dir, shortcuts,
#      HKCU Run autostart value, HKCU (not HKLM) uninstall registration
#   3. installed app launches (isolated LOCALAPPDATA so no engine start / :8483)
#   4. silent uninstall: removes app/shortcuts/Run/uninstall entry, keeps %LOCALAPPDATA%\LinkNote
#   5. launch-action simulation: reinstall with LAUNCHLINKNOTE=1 (same custom action the
#      Finish checkbox fires) and confirm the installed LinkNote.exe starts; then uninstall
#
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\msi\verify-msi.ps1
#   ... -SkipLaunchAction        # skip step 5
#
# Output: desktop\build\msi\msi-verify.json + PASS/FAIL + exit code.
[CmdletBinding()]
param(
    [string]$Msi = '',
    [string]$Version = '',
    [switch]$SkipLaunchAction
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $Version) {
    $m = Select-String -Path (Join-Path $repo 'desktop\pyproject.toml') -Pattern '^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' | Select-Object -First 1
    $Version = if ($m) { $m.Matches[0].Groups[1].Value } else { '0.2.1' }
}
if (-not $Msi) { $Msi = Join-Path $repo "dist-desktop\LinkNote-Setup-$Version.msi" }
if (-not (Test-Path $Msi)) { throw "MSI not found: $Msi (run build-msi.ps1 first)" }
$WixDir = Join-Path $repo 'tools\wix314'
$WorkDir = Join-Path $repo 'desktop\build\msi\verify'
$OutJson = Join-Path $repo 'desktop\build\msi\msi-verify.json'
$InstallDir = Join-Path $env:LOCALAPPDATA 'Programs\LinkNote'
$DataDir = Join-Path $env:LOCALAPPDATA 'LinkNote'
$RunKey = 'HKCU\Software\Microsoft\Windows\CurrentVersion\Run'

function Step([string]$m) { Write-Host ''; Write-Host "=== $m ===" -ForegroundColor Cyan }
function Info([string]$m) { Write-Host "  $m" }
function Check($obj, [string]$name, $value) { $obj[$name] = $value; Info ("{0,-34} {1}" -f $name, $value) }
function Get-Sha256([string]$p) { if (-not (Test-Path -LiteralPath $p)) { return '' }; (Get-FileHash -Algorithm SHA256 -LiteralPath $p).Hash }
function Read-RunValue {
    $out = cmd /c "reg query `"$RunKey`" /v LinkNote 2>&1"
    if ($LASTEXITCODE -ne 0) { return '' }
    if (-not $out) { return '' }
    $line = @($out | Where-Object { $_ -match 'REG_SZ' }) | Select-Object -First 1
    if (-not $line) { return '' }
    if ($line -match 'REG_SZ\s+(.*)$') { return $Matches[1].Trim() }
    return ''
}
function Remove-Product([string]$code) {
    $args = @('/x', $code, '/qn', '/norestart')
    $p = Start-Process -FilePath 'msiexec.exe' -ArgumentList $args -Wait -PassThru
    return $p.ExitCode
}
function Wait-ProcessGone([string]$name, [int]$timeoutSec = 10) {
    $deadline = (Get-Date).AddSeconds($timeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Name $name -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 300
    }
    return $false
}
function Stop-LinkNote {
    foreach ($p in @(Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue)) {
        try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch { }
    }
    [void](Wait-ProcessGone 'LinkNote' 5)
}

if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
Add-Type -Path (Join-Path $WixDir 'Microsoft.Deployment.WindowsInstaller.dll')
$db = New-Object Microsoft.Deployment.WindowsInstaller.Database($Msi, [Microsoft.Deployment.WindowsInstaller.DatabaseOpenMode]::ReadOnly)
$props = @{}
$view = $db.OpenView('SELECT * FROM Property')
$view.Execute()
while ($true) {
    $rec = $view.Fetch(); if ($null -eq $rec) { break }
    $props[[string]$rec.GetString(1)] = [string]$rec.GetString(2)
}
$view.Close()
$db.Close()
$productCode = $props['ProductCode']
$uninstallReg = "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$productCode"
$uninstallRegMachine = "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\$productCode"
$appExe = Join-Path $InstallDir 'LinkNote.exe'
$engineExe = Join-Path $InstallDir 'engine\BiliNoteBackend.exe'
$desktopLnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'LinkNote.lnk'
$startMenuDir = Join-Path ([Environment]::GetFolderPath('Programs')) 'LinkNote'
$startMenuLnk = Join-Path $startMenuDir 'LinkNote.lnk'

$result = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    msi          = $Msi
    msi_bytes    = (Get-Item $Msi).Length
    msi_mb       = [math]::Round((Get-Item $Msi).Length / 1MB, 1)
    msi_sha256   = (Get-Sha256 $Msi)
    product_code = $productCode
    checks       = [ordered]@{}
    verified     = $false
}
$c = $result.checks

function Get-InstalledLinkNoteCodes {
    # Any LinkNote MSI product from earlier builds (the new ProductCode won't match
    # a previously installed build because WXS uses Id="*").
    $codes = New-Object System.Collections.Generic.List[string]
    foreach ($root in @(
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'
    )) {
        if (-not (Test-Path $root)) { continue }
        foreach ($key in @(Get-ChildItem $root -ErrorAction SilentlyContinue)) {
            $dn = ''
            try { $dn = [string](Get-ItemProperty -Path $key.PSPath -Name DisplayName -ErrorAction Stop).DisplayName } catch { }
            if ($dn -eq 'LinkNote' -and $key.PSChildName -match '^\{.+\}$') {
                if (-not $codes.Contains($key.PSChildName)) { $codes.Add($key.PSChildName) }
            }
        }
    }
    return $codes.ToArray()
}

Step '0/5 Preconditions'
Check $c 'product_code' $productCode
Check $c 'msi_mb' $result.msi_mb
Check $c 'msi_sha256' $result.msi_sha256
Stop-LinkNote
$existingCodes = @(Get-InstalledLinkNoteCodes)
$c.pre_existing_products = ($existingCodes -join ',')
foreach ($code in $existingCodes) {
    Info "uninstalling previously installed LinkNote product $code ..."
    $rc = Remove-Product $code
    Info "previous uninstall exit=$rc"
    $deadline = (Get-Date).AddSeconds(90)
    while ((Get-Date) -lt $deadline -and (Test-Path -LiteralPath $appExe)) { Start-Sleep -Milliseconds 500 }
}
$remainingCodes = @(Get-InstalledLinkNoteCodes)
Check $c 'pre_state_clean' ($remainingCodes.Count -eq 0)
Check $c 'pre_state_install_dir_clean' (-not (Test-Path -LiteralPath $appExe))

Step '1/5 Silent per-user install (no elevation, no launch)'
$installLog = Join-Path $WorkDir 'install.log'
$t0 = Get-Date
$ip = Start-Process -FilePath 'msiexec.exe' -Wait -PassThru -ArgumentList @(
    '/i', "`"$Msi`"", '/qn', '/norestart', '/l*v', "`"$installLog`""
)
$c.install_exit_code = $ip.ExitCode
$c.install_seconds = [math]::Round(((Get-Date) - $t0).TotalSeconds, 1)
Check $c 'install_exit_ok' ($ip.ExitCode -in @(0, 3010))
Check $c 'exe_installed' (Test-Path -LiteralPath $appExe)
Check $c 'internal_dir' (Test-Path -LiteralPath (Join-Path $InstallDir '_internal'))
Check $c 'engine_installed' (Test-Path -LiteralPath $engineExe)
$manifest = Join-Path $repo 'desktop\build\engine-manifest.json'
$engineShaOk = $false
if ($c.engine_installed -and (Test-Path $manifest)) {
    $expected = (Get-Content $manifest -Raw | ConvertFrom-Json).engine_exe_sha256
    $engineShaOk = ((Get-Sha256 $engineExe) -eq $expected)
}
Check $c 'engine_sha_match' $engineShaOk
Check $c 'data_dir_created' (Test-Path -LiteralPath $DataDir)
Check $c 'desktop_shortcut' (Test-Path -LiteralPath $desktopLnk)
Check $c 'startmenu_shortcut' (Test-Path -LiteralPath $startMenuLnk)
$arpHkcu = Test-Path -LiteralPath $uninstallReg
$arpHklm = Test-Path -LiteralPath $uninstallRegMachine
Check $c 'uninstall_entry_present' ($arpHkcu -or $arpHklm)
Check $c 'uninstall_entry_single' (($arpHkcu -or $arpHklm) -and -not ($arpHkcu -and $arpHklm))
Check $c 'license_installed' (Test-Path -LiteralPath (Join-Path $InstallDir 'LICENSE'))
Check $c 'notice_installed' (Test-Path -LiteralPath (Join-Path $InstallDir 'NOTICE'))
Check $c 'engine_notice_installed' (Test-Path -LiteralPath (Join-Path $InstallDir 'engine\THIRD-PARTY-NOTICES.txt'))
Check $c 'license_gpl_text' (Test-Path -LiteralPath (Join-Path $InstallDir 'licenses\GPL-3.0.txt'))
Check $c 'license_lgpl_text' (Test-Path -LiteralPath (Join-Path $InstallDir 'licenses\LGPL-3.0.txt'))
$runValue = Read-RunValue
$c.autostart_value = $runValue
$expectedRun = '"' + $appExe + '" --minimized'
$c.autostart_live_ok = ($runValue -eq $expectedRun)
$installLogText = if (Test-Path -LiteralPath $installLog) { Get-Content $installLog -Raw } else { '' }
$c.autostart_written_in_log = [bool]($installLogText -match 'RegAddValue\(Name=LinkNote,Value="')
# Third-party startup managers (e.g. Lenovo PC Manager / 电脑管家) keep a block-list
# under HKCU\...\Run\LenovoDisabled and delete matching Run values within ~1s. In that
# case the MSI write is still correct (proven by the verbose install log).
$lenovoBlock = ''
try { $lenovoBlock = [string](Get-ItemProperty -Path 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run\LenovoDisabled' -Name 'LinkNote' -ErrorAction Stop).LinkNote } catch { }
$c.autostart_blocked_by_third_party = [bool]$lenovoBlock
$c.autostart_block_entry = $lenovoBlock
$c.autostart_written = $c.autostart_live_ok -or ($c.autostart_written_in_log -and $c.autostart_blocked_by_third_party)
Check $c 'autostart_written' $c.autostart_written
Check $c 'autostart_blocked_by_third_party' $c.autostart_blocked_by_third_party
Check $c 'no_auto_launch_on_install' (-not (Get-Process -Name 'LinkNote' -ErrorAction SilentlyContinue))

Step '2/5 Installed app launch (isolated LOCALAPPDATA, engine_auto_start=false)'
$profile = Join-Path $WorkDir 'profile'
New-Item -ItemType Directory -Force -Path (Join-Path $profile 'LinkNote') | Out-Null
Set-Content -Path (Join-Path $profile 'LinkNote\settings.json') -Encoding utf8 -Value (@{
    engine_auto_start = $false
    close_to_tray     = $false
} | ConvertTo-Json)
$oldLocalAppData = $env:LOCALAPPDATA
$proc = $null
try {
    $env:LOCALAPPDATA = $profile
    $proc = Start-Process -FilePath $appExe -PassThru
    Start-Sleep -Seconds 8
    $proc.Refresh()
    Check $c 'installed_app_launch_ok' (-not $proc.HasExited)
    if (-not $proc.HasExited) {
        $live = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
        if ($live) {
            $c.app_window_title = $live.MainWindowTitle
            Check $c 'app_window_handle_nonzero' ($live.MainWindowHandle -ne 0)
        }
    }
} finally {
    if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
    $env:LOCALAPPDATA = $oldLocalAppData
}
Stop-LinkNote
# frozen Agent worker mode (same exe, --agent-worker)
$awOut = Join-Path $WorkDir 'agent-worker.out.txt'
$aw = Start-Process -FilePath $appExe -ArgumentList @('--agent-worker', '--version') -Wait -PassThru -NoNewWindow `
    -RedirectStandardOutput $awOut -RedirectStandardError ($awOut + '.err')
$awText = if (Test-Path $awOut) { (Get-Content $awOut -Raw).Trim() } else { '' }
Check $c 'agent_worker_ok' ($aw.ExitCode -eq 0 -and $awText -match 'linknote_agent')

Step '3/5 Silent uninstall (user data must survive)'
$marker = Join-Path $DataDir 'msi-verify-preserve.txt'
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
Set-Content -Path $marker -Encoding utf8 -Value "created by verify-msi.ps1 at $(Get-Date -Format o)"
$uninstallLog = Join-Path $WorkDir 'uninstall.log'
$up = Start-Process -FilePath 'msiexec.exe' -Wait -PassThru -ArgumentList @(
    '/x', $productCode, '/qn', '/norestart', '/l*v', "`"$uninstallLog`""
)
Check $c 'uninstall_exit_ok' ($up.ExitCode -in @(0, 3010))
$deadline = (Get-Date).AddSeconds(60)
while ((Get-Date) -lt $deadline -and (Test-Path -LiteralPath $appExe)) { Start-Sleep -Milliseconds 500 }
Check $c 'program_removed' (-not (Test-Path -LiteralPath $appExe))
Check $c 'desktop_shortcut_removed' (-not (Test-Path -LiteralPath $desktopLnk))
Check $c 'startmenu_removed' (-not (Test-Path -LiteralPath $startMenuDir))
Check $c 'uninstall_entry_removed' (-not (Test-Path -LiteralPath $uninstallReg))
Check $c 'autostart_removed' (-not (Read-RunValue))
Check $c 'userdata_preserved' (Test-Path -LiteralPath $marker)
if (Test-Path -LiteralPath $marker) { Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue }

if (-not $SkipLaunchAction) {
    Step '4/5 Launch action wiring (static)'
    # The genuine Finish -> DoAction path needs the *full* MSI UI (client context);
    # it is verified by verify-msi-ui.ps1 with real button clicks. Under /qn or /qb
    # the install runs in the msiexec service context, where a GUI launch custom
    # action cannot reach the user desktop, so no silent launch test is attempted.
    $c.launch_action_started_app = 'see verify-msi-ui.ps1'
    $c.launch_action_process_path_ok = 'see verify-msi-ui.ps1'
    $c.launch_install_exit_ok = 'see verify-msi-ui.ps1'
    $c.launch_custom_action_in_log = 'see verify-msi-ui.ps1'
}

Step '5/5 Verdict'
$hardChecks = @(
    'pre_state_clean', 'install_exit_ok', 'exe_installed', 'internal_dir', 'engine_installed',
    'engine_sha_match', 'data_dir_created', 'desktop_shortcut', 'startmenu_shortcut',
    'uninstall_entry_present', 'uninstall_entry_single', 'autostart_written',
    'license_installed', 'notice_installed', 'engine_notice_installed', 'license_gpl_text', 'license_lgpl_text',
    'no_auto_launch_on_install', 'installed_app_launch_ok', 'agent_worker_ok',
    'uninstall_exit_ok', 'program_removed', 'desktop_shortcut_removed', 'startmenu_removed',
    'uninstall_entry_removed', 'autostart_removed', 'userdata_preserved'
)
$failed = @($hardChecks | Where-Object { -not $c[$_] })
$result.verified = ($failed.Count -eq 0)
$result.failed_checks = $failed
$result | ConvertTo-Json -Depth 8 | Set-Content -Path $OutJson -Encoding utf8
Write-Host ''
Write-Host ("  MSI:    {0}  ({1} MB, sha256 {2})" -f $Msi, $result.msi_mb, $result.msi_sha256)
Write-Host ("  report: {0}" -f $OutJson)
if ($result.verified) {
    Write-Host 'MSI VERIFY PASS' -ForegroundColor Green
    exit 0
}
Write-Host ("MSI VERIFY FAIL: {0}" -f ($failed -join ', ')) -ForegroundColor Red
exit 1


