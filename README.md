# LinkNote / 链记

手机分享视频链接 → 家里电脑的 BiliNote 生成 AI 笔记 → Markdown 回到手机。

> 工作名，可改名。定位：**BiliNote 的移动端远程入口**，不是另一个 BiliNote。
> 视频、音频、Whisper 模型、LLM API Key 全部留在你的电脑；云端只中转链接、任务进度和 Markdown。

## 现在能跑什么

| 能力 | 状态 |
|---|---|
| Relay：配对 / 设备 / 任务 / Markdown（Cloudflare Worker + D1） | ✅ 20 项契约测试通过 |
| PC Agent：配对、领任务、调用 BiliNote、进度回传、失败提示、outbox | ✅ 161 项单测通过 |
| 手机 PWA：配对、分享/粘贴、进度、阅读器、复制/下载/分享、离线壳 | ✅ 10 项 Playwright 冒烟通过 |
| 真实链路：YouTube + B 站各生成一篇笔记 | ✅ 见 [E2E 报告](docs/e2e-report.md) |
| Android debug APK（Capacitor 壳 + ACTION_SEND） | ✅ 已构建；⚠️ 未真机验证 |

### 最终 APK

- 路径：`release/LinkNote-debug.apk`
- 大小：4,552,937 字节（4.34 MiB）
- SHA256：`E1C960162DBCFB870E24BB2B33E3C62BB2EF5B514B41C3C5D5CA8F84B59546CA`
- 构建时间：2026-10-24 05:03:28（Asia/Shanghai）
- 包名 `xyz.xykong.linknote`，label「链记」，minSdk 24 / targetSdk 36，debug 签名，`android:allowBackup="false"`
- 分享入口：系统分享 `text/plain` → App 内收件箱预填
- 构建/安装说明见 [packaging/android/README.md](packaging/android/README.md)
| 生产部署到 `relay.xykong.xyz` | 待你绑定 Cloudflare 账号（见部署文档） |

![收件箱](assets/ui-shots/10-home-viewport.png)

![真实 B 站笔记阅读器](assets/ui-shots/08-real-bilibili-note.png)

## 它怎么工作

```text
手机 PWA / APK ──HTTPS──▶ Cloudflare Worker + D1（只存 URL/状态/Markdown）
        ▲                              ▲
        └──── 前台轮询 3s ─────────────┘
                                       │ PC Agent 主动轮询 + Bearer
                                       ▼
                        Windows 电脑上的 BiliNote（本机，不改代码）
                     yt-dlp → ffmpeg → Whisper/Groq → 你的 LLM Key → Markdown
```

电脑不需要公网 IP，路由器不用端口映射；手机在 4G/公司网络也能用。原理与取舍见 [架构](docs/architecture.md)。

## 快速开始（本地，5 分钟）

前置：Windows 已装好 BiliNote（能正常生成一篇笔记）、Node.js 20+、[uv](https://docs.astral.sh/uv/)。

```powershell
# 0. 在仓库根目录
npm run setup          # 安装 relay/app 依赖、vendor、sync、本地 D1、pc-agent venv

# 1. 启动 Relay + PWA（保持运行）
npm run dev            # http://127.0.0.1:8787

# 2. 新开一个 PowerShell：配对 + 启动 Agent
cd pc-agent
.venv\Scripts\python.exe -m linknote_agent pair --relay http://127.0.0.1:8787
.venv\Scripts\python.exe -m linknote_agent run

# 3. 浏览器打开 http://127.0.0.1:8787
#    输入配对码 → 粘贴视频链接 → 等电脑生成 → 阅读/下载 Markdown
```

想直接看真实生成的笔记：`assets/e2e/bilibili-note.md`、`assets/e2e/real-note.md`。

## 文档

- [架构与取舍](docs/architecture.md)
- [接口契约](docs/api-contract.md)（Relay / Agent / PWA 三方唯一约定）
- [部署到 Cloudflare](docs/deployment.md)
- [使用指南（Windows + Android）](docs/user-guide.md)
- [安全模型](docs/security.md)
- [端到端验证报告](docs/e2e-report.md)
- [Android 打包说明](packaging/android/README.md)
- [PC Agent 说明](pc-agent/README.md)

## 仓库结构

| 目录 | 说明 |
|---|---|
| `relay/` | Cloudflare Worker + D1，配对/任务/笔记 API，同时托管 PWA |
| `pc-agent/` | Windows PC Agent，调用本机 BiliNote |
| `app/` | 手机 PWA（`app/www`，Capacitor `webDir`） |
| `app/android/` | Capacitor Android 工程（含 ACTION_SEND 分享接收） |
| `packaging/android/` | 工具链安装 + APK 构建脚本、补丁、验证记录 |
| `docs/` | 架构、接口、部署、安全、E2E 报告 |
| `scripts/` | 开发/同步/UI 冒烟/图标/端到端脚本 |
| `release/` | `LinkNote-debug.apk`（构建产物，不上 git） |

## 测试

```powershell
npm --prefix relay test          # Relay 契约 20 项（自动起 wrangler dev）
node scripts\ui-smoke.mjs        # PWA UI 10 项（需 relay 在 8787）
cd pc-agent; .venv\Scripts\python.exe -m pytest -q   # 161 项
npm run check                    # 上面三项的组合（Agent venv 存在时）
```

真实链路验证需要本机 BiliNote 可用，步骤见 [E2E 报告](docs/e2e-report.md)。

## 成本

- 个人/小团队：Cloudflare Workers + D1 免费额度内 **$0/月**（域名自备）。
- 超过免费额度：Workers Paid **$5/月** 起。
- 不产生云转写/云 LLM 费用；这部分走你自己的 BiliNote 配置。

## 已知限制

- **电脑必须常开并运行 Agent**；电脑离线时任务排队，恢复后继续。
- **没有推送**：App 不在前台时不会提醒，回到前台自动刷新（后续可接邮件/微信服务号）。
- **Relay 能看到 Markdown 明文**；E2E 加密是后续项。
- 配对创建/领取尚未做 IP 限流；公网大规模开放前需补。
- Android APK 尚未真机验证；PWA 在 Android Chrome 上功能完整。
- B 站/YouTube 等平台的下载能力与 ToS 风险由 BiliNote 和用户自己承担；不要公开分发受版权保护的内容。

## License

本仓库代码 MIT。BiliNote 上游为 MIT；本项目不包含也不修改其源码，仅通过 HTTP 调用本机安装的 BiliNote。详见 `NOTICE`。
