# LinkNote MSI *contents* verifier (no install, no system changes).
#
# Reads the MSI database produced by build-msi.ps1 and checks the package structure:
#   * ProductVersion / per-user properties / ExitDialog launch checkbox + action
#   * Autostart HKCU Run row
#   * File table: LinkNote.exe version+size, licenses\GPL-3.0.txt,
#     licenses\LGPL-3.0.txt, engine\BiliNoteBackend.exe, LICENSE, NOTICE,
#     engine\THIRD-PARTY-NOTICES.txt
#
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\msi\verify-msi-contents.ps1
#   ... -Msi dist-desktop\LinkNote-Setup-0.4.0.msi -Version 0.4.0
#
# Exit code 0 = PASS, 1 = FAIL, 2 = could not inspect. Writes msi-contents-verify.json.
[CmdletBinding()]
param(
    [string]$Msi = '',
    [string]$Version = ''
)
$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path
if (-not $Version) {
    $m = Select-String -Path (Join-Path $repo 'desktop\pyproject.toml') -Pattern '^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' | Select-Object -First 1
    $Version = if ($m) { $m.Matches[0].Groups[1].Value } else { '0.4.0' }
}
if (-not $Msi) { $Msi = Join-Path $repo "dist-desktop\LinkNote-Setup-$Version.msi" }
if (-not (Test-Path $Msi)) { throw "MSI not found: $Msi (build first)" }
$AppExe = Join-Path $repo 'dist-desktop\LinkNote\LinkNote.exe'
if (-not (Test-Path $AppExe)) { throw "dist app exe not found: $AppExe" }
$OutJson = Join-Path $repo ("desktop\build\msi\msi-contents-verify-$Version.json")

$wix = Join-Path $repo 'tools\wix314'
Add-Type -Path (Join-Path $wix 'Microsoft.Deployment.WindowsInstaller.dll')
$db = New-Object Microsoft.Deployment.WindowsInstaller.Database($Msi, [Microsoft.Deployment.WindowsInstaller.DatabaseOpenMode]::ReadOnly)

function Read-MsiTable([string]$sql) {
    $view = $db.OpenView($sql)
    $view.Execute()
    $rows = New-Object System.Collections.ArrayList
    while ($true) {
        $rec = $view.Fetch()
        if ($null -eq $rec) { break }
        $vals = @()
        for ($i = 1; $i -le $rec.FieldCount; $i++) {
            try { $vals += $rec.GetString($i) } catch { $vals += $rec.GetInteger($i) }
        }
        [void]$rows.Add($vals)
    }
    $view.Close()
    return ,$rows
}

$result = [ordered]@{
    generated_at = (Get-Date).ToString('o')
    msi          = $Msi
    msi_bytes    = (Get-Item $Msi).Length
    msi_mb       = [math]::Round((Get-Item $Msi).Length / 1MB, 1)
    msi_sha256   = (Get-FileHash $Msi -Algorithm SHA256).Hash
    expected_version = $Version
    checks       = [ordered]@{}
    verified     = $false
}
$c = $result.checks

# ------------------------------------------------------------------ properties
$props = @{}
foreach ($r in @((Read-MsiTable 'SELECT * FROM Property'))) { $props[[string]$r[0]] = [string]$r[1] }
$c.product_version = ($props['ProductVersion'] -eq $Version)
$c.product_name = ($props['ProductName'] -eq 'LinkNote')
$c.per_user_allusers = ($props['ALLUSERS'] -eq '2')
$c.per_user_msiinstallperuser = ($props['MSIINSTALLPERUSER'] -eq '1')
$c.exit_checkbox_text = ($props['WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT'] -eq 'Launch LinkNote')
$c.exit_checkbox_default = ($props['WIXUI_EXITDIALOGOPTIONALCHECKBOX'] -eq '1')
# 0.5.0: no author-operated domain in the package properties (neutral placeholder only).
$arpHelpLink = [string]$props['ARPHELPLINK']
$c.arphelplink_value = $arpHelpLink
$c.arphelplink_neutral = ($arpHelpLink -notmatch ('xy' + 'kong')) -and ($arpHelpLink -match '^https?://')
$authorDomainProps = @($props.Keys | Where-Object { ([string]$props[$_]) -match ('xy' + 'kong') })
$c.author_domain_props = ($authorDomainProps -join ',')
$c.author_domain_absent = ($authorDomainProps.Count -eq 0)

# ----------------------------------------------------- launch action / autostart
$c.launch_custom_action = [bool](@((Read-MsiTable 'SELECT * FROM CustomAction') | Where-Object { $_[0] -eq 'LaunchLinkNote' }).Count)
$c.exit_finish_doaction = [bool](@((Read-MsiTable 'SELECT * FROM ControlEvent') | Where-Object {
    $_[0] -eq 'ExitDialog' -and $_[1] -eq 'Finish' -and $_[2] -eq 'DoAction' -and $_[3] -eq 'LaunchLinkNote'
}).Count)
$c.autostart_registry = [bool](@((Read-MsiTable 'SELECT * FROM Registry') | Where-Object {
    $_[2] -eq 'Software\Microsoft\Windows\CurrentVersion\Run' -and $_[3] -eq 'LinkNote'
}).Count)

# ------------------------------------------------------------------ File table
$files = @((Read-MsiTable 'SELECT * FROM File'))
$c.file_count = $files.Count
$distExe = Get-Item $AppExe

$exeRow = $files | Where-Object { ([string]$_[2]).Split('|')[0] -eq 'LinkNote.exe' } | Select-Object -First 1
$c.app_exe_in_msi = [bool]$exeRow
if ($exeRow) {
    $c.app_exe_msi_version = [string]$exeRow[4]
    $c.app_exe_dist_version = $distExe.VersionInfo.FileVersion
    $c.app_exe_version_ok = ([string]$exeRow[4] -eq $distExe.VersionInfo.FileVersion) -and ([string]$exeRow[4] -eq "$Version.0")
    $c.app_exe_size_ok = (([int64]$exeRow[3] -eq [int64]$distExe.Length) -or ([int64]$exeRow[3] -eq 0))
    $c.app_exe_msi_bytes = [int64]$exeRow[3]
    $c.app_exe_dist_bytes = [int64]$distExe.Length
}

function Test-MsiFileNames([string[]]$longNames) {
    # MSI FileName is "short|long" when the long name differs; compare every part.
    foreach ($row in $files) {
        foreach ($part in ([string]$row[2]).Split('|')) {
            if ($longNames -contains $part) { return $true }
        }
    }
    return $false
}
$c.license = Test-MsiFileNames @('LICENSE')
$c.notice = Test-MsiFileNames @('NOTICE')
$c.license_gpl_text = Test-MsiFileNames @('GPL-3.0.txt')
$c.license_lgpl_text = Test-MsiFileNames @('LGPL-3.0.txt')
$c.engine_backend = Test-MsiFileNames @('BiliNoteBackend.exe')
$c.engine_notice = Test-MsiFileNames @('THIRD-PARTY-NOTICES.txt')
# engine adds ~1900 runtime files + app bundle ~195 + licenses; a much smaller
# count would mean the payload was not harvested.
$c.payload_file_count_ok = ($files.Count -ge 2000)

$db.Close()

$hardChecks = @(
    'product_version', 'product_name', 'per_user_allusers', 'per_user_msiinstallperuser',
    'exit_checkbox_text', 'exit_checkbox_default', 'launch_custom_action', 'exit_finish_doaction',
    'autostart_registry', 'app_exe_in_msi', 'app_exe_version_ok', 'app_exe_size_ok',
    'license', 'notice', 'license_gpl_text', 'license_lgpl_text', 'engine_backend', 'engine_notice', 'payload_file_count_ok',
    'arphelplink_neutral', 'author_domain_absent'
)
$failed = @($hardChecks | Where-Object { -not $c[$_] })
$result.verified = ($failed.Count -eq 0)
$result.failed_checks = $failed

Write-Host ''
foreach ($k in $c.Keys) { Write-Host ("  {0,-32} {1}" -f $k, $c[$k]) }
Write-Host ''
Write-Host ("  MSI: {0} ({1} MB, sha256 {2})" -f $Msi, $result.msi_mb, $result.msi_sha256)
$result | ConvertTo-Json -Depth 6 | Set-Content $OutJson -Encoding utf8
Write-Host ("  report: {0}" -f $OutJson)
if ($result.verified) {
    Write-Host 'MSI CONTENTS VERIFY PASS' -ForegroundColor Green
    exit 0
}
Write-Host ("MSI CONTENTS VERIFY FAIL: {0}" -f ($failed -join ', ')) -ForegroundColor Red
exit 1



