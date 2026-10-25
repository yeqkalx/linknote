<#
.SYNOPSIS
  构建 LinkNote debug APK：cap sync -> 应用分享接收补丁 -> gradlew assembleDebug -> 复制到 release\。

.DESCRIPTION
  前置条件：先运行 setup-android.ps1 安装 JDK/SDK；app\www\index.html 已就绪
  （由 app-dev / scripts\sync-app.mjs 生成）。

  生成物：release\LinkNote-debug.apk

.PARAMETER Root
  项目根目录，默认由脚本位置推导。

.PARAMETER SkipSync
  跳过 npx cap sync android（仅当确定 web assets 无变化时使用）。

.PARAMETER SkipPatch
  不应用分享接收补丁（MainActivity / Manifest / 应用名）。

.PARAMETER Clean
  构建前先执行 gradlew clean（从零重编译，用于验收/排查）。
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$SkipSync,
    [switch]$SkipPatch,
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'

# 注意：Windows PowerShell 5.1 在 param 默认值里取 $PSScriptRoot 可能为空，故在脚本体内解析。
if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
}

function Step([string]$message) {
    Write-Host ''
    Write-Host "=== $message ===" -ForegroundColor Cyan
}

# 统一跑外部命令：临时把 EAP 调成 Continue，避免 PS5.1 把原生程序 stderr 当终止错误。
function Invoke-External {
    param([scriptblock]$Block, [string]$What)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Block 2>&1 | Out-Host
    } finally {
        $ErrorActionPreference = $prev
    }
    if ($LASTEXITCODE -ne 0) { throw "$What 失败 (exit $LASTEXITCODE)" }
}

$JdkDir  = Join-Path $Root 'tools\jdk'
$SdkDir  = Join-Path $Root 'tools\android-sdk'
$AppDir  = Join-Path $Root 'app'
$AndroidDir = Join-Path $AppDir 'android'
$ReleaseDir = Join-Path $Root 'release'
$CapCli  = Join-Path $AppDir 'node_modules\@capacitor\cli\bin\capacitor'
$Patches = Join-Path $PSScriptRoot 'patches'

# ---- 环境 ----
Step '环境'
if (-not (Test-Path (Join-Path $JdkDir 'bin\java.exe'))) {
    throw 'JDK 未安装：先运行 packaging\android\setup-android.ps1'
}
if (-not (Test-Path (Join-Path $SdkDir 'build-tools\36.0.0'))) {
    throw 'Android SDK 未安装：先运行 packaging\android\setup-android.ps1'
}
$env:JAVA_HOME = $JdkDir
$env:ANDROID_HOME = $SdkDir
$env:ANDROID_SDK_ROOT = $SdkDir
$env:PATH = "$(Join-Path $JdkDir 'bin');$(Join-Path $SdkDir 'platform-tools');$env:PATH"
Write-Host "JAVA_HOME=$env:JAVA_HOME"
Write-Host "ANDROID_HOME=$env:ANDROID_HOME"

# ---- Capacitor 工程 ----
Step 'Capacitor 工程'
if (-not (Test-Path (Join-Path $AppDir 'www\index.html'))) {
    throw 'app\www\index.html 不存在：先构建/同步 PWA 资源（npm run sync 或由 app-dev 生成）'
}
Push-Location $AppDir
try {
    if (-not (Test-Path (Join-Path $AndroidDir 'app\build.gradle'))) {
        Write-Host 'app\android 不存在，执行 cap add android…'
        Invoke-External { & node $CapCli add android } 'cap add android'
    }
    if (-not $SkipSync) {
        Write-Host '同步 Web 资源到 Android 工程…'
        Invoke-External { & node $CapCli sync android } 'cap sync android'
    }
} finally {
    Pop-Location
}

# ---- 品牌图标（assets\android-icons → app\android\...\res，幂等）----
$iconSrc = Join-Path $Root 'assets\android-icons'
if (Test-Path $iconSrc) {
    Step '应用 LinkNote 品牌图标'
    $resDir = Join-Path $AndroidDir 'app\src\main\res'
    foreach ($d in @('mdpi', 'hdpi', 'xhdpi', 'xxhdpi', 'xxxhdpi')) {
        $target = Join-Path $resDir "mipmap-$d"
        New-Item -ItemType Directory -Force -Path $target | Out-Null
        foreach ($name in @('ic_launcher.png', 'ic_launcher_round.png')) {
            $srcFile = Join-Path $iconSrc "legacy\$d\$name"
            if (Test-Path $srcFile) { Copy-Item $srcFile (Join-Path $target $name) -Force }
        }
        $fg = Join-Path $iconSrc "foreground\$d\ic_launcher_foreground.png"
        if (Test-Path $fg) { Copy-Item $fg (Join-Path $target 'ic_launcher_foreground.png') -Force }
        Write-Host "  mipmap-${d}: ic_launcher / ic_launcher_round / ic_launcher_foreground"
    }
    $bgSrc = Join-Path $iconSrc 'values\ic_launcher_background.xml'
    if (Test-Path $bgSrc) {
        New-Item -ItemType Directory -Force -Path (Join-Path $resDir 'values') | Out-Null
        Copy-Item $bgSrc (Join-Path $resDir 'values\ic_launcher_background.xml') -Force
        Write-Host '  values\ic_launcher_background.xml (#1C1A18)'
    }
} else {
    Write-Host '  assets\android-icons 不存在，跳过图标替换'
}

# ---- 分享接收补丁（MainActivity / Manifest / 应用名）----
if (-not $SkipPatch) {
    Step '应用 Android 分享接收补丁'
    $javaDir = Join-Path $AndroidDir 'app\src\main\java\xyz\xykong\linknote'
    New-Item -ItemType Directory -Force -Path $javaDir | Out-Null
    Copy-Item (Join-Path $Patches 'MainActivity.java') (Join-Path $javaDir 'MainActivity.java') -Force
    Write-Host '  MainActivity.java (ACTION_SEND + LinkNoteNative.getSharedText)'

    $manifestPath = Join-Path $AndroidDir 'app\src\main\AndroidManifest.xml'
    $manifest = Get-Content $manifestPath -Raw
    if ($manifest -notmatch 'android\.intent\.action\.SEND') {
        $pattern = '(?s)(<intent-filter>\s*<action android:name="android.intent.action.MAIN" />.*?</intent-filter>)'
        $replacement = @'
$1

            <!-- Receive links / text shared from other apps (Chrome, Bilibili, WeChat, ...). -->
            <intent-filter>
                <action android:name="android.intent.action.SEND" />
                <category android:name="android.intent.category.DEFAULT" />
                <data android:mimeType="text/plain" />
            </intent-filter>
'@
        # .NET 实例方法才支持 count 参数；静态 4 参重载的第 4 参是 RegexOptions，不可传 count。
        $rx = New-Object System.Text.RegularExpressions.Regex($pattern)
        $manifest = $rx.Replace($manifest, $replacement, 1)
        [System.IO.File]::WriteAllText($manifestPath, $manifest, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host '  AndroidManifest.xml (SEND text/plain intent-filter)'
    } else {
        Write-Host '  AndroidManifest.xml 已含 SEND filter，跳过'
    }

    # 隐私加固：token 存在 WebView localStorage，禁止系统备份/换机迁移带走（幂等）。
    if ($manifest -match 'android:allowBackup="true"') {
        $manifest = $manifest.Replace('android:allowBackup="true"', 'android:allowBackup="false"')
        [System.IO.File]::WriteAllText($manifestPath, $manifest, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host '  AndroidManifest.xml (android:allowBackup=false)'
    } else {
        Write-Host '  AndroidManifest.xml allowBackup 已为 false（或未找到），跳过'
    }

    $stringsPath = Join-Path $AndroidDir 'app\src\main\res\values\strings.xml'
    if (Test-Path $stringsPath) {
        $strings = Get-Content $stringsPath -Raw
        if ($strings -match '<string name="app_name">LinkNote</string>') {
            $strings = $strings.Replace('<string name="app_name">LinkNote</string>', '<string name="app_name">链记</string>')
            $strings = $strings.Replace('<string name="title_activity_main">LinkNote</string>', '<string name="title_activity_main">链记</string>')
            [System.IO.File]::WriteAllText($stringsPath, $strings, (New-Object System.Text.UTF8Encoding($false)))
            Write-Host '  res\values\strings.xml (应用名 = 链记)'
        }
    }

    # 版本号：对应 PWA v0.4（自托管）这一轮发版（versionCode 4 / versionName 1.3）；
    # 仅当仍是 cap add 默认值 1/1.0 时改写（幂等，cap add 重建后也能自动应用）。
    $gradlePath = Join-Path $AndroidDir 'app\build.gradle'
    if (Test-Path $gradlePath) {
        $gradle = Get-Content $gradlePath -Raw
        if ($gradle -match 'versionCode\s+1\b' -and $gradle -match 'versionName\s+"1\.0"') {
            $gradle = [regex]::Replace($gradle, 'versionCode\s+1\b', 'versionCode 4')
            $gradle = $gradle.Replace('versionName "1.0"', 'versionName "1.3"')
            [System.IO.File]::WriteAllText($gradlePath, $gradle, (New-Object System.Text.UTF8Encoding($false)))
            Write-Host '  app\build.gradle (versionCode 4 / versionName 1.3)'
        } else {
            Write-Host '  app\build.gradle 版本号已是自定义值，跳过'
        }
    }
}

# ---- Gradle 构建 ----
Step 'gradlew assembleDebug'
$sdkEscaped = $SdkDir.Replace('\', '\\').Replace(':', '\:')
"sdk.dir=$sdkEscaped" | Set-Content -Path (Join-Path $AndroidDir 'local.properties') -Encoding ascii

Push-Location $AndroidDir
try {
    if ($Clean) {
        Invoke-External { & .\gradlew.bat clean --console=plain } 'gradlew clean'
    }
    Invoke-External { & .\gradlew.bat assembleDebug --console=plain } 'gradlew assembleDebug'
} finally {
    Pop-Location
}

$apk = Join-Path $AndroidDir 'app\build\outputs\apk\debug\app-debug.apk'
if (-not (Test-Path $apk)) { throw "未找到 APK: $apk" }
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$releaseApk = Join-Path $ReleaseDir 'LinkNote-debug.apk'
Copy-Item $apk $releaseApk -Force

Write-Host ''
Write-Host '构建完成。' -ForegroundColor Green
Write-Host ("  APK: {0} ({1:N1} MB)" -f $releaseApk, ((Get-Item $releaseApk).Length / 1MB))
Write-Host '  安装: adb install -r release\LinkNote-debug.apk'
