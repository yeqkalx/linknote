# 部署 Relay（Cloudflare Workers + D1）

> 目标：把 `relay/` 部署到 `https://relay.xykong.xyz`，同时把 PWA 一起托管（一个域名）。
> 免费额度：Workers 10 万请求/天 + D1 500 万行读/天，个人使用完全够。

## 0. 前置

- Node.js 20+、Cloudflare 账号、已托管在 Cloudflare 的域名（例如 `xykong.xyz`）。
- 不需要服务器、不需要备案、不需要公网 IP。

## 1. 构建并同步 PWA

在仓库根目录：

```powershell
npm --prefix app install
npm --prefix app run vendor     # marked / DOMPurify → app/www/vendor
npm --prefix app run sync       # app/www → relay/public
```

## 2. 登录 Cloudflare

```powershell
cd relay
npx wrangler login
```

浏览器授权后，`wrangler whoami` 应能看到账号。

## 3. 创建 D1 数据库

```powershell
npx wrangler d1 create linknote-db
```

输出类似：

```toml
[[d1_databases]]
binding = "DB"
database_name = "linknote-db"
database_id = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
```

把 `database_id` 填到 `relay/wrangler.toml` 里替换占位值。

## 4. 应用迁移

```powershell
npm run db:remote
```

首次会提示确认，输入 `y`。以后改表结构时新增 `migrations/0002_xxx.sql`，再跑一次。

## 5. 修改生产变量

编辑 `relay/wrangler.toml`：

```toml
[vars]
PUBLIC_BASE_URL = "https://relay.xykong.xyz"
ALLOWED_ORIGINS = "https://relay.xykong.xyz,https://linknote-relay.<你的子域>.workers.dev"
```

- `ALLOWED_ORIGINS` 是浏览器端允许的 Origin；原生 App 不发 Origin。
- 如果想让任意网页调用，可用 `*`（不建议）。
- 配对码/TTL、租约时长也可用变量覆盖。

## 6. 部署

```powershell
npm run deploy
```

首次部署后 Wrangler 会输出一个 `https://linknote-relay.<子域>.workers.dev` 地址。先验证：

```powershell
curl https://linknote-relay.<子域>.workers.dev/api/v1/health
```

看到 `{"ok":true,"data":{"status":"ok"...}}` 即成功。

## 7. 绑定自定义域名（国内必做）

`*.workers.dev` 在中国大陆访问经常被阻断/不稳定，必须用自定义域名：

1. Cloudflare Dashboard → Workers & Pages → `linknote-relay` → Settings → Domains & Routes。
2. Add → Custom domain → `relay.xykong.xyz`。
3. Cloudflare 自动创建 DNS 记录并签发证书（域名必须已托管在同一账号）。
4. 访问 `https://relay.xykong.xyz/api/v1/health` 验证。

> 不建议用 `relay.xykong.xyz` 以外的主机名；PWA 的 share target 和 Android App 默认地址都按这个域名写。

## 8. 部署 PWA

PWA 已经作为 Worker 的静态资源一起部署（`relay/public`）。访问根路径：

```text
https://relay.xykong.xyz/
```

应当看到「链记」配对页。手机 Chrome 打开 → 菜单 → 安装应用/添加到主屏幕，之后分享面板会出现「链记」。

## 9. 验证清单

- [ ] `/api/v1/health` 返回 ok
- [ ] 根路径能打开 PWA，manifest 可访问（`/manifest.webmanifest`）
- [ ] 手机 4G 能打开（不是只有 Wi-Fi）
- [ ] PC Agent 能 `pair`，手机能 `claim`
- [ ] 发一个 B 站短链接，任务状态从 queued → running → succeeded
- [ ] 手机能读到 Markdown、下载 `.md`

## 10. 成本

| 项 | 免费额度 | 超出后 |
|---|---|---|
| Workers 请求 | 100k/天 | $5/月含 10M 请求 |
| Workers CPU | 10ms/次 | Paid 30M CPU-ms/月 |
| D1 行读 | 5M/天 | Paid 25B/月 |
| D1 行写 | 100k/天 | Paid 50M/月 |
| D1 存储 | 5GB（单库 500MB） | Paid 5GB 起 |
| 自定义域名/TLS | 免费 | — |

实时架构（v0.2.0+）：Agent/手机通过 Durable Object WebSocket Hibernation 长连接推送，空闲连接不计时长；D1 只存任务元数据。成本随事件量而非轮询次数增长。`npm test` 会启动本地 wrangler 验证 DO/WS；生产需要设置 `INTERNAL_SECRET`、`TICKET_SECRET` 并 apply D1 migration 0002。

## 11. 中国大陆访问备选

如果实测 4G 下 Cloudflare 自定义域名仍然慢/断：

- 备选 A：香港/新加坡 VPS（约 ¥40–60/月）跑同一套 API（Worker 逻辑可移植到 Node/Hono + SQLite），DNS 切到 VPS。
- 备选 B：Cloudflare Tunnel 只给个人用（不适合产品）。
- 大陆服务器需要 ICP 备案，不建议起步就做。

迁移时只需要改手机端「服务器地址」和 PC Agent 的 `relay_url`，其余接口不变。

## 12. 回滚

```powershell
npx wrangler deployments list
npx wrangler rollback <deployment-id>
```

D1 数据不会随 Worker 回滚；表结构变更请用向后兼容的迁移（先加列，后删列）。

## 13. 常见问题

**`database_id` 还是占位值**：`npm run deploy` 会失败，按第 3 步替换。

**部署后 404**：确认 `relay/public/index.html` 存在；先执行 `npm --prefix app run sync`。

**自定义域名 522/1016**：DNS 记录必须是 Cloudflare 代理（橙色云），且 Worker 路由绑定成功。

**手机安装后没有「链记」分享项**：需要 HTTPS、manifest 有 `share_target`、且从浏览器菜单安装（不是普通书签）；Android Chrome 才支持。
