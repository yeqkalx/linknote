# LinkNote PC Agent（Windows）

手机分享视频链接 → 云端 Relay → **这台电脑上的 BiliNote** 生成 AI 笔记 → Markdown 回到手机。

本目录是常驻在 Windows 上的 PC Agent：通过 **Relay WebSocket 实时通道**接收手机任务，
调用本机 BiliNote 后端（`http://127.0.0.1:8483/api`）生成笔记，把进度和 Markdown 回传。
视频、音频、Whisper 模型、LLM API Key **都不离开这台电脑**；云端只中转链接、状态和
一次性的笔记正文（不落 D1/DO SQLite）。

- 接口契约：[../docs/api-contract.md](../docs/api-contract.md)（REST）
- 实时协议：[../docs/realtime-protocol.md](../docs/realtime-protocol.md)（WS，冻结）
- 架构说明：[../docs/architecture.md](../docs/architecture.md)

```
Relay（WSS 实时通道 + Bearer agt_...）
   │  ← hello / task / note_request / ack_note / ping
   │  → task_update / task_note / pong
   ▼
PC Agent（本程序：python -m linknote_agent run）
   │  http://127.0.0.1:8483/api
   ▼
BiliNote 后端（已安装，配置好 LLM Key / 转写引擎 / ffmpeg）
```

## 前置条件

1. **BiliNote 已安装并可用**：LLM provider/model、转写引擎、ffmpeg 都已配好
   （先用 BiliNote 网页界面成功生成过一篇笔记）。
   打包版默认后端路径为 `C:\app\bilinote\BiliNoteBackend.exe`，如不同请在配置里改。
2. Windows 10/11，已安装 [uv](https://docs.astral.sh/uv/)（Python 3.11 可由 uv 自动下载）。
3. 一个 Relay 地址：生产 `https://relay.xykong.xyz`，本地开发 `http://127.0.0.1:8787`。

## 安装

在 `pc-agent` 目录执行（PowerShell / cmd 均可）：

```powershell
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -e ".[dev]"
```

运行时依赖：`httpx`（REST）、`websockets`（实时通道）；`dev` 额外装
`pytest` / `pytest-asyncio`。

## 配对（第一次使用）

```powershell
# 生产 Relay（默认）
.venv\Scripts\python.exe -m linknote_agent pair

# 本地开发 Relay
.venv\Scripts\python.exe -m linknote_agent pair --relay http://127.0.0.1:8787

# 可选：自定义电脑名（手机端显示的名字）
.venv\Scripts\python.exe -m linknote_agent pair --name 我的书房电脑
```

终端会打印 8 位配对码和过期时间（10 分钟）：

```
配对码：K7M4-9QXB
有效期至：2026-10-24 12:40:00（本地时间，约 10 分钟）
凭据已保存：C:\Users\你\.linknote\agent.json
请在手机 LinkNote App 输入上面的配对码完成配对。
等待手机领取配对码…
```

手机输入配对码后，命令会打印设备名并退出。`agent_token` 在创建配对的瞬间就已**原子写入**
`~/.linknote/agent.json`；即使配对码过期/终端被关掉，凭据也在本机（过期后需重新 `pair`
生成新码）。

也可以双击 `packaging\pair.cmd`（等价命令，带 PATH 检查）。

## 运行

```powershell
.venv\Scripts\python.exe -m linknote_agent run
# 或者双击 packaging\start_agent.cmd
```

启动后（WebSocket 推送模式）：

- 用 `agent_token`（Header `Authorization: Bearer ...`）连 `wss://<relay>/api/v1/agent/ws`，
  收到 `hello` 后开始工作；服务端推 `task` 才处理（同一时刻只跑一个任务，BiliNote 后端本身串行）；
- **心跳走 WebSocket 协议层 ping**（`ping_interval=25s` / `ping_timeout=20s`，Cloudflare 边缘
  自动应答，不唤醒 DO、不计请求）；**不再发应用层 JSON ping**。仍保留对服务端 JSON
  `{"type":"ping"}` 回 `pong`、对 `{"type":"probe"}` 回 `probe_ack`（旧 Relay 兼容）；
- 收到 `hello` 后立即上报一次 `agent_ready`（`ready` + `issues`），readiness 变化时重发：
  - `model_not_configured`：`provider_id` / `model_name` 为空；
  - `engine_unreachable`：BiliNote `/api/health` 失败/超时（≤2s，老版本自动回退 provider 探针）；
  - BiliNote 健康探测结果缓存 30s，服务端 ping/probe 触发的刷新不会每次都探测；
- 断线按 `1/2/4/8/15/30 秒 + 抖动` 永久重连；网络类错误时每秒探测 Relay 主机，
  网络一恢复立即重连；
- 重连成功后重新 `hello`，补发当前任务状态、readiness 和所有未 ack 的笔记；服务端也会重推
  `queued/running` 任务（契约 §11.3：每次最多 50 条，其余由 Agent 用 REST 补领一次）；
- **WS 断开期间**用 REST `GET /api/v1/agent/tasks/next` 兜底领任务，WS 恢复后立即停用；
  不做空闲轮询（hello 后的一次性补领 + 断开兜底，两处都用任务 id 去重，避免重复处理）；
- 服务端主动关闭码按契约 §11.1 分类：`4001`（被另一个 Agent 实例顶掉，日志报「重复实例」并
  继续退避重连）、`4002`（心跳超时）、`1000/1001`（主动关闭）都会自动重连；
- 领到任务后自动检查 BiliNote 后端，没启动就拉起 `BiliNoteBackend.exe`
  （隐藏窗口 + 日志重定向到 `~/.linknote/logs/bilinote_backend.log`）；
- 提交 `/generate_note`，每 3 秒轮询 `/task_status/{id}`，状态变化时通过 WS
  `task_update` 回传 `status/progress/stage/message`；
- 成功后把 Markdown **先保存到本机**（`notes/` + `outbox/`），再发 `task_note`；
  手机收到后发 `ack_note`：
  - `persisted!==false`（含旧 Relay 缺省）→ 清理 outbox、保留 `notes/`；
  - `persisted:false`（手机写本地缓存失败）→ **保留 outbox**，按 `30s / 2min / 10min`
    最多重发 3 次 `task_note`；之后等 `note_request` 或重启/重连补发；
- 手机请求未缓存的笔记（`note_request`）时，从本地 outbox/notes 重发 `task_note`；
- 失败把常见错误翻成中文，通过 WS `task_update status=failed error=... error_code=...` 回传。

调试用：

```powershell
.venv\Scripts\python.exe -m linknote_agent run --once --verbose   # 用 REST 领一个任务并处理（调试）
.venv\Scripts\python.exe -m linknote_agent run --poll             # 强制旧 REST 轮询（排障/兼容）
.venv\Scripts\python.exe -m linknote_agent status               # 查看配对/Relay/WS/后端状态
.venv\Scripts\python.exe -m linknote_agent status --json        # 机器可读
```

### 笔记与 outbox 的关系（实时模式）

- 笔记正文**只在本机**：`~/.linknote/notes/<task_id>.md` 永久保留；
- `~/.linknote/outbox/note-<task_id>.json` 是「待手机确认保存」队列（含正文副本）：
  断线/重启后会重发 `task_note`；收到 `ack_note persisted!==false` 后该 JSON 删除，
  Markdown 文件保留；`ack_note persisted:false` 时保留并按 30s/2min/10min 重发最多 3 次；
- 超过 `note_retention_days`（默认 7 天）仍未 ack 的 outbox 记录会被清理（本地 md 不删）；
- 手机端 `note_request` 即使 outbox 已清理，也会从 `notes/*.md` 读取并重发。

## Bot 入口（v0.4：Telegram / QQ）

除了手机 PWA/APK，本机 Agent 还能同时跑 **自己的** Telegram Bot / QQ 机器人作为链接入口。
设计见 [../docs/bot-channels.md](../docs/bot-channels.md)。

- Bot 与 WS/REST runner **并行启动**（`run` 时自动拉起，任一个 channel 启用即启动），不阻塞任务处理；
- Bot 用 Relay 签发的**专用 device token**（`POST /api/v1/agent/devices`）建任务/查任务，
  不碰手机 token；任务仍由同一台电脑的 Agent 生成；
- 完成后从本机 `notes/<task_id>.md` 读笔记回发；pending 持久化在 `~/.linknote/bot-state.json`，
  重启恢复；QQ 回复窗口关闭时转 pending，等 `/last` 或用户下一条消息补发；
- 命令：`/start`、`/help`、`/status`（最近 5 个任务）、`/last`（重发最近笔记）、
  `/retry <task_id>`（重试失败任务）；其他文本提取第一个支持平台的链接建任务；
- 本地限流：同一聊天每小时最多 10 个任务。

### Telegram（长轮询，无需公网/Webhook）

1. 找 [@BotFather](https://t.me/BotFather) `/newbot` 拿 token；
2. 编辑 `~/.linknote/agent.json`（或环境变量）：

```jsonc
{
  "telegram_enabled": true,
  "telegram_bot_token": "123456:ABC...",   // 只存本机，不写日志/不上传
  "telegram_allow_groups": false            // 默认只处理私聊；群聊需 @bot 或命令
}
```

3. 重启 `run`：日志出现 `Telegram Bot 已就绪：@your_bot`，`getMe` 会自动回填 username。
   - 私聊：所有文本都处理；群聊：`telegram_allow_groups=true` 时只处理 `@bot` / 命令；
   - 完成投递：`sendMessage` 预览（`bot_telegram_preview_chars`，默认 3500 字）+
     `sendDocument` 上传完整 `.md`；
   - `getUpdates` offset 持久化（重启不重复/不漏），429 按 `retry_after` 退避。
   - **中国大陆直连 Telegram 通常不可用**，需用户自行解决网络。

### QQ（官方开放平台 WebSocket 网关）

1. 在 QQ 开放平台创建机器人，拿 AppID/AppSecret；
2. 配置：

```jsonc
{
  "qq_enabled": true,
  "qq_app_id": "102000001",
  "qq_app_secret": "***",                   // 只存本机
  "bot_qq_chunk_chars": 1500                // 长笔记分片
}
```

3. 重启 `run`：Agent 会用 `getAppAccessToken`（缓存 7200s）→ `GET /gateway` →
   WSS 连接 → op10 hello → op2 Identify（`QQBot <token>`、`intents=1<<25`）→ 心跳/Resume，
   日志出现 `QQ Bot 已就绪（READY）` 即连接成功。
   - 收 `C2C_MESSAGE_CREATE` / `GROUP_AT_MESSAGE_CREATE`；链接取 `content`，否则取
     `ark_data.fields.jump_url`（QQ 会把 B 站链接转成卡片）；
   - 被动回复带原消息 `msg_id` + 递增 `msg_seq`（C2C 60 分钟内最多 4 次、群 5 分钟最多 5 次）；
     长笔记按 `bot_qq_chunk_chars` 分片，末尾提示完整笔记在电脑/手机；
   - 超窗口/无权限 → 笔记转 pending，用户发 `/last` 或下一条消息时用新的被动窗口补发；
   - **个人主体限制**：QQ 开放平台个人认证暂不支持公开使用，只能加自己当群主/管理员的群，
     或使用 20 个「内部体验号码」；要公开需企业/个体户认证；
   - `intents 1<<25` 需在开放平台申请单聊/群事件权限，否则 Identify 失败。

### 用环境变量联调（凭证不要进仓库）

```powershell
# Telegram
$env:LINKNOTE_TELEGRAM_ENABLED = "true"
$env:LINKNOTE_TELEGRAM_BOT_TOKEN = "<BotFather token>"
# QQ
$env:LINKNOTE_QQ_ENABLED = "true"
$env:LINKNOTE_QQ_APP_ID = "<AppID>"
$env:LINKNOTE_QQ_APP_SECRET = "<AppSecret>"
.venv\Scripts\python.exe -m linknote_agent run
```

> Bot token/AppSecret 只存本机 `agent.json`；日志/异常/`status --json` 导出都经过 mask。
> 任何能给这个 bot 发消息的人都能用你的电脑生成笔记，**不要把 bot 公开给陌生人**。

## 登录自启（可选）

```powershell
# 安装（启动文件夹快捷方式，pythonw 静默运行）
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\install_autostart.ps1

# 安装并立即后台跑一次
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\install_autostart.ps1 -Start

# 取消自启
powershell -NoProfile -ExecutionPolicy Bypass -File .\packaging\install_autostart.ps1 -Remove
```

也可以直接运行 CLI 子命令（内部调用同一脚本）：

```powershell
.venv\Scripts\python.exe -m linknote_agent install-autostart
.venv\Scripts\python.exe -m linknote_agent install-autostart --remove
```

> 自启用 `pythonw.exe`（无控制台窗口）。所有输出都写到
> `%USERPROFILE%\.linknote\logs\agent.log`（5 MB 滚动、保留 3 份），排障先看它。

## 命令一览

| 命令 | 作用 |
|---|---|
| `pair [--relay URL] [--name 名字] [--no-wait]` | 创建配对、保存 token、显示配对码并轮询领取 |
| `run [--poll] [--once] [--relay URL]` | 常驻运行（默认 WebSocket 推送；`--poll` 强制 REST 轮询，`--once` 只走一次 REST 领取） |
| `status [--json]` | 查看配对、Relay/WS 地址、BiliNote 后端、outbox/待 ack 笔记数量 |
| `install-autostart [--remove]` | 安装/移除登录自启快捷方式 |
| `unpair [--yes] [--revoke-devices]` | 清除本机凭据；`--revoke-devices` 同时撤销所有手机 |
| `--help` / `<command> --help` | 查看帮助 |
| `--version` | 版本号 |
| `--config PATH`（全局） | 指定配置文件路径（默认 `~/.linknote/agent.json`） |

## 配置：`~/.linknote/agent.json`

`pair` 会自动创建；其余字段可手改（改完重启 `run` 生效）。可用环境变量覆盖：
`LINKNOTE_<字段名大写>`，例如 `LINKNOTE_PROVIDER_ID=deepseek`、
`LINKNOTE_BACKEND_EXE=D:\bilinote\BiliNoteBackend.exe`。

| 字段 | 默认值 | 说明 |
|---|---|---|
| `relay_base_url` | `https://relay.xykong.xyz` | Relay 地址（不带 `/api/v1`） |
| `agent_token` | `""` | 配对得到，**只在本机，勿外传**；丢失只能重新配对 |
| `agent_id` / `agent_name` / `platform` / `version` | pair 写入 | 展示用 |
| `backend_base_url` | `http://127.0.0.1:8483/api` | BiliNote 后端地址 |
| `backend_exe` | `C:\app\bilinote\BiliNoteBackend.exe` | 后端可执行文件；不自动启动可留空 |
| `backend_workdir` | `C:\app\bilinote` | 启动后端时的工作目录 |
| `auto_start_backend` | `true` | 后端没跑时是否自动拉起 |
| `backend_start_timeout` | `90` | 等待后端就绪秒数 |
| `provider_id` / `model_name` | `deepseek` / `deepseek-v4-flash` | BiliNote 的 LLM 配置（与 BiliNote 界面里的要一致） |
| `quality` | `fast` | `fast` / `medium` / `slow` |
| `style` | `minimal` | 笔记风格（必须是非空字符串） |
| `screenshot` | `false` | 是否生成视频截图（会显著变慢） |
| `insert_video_links` | `true` | 笔记里插入可跳转的时间点链接 |
| `poll_interval_seconds` | `4.0` | REST 轮询模式空闲领取间隔（WS 断开兜底时用 `fallback_poll_interval_seconds`） |
| `fallback_poll_interval_seconds` | `4.0` | WS 断开期间 REST 兜底领取任务的间隔 |
| `task_status_poll_interval_seconds` | `3.0` | 轮询 BiliNote 状态间隔 |
| `progress_min_interval_seconds` | `8.0` | 同状态进度回传最小间隔（契约 ≤10 秒） |
| `heartbeat_interval_seconds` | `30.0` | REST Agent 心跳间隔（WS 模式下仍保留，兼容旧 REST 在线状态） |
| `lease_heartbeat_interval_seconds` | `300.0` | 长任务租约续期间隔（契约 5 分钟） |
| `task_timeout_seconds` | `3600.0` | 单任务最长等待，超时判失败 |
| `max_status_errors` | `20` | 连续查询 BiliNote 状态失败多少次判定后端失联 |
| `use_websocket` | `true` | 是否走 WS 推送；false 等价 `run --poll` |
| `ws_url` | `""` | 留空由 `relay_base_url` 推导；本地联调可填 `ws://127.0.0.1:8798/api/v1/agent/ws` |
| `ws_ping_interval_seconds` | `25.0` | **WebSocket 协议层 ping 间隔**（v0.3 起不再发应用层 JSON ping） |
| `ws_ping_timeout_seconds` | `20.0` | 协议层 ping 未回 pong 的超时（websockets 据此判定断线并重连） |
| `ws_dead_timeout_seconds` | `60.0` | **已废弃**（v0.3 起改用协议 ping 判活）；保留字段仅为兼容旧配置 |
| `ws_hello_timeout_seconds` | `10.0` | 连接后等待 hello 的超时 |
| `ws_reconnect_delays` | `[1,2,4,8,15,30]` | 断线退避基数（秒），可写成 `"1,2,4,8,15,30"` |
| `ws_jitter_ratio` | `0.2` | 每次退避附加 0–20% 抖动，总上限 30 秒 |
| `rest_fallback` | `true` | WS 断开期间是否用 REST `/agent/tasks/next` 兜底 |
| `rest_heartbeat_enabled` | `true` | WS 模式是否同时发 REST 心跳（兼容旧 REST 在线状态） |
| `note_retention_days` | `7.0` | 未 ack 笔记在 outbox 的保留天数（`notes/` 永久保留） |
| `readiness_health_cache_seconds` | `30.0` | BiliNote 引擎健康探测缓存（agent_ready 用） |
| `readiness_health_timeout_seconds` | `2.0` | BiliNote `/api/health` 探测超时 |
| `note_ack_retry_delays_seconds` | `[30,120,600]` | `ack_note persisted=false` 时最多 3 次重发延迟（秒） |
| `telegram_enabled` | `false` | 是否启用 Telegram Bot 入口 |
| `telegram_bot_token` | `""` | BotFather token（日志/导出 mask，只存本机） |
| `telegram_bot_username` | `""` | getMe 回填/缓存 |
| `telegram_device_token` | `""` | Relay 为 Bot 签发的专用 device token（只存本机） |
| `telegram_allow_groups` | `false` | 默认只处理私聊；开启后群聊只处理 @bot/命令 |
| `qq_enabled` | `false` | 是否启用 QQ 官方 Bot 入口 |
| `qq_app_id` / `qq_app_secret` | `""` | QQ 开放平台凭证（secret 日志/导出 mask，只存本机） |
| `qq_device_token` | `""` | Relay 为 QQ Bot 签发的专用 device token |
| `bot_task_poll_seconds` | `3.0` | Bot 轮询 Relay 任务状态间隔 |
| `bot_task_timeout_seconds` | `3600.0` | Bot 端单任务最长等待 |
| `bot_telegram_preview_chars` | `3500` | Telegram 预览正文字符数（完整笔记走附件） |
| `bot_qq_chunk_chars` | `1500` | QQ 长笔记分片字符数（受被动回复次数限制） |
| `outbox_dir` / `notes_dir` / `log_dir` | `""`（用 `~/.linknote/` 下默认子目录） | 可自定义 |
| `verbose` | `false` | 是否输出 DEBUG 日志 |

## 本地数据目录（`~/.linknote/`）

| 路径 | 内容 |
|---|---|
| `agent.json` | 配置 + agent_token（原子写；POSIX 上权限 0600，Windows 上 token 仅存本机） |
| `logs/agent.log` | Agent 日志（5 MB 滚动 ×3） |
| `logs/bilinote_backend.log` | 自动拉起的 BiliNote 后端 stdout/stderr |
| `outbox/*.json` | WS 模式：`note-*.json` 待手机 ack 的笔记（正文副本）；REST 模式：`complete-*`/`fail-*` 待补传 |
| `notes/<task_id>.md` | 笔记正文永久留档（笔记从不写进 Relay D1/DO SQLite） |

## 状态映射

BiliNote 任务状态 → 契约 stage（`docs/api-contract.md` §7.2 只允许这六种）：

| BiliNote | 契约 stage | 默认 progress | 回传 message 示例 |
|---|---|---|---|
| `PENDING` | `parsing` | 2 | 已领取任务，等待 BiliNote 开始处理（已用 12 秒） |
| `PARSING` | `parsing` | 5 | 正在解析链接（已用 20 秒） |
| `DOWNLOADING` | `downloading` | 20 | 正在下载音视频（已用 45 秒） |
| `TRANSCRIBING` | `transcribing` | 45 | 正在语音转写（已用 2 分 10 秒） |
| `SUMMARIZING` | `summarizing` | 70 | AI 正在总结内容（已用 3 分 5 秒） |
| `FORMATTING` | `formatting` | 85 | 正在整理格式（已用 3 分 20 秒） |
| `SAVING` | `saving` | 95 | 正在保存笔记（已用 3 分 25 秒） |
| `SUCCESS` | `saving` → done | 100 | WS `task_update status=completed` + `task_note`（正文不回写服务器） |
| `FAILED` | — | — | WS `task_update status=failed error=中文原因 error_code=...` |

> 契约里 Agent 能回传的 stage 没有 `pending`，而 BiliNote 的 `PENDING` 是「已进后端队列、
> 还没开始处理」。这里映射为 `parsing` 并在 message 里明确写「等待 BiliNote 开始处理」，
> 语义不丢，也不违反契约枚举。

## 常见失败提示

Agent 会把 BiliNote 的原始报错翻译成中文再回传 fail（手机端直接展示）：

| 现象/原文关键字 | 回传 code | 手机端看到的中文提示 |
|---|---|---|
| `401` / `invalid_api_key` / `api key` | `model_auth_error` | 模型 API Key 失效或未配置，去 BiliNote 设置里填 Key |
| 余额/额度/quota/insufficient | `quota_exceeded` | 模型账户余额或额度不足，充值或换服务商 |
| 转写模型未下载就绪 / whisper | `transcriber_not_ready` | 去 BiliNote 设置 → 音频转写配置下载模型或改用在线引擎 |
| 不支持平台/链接无效 | `unsupported_platform` | 确认链接来自 B站/YouTube/抖音/快手且视频公开 |
| 后端未启动/启动失败 | `backend_unavailable` | 检查 BiliNote 是否安装、`backend_exe` 路径与后端日志 |
| 超过任务超时 | `task_timeout` | 视频过长或网络较慢，稍后重试 |
| BiliNote 成功但 Markdown 为空 | `empty_note` | 到 BiliNote 界面查看任务详情后重试 |

## 排障

1. **`run` 提示「还没有配对」**：先 `pair`；或本机 `~/.linknote/agent.json` 丢了
   （token 丢失只能重新配对）。
2. **手机端显示电脑离线**：`status` 看 Relay 是否可达；WS 日志（连接/重连/心跳）在
   `agent.log`。代理/VPN 可能拦截 `connect`，给 `httpx`/`websockets` 配代理见「已知风险」。
3. **`401` 配对失效**：Relay 侧 token 被撤销/重配过。重新 `pair`（新 token 覆盖旧配置）；
   WS 握手 401 时 Agent 会记 ERROR、继续按退避重连并用 REST 兜底，不会崩溃。
4. **WS 连不上但 REST 正常**：检查防火墙/代理是否允许 `Upgrade`；Agent 会自动走
   REST `/agent/tasks/next` 兜底保活，`status` 里能看到 WS 地址。
5. **BiliNote 后端拉不起来**：
   - 确认 `backend_exe` 指向真实文件；
   - 看 `~/.linknote/logs/bilinote_backend.log`（通常是 BiliNote 自身配置/端口占用）；
   - 也可以手动先打开 BiliNote，再 `run`（`auto_start_backend=false` 时只探测不拉起）。
6. **转写/模型报错**：Agent 只是转发 BiliNote 的错误，按手机端提示去 BiliNote 设置里修。
7. **outbox 里有待 ack 笔记堆积**：手机没上线/没 ack。恢复后重连会自动重发，
   手机打开该任务会发 `note_request` 再要一次；超过 7 天未 ack 的 outbox 记录会自动清理，
   `notes/` 里的 Markdown 不会丢。
8. **开机自启后「没反应」**：pythonw 没有窗口是正常的；看
   `%USERPROFILE%\.linknote\logs\agent.log` 和 `status.cmd` 的输出。
9. **端口/地址**：BiliNote 默认 8483；Relay 本地 wrangler 默认 8787（realtime 调试脚本是
   8798）。Agent 不监听任何端口。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest -q
```

当前 **254 passed**。测试**不依赖真实 Relay / BiliNote / Telegram / QQ**：REST 用
`httpx.MockTransport` 在进程内模拟（`tests/conftest.py` 的 `FakeRelay` / `FakeBiliNote`）；
WS 用注入的 fake connection + 本机 `websockets` fake server（真实握手，随机端口）验证。覆盖：

- 配置读写/原子写/环境变量/损坏容错（含所有 WS/readiness/ack/Bot 字段与列表类型转换、
  Bot token/secret 的 mask 与 repr 不泄露）；
- 配对流程（创建、保存 token、打印配对码、claimed / expired / 超时 / Relay 断开）；
- 任务生命周期（领任务 → 启动后端 → 提交 → 进度 → complete），进度映射与 10 秒节流；
- 失败路径与中文错误翻译、租约/Agent 心跳、REST outbox 落盘与重启补传、本地留档；
- **实时通道（v0.3）**：WS URL 推导、退避序列 `1/2/4/8/15/30+jitter`、真实本地 WS 握手
  （路径 + `Authorization: Bearer` + hello + 收发）、401 分类；
  **协议层 ping 参数（无应用层 JSON ping）**、服务端 JSON ping→`pong`、`probe`→`probe_ack`、
  空闲不因 dead_timeout 掉线、网络探测提前重连、事件分发、任务更新、`task_note` 只落本地、
  `note_request` 重发、**`ack_note persisted` 语义与 3 次重试（30s/2min/10min）**、
  **`agent_ready` 首次上报/变化重发/健康探测 30s 缓存/引擎探测回退**、
  重连补发、REST 兜底、旧 outbox 迁移；
- **Bot 入口（v0.4，全部 mock，不触网）**：
  - `bot-state.json` 原子读写、Telegram offset、QQ session/event seq、pending、限流持久化；
  - Telegram：`getMe` username、`getUpdates` 长轮询 + offset 持久化、私聊/群聊（@bot/命令）
    过滤、bot 消息忽略、预览截断 + `sendDocument` multipart、401/409/429 处理、
    错误信息不含 token；
  - QQ：access_token 缓存、Identify（`QQBot <token>` / `intents=1<<25` / `shard`）、
    心跳 op1、READY session、C2C/群事件与 `ark_data.fields.jump_url`、按事件 id 去重、
    被动回复 `msg_id`+`msg_seq`、长笔记分片上限、回复窗口关闭抛 `ReplyWindowClosed`；
  - Runner：链接→平台校验→bot device token 建任务→「已提交」→轮询→本机 notes 投递、
    失败通知、重启恢复 pending、device token 签发/保存/复用、10 任务/小时限流、
    `/help /status /last /retry`、QQ 窗口关闭转 pending 后由下条消息补发、CLI 并行启停；
- BiliNote 请求体字段（与 `wecom_bot` 完全一致）、ResponseWrapper 解包、engine_health 探测；
- CLI（`--help`、未配对拦截、`status --json`、`unpair`、`install-autostart` 命令拼装、
  `run --once`、`run --poll`、默认 WS 模式、Bot Runner 启停）。

> 真实 Telegram / QQ / Bot 凭证的端到端验证**未在本机执行**（省钱/无需真实凭证），
> 由用户在桌面 UI 填入自己的凭证后自行验证；mock 单测覆盖协议与业务分支。

真实链路验证（2026-10-24，fake BiliNote + fake phone，节选）：

**v0.3（本机 relay 8796 = relay v0.3 工作树快照 + 默认 30s 探测/退避配置）**
- Agent 连上后收到 hello 立即上报 `agent_ready ready=true issues=[]`，手机 hello/presence
  都带 `ready:true`（在线 ≠ 可生成的字段打通）；
- 静置 17s 后手机才连接：Relay 发 `probe`+`ping` 探活，Agent 回 `probe_ack`+`pong`，
  presence 保持 `online:true`（probe→probe_ack 链路）；
- 手机建任务 → `task_updated` → `note`；手机先 `ack_note persisted=false`（模拟写缓存失败）：
  Agent **保留 outbox** 并按配置延迟重发 `task_note` **3 次**，手机收到 3 次重发；
  随后 `ack persisted=true` → Agent 清 outbox、`notes/<task_id>.md` 保留；
- `GET /tasks/:id` 无 markdown 字段、`/tasks/:id/note` **410 `note_not_stored`**；
- Agent 全程无应用层 JSON ping（协议层 ping 保活）；
- relay 修复 `intVar` 缺省值 bug 后复测 alarm 频率：**只有 Agent（无手机）35s 内 0 次服务端
  JSON ping**；手机连接后 65s 仅 **30.1s / 60.1s 两次 ping**（30s 口径），Agent/手机均保持连接。
  该 bug 会让缺省 alarm/ping 变成 1s（v0.2 线上曾中招），v0.3 修复后符合降本目标。

**生产 relay v0.3.0（`wss://relay.xykong.xyz`，worker `c312b769…`）**
- health `version: 0.3.0`；Agent WSS 握手 101 → `agent_ready` → `/agent/me` 顶层
  `ready:true`、手机 hello/presence `ready:true, issues:[]`；
- 静置 17s 后手机连接触发 `probe`/`ping`，Agent 回 `probe_ack`/`pong`，presence 保持 online；
- 手机建任务 → `note`；`ack persisted=false` → Agent 保留 outbox 并重发 `task_note` 3 次 →
  手机 `persisted=true` → outbox 清空、`notes/` 保留；D1 无 markdown、`/note` 410；
- alarm 频率：无手机 35s **0 次 ping**；手机在线 65s **2 次 ping（29.9s / 59.8s）**，
  Agent 与手机全程在线（手机按契约回 pong）；
- 部署后边缘传播期偶发 SSL EOF/401：Agent 的重连退避会自动恢复（本次 E2E 脚本对边缘请求
  也做了退避重试，生产最终链路零人工干预跑通）。

**新 Agent + 旧 Relay 兼容（生产 `wss://relay.xykong.xyz`，relay v0.2.0）**
- 完整任务链路（pair/claim → hello → task → task_update → task_note → note → ack → 清理）通过，
  Agent 的 `agent_ready` 被旧 Relay 忽略但**不断连**；
- 旧 Relay 每 ~1s 发 JSON `ping`，新 Agent 回 `pong`，持续 70s **零断线**
  （旧 Relay 的 JSON 心跳仍可用，新 Agent 不再主动发 JSON ping）。

**生产 relay v0.2.0 任务链路**：Agent WSS 握手 **HTTP 101**（`Authorization: Bearer agt_…`）、
首帧 hello；手机建任务 1.3s 收到 note；`task_updated` 序列 `parsing→downloading→transcribing→
summarizing→succeeded 100`；ack 后 outbox 清空、`notes/` 保留；`/note` 410。
同 token 第二条连接顶替：旧连接收 **close 4001**，Agent 日志提示「可能重复启动」并自动重连成功。

> 注意：Relay v0.3 会按 `phone_stale` 关闭「长时间没有任何消息」的手机 socket（close 4002），
> 手机端必须响应服务端 JSON `ping`（回 `pong`）并/或每 25s 发 `ping`，否则会被判 stale
> （本 E2E 的 fake phone 一开始没回 pong 就复现了 4002；PWA 需保证该行为）。

## 与契约相关的重要说明（实现选择）

1. **只使用契约字段**：任务只读 `id/url/platform/attempts/created_at`；progress 只发契约
   允许的六种 stage；complete/fail 的字段、长度、幂等语义都按契约处理。
2. **进度节流**：状态变化立即回传；同状态最多每 `progress_min_interval_seconds`（默认 8 秒，
   ≤ 契约的 10 秒）回传一次，同时顺延租约。
3. **outbox 的范围**：契约只要求「已完成笔记」可重试，这里把 `fail` 也纳入 outbox
   （同样幂等），并在补传被明确拒绝（404/409/401）时把笔记另存本地，
   避免用户既拿不到结果、本地也没有留档。
4. **Markdown > 1.5 MB**：Agent 先在本地拦截，不把必然 413 的请求发出去；任务以 `note_too_large`
   失败，同时把完整 Markdown 存到 `~/.linknote/notes/`（契约要求 ≤1.5 MB，无法绕过）。
5. **PENDING → parsing**：见上文状态映射说明。
6. **Token 安全**：只存本机 `agent.json`（原子写 + 尽量 0600），不写日志、不进 URL；
   Windows 上后续可接 DPAPI（契约/架构文档均已注明 MVP 阶段明文）。
7. **失败 error_code**：契约给了 `bilinote_error` 示例但未穷举，这里使用
   `model_auth_error` / `quota_exceeded` / `transcriber_not_ready` / `unsupported_platform` /
   `backend_unavailable` / `task_timeout` / `empty_note` / `note_too_large` 等可读 code，
   `message` 始终是可直接展示的中文。
8. **实时模式的任务领取**：WS 在线时**不轮询** REST `/agent/tasks/next`（避免「推送 + REST
   轮询」重复处理同一任务）。只在两处用 REST 补领：① 收到 hello 后按 `GET /agent/me` 的
   `queue.queued` 一次性补领（契约 §11.3：hello 每次最多推送 50 条，其余由 Agent 用 REST 兜底）；
   ② WS 断开期间兜底轮询。两处都用任务 id 去重，同一任务不会处理两次。
9. **task_update 字段**：除协议示例的 `status/progress/title/error` 外，按契约 §11.2 增量约定
   附带可选 `stage`（契约 §8 六种）、`message`（≤200）与 failed 时的 `error_code`（≤64）；
   非法 stage 会被 Relay 忽略而不是报错。
10. **ack_note 转发**：协议 §2.1 未列该消息，契约 §11.3 已明确 Relay 会转发给 Agent
    （`{"type":"ack_note","task_id"}`），Agent 收到才删 outbox 记录、保留 `notes/`。
11. **服务端关闭码**：契约 §11.1 的 `4001`（被新 Agent 顶掉）/`4002`（心跳超时）/
    `4003`（手机设备被撤销，不涉及 Agent）都会被识别并记录；`4001` 会提示「可能重复启动
    Agent」，并按退避继续重连（不会崩、也不会热循环）。
12. **笔记正文永不回写服务器**：WS 模式只发 `task_note`；REST `/complete` 仅存在于
    `--poll/--once` 兼容路径（旧客户端），新链路不调用。
13. **笔记大小**：单条 `task_note` ≤1.5 MiB；超限时任务置失败并只在本地保留 Markdown
    （不让手机端无限重试一个必然被拒的正文）。
14. **REST 心跳保留**：WS 模式下仍每 30 秒发 `POST /agent/heartbeat`，仅用于兼容旧 REST
    在线状态/`GET /agent/me`；手机 presence 以 DO 实时通道为准。
15. **心跳（v0.3）**：删除 25s 应用层 JSON ping，改用 WebSocket 协议层 ping
    （`ping_interval=25s` / `ping_timeout=20s`，Cloudflare 边缘自动应答、不唤醒 DO）；
    仍保留对服务端 JSON `ping` 回 `pong`、对 `probe` 回 `probe_ack`，旧 Relay 心跳仍可用。
16. **readiness（v0.3）**：收到 `hello` 后立即上报一次 `agent_ready`，readiness 变化时重发；
    `model_not_configured`（provider/model 为空）、`engine_unreachable`（BiliNote `/api/health`
    失败/超时 ≤2s，老版本无该路由时自动回退 `/get_all_providers` 探针）；
    BiliNote 健康探测缓存 30s，服务端 ping/probe 触发的刷新不会每次探测。
17. **ack persisted（v0.3）**：`ack_note.persisted!==false` 才清 outbox（旧 Relay 缺省 true）；
    `persisted:false` 时保留 outbox，按 `30s/2min/10min` 最多重发 3 次 `task_note`（后台任务，
    不阻塞主循环），之后等 `note_request` 或重启/重连补发；手机再次 ack true 会取消重发并清理。

## 已知风险

- Windows 家庭宽带下 Relay 走 WSS 出站长连接，不需要入口映射；但公司代理/Clash TUN 可能
  影响 `connect`/`Upgrade`，必要时给 `httpx`/`websockets` 配代理（后续可加 `HTTPS_PROXY` 支持）。
- WS 断线期间只有 REST `/agent/tasks/next` 兜底能领到 **queued** 任务；正在 running 的任务
  要靠 Relay hello 后重推（实时协议已约定），若 Relay 未实现该行为，重启后的 running 任务
  需等租约过期回收。
- 手机长时间不 ack 时 outbox 会保留正文副本到 `note_retention_days`（默认 7 天），
  磁盘占用与笔记数量成正比；`notes/` 永久保留，需要用户自行清理。
- BiliNote 后端无鉴权、只有本机用；Agent 不把它暴露到公网。
- 自动拉起的后端进程在 Agent 退出后**不杀**（那是用户自己的 BiliNote），下次直接复用。

### Bot 入口（v0.4）风险

- **Telegram**：中国大陆直连通常不可用，需用户自行解决网络；同一 bot 若设置了 Webhook
  或另有一个实例在 `getUpdates`，会返回 409（单消费者）。
- **QQ**：个人主体认证暂不支持公开使用（只能自建群/内部体验号）；`intents 1<<25` 需在开放
  平台申请单聊/群事件权限，未授权时 Identify 失败；被动回复窗口 C2C 60 分钟/群 5 分钟，
  超窗会转 pending 等 `/last`；长笔记受被动回复次数限制会截断并提示。
- **Bot 安全**：任何能给 bot 发消息的人都能用你的电脑生成笔记（消耗你的 LLM Key/算力），
  不要把 bot 公开给陌生人；Bot 凭证只存本机，但 Relay 与 Bot 平台仍能看到链接/状态/笔记。
- **Relay 依赖**：Bot 建任务依赖 `POST /api/v1/agent/devices` 端点（v0.4 新增）；
  Relay 未部署该端点时会启动失败并记录日志（不影响手机 PWA/WS 主链路）。
- **未真实验证**：本机未用真实 Telegram token / QQ AppID 做端到端（按 Lead 要求省钱），
  由用户用桌面 UI 自行验证 getMe / READY / 链接→笔记回传。

## License

MIT。上游 BiliNote 为 MIT，本目录复用了 `wecom_bot`（BiliNote 衍生）的成熟逻辑并重写为
独立模块 + 测试，归属说明见仓库根 `NOTICE`。
