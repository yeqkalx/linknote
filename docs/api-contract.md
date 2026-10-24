# LinkNote Relay API v1 — 接口契约

> 本文件是 Relay（Cloudflare Worker）、PC Agent（Python）、移动端（PWA/Capacitor）三方共同遵守的唯一约定。
> 任何一方要改字段，先改这里，再改实现。
>
> **v0.2.0（2026-10）实时架构**：Relay 新增 Durable Objects（SQLite 后端）+ WebSocket Hibernation 推送，
> 用于替换 3–5 秒轮询。WS 消息格式以 [`docs/realtime-protocol.md`](./realtime-protocol.md) 为准，
> 本文第 11 节列出 Relay 的实现补充（均为增量字段/消息，不破坏已冻结字段）。
> REST 全部保留兼容；**笔记正文不再保存到 D1**，改用 WebSocket 转发 + 手机本地缓存。

- Base URL（本地）：`http://127.0.0.1:8787`（本仓库 `npm run dev` 起的实例带本地 dev secret）
- Base URL（生产示例）：`https://relay.xykong.xyz`
- WS Base（本地）：`ws://127.0.0.1:8787/api/v1`；生产：`wss://relay.xykong.xyz/api/v1`
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
| 401 | `unauthorized` / `ticket_invalid` | token 缺失/失效/已撤销；WS ticket 无效或过期（仅 WS 握手） |
| 403 | `origin_not_allowed` / `forbidden` | 浏览器 Origin 不在白名单 |
| 404 | `task_not_found` / `pairing_not_found` / `not_found` | 资源不存在或不属于你 |
| 409 | `pairing_already_claimed` / `invalid_state` / `duplicate` | 状态冲突 |
| 410 | `note_not_stored` | 笔记正文不再由 Relay 保存（见 6.4） |
| 413 | `markdown_too_large` | 笔记超过 1.5 MB |
| 426 | `upgrade_required` | 需要 WebSocket 升级（对 WS 路径发普通 GET） |
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

`online`：该 agent 是否有一条活着的实时（WebSocket）连接；以 Durable Object 的实时状态为准，
DO 不可用或该 agent 从未用过实时通道时退回 `last_seen_at` 60 秒判定（兼容旧 REST 轮询 Agent）。
手机端实时 presence 以 WS `presence` 推送为准（见第 11 节）。

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
如果该设备有一条实时 WebSocket，Relay 会立刻把它踢下线。

### 5.4 实时连接 ticket（手机）

`POST /api/v1/realtime/ticket`，Device 认证（`Authorization: Bearer dev_...`）。

```json
{
  "ok": true,
  "data": {
    "ticket": "<base64url(payload)>.<base64url(HMAC-SHA256)>",
    "expires_at": "2026-10-24T04:31:00.000Z",
    "agent_id": "uuid",
    "agent_online": true,
    "server_time": "..."
  }
}
```

- ticket TTL **60 秒**，HMAC 签名，绑定 `agent_id` + `device_id`，只能用于 WS 握手；
  长期 device token 绝不出现在 URL 里。
- `agent_online` 为增量字段（便于首屏先画在线态，最终以 WS `presence` 为准）。
- 拿到 ticket 后连 `wss://<relay>/api/v1/ws?ticket=<ticket>`。
- ticket 过期/非法时 WS 握手返回 `401 ticket_invalid`（浏览器只能看到 `close=1006`，
  手机端应重新取 ticket 重试；`POST /realtime/ticket` 返回 401 才代表 device token 失效）。

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

- `task.has_note`：**任务是否已产出笔记**（等价于 `status === "succeeded"`）。
  注意：正文不在服务端，值为 `true` 只代表任务成功，不代表能用 REST 取到正文。
- `agent.online` 以 Durable Object 实时状态为准（Agent WS 断开秒级变 false）。

### 6.4 获取笔记 Markdown（**已废弃**）

`GET /api/v1/tasks/{task_id}/note`，Device 认证。

**返回 `410 note_not_stored`**：

```json
{
  "ok": false,
  "error": {
    "code": "note_not_stored",
    "message": "笔记正文不再保存在服务器，请通过实时通道获取（手机端会把笔记缓存在本地）"
  }
}
```

- v0.2.0 起笔记正文只经 WebSocket 转发（见第 11 节），不落 D1 / Durable Object SQLite。
- 手机端收到 WS `note` 后写入本地缓存（IndexedDB 优先），阅读器从缓存渲染；
  缓存缺失时发 `note_request`，由 PC Agent 从本地 outbox 重发。
- 老 REST Agent 仍可用 `POST /agent/tasks/{id}/complete` 带 markdown，Relay 会把正文
  **best-effort 转发给在线的同 agent 手机**（不保存），但手机不在线时无法补发。

### 6.5 失败重试

`POST /api/v1/tasks/{task_id}/retry`，Device 认证。

仅允许对 `failed` 或 `succeeded` 的任务重试；创建一个新的 queued 任务（新 id），URL 相同。返回新任务摘要。

### 6.6 从列表移除

`DELETE /api/v1/tasks/{task_id}`，Device 认证。

仅允许对终态（`succeeded`/`failed`）任务操作：设置 `archived_at`，列表不再返回，数据保留。返回 `{ "ok": true }`。

## 7. Agent 取任务与回传

### 7.1 领取下一个任务（原子，**实时通道的 REST 兜底**）

`GET /api/v1/agent/tasks/next`，Agent 认证。

- 实时架构下 Agent 优先用 WebSocket：连接后 DO 会把该 agent 的 `queued`/`running` 任务逐条推送
  （`{"type":"task", ...}`），Agent 收到后直接处理并用 `task_update status=running` 完成“领取”语义
  （Relay 会把 D1 从 queued → running，`attempts+1`、写 30 分钟租约）。
- **WS 在线时不要同时轮询本接口**，否则同一条 queued 可能被推送和 REST 各领取一次。
  仅当 WS 不可用时用本接口兜底；返回结构不变。
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

### 7.3 任务成功（**兼容入口，正文不落库**）

`POST /api/v1/agent/tasks/{task_id}/complete`，Agent 认证。

```json
{
  "title": "视频标题",
  "markdown": "# 标题\n\n...",
  "duration_seconds": 754,
  "meta": { "platform": "bilibili", "video_id": "BV..." }
}
```

- `title`：≤200 字符（空则用 `未命名笔记`）。
- `markdown`：可选，≤1.5 MiB（1,572,864 字节）。**v0.2.0 起不再写 D1**；
  仅当有同 agent 手机在线时经 Durable Object 转发一条 WS `note` 给手机（best-effort 兼容老脚本），
  手机不在线则正文丢弃（正式链路必须用 WS `task_note`，Agent 本地 outbox 可重发）。
- 服务端置 `status=succeeded`、`stage=done`、`progress=100`、`completed_at=now`，清空错误。
- 响应增加 `note_forwarded`（boolean，增量字段）：是否已转发给在线手机。
- 重复调用：若已经是 succeeded，返回 `{ "ok": true, "note_forwarded": false }`（幂等）。

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

`GET /api/v1/agent/tasks/{task_id}`，Agent 认证。返回 Agent 自己的任务摘要，用于本地 outbox 重试时判断状态。摘要不含 `markdown`。

### 7.7 Agent 实时连接

`GET /api/v1/agent/ws`，HTTP Upgrade，Header `Authorization: Bearer <agent_token>`。

- Relay 用 token 查 D1 得到 `agent_id`，只信任 token 对应的 agent_id，然后连到该 agent 的 Durable Object。
- 每台电脑同一时刻最多 1 条 Agent socket；新连接会顶掉旧连接（旧连接若收到关闭帧，code=4001）。
- 连接成功后先收 `hello`，随后 DO 从 D1 拉 `queued`/`running` 任务逐条推送（最多 50 条，其余可由 REST 兜底领取）。
- 消息契约见第 11 节。

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
- 实时通道里 Agent 用 `task_update` 的 `status` 是 `running` | `completed` | `failed`（见 11.2）；
  Relay 映射到 D1：`completed → succeeded`、`failed → failed`、`running → running`（幂等，不重复加 attempts）。
- 手机端实时更新以 WS `task_updated` 为准，WS 断线时用 REST 快照兜底。

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

## 9. CORS 与 WS Origin

- Worker 读取环境变量 `ALLOWED_ORIGINS`（逗号分隔）；`*` 表示全部允许。
- 默认允许：`http://localhost:5173`、`http://127.0.0.1:8787`、`http://127.0.0.1:8798`、`http://localhost:8787`、`http://localhost:8798`、`https://relay.xykong.xyz`、`capacitor://localhost`、`https://localhost`。
- 预检 `OPTIONS` 返回 204 + CORS 头；不允许的 Origin 返回 403。
- 原生 App / Python Agent 不发送 Origin，无需 CORS。
- WebSocket 握手同样校验 Origin：浏览器/WebView 带了不在白名单的 Origin 会被 403 拒绝；Node/Python 客户端不带 Origin。

## 10. 限制与安全（MVP）

| 项 | 值 |
|---|---|
| 配对码 | 8 位 / 10 分钟 / 单次 |
| 任务 URL | ≤2048 字符，仅白名单平台 |
| Markdown | ≤1.5 MiB（1,572,864 字节），仅 WS 转发，不落库 |
| 单 Agent 并发 | 1 个任务 |
| Agent 任务来源 | 优先 WS 推送（重连 hello 后补发 queued/running）；REST `/agent/tasks/next` 仅兜底 |
| App 任务更新 | WS `task_updated` 推送；断线时 REST 快照（不再 3 秒轮询） |
| WS ticket | TTL 60 秒，HMAC 绑定 agent_id + device_id |
| 心跳 | 两端每 25s `ping`/`pong`；DO alarm 每 30s 检查；Agent >60s 判离线、手机 >90s 断开 |
| 在线状态 | Agent socket 正常关闭秒级 offline；网络突断由心跳兜底（≤60s） |
| 传输内容 | 只有链接、状态、Markdown；视频/音频/LLM Key 不上云 |

- **笔记正文不落 D1 / Durable Object SQLite / 日志**：只在 Relay 内存中转发给在线手机，
  手机收到后写入本地缓存；Agent 本地 outbox 保留正文直到收到 `ack_note` 或按本地策略清理。
- Relay 在转发瞬间能看到 Markdown 明文；MVP 不做端到端加密（后续可加 device key）。
- 不提供删除账号接口；MVP 用撤销 token + 归档任务。
- 限流：配对创建/领取的 IP 限流为预留项，MVP 依赖 8 位码 + 10 分钟 TTL；上线公网前必须补上。
- Agent 只把白名单平台链接交给本机 BiliNote；BiliNote 自身还有 URL 校验，双保险。

## 11. 实时通道（WebSocket）实现补充

> 基础契约（连接方式、消息类型、心跳、断线重连）见 [`docs/realtime-protocol.md`](./realtime-protocol.md)。
> 本节只列 Relay 的实现细节与**增量字段/消息**（不改变已冻结字段语义）。

### 11.1 连接

| 角色 | URL | 鉴权 | 说明 |
|---|---|---|---|
| Agent（电脑） | `wss://<relay>/api/v1/agent/ws` | Header `Authorization: Bearer agt_...` | 每 agent 最多 1 条；新顶旧 |
| 手机（PWA/APK） | `wss://<relay>/api/v1/ws?ticket=<ticket>` | `POST /api/v1/realtime/ticket` 换取 | ticket TTL 60s，URL 不放 device token |

- 连接成功后顺序：Agent 收 `hello` → 补发 `task`（queued/running）；手机收 `hello` → `presence`。
- 服务端时间字段 `server_time`（ISO）。
- WS 路径收到非 Upgrade 的普通 GET 返回 `426 upgrade_required`。
- 手机 ticket 失效用握手 401；Agent token 失效用握手 401。浏览器拿不到握手状态码，`onclose` 会看到 `1006`。
- 服务端主动关闭码：

| code | 场景 |
|---|---|
| 4001 | Agent：同 agent_id 有新连接，旧连接被顶掉 |
| 4002 | 心跳超时（Agent 或手机） |
| 4003 | 设备已被 Agent 撤销（手机） |

### 11.2 Agent → Server（Relay 处理）

```json
{"type":"task_update","task_id":"...","status":"running","progress":30,"stage":"transcribing","message":"正在转写中","title":"视频标题"}
{"type":"task_update","task_id":"...","status":"completed","progress":100,"title":"视频标题"}
{"type":"task_update","task_id":"...","status":"failed","error":"模型余额不足","error_code":"billing"}
{"type":"task_note","task_id":"...","markdown":"# ...","title":"视频标题"}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
```

- `task_update` 的 `status` 取值 `running` / `completed` / `failed`（也容忍 `succeeded`）；
  Relay 校验该 task 属于本 agent（D1 `WHERE agent_id=?`）后更新 D1，并向手机广播 `task_updated`。
- **增量字段**：`stage`（契约第 8 节枚举，非法值忽略）、`message`（≤200）、`error_code`（≤64）。
  Relay 会把这些字段一并写入 D1 并包含在 `task_updated` 里；PWA 用它展示阶段时间线。
- `running` 同时承担“领取任务”语义：D1 `queued → running`、`attempts+1`、写 30 分钟租约；重复 running 幂等。
- `task_note`：**正文绝不写 D1 / DO SQLite**。有手机在线→广播 `note`；无手机在线→DO 只记录
  “该 task_id 的正文已收到但未送达”（不存正文），等手机 `note_request` 时再向 Agent 要。
  重复/乱序 `task_note` 幂等，不报错、不断连。若该 task 不属于本 agent 才回 `{"type":"error","code":"task_not_found"}`。
- 任何消息都刷新 Agent `last_seen`；Agent 每 25s 发 `ping`，Relay 回 `pong`。

### 11.3 Server → Agent（Relay → Agent）

```json
{"type":"hello","agent_id":"...","server_time":"..."}
{"type":"task","task":{"id":"...","url":"...","platform":"bilibili","status":"queued","stage":"pending","progress":0,"title":null,"created_at":"..."}}
{"type":"note_request","task_id":"..."}
{"type":"ack_note","task_id":"..."}
{"type":"phone_online","online":true}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
{"type":"error","code":"task_not_found","message":"任务不存在"}
```

- **增量**：
  - `ack_note`：手机的 `ack_note` 会转发给 Agent，Agent 收到后可清理本地 outbox（保留本机 notes）。
  - `task.task` 含 `stage`/`progress`，便于 Agent 恢复“正在处理”的任务状态（可选使用）。
  - `error`：排障用增量消息（不影响连接），当前可能出现 `bad_request` / `task_not_found` / `invalid_state` / `note_too_large`。
- Agent 离线时任务保留 D1 `queued`，重连 hello 后补发（最多 50 条/次，其余用 REST 兜底）。
- 手机连接/断开时 Agent 收到 `phone_online`（可选优化）。

### 11.4 Server → Phone（Relay → 手机）

```json
{"type":"hello","agent_id":"...","online":true,"server_time":"..."}
{"type":"presence","online":true,"last_seen":"...","server_time":"..."}
{"type":"task_updated","task":{"id":"...","status":"running","stage":"transcribing","progress":30,"message":"正在转写","title":"...","error_code":null,"error_message":null,"has_note":false,"created_at":"...","updated_at":"...","completed_at":null},"server_time":"..."}
{"type":"note","task_id":"...","markdown":"# ...","title":"...","server_time":"..."}
{"type":"note_unavailable","task_id":"...","reason":"agent_offline"}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
{"type":"error","code":"task_not_found","message":"任务不存在"}
```

- `presence`：连接建立/Agent 上线即 true；Agent 正常 close 秒级 false；网络突断 ≤60s false。
  手机自己断开不影响该值。`last_seen` 是 Agent 最后一次活动时间。
- `task_updated.task` 是完整任务摘要（同 REST `GET /tasks/{id}.task`，含 `stage`/`message`/`has_note`/`updated_at` 等增量字段）。
  手机收到后直接更新列表/详情，不轮询。
- `note`：正文只在此消息/手机本地缓存中出现，手机应写入 IndexedDB（localStorage 兜底）后回 `ack_note`。
- `note_request` 时 Agent 离线 → `note_unavailable`（UI 提示“电脑离线，稍后重试”）。
- `ack_note` / `note_request` 之外的消息类型服务端忽略；手机每 25s 发 `ping`。

### 11.5 手机 → Server（Relay 处理）

```json
{"type":"note_request","task_id":"..."}
{"type":"ack_note","task_id":"..."}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
```

- `note_request`：Relay 校验 task 属于该 ticket 的 agent_id；Agent 在线→转发 `note_request`，离线→回 `note_unavailable`。
- `ack_note`：Relay 删除“未送达”标记并转发 `ack_note` 给 Agent。
- 手机消息会刷新该 socket 的 last_seen；>90s 无消息由 DO alarm 断开。

### 11.6 多租户隔离（硬约束）

- Durable Object 实例按 `idFromName(agent_id)` 划分：A 的 agent/phone socket 与 B 完全隔离。
- 所有 D1 查询强制 `WHERE agent_id = ?`；`agent_id` 只从 Agent token / 手机 ticket 推导，
  客户端消息里的任何 agent_id 字段都不被采用。
- 测试覆盖：A 的 token 读不到 B 的 tasks；B 的任务/笔记/presence 永不发给 A 的 socket。

### 11.7 本地联调

```powershell
cd relay
npm install
npm run db:local                    # 本地 D1 migrations（默认 .wrangler/state）
npm run dev                         # http://127.0.0.1:8787，已带 dev INTERNAL_SECRET/TICKET_SECRET
npm test                            # 46 项 REST + WS 契约/实时测试（独立 8799 端口与 state）
```

- Node 客户端示例（Agent）：

```js
import WebSocket from 'ws';
const ws = new WebSocket('ws://127.0.0.1:8787/api/v1/agent/ws', {
  headers: { authorization: `Bearer ${agentToken}` },
});
```

- 手机/PWA：先 `POST /api/v1/realtime/ticket`，再连 `ws://127.0.0.1:8787/api/v1/ws?ticket=<ticket>`。
- `npm run dev:realtime` 是 8798 端口的等价脚本（供多人联调时避开 8787）。
