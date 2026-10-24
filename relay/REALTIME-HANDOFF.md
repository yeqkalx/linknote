# LinkNote Relay 实时通道交接（agent-dev / app-dev）

> 状态（2026-10-24）：Relay v0.2.0 已实现 Durable Objects（SQLite 后端）+ WebSocket Hibernation，
> REST 全部保留兼容；本地契约/实时测试 46/46 通过。协议基础以 `docs/realtime-protocol.md` 为准，
> 接口细节与增量字段见 `docs/api-contract.md` 第 11 节。本文件给 PC Agent / PWA 的具体接入信息。

## 1. URL

| 环境 | REST Base | Agent WS | 手机 ticket | 手机 WS |
|---|---|---|---|---|
| 本地（默认） | `http://127.0.0.1:8787/api/v1` | `ws://127.0.0.1:8787/api/v1/agent/ws` | `POST /api/v1/realtime/ticket` | `ws://127.0.0.1:8787/api/v1/ws?ticket=...` |
| 本地（多人联调用） | `http://127.0.0.1:8798/api/v1` | `ws://127.0.0.1:8798/api/v1/agent/ws` | 同上 | `ws://127.0.0.1:8798/api/v1/ws?ticket=...` |
| 生产 | `https://relay.xykong.xyz/api/v1` | `wss://relay.xykong.xyz/api/v1/agent/ws` | 同上 | `wss://relay.xykong.xyz/api/v1/ws?ticket=...` |

- 本地启动：`cd relay && npm install && npm run db:local && npm run dev`
  （`dev`/`dev:realtime` 脚本已带本地 `INTERNAL_SECRET`/`TICKET_SECRET`，不需要 `.dev.vars`）。
- 生产 secrets 用 `wrangler secret put` 管理，不在源码/仓库里。

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
6. Relay → Agent 增量消息：
   - `{"type":"ack_note","task_id":"..."}`：手机已把正文写入本地缓存，可清理本地 outbox（本机 `notes/` 保留）。
   - `{"type":"note_request","task_id":"..."}`：手机要正文，从 outbox/notes 重发 `task_note`。
   - `{"type":"phone_online","online":true|false}`：可选优化，可据此决定是否立即推送。
   - `{"type":"error","code":"...","message":"..."}`：仅排障（如 `task_not_found`），不影響连接。
7. 心跳：每 25s 发 `{"type":"ping","ts":...}`，收到 `{"type":"pong"}`；任何消息都刷新在线状态。
   Relay alarm 30s 检查，`last_seen > 60s` 判离线并关 socket（服务端关闭码 4002）。
8. 重连：1/2/4/8/15/30s + jitter；重连后重新 hello + 拉 pending。
   **WS 在线时不要轮询 `GET /agent/tasks/next`**（会与推送重复领取）；只在 WS 断开时用 REST 兜底。
9. 新连接会顶掉旧连接（旧连接若收到关闭帧 code=4001）。每台电脑只保持 1 条 WS。

## 3. 手机（PWA / APK）接入要点

客户端代码：`app/www/js/api.js`、`app.js`、`views/*`（app-dev 负责）。

1. 取 ticket：`POST /api/v1/realtime/ticket`，Header `Authorization: Bearer <device_token>`。
   响应 `data: { ticket, expires_at, agent_id, agent_online, server_time }`。
2. 连接：`new WebSocket(ws(s)://<relay>/api/v1/ws?ticket=<ticket>)`。
   ticket 60s 有效；**每次连接前都重新取 ticket**（正常断线重连也这样）。
3. 首帧 `hello`（含 `online`），紧跟 `presence`；之后 `task_updated` / `note` / `note_unavailable` / `ping`。
4. 任务列表/详情：创建任务仍走 REST `POST /tasks`；之后只认 WS `task_updated` 更新，不轮询。
   `task_updated.task` 是完整摘要（含 `stage`/`message`/`has_note`/`updated_at`）。
5. 笔记：`note` 消息只有正文。收到后写入 IndexedDB（localStorage 兜底）再发 `ack_note`。
   - 打开未缓存笔记：发 `{"type":"note_request","task_id":"..."}`；
   - Agent 在线 → Relay 转发，Agent 重发 `task_note` → 收到 `note`；
   - Agent 离线 → 收到 `{"type":"note_unavailable","task_id":"...","reason":"agent_offline"}`，提示“电脑离线，稍后重试”。
   - `GET /api/v1/tasks/{id}/note` 返回 **410 `note_not_stored`**（旧接口不可再用）。
6. 在线状态：顶部“电脑在线/离线”只认 `presence`；正常关闭秒级 false，网络突断 ≤60s false。
   手机自己断开不影响该值。
7. 断线：`onclose` 拿不到 ticket 错误细节（握手 401 时浏览器是 `1006`）。策略：
   - 每次连接前重取 ticket（重取返回 401 → device token 失效 → 清 token 回配对页）；
   - 刚取到 ticket 仍连续 2 次 1006/异常关闭 → 显示“重连中/稍后重试”，不清 token；
   - 退避 1/2/4/8/15s；断线期间 REST 快照兜底，避免白屏。
8. 服务端主动关闭码：4002 心跳超时；4003 设备被 Agent 撤销（清 token 回配对页）。

## 4. 消息字段速查（Relay 实现补充，增量不破坏冻结协议）

- `task_update` 增加可选 `stage`、`message`、`error_code`；`status` 只认 `running|completed|failed`（也容忍 `succeeded`）。
- Server→Agent 增加 `ack_note`、`error`；`task.task` 带 `stage`/`progress`。
- Server→Phone 的 `task_updated.task` 带 `stage`/`message`/`has_note`/`updated_at`/`completed_at`。
- REST `GET /tasks/{id}` 不返回 `markdown`；`has_note` = `status === "succeeded"`（任务已产出笔记，正文不在服务端）。
- REST `POST /agent/tasks/{id}/complete` 带 markdown 时仅 best-effort 广播给在线手机（`note_forwarded` 字段），不落 D1。

## 5. 本地测试

```powershell
cd relay
npm install
npm run db:local     # 本地 D1 migrations（.wrangler/state）
npm test             # 46 项：REST 契约 + WS 实时 + A/B 隔离 + 正文不落库（独立 8799 state）
npm run typecheck    # tsc --noEmit
```

`npm test` 覆盖：agent 连接/替换、ticket 校验/过期、presence 实时、任务创建→agent 秒收、
`task_update`→D1+手机、`task_note`→手机、`ack_note`→agent、`note_request` 重发、
离线 queued 重连补发、心跳超时离线、A/B 多租户隔离、撤销设备、正文不落 D1/DO SQLite
（对本地持久化文件做二进制 marker 扫描）。

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

## 6. 部署与回滚（2026-10-24 已上线）

- 生产域名：`https://relay.xykong.xyz`（Worker `linknote-relay`，D1 `linknote-db`）。
- 当前生产 version：`b37623dd-7284-4c12-8757-6ce61a4bbc14`（Lead 同步新 PWA 后部署，2026-10-24T08:30Z）。
  该版本包含 relay v0.2.0 代码 + 新 PWA 静态资源（app/www 0.3.0）。
- 历史版本：`3620d7fc-6a8f-4aaf-9510-3b2a613b63ae`（relay v0.2.0 首部署，旧 PWA）、
  `18db4628-d4ff-4389-be29-9c31daa86ee9`（旧 relay REST-only）。
- 回滚：
  ```powershell
  cd relay
  npx wrangler rollback 3620d7fc-6a8f-4aaf-9510-3b2a613b63ae
  ```
  ⚠️ `wrangler rollback` 会连同静态资源一起回退（即回到旧 PWA）。若只是 relay 代码回归，更推荐用
  当前源码重新 `npx wrangler deploy`（静态资源由 Lead 统一 sync，避免 PWA 回退）。
  DO 实例 / secrets / D1 迁移都会保留（旧代码不感知 DO，安全）。
- 生产 secrets（已设置，随 Worker 保留；不在仓库/源码里）：
  `INTERNAL_SECRET`、`TICKET_SECRET`（`npx wrangler secret put <NAME>`，64 hex）。
- D1 迁移：`0001_init.sql`、`0002_realtime.sql` 均已 apply（`npm run db:remote`）。
  生产 D1 已清理全部 E2E 测试数据（保留真实 `我的电脑` 数据），`tasks.markdown` 全 NULL。
- DO migration：`v1 new_sqlite_classes=["AgentRoom"]` 已随 deploy 创建（`wrangler.toml`）。
- 生产验证（2026-10-24，`node .wrangler/prod-smoke.mjs` / `prod-close-probe.mjs` / `final-verify.mjs`，PASS；
  这些临时脚本在收尾时已随 `.wrangler` 一起清理，可按本文件第 5 节方法重建）：
  - `GET /api/v1/health` → `version 0.2.0, realtime true`；
  - Agent WS（Header token）/ 手机 WS（ticket）均握手 101，invalid token/ticket 握手 401；
  - hello/presence 正常；REST 建任务 → Agent WS 秒收 `task`；
  - `task_update(running 42%)` / `task_update(completed)` → 手机 `task_updated`；
  - `task_note` → 手机 `note`（正文一致）；手机 `ack_note` → Agent 收到；
  - REST detail 无 markdown 字段、`has_note=true`；`GET /tasks/:id/note` → 410 `note_not_stored`；
  - Agent 断开 → 手机 presence false。
  - 服务端主动 close（生产已验证）：新 Agent 连接顶掉旧连接 → 旧连接 **4001**；撤销设备 → 手机 **4003**。
  - 端到端：PC Agent（真实客户端）202+ 项测试 + 生产 REST/WS 全链路通过；PWA 生产 16/16 通过。

> 已知本地环境差异：Miniflare `wrangler dev` 下「Durable Object 主动 close 的 WS 帧」不一定会转发给客户端
> （客户端可能看到 1006 或收不到 close 帧），但服务端状态（离线/presence/踢下线）已生效；**生产 Cloudflare
> 已验证 4001/4003 正常下发**。契约测试对本地这一差异做了宽容断言，功能用 presence/REST 状态验证。
