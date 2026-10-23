# LinkNote Relay API v1 — 接口契约

> 本文件是 Relay（Cloudflare Worker）、PC Agent（Python）、移动端（PWA/Capacitor）三方共同遵守的唯一约定。
> 任何一方要改字段，先改这里，再改实现。

- Base URL（本地）：`http://127.0.0.1:8787`
- Base URL（生产示例）：`https://relay.xykong.xyz`
- 所有路径以 `/api/v1` 开头。
- 请求/响应均为 UTF-8 JSON；除特别说明外，`Content-Type: application/json`。
- 时间统一为 ISO 8601 UTC 字符串，例如 `2026-10-24T04:30:00.000Z`。
- ID 统一为 UUID v4 字符串。
- 认证：`Authorization: Bearer <token>`。

## 0. 统一响应包

成功：

```json
{ "ok": true, "data": { } }
```

失败（HTTP 状态码同时表达语义，body 里也有 code）：

```json
{ "ok": false, "error": { "code": "invalid_url", "message": "只支持 B 站/YouTube/抖音/快手链接" } }
```

| HTTP | error.code 示例 | 说明 |
|---|---|---|
| 400 | `bad_request` / `invalid_url` / `unsupported_platform` / `invalid_code` | 参数错误 |
| 401 | `unauthorized` | token 缺失、失效、已撤销 |
| 403 | `origin_not_allowed` / `forbidden` | 浏览器 Origin 不在白名单 |
| 404 | `task_not_found` / `pairing_not_found` / `not_found` | 资源不存在或不属于你 |
| 409 | `pairing_already_claimed` / `invalid_state` / `duplicate` | 状态冲突 |
| 413 | `markdown_too_large` | 笔记超过 1.5 MB |
| 429 | `rate_limited` | 触发限流（MVP 仅预留） |
| 500 | `internal` | 服务端 bug |

错误 message 会直接显示给用户，必须是可以看懂的短句（中文）。

## 1. 认证与 token

- **Agent token**：`agt_` + 43 字符 base64url（32 字节随机数），创建配对时只返回一次。
- **Device token**：`dev_` + 43 字符 base64url，手机领取配对码时只返回一次。
- 服务端只保存 `SHA-256(token)` 的十六进制值，不保存明文。
- token 丢失只能重新配对；不要在日志/URL/崩溃报告里出现。
- 携带无效 token 一律 401；使用别的设备的资源一律 404（不泄露存在性）。

## 2. 健康检查

### `GET /api/v1/health`（公开）

```json
{ "ok": true, "data": { "status": "ok", "version": "0.1.0", "time": "..." } }
```

## 3. 配对

### 3.1 创建配对（Agent 调用，公开但限流）

`POST /api/v1/pairings`

```json
{ "agent_name": "我的电脑", "platform": "windows", "version": "0.1.0" }
```

- `agent_name`：1–40 字符；默认 `我的电脑`。
- `platform`：`windows` | `macos` | `linux`；默认 `windows`。
- `version`：可选，Agent 版本。

返回 `201`：

```json
{
  "ok": true,
  "data": {
    "agent_id": "uuid",
    "pairing_id": "uuid",
    "code": "K7M4-9QXB",
    "expires_at": "2026-10-24T04:40:00.000Z",
    "agent_token": "agt_xxx"
  }
}
```

- 配对码 8 位，字母表 `23456789ABCDEFGHJKLMNPQRSTUVWXYZ`（去掉易混的 0/O/1/I），显示为 `XXXX-XXXX`，比较时忽略大小写和横线。
- 有效期 **10 分钟**，只能被领取一次。
- 服务端只保存 code 的 SHA-256。
- Agent 必须立刻把 `agent_token` 写入本地配置；关闭窗口后就拿不到了。

### 3.2 查询配对状态（Agent）

`GET /api/v1/pairings/{pairing_id}`，Agent 认证。

```json
{
  "ok": true,
  "data": {
    "status": "pending",
    "expires_at": "...",
    "device": null
  }
}
```

- `status`：`pending` | `claimed` | `expired`。
- 已领取时：

```json
{
  "status": "claimed",
  "device": { "id": "uuid", "name": "Pixel 9", "platform": "android", "paired_at": "..." }
}
```

### 3.3 领取配对码（手机，公开但限流）

`POST /api/v1/pairings/claim`

```json
{ "code": "K7M4-9QXB", "device_name": "我的手机", "platform": "android" }
```

返回 `200`：

```json
{
  "ok": true,
  "data": {
    "device_id": "uuid",
    "device_token": "dev_xxx",
    "agent": { "id": "uuid", "name": "我的电脑", "platform": "windows" },
    "server_time": "..."
  }
}
```

错误：`invalid_code`（格式不对）、`pairing_not_found`、`pairing_expired`、`pairing_already_claimed`。

### 3.4 追加配对码（Agent，给第二台手机）

`POST /api/v1/agent/pairings`，Agent 认证，body 可用 `{}`。

返回同 3.1，但没有 `agent_token`（复用当前 Agent）：

```json
{ "pairing_id": "uuid", "code": "K7M4-9QXB", "expires_at": "..." }
```

### 3.5 取消配对（Agent）

`POST /api/v1/pairings/{pairing_id}/cancel`，Agent 认证。返回 `{ "ok": true }`。

## 4. Agent 自身

### 4.1 Agent 信息

`GET /api/v1/agent/me`，Agent 认证。

```json
{
  "ok": true,
  "data": {
    "agent": {
      "id": "uuid", "name": "我的电脑", "platform": "windows",
      "version": "0.1.0", "online": true,
      "last_seen_at": "...", "created_at": "..."
    },
    "devices": [
      { "id": "uuid", "name": "我的手机", "platform": "android", "paired_at": "...", "last_seen_at": "..." }
    ],
    "queue": { "queued": 0, "running": 0 },
    "server_time": "..."
  }
}
```

`online` = `last_seen_at` 在 60 秒内。

### 4.2 心跳

`POST /api/v1/agent/heartbeat`，Agent 认证。

```json
{ "version": "0.1.0", "busy": false }
```

返回：

```json
{ "ok": true, "data": { "server_time": "...", "queued_tasks": 0 } }
```

Agent 每 30 秒调用一次；`busy=true` 表示正在处理任务。

### 4.3 撤销设备

`DELETE /api/v1/agent/devices/{device_id}`，Agent 认证。

设备 token 立即失效（后续 401）。返回 `{ "ok": true }`。

## 5. 手机端

### 5.1 当前设备信息

`GET /api/v1/me`，Device 认证。

```json
{
  "ok": true,
  "data": {
    "device": { "id": "uuid", "name": "我的手机", "platform": "android", "paired_at": "..." },
    "agent": { "id": "uuid", "name": "我的电脑", "online": true, "last_seen_at": "..." },
    "server_time": "..."
  }
}
```

### 5.2 修改设备名

`PATCH /api/v1/me`，Device 认证。

```json
{ "device_name": "新的名字" }
```

返回同 5.1 的 `device`。

### 5.3 解除配对（手机主动）

`DELETE /api/v1/me`，Device 认证。设备 token 立即失效。返回 `{ "ok": true }`。

## 6. 任务

### 6.1 提交任务

`POST /api/v1/tasks`，Device 认证。

```json
{ "url": "https://www.bilibili.com/video/BV...", "client_key": "uuid-or-null" }
```

- `url`：1–2048 字符。可以是纯链接，也可以是分享文案（服务端会提取第一个链接）。缺 `https://` 的已知平台裸链接会自动补全。
- `client_key`：可选，App 生成，用于断网重试幂等；同一设备相同 `client_key` 重复提交返回已有任务。
- 只接受平台：`bilibili`（bilibili.com / b23.tv）、`youtube`（youtube.com / youtu.be）、`douyin`（douyin.com / v.douyin.com）、`kuaishou`（kuaishou.com / v.kuaishou.com）。
- 拒绝：IP 字面量、localhost、非 80/443 端口、非 http(s)、超长、不支持的域名。

返回 `201`，任务摘要：

```json
{
  "ok": true,
  "data": {
    "task": {
      "id": "uuid", "url": "...", "platform": "bilibili",
      "status": "queued", "stage": "pending", "progress": 0,
      "message": "排队中", "title": null,
      "error_code": null, "error_message": null,
      "has_note": false,
      "created_at": "...", "updated_at": "...", "completed_at": null
    }
  }
}
```

### 6.2 任务列表

`GET /api/v1/tasks?limit=20&before=2026-10-24T04:30:00.000Z`，Device 认证。

- `limit`：1–50，默认 20。
- `before`：可选，游标（上一页最后一条的 `created_at`）。
- 只返回当前设备、未归档的任务，按 `created_at DESC`。
- 返回的每一项都是任务摘要（**不含 markdown**）。

```json
{ "ok": true, "data": { "items": [ ... ], "next_before": "..." } }
```

### 6.3 任务详情

`GET /api/v1/tasks/{task_id}`，Device 认证。返回任务摘要 + `agent` 在线状态：

```json
{
  "ok": true,
  "data": {
    "task": { },
    "agent": { "id": "uuid", "name": "我的电脑", "online": true }
  }
}
```

### 6.4 获取笔记 Markdown

`GET /api/v1/tasks/{task_id}/note`，Device 认证。

只有 `status=succeeded` 才有；否则 409 `invalid_state`。

```json
{
  "ok": true,
  "data": {
    "task_id": "uuid",
    "title": "视频标题",
    "markdown": "# ...",
    "completed_at": "..."
  }
}
```

### 6.5 失败重试

`POST /api/v1/tasks/{task_id}/retry`，Device 认证。

仅允许对 `failed` 或 `succeeded` 的任务重试；创建一个新的 queued 任务（新 id），URL 相同。返回新任务摘要。

### 6.6 从列表移除

`DELETE /api/v1/tasks/{task_id}`，Device 认证。

仅允许对终态（`succeeded`/`failed`）任务操作：设置 `archived_at`，列表不再返回，数据保留。返回 `{ "ok": true }`。

## 7. Agent 取任务与回传

### 7.1 领取下一个任务（原子）

`GET /api/v1/agent/tasks/next`，Agent 认证。

- 服务端在一个事务里：先把本 Agent 租约过期的 `running` 任务处理掉（`attempts < 2` → 重新 queued；否则 → failed `agent_lease_expired`），再原子领取最早的一条 `queued` 任务，置为 `running`、`attempts += 1`、`lease_expires_at = now + 30min`。
- 没有任务时返回 `200` + `{ "task": null }`（不要返回 204，客户端处理更简单）。

```json
{
  "ok": true,
  "data": {
    "task": {
      "id": "uuid", "url": "...", "platform": "bilibili",
      "attempts": 1, "created_at": "..."
    }
  }
}
```

- Agent 轮询间隔建议 3–5 秒（空闲时 5 秒，有任务时立即继续）。
- 同一个 Agent 同一时刻只跑一个任务（BiliNote 后端本身是串行执行器）。

### 7.2 进度回传

`POST /api/v1/agent/tasks/{task_id}/progress`，Agent 认证。

```json
{
  "stage": "transcribing",
  "progress": 45,
  "message": "正在语音转写（约 2/5 分钟）"
}
```

- `stage`：`parsing` | `downloading` | `transcribing` | `summarizing` | `formatting` | `saving`。
- `progress`：0–100，可选；不传则保持原值。
- `message`：可选，≤200 字符。
- 只有属于该 Agent 且 `status=running` 的任务可更新，否则 404/409。
- 每次调用都会顺延租约 30 分钟。Agent 在状态变化时立刻回传，平时最多每 10 秒一次。

### 7.3 任务成功

`POST /api/v1/agent/tasks/{task_id}/complete`，Agent 认证。

```json
{
  "title": "视频标题",
  "markdown": "# 标题\n\n...",
  "duration_seconds": 754,
  "meta": { "platform": "bilibili", "video_id": "BV..." }
}
```

- `title`：≤200 字符，必填（空则用 `未命名笔记`）。
- `markdown`：≤1.5 MiB（1,572,864 字节），必填非空。
- `duration_seconds`、`meta` 可选，仅展示/排障用。
- 服务端置 `status=succeeded`、`stage=done`、`progress=100`、`completed_at=now`，清空错误。
- 重复调用：若已经是 succeeded，返回 `{ "ok": true }`（幂等），避免 Agent 网络重试导致失败。

### 7.4 任务失败

`POST /api/v1/agent/tasks/{task_id}/fail`，Agent 认证。

```json
{ "code": "bilinote_error", "message": "BiliNote 生成失败：模型余额不足" }
```

- `message` ≤1000 字符，会展示给手机用户。
- 服务端置 `status=failed`、`stage=failed`、`error_code`、`error_message`、`completed_at=now`。
- 重复调用幂等。

### 7.5 租约续期

`POST /api/v1/agent/tasks/{task_id}/heartbeat`，Agent 认证，body `{}`。

长任务（>30 分钟）每 5 分钟调用一次。返回 `{ "ok": true, "data": { "lease_expires_at": "..." } }`。

### 7.6 查询任务（Agent 自检/补传）

`GET /api/v1/agent/tasks/{task_id}`，Agent 认证。返回 Agent 自己的任务摘要，用于本地 outbox 重试时判断状态。

## 8. 任务状态机

```
queued ──claim──▶ running ──complete──▶ succeeded
                      │
                      ├──fail────────▶ failed
                      │
                      └──lease 过期──▶ queued（attempts<2）或 failed（attempts>=2）

succeeded/failed ──retry──▶ 新任务 queued
```

- `status`：`queued` | `running` | `succeeded` | `failed`
- `stage`：`pending` | `parsing` | `downloading` | `transcribing` | `summarizing` | `formatting` | `saving` | `done` | `failed`
- `progress`：0–100，允许后端为 0 直到 done。

App 文案建议：

| stage | 中文 |
|---|---|
| pending | 排队中 |
| parsing | 正在解析链接 |
| downloading | 正在下载音视频 |
| transcribing | 正在语音转写 |
| summarizing | AI 正在总结 |
| formatting | 正在整理格式 |
| saving | 正在保存 |
| done | 已完成 |
| failed | 失败 |

## 9. CORS

- Worker 读取环境变量 `ALLOWED_ORIGINS`（逗号分隔）；`*` 表示全部允许。
- 默认允许：`http://localhost:5173`、`http://127.0.0.1:8787`、`http://localhost:8787`、`https://relay.xykong.xyz`、`capacitor://localhost`、`https://localhost`。
- 预检 `OPTIONS` 返回 204 + CORS 头；不允许的 Origin 返回 403。
- 原生 App 不发送 Origin，无需 CORS。

## 10. 限制与安全（MVP）

| 项 | 值 |
|---|---|
| 配对码 | 8 位 / 10 分钟 / 单次 |
| 任务 URL | ≤2048 字符，仅白名单平台 |
| Markdown | ≤1.5 MiB（1,572,864 字节） |
| 单 Agent 并发 | 1 个任务 |
| Agent 空闲轮询 | 3–5 秒 |
| App 轮询 | 前台 3 秒，后台停止 |
| 传输内容 | 只有链接、状态、Markdown；视频/音频/LLM Key 不上云 |

- Relay **能看到** Markdown 明文；MVP 不做端到端加密（后续可用 device key 加密 markdown 字段）。
- 不提供删除账号接口；MVP 用撤销 token + 归档任务。
- 限流：配对创建/领取的 IP 限流为预留项，MVP 依赖 8 位码 + 10 分钟 TTL；上线公网前必须补上。
- Agent 只把白名单平台链接交给本机 BiliNote；BiliNote 自身还有 URL 校验，双保险。
