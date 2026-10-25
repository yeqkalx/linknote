# 自托管 LinkNote Relay（用自己的 Cloudflare 账号 + 域名）

> LinkNote v0.5.0 起，**项目不再内置任何作者的域名或公共 Relay**。
> 每个用户用自己的 Cloudflare 账号部署一个 Relay Worker（免费额度足够个人使用），然后把桌面/手机指向自己的域名。
>
> 你需要：Cloudflare 账号（免费）、一个已托管在 Cloudflare 的域名（例如 `relay.example.com`）、Node.js 20+。

## 1. 部署 Relay（约 10 分钟）

```powershell
git clone <你的 LinkNote 仓库>
cd linknote-to-android

# 1) 安装 Relay 依赖
cd relay
npm install
npx wrangler login

# 2) 创建 D1 数据库，把返回的 database_id 填进 relay/wrangler.toml
npx wrangler d1 create linknote-db
```

编辑 `relay/wrangler.toml`，至少改这 4 处：

```toml
name = "linknote-relay"                     # 可改成 linknote-relay-你的名字
[d1_databases]
binding = "DB"
database_name = "linknote-db"
database_id = "<上一步返回的 database_id>"

[vars]
PUBLIC_BASE_URL = "https://relay.example.com"
ALLOWED_ORIGINS = "http://localhost:5173,http://127.0.0.1:8787,http://localhost:8787,https://relay.example.com"
```

然后给 Worker 绑定自定义域名（任选其一）：

- 部署后在 Cloudflare Dashboard → Workers & Pages → `linknote-relay` → Settings → Domains & Routes → Add custom domain → `relay.example.com`；
- 或在 `wrangler.toml` 里取消注释并改：

```toml
routes = [{ pattern = "relay.example.com", custom_domain = true }]
```

继续：

```powershell
# 3) 建表（首次部署前必须）
npx wrangler d1 migrations apply linknote-db --remote

# 4) 设置两个密钥（生成随机 64 位 hex，不要提交到仓库）
$internal = node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
$ticket   = node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put INTERNAL_SECRET   # 粘贴 $internal
npx wrangler secret put TICKET_SECRET     # 粘贴 $ticket

# 5) 同步 PWA 静态文件并部署
cd ..
npm --prefix app install
npm --prefix app run vendor
npm --prefix app run sync
cd relay
npx wrangler deploy
```

验证：

```powershell
curl https://relay.example.com/api/v1/health
# 期望：{"ok":true,"data":{"status":"ok","version":"0.5.0","realtime":true,...}}
```

浏览器打开 `https://relay.example.com/` 应能看到链记 PWA。

## 2. 配置客户端

### Windows 桌面
1. 安装 LinkNote MSI。
2. 首次向导新增 **「连接 Relay」** 步骤：填 `https://relay.example.com` → 测试连接 → 保存。
3. 继续 ffmpeg/引擎/AI/Whisper/手机配对。设置页随时可以改 Relay。
4. 如果没配置 Relay：Agent 不会启动，就绪横幅会提示“需要配置 Relay”。

### 手机 PWA（推荐）
直接打开 `https://relay.example.com/` 安装 PWA；PWA 与 Relay 同源，无需手填地址。

### Android APK
APK 没有默认 Relay。首次配对页展开「服务器地址（Relay）」，填 `https://relay.example.com` 再配对。

### PC Agent CLI（可选）
```powershell
python -m linknote_agent pair --relay https://relay.example.com
python -m linknote_agent run
```
不传 `--relay` 且配置为空时，Agent 会报错“还没有配置 Relay 地址”。

## 3. 成本

- 个人 1–3 台常在线设备：Cloudflare Workers + D1 + DO 免费额度内 **$0/月**（域名自备）。
- 免费额度是**硬失败**，约 32 台常在线 Agent 会接近上限；更多人用或要稳定 SLA 时，开 Workers Paid（**$5/月** 起）。
- 详细算法见 [成本模型](cost-model.md)。

## 4. 安全与运维

- `INTERNAL_SECRET` / `TICKET_SECRET` 只放 Cloudflare secret；**不要**写进 `wrangler.toml`/仓库。
- `ALLOWED_ORIGINS` 必须包含你的 PWA 域名（浏览器跨域/同源校验需要）；本地开发保留 localhost 项。
- 备份 D1：
  ```powershell
  npx wrangler d1 export linknote-db --remote --output linknote-backup.sql
  ```
- 升级：
  ```powershell
  git pull
  npm --prefix app install
  npm --prefix app run sync
  cd relay
  npm install
  npx wrangler d1 migrations apply linknote-db --remote
  npx wrangler deploy
  ```
- 未做公开服务前建议**只给自己/朋友使用**，并加限流；不要把你的 Relay 当作公共免费后端。
- Telegram/QQ 等 Bot 的凭证只存用户本机，不经过 Relay；v0.5 只保留 Telegram Bot（本机长轮询），QQ Bot 已删除。

## 5. 常见问题

| 现象 | 处理 |
|---|---|
| `/api/v1/health` 404 | 没部署成功或路由没绑定；`npx wrangler deployments list` 检查；确认自定义域名是橙色云代理 |
| PWA 打开 404 | 部署前没执行 `npm --prefix app run sync`（`relay/public` 是生成目录，不提交 git） |
| 手机配对报 origin_not_allowed | `ALLOWED_ORIGINS` 没包含你的 PWA 域名 |
| 桌面导出 Agent 起不来 | 向导/设置里 Relay 地址为空或不可达；先跑 health 测试 |
| 522/1016 | DNS 记录必须是 Cloudflare 代理（橙色云），Worker 路由/自定义域名绑定成功 |
| D1 报 no such table | 没执行 `wrangler d1 migrations apply --remote` |
| 升级后 PWA 仍旧 | Service Worker 缓存：关掉页面重开/强刷一次；SW 版本会清旧壳 |
