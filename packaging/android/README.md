# LinkNote Android 打包（Capacitor 壳）

把 `app/www` 的 LinkNote PWA 装进 Capacitor 8.5.3 Android 壳，产出可安装的 debug APK，并让 App 能接收其他 Android 应用分享的文本/链接。

- APK：`release\LinkNote-debug.apk`
- 包名：`xyz.xykong.linknote`
- 应用名（launcher label）：`链记`（Capacitor appName 仍为 `LinkNote`）
- minSdk 24 / targetSdk 36 / compileSdk 36，INTERNET 权限
- 分享入口：`ACTION_SEND` + `text/plain`

---

## 1. 目录与文件

```
tools/                              本机工具链（JDK / SDK / 下载缓存，勿提交 git）
├─ jdk/                             Temurin 21 JDK（zip 解压）
├─ android-sdk/                     Android SDK root
└─ downloads/                       zip 缓存（重复执行 setup 不会重复下载）

packaging/android/
├─ setup-android.ps1                安装/修复工具链（幂等）
├─ build-android.ps1                一键构建：cap sync → 品牌图标 → 打补丁 → assembleDebug → 复制 APK
├─ patches/MainActivity.java        分享接收实现（app/android 被 gitignore，故在此留存）
└─ README.md                        本文件

packaging/android-probe/            工具链探针工程（Hello World，可删）
assets/android-icons/               LinkNote 品牌图标源（legacy/foreground/values，构建时幂等覆盖到 Android res）
app/capacitor.config.json           Capacitor 配置（appId/appName/webDir/androidScheme）
app/android/                        生成的 Android 工程（含上述补丁与图标）
app/www/                            PWA 资源（由 app-dev 维护，构建前必须存在 index.html）
release/LinkNote-debug.apk          最终产物
```

## 2. 已验证环境版本（2026-10-24，本机）

| 组件 | 版本 / 位置 |
| --- | --- |
| OS | Windows 11 x64（仅 Windows PowerShell 5.1，无 pwsh） |
| Node / npm | v24.19.0 / 11.17.0 |
| JDK | Temurin 21.0.12.1+1 LTS → `tools\jdk` |
| Android cmdline-tools | 13114758（`tools\android-sdk\cmdline-tools\latest`） |
| Android SDK | platform-tools 37.0.1；platforms;android-36；build-tools;36.0.0（+35.0.0 自动补装） |
| Gradle | 8.14.3（wrapper 自动下载） |
| AGP | 8.13.0 |
| Capacitor | 8.5.3（cli/core/android） |

> 安装了 build-tools 36 之后，Gradle 构建过程中还会自动补装 `build-tools;35.0.0`（AGP 依赖），属正常现象。

## 3. 一键安装工具链

```powershell
powershell -ExecutionPolicy Bypass -File packaging\android\setup-android.ps1
```

脚本做的事：

1. JDK 21：默认下载 Temurin 官方 zip 解压到 `tools\jdk`（免管理员）。加 `-UseWinget` 会优先试 `winget install EclipseAdoptium.Temurin.21.JDK`，失败自动回退 zip。
2. Android cmdline-tools：下载官方 zip 到 `tools\android-sdk\cmdline-tools\latest`。
3. 用 `sdkmanager` 安装 `platform-tools`、`platforms;android-36`、`build-tools;36.0.0`，并自动接受 licenses（写 `tools\android-sdk\licenses`）。
4. 设置 `JAVA_HOME` / `ANDROID_HOME` / `ANDROID_SDK_ROOT`（当前进程 + 用户级环境变量；`-NoPersistEnv` 可只设当前进程）。
5. 若 `app\android` 已存在，写入 `local.properties`（`sdk.dir=...`）。

可重复执行：已存在的 JDK/SDK 会跳过下载；`-Force` 可强制重装 JDK。

## 4. 构建 APK

```powershell
# 前置：app\www\index.html 已就绪（由 app-dev / npm run sync 生成）
powershell -ExecutionPolicy Bypass -File packaging\android\build-android.ps1
```

脚本流程：

1. 校验 `tools\jdk`、`tools\android-sdk\build-tools\36.0.0`。
2. 若 `app\android` 不存在 → `npx cap add android`；然后 `npx cap sync android`（把 `app/www` 拷进 `assets/public`）。
3. **应用品牌图标（幂等）**：`assets\android-icons\` → `app\android\app\src\main\res\`：
   - `legacy\<density>\ic_launcher.png`、`ic_launcher_round.png` → `mipmap-<density>\`（mdpi/hdpi/xhdpi/xxhdpi/xxxhdpi）
   - `foreground\<density>\ic_launcher_foreground.png` → `mipmap-<density>\`（adaptive icon 前景层）
   - `values\ic_launcher_background.xml`（`#1C1A18`）→ `res\values\`
   - 源目录不存在时自动跳过；同源 assets 直接覆盖，可反复执行。
4. 应用分享补丁：复制 `patches\MainActivity.java`、向 `AndroidManifest.xml` 注入 SEND filter、应用名改 `链记`、`allowBackup=false`（幂等，已有则跳过）。
5. `cd app\android && .\gradlew.bat assembleDebug`。
6. 复制 `app\android\app\build\outputs\apk\debug\app-debug.apk` → `release\LinkNote-debug.apk`。

### 手动构建（等价）

> 手动构建前记得先执行第 3 步的图标复制，或直接使用 `build-android.ps1`（推荐）。

```powershell
$root = 'C:\yeqkalx\code\bilinote-to-android'
$env:JAVA_HOME    = "$root\tools\jdk"
$env:ANDROID_HOME = "$root\tools\android-sdk"
$env:ANDROID_SDK_ROOT = $env:ANDROID_HOME
$env:PATH = "$env:JAVA_HOME\bin;$env:ANDROID_HOME\platform-tools;$env:PATH"

cd $root\app
npx cap sync android          # 必须：app/www 有 index.html
cd android
.\gradlew.bat assembleDebug
```

产物：`app\android\app\build\outputs\apk\debug\app-debug.apk`
安装：`adb install -r release\LinkNote-debug.apk`

### 一键构建开关

- `-Clean`：先 `gradlew clean` 再编译（从零验证，约 5s + 依赖下载）。
- `-SkipSync`：跳过 `cap sync`（确认 web assets 未变时用）。
- `-SkipPatch`：不重打 MainActivity/Manifest/应用名补丁（图标替换仍执行）。

### 构建记录（最终定稿 APK，含品牌图标）

| 项 | 值 |
| --- | --- |
| 交付路径 | `C:\yeqkalx\code\bilinote-to-android\release\LinkNote-debug.apk` |
| 构建时间 | 2026-10-24 05:03:28（Asia/Shanghai, UTC+08:00） |
| www 冻结时间 | 2026-10-24 04:55:37（app/www 最终修改；此后未再改动） |
| 大小 | 4,552,937 字节（4.34 MiB / 约 4.55 MB） |
| SHA256 | `E1C960162DBCFB870E24BB2B33E3C62BB2EF5B514B41C3C5D5CA8F84B59546CA` |
| 构建命令 | `powershell -ExecutionPolicy Bypass -File packaging\android\build-android.ps1`（内部：cap sync → 品牌图标 → 打补丁 → gradlew assembleDebug → 复制 release） |
| 图标 | `res/mipmap-{mdpi,hdpi,xhdpi,xxhdpi,xxxhdpi}-v4/` 下 `ic_launcher.png`、`ic_launcher_round.png`、`ic_launcher_foreground.png` 共 15 个 PNG 与 `assets\android-icons\` 源文件 **SHA256 逐字节一致**；adaptive icon 背景色 `resources.arsc` 中 `color/ic_launcher_background = #ff1c1a18` ✓（原先为 Capacitor 默认蓝色图标）。 |
| 图标幂等性 | 紧接着重跑一次 `build-android.ps1`：图标复制内容逐字节相同 → Gradle `assembleDebug` 判定 `UP-TO-DATE`，APK 字节未变（同一 SHA256 `E1C960…46CA`），证明图标步骤可安全重复执行。 |
| 校验 | aapt2：package=`xyz.xykong.linknote`、versionCode=1、versionName=1.0、minSdk=24、targetSdk=36、compileSdk=36、label=`链记`、launchable-activity=`xyz.xykong.linknote.MainActivity`；INTERNET 权限。Manifest：`ACTION_SEND`+`text/plain` intent-filter ✓、`android:allowBackup="false"` ✓、launchMode=singleTask ✓。assets/public：30/30 与 `app/www` 逐字节一致（另含 Capacitor 生成的 0 字节 `cordova.js`/`cordova_plugins.js`），含 index.html/css/js/vendor/icons/manifest/sw.js/share.html。apksigner：v2 签名通过（Android Debug 证书）。apkanalyzer summary：`xyz.xykong.linknote 1 1.0`。 |
| 校验原始输出 | `packaging\android\verification\`（aapt2-badging.txt、aapt2-manifest-xmltree.txt、AndroidManifest.printed.xml、apkanalyzer-summary.txt、apksigner-verify.txt、apk-filelist.txt、apk-filelist-with-sizes.txt、icon-verify.txt、SHA256SUMS.txt、manifest-patch-selftest.xml） |
| 未验证 | 真机/模拟器安装、launcher 图标实际显示、分享端到端（本机 `adb devices` 为空、无模拟器）；release 签名（仅 debug 证书） |

> 注：debug APK 的 zip 时间戳/签名不同，同样的源码两次构建 SHA256 也会变化，故以上记录对应交付的那一次构建。

## 5. 分享接收（Android → WebView）

**Manifest**：`MainActivity` 增加

```xml
<intent-filter>
    <action android:name="android.intent.action.SEND" />
    <category android:name="android.intent.category.DEFAULT" />
    <data android:mimeType="text/plain" />
</intent-filter>
```

**MainActivity 行为**（`patches/MainActivity.java`）：

- `onCreate` / `onNewIntent` 读取 `ACTION_SEND` 的 `EXTRA_TEXT`；`EXTRA_TEXT` 为空时，若 `EXTRA_SUBJECT` 是链接（`http(s)://` / `www.`）则用 `EXTRA_SUBJECT`。
- 页面加载完成（`onPageFinished`）后注入：
  - `window.__LINKNOTE_SHARED_TEXT__ = "<text>"`
  - `window.dispatchEvent(new CustomEvent('linknote:share', { detail: { text: "<text>" } }))`
- 提供拉取 API（不依赖注入时机，随时可读）：

```js
const text = await window.LinkNoteNative.getSharedText(); // string | null
window.LinkNoteNative.clearSharedText?.();               // 可选：读完清空
```

- 冷启动（从分享面板启动 App）与热启动（App 在后台时分享，走 `onNewIntent`）都支持；热启动且页面已加载时立即注入。
- 去重：同一份分享文本只 push 一次（`lastInjectedText`）；WebView 重载 / 页面重建 / 加载失败重试都不会重复 dispatch 同一条 `linknote:share`。前端 `js/bridge.js` 另有文本级去重 + 20 秒轮询 + 回前台补读兜底。
- 落地提示：前端在启动时「先读 `getSharedText()`，再监听 `linknote:share` 事件」最稳；只看 `window.__LINKNOTE_SHARED_TEXT__` 可能因注入发生在脚本执行之后而读到 `undefined`（页面加载完成回调时机）。PWA 侧原有的 Web Share Target（`share.html?text=...` → `#/home?draft=...`）不受影响，两条通道可并存。

## 6. 校验命令

```powershell
$bt = "$root\tools\android-sdk\build-tools\36.0.0"
& "$bt\aapt2.exe" dump badging release\LinkNote-debug.apk | Select-String 'package:|application-label|launchable-activity'
& "$bt\aapt2.exe" dump xmltree release\LinkNote-debug.apk --file AndroidManifest.xml | Select-String 'SEND|text/plain'

# apkanalyzer（cmdline-tools）
& "$root\tools\android-sdk\cmdline-tools\latest\bin\apkanalyzer.bat" manifest print release\LinkNote-debug.apk
& "$root\tools\android-sdk\cmdline-tools\latest\bin\apkanalyzer.bat" apk summary release\LinkNote-debug.apk

# 签名校验（debug 证书，v2 scheme）
& "$root\tools\android-sdk\build-tools\36.0.0\apksigner.bat" verify --verbose --print-certs release\LinkNote-debug.apk
```

定稿构建的校验原始输出存在 `packaging\android\verification\`：
`aapt2-badging.txt`、`aapt2-manifest-xmltree.txt`、`AndroidManifest.printed.xml`、`apkanalyzer-summary.txt`、`apksigner-verify.txt`、`apk-filelist.txt`、`SHA256SUMS.txt`。

## 7. 已知坑 / 注意事项

1. **`capacitor.config.ts` 需要 TypeScript**：本工程用 `app/capacitor.config.json`。若有人再次放回 `.ts` 且未装 typescript，所有 `npx cap ...` 命令（包括 `--help`）都会报 "Could not find installation of TypeScript"。
2. **`cap add android` / `cap sync` 要求 `app/www/index.html` 存在**：缺失时 `cap add` 会先建一半工程再失败。此时补上 index.html 后重跑 `npx cap sync android` 即可修复（不要重复 add）。
3. **`sdkmanager --licenses` 在 PowerShell 管道下喂 stdin 不生效**：必须用 `cmd /c "sdkmanager.bat --licenses < yes.txt"`。setup 脚本已这样处理。
4. **Gradle 下载偶发 TLS 握手失败**（`Remote host terminated the handshake`，对 dl.google.com/maven2）：属瞬时网络问题，直接重跑 `gradlew` 即可（已缓存产物不会重下）。工程已在 `gradle.properties` 加 `-Djava.net.preferIPv4Stack=true` 与 http 超时。若长期不稳，可在 `app/android/build.gradle` 的 `buildscript.repositories` / `allprojects.repositories` 的 `google()` 前加阿里云镜像 `maven { url 'https://maven.aliyun.com/repository/google' }`。
5. **winget 装 JDK 需要 UAC 提权**：非交互会话会卡住/失败，故默认用 portable zip；有管理员权限时可 `-UseWinget`。
6. **`app/android/` 的版本控制**：根 `.gitignore` 现在只忽略 `app/android/` 下的构建产物（`build/`、`.gradle/`、`local.properties`、`assets/public/`、`capacitor-cordova-android-plugins/`），Android 源码（含 MainActivity）可提交；同时 `patches/MainActivity.java` 保留一份，`build-android.ps1` 每次构建都会重新应用，防止将来 `cap add` 重建工程时丢失分享代码。
7. **`tools/` 不入 git**：SDK 约 1.4 GB。根 `.gitignore` 已加 `tools/`。
8. **未做真机/模拟器验证**：本机没有连接设备、也没装模拟器（`adb devices` 为空）。APK 通过 aapt2/apkanalyzer/apksigner 静态校验；分享流程的运行时行为需真机确认（见第 8 节）。
9. **WebView 调试**：debug 构建 `webContentsDebuggingEnabled: true`，`chrome://inspect` 可调试 `https://localhost`。
10. **明文 HTTP 被禁**（`allowMixedContent: false` + `androidScheme: https`）：如果 relay 用 `http://` 地址，WebView 会拦截；正式环境应走 HTTPS（relay 的 CORS 已放行 `https://localhost`）。
11. **本机只有 Windows PowerShell 5.1（无 `pwsh`）**：脚本按 5.1 兼容编写（已实测），文档统一用 `powershell -ExecutionPolicy Bypass -File ...`。5.1 下注意：`param` 默认值里 `$PSScriptRoot` 可能为空；`$ErrorActionPreference='Stop'` 时原生程序的 stderr（`java -version`、`curl`、`gradlew`）会变成终止错误——脚本已用 `cmd /c ... 2>&1` 或临时降级 EAP 处理。
12. **multidex 属正常**：debug APK 里应用代码在 `classes6.dex`（共 classes.dex~classes6.dex）。用 `apkanalyzer dex packages release\LinkNote-debug.apk` 可看到 `xyz.xykong.linknote.MainActivity`。
13. **`cap sync` 后的 `assets/public` 会额外含 `cordova.js` / `cordova_plugins.js`**，这是 Capacitor 生成的，属正常；其余文件应与 `app/www` 一一对应（构建脚本不校验，可手工 `Compare-Object`）。

## 8. 未验证项（需真机）

- 从 Chrome/哔哩哔哩/微信分享链接到「链记」后，App 是否出现在分享面板、冷/热启动注入是否生效。
- `window.LinkNoteNative.getSharedText()` 在真实页面中的返回。
- 与 relay 后端的端到端连通（本任务不涉及 relay 部署）。
- release 签名（当前只有 debug 签名）。
