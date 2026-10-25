# Telegram Bot 链接入口

> v0.5.0 起只保留 Telegram Bot；QQ Bot 已移除。
> Bot 跑在**用户自己的 Windows 电脑**上，用**用户自己**的 Bot Token；凭证只存本机，不上传 Relay、不经过任何公共 bot 服务。
> 前提：你已经按 [自托管 Relay](self-hosting.md) 部署了自己的 Cloudflare Relay，并在桌面填好 Relay 地址。

## 1. 架构

```text
Telegram 用户 ── getUpdates 长轮询 ──┐
                                     ├─ 本机 Agent（Bot Runner）
                                     ├─ 用 bot 专属 device token 调 Relay 建任务
                                     ├─ Relay 队列 → 同一台电脑的 Agent → BiliNote
                                     ├─ 读取本机 notes/<task_id>.md
                                     └─ 把笔记发回 Telegram（预览 + .md 文件）
```

- **不需要公网回调/Webhook**：本机主动长轮询 Telegram。
- Bot 凭证存在本机 `%LOCALAPPDATA%\LinkNote\agent.json`，日志/异常/导出会掩码。
- 任务走现有 Relay → Agent 管线；笔记从本机直接回发，不经过 Relay 的 bot 服务。
- Relay 侧只新增一个 Agent 认证端点 `POST /api/v1/agent/devices` 来签发 bot 专用 device token。

## 2. 创建 Telegram Bot

1. Telegram 搜 `@BotFather` → `/newbot` → 起名字/用户名（必须以 `bot` 结尾）。
2. 复制 token（形如 `123456:ABC...`）。**token 等于 bot 的全部控制权，不要发到群里/仓库/截图。**
3. 打开桌面 LinkNote → 设置 → 消息入口 → Telegram：粘贴 token → 测试连接（应显示 `@你的bot`）→ 启用 → 保存（Agent 自动重启）。
4. 在 Telegram 打开 `t.me/你的bot` → `/start` → 直接发 B 站/YouTube/抖音/快手链接。
5. 完成后 bot 先发预览，再发完整 `.md` 文件。

## 3. 命令与行为

| 命令 | 说明 |
|---|---|
| `/start` / `/help` | 使用说明 |
| `/status` | 最近 5 个任务 |
| `/last` | 重发最近一篇笔记 |
| `/retry <task_id>` | 重新生成失败任务（可选） |

- 私聊默认全部处理；群聊默认不处理（BotFather privacy mode 只收到 @ 消息）；需要群聊时在桌面打开“允许群聊”，并在 BotFather `/setprivacy` → Disable 后重新拉 bot 进群。
- 同一聊天每小时最多 10 个任务（本机限流）；只接受白名单平台 URL。
- 完成投递：`sendMessage` 预览（≤3500 字）+ `sendDocument` 上传完整 `.md`；Telegram 频控约 1 条/秒/聊天，429 会按 `retry_after` 退避。
- 状态持久化在本机 `bot-state.json`（offset/pending），重启后会恢复未完成任务与待投递。

## 4. 限制与风险

- **Telegram 在中国大陆通常不可直连**，需要用户自行解决网络。
- 同一 bot token 如果同时被 Webhook 或多个 polling 消费者使用会 409；一台电脑只跑一个 Agent。
- **任何能给这个 bot 发消息的人，都能用你的电脑和你的 LLM Key 生成笔记**；不要把 bot 公开分享，群聊要确认成员可信。
- Bot 消息与笔记会经过 Telegram 平台云（非端到端加密），与手机 PWA 的 Relay 明文转发同样需要知情。
- 长笔记预览会截断，但 `.md` 文件是完整的。

## 5. 排查

- 桌面 Agent 日志：`%LOCALAPPDATA%\LinkNote\logs\agent.log`，搜 `bot` / `telegram` / `relay`。
- 测试连接失败：token 错误 / 网络不通（代理）；409：同 token 在别处轮询。
- 配对/建任务失败：先确认 Relay 地址已配置且 `/api/v1/health` 正常。
- 手机/桌面版本升级后 PWA 缓存：重开页面一次让 Service Worker 接管。
