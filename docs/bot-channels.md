# Bot 链接入口（v0.4.0 设计）：Telegram / QQ 个人机器人

> 目标：让用户除了手机 PWA/APK，还能用**自己的** Telegram Bot / QQ 机器人发链接。
> 原则：**每个用户填自己的 bot 凭证，bot 跑在用户自己的 Windows 电脑上**；凭证只存本机，不上传 Relay。
> Agent / BiliNote 管线、手机 PWA 都不改；Bot 只是本机 Agent 旁边多出来的两个入口。

## 1. 架构

```text
Telegram 用户 ── getUpdates 长轮询 ──┐
QQ 用户 ── 官方 WebSocket 网关 ───────┤  本机 Agent 进程（linknote_agent）
                                      ├─ Bot Runner：收到链接
                                      ├─ 用 bot 专属 device token 调 Relay REST 建任务
                                      ├─ Relay 队列 → 同一台电脑的 Agent 处理
                                      ├─ 完成后读取本机 notes/<task_id>.md
                                      └─ 把笔记发回 Telegram/QQ 聊天
```

- **不新增云端 bot 服务、不新增 D1 表、不需要 Webhook/公网回调**：
  - Telegram 用 `getUpdates` 长轮询（出站 HTTPS）；
  - QQ 用官方 WebSocket 网关（出站 WSS，官方文档说明“适合个人单机使用”）。
- 用户凭证存在 `%LOCALAPPDATA%\LinkNote\agent.json`（即 Agent 配置），只在本机；`GET /agent/me`/日志里不得出现明文 token。
- Bot 建任务用**专门的 device token**（新增 Relay 端点签发），与手机设备同级隔离；不碰 agent token，也不共享手机 token。

## 2. Relay 增量（很小）

`POST /api/v1/agent/devices`（Agent 认证）
```json
{"name":"Telegram @abclinknote_bot","platform":"telegram"}
```
返回（token 只出现一次）：
```json
{"ok":true,"data":{"device":{"id":"uuid","name":"...","platform":"telegram","created_at":"..."},"token":"dev_..."}}
```
- `platform` 允许 `android|ios|web|telegram|qq`；写 devices 表（token_hash）。
- 本地 Bot Runner 首次运行/device token 失效时调用；之后 Bot 用该 token 调 `POST /tasks`、`GET /tasks/{id}`。
- 其他 Relay 行为不变；部署前需 `wrangler deploy`。

## 3. pc-agent 配置（agent.json 新字段）

```jsonc
{
  "telegram_enabled": false,
  "telegram_bot_token": "",        // 用户自己的 BotFather token
  "telegram_bot_username": "",     // 运行时 getMe 回填/缓存
  "telegram_device_token": "",     // 本机生成，勿上传
  "telegram_allow_groups": false,  // 第一版默认只允许私聊
  "qq_enabled": false,
  "qq_app_id": "",
  "qq_app_secret": "",
  "qq_device_token": "",
  "bot_task_poll_seconds": 3.0,
  "bot_task_timeout_seconds": 3600.0,
  "bot_telegram_preview_chars": 3500,
  "bot_qq_chunk_chars": 1500
}
```
- 环境变量覆盖沿用现有 `LINKNOTE_*` 机制（测试用 `LINKNOTE_TELEGRAM_BOT_TOKEN` 等）。
- 不在日志/异常里打印 token；配置导出 mask。

## 4. Bot 行为

### 命令
- `/start`、`/help`：说明“发视频链接即可”，列出支持平台。
- `/status`：最近 5 个任务（短 id/状态/标题）。
- `/last`：重发最近一篇笔记（QQ 用于关闭回复窗口后主动取回）。
- `/retry <task_id>`：对失败任务重新建任务。
- 其他文本：提取第一个 B 站/YouTube/抖音/快手链接 → 创建任务 → 回复“已提交 #短id，处理中…”。

### Telegram
- 私聊：所有文本都处理；群聊：默认关闭（`telegram_allow_groups=false`），开启时只处理 `@bot` 或命令。
- 完成投递：`sendMessage` 预览（≤3500 字）+ `sendDocument` 发送完整 `.md`（multipart，本机文件）。
- 频控：每聊天约 1 条/秒；429 用 `retry_after` 退避；`getUpdates` 用 offset 去重。
- 注意：中国大陆直连 Telegram 通常不可用，用户需自行解决网络。

### QQ（官方开放平台）
- 连接：`POST /app/getAppAccessToken`（AppID+AppSecret，7200s）→ `GET /gateway` → `wss://.../websocket` → op2 Identify（`token: "QQBot <access_token>"`, `intents: 1<<25`）→ 心跳 op1 / op11。
- 事件：`C2C_MESSAGE_CREATE`（单聊）、`GROUP_AT_MESSAGE_CREATE`（群@）；链接提取：`content`，否则 `ark_data.fields.jump_url`（QQ 会把 B 站链接转成卡片）。
- 回复：被动消息带原消息 `id` + 递增 `msg_seq`：
  - C2C 60 分钟内、最多 4 次；群聊 5 分钟内、最多 5 次。
  - 超窗口：尝试主动消息（不带 msg_id）；失败则把笔记存本机 pending，等用户 `/last` 或下一条消息时用被动回复补发。
- 长笔记：按 ~1500 字分片（最多用满被动次数），末尾提示“完整笔记在电脑/手机”；文件上传后续再做（官方分片上传流程较繁琐）。
- 个人主体限制（必须告知用户）：QQ 开放平台**个人认证暂不支持公开使用**；只能加自己当群主/管理员的群，或使用 20 个“内部体验号码”。要公开给普通用户需企业/个体户认证。
- intents `1<<25` 若平台未给权限，Identify 会失败；需在开放平台申请群/单聊事件权限。

## 5. 可靠性与状态

- `bot-state.json`（本机，原子写）：Telegram offset；QQ session/seq；待投递/进行中任务（task_id、channel、chat_id、reply_msg_id、msg_seq、created_at）；重启后恢复轮询/补投递。
- 任务完成时 `Outbox.note_for(task_id)` 读 `notes/`（Agent 先落盘再发 Relay），读不到则短重试。
- 同一聊天每小时最多 10 个任务（本地限流）；只接受白名单平台 URL。
- 群聊/多人群：所有绑定到同一台电脑；文档提示“任何能给这个 bot 发消息的人都能用你的电脑生成笔记，不要把 bot 公开给陌生人”。

## 6. 桌面 UI（设置页）

新增“消息入口”卡：
- Telegram：Bot Token（密码框）、测试连接（getMe 显示 @username）、启用开关、状态。
- QQ：AppID / AppSecret、测试连接（mint access token）、启用开关、状态 + 个人主体限制提示。
- 保存后写 `agent.json`（含 bot 字段）并重启 Agent；token 输入框显示掩码，不写日志。
- 帮助文案：BotFather `/newbot`；QQ 开放平台创建机器人 → 拿 AppID/AppSecret → 加群/加内部体验号。

## 7. 测试与验收

- relay：新增 `/agent/devices` 契约测试；现有 51 项不回归。
- pc-agent：Telegram（mock getUpdates/send）、QQ（mock WS 帧/发送 API/窗口关闭 fallback）、Runner（链接→任务→本地笔记→投递、重启恢复、device token 签发）；现有 222 项不回归。
- desktop：配置 roundtrip/掩码/测试按钮/开关；84 smoke + 17 pytest 不回归。
- 真实联调：用用户提供的 Telegram @abclinknote_bot token 与 QQ AppID/AppSecret 验证 getMe/access_token/gateway/READY；待用户发一条消息后验证链接→笔记回传。
- 版本：desktop **0.4.0**（重新打包 MSI/EXE）；Relay 仅新增端点需重新部署；PWA/APK 不变。

## 8. 隐私与安全

- Bot 消息与笔记会经过 Telegram / QQ 的平台云（非端到端加密），与手机 PWA 的 Relay 明文转发同样需要披露。
- Bot token/Secret 只存本机；桌面 UI 掩码；不写日志；不随仓库提交。
- 只接受已知平台 URL，沿用 Relay 的 SSRF/平台校验；本地限流。
- QQ 个人主体不可公开使用；不要让用户把 bot 分享给陌生人。

## 9. 用户使用步骤（v0.4.0）

### A. 创建 Telegram bot
1. Telegram 搜 `@BotFather` → `/newbot` → 起名字/用户名（必须以 `bot` 结尾）。
2. 复制得到的 token（形如 `123456:ABC...`）。**token 等于 bot 的全部控制权，不要发到群里/仓库。**
3. 打开桌面 LinkNote → 设置 → 消息入口 → Telegram：粘贴 token → 测试连接（应显示 `@你的bot`）→ 启用 → 保存（Agent 自动重启）。
4. 在 Telegram 打开 `t.me/你的bot` → `/start` → 直接发 B 站/YouTube/抖音/快手链接。
5. 处理完成后 bot 会先发预览，再发完整 `.md` 文件；命令：`/status`、`/last`、`/retry`、`/help`。
6. 群聊默认不处理（bot 的 privacy mode 只收到 @ 消息）；需要群聊时在桌面打开“允许群聊”，并在 BotFather `/setprivacy` → Disable 后重新拉 bot 进群。

### B. 创建 QQ 机器人（QQ 开放平台）
1. <https://q.qq.com/> 注册/登录 → 主体认证 → 创建机器人 → 拿 **AppID / AppSecret**。
2. 开发设置 → 事件订阅：选择 **WebSocket**（本机直连，不需要公网回调）；不需要配置回调地址。
3. 服务范围：个人主体只能添加自己当群主/管理员的群，或配置“内部体验号码”（最多 20 个）；个人主体**不支持公开使用**，公开给普通用户需要企业/个体户认证。
4. 桌面 LinkNote → 设置 → 消息入口 → QQ：填 AppID / AppSecret → 测试连接（access token 获取成功）→ 启用 → 保存。
5. 在手机 QQ 里把机器人拉进你的群（或私聊），@机器人 发链接；处理完成后机器人回笔记文本。
6. QQ 被动回复窗口：群聊约 5 分钟、单聊约 60 分钟，且每条消息最多 4–5 次回复；任务超时未回时发送 `/last` 取最近一篇。
7. 如果机器人收不到消息：检查开放平台是否申请了 `GROUP_AND_C2C_EVENT`（1<<25）事件权限、机器人是否已在群内/你是内部体验号、是否用最新版手机 QQ。

### C. 排查
- 桌面 Agent 日志：`%LOCALAPPDATA%\LinkNote\logs\agent.log` 搜索 `bot` / `telegram` / `qq`。
- Telegram 报 401：token 错误；409：同一 token 在别处轮询；网络不通：需要代理（大陆直连 Telegram 通常不可用）。
- QQ access token 失败：AppID/AppSecret 错误或主体未完成认证；WebSocket READY 失败：intents 没权限或 bot 未上线。
- 机器人不应公开分享：任何能给 bot 发消息的人都能使用你的电脑生成笔记。

