<#
.SYNOPSIS
  安装 / 修复 LinkNote Android 构建工具链（JDK 21 + Android SDK 36）。可重复执行（幂等）。

.DESCRIPTION
  目录约定（均在项目根目录下）：
    tools\jdk             Temurin 21 JDK（官方 zip 解压，免管理员权限）
    tools\android-sdk     Android SDK root（cmdline-tools\latest + platform-tools +
                          platforms;android-36 + build-tools;36.0.0）
    tools\downloads       下载缓存（zip 不会重复下载）

  每次执行会：
    1. 校验 / 安装 JDK 21（默认 portable Temurin zip；-UseWinget 时优先 winget；
       都失败会尝试 Adoptium API 解析最新 21 GA 下载地址）
    2. 校验 / 安装 Android command-line tools 与 SDK 包，并自动接受 licenses
    3. 写入 JAVA_HOME / ANDROID_HOME / ANDROID_SDK_ROOT（当前进程 + 用户级持久化）
    4. 若 app\android 工程已存在，生成 app\android\local.properties（sdk.dir）

.PARAMETER Root
  项目根目录，默认由脚本位置推导（packaging\android 的上两级）。

.PARAMETER UseWinget
  优先用 winget 安装 EclipseAdoptium.Temurin.21.JDK（需要管理员/可交互会话），
  失败自动回退到 portable zip。

.PARAMETER NoPersistEnv
  只设置当前进程环境变量，不写入用户级环境变量。

.PARAMETER Force
  即使检测到 JDK 已安装也重新下载解压。

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File packaging\android\setup-android.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File C:\yeqkalx\code\bilinote-to-android\packaging\android\setup-android.ps1
  # PowerShell 7+ 也可用 pwsh 替代 powershell
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$UseWinget,
    [switch]$NoPersistEnv,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# 注意：Windows PowerShell 5.1 在 param 默认值里取 $PSScriptRoot 可能为空，故在脚本体内解析。
if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

# 版本 / 下载地址（与已验证的工具链保持一致）
$JdkUrl          = 'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_x64_windows_hotspot_21.0.12.1_1.zip'
$AdoptiumApiUrl  = 'https://api.adoptium.net/v3/assets/latest/21/hotspot?architecture=x64&image_type=jdk&os=windows&vendor=eclipse'
$CmdlineToolsUrl = 'https://dl.google.com/android/repository/commandlinetools-win-13114758_latest.zip'
$SdkPackages     = @('platform-tools', 'platforms;android-36', 'build-tools;36.0.0')

$ToolsDir  = Join-Path $Root 'tools'
$Downloads = Join-Path $ToolsDir 'downloads'
$JdkDir    = Join-Path $ToolsDir 'jdk'
$SdkDir    = Join-Path $ToolsDir 'android-sdk'
$JavaExe   = Join-Path $JdkDir 'bin\java.exe'
$SdkManager = Join-Path $SdkDir 'cmdline-tools\latest\bin\sdkmanager.bat'
$YesFile   = Join-Path $ToolsDir '_yes_licenses.txt'

function Step([string]$message) {
    Write-Host ''
    Write-Host "=== $message ===" -ForegroundColor Cyan
}
function Info([string]$message) { Write-Host "  $message" }
function Warn([string]$message) { Write-Host "  [warn] $message" -ForegroundColor Yellow }

function Download-File([string]$url, [string]$dest, [int64]$minBytes = 1) {
    if (-not (Get-Command curl.exe -ErrorAction SilentlyContinue)) {
        throw 'curl.exe not found (Windows 10 1803+ should provide it)'
    }
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -ge $minBytes)) {
        Info "已存在，跳过下载: $dest ($([math]::Round((Get-Item $dest).Length/1MB,1)) MB)"
        return
    }
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Info "下载($attempt/3): $url"
        # curl 的进度/错误走 stderr；用 cmd 合并到 stdout，避免 EAP=Stop 时误终止。
        cmd /c "curl.exe -L --fail --retry 3 --retry-delay 3 --connect-timeout 30 -o `"$dest`" `"$url`" 2>&1" |
            Select-Object -Last 1 | Out-Host
        if ($LASTEXITCODE -eq 0 -and (Test-Path $dest) -and (Get-Item $dest).Length -ge $minBytes) {
            Info "完成: $([math]::Round((Get-Item $dest).Length/1MB,1)) MB"
            return
        }
        Warn "第 $attempt 次下载失败，重试…"
        Start-Sleep -Seconds 3
    }
    throw "下载失败: $url"
}

function Expand-ZipToNewDir([string]$zip, [string]$targetDir) {
    if (Test-Path $targetDir) { Remove-Item $targetDir -Recurse -Force }
    New-Item -ItemType Directory -Force -Path $targetDir | Out-Null
    cmd /c "tar.exe -xf `"$zip`" -C `"$targetDir`" 2>&1" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "解压失败: $zip" }
}

function Escape-JavaPropertiesPath([string]$path) {
    return $path.Replace('\', '\\').Replace(':', '\:')
}

New-Item -ItemType Directory -Force -Path $ToolsDir, $Downloads | Out-Null
Write-Host "项目根目录: $Root"
Write-Host "工具目录:   $ToolsDir"

# ---------------------------------------------------------------------------
# 1. JDK 21
# ---------------------------------------------------------------------------
Step '1/4 JDK 21'
$JavaHome = $null

if ($UseWinget) {
    $winget = Get-Command winget -ErrorAction SilentlyContinue
    if ($winget) {
        try {
            Info '尝试 winget 安装 EclipseAdoptium.Temurin.21.JDK…'
            & winget install --id EclipseAdoptium.Temurin.21.JDK -e --silent `
                --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
            $wingetJdk = Get-ChildItem "${env:ProgramFiles}\Eclipse Adoptium" -Directory -Filter 'jdk-21*' -ErrorAction SilentlyContinue |
                Sort-Object Name -Descending | Select-Object -First 1
            if ($wingetJdk -and (Test-Path (Join-Path $wingetJdk.FullName 'bin\java.exe'))) {
                $JavaHome = $wingetJdk.FullName
                Info "winget JDK: $JavaHome"
            }
        } catch {
            Warn "winget 安装失败: $($_.Exception.Message)"
        }
    } else {
        Warn 'winget 不可用，改用 portable zip'
    }
}

if (-not $JavaHome -and ((Test-Path $JavaExe) -and (-not $Force))) {
    $JavaHome = $JdkDir
    Info "复用已有 JDK: $JdkDir"
}

if (-not $JavaHome) {
    $jdkZip = Join-Path $Downloads 'temurin21-jdk.zip'
    try {
        Download-File $JdkUrl $jdkZip (150MB)
    } catch {
        Warn "固定下载地址失败，改为查询 Adoptium API…"
        $json = cmd /c "curl.exe -s --connect-timeout 20 --max-time 60 `"$AdoptiumApiUrl`" 2>&1"
        if ($LASTEXITCODE -ne 0 -or -not $json) { throw 'JDK 下载失败（固定地址与 Adoptium API 均不可用）' }
        $assets = ($json -join "`n") | ConvertFrom-Json
        $link = ($assets | Select-Object -First 1).binary.package.link
        if (-not $link) { throw 'JDK 下载失败：Adoptium API 未返回下载地址' }
        Download-File $link $jdkZip (150MB)
    }
    $tmp = Join-Path $ToolsDir '_jdk_tmp'
    Expand-ZipToNewDir $jdkZip $tmp
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (-not $inner) { throw 'JDK 解压后未找到目录' }
    if (Test-Path $JdkDir) { Remove-Item $JdkDir -Recurse -Force }
    Move-Item $inner.FullName $JdkDir
    Remove-Item $tmp -Recurse -Force
    $JavaHome = $JdkDir
    Info "已解压 JDK: $JdkDir"
}

# 注意：java -version 走 stderr；在 $ErrorActionPreference='Stop' 下会变成终止错误，
# 因此用 cmd 把 stderr 合进 stdout（下同）。
& cmd /c "`"$JavaHome\bin\java.exe`" -version 2>&1" | Out-Host
$env:JAVA_HOME = $JavaHome
$env:PATH = "$(Join-Path $JavaHome 'bin');$env:PATH"

# ---------------------------------------------------------------------------
# 2. Android command-line tools
# ---------------------------------------------------------------------------
Step '2/4 Android command-line tools'
if (-not (Test-Path $SdkManager)) {
    $cmdZip = Join-Path $Downloads 'commandlinetools-win.zip'
    Download-File $CmdlineToolsUrl $cmdZip (50MB)
    $tmp = Join-Path $ToolsDir '_cmdline_tmp'
    Expand-ZipToNewDir $cmdZip $tmp
    $inner = Get-ChildItem $tmp -Directory | Select-Object -First 1
    if (-not $inner) { throw 'cmdline-tools 解压后未找到目录' }
    $latest = Join-Path $SdkDir 'cmdline-tools\latest'
    if (Test-Path $latest) { Remove-Item $latest -Recurse -Force }
    New-Item -ItemType Directory -Force -Path (Split-Path $latest) | Out-Null
    Move-Item $inner.FullName $latest
    Remove-Item $tmp -Recurse -Force
    Info "已安装 cmdline-tools: $latest"
} else {
    Info "复用已有 cmdline-tools: $SdkManager"
}
& cmd /c "`"$SdkManager`" --version 2>&1" | Out-Host

# ---------------------------------------------------------------------------
# 3. SDK packages + licenses
# ---------------------------------------------------------------------------
Step '3/4 Android SDK 包 + licenses'
$env:ANDROID_HOME = $SdkDir
$env:ANDROID_SDK_ROOT = $SdkDir
[System.IO.File]::WriteAllText($YesFile, ((1..300 | ForEach-Object { 'y' }) -join "`r`n"))

Info '接受 SDK licenses…'
cmd /c "`"$SdkManager`" --sdk_root=`"$SdkDir`" --licenses 2>&1 < `"$YesFile`"" | Select-Object -Last 2 | Out-Host

Info "安装 SDK 包: $($SdkPackages -join ', ')"
cmd /c "`"$SdkManager`" --sdk_root=`"$SdkDir`" $($SdkPackages -join ' ') 2>&1 < `"$YesFile`"" | Select-Object -Last 5 | Out-Host

if (-not (Test-Path (Join-Path $SdkDir 'platforms\android-36'))) {
    Warn 'platforms;android-36 未安装成功，尝试 35 回退…'
    cmd /c "`"$SdkManager`" --sdk_root=`"$SdkDir`" platforms;android-35 build-tools;35.0.0 2>&1 < `"$YesFile`"" | Select-Object -Last 5 | Out-Host
}
if (-not (Test-Path (Join-Path $SdkDir 'build-tools\36.0.0'))) {
    throw 'build-tools;36.0.0 安装失败，请检查网络后重跑本脚本'
}

& cmd /c "`"$SdkDir\platform-tools\adb.exe`" version 2>&1" | Select-Object -First 2 | Out-Host
Get-ChildItem (Join-Path $SdkDir 'build-tools'), (Join-Path $SdkDir 'platforms') -Directory -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty Name | ForEach-Object { Info "已安装: $_" }

# ---------------------------------------------------------------------------
# 4. 环境变量 + local.properties
# ---------------------------------------------------------------------------
Step '4/4 环境变量与 local.properties'
$env:PATH = "$(Join-Path $SdkDir 'platform-tools');$env:PATH"

if (-not $NoPersistEnv) {
    [Environment]::SetEnvironmentVariable('JAVA_HOME', $JavaHome, 'User')
    [Environment]::SetEnvironmentVariable('ANDROID_HOME', $SdkDir, 'User')
    [Environment]::SetEnvironmentVariable('ANDROID_SDK_ROOT', $SdkDir, 'User')
    Info '已写入用户级环境变量（新开的终端生效）'
} else {
    Info '按 -NoPersistEnv 跳过用户级环境变量持久化'
}

$androidApp = Join-Path $Root 'app\android'
if (Test-Path $androidApp) {
    $localProps = Join-Path $androidApp 'local.properties'
    $sdkEscaped = Escape-JavaPropertiesPath $SdkDir
    "sdk.dir=$sdkEscaped" | Set-Content -Path $localProps -Encoding ascii
    Info "已写入 $localProps"
}

Write-Host ''
Write-Host '工具链就绪。' -ForegroundColor Green
Write-Host "  JAVA_HOME        = $JavaHome"
Write-Host "  ANDROID_HOME     = $SdkDir"
Write-Host "  ANDROID_SDK_ROOT = $SdkDir"
Write-Host ''
Write-Host '下一步：'
Write-Host '  powershell -ExecutionPolicy Bypass -File packaging\android\build-android.ps1'
Write-Host '  # 或手动：'
Write-Host '  cd app; npx cap sync android'
Write-Host '  cd app\android; .\gradlew.bat assembleDebug'

