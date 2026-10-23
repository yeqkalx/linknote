# 使用指南（Windows + Android）

> 你需要：一台常开的 Windows 电脑（装好 BiliNote）+ 一部 Android 手机。
> 手机和电脑**不需要在同一个 Wi-Fi**；电脑不需要公网 IP。

## 1. 电脑端准备

### 1.1 BiliNote 必须已经可用

在电脑上打开 BiliNote，确认：

- [ ] 能正常生成一篇笔记（下载、转写、LLM 都通）
- [ ] 已配置 LLM 服务商和可用 Key
- [ ] 已下载本地 Whisper 模型，或配置了 Groq Key
- [ ] ffmpeg 可用（BiliNote 启动时会自检）

> 链记不重新实现这些，它只是把「手机发来的链接」交给 BiliNote。

### 1.2 安装 LinkNote PC Agent

1. 安装 Python 3.11+（或使用 `uv`）。
2. 解压/克隆本仓库到电脑，例如 `C:\linknote`。
3. 双击 `pc-agent\packaging\setup.cmd`（或按 `pc-agent/README.md` 手动执行）：

```powershell
cd C:\linknote\pc-agent
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -e .
```

### 1.3 配对手机

双击 `pc-agent\packaging\pair.cmd`，或：

```powershell
cd C:\linknote\pc-agent
.venv\Scripts\python.exe -m linknote_agent pair --relay https://relay.xykong.xyz
```

窗口会显示：

```text
服务器：https://relay.xykong.xyz
配对码：K7M4-9QXB
有效期：10 分钟
等待手机领取…
```

把这个 8 位码输入手机（见第 2 步）。手机领取成功后，电脑端会自动打印设备名并退出。

### 1.4 启动 Agent（保持运行）

双击 `pc-agent\packaging\start_agent.cmd`，或：

```powershell
cd C:\linknote\pc-agent
.venv\Scripts\python.exe -m linknote_agent run
```

看到类似日志即正常：

```text
LinkNote Agent 已启动
Relay: https://relay.xykong.xyz
设备: 我的手机
等待任务…
```

**电脑关机/睡眠/Agent 退出时，任务会排队；电脑恢复后自动继续。**

### 1.5 开机自启（推荐）

```powershell
cd C:\linknote\pc-agent
powershell -ExecutionPolicy Bypass -File packaging\install_autostart.ps1
```

安装后登录 Windows 会自动静默启动；卸载加 `-Remove`。

## 2. 手机端准备

### 2.1 打开 PWA

用 Android Chrome 打开：

```text
https://relay.xykong.xyz
```

首次使用需要手机与电脑配对：

1. 在电脑上运行 `pair` 命令，得到 8 位配对码。
2. 手机页面输入配对码（横线可省略、大小写不敏感）。
3. 设备名可以改成「我的手机」。
4. 配对成功后进入收件箱。

> 如果页面提示「服务器地址」，说明你用的是 APK 或非官方域名，填入你的 Relay 地址（例如 `https://relay.xykong.xyz`）。

### 2.2 安装到桌面（可选但推荐）

Chrome 菜单 → **安装应用 / 添加到主屏幕**。

安装后：

- 桌面有「链记」图标；
- 在 B 站/YouTube/抖音/快手点「分享」→ 选择「链记」，链接会直接进入收件箱；
- 不用每次打开浏览器。

> Android Chrome 89+ 才支持 Web Share Target；其他浏览器请用「复制链接 → 打开链记粘贴」。

## 3. 生成第一篇笔记

1. 在 B 站 App 里点分享 → 复制链接 / 分享到链记。
2. 链记收件箱里出现链接，点「生成笔记」。
3. 页面显示进度：解析 → 下载 → 转写 → AI 总结 → 完成。
4. 完成后进入阅读器：
   - 复制全文
   - 下载 `.md`
   - 系统分享
   - 重试 / 从列表移除
5. Markdown 会缓存在手机，断网也能看已生成的笔记。

## 4. 常见问题

### 手机显示「电脑离线」

- 电脑是否开机、是否睡眠？
- Agent 窗口/后台进程是否在运行？
- 电脑能否正常上网？
- 在电脑上运行 `python -m linknote_agent status` 查看。

### 配对码无效/过期

- 配对码 10 分钟内有效、只能使用一次。
- 重新运行 `pair` 命令生成新码。
- 检查手机和电脑填的是同一个 Relay 地址。

### 任务一直「排队中」

- Agent 没在运行，或 Relay 地址不一致。
- 电脑睡眠/断网。
- 查看 `pc-agent/logs/agent.log` 和 `pc-agent/logs/bilinote_backend.log`。

### 任务失败：模型 Key / 余额 / 转写模型

- 这是 BiliNote 侧的配置问题，不是链记的问题。
- 打开 BiliNote → 设置，检查 LLM Key、模型、转写引擎。
- 重新在 BiliNote 里生成一次确认可用。

### 下载的 `.md` 打不开

- 任意 Markdown 编辑器 / Obsidian / Typora 都可以打开。
- 文件名来自视频标题，特殊字符会被替换。

### 想换手机 / 解除配对

- 手机：设置 → 解除配对。
- 电脑：`python -m linknote_agent status` 查看；或在手机端解除后重新 `pair`。
- 需要在 Relay 侧移除设备时，可用 Agent token 调用 `DELETE /api/v1/agent/devices/{id}`（进阶）。

### 电脑端日志在哪

```text
pc-agent\logs\agent.log              # Agent 自身
pc-agent\logs\bilinote_backend.log   # BiliNote 后端输出
```

### 换 Relay / 换域名

- 手机设置页改「服务器地址」。
- 电脑 `python -m linknote_agent unpair` 后，用新 `--relay` 重新 `pair`。
- 旧 token 会随设备撤销失效。

## 5. APK（可选）

如果你拿到 `release/LinkNote-debug.apk`：

1. 传到手机，允许「安装未知来源应用」。
2. 安装后打开，首次在配对页/设置页填 Relay 地址（`https://relay.xykong.xyz`）。
3. 其余流程和 PWA 一致；分享面板中的「LinkNote」可以直接接收链接。

> debug APK 只用于自测，不要公开分发；正式上架需要签名、软著、备案等。

## 6. 隐私

- 只有**链接、任务状态、Markdown** 经过 Relay。
- 视频/音频、Whisper 模型、LLM Key 都在你的电脑上。
- 笔记在 Relay 的 D1 数据库里是明文（自用场景）；介意可等后续端到端加密版本。
