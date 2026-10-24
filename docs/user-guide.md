# 使用指南（Windows + Android）

> 你需要：一台常开的 Windows 电脑 + 一部 Android 手机。
> 手机和电脑**不需要在同一个 Wi-Fi**；电脑不需要公网 IP。
> 电脑上不需要手动安装 BiliNote——桌面客户端安装包已内置引擎。

## 方案 A：桌面客户端（普通用户推荐）

### A1. 安装

双击：

```text
dist-desktop\LinkNote-Setup-0.2.0.exe
```

- per-user 安装，不需要管理员权限
- 默认安装到 `%LOCALAPPDATA%\Programs\LinkNote`
- 默认创建桌面/开始菜单快捷方式，默认开机自启
- 内置 BiliNote 引擎；卸载时保留 `%LOCALAPPDATA%\LinkNote` 里的设置/笔记数据

### A2. 首次打开：7 步配置向导

程序会自动弹出向导：

1. **欢迎 + 隐私说明**：视频/音频/API Key/转写内容留本机；Relay 中转并保存最终 Markdown。
2. **ffmpeg**：检测不到就点“下载 ffmpeg”（约 100 MB，自动解压）。
3. **引擎**：启动内置 BiliNote 引擎；状态变绿后继续。
4. **AI 模型**：选 DeepSeek/OpenAI 等，粘贴自己的 API Key，点“保存并测试连接”。
5. **语音转写**：默认 Faster Whisper + small，点“下载模型”；网络慢可选 tiny（约 75 MB）。
6. **配对手机**：显示配对码 + 二维码；手机输入/扫码。
7. **完成**：程序进入托盘常驻。

> 没有 LLM API Key 时无法生成笔记；API Key 由你自己在对应服务商购买/创建。
> 以后想改配置：托盘图标 → 打开主界面 → 对应页面；设置页可“重新运行配置向导”。

### A3. 手机端

1. Android Chrome 打开 `https://relay.xykong.xyz`，或安装 `release\LinkNote-debug.apk`。
2. 输入桌面端显示的配对码（或相机扫码打开）。
3. 安装 PWA（Chrome 菜单 → 安装应用），之后 B 站/YouTube 分享面板会出现「链记」。
4. 分享链接 → 等电脑生成 → 手机阅读/下载 Markdown。

### A4. 日常使用

- 桌面端关闭窗口 = 最小化到托盘；右键托盘可打开主界面/暂停任务/重启引擎/退出。
- 电脑关机/休眠或暂停 Agent 时，手机会显示离线；任务会排队，恢复后继续。
- 托盘图标：绿=正常；黄=部分服务未就绪；红=离线/错误。
- 日志目录：`%LOCALAPPDATA%\LinkNote\logs`；主界面“日志”页可直接看。

## 方案 B：PC Agent CLI（开发/高级用户）

如果你不装桌面客户端，也可以直接用仓库里的 Agent（需要自己安装并配置 BiliNote）：

```powershell
cd C:\linknote\pc-agent
uv venv .venv --python 3.11
uv pip install --python .venv\Scripts\python.exe -e .
.venv\Scripts\python.exe -m linknote_agent pair --relay https://relay.xykong.xyz
.venv\Scripts\python.exe -m linknote_agent run
```

`pair` 会打印 8 位配对码；手机领取后 Agent 自动运行。详细参数见 [pc-agent/README.md](../pc-agent/README.md)。

## 常见问题

### 手机显示「电脑离线」

- 桌面端是否在运行（托盘图标）？
- 电脑是否睡眠/断网？
- 主界面状态页看 Agent / 引擎是否绿色；点“测试连接”。
- 如果用的是方案 B，确认 `python -m linknote_agent run` 进程还在。

### 配对码无效/过期

- 配对码 10 分钟内有效、只能使用一次。
- 桌面端：状态页/托盘 → 生成配对码；生成新的即可。
- 手机和电脑必须使用同一个 Relay 地址（默认 `https://relay.xykong.xyz`）。

### 首次向导卡在下载

- ffmpeg：默认从 gyan.dev 下载；网络不通可在“引擎与 ffmpeg”页手动选择已有 `bin` 目录。
- Whisper：默认走 `hf-mirror.com`；公司网络可在“引擎与 ffmpeg → 网络”改 HF_ENDPOINT 或配代理。
- 下载失败可重试；模型下载不会重复下载已完成部分。

### 任务失败：模型 Key / 余额 / 转写模型

这是 BiliNote 引擎侧的配置问题：

- AI Key 无效/余额不足 → 在「AI 模型」页更新并测试。
- 转写模型没下载 → 在「语音转写」页下载。
- 具体原因会在手机任务详情/桌面端日志里显示。

### 想换电脑/解除配对

- 桌面端设置 → 解除配对；手机会变回未配对。
- 手机端设置 → 解除配对；桌面端会显示设备已移除。

### 桌面端安装包被 SmartScreen 拦截

当前安装包未做代码签名，Windows 会提示“未知发布者”。选择“更多信息 → 仍要运行”；正式分发前需要购买代码签名证书。

## 隐私

- 视频、音频、Whisper 转写内容、API Key 都在你的电脑。
- Relay 中转：链接、任务状态、最终 Markdown。
- **Markdown 会保存在 Relay 数据库中**，供手机读取；当前没有端到端加密。
