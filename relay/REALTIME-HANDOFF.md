# LinkNote Relay 实时通道交接（agent-dev / app-dev）

> 状态（2026-10-25）：Relay **v0.5.0** —— 自托管模板，**作者不再提供公共 relay/域名**。
> 每个用户在自己的 Cloudflare 账号部署（见 `docs/self-hosting.md` + `relay/wrangler.toml` 顶部注释）。
> 本地契约/实时测试 **53/53**（v0.3 见第 7 节、v0.4 Bot 设备签发见第 8 节、v0.5 见第 9 节）。
> Durable Objects（SQLite 后端）+ WebSocket Hibernation + REST 兼容。
> 协议基础以 `docs/realtime-protocol.md` / `docs/v0.3-protocol-delta.md` / `docs/bot-channels.md` 为准，
> 接口细节与增量字段见 `docs/api-contract.md`。

## 1. URL

| 环境 | REST Base | Agent WS | 手机 ticket | 手机 WS |
|---|---|---|---|---|
| 本地（默认） | `http://127.0.0.1:8787/api/v1` | `ws://127.0.0.1:8787/api/v1/agent/ws` | `POST /api/v1/realtime/ticket` | `ws://127.0.0.1:8787/api/v1/ws?ticket=...` |
| 本地（多人联调用） | `http://127.0.0.1:8798/api/v1` | `ws://127.0.0.1:8798/api/v1/agent/ws` | 同上 | `ws://127.0.0.1:8798/api/v1/ws?ticket=...` |
| 自托管示例 | `https://your-relay.example.com/api/v1` | `wss://your-relay.example.com/api/v1/agent/ws` | 同上 | `wss://your-relay.example.com/api/v1/ws?ticket=...` |

- **自托管示例域名不是可用的公共服务**：请替换成你自己部署的 Relay 域名。
- 本地启动：`cd relay && npm install && npm run db:local && npm run dev`
  （`dev`/`dev:realtime` 脚本已带本地 `INTERNAL_SECRET`/`TICKET_SECRET`，不需要 `.dev.vars`）。
- 自托管部署的 secrets 用 `wrangler secret put` 管理，不在源码/仓库里。

## 2. Agent（PC）接入要点

客户端代码：`pc-agent/linknote_agent/relay_client.py` + `runner.py`（agent-dev 负责）。

1. 连接：`GET /api/v1/agent/ws`，Header `Authorization: Bearer <agent_token>`（**不要**把 token 放 URL）。
2. 首帧 `hello`：`{"type":"hello","agent_id":"...","server_time":"..."}`（agent_id 应与配对返回一致）。
3. 随后 Relay 会从 D1 补发该 agent 的 `queued`/`running` 任务（最多 50 条/次）：
   `{"type":"task","task":{"id","url","platform","status","stage","progress","title","created_at"}}`。
   收到直接处理；**同一条 task 可能重复推送**，按 `task.id` 去重（本地“当前任务/已完成”状态）。
4. 处理过程发 `task_update`（可选 `stage`/`message`，PWA 阶段时间线要用）：
   ```json
   {"type":"task_update","task_id":"...","status":"running","stage":"parsing","progress":1,"message":"正在解析链接","title":"视频标题"}
   {"type":"task_update","task_id":"...","status":"running","stage":"transcribing","progress":45,"message":"正在语音转写","title":"视频标题"}
   {"type":"task_update","task_id":"...","status":"completed","progress":100,"title":"视频标题"}
   {"type":"task_update","task_id":"...","status":"failed","error":"模型余额不足","error_code":"billing"}
   ```
   - `status`：`running` / `completed` / `failed`；`stage` 枚举见契约 §8（非法值忽略）。
   - `running` 即“领取”：Relay 更新 D1 `queued→running`（attempts+1、30 分钟租约）并推手机，重复 running 幂等。
5. 完成后发 `task_note`（**正文只走 WS，不写 D1**）：
   ```json
   {"type":"task_note","task_id":"...","markdown":"# ...","title":"视频标题"}
   ```
   重复/乱序发送幂等；无手机在线时 Relay 只记录 task_id（不存正文），手机 `note_request` 时再向 Agent 要。
6. Relay → Agent 增量消息（v0.3 有更新）：
   - `{"type":"ack_note","task_id":"...","persisted":true|false}`：手机侧缓存结果。
     `persisted=true` 可清理 outbox（本机 `notes/` 保留）；`persisted=false` **保留 outbox**，按 30s/2min/10min 重发。
   - `{"type":"note_request","task_id":"..."}`：手机要正文，从 outbox/notes 重发 `task_note`。
   - `{"type":"ping","ts":...}` / `{"type":"probe","ts":...}`：Relay 心跳/探活，回 `pong` / `probe_ack`。
   - `{"type":"phone_online","online":true|false}`：可选优化，可据此决定是否立即推送。
   - `{"type":"error","code":"...","message":"..."}`：仅排障（如 `task_not_found`），不影響连接。
7. 心跳（v0.3）：**新 Agent 请改用 WebSocket 协议 ping**（如 `websockets.connect(..., ping_interval=25, ping_timeout=20)`），
   不要再发 25s 应用层 JSON ping（Relay 仍兼容旧 JSON ping→pong，但不再依赖它）。
   - 收到服务端 JSON `ping` 回 `pong`；收到 `probe` 回 `probe_ack`（可附带 `ready`/`issues`）。
   - Relay 只在你连上且**有手机在线**时每 30s ping；Agent 60s 无消息才探活/判离线（关闭码 4002）。
8. readiness（v0.3）：收到 `hello` 后立即发一次
   `{"type":"agent_ready","ready":false,"issues":[{"code":"model_not_configured","label":"未配置 AI 模型"}]}`，
   状态变化时重发；Relay 会透过 phone `hello`/`presence` 与 REST `/agent/me` 暴露给手机。
9. 重连：1/2/4/8/15/30s + jitter；重连后重新 hello + 拉 pending。
   **WS 在线时不要轮询 `GET /agent/tasks/next`**（会与推送重复领取）；只在 WS 断开时用 REST 兜底。
10. 新连接会顶掉旧连接（旧连接若收到关闭帧 code=4001）。每台电脑只保持 1 条 WS。

## 3. 手机（PWA / APK）接入要点

客户端代码：`app/www/js/api.js`、`app.js`、`views/*`（app-dev 负责）。

1. 取 ticket：`POST /api/v1/realtime/ticket`，Header `Authorization: Bearer <device_token>`。
   响应 `data: { ticket, expires_at, agent_id, agent_online, server_time }`。
2. 连接：`new WebSocket(ws(s)://<relay>/api/v1/ws?ticket=<ticket>)`。
   ticket 60s 有效；**每次连接前都重新取 ticket**（正常断线重连也这样）。
3. 首帧 `hello`（含 `online`、`ready`、`issues`），紧跟 `presence`（含 `online`、`last_seen`、`ready`、`issues`）；
   之后 `task_updated` / `note` / `note_unavailable` / `ping`。`ready` 为 null 表示旧 Agent 未上报（按“在线”显示），
   `ready===false` 时显示“电脑在线 · 需要处理”，issues 可展开。
4. 任务列表/详情：创建任务仍走 REST `POST /tasks`；之后只认 WS `task_updated` 更新，不轮询。
   `task_updated.task` 是完整摘要（含 `stage`/`message`/`has_note`/`updated_at`）。
5. 笔记：`note` 消息只有正文。收到后写入 IndexedDB（localStorage 兜底）：
   - 写入成功 → `{"type":"ack_note","task_id":"..."}`（或显式 `persisted:true`）；
   - 写入失败 → `{"type":"ack_note","task_id":"...","persisted":false}`，提示“未保存到手机，可重试”，
     Agent 会保留 outbox 并重试；稍后重试保存成功后发 `persisted:true` 即可清 outbox。
   - 打开未缓存笔记：发 `{"type":"note_request","task_id":"..."}`；
   - Agent 在线 → Relay 转发，Agent 重发 `task_note` → 收到 `note`；
   - Agent 离线 → 收到 `{"type":"note_unavailable","task_id":"...","reason":"agent_offline"}`，提示“电脑离线，稍后重试”。
   - `GET /api/v1/tasks/{id}/note` 返回 **410 `note_not_stored`**（旧接口不可再用）。
6. 在线状态：顶部“电脑在线/离线”只认 `presence`；正常关闭秒级 false。
   v0.3 起手机连上时 Relay 可能先对 Agent 做 3s 探活（`ping`+`probe`），无响应会先推 `presence online:false`，
   Agent 之后有消息再恢复 `online:true`（电脑休眠/断网场景不会再长时间“假在线”）。
   没有手机在线时 Relay 不做周期心跳（成本优化），手机打开后才会开始 30s alarm。
   **手机必须应答 Relay 的 `{"type":"ping"}`**（回 `{"type":"pong","ts":...}`），否则超过
   `WS_PHONE_STALE_SECONDS`(90s) 会被服务端断开（4002）并触发重连。
7. 断线：`onclose` 拿不到 ticket 错误细节（握手 401 时浏览器是 `1006`）。策略：
   - 每次连接前重取 ticket（重取返回 401 → device token 失效 → 清 token 回配对页）；
   - 刚取到 ticket 仍连续 2 次 1006/异常关闭 → 显示“重连中/稍后重试”，不清 token；
   - 退避 1/2/4/8/15s；断线期间 REST 快照兜底，避免白屏。
8. 服务端主动关闭码：4002 心跳超时；4003 设备被 Agent 撤销（清 token 回配对页）。

## 4. 消息字段速查（Relay 实现补充，增量不破坏冻结协议）

- `task_update` 增加可选 `stage`、`message`、`error_code`；`status` 只认 `running|completed|failed`（也容忍 `succeeded`）。
- Server→Agent 增加 `ack_note`（v0.3 带 `persisted: boolean`）、`error`、`ping`、`probe`；
  `task.task` 带 `stage`/`progress`。
- Agent→Server 支持 `agent_ready`、`probe_ack`（v0.3）；旧的 JSON `ping` 仍回 `pong`。
- Server→Phone 增加 `presence`/`hello` 的 `ready`/`issues`（v0.3）；`task_updated.task` 带
  `stage`/`message`/`has_note`/`updated_at`/`completed_at`。
- REST `GET /agent/me` 顶层增加 `ready`/`issues`（v0.3；旧 Agent 为 null）。
- REST `GET /tasks/{id}` 不返回 `markdown`；`has_note` = `status === "succeeded"`（任务已产出笔记，正文不在服务端）。
- REST `POST /agent/tasks/{id}/complete` 带 markdown 时仅 best-effort 广播给在线手机（`note_forwarded` 字段），不落 D1。

## 5. 本地测试

```powershell
cd relay
npm install
npm run db:local     # 本地 D1 migrations（.wrangler/state）
npm test             # 50 项：REST 契约 + WS 实时 + v0.3 成本/ack/readiness + A/B 隔离 + 正文不落库（独立 8799 state）
npm run typecheck    # tsc --noEmit
```

`npm test` 覆盖：agent 连接/替换、ticket 校验/过期、presence 实时、任务创建→agent 秒收、
`task_update`→D1+手机、`task_note`→手机、`ack_note`→agent（含 v0.3 persisted）、`note_request` 重发、
离线 queued 重连补发、心跳超时离线、A/B 多租户隔离、撤销设备、正文不落 D1/DO SQLite
（对本地持久化文件做二进制 marker 扫描）。
v0.3 新增断言：readiness 透传、手机连上探活超时/恢复、**无手机不设 alarm / 不主动 ping /
不写 `room_meta.last_seen_at`**、手机连上才出现 alarm 且断开即清、`ack persisted=false` 不删 undelivered。
这些断言直接读取 Miniflare 的 DO SQLite（`room_meta` / `undelivered_notes` / `_cf_ALARM`），不依赖 30s 真实计时。

单独起一个稳定实例给别人联调：

```powershell
cd relay
npm run db:local
npm run dev:realtime     # http://127.0.0.1:8798，dev secrets 已内置
```

手动验收示例（Node）：

```js
import WebSocket from 'ws';
const ws = new WebSocket('ws://127.0.0.1:8798/api/v1/agent/ws', {
  headers: { authorization: `Bearer ${agentToken}` },
});
ws.on('message', (raw) => console.log(JSON.parse(String(raw))));
```

## 6. 部署（v0.5：自托管；作者不再提供公共 relay）

> ⚠️ v0.5 起作者**不再提供公共 relay，也不再维护任何公共域名**。每个用户在自己的 Cloudflare 账号 +
> 自己的域名部署一套。客户端默认 Relay 为空，必须由用户填写自己的 Relay 地址。
> 完整步骤见 `docs/self-hosting.md`，`relay/wrangler.toml` 顶部也有占位符说明。

1. 创建 D1：`npx wrangler d1 create linknote-db`，把输出的 database_id 填进 `relay/wrangler.toml`。
2. 修改 `relay/wrangler.toml`：
   - `PUBLIC_BASE_URL` / `ALLOWED_ORIGINS` 里的 `https://your-relay.example.com` → 你自己的域名（或 workers.dev 地址）；
   - 如需自定义域名，取消注释 `routes` 并替换成自己的域名。
3. 设置 secrets：`npx wrangler secret put INTERNAL_SECRET`、`npx wrangler secret put TICKET_SECRET`（64 hex）。
4. 应用 D1 迁移 `npm run db:remote`，然后 `npx wrangler deploy`。
5. 客户端（Desktop 向导/设置、PWA、APK）填自己的 Relay 地址；不要连接别人的实例。

- D1 schema：`0001_init.sql`、`0002_realtime.sql`；DO migration `v1 new_sqlite_classes=["AgentRoom"]`。
- 回滚/升级：直接重新 deploy 目标版本代码即可；无 D1 schema 变更时数据兼容，DO 实例/secrets/迁移保留。
- 本文件历史版本中的「作者公共实例 version id / 生产冒烟记录」已在 v0.5 移除（公共实例下线），
  技术验证结论（WS 握手、presence、4001/4003、成本数字等）仍适用于任何自托管部署。

> 已知本地环境差异：Miniflare `wrangler dev` 下「Durable Object 主动 close 的 WS 帧」不一定会转发给客户端
> （客户端可能看到 1006 或收不到 close 帧），但服务端状态（离线/presence/踢下线）已生效；Cloudflare
> 线上环境（历史验证）4001/4003 正常下发。契约测试对本地这一差异做了宽容断言，功能用 presence/REST 状态验证。

## 7. v0.3.0 变更（Relay 侧）

> 依据 `docs/v0.3-protocol-delta.md` §1–§3。目标：DO 成本回到免费额度、修正在线判定、
> ack 区分「收到/已保存」、手机知道「电脑在线 ≠ 可以生成」。

### 7.1 心跳/在线（成本）
- **没有手机 socket 时**：不设 alarm、不主动 ping、不写 `room_meta` SQL（Agent 连接/断开的 online 跃迁除外）。
- **默认值修复（重要）**：v0.2 的 `intVar` 在环境变量缺省时把 `Number('')=0` clamp 到最小值，
  导致线上 `WS_ALARM_SECONDS` 未配置时实际变成 **1s alarm**（v0.2 生产实测：手机在线时 ~1 ping/s，
  白白消耗 DO 额度）。v0.3 已修正为「未配置/空串 → 用默认值」，默认 alarm=30s。
  回归用例：测试套件用**不设置任何 WS_* 变量**的第二个实例断言 alarm 约 30s。
- `last_seen` 只放 DO 内存；`online`/`last_seen_at`/`ready`/`issues` 只在跃迁或变化时合并成 1 条 SQL 写入。
- **有手机 socket 时**：每 30s alarm → 给 Agent/手机发 `ping`；Agent 60s 无任何消息 → `probe`+`ping` 观察 3s，
  仍无响应才 close 4002 + presence offline（hibernation 唤醒后内存 last_seen 会回落，避免误杀活连接）。
  手机全部断开后立刻 `deleteAlarm`。
- Agent 连上后若 15s 无活动，手机连上会触发探活（`ping`+`probe`，3s 超时）：无响应 → `presence {online:false}`
  （只标记，不 close）；收到 `pong`/`probe_ack`/任何 Agent 消息 → `presence {online:true}`。
- 新 Agent 请改用 WebSocket 协议 ping（`ping_interval=25`），别再发 25s 应用层 JSON ping；
  旧 Agent 的 JSON `ping` Relay 仍回 `pong`（兼容）。Agent 收到服务端 `ping` 回 `pong`、收到 `probe` 回 `probe_ack`。

### 7.2 ack_note（已保存 vs 仅收到）
- Phone → Relay：`{"type":"ack_note","task_id":"...","persisted":true|false}`，**缺省 true**（旧 PWA 兼容）。
- Relay → Agent：转发 `{"type":"ack_note","task_id":"...","persisted":...}`。
  - `persisted=false`：**不删除** DO 里该 task 的“未送达”标记，Agent 应保留 outbox 并按退避重发；
  - `persisted=true`：删除标记，Agent 可清理 outbox（本机 `notes/` 保留）。
- Phone：本地缓存写入成功才 ack true；失败 ack false 并提示可重试。

### 7.3 readiness（在线 ≠ 可生成）
- Agent → Relay：`{"type":"agent_ready","ready":false,"issues":[{"code":"model_not_configured","label":"未配置 AI 模型"}]}`
  （收到 hello 后立即发一次，变化时重发；issues 最多 20 项，code ≤64，label ≤200）。
- Relay 内存保存，变化时写 `room_meta`；在以下位置返回（缺失/旧 Agent 为 `null`）：
  - phone `hello`：`ready`, `issues`
  - `presence`：`online`, `last_seen`, `ready`, `issues`
  - DO `/internal/presence` → REST `GET /api/v1/agent/me` 顶层 `ready`, `issues`
- 旧 Agent 不上报 → `ready:null`（未知，UI 按“在线”显示）；新 PWA 在 `ready===false` 时显示“电脑在线 · 需要处理”。
- `probe_ack` 可选携带 `ready`/`issues`（Relay 会当作 readiness 更新；老客户端只回 `ts` 也可以）。

### 7.4 配置项（wrangler.toml vars / 测试可用 `--var` 覆盖）
| 变量 | 默认 | 说明 |
|---|---|---|
| `TICKET_TTL_SECONDS` | 60 | 手机 WS ticket 有效期 |
| `WS_ALARM_SECONDS` | 30 | 有手机时的 alarm 周期 |
| `WS_AGENT_STALE_SECONDS` | 60 | Agent 无消息多久进入探活/判离线 |
| `WS_PHONE_STALE_SECONDS` | 90 | 手机无消息多久被断开 |
| `WS_PROBE_STALE_SECONDS` | 15 | 手机连上时 Agent 上次活动超过该值就探活 |
| `WS_PROBE_TIMEOUT_SECONDS` | 3 | 探活超时 |

### 7.5 测试与验证
- `cd relay && npm test` → **53/53 PASS**（v0.3 时 51 项；v0.4 新增 2 项 Bot 设备签发用例）：新增 v0.3 用例
  （readiness 透传、探活超时/恢复、无手机不设 alarm/不 ping/`room_meta.last_seen_at` 不变、
  手机连上出现 alarm/断开清除、ack persisted=false 保留 undelivered、缺省 true 删除、
  **默认配置 alarm≈30s（intVar 回归）**）。
  断言直接读取 Miniflare 的 DO SQLite（`room_meta`/`undelivered_notes`/`_cf_ALARM`），不依赖 30s 真实计时；
  默认配置回归会额外起一个不传 `WS_*` 变量的独立实例（port+1，测完清理）。
- `npm run typecheck` 通过。
- 兼容性：REST/D1 schema 未变；旧 Agent/旧 PWA 不受影响（缺 persisted 视为 true、缺 ready 视为 null）。

## 8. v0.4.0 增量：Agent 签发 Bot 专用 device token

> 依据 `docs/bot-channels.md` §2。用户在自己的电脑跑 Telegram bot（v0.5 起 QQ Bot 已删除），bot 不需要扫码配对，
> 由本机 Agent 向 Relay 申请一个独立 device token。**不新增 D1 表、不改现有端点行为、无新 secrets/迁移。**

`POST /api/v1/agent/devices`（Agent 认证，`Authorization: Bearer agt_...`）

```json
// 请求
{ "name": "Telegram @abclinknote_bot", "platform": "telegram" }
// 201 响应（明文 token 只在这一次出现）
{
  "ok": true,
  "data": {
    "device": { "id": "uuid", "name": "Telegram @abclinknote_bot", "platform": "telegram", "created_at": "..." },
    "token": "dev_..."
  }
}
```

- `name` 必填 1–40 字符；`platform` ∈ `android|ios|web|telegram`（v0.5 起移除 `qq`），非法值 → `400 invalid_platform`。
- D1 `devices` 只存 `sha256(token)`；响应不返回 `token_hash`，不写日志；丢失只能重新签发。
- Bot 用该 token 调普通 device 接口：`POST /tasks`、`GET /tasks`、`GET /tasks/{id}`、`GET /me` 等；
  任务按 `device_id` 与手机隔离，但与该 Agent 的任务队列/处理链路共享（同一台电脑处理）。
- 撤销：Agent 用 `DELETE /api/v1/agent/devices/{device_id}`；被撤销后 bot token 立即 401。
- 版本：v0.5 起 `RELAY_VERSION = 0.5.0`（health 的 `data.version`）；测试 53/53；typecheck 通过。
  部署：仅需 `npx wrangler deploy`，无 schema/secrets/DO migration 变更。

## 9. v0.5.0 变更：自托管 + 只保留 Telegram Bot

> 依据 `docs/v0.5-selfhost.md`。用户决定 Release 不再使用作者域名/Cloudflare，改为每人自托管 Relay；
> 同时删除 QQ Bot，只保留 Telegram（本机长轮询）。

- **作者不再提供公共 relay**：`relay/wrangler.toml` 改为自托管模板 ——
  无作者自定义域名 routes、`database_id = REPLACE_WITH_YOUR_D1_DATABASE_ID`、
  `PUBLIC_BASE_URL = https://your-relay.example.com`、`ALLOWED_ORIGINS` 只保留 localhost + 示例域名；
  部署前必须替换成自己的 D1/域名并设置 secrets（见第 6 节与 `docs/self-hosting.md`）。
- **删除 qq 设备平台**：`DevicePlatform` / `/agent/devices` 只接受 `android|ios|web|telegram`；
  传 `platform:"qq"` 返回 `400 invalid_platform`（契约测试已覆盖）。
- `RELAY_VERSION = "0.5.0"`（health 的 `data.version`）；REST/WS 其余契约不变；
  无 D1 schema / secrets / DO migration 变更，任何 v0.3/v0.4 实例可直接升级。
- 本地验证：`cd relay && npm test` → **53/53 PASS**；`npm run typecheck` 通过；不部署到任何旧域名。
- 客户端侧（由对应负责人改）：Desktop/PWA/APK 默认 Relay 为空、首次配置填自己的 Relay；QQ Bot 移除。
