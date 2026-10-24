# LinkNote Desktop（Windows 客户端）

面向普通用户的 LinkNote 桌面端：**安装后即用**，不需要命令行、不需要手动安装 BiliNote。

- 安装包：`dist-desktop/LinkNote-Setup-0.2.0.exe`（per-user，免管理员）
- 主程序：`dist-desktop/LinkNote/LinkNote.exe`
- 内置 BiliNote 引擎：安装包默认包含（约 300 MB，BiliNote MIT）
- 系统托盘常驻、开机自启、状态绿/黄/红
- 首次启动 7 步配置向导：ffmpeg → 引擎 → AI Key → Whisper → 手机配对

![桌面端](C:/yeqkalx/code/bilinote-to-android/assets/desktop-ui-real.png)

## 1. 用户怎么用

1. 双击 `LinkNote-Setup-0.2.0.msi`（推荐；Windows Installer，免管理员）。
   - 如果机器上装了联想电脑管家/火绒，它们可能拦截 Inno 版 `LinkNote-Setup-0.2.0.exe` 的临时解压；MSI 由 Windows Installer 服务创建目录，通常可通过。
2. 首次打开按向导：
   - 自动下载 ffmpeg（约 100 MB，只需一次）
   - 启动内置 BiliNote 引擎
   - 选择 DeepSeek/OpenAI 等供应商，粘贴自己的 API Key 并测试
   - 选择 Whisper 模型并下载（默认 small，也可用 tiny/base）
   - 生成配对码/二维码，手机 LinkNote 输入或扫码
3. 之后程序常驻托盘；手机分享视频链接，电脑自动生成 Markdown 回传手机。

关闭窗口默认最小化到托盘；右键托盘可打开主界面、暂停任务、重启引擎、退出。开机自启默认开启，可在设置关闭。

## 2. 它管理什么

| 功能 | 实现 |
|---|---|
| BiliNote 引擎 | 安装包内置 clean engine；桌面端启动/停止/探测/自动复用已运行实例 |
| ffmpeg | 自动检测系统 ffmpeg；没有则从 gyan.dev 下载解压到 `%LOCALAPPDATA%\LinkNote\ffmpeg`，通过 `FFMPEG_BIN_PATH` 注入引擎 |
| LLM 供应商/Key | 通过 BiliNote `/get_all_providers`、`/update_provider`、`/connect_test` 管理；Key 存在本机引擎数据库 |
| 模型选择 | `/model_list`、`/models`；默认写入 Agent 配置 |
| Whisper | `/transcriber_config`、`/transcriber_download`、`/transcriber_models_status`；默认 HF 镜像 `hf-mirror.com`，可改 |
| 手机配对 | 桌面端创建配对码/二维码，Agent 自动轮询 Relay 直到手机领取 |
| 常驻 | PySide6 QSystemTrayIcon + 独立 Agent 子进程 + HKCU Run 自启 |
| 日志 | `%LOCALAPPDATA%\LinkNote\logs`；引擎/Agent 页可查看 |
| 数据 | `%LOCALAPPDATA%\LinkNote`（settings/agent/logs/notes/outbox），卸载保留 |

## 3. 隐私说明（准确版）

- 视频文件、音频、API Key、Whisper 转写内容**只在你的电脑**。
- 手机 Relay 只中转：视频链接、任务状态、最终 Markdown。
- **最终 Markdown 会保存在 Relay 的 D1 数据库中**，供手机读取（这是当前架构的取舍，后续可加端到端加密）。
- 桌面端不上传本机路径、日志或引擎配置。

## 4. 系统要求

- Windows 10/11 x64
- 安装包约 127 MB，安装后约 430 MB（不含 Whisper 模型）
- 首次配置另需下载：ffmpeg 约 100 MB + Whisper small 约 460 MB（可选 tiny 约 75 MB）
- 需要网络访问 `relay.xykong.xyz`、`api.deepseek.com`（或你选的 LLM）、`hf-mirror.com`/HuggingFace
- 电脑需要保持开机/不休眠，手机才能随时发任务

## 5. 开发运行

```powershell
cd desktop
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -e ..\pc-agent -e ".[dev]"

# 开发运行（需要本机 BiliNote 引擎，或先用打包引擎 staging）
.venv\Scripts\python.exe -m linknote_desktop

# 无 UI 自测
.venv\Scripts\python.exe -m linknote_desktop.ui._smoke

# 真实链路手工 E2E（独立临时 LOCALAPPDATA，不污染用户配置）
.venv\Scripts\python.exe -m tests.manual_e2e_desktop
```

## 6. 打包

```powershell
# 生成干净引擎 staging（默认 C:\app\bilinote）
powershell -ExecutionPolicy Bypass -File desktop\packaging\stage-engine.ps1

# PyInstaller + Inno Setup + 安装/卸载验证
powershell -ExecutionPolicy Bypass -File desktop\packaging\build-desktop.ps1
```

详细工具链、验证记录、签名步骤见 [`packaging/README.md`](packaging/README.md)。

## 7. 架构

```text
LinkNote.exe (PySide6)
├── MainWindow / TrayIcon        # 6 页 + 向导 + 托盘
├── DesktopController            # Qt 信号 + 后台线程池
│   ├── EngineManager            # BiliNoteBackend.exe 启停/探测
│   ├── AgentProcess             # LinkNote.exe --agent-worker 子进程（现有 linknote_agent）
│   ├── FFmpegManager            # 检测/下载/解压
│   └── RelayService             # 配对/设备状态（复用 RelayClient）
└── engine\BiliNoteBackend.exe   # 安装包内置
```

- UI 不是 WebView，是原生 Qt；视觉与手机端一致。
- Agent 复用 `pc-agent` 的成熟逻辑，在子进程运行，GUI 不阻塞。
- 引擎复用 BiliNote v2.4.4 HTTP API，不修改其源码。

## 8. 已知限制 / 发布前 TODO

- **安装包未代码签名**：Windows SmartScreen 会提示未知发布者；正式分发需要 EV/OV 代码签名证书。
- 无自动更新（后续接 GitHub Releases/自有更新服务）。
- LLM Key 仍需用户自备（BYOK）；对普通用户这是最大门槛，后续可评估托管模型/额度套餐。
- Relay 目前是单区域 Worker + D1，公开大规模分发前需限流/监控/备份。
- 内置引擎版本随安装包版本走；升级 LinkNote 时需同步升级引擎。
- 安装包内引擎来自本地 BiliNote 构建；公开分发前需确认上游版本/许可与更新策略。
- Markdown 在 Relay 明文存储；端到端加密为后续项。
