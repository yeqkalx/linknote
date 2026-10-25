# 端到端验证报告（2026-10-24）

> 目的：证明「手机/浏览器 → Relay → PC Agent → 本机 BiliNote → Markdown 回手机」真实链路可用，而不是只跑 mock。
> 环境：Windows、本机 BiliNote 安装版 `C:\app\bilinote\BiliNoteBackend.exe`，LLM `deepseek / deepseek-v4-flash`，转写 fast-whisper small，本地 wrangler D1。

## 1. 结论

| 链路 | 结果 | 证据 |
|---|---|---|
| Relay 契约（20 项） | ✅ | `relay/npm test` → PASS 20 / FAIL 0 |
| PC Agent 单测（161 项） | ✅ | `pc-agent/pytest -q` → 161 passed |
| PWA UI 流程（10 项） | ✅ | `scripts/ui-smoke.mjs` → PASS 10 / FAIL 0 |
| PWA 离线壳 + SW | ✅ | 断网重载仍显示配对页 |
| 真实 YouTube 任务 | ✅ | 19 秒视频 12 秒生成，`Me at the zoo`，403 字 |
| 真实 B 站任务 | ✅ | 3:32 视频约 57 秒生成，`【官方 MV】Never Gonna Give You Up - Rick Astley`，779 字 |
| Android debug APK | ✅ 构建 / ⚠️ 未真机 | 4,687,552 字节，包名、label、SEND intent-filter、allowBackup=false、品牌图标已校验 |

## 2. 真实链路细节

### 2.1 配对与提交

- PC Agent 使用独立测试目录 `pc-agent/.e2e`，不影响真实 `~/.linknote`。
- `python -m linknote_agent pair --relay http://127.0.0.1:8787 --no-wait` 生成配对码并保存 `agent_token`。
- 浏览器/脚本用配对码 `POST /api/v1/pairings/claim` 领取 `device_token`。
- `POST /api/v1/tasks` 提交链接，状态 `queued`。
- Agent `run --once` 领取任务（`running`），调用 BiliNote `/generate_note`，状态变化回传 progress，成功 `POST /complete`。

### 2.2 生成结果

| 平台 | 任务 ID | 标题 | Markdown | 用时 | 样本 |
|---|---|---|---|---|---|
| YouTube | `75c797b2-7b7f-4e68-9afc-f98a994a3a40` | Me at the zoo | 403 字 | 约 12 秒 | `assets/e2e/real-note.md` |
| Bilibili | `bfdaba28-e6e8-40f9-9abf-6164861d134f` | 【官方 MV】Never Gonna Give You Up - Rick Astley | 779 字 | 约 57 秒（含 3:32 转写） | `assets/e2e/bilibili-note.md` |

生成结果通过 `GET /api/v1/tasks/{id}/note` 从 Relay 取回，中文与 Markdown 结构无损。

### 2.3 一个环境注意点（不是产品缺陷）

测试机直连 `https://api.bilibili.com` 时 TLS 握手失败（`UNEXPECTED_EOF_WHILE_READING`），但本机有可用本地代理 `127.0.0.1:7897`。给 Agent（进而给自动拉起的 BiliNote）设置 `HTTPS_PROXY/HTTP_PROXY` 后，B 站链路成功。

- 普通家庭/公司网络通常不需要代理。
- 如果用户所在网络访问 B 站 API 异常，BiliNote 本身也会失败；这不是 LinkNote 能绕过的问题。
- 后续可在 Agent 配置中加 `proxy` 字段并传给子进程，作为可选增强。

## 3. PWA UI 冒烟（真实 Relay + 模拟 Agent 回传）

脚本 `scripts/ui-smoke.mjs`（Playwright + 系统 Chrome，移动视口 390×844）：

1. 首次打开进入配对页
2. 输入配对码进入收件箱
3. device token 写入 localStorage
4. 通过 UI 提交任务并出现在列表
5. 列表能看到 running 状态
6. 详情页渲染 Markdown，XSS（script/onerror）被 DOMPurify 清洗
7. 收件箱显示完成任务
8. 设置页可用
9. PWA `/share?text=...` 预填分享内容
10. 页面无 JS 错误

截图：`assets/ui-shots/01-pair.png`、`02-home-empty.png`、`03-reader.png`、`04-home-done.png`、`05-settings.png`、`06-share-target.png`、`10-home-viewport.png`。

## 3.1 Agent 常驻模式

`python -m linknote_agent run` 常驻运行 35 秒后，`GET /api/v1/agent/me` 返回 `online=true`、`last_seen_at` 为 30 秒内、`queue.queued=0/ running=0`，说明心跳与空闲轮询正常。

## 3.2 Android APK

最终 APK 构建于 2026-10-24 16:32:04：

- 路径：`release/LinkNote-debug.apk`
- 大小：4,687,552 字节（4.47 MiB）
- SHA256：`C3C3229DDC46CA18BADC86AA1F15AB960A38BFC4F344222AA85619E7CF6F2D06`
- launcher / adaptive 图标已替换为 LinkNote 品牌图标（15 个 PNG 与 `assets/android-icons` 逐字节一致；adaptive background `#1C1A18`）
- `app/www` 30 个文件与 APK `assets/public` 逐字节一致；另有 Capacitor 生成的 0 字节 `cordova.js/cordova_plugins.js`
- aapt2：`xyz.xykong.linknote`、versionCode 1、minSdk 24、targetSdk 36、label 链记、launchable MainActivity、INTERNET
- Manifest：`ACTION_SEND` + `text/plain`、singleTask、`allowBackup="false"`
- apksigner：v2 debug 签名通过
- DEX 中确认存在 `linknote:share` / `__LINKNOTE_SHARED_TEXT__` / `LinkNoteAndroid` / `getSharedText`

## 4. 未覆盖 / 已知限制

- **Android APK 未真机验证**：本机没有 adb 设备/模拟器；已静态校验 package/label/intent-filter/assets、apksigner 签名。最终 APK 在 `release/LinkNote-debug.apk`。
- **无推送**：App 不在前台时不会收到完成通知；回到前台自动刷新。
- **限流**：配对创建/领取尚未做 IP 限流；公网开放前应补（见 `docs/security.md`）。
- **笔记明文**：Relay 在转发瞬间能看到 Markdown，但正文不落 D1/DO；E2E 加密为后续项。
- **B 站网络**：见 §2.3。
- **真机分享**：Android ACTION_SEND 代码与契约已对齐，但没有设备可验证实际分享面板行为。

## 5. 复现命令

```powershell
# 1. Relay + PWA
npm --prefix app run vendor
npm --prefix app run sync
npm --prefix relay run db:local
npm --prefix relay run dev

# 2. Relay 契约测试
npm --prefix relay test

# 3. PC Agent 单测
cd pc-agent
.venv\Scripts\python.exe -m pytest -q

# 4. PWA UI 冒烟（另开窗口，Relay 保持运行）
node scripts\ui-smoke.mjs

# 5. 真实链路（需要本机 BiliNote 可用）
#   见 scripts/e2e-real-claim.mjs + pc-agent README 的 pair/run 流程
```



## 6. Realtime（Durable Objects + WebSocket Hibernation，2026-10-24）

- Relay：`npm --prefix relay test` → 46/46 PASS（REST + agent/phone WS、ticket、presence、task_updated、task_note、ack_note、note_request、多租户隔离、正文不落 D1/DO 的文件 marker 扫描）。
- PC Agent：`pytest` → 206 passed（旧 161 + 新增 WS/重连/outbox/ack 45）；生产 relay 实测 Agent WS 101 + hello、手机建任务 1.3s 收到 note、ack 清 outbox、`/note` 410、手机离线重发、close 4001。
- PWA：生产域 `<self-hosted-relay>` 真实链路 16/16 PASS（ticket→WS、presence、task_updated、note 本地缓存、ack、无轮询、断线重连）；mock WS 33/33 + 兜底 9/9 + 官方 UI smoke 10/10。
- Relay 生产版本：`b37623dd-7284-4c12-8757-6ce61a4bbc14`；remote D1 migration 0002 已 apply；`tasks.markdown` 非空计数 0；E2E 测试 agent 已清理，保留真实 `我的电脑`。
- 桌面端：0.2.2 MSI 安装版真实启动 → Agent WS hello + 持续 ping/pong；57 项 UI smoke / 8 项 pytest。
