# WAMEIJI-XIANYU 改造方案（参考 ai-goofish-monitor）

更新时间：2026-06-19

## 0. 目标

把当前 `F:\WAMEIJI-XIANYU-full-handoff` 项目大改成与参考项目 [Usagi-org/ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor) 对齐：

- 闲鱼侧的所有功能直接使用参考项目的实现方式（不另作创新）。
- 挖煤姬侧以参考项目为模板做"对等"实现。
- 前端从原生 HTML/JS 升级为参考项目的 Vue 3 + Vite + Tailwind 美化界面。
- 业务核心（采购决策、机会计算、告警、复核、报告、Web 面板）保留并平滑迁移。

合规边界与现有项目保持一致：不绕验证码、不自动下单/付款/发消息，所有采集层在登录失效或风控时返回 `human_required`。

## 1. 参考项目关键资产

参考项目仓库已在本地 `F:\WAMEIJI-XIANYU-full-handoff\reference_usagi\`。

| 资源 | 路径 | 我们怎么用 |
| --- | --- | --- |
| FastAPI 入口 + 生命周期 | `src/app.py` | 替换当前 `web_server.py`，但保留我们的业务路由 |
| Playwright 闲鱼爬虫 | `src/scraper.py`、`src/parsers.py` | 直接复用，改成调用我们 `cd_monitor.services` 的数据契约 |
| AI 多模态分析 | `src/ai_handler.py`、`src/ai_message_builder.py`、`src/services/ai_*.py` | 复用，复用 OpenAI 兼容接口 |
| SQLite 启动与 schema | `src/infrastructure/persistence/sqlite_bootstrap.py` | 在我们 SQLite 之上叠加新表（task/result/price_history），旧表不动 |
| 任务/账号/结果 API | `src/api/routes/*.py` | 复制并改名到我们 `web/api/` 下，路径前缀 `/api` |
| 任务调度（Cron） | `src/core/cron_utils.py`、`src/services/scheduler_service.py` | 复用，注入到我们的后台 worker |
| 通知（多渠道） | `src/services/notification_config_service.py`、`src/infrastructure/external/notification_clients/` | 替换我们现有的 `notify/` |
| 账号策略 + 失败保护 | `src/services/account_strategy_service.py`、`src/failure_guard.py` | 复用，处理多账号轮换 |
| 卖家/价格缓存 | `src/services/seller_profile_cache.py`、`src/services/price_history_service.py` | 接入，喂养给机会计算 |
| Vue 3 前端 | `web-ui/src/views/*` | 整包替换 `web/`，主题色按我们需求微调 |
| Chrome 扩展（登录态导出） | `chrome-extension/` | 直接打包给客户使用 |

## 2. 总体架构

新系统由四个层次组成，沿用参考项目的分层思路：

```
┌────────────────────────────────────────────────────┐
│  Web UI  (Vue 3 + Vite + Tailwind, port 8000)      │
│  - Dashboard / Tasks / Results / Accounts / Settings│
└─────────────────────┬──────────────────────────────┘
                      │  HTTP / WebSocket
┌─────────────────────▼──────────────────────────────┐
│  FastAPI  (src/app.py + our routes)                │
│  - 任务、账号、结果、日志、AI Prompt、设置         │
│  - WebSocket 推送实时日志/价格                      │
└──────┬───────────────────────┬─────────────────────┘
       │                       │
┌──────▼──────────┐    ┌───────▼─────────────────────┐
│  Scheduler      │    │  ProcessService (per-task)  │
│  - APScheduler  │───▶│  - Playwright 闲鱼/挖煤姬  │
│  - Cron         │    │  - AI 多模态分析            │
└──────┬──────────┘    │  - 卖家/价格缓存            │
       │               │  - 通知                     │
       │               └───────┬─────────────────────┘
       │                       │
┌──────▼───────────────────────▼─────────────────────┐
│  Persistence (SQLite, data/app.sqlite3)            │
│  - tasks / task_logs / results / accounts /        │
│    price_history / settings / ai_prompts           │
│  - + 我们的 watchlist / opportunities / reviews /  │
│    wameiji_prices / xianyu_samples                 │
└────────────────────────────────────────────────────┘
```

## 3. 目录与文件改造清单

### 3.1 新增/保留

| 路径 | 动作 | 说明 |
| --- | --- | --- |
| `src/cd_monitor/app.py` | 新增 | FastAPI 入口，参考 `reference_usagi/src/app.py` |
| `src/cd_monitor/web/routers/` | 新增 | 把参考项目 `src/api/routes/*.py` 改为我们的 router 名字 |
| `src/cd_monitor/web/dependencies.py` | 新增 | 注入 ProcessService / SchedulerService / TaskGenerationService |
| `src/cd_monitor/services/scraper/` | 新增 | 整包复用 `reference_usagi/src/scraper.py`、`parsers.py`、`ai_handler.py`、`rotation.py`、`failure_guard.py` |
| `src/cd_monitor/services/scheduler_service.py` | 新增 | 参考项目 `SchedulerService` |
| `src/cd_monitor/services/process_service.py` | 新增 | 参考项目 `ProcessService` |
| `src/cd_monitor/services/ai_*.py` | 新增 | AI 请求、消息构造、响应解析、调度器 |
| `src/cd_monitor/services/notification_config_service.py` | 新增 | 多渠道通知 |
| `src/cd_monitor/services/account_strategy_service.py` | 新增 | 账号轮换策略 |
| `src/cd_monitor/services/seller_profile_cache.py` | 新增 | 卖家档案缓存 |
| `src/cd_monitor/services/price_history_service.py` | 新增 | 价格历史与市场参考价 |
| `src/cd_monitor/infrastructure/persistence/sqlite_bootstrap.py` | 新增 | 启动建表 + 兼容旧 `config.json/jsonl/price_history` |
| `src/cd_monitor/infrastructure/persistence/sqlite_task_repository.py` | 新增 | 任务仓储 |
| `src/cd_monitor/infrastructure/external/ai_client.py` | 新增 | OpenAI 兼容客户端 |
| `src/cd_monitor/infrastructure/external/notification_clients/` | 新增 | ntfy / 企业微信 / Bark / Telegram / Webhook |
| `src/cd_monitor/infrastructure/config/settings.py` | 新增 | Pydantic Settings，参考项目 `env_manager.py` |
| `web-ui/` | 新增 | 整包复用 `reference_usagi/web-ui/`，按需求微调主题 |
| `chrome-extension/` | 拷贝 | 客户导出登录态用 |
| `Dockerfile` / `docker-compose.yaml` | 新增 | 参考项目版本 |
| `start.sh` | 新增 | 一键启动 |

### 3.2 重写

| 路径 | 动作 | 说明 |
| --- | --- | --- |
| `src/cd_monitor/cli.py` | 重写 | CLI 改薄，只保留 `init-db / doctor / import-* / evaluate-*`，Web 由 `python -m cd_monitor.app` 启动 |
| `src/cd_monitor/web_server.py` | 删除 | 由 `app.py` + 路由拆分替代 |
| `src/cd_monitor/scheduler/` | 重写 | 用 `scheduler_service.py` 取代当前 `APScheduler` 包装 |
| `src/cd_monitor/notify/` | 重写 | 接入参考项目多渠道通知服务 |
| `web/` | 删除 | 完全由 `web-ui/` 替换 |
| `tests/test_web_server.py` | 重写 | 改为针对 FastAPI 路由的 `httpx.AsyncClient` 测试 |

### 3.3 保留

| 路径 | 动作 | 说明 |
| --- | --- | --- |
| `src/cd_monitor/core/` | 保留 | 品番/JAN 标准化、标题匹配、成本模型、利润/ROI 标签等业务核心 |
| `src/cd_monitor/review/` | 保留 | 复核与二次复核 |
| `src/cd_monitor/storage/` | 保留并扩展 | 现有 SQLite 表结构，叠加参考项目的新表 |
| `src/cd_monitor/sources/wameiji_browser.py` | 改写 | 不再使用 HTML 解析；改为对等的 Playwright 实时爬虫（结构照搬 `scraper.py`） |
| `src/cd_monitor/sources/xianyu_browser.py` | 改写 | 同上，作为 `scraper.py` 的薄包装 |
| `config.example.yaml` | 保留 | 业务层配置仍走 yaml |
| `.env.example` | 重写 | 沿用参考项目 `.env.example` 字段 |
| `EXTERNAL_COLLECTOR_CONTRACT.md` | 保留 | 兼容离线手工快照导入路径 |

## 4. 数据模型增量

在现有 SQLite 之上追加以下表（参考项目 schema 拷过来再适配）：

- `tasks` - 监控任务：名称、关键词、价格区间、版本、必需/排除关键词、AI Prompt、cron、绑定账号、运行状态。
- `task_logs` - 任务运行日志。
- `task_images` - 任务图片临时目录索引。
- `results` - 监控结果：标题、价格、链接、卖家、AI 判断、AI 原文、发布时间、是否已分析、图片路径。
- `price_history` - 价格历史 + 闲鱼市场参考价。
- `seller_profiles` - 卖家档案缓存。
- `accounts` - 闲鱼登录态（多账号）。
- `ai_prompts` - 任务级 AI Prompt 模板。
- `notification_config` - 多渠道通知配置。
- `settings_kv` - 通用 KV 设置。

现有表（`watchlist / scans / listings / xianyu_samples / opportunities / sent_alerts / review_decisions / recheck_tasks / product_mappings`）原样保留。

## 5. 核心模块映射

| 参考项目模块 | 当前项目对等模块 | 处理方式 |
| --- | --- | --- |
| `scraper.py` 主爬虫 | `services/live_browser_capture.py` + `sources/xianyu_browser.py` | 整包替换 |
| `parsers.py` JSON 解析 | 我们自己写的小 HTML 解析 | 删除，统一走 `parsers.py` |
| `ai_handler.py` 多模态分析 | 当前没有 | 整包新增 |
| `failure_guard.py` 失败保护 | `services/live_scan.py` 部分逻辑 | 复用并替换 |
| `rotation.py` 账号/代理轮换 | `services/xianyu_login_state.py` 单一登录态 | 整包替换，支持多账号 |
| `price_history_service.py` | 当前没有 | 整包新增 |
| `task_service` / `process_service` / `scheduler_service` | `cli.py` + `scheduler/` | 整包替换 |
| `notification_*` | `notify/feishu.py`、`notify/dingtalk.py` | 整包替换为多渠道 |
| `chrome-extension/` 登录态导出 | 当前用 Playwright `storage_state` 命令行 | 同时保留两个入口 |
| `web-ui/` Vue 3 | `web/index.html + app.js + styles.css` | 整包替换 |

## 6. 业务核心保留

以下模块完全保留，作为"采购决策"和"复核"业务内核：

- `core/identifiers.py` 品番/JAN 标准化
- `core/matcher.py` 标题匹配
- `core/cost_model.py` 成本模型
- `core/xianyu_cleaner.py` 闲鱼价格清洗
- `core/evaluator.py` 评估 + 风险标签 + 利润/ROI
- `core/opportunity.py` 机会生成
- `review/` 复核与二次复核
- `storage/` 数据库访问层

`core` 模块的对外接口维持稳定，由 `services/scraper/wameiji_browser.py` 在采集完后调用即可。

## 7. 端到端流程

1. 用户在 Web UI 创建任务：
   - 关键词（如 `SRCL-3520`）、价格区间、版本、必需/排除关键词、AI Prompt、可选 Cron、绑定账号。
2. `SchedulerService` 触发或用户手动启动。
3. `ProcessService` fork 子进程跑 `scraper.run_task(task_config)`：
   - 选择账号（轮换/失败保护）。
   - Playwright 打开闲鱼搜索页，抓搜索 JSON + 详情 + 卖家信息 + 图片。
   - `ItemAnalysisDispatcher` 并发调用 AI 评估商品。
   - 结果写入 `results` 表 + `price_history` + `seller_profiles`。
   - 命中关键词规则的商品写 `sent_alerts` + 通知。
4. 我们 `core/evaluator.py` 在 `results` 写入后计算机会，写入 `opportunities`。
5. Web UI 实时刷新：Dashboard / Tasks / Results / Opportunities / Accounts / Settings。
6. 用户在 Opportunity 详情页"通知/复核"操作写回 `review_decisions`。
7. 报告与回测仍走 `report / backtest` CLI。

## 8. 前端改造

直接复用 `reference_usagi/web-ui/`，只做以下微调：

- 品牌名 `WAMEIJI-XIANYU`，副标题"采购决策与复核平台"。
- 主色按公司 VI 调整（深蓝 + 暖橙）。
- 顶部增加"挖煤姬任务" tab，与闲鱼任务并列。
- 看板区多一个"机会面板"卡片，点击进入现有 Opportunities 视图。

构建产物落 `dist/`，由 FastAPI 直接挂载。开发模式 `web-ui` 走 Vite，Vite 把 `/api /auth /ws` 代理到 `:8000`。

## 9. 关键配置（.env）

沿用参考项目 `.env.example`：

```env
# AI
OPENAI_API_KEY=
OPENAI_BASE_URL=
OPENAI_MODEL_NAME=

# Server
SERVER_PORT=8000
WEB_USERNAME=admin
WEB_PASSWORD=admin123

# 爬虫
RUN_HEADLESS=true
LOGIN_IS_EDGE=false
PCURL_TO_MOBILE=true
SKIP_AI_ANALYSIS=false

# 账号/代理轮换
ACCOUNT_ROTATION_ENABLED=false
ACCOUNT_ROTATION_MODE=per_task
ACCOUNT_STATE_DIR=state
PROXY_ROTATION_ENABLED=false
PROXY_POOL=
PROXY_ROTATION_RETRY_LIMIT=2
TASK_FAILURE_THRESHOLD=3
TASK_FAILURE_PAUSE_SECONDS=600

# 通知
NTFY_TOPIC_URL=
GOTIFY_URL=
GOTIFY_TOKEN=
BARK_URL=
WX_BOT_URL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
WEBHOOK_URL=

# 业务层
CD_DB_PATH=data/cd_monitor.db
CD_CONFIG_PATH=config.yaml
CD_PROFIT_MARGIN_MIN=0.15
```

## 10. 实施阶段

| 阶段 | 工作 | 验证 |
| --- | --- | --- |
| 1. 基建 | 拷入 `reference_usagi/src/{app,scraper,parsers,ai_handler,rotation,failure_guard}.py`；写 `app.py` 框架 + `/health` | `python -m cd_monitor.app` 启动返回 healthy |
| 2. 持久化 | 写 `sqlite_bootstrap.py` + 新表 schema + 兼容导入 | `python -m pytest tests/test_storage.py` 全过 |
| 3. 爬虫适配 | 把 `scraper.py` 拆成闲鱼/挖煤姬两个 task_config；接入 `services/xianyu_login_state.py` 保留旧 CLI 入口 | 用 `xianyu-login-state` 导出 state，跑一次 mock 任务跑通 |
| 4. 任务与调度 | 写 `task_service / process_service / scheduler_service`；保留 `cli.py` 中的 init-db / doctor / import-* / evaluate-* | `python -m pytest` 全过 |
| 5. AI | 接入 `ai_handler / ai_message_builder / ai_response_parser / ai_request_compat`；任务级 Prompt 模板 | 跑通 `task generate` AI 任务生成 job |
| 6. 通知 | 接入 `notification_config_service / notification_clients/*`；保留飞书/钉钉 channel 映射 | 跑通 `notify-dry-run` |
| 7. 前端 | 拷入 `web-ui/` 完整项目，按 §8 微调；FastAPI 挂 `dist/` | `npm run build` 成功；`/` 返回 Vue SPA；Dashboard / Tasks / Results / Opportunities 都能加载 |
| 8. 业务核心适配 | `core/evaluator.py` 在 `results` 写入后触发机会计算 | 跑通 `scan-live` 产生 opportunity |
| 9. 验证 | `pytest + ruff + npm run build + docker compose up` | 全部通过；浏览器走通端到端 |
| 10. 文档 | 更新 `README.md / DELIVERY_REPORT.md / HANDOFF_TO_CLAUDE_CODE.md / CLAUDE_CODE_TODO.md` | 无 TODO 残留 |

## 11. 风险与回退

- **风险 1：参考项目前端未经验证场景多。** 缓解：阶段 7 优先以 mock 数据走通端到端，再接真实数据。
- **风险 2：AI 多模态调用成本。** 缓解：`SKIP_AI_ANALYSIS` 保留为关键词模式；任务级可关闭图片分析。
- **风险 3：多账号轮换违反平台规则。** 缓解：默认 `ACCOUNT_ROTATION_ENABLED=false`；登录态导出走 Chrome 扩展人工完成。
- **风险 4：旧业务表与新表并存数据冗余。** 缓解：保留旧 CLI 入口，旧表仍写入；机会表由新评估器统一写，旧 evaluator 写机会时标 `source=legacy`，便于回溯。
- **回退：若阶段 7 前任一阶段失败，可继续在旧 `web_server.py + web/` 体系下运行；新 FastAPI 与旧 Web 共存。**

## 12. 当前未覆盖（请确认）

下列项未在本方案内展开，等你确认范围后再细化：

- 挖煤姬侧登录态导出方式（参考项目只有闲鱼，挖煤姬需要单独设计 Chrome 扩展或 Playwright 登录态导出命令）。
- 客户演示数据（mock 任务/示例 watch）。
- Docker 镜像仓库与 CI。
- 中文文案微调清单。

如果上述方案与你的预期一致，下一步我就按 §10 的阶段 1 启动基建工作（拷入参考项目核心代码 + 搭起 FastAPI 入口）。
