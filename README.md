# LinkNote / 链记

手机分享视频链接 → 家里电脑的 BiliNote 生成 AI 笔记 → Markdown 回到手机。

> 工作名，可改名。定位：**BiliNote 的移动端远程入口**，不是另一个 BiliNote。
> 视频下载与默认语音转写在你的电脑完成，笔记文件保存在本机。使用在线 AI 时，必要文本直连你选择的模型服务商，密钥不经链记中继；链记中继不持久保存笔记正文（转发瞬间可见明文，尚未端到端加密）。

## 现在能跑什么

| 能力 | 状态 |
|---|---|
| Relay：配对/设备/任务 + DO WebSocket 推送 + readiness + ack persisted（v0.3.0） | ✅ 51 项契约/实时测试通过 |
| PC Agent：WS 收任务、协议心跳、probe/ready 上报、笔记 ack 重试 | ✅ 222 项单测通过 |
| 手机 PWA：配对、分享、实时进度、阅读器、缓存固定/徽标、readiness、无假进度 | ✅ 10 项 Playwright 冒烟 + v0.3 mock E2E 全绿 |
| 桌面客户端 v0.3.0：4 导航 + 就绪横幅 + Obsidian/本地目录自动归档 | ✅ 84 项 UI smoke + 17 项 pytest |
| 真实链路：YouTube + B 站各生成一篇笔记 | ✅ 见 [E2E 报告](docs/e2e-report.md) |
| Windows 桌面安装包（内置 BiliNote 引擎） | ✅ MSI 0.3.0 已安装验证；EXE 备用 |
| Android debug APK（Capacitor 壳 + ACTION_SEND） | ✅ 已构建（v1.2）；⚠️ 未真机验证 |
| 生产部署到 `relay.xykong.xyz` | ✅ v0.3.0，Worker `c312b769` |

## v0.3.0 更新（2026-10-24）

- **成本**：只有手机在线时才启用 30s alarm/心跳；无手机时 Relay 不主动 ping、不写心跳 SQL；Agent 改用 WebSocket 协议 ping。修复了 v0.2 线上 `intVar` 缺省值 bug（alarm 实际 1s）。见 [成本模型](docs/cost-model.md)。
- **笔记可靠性**：`ack_note.persisted` 区分“收到”和“保存成功”；手机保存失败会让 Agent 保留 outbox 并按 30s/2min/10min 重发（最多 3 次）；手机缓存 30 篇/6MB、收藏不淘汰、显示“已保存到手机/需从电脑取回”。
- **readiness**：手机能看到“电脑在线 · 可生成”或“在线 · 需要处理 N 项”，不再把“Agent 在线”当成“能生成”。
- **桌面端**：一级导航缩为 笔记库/任务/设备/设置；顶部就绪横幅；设置页可开启 Obsidian/本地目录自动归档（补 YAML frontmatter）。
- **许可**：默认 FFmpeg 改为 BtbN LGPL shared 构建（首次运行下载），安装包附带 GPLv3/LGPLv3 全文；BiliNote 引擎仍为 MIT 编译产物。未做代码签名。
- 协议增量：[realtime-protocol v1.1](docs/realtime-protocol.md) / [v0.3 delta](docs/v0.3-protocol-delta.md)。

## Windows 桌面客户端（推荐给普通用户）

给不想折腾命令行/API 路径的人：**一个安装包，内置 BiliNote 引擎**，首次打开走 7 步向导。
品牌已更新为蓝白配色 / 链条文档图标（`assets/brand/`）。

- 安装包（MSI，推荐）：`dist-desktop/LinkNote-Setup-0.3.0.msi`
  - 大小：151,453,069 字节（144.4 MB）
  - SHA256：`AF17C876C30DB6D2BA6623A41EEEC8C148CEEE761D3DD2F457A800B64D4A18DC`
  - Windows Installer per-user 安装，免管理员；由安装服务创建 `%LOCALAPPDATA%\LinkNote` 数据目录，不经过 Inno 的临时解压，适合被联想电脑管家/火绒拦截的机器
- 安装包（EXE，备用）：`dist-desktop/LinkNote-Setup-0.3.0.exe`
  - 大小：133,550,198 字节（127.4 MB）
  - SHA256：`A7407EA4B7AAE95E3A600EE503748D405A946B8F157E21C2D05056C9B6F62CF9`
  - per-user 安装，免管理员，默认创建桌面/开始菜单快捷方式、默认开机自启
  - 内置 BiliNote 引擎（约 300 MB），不要求用户自己安装 BiliNote
- 主程序：`dist-desktop/LinkNote/LinkNote.exe`
  - SHA256：`BC353B5CD0A42DD2AFA145CA582E513F6D6D710BD8DF29D2E192F37DC6D54E73`（FileVersion 0.3.0.0）
- 首次向导：ffmpeg（LGPL shared，约 78MB）→ 启动引擎 → 填 LLM Key/选模型 → 下载 Whisper → 配对手机
- 主界面：笔记库（搜索/预览/打开文件）、任务、设备（配对常驻）、设置（AI/转写/引擎/日志入口；自动归档）
- 常驻系统托盘；托盘显示绿/黄/红状态；开机自启；关闭窗口即最小化
- 详细说明：[desktop/README.md](desktop/README.md)、[打包说明](desktop/packaging/README.md)

![桌面端主界面](assets/desktop-ui.png)

![任务](assets/desktop-ui-tasks.png)

![笔记库](assets/desktop-ui-library.png)

![设备](assets/desktop-ui-devices.png)

![首次配置向导：配对手机](assets/desktop-ui-onboarding-pair.png)

### 最终 APK（手机端可选）

- 路径：`release/LinkNote-debug.apk`
- 大小：4,700,200 字节（4.48 MiB）
- SHA256：`5925CC0F1E9BC60C150F178F348BCEADAC32C1C2FE1BF2896FE3B64193A4EACD`
- 构建时间：2026-10-24 19:14:09（Asia/Shanghai）
- 包名 `xyz.xykong.linknote`，versionCode 3 / versionName 1.2，label「链记」，minSdk 24 / targetSdk 36，debug 签名，`android:allowBackup="false"`
- 内置 PWA v0.3.0（APP_VERSION/SW_VERSION 均 0.3.0）
- 分享入口：系统分享 `text/plain` → App 内收件箱预填
- 构建/安装说明见 [packaging/android/README.md](packaging/android/README.md)

![收件箱](assets/ui-shots/10-home-viewport.png)

![阅读器](assets/ui-shots/03-reader.png)

## 它怎么工作

```text
手机 PWA / APK ◀── WebSocket 推送 ──▶ Cloudflare Worker + Durable Object(SQLite)
        │                                │  Hibernation 长连接；D1 只存任务元数据
        │  REST 快照兜底                  │  presence / task_updated / note 转发
        ▼                                ▼
  手机本地缓存笔记 ◀── note ── PC Agent（WebSocket，断线退避重连）
                                       │ http://127.0.0.1:8483/api
                                       ▼
                        Windows 电脑上的 BiliNote（本机，不改代码）
                     yt-dlp → ffmpeg → Whisper/Groq → 你的 LLM Key → Markdown
```

电脑不需要公网 IP，路由器不用端口映射；手机在 4G/公司网络也能用。原理与取舍见 [架构](docs/architecture.md)。

## 快速开始

### 普通用户（Windows）

1. 双击 `dist-desktop/LinkNote-Setup-0.3.0.msi` 安装（推荐；EXE 为备用）。
2. 按首次配置向导完成：ffmpeg → 引擎 → LLM Key → Whisper → 手机配对。
3. 手机打开 `https://relay.xykong.xyz`（或安装 APK），输入配对码即可。

### 开发/本地联调

前置：Node.js 20+、[uv](https://docs.astral.sh/uv/)、本机 BiliNote 引擎（或 `desktop/build/engine`）。

```powershell
npm run setup          # 安装 relay/app 依赖、vendor、sync、本地 D1、pc-agent venv

# 1. 启动 Relay + PWA（保持运行）
npm run dev            # http://127.0.0.1:8787

# 2. 新开 PowerShell：开发模式桌面端（可不安装）
cd desktop
.venv\Scripts\python.exe -m linknote_desktop

# 或者用老式 CLI Agent：
cd pc-agent
.venv\Scripts\python.exe -m linknote_agent pair --relay http://127.0.0.1:8787
.venv\Scripts\python.exe -m linknote_agent run
```

想直接看真实生成的笔记：`assets/e2e/bilibili-note.md`、`assets/e2e/real-note.md`。

## 文档

- [桌面客户端说明](desktop/README.md)（安装/向导/架构/限制）
- [桌面端打包](desktop/packaging/README.md)
- [架构与取舍](docs/architecture.md)
- [实时协议（DO/WS）v1.1](docs/realtime-protocol.md) / [v0.3 协议增量](docs/v0.3-protocol-delta.md)
- [接口契约](docs/api-contract.md)（Relay / Agent / PWA 三方唯一约定）
- [成本模型](docs/cost-model.md)（免费额度/DO 计费/压测口径）
- [v0.3.0 发布报告](docs/v0.3-release-report.md)
- [部署到 Cloudflare](docs/deployment.md)
- [使用指南（Windows + Android）](docs/user-guide.md)
- [安全模型](docs/security.md)
- [端到端验证报告](docs/e2e-report.md)
- [Android 打包说明](packaging/android/README.md)
- [PC Agent 说明](pc-agent/README.md)

## 仓库结构

| 目录 | 说明 |
|---|---|
| `desktop/` | Windows 桌面客户端（PySide6 + 托盘 + 首次向导 + 打包） |
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
npm --prefix relay test          # Relay 契约/实时 51 项（自动起 wrangler dev）
node scripts\ui-smoke.mjs        # PWA UI 10 项（需 relay 在 8787 且 UI_RELAY 指向它）
cd pc-agent; .venv\Scripts\python.exe -m pytest -q   # Agent 222 项
cd desktop; .venv\Scripts\python.exe -m linknote_desktop.ui._smoke   # 桌面 84 项 smoke
cd desktop; .venv\Scripts\python.exe -m pytest tests -q              # 桌面 17 项 pytest
npm run check                    # 常用组合（Agent venv 存在时）
```

真实链路验证需要本机 BiliNote 可用，步骤见 [E2E 报告](docs/e2e-report.md)。

## 成本

- 个人 1–3 台常在线：Cloudflare Workers + D1 + DO 免费额度内 **$0/月**（域名自备）。
- **免费额度是硬上限**：DO 10 万请求/天 + 10 万行写/天，超限当天直接报错，不是账单。v0.2.x 的 30s 心跳约 **32 台常在线**就到上限。
- v0.3.0 起只在手机在线时才心跳，估算 1,000 台常在线仍可留在免费额度内；生产/内测建议开 Workers Paid **$5/月** 兜底。
- 不产生云转写/云 LLM 费用；这部分走你自己的 BiliNote 配置。
- 明细与算法见 [成本模型](docs/cost-model.md)。

## 已知限制

- **电脑必须常开并运行 Agent**；电脑离线时任务排队，恢复后继续。
- **没有推送**：App 不在前台时不会提醒，回到前台自动刷新（后续可接邮件/微信服务号）。
- **Relay 在转发瞬间能看到 Markdown 明文，但正文不落 D1/DO**；E2E 加密是后续项。
- 配对创建/领取尚未做 IP 限流；公网大规模开放前需补。
- **安装包未做代码签名**：SmartScreen 会提示“未知发布者”；本机 Inno EXE 仍被安全软件拦（exit 5），分发请用 MSI。
- **FFmpeg 首次运行需联网**从 GitHub/BtbN 下载 LGPL shared 构建（约 78MB，解压 177MB）；下载失败可在向导跳过或手动指定本机 ffmpeg。
- 联想电脑管家会在部分机器上删除 HKCU Run 自启项；需要在联想启动项管理里允许 LinkNote（MSI 写入本身正确）。
- Android APK 尚未真机验证；PWA 在 Android Chrome 上功能完整。
- B 站/YouTube 等平台的下载能力与 ToS 风险由 BiliNote 和用户自己承担；不要公开分发受版权保护的内容。

## License

本仓库代码 MIT。BiliNote 上游为 MIT；仓库不包含也不修改其源码。Windows 桌面安装包可选内置**编译后**的 BiliNote 引擎（MIT，未修改源码，独立进程 + HTTP），也可以 `-SkipEngine` 不内置、使用本机已装的 BiliNote。详见 `NOTICE`。




