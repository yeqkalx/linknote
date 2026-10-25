# LinkNote Realtime Protocol v1.1（v0.3.0 冻结给 relay / agent / PWA 三方实现）

> 目标：去掉 4s/3s 轮询，改为 Durable Objects (SQLite) + WebSocket Hibernation 推送。
> 本文是三方唯一契约；实现中如需改字段，先改本文并同步 Lead。
>
> **v1.1（v0.3.0）增量**：只在有手机连接时才心跳（DO 成本）；区分“收到”与“保存成功”（`ack_note.persisted`）；
> 新增 readiness（`agent_ready` / `ready` / `issues`）；Agent 心跳改用 WebSocket 协议 ping。
> 旧 0.2.2 Agent/PWA 与旧 Relay 双向兼容。

## 1. 身份与连接

### 1.1 Agent（电脑）
- `GET /api/v1/agent/ws`，HTTP Upgrade，Header `Authorization: Bearer <agent_token>`。
- Relay 用 token 查 D1 `agents`，得到 `agent_id`；**只信任 token 对应的 agent_id**。
- DO 实例：`env.AGENT_ROOM.idFromName(agent_id)`。每个 agent 一个 DO，Agent socket 每台电脑最多 1 条（新连接顶掉旧连接）。

### 1.2 手机（PWA / APK）
- `POST /api/v1/realtime/ticket`，Header `Authorization: Bearer <device_token>`。
- 返回 `{ "ticket": "...", "expires_at": "ISO", "agent_id": "...", "agent_online": true }`，ticket TTL 60s，HMAC 签名，绑定 agent_id；
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
{"type":"task","task":{"id":"...","url":"...","status":"queued","stage":"pending","platform":"bilibili","title":null,"created_at":"..."}}
{"type":"note_request","task_id":"..."}
{"type":"phone_online","online":true}
{"type":"ack_note","task_id":"...","persisted":true}
{"type":"ping","ts":123}
{"type":"probe","ts":123}
```
- Agent 连接成功后：DO 发送 `hello`，然后从 D1 拉该 agent 的 `queued/running` 任务逐条发 `task`。
- 手机打开未缓存笔记时，DO 向 Agent 发 `note_request`。
- 手机连上/断开时发 `phone_online`，Agent 可据此决定是否立即推送新笔记（可选优化）。
- `ack_note.persisted`：缺省 true（兼容旧 Relay）。false 表示“手机收到但没保存成功”，Agent 不应清理 outbox，应择机重发。
- `probe`：手机连上时用于探活 Agent；Agent 必须尽快回 `probe_ack`（3s 内）。

### 2.2 Agent → Server
```json
{"type":"task_update","task_id":"...","status":"running","stage":"downloading","progress":30,"message":"下载中","title":"..."}
{"type":"task_update","task_id":"...","status":"completed","progress":100,"stage":"saving","title":"..."}
{"type":"task_update","task_id":"...","status":"failed","error":"...","error_code":"billing"}
{"type":"task_note","task_id":"...","markdown":"...","title":"..."}
{"type":"agent_ready","ready":false,"issues":[{"code":"model_not_configured","label":"未配置 AI 模型"}]}
{"type":"pong","ts":123}
{"type":"probe_ack","ts":123}
```
- **Agent 不再发送周期性应用层 `ping`**：改用 WebSocket 协议 ping（`websockets` `ping_interval=25, ping_timeout=20`）。
  Cloudflare 运行时自动应答协议 ping，不唤醒 DO、不计请求；`pong` 仅用于回应服务端 `ping`（有手机在线时才会发）。
- `agent_ready`：收到 `hello` 后立即发一次，readiness 变化时重发。`issues[].code` ≤64、`label` ≤200，最多 20 项。
- `task_update`：Relay 校验 task 属于该 agent 后更新 D1 元数据，并推给手机 `task_updated`；`stage`/`message` 为可选增量字段。
- `task_note`：**正文不写 D1/DO SQLite**。DO 立即转发给在线手机；无手机在线则只记 “已收到但未送达” 的 task_id，等手机 `note_request` 再向 Agent 要；Agent 本地 notes/outbox 保留到 `ack_note.persisted != false` 或本地保留策略到期。
- `probe_ack`：可附带 `ready`/`issues`（增量字段），Relay 会更新 readiness。

### 2.3 Server → Phone
```json
{"type":"hello","agent_id":"...","online":true,"ready":true,"issues":[],"server_time":"..."}
{"type":"presence","online":true,"last_seen":"...","ready":false,"issues":[{"code":"engine_unreachable","label":"BiliNote 引擎不可用"}],"server_time":"..."}
{"type":"task_updated","task":{"id":"...","status":"running","stage":"downloading","progress":30,"message":"下载中","title":"..."}}
{"type":"note","task_id":"...","markdown":"...","title":"..."}
{"type":"note_unavailable","task_id":"...","reason":"agent_offline"}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
```
- `presence`：Agent socket 存在即 online；正常 close 立即 false；网络突断由“手机在线期间的 alarm”兜底（最坏约 90s）。
- `ready`：`true` 可生成；`false` 表示在线但有阻塞问题；`null`/缺失表示未知（旧 Agent），UI 按“在线”展示。
- `issues`：阻塞原因列表（`code`/`label`），手机展示“需要处理 N 项”；`ready===true` 时为空。
- `note`：手机写入本地缓存成功后回 `ack_note {persisted:true}`；失败回 `persisted:false`。

### 2.4 Phone → Server
```json
{"type":"note_request","task_id":"..."}
{"type":"ack_note","task_id":"...","persisted":true}
{"type":"ack_note","task_id":"...","persisted":false}
{"type":"ping","ts":123}
{"type":"pong","ts":123}
```
- 打开未缓存笔记：发 `note_request`；若 Agent 离线，收到 `note_unavailable`，UI 提示“电脑离线，稍后重试”。
- `ack_note.persisted` 缺省 true（兼容旧客户端）。
- 手机仍每 25s 发应用层 `ping`（页面打开期间）；**不再 3s 轮询任务列表**。

## 3. 心跳与在线状态（v0.3 成本版）

### 3.1 成本约束（必须遵守）
- **只有存在手机 socket 时才 `setAlarm`**；没有手机：不设 alarm、不主动 ping、不写 `room_meta`。
- `last_seen` 只存内存；`online` / `last_seen_at` / `ready` / `issues` 只在跃迁或变化时合并写 1 条 SQL。
- 手机全部断开时 `deleteAlarm`。
- 手机连接期间：alarm 每 30s 向 agent/phone 发 `ping`；Agent 60s 无任何消息先 `probe`（3s），仍无响应才 close 4002 + offline。
- 手机连接时：若 Agent 上次活动 >15s，立即 `ping` + `probe`，3s 内无响应 → `presence online:false`（不 close，等下一次 alarm/重连修正）。
- 离线发现最坏窗口约 **60–93s**（alarm 30s + stale 60s + probe 3s）；如需更短可调 `WS_ALARM_SECONDS` / `WS_AGENT_STALE_SECONDS`。
- 计算与压测见 [`docs/cost-model.md`](./cost-model.md)。

### 3.2 Hibernation
- 使用 `state.acceptWebSocket(server, tags)`、`webSocketMessage` / `webSocketClose` / `webSocketError`；不要用 `setInterval`。
- 不要依赖 `setTimeout` 跨 hibernation 生效；3s 探活 timer 丢失时由下一次 alarm 重新探活（不允许永久假在线）。
- `setAlarm` 用 `state.storage.setAlarm`；alarm 内按需重排/清除。

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
- DO SQLite：online/last_seen_at/ready/issues（只在跃迁/变化时写）、已收到但未送达的 task_id（不存正文）；具体表由 relay 实现者定。
- **禁止**把 markdown 写进 D1 / DO SQLite / logs；手机收到后本地缓存。
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

- 无手机连接时：Agent 连接/断开之外，不产生 alarm、不主动 ping、不写 `room_meta.last_seen_at`。
- 手机打开 3 分钟：约 6–8 次 alarm/ping；关闭后 alarm 清除。
- 手机连上：alive Agent 3s 内 `online=true` 且 `ready/issues` 正确；kill Agent 后 ≤90s `online=false`。
- 手机缓存失败：`ack_note persisted=false`，Agent 保留 outbox 并重试；成功后清理。
- 旧 0.2.2 Agent/PWA 与新 Relay、新客户端与旧 Relay 均可工作。
- A/B 两套 token 互不可见。
- Wrangler deploy 后 `https://relay.example.com/api/v1/health`、PWA 版本与 WS 握手通过。
