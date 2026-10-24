# 构建 per-user MSI（Windows Installer）——Inno Setup 被安全软件拦截时的替代安装器。
#
# 前置：WiX 3.14 解压到 <repo>\tools\wix314（含 candle.exe/light.exe/heat.exe）。
# 产物：dist-desktop\LinkNote-Setup-0.2.0.msi
#
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\build-msi.ps1
#   powershell -ExecutionPolicy Bypass -File desktop\packaging\build-msi.ps1 -SkipPayload
[CmdletBinding()]
param(
    [switch]$SkipPayload,
    [string]$Version = "0.2.0"
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$wix = Join-Path $repo "tools\wix314"
$candle = Join-Path $wix "candle.exe"
$light = Join-Path $wix "light.exe"
$heat = Join-Path $wix "heat.exe"
if (-not (Test-Path $candle) -or -not (Test-Path $light) -or -not (Test-Path $heat)) {
    throw "未找到 WiX 3.14：$wix。请把 wix314-binaries.zip 解压到该目录。"
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

if (-not $SkipPayload) {
    if (Test-Path $payload) { Remove-Item -Recurse -Force $payload }
    New-Item -ItemType Directory -Path $payload -Force | Out-Null
    Copy-Item (Join-Path $appDir "*") $payload -Recurse -Force
    Copy-Item $engineDir (Join-Path $payload "engine") -Recurse -Force
}
New-Item -ItemType Directory -Path $build -Force | Out-Null
New-Item -ItemType Directory -Path $obj -Force | Out-Null

$appFiles = Join-Path $build "AppFiles.wxs"
Write-Host "== heat: harvesting payload =="
& $heat dir $payload -cg AppFiles -dr INSTALLDIR -var var.SourceDir -gg -g1 -sfrag -srd -sreg -scom -out $appFiles
if ($LASTEXITCODE -ne 0) { throw "heat 失败（exit $LASTEXITCODE）" }

Write-Host "== candle =="
& $candle -ext WixUIExtension -arch x64 "-dSourceDir=$payload" "-dIconPath=$icon" "-dLicenseRtf=$license" -out "$obj\" $main $appFiles
if ($LASTEXITCODE -ne 0) { throw "candle 失败（exit $LASTEXITCODE）" }

Write-Host "== light =="
& $light -ext WixUIExtension -cultures:en-us -out $msi (Join-Path $obj "LinkNote.msi.wixobj") (Join-Path $obj "AppFiles.wixobj")
# light 在只有 ICE 警告时可能返回 204；以产物存在为准。
if (-not (Test-Path $msi)) { throw "light 失败（exit $LASTEXITCODE），未生成 MSI" }

$item = Get-Item $msi
$hash = (Get-FileHash $msi -Algorithm SHA256).Hash
Write-Host ""
Write-Host "MSI  : $msi"
Write-Host "size : $($item.Length) bytes"
Write-Host "sha256: $hash"
