# LinkNote Realtime Protocol v1（冻结给 relay / agent / PWA 三方实现）

> 目标：去掉 4s/3s 轮询，改为 Durable Objects (SQLite) + WebSocket Hibernation 推送。
> 本文是三方唯一契约；实现中如需改字段，先改本文并同步 Lead。

## 1. 身份与连接

### 1.1 Agent（电脑）
- `GET /api/v1/agent/ws`，HTTP Upgrade，Header `Authorization: Bearer <agent_token>`。
- Relay 用 token 查 D1 `agents`，得到 `agent_id`；**只信任 token 对应的 agent_id**。
- DO 实例：`env.AGENT_ROOM.idFromName(agent_id)`。每个 agent 一个 DO，Agent socket 每台电脑最多 1 条（新连接顶掉旧连接）。

### 1.2 手机（PWA / APK）
- `POST /api/v1/realtime/ticket`，Header `Authorization: Bearer <device_token>`。
- 返回 `{ "ticket": "...", "expires_at": "ISO", "agent_id": "..." }`，ticket TTL 60s，HMAC 签名，绑定 agent_id；
- 手机连 `GET /api/v1/ws?ticket=<ticket>`（Upgrade）；Relay 验签后连到同一 `agent_id` DO。
- 浏览器 WebSocket 不能带 Authorization 头，所以手机用 ticket；Agent 用 Header。

### 1.3 多租户硬约束
- 任何 DO / D1 查询必须 `WHERE agent_id = ?`；phone/agent 事件只能发往同 agent_id 的 socket。
- 客户端不能传 `agent_id` 决定订阅目标；只能从 token/ticket 推导。
- 测试必须覆盖：A 的 token 读不到 B 的 tasks/notes/presence。

## 2. WebSocket 消息

统一 JSON：`{ "type": "...", ... }`。服务端时间 `server_time` ISO。

### 2.1 Server → Agent
```json
{"type":"hello","agent_id":"...","server_time":"..."}
{"type":"task","task":{"id":"...","url":"...","status":"queued","stage":"queued","platform":"bilibili","title":null,"created_at":"..."}}
{"type":"note_request","task_id":"..."}
{"type":"phone_online","online":true}
{"type":"ack_note","task_id":"..."}
{"type":"ping","ts":123}
```
- Agent 连接成功后：DO 发送 `hello`，然后从 D1 拉该 agent 的 `queued/running` 任务逐条发 `task`。
- 手机打开未缓存笔记时，DO 向 Agent 发 `note_request`。
- 手机连上/断开时发 `phone_online`，Agent 可据此决定是否立即推送新笔记（可选优化）。

### 2.2 Agent → Server
```json
{"type":"task_update","task_id":"...","status":"running","stage":"downloading","progress":30,"message":"下载中","title":"..."}
{"type":"task_update","task_id":"...","status":"completed","progress":100,"stage":"saving","title":"..."}
{"type":"task_update","task_id":"...","status":"failed","error":"..."}
{"type":"task_note","task_id":"...","markdown":"...","title":"..."}
{"type":"pong","ts":123}
{"type":"ping","ts":123}
```
- `task_update`：Relay 校验 task 属于该 agent 后更新 D1 元数据，并推给手机 `task_updated`；`stage`/`message` 为可选增量字段（PWA 时间线用）。
- `ack_note`：手机收到/缓存正文后发 `ack_note`；Relay 转发给 Agent，Agent 据此清理 outbox（本机 notes 保留）。
- `task_note`：**正文不写 D1**。DO 立即转发给在线手机（`note`）；若没有手机在线，DO 记录“已收到但未送达”的内存/DO 状态并保留 task 元数据为 completed；手机之后 `note_request` 时向 Agent 转发请求，Agent 从本地 outbox/notes 重发。
- Agent 本地 outbox 保留正文，直到收到手机 `ack_note` 或超过本地保留策略。

### 2.3 Server → Phone
```json
{"type":"hello","agent_id":"...","online":true,"server_time":"..."}
{"type":"presence","online":true,"last_seen":"..."}
{"type":"presence","online":false,"last_seen":"..."}
{"type":"task_updated","task":{"id":"...","status":"running","stage":"downloading","progress":30,"message":"下载中","title":"...","error":null}}
{"type":"note","task_id":"...","markdown":"...","title":"..."}
{"type":"note_unavailable","task_id":"...","reason":"agent_offline"}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
```
- `presence` 用于手机顶部“电脑在线/离线”；连接建立即 true，Agent socket close 立即 false；网络突断由心跳兜底（≤60s）。
- `note` 由手机写入本地缓存（IndexedDB/localStorage），随后发 `ack_note` 给 Relay。

### 2.4 Phone → Server
```json
{"type":"note_request","task_id":"..."}
{"type":"ack_note","task_id":"..."}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
```
- 打开未缓存笔记：发 `note_request`；若 Agent 离线，收到 `note_unavailable`，UI 提示“电脑离线，稍后重试”。
- **不再 3s 轮询任务列表**；只在 WS 断线时用 REST 快照兜底。

## 3. 心跳与在线状态

- 两端每 25s 发 `{"type":"ping","ts":...}`，对端回 `pong`。
- DO 记录每个角色 `last_seen`；每 30s 的 alarm 检查：
  - Agent last_seen > 60s → 关闭 agent socket，广播 `presence:false`；
  - Phone last_seen > 90s → 关闭 phone socket（不广播 offline）。
- 正常 `close` / `error`：
  - Agent 断开 → 立即广播 `presence:false`；
  - Phone 断开 → 不改变电脑在线状态。
- Hibernation：使用 `state.acceptWebSocket(server, tags)`、`webSocketMessage` / `webSocketClose` / `webSocketError`；不要用 `setInterval`；alarm 用 `state.storage.setAlarm`。
- 心跳不是计费重点，但避免每 5s 发；25–30s 即可。

## 4. DO SQLite 与 D1

- `wrangler.toml`：
```toml
[durable_objects]
bindings = [{ name = "AGENT_ROOM", class_name = "AgentRoom" }]

[[migrations]]
tag = "v1"
new_sqlite_classes = ["AgentRoom"]
```
- D1：只存任务元数据、agent/device/pairing；**tasks 表不再写入 markdown**。
- DO SQLite：在线状态、最近 last_seen、已收到但未送达的 task_id（不存正文）、可选待处理任务缓存；具体表由 relay 实现者定。
- **禁止**把 markdown 写进 D1 / DO SQLite / logs；手机收到后本地缓存。
- 任务元数据字段沿用现有 tasks（去掉 markdown/note 正文）；`GET /api/v1/tasks/:id` 仍返回元数据，不返回正文。
- 兼容：旧客户端用 REST 轮询仍可用；新客户端优先 WS。

## 5. 断线重连

- Agent：`1s, 2s, 4s, 8s, 15s, 30s` + jitter，上限 30s；重连后重新 `hello`/拉 pending；网络恢复立即重连。
- PWA：`1s, 2s, 4s, 8s, 15s`；断线显示“重连中”，同时用 REST 快照避免白屏；重连成功后重新订阅。
- ticket 过期：PWA 自动重新 `POST /realtime/ticket` 再连；401/403 时清 token 回配对页。

## 6. Worker 内部通知

- REST 创建任务 / 更新任务后，Worker 通过 `env.AGENT_ROOM.idFromName(agent_id).fetch()` 发内部事件（带 `X-Internal-Secret`），DO 再广播。
- 内部 secret 用 `wrangler.toml` 的 `INTERNAL_SECRET` var；生产值不要写死源码。
- 定时/心跳由 DO alarm，不额外 Cron。

## 7. 验收

- `npx wrangler dev` 下：Agent WS 连上 → PWA ticket 连上 → online true；手机创建任务 → Agent 秒收；Agent 发 task_update → 手机秒收；Agent 发 task_note → 手机收到且 D1 无 markdown；手机 ack。
- 关闭 Agent → 手机 ≤ 2s 收到 offline；kill -9 Agent → ≤60s 收到 offline。
- A/B 两套 token 互不可见。
- Wrangler deploy 后 `https://relay.xykong.xyz/api/v1/health` 与 WS 握手通过。

