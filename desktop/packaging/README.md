# LinkNote Desktop Windows 打包（PyInstaller + Inno Setup）

本目录负责把 `desktop/linknote_desktop` 打成 Windows 桌面端 EXE，并生成
per-user（免管理员）安装包。所有中间产物和大文件都在 `desktop/build/` 与
`dist-desktop/`，**不要提交 git**（根 `.gitignore` 已忽略 `build/`、`tools/`；
`dist-desktop/.gitignore` 由构建脚本自动写入）。

## 1. 产物

| 产物 | 路径 | 说明 |
|---|---|---|
| 桌面端主程序 | `dist-desktop/LinkNote/LinkNote.exe` | PyInstaller onedir，windowed（无控制台） |
| 安装包 | `dist-desktop/LinkNote-Setup-0.2.0.exe` | Inno Setup 6，per-user 安装 |
| 内置引擎（可选组件） | `desktop/build/engine/` | BiliNoteBackend.exe + `_internal/`，约 300 MB / 1903 文件 |
| 引擎 staging 清单 | `desktop/build/engine-manifest.json` | 来源、文件数、SHA256 |
| 构建报告 | `desktop/build/build-report.json` | 各步骤结果、大小、SHA256、安装/卸载验证 |
| 工具链 probe | `desktop/build/probe/` | 最小 PySide6 windowed exe 的构建+启动证据 |

最终数字以 `desktop/build/build-report.json` 为准（每次构建自动更新）。
`0.2.0` 首次完整构建结果见第 8 节。

## 2. 工具链（本机已验证）

| 工具 | 版本 | 位置 |
|---|---|---|
| Python | 3.11.16 | `desktop/.venv/Scripts/python.exe`（uv 创建，venv 内无 pip，正常） |
| PySide6 | 6.12.0 | venv |
| PyInstaller | 6.22.3 | `desktop/.venv/Scripts/pyinstaller.exe` |
| Inno Setup | 6.7.3 | `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`（winget per-user 安装） |
| 引擎来源 | BiliNote 本地构建 | `C:\app\bilinote`（可用 `-Source` 指定） |

安装/定位 Inno Setup：

```powershell
powershell -ExecutionPolicy Bypass -File desktop\packaging\install-inno-setup.ps1
# 只查询：-CheckOnly；强制装到项目内 tools\innosetup：-ForceLocal
```

解析顺序（第一个命中即用，可复现）：`tools\innosetup\ISCC.exe` →
`%LOCALAPPDATA%\Programs\Inno Setup 6` → `%ProgramFiles(x86)%\Inno Setup 6` →
`%ProgramFiles%\Inno Setup 6` → 卸载注册表 `InstallLocation` → PATH。
`build-desktop.ps1` 会自动解析，缺失时自动安装（winget 优先，回退官方 installer
静默安装到 `tools/innosetup`）。

## 3. 构建步骤

```powershell
# 0) 可选：先跑工具链 probe（约 1 分钟；build-desktop 默认也会跑）
powershell -ExecutionPolicy Bypass -File desktop\packaging\probe\build-probe.ps1

# 1) 从本地 BiliNote 干净 staging 引擎（约 300 MB；不会启动引擎、不占用 8483）
powershell -ExecutionPolicy Bypass -File desktop\packaging\stage-engine.ps1
#    可用 -Source D:\path\to\bilinote 指定来源；-Force 覆盖已有 staging

# 2) 主构建：PyInstaller EXE + Inno Setup 安装包 + 启动/安装/卸载验证
powershell -ExecutionPolicy Bypass -File desktop\packaging\build-desktop.ps1

# 2b) 单独复跑安装包验收（不重建，干净 profile；输出 desktop\build\installer-verify.json）
powershell -ExecutionPolicy Bypass -File desktop\packaging\verify-installer.ps1

# 变体
#   只打 EXE：            ...\build-desktop.ps1 -SkipInstaller
#   不打内置引擎：        ...\build-desktop.ps1 -SkipEngine   # 仅开发/应急，发布形态默认内置
#   复用已有 EXE 只打安装包：...\build-desktop.ps1 -SkipApp
#   跳过 probe/验证（快）： ...\build-desktop.ps1 -SkipProbe -SkipVerify
#   换版本号：            ...\build-desktop.ps1 -Version 0.2.1
```

`build-desktop.ps1` 的步骤：工具链检查 → probe → PyInstaller（`LinkNote.spec`）→
EXE 校验（PE subsystem=GUI、图标、启动、窗口、`--agent-worker` 冻结模式）→ 引擎
staging 检查 → ISCC 编译 → 静默安装/启动/卸载验证 → 写 `build-report.json`。

直接等价于 PyInstaller 的命令（`LinkNote.spec` 是唯一打包配置）：

```powershell
desktop\.venv\Scripts\pyinstaller.exe --noconfirm --clean `
    --distpath dist-desktop --workpath desktop\build\app\work `
    desktop\packaging\LinkNote.spec
```

## 4. 打包配置要点（`LinkNote.spec`）

- **onedir + windowed**：`console=False`，`dist-desktop/LinkNote/LinkNote.exe` +
  `_internal/`；启动快、无控制台窗口。
- **入口包装**：PyInstaller 入口是 `desktop/packaging/linknote_launcher.py`（不是
  `linknote_desktop/__main__.py`）。PyInstaller 会把入口脚本当顶层 `__main__` 执行，
  直接用它会导致 `from .application import run_app` 这种相对导入报
  “attempted relative import with no known parent package”；launcher 以包模块方式
  `from linknote_desktop.__main__ import main` 后再调用，`python -m linknote_desktop`
  行为不变。
- **入口**：`desktop/linknote_desktop/__main__.py`。正常启动 GUI；带
  `--agent-worker` 时冻结 exe 直接转交 `linknote_agent.cli:main`（Agent 子进程
  复用同一个 LinkNote.exe，无需额外 python）。
- **资源**：`linknote_desktop/resources/*` 打到 `_internal/linknote_desktop/resources/`，
  与 `config.resource_dir()`（`sys._MEIPASS/linknote_desktop/resources`）一致。
- **editable 安装兼容**：`desktop` 与 `pc-agent` 两个源码根显式加进
  `pathex`；`linknote_agent`（懒加载）用 `collect_submodules` 收全。
- **hiddenimports**：`qrcode`/`qrcode.image.pil`（配对二维码按需导入）、
  `PySide6.QtNetwork`（单实例 QLocalServer）、`PySide6.QtSvg`。
- **版本资源**：`version_info.txt` → 文件属性显示 LinkNote 0.2.0 / MIT。
- **图标**：`linknote_desktop/resources/icon.ico`（EXE 图标 + 窗口/托盘图标）。

## 5. 安装包行为（`desktop/installer/LinkNote.iss`）

- **per-user**：`PrivilegesRequired=lowest`，默认装到
  `%LOCALAPPDATA%\Programs\LinkNote`，**不弹 UAC**；`AppId` 固定，支持覆盖升级。
- **快捷方式**：开始菜单 `LinkNote 链记` + `卸载 LinkNote`；桌面快捷方式（默认创建）。
- **组件**（面向普通用户，开箱即用）：
  - `app`（必需）— 主程序。
  - `engine`（**必需/默认内置**，约 300 MB）— BiliNote 引擎，装到
    `{app}\engine\`，应用按 `app_dir()/engine/BiliNoteBackend.exe` 自动识别。
    用户装完只需：下载 ffmpeg → 填 LLM Key → 下载 Whisper 模型。
- **默认开机自启**（任务 `autostart`，默认勾选）：写
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，值名 `LinkNote`，
  命令 `"<install>\LinkNote.exe" --minimized`。该值与 app 内「开机自启」开关
  （`services/autostart.py`）完全一致，两端幂等，不会重复写值；用户可在安装向导
  或应用设置里取消。
- **卸载保留用户数据**：卸载器从不触碰 `%LOCALAPPDATA%\LinkNote`（`settings.json`、
  `agent.json`、配对 token、`logs/`、`notes/`、`outbox/`）。
  `[UninstallDelete]` 故意留空；内置引擎运行时写在 `{app}\engine\` 下的数据
  （config/data/向量库等）不会被卸载器记录，同样会保留。
- **升级**：`[InstallDelete]` 先清 `{app}\_internal` 再覆盖，避免旧运行时残留。
- **运行中安装/卸载**：`CloseApplications=yes`（Restart Manager）处理被占用的
  `LinkNote.exe` / `BiliNoteBackend.exe`。
- **语言**：简体中文（`ChineseSimplified.isl`，官方翻译）+ English。

## 6. 内置 BiliNote 引擎（默认包含）

**默认策略（2026-10 调整）**：安装包默认内置干净引擎，普通用户装完即可用，不需要
自己装 BiliNote。`engine` 组件为 fixed（安装向导中显示为已勾选、不可取消）。

`stage-engine.ps1` 从 `C:\app\bilinote`（或 `-Source`）只复制运行所需文件：

```
BiliNoteBackend.exe + _internal/ + static/
```

**不复制**（用户数据/安装残留）：`config/`、`data/`、`note_results/`、`logs/`、
`uploads/`、`vector_db/`、`bili_note.db`、`Uninstall BiliNote.lnk`、`app.exe`（Tauri 外壳）。
脚本会校验目标目录中不含这些项，并写 `engine-manifest.json`（含引擎 exe SHA256）。

体量与许可：

- staging ≈ **300 MB / 1903 文件**（`_internal` 占绝大部分，主要是 Qt/onnxruntime 等）；
  Inno 压缩后安装包实测 **126.9 MB**（0.2.0，见第 8 节）。
- BiliNote 为 **MIT** 许可（https://github.com/JefferyHcool/BiliNote）。随包附带
  `desktop/installer/THIRD-PARTY-NOTICES.txt`（装到 `{app}\engine\`），说明来源、
  许可、未修改分发、与 LinkNote 无隶属关系；安装目录同时放 `LICENSE`（LinkNote MIT）
  与 `NOTICE`。若不想再分发该二进制（例如 CI 无本地 BiliNote），可用
  `-SkipEngine` 构建 app-only 安装包（开发/应急用，非发布形态）。

## 7. 验证与证据

每次 `build-desktop.ps1` 自动做：

1. **probe**：最小 PySide6 windowed exe 构建 → PE subsystem=2（无控制台）→
   图标存在 → 启动 → 窗口渲染截图 → 正常退出。
   `desktop/build/probe/probe-result.json`、`probe-window.png`。
2. **EXE**：PE subsystem=2、图标可提取、启动 8 秒存活、主窗口句柄非 0、
   窗口渲染截图（PrintWindow）、桌面截图、`LinkNote.exe --agent-worker --version`
   冻结模式退出码 0。
3. **安装包**（`verify-installer.ps1`，可单独重跑）：PE 信息/图标/SHA256 → 静默
   per-user 安装（默认组件含引擎、默认任务含桌面快捷方式+开机自启）→ 校验
   `LinkNote.exe`、`_internal/`、`engine/BiliNoteBackend.exe`（SHA256 与 staging
   一致）、LICENSE/NOTICE/THIRD-PARTY-NOTICES、桌面/开始菜单快捷方式、卸载注册项、
   开机自启写入（以安装日志为准，见第 8 节说明）→ **干净隔离 `%LOCALAPPDATA%`
   profile** 启动已安装程序（不占 8483），校验 `agent.json.backend_exe` 自动指向
   `{app}\engine\BiliNoteBackend.exe` → `--minimized` 托盘模式存活 →
   `--agent-worker --version` → 静默卸载 → 程序/快捷方式/卸载项/自启值移除、
   用户数据保留。
4. 汇总写入 `desktop/build/build-report.json` 与
   `desktop/build/installer-verify.json`。

## 8. 0.2.0 构建结果（最终）

- 构建时间：2026-10-24 14:33–14:35（+08:00，UI 冻结后重建）
- 最终验收复跑：2026-10-24 14:4x，`verify-installer.ps1` → **INSTALLER VERIFY PASS**

| 产物 | 路径 | 字节 | 大小 | SHA256 |
|---|---|---|---|---|
| 桌面端 EXE | `dist-desktop/LinkNote/LinkNote.exe` | 6,252,046 | 5.96 MB | `918ED8181340BE2699BE6860C554895A0C34CBFF9E48F0970D76215D1332AC21` |
| 桌面包目录 | `dist-desktop/LinkNote/`（185 文件） | — | 132.2 MB | — |
| 安装包 | `dist-desktop/LinkNote-Setup-0.2.0.exe` | 133,085,401 | 126.9 MB | `C2C60506DE870BD2BD66319A2A1A3816269F5C4A23B2BAFCA8AAB09BFF5C07FA` |
| 内置引擎（staging） | `desktop/build/engine/`（1903 文件） | — | 300.4 MB | `BiliNoteBackend.exe` = `59689B1C6B91AFFFE57396845C200F192F1E82B541D000DC80210BF676E8C608` |

验收清单（全部 PASS，证据在 `desktop/build/build-report.json` /
`installer-verify.json` / `probe/` / 截图）：

- [x] probe：windowed PySide6 exe 构建、PE subsystem=2（无控制台）、图标、启动/渲染/退出
- [x] EXE：PE subsystem=2、图标、8 秒存活、窗口 `LinkNote 链记 - 链记 LinkNote`
- [x] `LinkNote.exe --agent-worker --version` → `linknote_agent 0.1.0`，退出码 0
- [x] 安装：`/VERYSILENT` per-user 安装到自选目录成功（默认组件 app+engine，约 11 秒）
- [x] 安装文件：`LinkNote.exe`、`_internal/`、`engine/BiliNoteBackend.exe`（SHA256 与
      staging 完全一致）、`LICENSE`、`NOTICE`、`engine/THIRD-PARTY-NOTICES.txt`
- [x] 快捷方式：桌面 `LinkNote 链记`、开始菜单 `LinkNote\LinkNote 链记` + `卸载 LinkNote`
- [x] 干净隔离 `%LOCALAPPDATA%` profile 下启动已安装程序成功，且
      `agent.json.backend_exe` = `{app}\engine\BiliNoteBackend.exe`（内置引擎自动识别，
      不再受开发机旧 settings 影响）
- [x] `--minimized` 托盘模式启动 5 秒存活
- [x] 开机自启：默认任务选中，Inno 安装日志确认写入
      `HKCU\...\Run\LinkNote = "<app>\LinkNote.exe" --minimized`；卸载日志确认删除
      （说明见下方“自启验证说明”）
- [x] 卸载：程序/`_internal`/引擎移除、快捷方式移除、卸载注册项移除、自启值移除
- [x] 卸载保留用户数据：`%LOCALAPPDATA%\LinkNote` 标记文件保留

**自启验证说明**：本测试机为 Windows 11 26200，装有 LenovoBaiying / GameViewer 等
启动项管理工具，部分安装后从实时注册表立即读取 `Run\LinkNote` 会偶发读不到
（写入由 Inno 日志确认成功、卸载日志确认删除；同一安装包在多次独立复测中既有
读到时也有读不到时）。因此验收以 Inno 安装/卸载日志为准，并在
`installer-verify.json` 同时记录 `autostart_live_value` 供排查。普通用户机器上
该 HKCU Run 写入为标准 Inno `[Registry]` 行为（与 app 内自启开关同值同命令）。

## 9. 已知问题 / 限制

- **引擎数据位置**：应用默认以引擎 exe 所在目录为工作目录，所以内置引擎的
  `config/ data/ vector_db/ bili_note.db`（含 LLM API Key）写在
  `%LOCALAPPDATA%\Programs\LinkNote\engine\` 下。卸载时这些运行时文件不在卸载器
  记录中，会保留；但升级/重装如需全新引擎状态，请手动清理该目录。
  *建议（app 侧，非打包范围）*：让 `DesktopConfig` 默认把 `engine_workdir`
  指向 `%LOCALAPPDATA%\LinkNote\engine`。
- **ffmpeg 不随包**：应用内「下载 ffmpeg」或使用系统/已有 BiliNote 的 ffmpeg；
  内置引擎也不会自带 ffmpeg（BiliNote 本身要求系统 ffmpeg 或 `FFMPEG_BIN_PATH`）。
- **安装包未代码签名**：SmartScreen 可能提示「未知发布者」，签名方法见第 10 节。
- **安装包体积**：126.9 MB（0.2.0，内置引擎为固定组件，安装后约 432 MB）。
- **首次启动**：内置引擎已随包安装；用户只需在向导/设置里下载 ffmpeg、填 LLM Key、
  下载 Whisper 模型。若要复用本机已装的 BiliNote，可在「引擎与 ffmpeg」页指定 exe。
- **构建耗时**：PyInstaller 约 1–2 分钟，含引擎的 Inno 编译约 1–3 分钟
  （`lzma2/max` + `SolidCompression`），安装/卸载验证约 1–2 分钟。
- **中文语言文件**：`desktop/installer/ChineseSimplified.isl` 为 Inno 官方翻译
  （6.5.0+，来自 jrsoftware/issrc），随仓库提交（约 21 KB）。

## 10. Release 代码签名（可选）

1. 准备代码签名证书（OV/EV，PFX 或证书存储）。
2. 签 EXE（可选，Inno 会在安装包里再签）：

   ```powershell
   & "C:\Program Files (x86)\Windows Kits\10\bin\x64\signtool.exe" sign `
       /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
       /f cert.pfx /p <password> dist-desktop\LinkNote\LinkNote.exe
   ```

3. 在 `LinkNote.iss` 的 `[Setup]` 增加：

   ```
   SignTool=signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 /f cert.pfx /p <password> $f
   SignedUninstaller=yes
   ```

   并在命令行给 ISCC 加 `/Ssigntool="..."`，或把签名命令写进 ISCC 的
   `--signtool` 参数；随后重新 `build-desktop.ps1 -SkipEngine ...` 生成已签名安装包。
4. 用 `Get-AuthenticodeSignature dist-desktop\*.exe` 验证 `Status=Valid`。

## 11. 排障

| 现象 | 处理 |
|---|---|
| `ISCC.exe not resolved` | 跑 `install-inno-setup.ps1`；或手动装 Inno Setup 6 后重试 |
| `PyInstaller failed` | 看 `desktop/build/app/pyinstaller-stderr.log`（常见：`.ui` 模块尚未实现） |
| 安装包编译报找不到 `dist-desktop/LinkNote` | 先构建 EXE（不要只跑 `-SkipApp`） |
| `a real LinkNote installation is registered` | 本机已装过 LinkNote，安装/卸载自动验证被跳过；先手动卸载 |
| 引擎组件没进安装包 | 先跑 `stage-engine.ps1`；`build-report.json.engine.staged=false` 会提示 |
| 启动验证失败但手动能开 | 检查 `desktop/build/app/verify/`、`desktop/build/installer-test/install.log` |

## 12. Git 卫生

- 不提交：`desktop/build/`（约 400 MB+）、`dist-desktop/`（安装包）、
  `tools/innosetup/`、`tools/downloads/`、`desktop/.venv/`。
- 提交：本目录脚本/spec/README、`desktop/installer/LinkNote.iss`、
  `desktop/installer/ChineseSimplified.isl`、`desktop/installer/THIRD-PARTY-NOTICES.txt`、
  `probe/probe_window.py`、`linknote_launcher.py`、`verify-installer.ps1`。
- 根 `.gitignore` 已覆盖 `build/`、`tools/`；`dist-desktop/` 由构建脚本写入
  `.gitignore`（`*` + `!.gitignore`），如仓库策略不同请让 lead 在根 `.gitignore`
  增加 `/dist-desktop/`。


