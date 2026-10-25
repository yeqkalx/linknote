# 部署 Relay（Cloudflare Workers + D1）

> v0.5.0 起项目**不提供公共 Relay**。每个用户用自己的 Cloudflare 账号和域名部署。
> 最短路径见 **[自托管 Relay](self-hosting.md)**；本文是更完整的部署/运维参考。

## 1. 前置

- Node.js 20+、Cloudflare 账号、已托管在 Cloudflare 的域名（例如 `relay.example.com`）。
- 不需要服务器、不需要公网 IP。
- 免费额度：Workers/DO 10 万请求/天 + DO 10 万行写/天 + D1 500 万行读/天；**超限是硬失败，不是账单**。个人 1–3 台设备够用；长期公开服务或 30 台以上常在线建议 Workers Paid（$5/月 起）。算法见 [成本模型](cost-model.md)。

## 2. 构建 PWA + 部署

```powershell
cd <仓库根目录>
npm --prefix app install
npm --prefix app run vendor       # marked / DOMPurify → app/www/vendor
npm --prefix app run sync         # app/www → relay/public

cd relay
npm install
npx wrangler login
npx wrangler d1 create linknote-db
```

把 `npx wrangler d1 create` 返回的 `database_id` 填进 `relay/wrangler.toml`，并修改：

```toml
[vars]
PUBLIC_BASE_URL = "https://relay.example.com"
ALLOWED_ORIGINS = "http://localhost:5173,http://127.0.0.1:8787,http://localhost:8787,https://relay.example.com"
```

```powershell
npx wrangler d1 migrations apply linknote-db --remote

# 生成两个随机 64 位 hex 密钥（不要提交到仓库）
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
npx wrangler secret put INTERNAL_SECRET
npx wrangler secret put TICKET_SECRET

npx wrangler deploy
```

首次部署后可先测 workers.dev 地址：

```powershell
curl https://linknote-relay.<你的子域>.workers.dev/api/v1/health
```

## 3. 绑定自定义域名

`*.workers.dev` 在中国大陆访问经常不稳定，建议绑定自己的域名：

1. Cloudflare Dashboard → Workers & Pages → `linknote-relay` → Settings → Domains & Routes。
2. Add → Custom domain → `relay.example.com`。
3. Cloudflare 自动创建 DNS 记录并签发证书（域名必须托管在同一账号）。
4. 访问 `https://relay.example.com/api/v1/health` 验证。

也可以在 `wrangler.toml` 里配置：

```toml
routes = [{ pattern = "relay.example.com", custom_domain = true }]
```

## 4. 部署 PWA

PWA 已作为 Worker 静态资源一起部署（`relay/public`，由 `npm --prefix app run sync` 生成，不提交 git）。
访问根路径 `https://relay.example.com/`，手机 Chrome 打开后从菜单安装。

## 5. 验证清单

- [ ] `/api/v1/health` 返回 `ok` 且 `realtime:true`
- [ ] 根路径能打开 PWA，`/manifest.webmanifest` 可访问
- [ ] 手机 4G 能打开
- [ ] PC Agent 能 `pair --relay https://relay.example.com`，手机能 `claim`
- [ ] 发一个视频链接，任务 `queued → running → succeeded`
- [ ] 手机能读到 Markdown/下载 `.md`
- [ ] PWA 域名已写进 `ALLOWED_ORIGINS`

## 6. 成本

| 项 | 免费额度 | 超出后 |
|---|---|---|
| Workers 请求 | 100k/天 | Paid $5/月含 10M 请求 |
| Workers CPU | 10ms/次 | Paid 30M CPU-ms/月 |
| Durable Objects 请求（含 alarm） | 100k/天 | Paid $5/月含 1M，之后 $0.15/M |
| Durable Objects 行写（含 setAlarm） | 100k/天 | Paid 含 50M/月，之后 $1.00/M |
| Durable Objects 时长 | 13k GB-s/天 | Paid $12.50/M GB-s |
| D1 行读 | 5M/天 | Paid 25B/月 |
| D1 行写 | 100k/天 | Paid 50M/月 |
| D1 存储 | 5GB（单库 500MB） | Paid 5GB 起 |
| 自定义域名/TLS | 免费 | — |

**免费额度是硬失败**：任意一项超限，当天该类型操作直接报错。v0.3.0 起只在手机在线时才 alarm/心跳，Agent 用 WebSocket 协议 ping；算法和监控项见 [成本模型](cost-model.md)。

## 7. 中国大陆访问备选

如果实测 4G 下 Cloudflare 自定义域名仍然慢/断：

- 备选 A：香港/新加坡 VPS 跑同一套 API（Worker 逻辑可移植到 Node/Hono + SQLite），DNS 切过去。
- 备选 B：Cloudflare Tunnel 只给个人用（不适合产品）。
- 大陆服务器需要 ICP 备案，不建议起步就做。

迁移时改手机端“服务器地址”和 PC Agent 的 `relay_base_url`，接口不变。

## 8. 回滚与备份

```powershell
npx wrangler deployments list
npx wrangler rollback <deployment-id>
npx wrangler d1 export linknote-db --remote --output linknote-backup.sql
```

D1 数据不会随 Worker 回滚；表结构变更请用向后兼容的迁移（先加列，后删列）。

## 9. 常见问题

**`database_id` 还是占位值**：`wrangler deploy` 会失败，按第 2 步替换。

**部署后 404**：确认 `relay/public/index.html` 存在；先执行 `npm --prefix app run sync`。

**自定义域名 522/1016**：DNS 必须是 Cloudflare 代理（橙色云），Worker 路由/自定义域名绑定成功。

**手机安装后没有「链记」分享项**：需要 HTTPS、manifest 有 `share_target`、且从浏览器菜单安装（不是普通书签）；Android Chrome 才支持。
