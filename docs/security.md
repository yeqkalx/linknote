# 安全模型与加固清单

> MVP 目标：自用/小范围测试。公网大规模上线前，请把「待补」项做完。

## 1. 资产与信任边界

| 资产 | 在哪 | 谁能看到 |
|---|---|---|
| 视频/音频文件 | PC 本地 | 用户自己 |
| Whisper 模型 / 转写文本 | PC 本地 | 用户自己 |
| LLM API Key | BiliNote 本机配置 | 用户自己 |
| 视频链接 | Relay D1 | Relay 运营者（就是用户自己） |
| 任务进度 | Relay D1 | 同上 |
| Markdown 笔记 | Relay D1 明文 | 同上 |
| Agent token | PC `~/.linknote/agent.json` | 本机用户 |
| Device token | 手机 localStorage | 手机用户 |

**Relay 是唯一公网入口，PC 没有任何入站端口。**

## 2. 已实现的防护

### 认证

- Agent/Device token：32 字节 CSPRNG 随机数，前缀区分；服务端只保存 SHA-256，数据库泄露也无法直接登录。
- 配对码：8 位、32 字母表（约 40 bit）、10 分钟 TTL、单次使用；DB 只存 hash。
- 配对码领取有原子条件更新，防并发重复领取。
- 跨设备访问任务一律返回 404，不泄露任务是否存在。
- 撤销设备后 token 立即 401；该设备的 queued/running 任务自动置 failed。

### 输入

- URL 只接受 B 站 / YouTube / 抖音 / 快手白名单域名。
- 拒绝 IP 字面量、localhost、`.local/.internal`、非 80/443 端口、带用户名密码的 URL。
- URL 长度限制 2048；请求体限制约 3MB；Markdown 限制 1.5MB。
- Agent 侧再校验一次平台，BiliNote 后端自身也有 URL validator（双保险）。

### 输出

- 手机端 Markdown 先经 `marked` 渲染，再经 DOMPurify 清洗；禁用脚本/事件属性。
- 所有链接加 `target=_blank` + `rel=noreferrer noopener`。
- App 不使用 `innerHTML` 渲染除 DOMPurify 输出以外的任何用户内容。
- Relay 对 API 错误统一包装，不返回堆栈/SQL。

### 传输与浏览器

- 全站 HTTPS（Cloudflare 自动证书）。
- CORS 白名单（`ALLOWED_ORIGINS`），只允许部署域名/localhost/Capacitor。
- 不使用 cookie，token 放 `Authorization`，没有 CSRF 面。
- PWA 无第三方脚本/字体/CDN 请求。

### 电脑端

- Agent 只监听出站连接，不开放端口。
- BiliNote 后端默认只在本机 `127.0.0.1:8483` 使用（Agent 不转发外部请求）。
- 日志不打印 token；错误只记录任务 id 和异常类型。
- 自动拉起 BiliNote 时使用固定配置的可执行文件路径，不接受远程下发的命令。

## 3. 已知风险 / 待补

| 风险 | 影响 | 建议 |
|---|---|---|
| 配对创建/领取无限流 | 攻击者可刷 D1、爆破配对码 | 上线前加 Cloudflare Rate Limiting 或 D1 计数（按 IP） |
| Relay 明文存 Markdown | 平台运营者/数据库泄露可读笔记 | 后续做 E2E：手机生成密钥对，Agent 用公钥加密 markdown，Relay 只存密文 |
| token 存 localStorage | 手机 XSS/恶意扩展可窃取 | 前端严格 CSP；APK 后续可接 Android Keystore |
| Agent token 明文存 `~/.linknote/agent.json` | 本机其他用户可读 | Windows 接 DPAPI；限制文件 ACL 为当前用户 |
| 无账号找回 | token 丢失只能重配 | 可接受；后续加恢复码 |
| 无设备指纹/风控 | 被盗 token 可长期用 | 支持随时撤销；后续加设备列表确认 |
| 限流只靠 8 位码 | 理论上可暴力 | 40 bit + 10 分钟 + 单次，配合 IP 限流足够 |
| YouTube/B 站 ToS/版权 | 账号/法律风险 | 只做个人自用；公开产品前咨询法律 |
| 生成式 AI 合规 | 国内上线需备案/标识 | 公开分发前完成评估；MVP 不公开推广 |
| 应用商店 | 需软著/备案/资质 | 先 PWA + 侧载 APK，不上架 |

## 4. 密钥与应急

- 怀疑 Agent token 泄露：在 `~/.linknote/agent.json` 删除配置，重新 `pair`；旧 token 对应 agent 仍在库中，可在 D1 手动删除 agent 行。
- 怀疑 Device token 泄露：手机「设置 → 解除配对」，或在电脑 `DELETE /api/v1/agent/devices/{id}`。
- Relay 数据库导出：`wrangler d1 export linknote-db --remote --output backup.sql`。
- 不要提交 `~/.linknote/agent.json`、`.dev.vars`、D1 导出文件到 git。

## 5. 报告问题

自用项目，直接开 issue / 告诉项目维护者。不要在公开渠道贴 token、配对码或完整笔记。
