# 构建 per-user MSI（Windows Installer）——Inno Setup 被安全软件拦截时的替代安装器。
#
# 前置：WiX 3.14 解压到 <repo>\tools\wix314（含 candle.exe/light.exe/heat.exe）。
# 产物：dist-desktop\LinkNote-Setup-<ver>.msi
#
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\build-msi.ps1
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\build-msi.ps1 -SkipPayload
#
# 关键点（2026-10-24 修复）：
#  * Package/@InstallScope 在 WiX 3.14 下不会生成 ALLUSERS=2/MSIINSTALLPERUSER=1，
#    导致 MSI 走 msiexec 服务/提权上下文，HKCU Run 写入落在错误用户上下文。
#    WXS 已显式声明这两个属性，本脚本在 post-build 检查它们确实进了 Property 表。
#  * ExitDialog「Launch LinkNote」复选框由 WXS 的 WIXUI_EXITDIALOGOPTIONALCHECKBOX*
#    + Publish/DoAction(LaunchLinkNote) 实现；可用 LAUNCHLINKNOTE=1 静默验证启动路径。
[CmdletBinding()]
param(
    [switch]$SkipPayload,
    [string]$Version = "",
    [string]$WixDir = ""
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $WixDir) { $WixDir = Join-Path $repo "tools\wix314" }
$candle = Join-Path $WixDir "candle.exe"
$light = Join-Path $WixDir "light.exe"
$heat = Join-Path $WixDir "heat.exe"
$dark = Join-Path $WixDir "dark.exe"
if (-not (Test-Path $candle) -or -not (Test-Path $light) -or -not (Test-Path $heat)) {
    throw "未找到 WiX 3.14：$WixDir。请把 wix314-binaries.zip 解压到该目录（或 -WixDir 指定）。"
}

$appDir = Join-Path $repo "dist-desktop\LinkNote"
$engineDir = Join-Path $repo "desktop\build\engine"
$outDir = Join-Path $repo "dist-desktop"
$msi = Join-Path $outDir "LinkNote-Setup-$Version.msi"
$build = Join-Path $repo "desktop\build\msi"
$payload = Join-Path $build "payload"
$obj = Join-Path $build "obj"
$source = Join-Path $PSScriptRoot "msi"
$main = Join-Path $source "LinkNote.msi.wxs"
$license = Join-Path $source "license.rtf"
$icon = Join-Path $repo "desktop\linknote_desktop\resources\icon.ico"

if (-not (Test-Path (Join-Path $appDir "LinkNote.exe"))) {
    throw "缺少 $appDir\LinkNote.exe；请先运行 build-desktop.ps1。"
}
if (-not (Test-Path (Join-Path $engineDir "BiliNoteBackend.exe"))) {
    throw "缺少内置引擎 $engineDir；请先运行 stage-engine.ps1。"
}

if (-not $Version) {
    $pyproject = Join-Path $repo "desktop\pyproject.toml"
    if (Test-Path $pyproject) {
        $m = Select-String -Path $pyproject -Pattern '^version\s*=\s*"([0-9]+\.[0-9]+\.[0-9]+)"' | Select-Object -First 1
        if ($m) { $Version = $m.Matches[0].Groups[1].Value }
    }
    if (-not $Version) { $Version = "0.3.0" }
}
$msi = Join-Path $outDir "LinkNote-Setup-$Version.msi"

if (-not $SkipPayload) {
    if (Test-Path $payload) { Remove-Item -Recurse -Force $payload }
    New-Item -ItemType Directory -Path $payload -Force | Out-Null
    Copy-Item (Join-Path $appDir "*") $payload -Recurse -Force
    Copy-Item $engineDir (Join-Path $payload "engine") -Recurse -Force
    # Licenses / third-party notices (BiliNote MIT attribution, LinkNote MIT)
    foreach ($licName in @("LICENSE", "NOTICE")) {
        $licSrc = Join-Path $repo $licName
        if (Test-Path $licSrc) { Copy-Item $licSrc (Join-Path $payload $licName) -Force }
    }
    $engineNotice = Join-Path $repo "desktop\installer\THIRD-PARTY-NOTICES.txt"
    if (Test-Path $engineNotice) { Copy-Item $engineNotice (Join-Path $payload "engine\THIRD-PARTY-NOTICES.txt") -Force }
    # GPL-3.0 / LGPL-3.0 full texts -> <install dir>\licenses\
    $licensesDir = Join-Path $repo "desktop\installer\licenses"
    if (Test-Path $licensesDir) { Copy-Item $licensesDir $payload -Recurse -Force }
    # make sure the harvested tree never contains local runtime state
    foreach ($bad in @("bili_note.db", "Uninstall BiliNote.lnk", "app.exe")) {
        $hit = Get-ChildItem $payload -Recurse -File -Filter $bad -ErrorAction SilentlyContinue
        if ($hit) { throw "payload 内发现不应打包的文件：$($hit[0].FullName)" }
    }
}
New-Item -ItemType Directory -Path $build -Force | Out-Null
New-Item -ItemType Directory -Path $obj -Force | Out-Null
if (Test-Path $msi) { Remove-Item -LiteralPath $msi -Force }

$appFiles = Join-Path $build "AppFiles.wxs"
Write-Host "== heat: harvesting payload =="
& $heat dir $payload -cg AppFiles -dr INSTALLDIR -var var.SourceDir -gg -g1 -sfrag -srd -sreg -scom -out $appFiles
if ($LASTEXITCODE -ne 0) { throw "heat 失败（exit $LASTEXITCODE）" }

Write-Host "== candle =="
& $candle -ext WixUIExtension -arch x64 `
    "-dSourceDir=$payload" "-dIconPath=$icon" "-dLicenseRtf=$license" "-dProductVersion=$Version" `
    -out "$obj\" $main $appFiles
if ($LASTEXITCODE -ne 0) { throw "candle 失败（exit $LASTEXITCODE）" }

Write-Host "== light =="
& $light -ext WixUIExtension -cultures:en-us `
    -sice:ICE64 -sice:ICE69 -sice:ICE71 -sice:ICE80 `
    -out $msi (Join-Path $obj "LinkNote.msi.wixobj") (Join-Path $obj "AppFiles.wixobj")
# light 在只有 ICE 警告时可能返回 204；以产物存在为准。
if (-not (Test-Path $msi)) { throw "light 失败（exit $LASTEXITCODE），未生成 MSI" }

# ---------------------------------------------------------------------------
# Post-build static checks (fail early if the per-user/launch plumbing regressed)
# ---------------------------------------------------------------------------
Write-Host "== post-build checks =="
$dtf = Join-Path $WixDir "Microsoft.Deployment.WindowsInstaller.dll"
Add-Type -Path $dtf
$db = New-Object Microsoft.Deployment.WindowsInstaller.Database($msi, [Microsoft.Deployment.WindowsInstaller.DatabaseOpenMode]::ReadOnly)

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
$checks = [ordered]@{}
$props = @{}
$propRows = Read-MsiTable "SELECT * FROM Property"
foreach ($r in @($propRows)) { $props[[string]$r[0]] = [string]$r[1] }
$checks.per_user_allusers = ($props["ALLUSERS"] -eq "2")
$checks.per_user_msiinstallperuser = ($props["MSIINSTALLPERUSER"] -eq "1")
$checks.exit_checkbox_text = ($props["WIXUI_EXITDIALOGOPTIONALCHECKBOXTEXT"] -eq "Launch LinkNote")
$checks.exit_checkbox_default = ($props["WIXUI_EXITDIALOGOPTIONALCHECKBOX"] -eq "1")
$checks.launch_custom_action = [bool](@((Read-MsiTable "SELECT * FROM CustomAction") | Where-Object { $_[0] -eq "LaunchLinkNote" }).Count)
$checks.exit_finish_doaction = [bool](@((Read-MsiTable "SELECT * FROM ControlEvent") | Where-Object {
    $_[0] -eq "ExitDialog" -and $_[1] -eq "Finish" -and $_[2] -eq "DoAction" -and $_[3] -eq "LaunchLinkNote"
}).Count)
$checks.autostart_registry = [bool](@((Read-MsiTable "SELECT * FROM Registry") | Where-Object {
    $_[2] -eq "Software\Microsoft\Windows\CurrentVersion\Run" -and $_[3] -eq "LinkNote"
}).Count)
$checks.autostart_registry_component = [bool](@((Read-MsiTable "SELECT * FROM Registry") | Where-Object {
    $_[3] -eq "LinkNote" -and $_[5] -eq "Autostart"
}).Count)
$checks.autostart_in_feature = [bool](@((Read-MsiTable "SELECT * FROM FeatureComponents") | Where-Object {
    $_[1] -eq "Autostart"
}).Count)
$db.Close()

$failed = @($checks.Keys | Where-Object { -not $checks[$_] })
foreach ($k in $checks.Keys) { Write-Host ("  {0,-34} {1}" -f $k, $checks[$k]) }
if ($failed.Count -gt 0) { throw "post-build MSI checks failed: $($failed -join ', ')" }

$item = Get-Item $msi
$hash = (Get-FileHash $msi -Algorithm SHA256).Hash
Write-Host ""
Write-Host "MSI  : $msi"
Write-Host "size : $($item.Length) bytes"
Write-Host "sha256: $hash"
Write-Host "per-user / launch-action / autostart: all static checks PASS"




