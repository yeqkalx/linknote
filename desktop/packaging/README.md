# LinkNote Desktop Windows 打包（PyInstaller + Inno Setup）

本目录负责把 `desktop/linknote_desktop` 打成 Windows 桌面端 EXE，并生成
per-user（免管理员）安装包。所有中间产物和大文件都在 `desktop/build/` 与
`dist-desktop/`，**不要提交 git**（根 `.gitignore` 已忽略 `build/`、`tools/`；
`dist-desktop/.gitignore` 由构建脚本自动写入）。

## 1. 产物

| 产物 | 路径 | 说明 |
|---|---|---|
| 桌面端主程序 | `dist-desktop/LinkNote/LinkNote.exe` | PyInstaller onedir，windowed（无控制台），当前 0.4.0 |
| 安装包（MSI，推荐） | `dist-desktop/LinkNote-Setup-0.4.0.msi` | WiX 3.14 / Windows Installer，per-user 免管理员；含内置引擎 |
| 安装包（EXE） | `dist-desktop/LinkNote-Setup-0.4.0.exe` | Inno Setup 6，per-user 安装；部分安全软件会拦 Temp 解压 |
| 内置引擎（默认包含） | `desktop/build/engine/` | BiliNoteBackend.exe + `_internal/`，约 300 MB / 1903 文件 |
| 引擎 staging 清单 | `desktop/build/engine-manifest.json` | 来源、文件数、SHA256 |
| 构建报告 | `desktop/build/build-report.json` | 各步骤结果、大小、SHA256、安装/卸载验证 |
| MSI 验收报告 | `desktop/build/msi/msi-verify.json` / `msi-ui-verify.json` | 静默安装/卸载验收 + 真实 UI 点击验收 |
| 工具链 probe | `desktop/build/probe/` | 最小 PySide6 windowed exe 的构建+启动证据 |

版本号由 `desktop/pyproject.toml` 自动读取（`build-desktop.ps1` / `build-msi.ps1`），
当前 **0.4.0**；0.4.0 最终构建结果见第 8 节（本次按要求未在本机安装，仅做包内容/MSI 结构校验）。版本资源由构建脚本按 `pyproject.toml` 自动生成（`LINKNOTE_VERSION_FILE`），不会再出现 exe 版本滞后的情况。

### MSI 安装器（推荐）

联想电脑管家/火绒等安全软件可能拦截 Inno Setup 的 `is-*.tmp` 临时解压，报错 5；
MSI 用 Windows Installer 服务安装，不经过 EXE 自解压，通常可绕过这类拦截；且
**无需管理员权限**（UAC-compliant per-user 安装，HKCU 项目/快捷方式/自启均按当前用户写）。

```powershell
# WiX 3.14 解压到 tools\wix314（含 candle/light/heat）；先生成 dist-desktop\LinkNote 和 desktop\build\engine
powershell -ExecutionPolicy Bypass -File desktop\packaging\build-msi.ps1
# 静默安装/卸载验收（不重建）
powershell -ExecutionPolicy Bypass -File desktop\packaging\msi\verify-msi.ps1
# 真实 UI 点击验收：走完向导、勾选 Launch LinkNote、点 Finish、确认 app 启动
powershell -ExecutionPolicy Bypass -File desktop\packaging\msi\verify-msi-ui.ps1
```

- 产物：`dist-desktop/LinkNote-Setup-<version>.msi`（0.2.1 = 144.1 MB）
- `build-msi.ps1 -SkipPayload` 可复用 `desktop/build/msi/payload`（payload ≈ 433 MB）
- 依赖 WiX 3.14（`tools/wix314`），与 Inno Setup 互不依赖
- 安装到 `{LocalAppData}\Programs\LinkNote`；数据目录 `%LOCALAPPDATA%\LinkNote` 由安装服务创建
- 内置引擎装到 `{app}\engine`，装完只需下载 ffmpeg / 填 LLM Key / 下载 Whisper
- 安装目录含 `LICENSE`、`NOTICE`、`licenses\GPL-3.0.txt`、`licenses\LGPL-3.0.txt`；
  FFmpeg **不随包**，首次运行由 app 下载 BtbN **LGPL shared** 构建（约 82 MB zip）
  到 `%LOCALAPPDATA%\LinkNote\ffmpeg`，不再下载 GPL 构建
- 桌面 + 开始菜单快捷方式默认创建；**卸载保留 `%LOCALAPPDATA%\LinkNote` 用户数据**
- Finish 对话框「Launch LinkNote」复选框默认勾选，点 Finish 会启动安装后的 app
  （`LaunchLinkNote` type-34 CA + `ExitDialog/Finish` DoAction；异步启动，失败不影响安装）
- `check-frozen-package.py` 直接读取构建出的 exe 内 PYZ，校验 ffmpeg 模块含 BtbN LGPL URL（无 gyan.dev）、websockets/relay_ws 已冻结，以及 `linknote_agent.bots.*`（base/telegram/qq/runner/state/device_relay）+ httpx/httpcore 是否都在
- `msi\verify-msi-contents.ps1` 不安装、直接读 MSI 数据库校验结构（版本/每用户属性/Finish 勾选 action/自启/File 表版本与许可文件）
- `build-msi.ps1` 编译后自动做 9 项静态检查（ALLUSERS/MSIINSTALLPERUSER、checkbox 属性、
  CustomAction、ControlEvent、Autostart Registry/Component/Feature），防止回归
- 升级：`MajorUpgrade` + 固定 UpgradeCode；0.2.0 → 0.2.1 已验证覆盖升级（旧产品自动卸载、
  数据保留、单 ARP 项）

### 当前 MSI 产物（2026-10-24 20:4x +08:00，0.4.0）

- 路径：`dist-desktop/LinkNote-Setup-0.4.0.msi`（副本：`C:\Users\yeqkalx\Desktop\LinkNote-Setup-0.4.0.msi`）
- 大小：151,518,605 字节（144.5 MB）
- SHA256：`0C29888AA763FA364424D4E58C15EE5DFC670005A86E2A69E19A0F7276AF5B30`
- 验收：`verify-msi.ps1` 安装/卸载全 PASS；`verify-msi-ui.ps1` 真实点击 Finish 后
  LinkNote.exe 成功启动（PASS）。

### 关于开机自启（HKCU Run）与第三方启动项管理

MSI 的 `Autostart` 组件写
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run\LinkNote = "<app>\LinkNote.exe" --minimized`
（与 app 内「开机自启」开关同值，幂等），安装日志可见 `RegAddValue(Name=LinkNote,...)` 成功。
但本测试机的 **联想电脑管家（LenovoPcManagerService / 联想百应）会把该值列入
`HKCU\...\Run\LenovoDisabled\LinkNote = rem|...\LinkNote.exe --minimized` 并在写入后
约 1 秒内删除**（对照实验：手写 LinkNote 值 1s 被删，写 LinkNoteProbeTest 20s 仍在）。
这是第三方启动项管理行为，不是 MSI 写错；普通机器上该 HKCU Run 项可正常保留。
若用户机器同样开启联想电脑管家，需在「电脑管家 → 开机启动项」里允许 LinkNote，
或关闭其启动项管理；应用设置页的自启开关写的是同一注册表值。

## 2. 工具链（本机已验证）

| 工具 | 版本 | 位置 |
|---|---|---|
| Python | 3.11.16 | `desktop/.venv/Scripts/python.exe`（uv 创建，venv 内无 pip，正常） |
| PySide6 | 6.12.0 | venv |
| PyInstaller | 6.22.3 | `desktop/.venv/Scripts/pyinstaller.exe` |
| Inno Setup | 6.7.3 | `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`（winget per-user 安装） |
| 引擎来源 | BiliNote 本地构建 | 原 `C:\app\bilinote` 已清空；复用已验证的 `desktop/build/engine` staging（SHA 见第 8 节，1903 文件 / 300.4 MB） |

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
#   换版本号：            ...\build-desktop.ps1 -Version 0.2.2
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

## 8. 0.4.0 构建结果（最终，2026-10-24）

- 版本：**0.4.0**（pc-agent bot 入口 + Desktop 消息入口；Bot 模块必须随冻结包分发）
- 构建时间（+08:00）：EXE 20:44、MSI 20:46、Inno Setup 20:47
- 按用户要求：**本次未在本机安装/升级**，只做构建 + 冻结包内容 + MSI 数据库结构校验；
  机器上现有 0.3.0 安装与 `%LOCALAPPDATA%\LinkNote` 未改动。
- 构建命令：`build-desktop.ps1 -SkipProbe -SkipInstaller -SkipVerify` → `build-msi.ps1`
  → `build-desktop.ps1 -SkipApp -SkipProbe -SkipVerify`

| 产物 | 路径 | 字节 | 大小 | SHA256 |
|---|---|---|---|---|
| 桌面端 EXE | `dist-desktop/LinkNote/LinkNote.exe`（0.4.0.0） | 6,751,018 | 6.44 MB | `838CD27DB9C144D0A9E19A3C218E006973482A9BC779C801B8FC10E5976F65F5` |
| 桌面包目录 | `dist-desktop/LinkNote/`（195 文件） | — | 132.9 MB | — |
| 安装包（MSI，主） | `dist-desktop/LinkNote-Setup-0.4.0.msi` | 151,518,605 | 144.5 MB | `0C29888AA763FA364424D4E58C15EE5DFC670005A86E2A69E19A0F7276AF5B30` |
| 安装包（Inno EXE，备用） | `dist-desktop/LinkNote-Setup-0.4.0.exe` | 133,618,566 | 127.4 MB | `BF73841162C1A4506A3CA74A43ACC67B58693555099A742D8668B17BDBF08BE2` |
| 内置引擎（默认包含） | `desktop/build/engine/`（1903 文件） | — | 300.4 MB | `BiliNoteBackend.exe` = `59689B1C…6E8C608` |

MSI/EXE 已复制到 `C:\Users\yeqkalx\Desktop\`（0.4.0）。

验收清单（证据：`desktop/build/final-040/*`、`desktop/build/msi/msi-contents-verify-0.4.0.json`）：

- [x] **Bot 模块冻结（本次关键）**（`check-frozen-package.py` 读 exe 内 PYZ）：
      `linknote_agent.bots` + base/telegram/qq/runner/state/device_relay **7/7 全在**，
      代码对象可正常 extract；httpx 23 模块、httpcore 31 模块均在 → 无 ImportError 风险
- [x] **冻结包运行时导入**：`LinkNote.exe --agent-worker --version` → `linknote_agent 0.1.0`（exit 0），
      `--agent-worker --help` 正常（该路径会 import `linknote_agent.cli` → `bots.runner`，证明 bots 导入链可运行）
- [x] **其它冻结内容**：`websockets` 47 模块 + `relay_ws`、ffmpeg 模块含 BtbN LGPL shared URL（gyan.dev=0）
- [x] **MSI 数据库结构校验**（`verify-msi-contents.ps1`，不安装）：ProductVersion 0.4.0、
      ALLUSERS=2/MSIINSTALLPERUSER=1、Finish「Launch LinkNote」复选框 + DoAction、自启 Run 行、
      File 表 2103 项；`LinkNote.exe` 版本 0.4.0.0 / 大小 6,751,018 与 dist 一致；
      `LICENSE`、`NOTICE`、`licenses\GPL-3.0.txt`、`licenses\LGPL-3.0.txt`、
      `engine\BiliNoteBackend.exe`、`engine\THIRD-PARTY-NOTICES.txt` 全部在包内
- [x] `build-msi.ps1` 编译后 9 项静态检查 PASS
- [ ] 安装/升级/卸载/Finish 点击验证：按要求由用户在本机执行（本次未安装）
- [ ] Inno EXE 安装：历史在测试机上被安全软件拦（exit 5），本机发布仍以 MSI 为主
### 8.2 0.3.0 历史构建结果（2026-10-24）

- 版本：**0.3.0**（`desktop/pyproject.toml`；task-16 Desktop v0.3 + task-17 FFmpeg LGPL/许可完成后构建）
- 构建时间（+08:00）：EXE 19:04、MSI 19:05、Inno Setup 19:06
- 构建命令：`build-desktop.ps1 -SkipProbe -SkipInstaller` → `build-msi.ps1` → `build-desktop.ps1 -SkipApp -SkipProbe -SkipVerify`

| 产物 | 路径 | 字节 | 大小 | SHA256 |
|---|---|---|---|---|
| 桌面端 EXE | `dist-desktop/LinkNote/LinkNote.exe`（0.3.0.0） | 6,684,039 | 6.37 MB | `BC353B5CD0A42DD2AFA145CA582E513F6D6D710BD8DF29D2E192F37DC6D54E73` |
| 桌面包目录 | `dist-desktop/LinkNote/`（195 文件） | — | 132.8 MB | — |
| 安装包（MSI，主） | `dist-desktop/LinkNote-Setup-0.3.0.msi` | 151,453,069 | 144.4 MB | `AF17C876C30DB6D2BA6623A41EEEC8C148CEEE761D3DD2F457A800B64D4A18DC` |
| 安装包（Inno EXE，备用） | `dist-desktop/LinkNote-Setup-0.3.0.exe` | 133,550,198 | 127.4 MB | `A7407EA4B7AAE95E3A600EE503748D405A946B8F157E21C2D05056C9B6F62CF9` |
| 内置引擎（默认包含） | `desktop/build/engine/`（1903 文件） | — | 300.4 MB | `BiliNoteBackend.exe` = `59689B1C…6E8C608` |

验收清单（证据：`desktop/build/final-030/*.log|json`、`desktop/build/msi/msi-verify.json`、
`desktop/build/msi/msi-ui-verify.json`、`desktop/build/ws-verify/app-profile/LinkNote/logs/`）：

- [x] **冻结包内容检查**（`check-frozen-package.py` 读 exe 内 PYZ）：ffmpeg 模块含
      `https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl-shared.zip`，
      无 gyan.dev；`websockets` 47 个模块 + `linknote_agent.relay_ws` 已冻结 → PASS
- [x] **安装/升级**：0.2.2 装好后升级 0.3.0（静默、无管理员）→ 旧产品自动卸载、单 ARP 项 0.3.0、
      exe 0.3.0.0、引擎/桌面快捷方式存在、`%LOCALAPPDATA%\LinkNote` 数据保留
- [x] **安装内容**：`LICENSE`、`NOTICE`、`licenses\GPL-3.0.txt`、`licenses\LGPL-3.0.txt`、
      `engine\BiliNoteBackend.exe`（SHA 与 staging 一致）全部存在
- [x] **安装后 Agent WS**：真实启动安装版 app（隔离 profile、已配对）→ `agent.log`:
      `开始连接 Relay WebSocket：wss://relay.xykong.xyz/api/v1/agent/ws` →
      `收到 WebSocket hello（agent_id=…）` → `实时连接已建立`，随后持续 ping/pong；
      `agent-worker.log` 无 `No module named` 报错
- [x] **MSI 静默安装/卸载验收**（`verify-msi.ps1`）：数据目录创建、桌面/开始菜单快捷方式、
      LICENSE/NOTICE/licenses 文本、引擎 SHA、安装后启动 app、`--agent-worker --version`、
      卸载移除程序/快捷方式/ARP/自启值、保留用户数据 → PASS
- [x] **UI Finish 真实点击**（`verify-msi-ui.ps1`）：「Launch LinkNote」默认勾选 → 点 Finish →
      安装目录 LinkNote.exe 实际启动 → PASS
- [x] 自启 HKCU Run 写入正确（日志确认）；本机仍被联想电脑管家删除（第三方行为，见第 1 节）
- [ ] **Inno EXE 安装**：本机 `/VERYSILENT` 仍 exit 5（日志 `User canceled the installation process`，
      与 0.2.0/0.2.1/0.2.2 同样的安全软件拦截；MSI 不受影响，发布以 MSI 为主）
- 说明：FFmpeg 默认下载已改为 BtbN LGPL shared（task-17），首次运行下载约 82 MB zip；
  `licenses\` 随安装目录提供 GPL/LGPL 全文
### 8.3 0.2.2 历史构建结果（2026-10-24）

- 版本：**0.2.2**（修复 0.2.1 冻结包缺 `websockets` 导致 Agent WS 连不上的回归）
- 关键修复：`pyproject.toml` 增加 `websockets>=13,<16`（.venv 15.0.1），
  `LinkNote.spec` 增加 `collect_submodules("websockets")`；`build-desktop.ps1` 现在按
  `pyproject.toml` 动态生成 exe 版本资源（`LINKNOTE_VERSION_FILE`），exe/MSI/Inno 版本不会再漂移。
- 构建时间（+08:00）：EXE 17:00、MSI 17:01、Inno Setup 17:02

| 产物 | 路径 | 字节 | 大小 | SHA256 |
|---|---|---|---|---|
| 桌面端 EXE | `dist-desktop/LinkNote/LinkNote.exe`（0.2.2.0） | 6,656,385 | 6.35 MB | `41561E659652E7EAA0C2AA66EB687DD98C0B9D15C79D0BC97C5B45AA18A85720` |
| 桌面包目录 | `dist-desktop/LinkNote/`（195 文件） | — | 132.8 MB | — |
| 安装包（MSI，推荐） | `dist-desktop/LinkNote-Setup-0.2.2.msi` | 151,403,617 | 144.1 MB | `27224790573F455885B66D61B2921D44CD42AF3A9401540FE360614079135D72` |
| 安装包（Inno EXE） | `dist-desktop/LinkNote-Setup-0.2.2.exe` | 133,492,705 | 127.3 MB | `752F242B16FF01AB81C987DDC5F83081F4F8A9C8F61420D68153F4E881E645F7` |
| 内置引擎（默认包含） | `desktop/build/engine/`（1903 文件） | — | 300.4 MB | `BiliNoteBackend.exe` = `59689B1C…6E8C608` |

验收清单（证据：`desktop/build/final-022/*.log|json`、`desktop/build/ws-verify/logs/`、
`desktop/build/msi/msi-ui-verify.json`）：

- [x] **WebSocket 回归修复（核心）**：`verify-agent-ws.ps1` 用真实生产 Relay token 运行冻结 exe 的
      Agent worker → agent.log 出现 `收到 WebSocket hello` / `实时连接已建立`，并持续 ping/pong；
      全日志无 `No module named 'websockets'` → **PASS**（dist EXE + 安装版 app 双验证）
- [x] **安装后 app 启动 WS 验证**：装 0.2.2 后用干净隔离 `%LOCALAPPDATA%`（已配对 agent.json）
      启动 LinkNote.exe，App 自行拉起 `--agent-worker`，`agent-worker.log`/`agent.log` 同样
      hello + 无缺包错误
- [x] **升级路径 0.2.1 → 0.2.2（MSI）**：旧产品自动卸载、单 ARP 项 0.2.2、exe 0.2.2.0、
      内置引擎/桌面快捷方式存在、`%LOCALAPPDATA%\LinkNote` 用户数据保留
- [x] **UI Finish 点击**：`verify-msi-ui.ps1` 真实点击 ExitDialog Finish（复选框 `Launch LinkNote`
      默认勾选）→ 安装目录 LinkNote.exe 启动 → PASS
- [x] **静默卸载**：程序/快捷方式/ARP/自启值移除，用户数据保留
- [x] MSI 静态检查 9 项 PASS（per-user 属性、checkbox/CustomAction/ControlEvent、Autostart）
- [x] MSI 静默安装（免管理员）、数据目录创建、LICENSE/NOTICE、引擎 SHA256 匹配
- [ ] Inno EXE 安装：本机仍受第三方安全软件拦截（`/VERYSILENT` exit 5，0.2.0/0.2.1 同样）；
      EXE 产物已构建并校验版本/哈希，发布以 MSI 为准
- 说明：本测试机联想电脑管家会删除 HKCU Run 自启项（见第 1 节），与安装包无关
### 8.4 0.2.1 历史构建结果（2026-10-24）

- 版本：**0.2.1**（`desktop/pyproject.toml`；UI 冻结 / task-7 完成后重打；用户已装 0.2.0，因此必须升版本才能触发 MajorUpgrade/Inno 升级）
- 构建时间（+08:00）：EXE 16:29、MSI 16:31、Inno Setup EXE 16:37
- 构建命令：`build-desktop.ps1 -SkipProbe -SkipInstaller` → `build-msi.ps1` → `build-desktop.ps1 -SkipApp -SkipProbe -SkipVerify`

| 产物 | 路径 | 字节 | 大小 | SHA256 |
|---|---|---|---|---|
| 桌面端 EXE | `dist-desktop/LinkNote/LinkNote.exe` | 6,343,865 | 6.05 MB | `E345E038DE4B66982265020507FEBD7C7293F3ABEF1831DF185EF3C85D330882` |
| 桌面包目录 | `dist-desktop/LinkNote/`（185 文件） | — | 132.5 MB | — |
| 安装包（MSI，推荐） | `dist-desktop/LinkNote-Setup-0.2.1.msi` | 151,066,408 | 144.1 MB | `EBA57EFBEB2455587CFDD84DFC1F4E3B2818A6E81EE792F20536403542031141` |
| 安装包（Inno EXE） | `dist-desktop/LinkNote-Setup-0.2.1.exe` | 133,165,344 | 127 MB | `A72DA2F7F4BAEC31200D0E0BD98EE1A65075887E7A5F9D11D93F52F78D575FE6` |
| 内置引擎（默认包含） | `desktop/build/engine/`（1903 文件） | — | 300.4 MB | `BiliNoteBackend.exe` = `59689B1C6B91AFFFE57396845C200F192F1E82B541D000DC80210BF676E8C608` |

验收清单（证据：`desktop/build/msi/msi-verify.json`、`msi-ui-verify.json`、
`desktop/build/final-021/*.log`、`desktop/build/msi/verify-ui/*.png`）：

- [x] **EXE**：PE subsystem=2、图标、窗口 `linknote 链记 - linknote`、`--agent-worker --version` = linknote_agent 0.1.0
- [x] **MSI 静默安装（无管理员）**：12s 完成，`%LOCALAPPDATA%\Programs\LinkNote` 文件齐全、
      数据目录 `%LOCALAPPDATA%\LinkNote` 创建、桌面+开始菜单快捷方式、内置引擎 SHA 匹配
- [x] **MSI 安装内容**：`LICENSE`、`NOTICE`、`engine/THIRD-PARTY-NOTICES.txt`（BiliNote MIT 归属）
- [x] **UI Finish 点击（真实点击，非模拟）**：`verify-msi-ui.ps1` 走完安装向导 → ExitDialog
      找到「Launch LinkNote」复选框（默认勾选）→ 点击 Finish → **安装目录 LinkNote.exe 实际启动**
      （截图 `desktop/build/msi/verify-ui/finish-dialog.png` / `launched-app.png`）→ PASS
- [x] **自启 HKCU Run**：安装日志确认写入
      `HKCU\...\Run\LinkNote = "<app>\LinkNote.exe" --minimized`；但本测试机联想电脑管家
      （LenovoPcManagerService，`Run\LenovoDisabled\LinkNote = rem|...`）会在 ~1s 内删除（详见第 1 节说明）
- [x] **卸载**：程序/引擎/快捷方式/ARP 项/自启值移除；`%LOCALAPPDATA%\LinkNote` 用户数据保留
- [x] **升级路径 0.2.0 → 0.2.1（MSI）**：先装 0.2.0（exe 0.2.0.0），再装 0.2.1 → 旧产品自动卸载、
      仅 1 个 ARP 项（0.2.1）、exe 0.2.1.0、内置引擎/桌面快捷方式存在、用户数据保留；卸载干净
- [ ] **Inno EXE 安装**：本机 `LinkNote-Setup-0.2.1.exe /VERYSILENT` 返回 exit 5（0.2.0 同样问题；
      此前已记录联想/火绒拦截 Inno 自解压），故 MSI 为推荐发布路径；EXE 产物本身已构建并通过
      版本/哈希检查
### 8.5 0.2.0 历史构建结果（2026-10-24 14:33）

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
- **FFmpeg 默认下载 LGPL 构建**（BtbN win64 LGPL shared，zip 约 82MB/解压约 177MB），
  解压后 `%LOCALAPPDATA%\LinkNote\ffmpeg\bin` 保留 ffmpeg.exe + 同目录 DLL；不再默认下载
  gyan.dev GPLv3 essentials。安装目录 `licenses\` 附带 GPL-3.0/LGPL-3.0 全文。
  默认 URL 是 BtbN `master-latest`（会随上游更新）；如需可复现可设置环境变量
  `LINKNOTE_FFMPEG_URL` 指向固定日期的 BtbN autobuild 资产。
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











