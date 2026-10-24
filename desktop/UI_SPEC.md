# LinkNote Desktop UI 规范（给 UI 实现者）

桌面端是 **PySide6 原生窗口 + Windows 系统托盘**，不是 WebView。
UI 只放在 `desktop/linknote_desktop/ui/`；服务层、controller、配置、入口由 lead 维护。

## 1. 可直接使用的 Controller

`linknote_desktop.controller.DesktopController`（QObject）已经实现：

```python
controller.config             # DesktopConfig（可读写；改完调用 save_config）
controller.engine             # EngineManager / .api -> EngineAPI
controller.agent              # AgentProcess
controller.relay              # RelayService
controller.run_async(fn, *args, on_result=..., on_error=...)  # 后台线程执行
controller.start_engine_async(); stop_engine_async(); restart_engine_async()
controller.start_agent(); stop_agent(); restart_agent()
controller.create_pairing_async(agent_name=None); cancel_pairing()
controller.pairing_state() -> dict
controller.revoke_device(device_id)
controller.download_ffmpeg_async()
controller.save_config(); controller.set_autostart(bool); controller.autostart_enabled()
controller.agent_log_tail(n); controller.engine_log_tail(n)
controller.data_paths() -> dict
controller.last_status -> dict
```

信号：

```python
statusChanged(dict)     # 每 4 秒：engine/agent/relay/ffmpeg 状态
engineChanged(dict)
agentChanged(dict)
pairingChanged(dict)    # {} 或 {pairing_id, code, expires_at, status, relay_url, device?}
ffmpegProgress(int, int)
notification(str title, str message, str level)  # info/success/warning/error
logAppended(str)
error(str)
```

`statusChanged` 结构：

```python
{
  "engine": {"state": "stopped|starting|running|error", "message": str, "pid": int|None,
             "owned": bool, "path": str, "alive": bool},
  "agent": {"running": bool, "pid": int|None, "paired": bool, "error": str},
  "relay": {"url": str, "online": bool, "agent": dict|None,
            "queue": {"queued": int, "running": int}|None, "devices": list, "error": str},
  "ffmpeg": {"available": bool, "path": str, "bin_dir": str, "version": str},
}
```

BiliNote 引擎 API 调用示例：

```python
api = controller.engine_api()
controller.run_async(api.providers, on_result=..., on_error=...)
controller.run_async(api.update_provider, "deepseek", api_key="sk-...", on_result=...)
controller.run_async(api.connect_test, "deepseek", "deepseek-v4-flash", on_result=...)
controller.run_async(api.transcriber_config, on_result=...)
controller.run_async(api.download_whisper, "small", "fast-whisper", on_result=...)
```

> 所有网络调用都不要在 GUI 线程直接做；统一用 `controller.run_async`。
> 引擎没启动时 API 会抛 `EngineAPIError`，页面应显示“先启动引擎”而不是崩溃。

## 2. 视觉

- 尺寸：默认 1000×720，最小 900×620；左侧 220px 导航，右侧内容区。
- 配色（与手机端一致）：
  - 浅蓝背景 `#F4F7FC`
  - 卡片 `#FFFDF9`
  - 炭黑文字 `#221D19`
  - 次要文字 `#8A7C6F`
  - 品牌蓝 `#1E4DFD`
  - 成功 `#2E8B57` / 警告 `#D98E04` / 失败 `#C0392B`
- 字体：`Segoe UI`, `Microsoft YaHei`, sans-serif；标题可用 20-24px 加粗。
- 卡片圆角 12px，细边框 `#E8DCCB`，轻阴影（QGraphicsDropShadowEffect 可选）。
- 控件高度 ≥ 36px；主按钮品牌蓝底白字；次按钮白底描边。
- 全部中文文案。不要使用网络图片/外部字体。

## 3. 页面（`ui/pages/`）

### 3.1 状态页（首页）
- 顶部：LinkNote / 链记 标题 + 副标题「手机发链接，电脑做笔记」。
- 四张状态卡：
  1. **Relay**：地址、在线/离线、Agent 名称；按钮「测试连接」（调用 `controller.relay.health()`）。
  2. **Agent**：运行/停止、PID、已配对/未配对；按钮「启动/停止」。
  3. **BiliNote 引擎**：运行/停止/错误、路径、PID；按钮「启动/停止/重启」。
  4. **ffmpeg**：已安装/缺失、版本/路径；缺失时按钮「下载 ffmpeg」（`controller.download_ffmpeg_async()`），下载中显示进度条。
- 配对卡：
  - 未生成码时按钮「生成配对码」。
  - 生成后显示大号 `XXXX-XXXX`、过期时间、Relay 地址、复制按钮、二维码（`qrcode` 库生成 QPixmap）、「等待手机领取…」。
  - 已配对时显示设备列表（名称/平台/配对时间 + 撤下按钮）、「添加手机」生成新码。
- 最近事件：`logAppended` 最后 8 条，等宽小字。
- 状态卡颜色：绿点/黄点/红点。

### 3.2 AI 模型页
- 从 `/get_all_providers` 加载供应商下拉（显示 name/type），选中后：
  - API Key 输入（密码模式 + 显示/隐藏按钮）、Base URL 输入。
  - 「保存」→ `update_provider(id, api_key=..., base_url=...)`。
  - 「测试连接」→ `connect_test(id, model)`，结果用内联提示。
- 模型列表（`/model_list`）按 provider 过滤；下拉选择默认模型。
- 「添加模型」输入框 + 按钮 → `add_model(provider_id, model_name)` 后刷新。
- 「设为手机默认」→ 写入 `controller.config.provider_id/model_name`，`controller.save_config()`。
- 提示：API Key 存在本机 BiliNote 引擎的数据库里，不会上传到 Relay。
- 错误/加载态要可见；引擎未运行时页面顶部提示并禁用操作。

### 3.3 语音转写页
- 从 `/transcriber_config` 加载：
  - 转写引擎下拉（fast-whisper / groq / bcut / kuaishou；mlx 仅 macOS 隐藏）。
  - 本地模型下拉（whisper_model_sizes）。
  - 状态列表：每个模型 已下载/未下载/下载中/失败 + 「下载」按钮。
  - 「保存设置」→ `update_transcriber_config(type, size)`。
- 「下载模型」→ `download_whisper(size, type)`，然后每 3 秒轮询 `transcriber_status()` 直到 downloading=false；显示状态与失败原因。
- Groq 选中时提示：先在 AI 页选中 Groq 供应商并填 API Key。
- 显示当前引擎的 `/sys_health` 里 whisper 信息。

### 3.4 引擎与 ffmpeg 页
- BiliNote 引擎：
  - 路径 QLineEdit + 「浏览」选择 `BiliNoteBackend.exe` + 「检测」自动找。
  - 状态、PID、「启动 / 停止 / 重启」。
  - 「打开安装目录」「打开引擎日志」。
- ffmpeg：
  - 路径/版本、状态；「浏览」选择 bin 目录；「自动检测」；「下载安装」+ 进度条。
- 网络：
  - 代理启用 checkbox + URL；保存时同时写 `controller.config` 和 `api.update_proxy_config`。
  - HuggingFace 镜像 `HF_ENDPOINT` 输入（如 `https://hf-mirror.com`）。
- 系统健康表：`api.sys_health()` 的 backend/ffmpeg/db/whisper_model 行。

### 3.5 设置页
- Relay 地址、Agent 名称。
- 开机自启 checkbox（`controller.set_autostart` / `autostart_enabled`）。
- 关闭窗口时最小化到托盘 checkbox（写 `config.close_to_tray`）。
- 启动时最小化 checkbox（`config.start_minimized`）。
- 生成参数：quality/style/screenshot/insert_video_links（写 config）。
- 路径展示：数据目录/日志目录/Agent 配置，按钮打开。
- 「重新配对」按钮（未实现可先留 TODO，调用 controller.cancel_pairing + 提示）。
- 版本号 `0.2.0`。

## 3.6 首次配置向导（onboarding，新增）

普通用户安装后第一次打开必须走向导，不能要求用户自己装 BiliNote、配路径或命令行。

- 触发：`MainWindow` 启动时若 `controller.config.onboarding_done == False`，自动打开模态向导；主界面在向导完成前不启动引擎/Agent（controller.start 已按此逻辑）。
- 文件：`ui/onboarding.py`，窗口约 760×560，浅蓝/品牌蓝风格。
- 步骤：
  1. 欢迎 + 隐私说明（视频/音频/Key 留本机；只中转链接和 Markdown）。
  2. ffmpeg：`controller.ffmpeg_ready()`；没有则「下载 ffmpeg」，显示进度；成功后下一步。
  3. 引擎：安装包已内置 BiliNote；调用 `controller.start_engine_async()`，等待 `statusChanged` 中 engine.state == running；失败显示日志提示，可重试。
  4. AI 模型：从 `/get_all_providers` 加载，默认 DeepSeek；填 API Key，保存并测试连接。允许“稍后配置”，但提示不配置无法生成笔记。
  5. 语音转写：默认 fast-whisper + small；显示模型大小并「下载模型」，轮询 `/transcriber_models_status`；也可选 Groq（提示去 AI 页配 Groq key）；允许跳过。
  6. 配对手机：`controller.create_pairing_async()`，显示大号配对码 + 二维码（内容建议是 `https://relay.xykong.xyz/#/pair?code=XXXX`）+ Relay 地址；等待 claimed；允许跳过。
  7. 完成：`controller.complete_onboarding()`，关闭向导，进入主界面。
- 每一步有「上一步/下一步/跳过」，错误内联展示而不是抛异常；所有耗时操作走 `controller.run_async`。
- 向导完成后可在设置页重新打开（“配置向导”按钮）或重新配对。

## 3.7 日志页

- Tab：Agent / BiliNote 引擎。
- QPlainTextEdit 只读等宽，每 2 秒刷新 `agent_log_tail(300)` / `engine_log_tail(300)`；「打开日志目录」。
- 不要在日志页做自动滚动到末尾以外的复杂功能。

## 4. 托盘（`ui/tray.py`）

- `TrayIcon(controller, window)`：
  - QSystemTrayIcon + 菜单：状态（disabled，显示「引擎运行 · Agent 在线」/「未配对」/「离线」）、打开主界面、生成配对码、暂停/继续手机任务、重启引擎、打开日志、退出。
  - 双击/单击 show window。
  - 根据 `statusChanged` 换图标状态：绿（引擎+Agent+Relay 都 OK）、黄（部分）、红（错误/未配对）。图标可用 QPainter 在资源 icon 上加圆点。
  - `notification` 信号用 `showMessage` 弹托盘通知。
  - 退出时调用 `controller.shutdown()`，并 `QApplication.quit()`。

## 5. 主窗口（`ui/main_window.py`）

- 左侧导航：品牌区（icon+LinkNote/链记）、页面按钮（状态/AI 模型/语音转写/引擎与 ffmpeg/日志/设置）、底部版本。
- 右侧 QStackedWidget。
- 关闭事件：`config.close_to_tray` 为 true 时 hide + tray 提示；否则退出。
- 连接 controller 信号：状态页、托盘、通知。
- 提供方法 `show_page(name)`、`show_and_activate()`。

## 6. 约束

- 只改 `desktop/linknote_desktop/ui/`（和 `resources` 如必须，但已有 icon.png/icon.ico）。
- 不修改 `controller.py`、`config.py`、`services/`、入口文件；缺 API 在报告里列出来，由 lead 补。
- 所有控件中文；不要用外部 CDN。
- 交付前至少：
  - `python -m compileall desktop/linknote_desktop/ui` 通过；
  - 写一个 `ui/_smoke.py` 或说明如何 `QT_QPA_PLATFORM=offscreen` 实例化 MainWindow；
  - 报告文件清单、未实现/依赖 lead 的接口。

