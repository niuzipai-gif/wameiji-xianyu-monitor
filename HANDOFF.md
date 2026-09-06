# WAMEIJI-XIANYU 项目交接说明

> 给接手 agent 的项目状态说明。读完这份 + `MIGRATION_PLAN.md` + `CODEX_PROJECT_SPEC.md` 三份就能上手。

## 1. 这是什么

跨市场倒卖监控系统：

- **xianyu (Goofish)** — 抓 CD 商品，存到 `market_items` 表
- **wameiji (Mercari JP / Yahoo Auctions / Suruga-ya)** — 抓 CD 商品，存到 `market_items` 表
- **matcher** — 跨 catalog 匹配两边 CD，计算利润，存到 `opportunities` 表
- **web (port 9890)** — FastAPI + 内置 http.server + 静态前端 `web/`，前端是 `index.html` + `app.js`

两条线：闲鱼侧的 CD 是进货源（贵），挖煤姬侧的 CD 是出货源（便宜），价差扣掉国际运费 + 关税 + 平台费率 > 阈值就算机会。

## 2. 关键路径速查

| 用途 | 路径 |
|---|---|
| 入口 (web / cli) | `src/cd_monitor/app.py` + `src/cd_monitor/cli.py` |
| web server | `src/cd_monitor/web_server.py` (3338 行，单文件 http.server) |
| matcher | `data/match_real_v2.py` |
| 闲鱼抓取 (h5api 新版) | `data/xianyu_scraper_h5api.py` ← 当前在跑 |
| 闲鱼抓取 (html 旧版) | `data/xianyu_scraper.py` ← 已弃用，留作参考 |
| 挖煤姬抓取 | `data/multi_source_scraper.py` |
| 数据库 | docker volume `wameiji_db` -> `/var/lib/cd_monitor/cd_monitor.db` |
| 启动 | `docker compose up -d --build` |
| 前端 | `web/index.html` + `web/app.js` (~2500 行) |

## 3. 当前生产状态

### 已上线运行

- **h5api scraper** (`xianyu_scraper_h5api.py`) — 通过拦截 `/h5/mtop.taobao.idlemtopsearch.pc.search/` 的 POST 拿到 JSON，比 HTML scraping 稳定；返回带 `item_id / publish_time / image_url`。docker-compose 已切到这个。**首次跑通，已验证 1231 条 xianyu 商品全是 alicdn CDN 图，无登录页占位**。
- **multi_source_scraper** — wameiji 侧，从 mercari / yahoo / suruga-ya 抓。**mercari 被网络封掉，yahoo 可用 (单 cycle ~68 条)，suruga-ya 没启用**。
- **matcher** — 跨语言中-日 CD 匹配。已能跑通，主要靠 catalog_no 精确匹配 + 多信号 fallback (phash / cover_jaccard / title_jp / keywords)。

### 最近一次 session 改了什么

1. `data/xianyu_scraper_h5api.py` — 新文件，~570 行。删了 log() 里多余的 `print()`（之前会和文件 write 重复写一行）。
2. `docker-compose.yaml` — `command:` 覆盖 Dockerfile 默认 CMD，把 `xianyu_scraper.py` 换成 `xianyu_scraper_h5api.py`。
3. `src/cd_monitor/web_server.py` (~第 2617 行) — `_opportunities_paged` 的 LEFT JOIN 加了 `AND xm.catalog_no = o.catalog_no` 守卫。原来如果 opp 关联的 xianyu 商品 catalog_no 不一致会污染前端。
4. `src/cd_monitor/web_server.py` (~第 2869 行) — 删了 `_row_to_dict` 里"xianyu 字段为空时回退到 wameiji 数据"的逻辑。这条逻辑是用户"对不上"抱怨的根因之一：前端看上去闲鱼那栏和挖煤姬那栏是同一个商品（其实是 wameiji 数据被复制过去了）。
5. 数据库里手动清掉了 53 条 `opportunities.xianyu_display_sample_id` (sample 的 catalog 和 opp 的 catalog 不一致)。docker volume 没在交接包里 — 接手的 agent 启动容器会重新生成。

### 仍未解决（已知问题）

1. **匹配仍然"对不上"** — 大部分 opp 现在 `xianyu_display_sample_id` 是 NULL，所以前端闲鱼侧是空的。原因是 `match_real_v2.py` 的 `same_album` 硬门槛太严：要求 pHash≤14 **或** artist+title_jp 同时命中 **或** cover_jaccard≥0.30。大多数 xianyu 商品没算过 phash/cover_text，全被 `continue` 跳过。
   - **修复方向**：放松 same_album 检查，加上"打分最高者兜底"逻辑（top-scored fallback），允许 xianyu 商品入库后 phash 后跑一遍再严筛。修改点在 `match_real_v2.py` ~1009 行。本次 session 改了一半（缩进问题 patch 没贴上），接手从这里开始。
2. **闲鱼侧 0 增长** — h5api 抓到的商品 95%+ 都在 dedup 集里。**因为 watchlist 的 keyword 太窄**。需要扩 watchlist / 让 scraper 触发更多变体搜索词。
3. **挖煤姬侧仍只 Mercari 关键词映射，没做"以图搜图"** — `data/refine_wameiji.py` 和 `reverse_search.py` 有 saucednao 集成，但没在主流程里。
4. **前端"刷新不变"** — 根因是 (1) 闲鱼侧显示为空 + (2) refresh 只换排序不堆叠。修了 (1) 后会改善。
5. **JPY / RMB 双单位显示** — 后端已经有 `price_cny_display` 字段，前端没渲染。
6. **登录态录制流程不顺畅** — 现在用 `xianyu_state.json` (Playwright storage_state)，需要首次手动登录然后导出。挖煤姬侧账号状态文件在 `/app/data/state/`。
7. **terminal 静默问题** — docker 的 `_multi_scraper.log` / `_xianyu_h5api_scraper.log` 现在已经重定向到文件，不应该再弹 terminal。

## 4. 启动

```bash
cd <handoff_dir>
cp .env.example .env  # 改 API keys
docker compose up -d --build
# 等 ~60s healthcheck
curl http://localhost:9890/api/health
```

打开 http://localhost:9890 看前端。

## 5. 接手后第一周建议做的事（按 ROI 排）

1. **修 `match_real_v2.py` 的 same_album 太严**：加 top-scored fallback，让 xianyu 商品有机会被填进 opp。改完跑 `python3 match_real_v2.py` 看 opportunities 表的 xianyu_display_sample_id non-null 比例是否 > 50%。
2. **扩 watchlist**：加更多日语 + 中文关键词变体，把搜索空间扩大 3-5x。不扩的话 dedup 永远 95%+。
3. **挖煤姬加"以图搜图"**：把 `saucednao` 接到 wameiji 抓取主流程。已有 `reverse_search.py` 可复用。
4. **把 `price_snapshots` 表用上**：现在有表但没数据，开了就能做价格趋势图。
5. **Mercari 走代理**：当前被网络封，加日本 IP 代理或换浏览器 fingerprint。

## 6. 不要做的事

- 不要直接改 `data/xianyu_scraper.py` (旧版已弃用)
- 不要回退 `web_server.py` 的 catalog_no 守卫 (会重新出现 catalog 不一致污染)
- 不要重新启用 `_row_to_dict` 里的 wameiji-to-xianyu 回退 (会再次出现"两边同一个商品"的假象)
- 不要用 `git` 提交 (没有 .git 目录，所有变更都是直接编辑文件)

## 7. 容器与服务

启动后容器里有三个常驻进程：

| PID 角色 | 命令 | 用途 |
|---|---|---|
| cd-monitor web | `cd-monitor web --db … --host 0.0.0.0 --port 9890` | web 服务 |
| multi_source_scraper | `python3 multi_source_scraper.py` | wameiji 抓取 |
| xianyu_scraper_h5api | `python3 xianyu_scraper_h5api.py` | xianyu 抓取 |

log 文件：

- `/app/data/_multi_scraper.log` — wameiji 抓取日志
- `/app/data/_xianyu_h5api_scraper.log` — xianyu 抓取日志
- `/app/data/_web_server.log` — web 重启后的输出
- `/app/data/_xianyu_heartbeat.json` / `_scraper_heartbeat.json` / `_mall_heartbeat.json` — 进程心跳

要重启某个进程而不重启容器：

```bash
docker exec wameiji-xianyu-app bash -c 'pkill -f xianyu_scraper_h5api; sleep 2; cd /app/data && nohup python3 -u xianyu_scraper_h5api.py >> _xianyu_h5api_scraper.log 2>&1 &'
```

不要用 `docker exec -d` 这种让 docker 进程在容器里 detach 的方式 — PowerShell 这边会被 PTY 弹窗干扰。

## 8. 数据 schema 速查

```
market_items:
  - id, source ('xianyu'|'wameiji'), source_site ('mercari_jp'|'yahoo_auctions'|'suruga_ya'|'goofish'|'bunjang'),
    external_item_id, catalog_no, title, price, currency ('CNY'|'JPY'),
    url, image_url, image_phash, image_dhash, cover_text, availability, fetched_at

xianyu_price_samples:
  - id, catalog_no, title, price_cny, url, image_url, seller_text, raw_text,
    is_valid, invalid_reason, fetched_at
  ← 与 market_items 不同，存历史价格采样 (筛掉 merch 后)

opportunities:
  - id, catalog_no, wameiji_item_id (-> market_items), expected_profit, net_margin,
    match_confidence, xianyu_reference_price, expected_sale_price,
    xianyu_display_sample_id (是 market_items.id，不是 xianyu_price_samples.id!!),
    analysis_source, status, link_unique_key, created_at
```

**坑点**：opportunities.xianyu_display_sample_id 指向 `market_items.id` (source='xianyu')，不是 `xianyu_price_samples.id`。matcher 的 `fetch_xianyu_samples_for_catalog()` 在 `match_real_v2.py` ~第 196 行查询的是 `market_items`，不是 xps 表。

## 9. 想深入的话

- 整体架构看 `MIGRATION_PLAN.md`
- 业务规则 / 价格公式 / 利润阈值看 `WAMEIJI_DESIGN.md`
- 上游参考实现（h5api 思路来源）`reference_usagi/` 目录在原项目里没纳入交接包；接手可自行 clone `F:\闲鱼助手\ai-goofish-monitor` 参考
