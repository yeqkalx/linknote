# LinkNote PC Agent（Windows）

手机分享视频链接 → 云端 Relay → **这台电脑上的 BiliNote** 生成 AI 笔记 → Markdown 回到手机。

本目录是常驻在 Windows 上的 PC Agent：轮询 Relay 领取任务，调用本机 BiliNote 后端
（`http://127.0.0.1:8483/api`）生成笔记并回传进度/结果。视频、音频、Whisper 模型、
LLM API Key **都不离开这台电脑**，云端只中转链接、状态和 Markdown。

- 接口契约：[../docs/api-contract.md](../docs/api-contract.md)（唯一约定，本实现严格遵守）
- 架构说明：[../docs/architecture.md](../docs/architecture.md)

```
Relay（HTTPS 轮询 + Bearer agt_...）
   │  GET /agent/tasks/next     POST progress / complete / fail
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

只有两个运行时依赖：`httpx`；`dev` 额外装 `pytest` / `pytest-asyncio`。

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

启动后：

- 每 30 秒向 Relay 发一次心跳（手机端据此显示「电脑在线」）；
- 每 3–5 秒 `GET /api/v1/agent/tasks/next` 领任务，同一时刻只跑一个（BiliNote 后端本身串行）；
- 领到任务后自动检查 BiliNote 后端，没启动就拉起 `BiliNoteBackend.exe`
  （隐藏窗口 + 日志重定向到 `~/.linknote/logs/bilinote_backend.log`）；
- 提交 `/generate_note`，每 3 秒轮询 `/task_status/{id}`，状态变化立刻回传 progress；
- 成功后取 `result.markdown` + `result.audio_meta.title` 回传 complete；失败把常见错误
  翻译成中文回传 fail；
- Relay 暂时不可达时，已生成的结果先落本地 outbox，下一轮循环/下次启动自动补传。

调试用：

```powershell
.venv\Scripts\python.exe -m linknote_agent run --once --verbose   # 只处理一轮
.venv\Scripts\python.exe -m linknote_agent status               # 查看配对/Relay/后端状态
.venv\Scripts\python.exe -m linknote_agent status --json        # 机器可读
```

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
| `run [--once] [--relay URL]` | 常驻轮询任务（`--once` 只处理一轮，调试用） |
| `status [--json]` | 查看配对、Relay 在线/设备、BiliNote 后端、outbox 待补传数量 |
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
| `poll_interval_seconds` | `4.0` | 空闲时领任务间隔（契约建议 3–5 秒） |
| `task_status_poll_interval_seconds` | `3.0` | 轮询 BiliNote 状态间隔 |
| `progress_min_interval_seconds` | `8.0` | 同状态进度回传最小间隔（契约 ≤10 秒） |
| `heartbeat_interval_seconds` | `30.0` | Agent 心跳间隔（契约 30 秒） |
| `lease_heartbeat_interval_seconds` | `300.0` | 长任务租约续期间隔（契约 5 分钟） |
| `task_timeout_seconds` | `3600.0` | 单任务最长等待，超时判失败 |
| `max_status_errors` | `20` | 连续查询 BiliNote 状态失败多少次判定后端失联 |
| `outbox_dir` / `notes_dir` / `log_dir` | `""`（用 `~/.linknote/` 下默认子目录） | 可自定义 |
| `verbose` | `false` | 是否输出 DEBUG 日志 |

## 本地数据目录（`~/.linknote/`）

| 路径 | 内容 |
|---|---|
| `agent.json` | 配置 + agent_token（原子写；POSIX 上权限 0600，Windows 上 token 仅存本机） |
| `logs/agent.log` | Agent 日志（5 MB 滚动 ×3） |
| `logs/bilinote_backend.log` | 自动拉起的 BiliNote 后端 stdout/stderr |
| `outbox/*.json` | Relay 暂时不可达时暂存的结果，恢复后自动补传 |
| `notes/<task_id>.md` | 补传失败/笔记超 1.5 MB 时本地留档的 Markdown |

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
| `SUCCESS` | —（complete） | 100 | `POST /complete` 回传标题 + Markdown |
| `FAILED` | —（fail） | — | `POST /fail` 回传中文原因 |

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
2. **手机端显示电脑离线**：`status` 看 Relay 是否可达；心跳失败日志在 `agent.log`。
   代理/VPN 可能拦截 `connect`，给 `httpx` 配代理见「已知风险」。
3. **`401` 配对失效**：Relay 侧 token 被撤销/重配过。重新 `pair`（新 token 覆盖旧配置）。
4. **BiliNote 后端拉不起来**：
   - 确认 `backend_exe` 指向真实文件；
   - 看 `~/.linknote/logs/bilinote_backend.log`（通常是 BiliNote 自身配置/端口占用）；
   - 也可以手动先打开 BiliNote，再 `run`（`auto_start_backend=false` 时只探测不拉起）。
5. **转写/模型报错**：Agent 只是转发 BiliNote 的错误，按手机端提示去 BiliNote 设置里修。
6. **outbox 里有堆积**：Relay 长时间不可达导致；恢复网络后 `run` 会自动补传，
   `status` 里能看到待补传数量。补传被 Relay 拒绝（任务已被回收）时笔记会存到
   `~/.linknote/notes/<task_id>.md`，不会丢。
7. **开机自启后「没反应」**：pythonw 没有窗口是正常的；看
   `%USERPROFILE%\.linknote\logs\agent.log` 和 `status.cmd` 的输出。
8. **端口/地址**：BiliNote 默认 8483；Relay 本地 wrangler 默认 8787。Agent 不监听任何端口。

## 测试

```powershell
.venv\Scripts\python.exe -m pytest -q
```

测试**不依赖真实 Relay / BiliNote**：用 `httpx.MockTransport` 在进程内模拟两端
（见 `tests/conftest.py` 的 `FakeRelay` / `FakeBiliNote`），覆盖：

- 配置读写/原子写/环境变量/损坏容错；
- 配对流程（创建、保存 token、打印配对码、claimed / expired / 超时 / Relay 断开）；
- 任务生命周期（领任务 → 启动后端 → 提交 → 进度 → complete），进度映射与 10 秒节流；
- 失败路径与中文错误翻译、租约/Agent 心跳、outbox 落盘与重启补传、本地留档；
- BiliNote 请求体字段（与 `wecom_bot` 完全一致）、ResponseWrapper 解包；
- CLI（`--help`、未配对拦截、`status --json`、`unpair`、`install-autostart` 命令拼装、
  `run --once` 端到端）。

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

## 已知风险

- Windows 家庭宽带下 Relay 走 HTTPS 出站轮询，不需要入口映射；但公司代理/Clash TUN 可能
  影响 `connect`，必要时给 `httpx` 配代理（后续可加 `HTTPS_PROXY` 支持）。
- BiliNote 后端无鉴权、只有本机用；Agent 不把它暴露到公网。
- 自动拉起的后端进程在 Agent 退出后**不杀**（那是用户自己的 BiliNote），下次直接复用。

## License

MIT。上游 BiliNote 为 MIT，本目录复用了 `wecom_bot`（BiliNote 衍生）的成熟逻辑并重写为
独立模块 + 测试，归属说明见仓库根 `NOTICE`。
