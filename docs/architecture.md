# LinkNote（链记）— 架构与实现计划

> 工作名：**LinkNote / 链记**（可随时改名，代码里集中在 `app/www/js/config.js`、`app/capacitor.config.json`、`relay/wrangler.toml`）。
> 目标：手机分享视频链接 → 云端 Relay → 家里/公司电脑上的 BiliNote 生成 AI 笔记 → Markdown 回到手机阅读/导出。
> 原则：**视频、音频、Whisper 模型、LLM API Key 永远不离开电脑；云端只中转链接、状态和 Markdown。**

## 1. 为什么是现在这个形状

- BiliNote v2.4.4 的本机处理链路已经成熟（下载 → ffmpeg → 本地 Whisper/Groq → BYOK LLM → Markdown），不需要重写。
- 企业微信机器人原型（`BiliNote-master/wecom_bot`）已经证明「手机发链接 → 本机处理 → 回传 Markdown」可行，但依赖企业微信、不能做独立产品。
- 手机不可能替代 PC 做 Whisper + ffmpeg + 长文 LLM；全云端又会让用户失去 BYOK/隐私/成本优势。
- 家庭宽带没有公网 IPv4，4G 手机无法直连；必须有中转。Cloudflare Workers + D1 的免费额度对个人/小团队足够，且无需维护服务器。
- 参考项目：
  - `OnYx-main`：Android 原生 + 桌面 Node core + 局域网配对/离线队列/本地 Whisper 的产品化思路（我们只借鉴交互与可靠性，不复用其 LAN-only 架构）。
  - `video-to-note-main`：Python 下载/ASR/LLM 流水线与 Web UI 的拆分方式（BiliNote 已是更完整的同类实现）。
  - `whisper-transcriber-suite-master`：本地 Whisper 的安装/模型/打包经验（BiliNote 内置 faster-whisper，不重复造）。

## 2. 总体架构

```text
┌─────────────────────────────┐
│ Android / PWA（手机）        │
│ 分享面板 → 收件箱 → 阅读器   │
└──────────────┬──────────────┘
               │ HTTPS + Bearer(dev_...)
               ▼
┌─────────────────────────────┐
│ Cloudflare Worker + D1       │  relay.xykong.xyz
│ 配对 / 设备 / 任务 / 笔记    │  只存 URL、状态、Markdown
│ （同时托管 PWA 静态文件）     │
└──────────────┬──────────────┘
               ▲ 出站 HTTPS 轮询 + Bearer(agt_...)
               │ 每 3–5 秒领取任务 / 回传进度
┌──────────────┴──────────────┐
│ Windows PC Agent（Python）   │  常驻托盘/后台
│ 配对 / 任务循环 / 进度 / 重试 │
└──────────────┬──────────────┘
               │ http://127.0.0.1:8483/api
               ▼
┌─────────────────────────────┐
│ BiliNote 后端（不改）         │
│ yt-dlp → ffmpeg → Whisper →  │
│ 用户自己的 LLM Key → Markdown │
└─────────────────────────────┘
```

### 关键数据流

1. Agent 启动 / 用户点「添加手机」→ `POST /api/v1/pairings`，得到 8 位配对码，终端/窗口显示。
2. 手机输入配对码 → `POST /api/v1/pairings/claim`，拿到长期 `device_token`，本地保存。
3. 手机分享链接 → `POST /api/v1/tasks`，Relay 校验 URL/平台，D1 写 `queued`。
4. Agent 轮询 `GET /api/v1/agent/tasks/next`，原子领取任务（`running`，30 分钟租约）。
5. Agent 调 BiliNote `/generate_note`，每 3 秒轮询 `/task_status/{id}`，状态变化时回传 `progress`。
6. BiliNote 返回 `NoteResult.markdown`；Agent `POST /complete`，Relay 写 D1。
7. 手机前台每 3 秒轮询任务详情；成功后 `GET /note` 取 Markdown，渲染阅读器。
8. 断网/电脑离线：任务留在 `queued`；Agent 上线自动继续。Agent 崩溃：租约过期后任务自动回收（最多重试 2 次）。

## 3. 组件

### 3.1 Relay（Cloudflare Worker + D1）

- 纯 TypeScript，无框架；单文件路由 + 少量 lib。
- D1 表：`agents`、`devices`、`pairing_sessions`、`tasks`。
- 同时用 Workers Static Assets 托管 PWA（`relay/public/`），同源免 CORS 麻烦；生产一个域名搞定。
- 不做：视频转码、ASR、LLM、长时间后台任务、WebSocket（MVP）。
- 轮询而非 WebSocket 的原因：实现简单、免费额度够个人/小团队、无长连接计费/休眠复杂度；产品化后再上 Durable Objects。
- 免费额度（2026 官方）：100k 请求/天、10ms CPU/次；D1 5M 行读/天、100k 行写/天、5GB。一个 Agent 5 秒轮询 ≈ 17k 请求/天；10 个 Agent ≈ 170k/天，会超免费额度 → 到时升 $5/月或改 WebSocket。

### 3.2 PC Agent（Python 3.11+）

- 只依赖 `httpx`；不需要 FastAPI/数据库。
- 复用 `wecom_bot` 的成熟逻辑（重写为独立模块 + 测试）：
  - BiliNote HTTP 客户端、状态轮询、失败提示；
  - 平台识别/短链展开；
  - 自动拉起 `BiliNoteBackend.exe`（可配置路径）；
  - Windows 启动项/计划任务脚本。
- 新增：Relay 客户端、配对流程、租约/心跳、本地 outbox（任务完成后 Relay 暂时不可达 → 落盘重试）、结构化日志。
- 单任务串行：BiliNote 后端本身是串行执行器（`task_serial_executor`），多任务无意义。
- 配置存 `~/.linknote/agent.json`（token 明文仅在本机；Windows 建议后续接 DPAPI）。
- CLI：
  - `python -m linknote_agent pair --relay https://relay.xykong.xyz`
  - `python -m linknote_agent run`
  - `python -m linknote_agent status`
  - `python -m linknote_agent install-autostart`（可选，写启动文件夹快捷方式）

### 3.3 手机端（PWA 优先，Capacitor 壳为辅）

- 纯静态 `app/www/`，无构建步骤（marked + DOMPurify vendored），可直接部署/缓存，也方便 Capacitor `webDir` 指向。
- PWA：
  - `manifest.webmanifest` 声明 `share_target`（Android Chrome 安装后，分享面板直接出现「链记」）。
  - Service Worker 缓存壳，离线可打开、读已缓存笔记；API 永远走网络。
- Capacitor（可选 APK）：
  - 同一个 `www` 目录，`appId = xyz.xykong.linknote`。
  - 原生 `MainActivity` 处理 `ACTION_SEND`（text/plain）→ 注入 WebView 路由，补齐 APK 分享入口。
- 页面：
  - 配对页（输入 8 位码 / 可选扫码）；
  - 收件箱（输入/粘贴链接、最近任务、状态、电脑在线状态）；
  - 阅读器（Markdown 渲染、复制、下载 `.md`、系统分享、重试）；
  - 设置（Relay 地址、设备名、解除配对、安装 PWA）。
- 不做推送（国内无 GMS，Web Push 不可靠）：前台轮询 + 回到 App 时刷新即可；后续接邮件/微信服务号。

### 3.4 BiliNote 引擎

- 完全不动上游代码，通过 HTTP 调用。要求用户已安装 BiliNote 并配置好：
  - LLM provider/model（BYOK）；
  - 转写引擎：本地 faster-whisper 模型或 Groq Key；
  - ffmpeg（BiliNote 启动时自检）。
- Agent 配置里保存 `backend_base_url`、`backend_exe`、`provider_id`、`model_name`、`quality`、`style` 等，默认值与用户现有 `wecom_bot/config.json` 一致。
- 注意：BiliNote 后端无鉴权，只监听 `127.0.0.1` / `0.0.0.0` 内网；Agent 不暴露它。

## 4. 目录结构

```text
bilinote-to-android/
├─ README.md
├─ docs/
│  ├─ api-contract.md        # 三方接口契约（先读这个）
│  ├─ architecture.md        # 本文件
│  ├─ deployment.md          # Cloudflare / 域名 / D1 部署
│  ├─ user-guide.md          # 最终用户安装说明
│  └─ security.md            # 威胁模型与后续加固
├─ relay/                    # Cloudflare Worker + D1 + PWA 静态托管
│  ├─ src/index.ts
│  ├─ src/lib/*.ts
│  ├─ migrations/0001_init.sql
│  ├─ tests/contract.e2e.mjs # 启动 wrangler dev 的端到端契约测试
│  ├─ wrangler.toml
│  └─ package.json
├─ pc-agent/                 # Windows PC Agent
│  ├─ pyproject.toml
│  ├─ linknote_agent/*.py
│  ├─ tests/
│  └─ packaging/             # .cmd / 启动项脚本
├─ app/
│  ├─ www/                   # 静态 PWA（可直接打开/部署）
│  ├─ package.json           # vendored 依赖 + Capacitor 脚本
│  └─ capacitor.config.json
├─ scripts/
│  ├─ sync-app.mjs           # app/www → relay/public
│  ├─ vendor-app.mjs         # marked/dompurify 复制到 www/vendor
│  └─ dev.ps1 / e2e.ps1
└─ assets/                   # 图标源文件
```

## 5. 安全边界

- 公网只暴露 Relay；PC 无入站端口。
- Agent/Device token 32 字节随机、仅存 SHA-256；配对码 8 位、10 分钟、单次。
- URL 白名单 + 拒绝私网/IP 字面量/非标准端口；Agent 侧再校验一次。
- Markdown 在客户端 DOMPurify 清洗；不执行 HTML/脚本。
- Relay 能看到明文笔记（MVP 接受，文档明示）；后续可用设备公钥加密 markdown，Relay 只存密文。
- 不做账号系统：一对多设备通过配对码绑定，token 即身份；丢失即重配。
- 限流上线前补齐（Cloudflare Rate Limiting 或 D1 计数）。

## 6. 里程碑（本轮交付）

| # | 内容 | 验收 |
|---|---|---|
| M1 | Relay + D1 + 迁移 + 契约测试 | `npm test` 在本地 wrangler 上跑通完整配对/任务生命周期 |
| M2 | PC Agent + 单测 | pytest 通过；对真实 Relay 能配对、领任务、回传 |
| M3 | PWA | 手机浏览器可配对、提交、看到进度、阅读/导出 Markdown；可安装 |
| M4 | 端到端 | 真实 BiliNote 生成一篇笔记，手机（或模拟）拉到；全链路日志可追踪 |
| M5 | Android APK（尽力） | 有 JDK/SDK 则构建 debug APK 并验证分享入口；否则交付 Capacitor 工程 + 一键构建说明 |
| M6 | 文档 | README 5 分钟跑起来；部署/使用/安全文档齐全 |

## 7. 已知风险与后续

1. **电脑必须常开 + 装 Agent**：产品最大漏斗；先服务 BiliNote 现有用户/团队，不做大众。
2. **国内访问 Cloudflare**：`*.workers.dev` 不可靠；用自定义域名 `relay.xykong.xyz`，4G 实测。不行则同一套 API 迁香港/新加坡 VPS（README 记录迁移点）。
3. **免费额度**：10 个 Agent 以上会超；见 §3.1。
4. **无推送**：完成时 App 不在前台就无提醒；后续邮件/服务号/厂商推送。
5. **合规**：B 站/YouTube ToS、版权、生成式 AI 备案/标识、应用商店。MVP 只做自用/小范围测试，不公开分发 APK。
6. **品牌**：不使用 BiliNote 官方品牌；代码保留上游 MIT 归属说明。
