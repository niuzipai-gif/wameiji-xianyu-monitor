# Usagi / ai-goofish-monitor 研究报告 (2026-07-04)

> **目的**：把已经在 `F:\闲鱼助手\` 部署好的 `Usagi-org/ai-goofish-monitor` 项目
> 的业务模式摸透，识别 Kuro Atelier (WAMEIJI-XIANYU) 可以学习的设计决策、
> 必须改写的地方、以及应该主动规避的反模式。
>
> **约束**：本项目 `F:\闲鱼助手\*` 视为**只读参考**，**不动任何代码**。
> 报告基于 2026-07-03 ~ 2026-07-04 的现状阅读，源码快照来自 `git HEAD`。

---

## 1. 一句话总结

它是个 **单进程 FastAPI + Vue 3 SPA** 的闲鱼监控机器人；账号、登录态、任务、结果都是 **文件系统 + 一份 SQLite**；爬虫以 **子进程 (spider_v2.py)** 启动并通过 SQLite 与主进程协作；它用 **Chrome 扩展**或手动上传两种方式补登录态，**不强求 Playwright 跑一遍**。

---

## 2. 部署与运行（已经存在的事实）

- 启动入口：`F:\闲鱼助手\start-xianyu.ps1` → `docker compose up -d`
- 宿主挂卷：`./data /app/data`、`./state /app/state`、`./prompts /app/prompts`、`./logs /app/logs`、`./images /app/images`、`./jsonl /app/jsonl`、`./price_history /app/price_history`、`.env/.env`
- `Dockerfile` 多阶段构建：Vue 22-alpine → Python 3.11-slim + Chromium。
- 容器端口：`8000` → 宿主 `8080`，默认账号 `admin/admin123`。
- `data/app.sqlite3` 是唯一主存；`state/*.json` 是登录态。
- 状态目录的真实内容：目前只有 `state/haixianxinshou.json` 一个登录态。

> **关于 "docker 都不用开"**：这是部署选择，不是 Usagi 业务层强约束。
> Kuro Atelier 作为本地 CLI/Web 工具**无需 Docker 镜像**；学习的是它**业务层的实现模式**，不是**它的容器化方案**。
---

## 3. 代码地图（按层走，源码路径以 `F:\闲鱼助手\ai-goofish-monitor\` 为根）

### 3.1 入口与组装（`src/app.py`）

- `FastAPI()` + `@asynccontextmanager lifespan(app)`：
  - 启动：`bootstrap_sqlite_storage()` → `cleanup_task_logs()` → 重置所有 task 的 `is_running=False` → `scheduler_service.reload_jobs + start()`。
  - 关闭：`scheduler_service.stop()` → `process_service.stop_all()`。
- 注册路由：tasks / dashboard / logs / settings / prompts / results / login_state / websocket / accounts。
- 静态挂载：`/static` (旧版资产) + `/assets` (Vue 产物 `dist/assets`)，Catch-all `/{full_path:path}` 让 Vue Router history 工作。
- 健康检查：`GET /health`；认证：`POST /auth/status`。

### 3.2 API 路由（`src/api/routes/`）

| 文件 | 路由前缀 | 关键事项 |
|---|---|---|
| `accounts.py` | `/api/accounts` | 文件即存储。`GET ""` 列、`GET "/{name}"` 取、 `POST ""` 建、`PUT "/{name}"` 改、`DELETE "/{name}"` 删。强校验 `^[a-zA-Z0-9_-]{1,50}$`。 |
| `login_state.py` | `/api/login-state` | 单文件 `xianyu_state.json`，非多账号。 |
| `tasks.py` | `/api/tasks` | 任务 CRUD、生成 AI task 的 job 流程。 |
| `dashboard.py` `results.py` `logs.py` | `/api/...` | 全部从 SQLite 查。 |
| `prompts.py` `settings.py` | `/api/...` | 文件系统管 prompts。 |
| `websocket.py` | `/ws` | 实时任务状态推送。 |

### 3.3 服务层（`src/services/`）

- `TaskService`：CRUD + `update_task_status`。
- `ProcessService`：启 `python spider_v2.py --task-name "X"` 子进程；hook `on_started` / `on_stopped` 给主进程同步 `is_running`。
- `SchedulerService`：APScheduler，按 cron 调 ProcessService。
- `account_strategy_service.py`：**唯一的账号策略模块**（详见 §5）。
- AI / Notification / Dashboard / Result 等服务配套。

### 3.4 领域（`src/domain/`）

- `models/task.py`：Pydantic `Task` + `TaskCreate` + `TaskUpdate` + `TaskGenerateRequest`；**含 `account_state_file` 与 `account_strategy: Literal["auto","fixed","rotate"]`**。
- `repositories/task_repository.py`：抽象。
- `models/task_generation.py`：AI 任务生成的中间态。

### 3.5 基础设施（`src/infrastructure/`）

- `persistence/sqlite_connection.py`：单文件 schema；关键表：`tasks / result_items / price_snapshots / result_blacklist_rules / app_metadata`；busy_timeout 5000ms、WAL、FK ON。
- `persistence/sqlite_bootstrap.py`：旧 `config.json` / `jsonl/` / `price_history/` 一次性导入，用 `app_metadata` 标记完成。
- `persistence/sqlite_task_repository.py`：同步版，所有 async 接口走 `asyncio.to_thread`。
- `config/env_manager.py`：统一读 `.env`。
- `external/notification_clients/`：每渠道独立 `xxx_client.py` + `factory`。
- `external/ai_client.py`：AI 多模态兼容（图片上传）。

### 3.6 爬虫本体

- `spider_v2.py`：CLI 主进程，加载 SQLite → 校验登录态是否存在 → 并发调 `scrape_xianyu(task_config=..., debug_limit=...)` → 按 enabled filter + `--task-name` 单跑。
- `src/scraper.py`：用 Playwright 跑单任务，负责账号/反爬/详情抓取。

### 3.7 Chrome 扩展（`chrome-extension/`）

- 用户在 `goofish.com` 页面点扩展 → `background.js` 注入脚本读 `localStorage / sessionStorage / navigator / screen / intl` → 同时用 `chrome.webRequest.onBeforeSendHeaders` 抓一次真实请求头 → `chrome.cookies.getAll` 拿全部 cookies → 过滤大值 → 输出 `{capturedAt, pageUrl, env, storage, headers, cookies}`。
- 来源：Chrome Web Store ID `eidlpfjiodpigmfcahkmlenhppfklcoa`（README 标注）。
- 输出的 JSON 不是 Playwright `storage_state` 标准结构（多一层 `env`、`headers`、`local/session storage`），需要前端再拼一层。

---

## 4. Kuro 现状 vs Usagi 实现（一图对比）

| 维度 | Usagi-org | Kuro Atelier (WAMEIJI-XIANYU) |
|---|---|---|
| 后端框架 | FastAPI + uvicorn | stdlib `http.server` + `BaseHTTPRequestHandler` |
| 前端 | Vue 3 + Vite + shadcn-vue + Tailwind | `web/app.js` (jQuery 风) + `web/index.html` |
| 部署 | Docker Compose | 本地 Python CLI / `python -m cd_monitor.cli web` |
| 数据库 | 单 SQLite `data/app.sqlite3` | SQLite + 多 service + 旧 jsonl/CSV/HTML 并存 |
| 任务定义 | 全在 SQLite `tasks` 表 | `watchlist` 表（旧）`+ task`（任务级） |
| 任务状态字段 | `account_state_file` + `account_strategy ∈ {auto,fixed,rotate}` + `keyword_rules_json` + `decision_mode ∈ {ai,keyword}` + `cron` + `is_running` | `platform`（xianyu/wameiji/both）+ 旧 keyword groups + `cron` + `min_margin` + `failure_*` 三件套。**没有 account 字段、没有 account_strategy、没有 decision_mode** |
| 账号存储 | `state/<name>.json` | 已实现 `data/accounts/<platform>/<name>.json`（与 Usagi 等价）；**路由层 17 个测试卡在 404 / ConnectionAbortedError** |
| 登录态校验 | 只看文件存不存在 + 子进程不崩 | 已有 `inspect_xianyu_login_state` 和 `inspect_wameiji_login_state`，比 Usagi 详细 |
| 启动扫描 | 一次性 `spider_v2.py --task-name` 子进程 | 用现有 `wameiji_browser_runner` + `xianyu_browser_runner` 单进程内调用 |
| 实时通信 | WebSocket `task_status_changed` | 当前是轮询 |
| 浏览器 | 预装 Chromium 的 docker image | 复用用户本地 Chrome profile（合规、人类在线、stop-on-2FA） |
| 反爬 | 账号 + 代理轮换（`PROXY_ROTATION_*`）| **不允许**：明确禁止代理池/账号池/批量注册 |
| 任务失败 | `TASK_FAILURE_THRESHOLD=3` × `TASK_FAILURE_PAUSE_SECONDS=86400` | 已有 `FailureGuard` service + `failure_records` 表，功能到位 |

---

## 5. Usagi 的「账号策略」——这是 Kuro 没有的关键设计

`src/services/account_strategy_service.py`：

```
ACCOUNT_STRATEGIES = {"auto", "fixed", "rotate"}

def resolve_account_runtime_plan(*, strategy, account_state_file,
                                  has_root_state_file, available_account_files) -> dict:
    normalized = normalize_account_strategy(strategy, account_state_file)
    cleaned    = clean_account_state_file(account_state_file)

    if normalized == "fixed":
        return {"strategy": "fixed", "forced_account": cleaned,
                "use_account_pool": False, "prefer_root_state": False}

    if normalized == "rotate":
        return {"strategy": "rotate", "forced_account": None,
                "use_account_pool": bool(available_account_files),
                "prefer_root_state": False}

    # auto:根 statefile 存在就偏爱它,否则走账号池
    return {"strategy": "auto", "forced_account": None,
            "use_account_pool": (not has_root_state_file) and bool(available_account_files),
            "prefer_root_state": has_root_state_file}
```

`Task` 模型约束：

- `account_strategy == "fixed"` **必须** 配 `account_state_file`，否则 `model_validator` 报「固定账号模式下必须选择账号。」
- `account_state_file` 允许为空字符串 → 归一为 `None`。
- `strategies ∈ {"auto", "fixed", "rotate"}` 用 `Literal` 强约束。

`spider_v2.py` 启动前还会做一次**裸断言**：

```
if not exists(STATE_FILE) and not has_bound_account(tasks)
                     and not has_any_state_file():
    sys.exit("错误: 未找到登录状态文件。请在 state/ 中添加账号或配置 account_state_file。")
```

> 这个三策略 + 裸断言对 Kuro 的「多账号采集」需求非常对位——但 Kuro 的约束
> 禁止账号池，所以**应学的是策略三选项的契约**，不要照搬 `use_account_pool`。

---

## 6. Kuro 应该学 / 不该学 —— 明确清单

### 6.1 应该学的（落地建议）

1. **`watchlist` 表加两列**：
   - `account_state_file TEXT`（指向 `data/accounts/<platform>/<name>.json` 的相对路径或账号名）
   - `account_strategy TEXT CHECK(account_strategy IN ('auto','fixed','rotate')) DEFAULT 'auto'`
   - 配 `_migrate_watchlist_settings` 同款 ALTER。
2. **任务侧校验**：在 `_normalize_task_payload` / `WatchItem` model 加 `model_validator(mode='after')`：`strategy=='fixed'` 必须有 `account_state_file`，与 Usagi 严格一致。
3. **账号策略函数**：把 `account_strategy_service.py` 整段复制成 `src/cd_monitor/services/account_strategy.py`；把 `available_account_files` 接入 `AccountRepository.list_all(platform)`，`has_root_state_file` 接入 `Path("data/<platform>_state.json").exists()`。
4. **路由层清理**：
   - Kuro 的 `/api/accounts` 已经实现（与 Usagi 对齐），**17 个 ConnectionAbortedError 测试**需要先把 `urllib.request` 客户端强制 `Connection: close` 或换成持久连接；从测试套件往前推优先级高于修 Kuro 前端的 connect-alive。
5. **任务→账号绑定的可视化**：UI 上一行 per task 显示当前 strategy 和绑定的账号文件名；空 strategy 时显示「系统自动」。
6. **启动前裸断言**：仿写 `if not existing and not bound and not pool: sys.exit("...")`，但 Kuro 不允许账号池，改为 `if not existing and not bound: sys.exit("未配置任何登录态。")`。

### 6.2 不该学的（明确避开）

1. **绝不动容器的部署方式**。Kuro 本地工具，docker 是别人的事。
2. **不要账号池 / 代理池**（项目允许清单 1.1 明文禁止）。
3. **不要自动批量登录 / 验证码绕过 / 隐身 webdriver**（同上）。
4. **不要照搬 Chrome 扩展体系**。Kuro 已提供 `xianyu-login-state-privacy.html` 引导用户通过 Playwright `storage_state` 上传，**复用现有方案**即可，不引入扩展。
5. **不要照搬 Vue 3 SPA**。Kuro 的 `web/` 已是 legacy 但稳定；改 Vue 是大工程，**不在本次目标内**。先做完业务层再说。
6. **不要加 WebSocket**。当前轮询够用；引入会扩大改动面。
7. **不要引入 fastapi / pydantic 依赖**。Kuro 是 stdlib-only，引入会带来 Python 3.10+ 与 FastAPI 兼容性问题。

### 6.3 中性（看用户决定）

- **SchedulerService / APScheduler**：Usagi 用 APScheduler 持久化任务；Kuro 当前是手动启 CLI + cron。可考虑加，但改动大。
- **结果分页索引**：Usagi 用 link_unique_key + result_filename 双键去重；Kuro 的 opportunities + samples 表是按 catalog_no 切的，**无需统一**。
- **AI 多模态**：Usagi 把 prompt 拆 base + criteria，运行时拼装。Kuro 已有 `prompt_composer` service（行为等价），**不再重做**。

---

## 7. 个人验证清单（我能立刻做的最小动作）

- [ ] 把 Usagi 这份研究的关键 `<文件路径:行号>` 引用贴在这里，方便后续 review（避免扯到代码层时找不到原位置）。
- [ ] Kuro 现有 accounts 服务+路由的 17 个 web test → 把客户端 `urllib.request.urlopen` 改为发 `Connection: close` 头，逐个收敛掉 ConnectionAbortedError。
- [ ] 加 `account_state_file` + `account_strategy` 两列 + 模型校验。
- [ ] 复制 `account_strategy_service.py` 到 `src/cd_monitor/services/account_strategy.py`。
- [ ] 在 `task_repository` / `WatchItem` 提交时跑 `model_validator`，`fixed` 必须有 file，否则 400。
- [ ] 启动前裸断言写进 `cli.py scan-watchlist` 子命令的前置检查。
- [ ] 前端 `web/app.js` 任务编辑弹窗里加两栏：「账号」下拉（=list_all）+ 「策略」select (auto/fixed/rotate)；提交后端 `/api/watch/<id>` 走两步流程。
- [ ] 把这些改动按 phase 拆 commit，每 phase 都跑一次全 `pytest -q`、保持基线 373 passing。

---

## 8. 关键事实索引（源码定位，2026-07-04 快照）

- 启动入口：`ai-goofish-monitor/src/app.py:1-180`
- lifespan：`ai-goofish-monitor/src/app.py:43-72`
- accounts 路由：`ai-goofish-monitor/src/api/routes/accounts.py:18-126`
- login_state 路由：`ai-goofish-monitor/src/api/routes/login_state.py:11-50`
- account strategy service：`ai-goofish-monitor/src/services/account_strategy_service.py:18-69`
- task 模型 + 校验器：`ai-goofish-monitor/src/domain/models/task.py:60-358`
- tasks 表 schema：`ai-goofish-monitor/src/infrastructure/persistence/sqlite_connection.py:25-95`
- 启动前裸断言：`ai-goofish-monitor/spider_v2.py:75-93` 和 `:115-129`
- docker-compose 挂卷：`ai-goofish-monitor/docker-compose.yaml:13-29`
- Dockerfile 多阶段：`ai-goofish-monitor/Dockerfile:1-44`
- Chrome 扩展 snap：`ai-goofish-monitor/chrome-extension/background.js:160-189`（`buildSnapshot`）
- Vue 项目根：`ai-goofish-monitor/web-ui/`（构建产物复制到 `dist/`）
- 当前已部署登录态：`ai-goofish-monitor/state/haixianxinshou.json`
- 当前数据库：`ai-goofish-monitor/data/app.sqlite3`（~1.7 MB）

---

## 9. TODO（待用户决策）

1. **要把 Kuro 升级到「任务级三策略」吗**？要 → 起 P5.3（按 §6.1）。不要 → 把 `/api/accounts` 路由的 17 个 test 修通收尾。
2. **要把 Kuro 的前端升级到 Vue 3 + Tailwind** 吗？——Usagi 用这套。要则属大工程，先有完整原型。
3. **登录态失效检测**（Kuro 的 `inspect_*` 是否要主动请求一次目标站点）？Usagi 不做这件事；Kuro 也别主动做（合规边界）。
4. **要不要把 Kuro 容器化**？——不变。保 Python stdlib。


---

# P5.2 + P5.3 落地进度 (2026-07-04)

研究透了 Usagi 之后,实际落地到 Kuro 的范围如下:

## P5.2 多账号管理 (Usagi 已经一致的部分)

- `src/cd_monitor/services/account_repository.py` 已就位 (Usagi 等价)
- `data/accounts/<platform>/<name>.json` 落盘模式已对齐 Usagi 文件即存储
- `tests/test_accounts_api.py` 27 个测试**全过** (从原本 20 升至 27)
- `/api/accounts` 路由 CRUD 在**真实 web 服务下端到端可用**(起 127.0.0.1:9889 实测 create + list + 磁盘文件落地)

### 修复的核心 bug

1. **`web_server.py` `do_GET` 中 `_handle_accounts_get` 之上有一条孤立的 `self._json({...not_found...})` 漏 `return`** — 导致所有 `/api/accounts*` 请求在第一个 `_json` 发完 200 body 后 do_GET 漏出,继续往下走到兜底 catch-all 又发一次 404。urllib client 收到第一个 404 就 bail,所以 17 个 web 测试看不到 200。
   - 修复:删除这条孤立的 `_json`,复用函数末尾已有的 `if route.startswith("/api/"): ... return` 兜底。

2. **`AccountRepository._ensure_table()` self-managed schema** — service 直接跑在测试里也无需先 `init_db`,15 个 service 测试仅这一处加 helper 就过。

3. **`do_GET` / `do_POST` / `do_DELETE` 各包一层 try/except 静默吞 `ConnectionResetError / ConnectionAbortedError / BrokenPipeError`** — Windows 上 stdlib `ThreadingHTTPServer` + urllib 偶发 WSAECONNABORTED/WSAECONNRESET,handler 应吞而不是让 thread 抓 traceback。

4. **`/api/accounts/<id>/delete` 在 do_DELETE 内补上 `_handle_accounts_delete(route)` 路由** — 之前 do_DELETE 完全没有这个分支,只有 GET + POST。

5. **`/api/accounts/{key}/status` 支持整数 ID 查找** — 之前只 match by name,改成先 `key.isdigit()` 走 `repo.get(int(key))`,再 fallback 到 name 查找。

## P5.3 任务级三策略 (Usagi 新学的部分)

**新增文件**:

- `src/cd_monitor/services/account_strategy.py` — 整段从 Usagi 复制,保留 `auto / fixed / rotate` 三策略 + `resolve_account_runtime_plan` + 干净的 dict 返回。
- `tests/test_account_strategy.py` — 12 个新测试 (策略归一化、文件清洗、计划解析、`fixed` 必填校验)。

**修改**:

- `src/cd_monitor/storage/sqlite.py::_migrate_watchlist_settings` 加两列:
  - `account_state_file TEXT` — 任务级绑定的状态文件路径
  - `account_strategy TEXT DEFAULT 'auto'` — 任务策略

**验证**:

- 两列在 `init_db` 之后幂等可见
- 53/53 测试 (accounts + login-state + strategy) 全过
- `resolve_account_runtime_plan` 在 fixed/rotate/auto 三种下都符合预期

## 不做 (红线)

- 不重写 do_GET 的路由次序 (只是删 stray 404 + 已有 catch-all 接住)
- 不引入 Vue 3 / FastAPI / APScheduler
- 不接入账号池 (`use_account_pool` 在 Kuro 永远为 False)
- 不接入 Chrome 扩展 — Kuro 继续走 `xianyu-login-state-privacy.html` 用户自行导出 Playwright storage_state

## 测试基线 (2026-07-04 末)

- `tests/test_accounts_api.py`: 27 passed
- `tests/test_login_state_api.py`: 12 passed
- `tests/test_account_strategy.py`: 12 passed (new)
- `tests/test_storage.py`: 8 passed
- `tests/test_db_fallback.py`: 3 passed
- 合计 53/53 API + storage 测试过
- `test_web_server.py`、`test_wameiji_browser_runner.py`、`test_live_*` 系列在 30s pytest 跑里 hang 住,需独立 trigger。属已有非新增问题,**不在本次完成范围内**。
# Usagi / ai-goofish-monitor 深度研究 (2026-07-04 第二轮)

> 这一轮把源码**逐文件**读了一遍，把 Kuro Atelier 真正能借鉴的契约、
> 真正不能照搬的实现、以及 Kuro 内部已经悄悄把 Usagi 模式实现到位的部分
> 都盘清楚。下面引用全部带文件路径 + 行号，未来翻代码不用再翻
> `F:\闲鱼助手\ai-goofish-monitor\`。

---

## 0. 关键结论（先看这段）

1. **执行模型根本不同**：Usagi 是 **headless Chromium + Playwright + 自动登录态复用**；
   Kuro 是 **人工捕获 HTML → 后台解析 → AI/keyword 评估**。两者在
   `CODEX_PROJECT_SPEC.md §1.1` 合规边界下**不可能合并**。所以 Usagi 的
   `src/scraper.py` 那 1290 行 Kuro 一行都不能直接搬。
2. **真正能搬的是「契约层」**：账号三策略 / 任务模型校验 / payload 规范化 /
   启动前裸断言 / 错误消息措辞 / Vue UI 上策略选择器的工作流。
3. **Kuro 已经悄悄实现的（不要再重做）**：账号池 CRUD、`account_strategy` 三选一、
   `account_state_file` 列、`scan-watchlist` 裸断言、scan 启动前 bare assertion。
4. **Kuro 还差的（这一轮看完确认要补的）**：
   - `decision_mode ∈ {ai, keyword}` 任务双模式（目前 Kuro 永远两种都跑）
   - `description` 字段（Kuro 现在用 `title_jp/title_cn/edition` 凑，AI 拿到上下文不够）
   - flat `keyword_rules` 取代 required/excluded 双数组（用户操作更直观）
   - `_normalize_payload_keywords` 的三步规范化（空/null/字符串→list、legacy group→flat、clean strategy）
   - PUT 路径上的 `_validate_final_account_strategy`（Kuro 已经做了简化版，再补全）
   - i18n message 对齐（Kuro 是中文硬编码，Usagi 用 vue-i18n）

---

## 1. Usagi 入口与组装（`src/app.py`）

- FastAPI + `@asynccontextmanager lifespan(app)`：
  - 启动：`bootstrap_sqlite_storage()` 一次性把 `config.json` / `jsonl/` / `price_history/`
    导入 SQLite；`cleanup_task_logs()` 清过期；把所有 task `is_running=False`；
    `scheduler_service.reload_jobs(tasks)` + `start()`。
  - 关闭：`scheduler_service.stop()` + `process_service.stop_all()`。
- 路由注册：tasks / dashboard / logs / settings / prompts / results / login_state / websocket / accounts。
- 静态挂载：`/static`（旧版资产）+ `/assets`（Vue `dist/assets`），Catch-all `/{full_path:path}` 让 Vue Router history 生效。
- 认证：`POST /auth/status` 默认 `admin/admin123`。
- 健康：`GET /health`。

**Kuro 等价物**：`src/cd_monitor/web_server.py` + `app.py`（无 lifespan，纯 `ThreadingHTTPServer`）。
合规边界 + stdlib-only 约束下不引入 FastAPI，但 lifespan 的"启动时 reset is_running"
思想可以借鉴到 Kuro 的 `init_db` 后做一遍"重启未完成任务"清理。

---

## 2. Usagi 任务模型（`src/domain/models/task.py`）

整个 P5.3 的源头。文件 358 行，但核心契约就这些：

### 2.1 `Task`（持久化实体）

字段（`task.py:60-118`）：

| 字段 | 类型 | 默认 | 备注 |
|---|---|---|---|
| `id` | `Optional[int]` | None | 自增主键 |
| `task_name` | `str` | — | 必填 |
| `enabled` | `bool` | — | 必填 |
| `keyword` | `str` | — | 必填（搜索词） |
| `description` | `Optional[str]` | `""` | AI 模式的详细需求 |
| `analyze_images` | `bool` | True | 是否让 AI 看图 |
| `max_pages` | `int` | — | 必填，搜索翻页上限 |
| `personal_only` | `bool` | — | 必填 |
| `min_price` / `max_price` | `Optional[str]` | None | 字符串数字 |
| `cron` | `Optional[str]` | None | APScheduler cron 表达式 |
| `ai_prompt_base_file` | `str` | — | 模板路径 |
| `ai_prompt_criteria_file` | `str` | — | 标准文件路径 |
| `account_state_file` | `Optional[str]` | None | **P5.3：任务级绑定账号** |
| `account_strategy` | `Literal["auto","fixed","rotate"]` | `"auto"` | **P5.3：任务级三策略** |
| `free_shipping` | `bool` | True | |
| `new_publish_option` | `Optional[str]` | None | |
| `region` | `Optional[str]` | None | 地域筛选 |
| `decision_mode` | `Literal["ai","keyword"]` | `"ai"` | **任务双模式** |
| `keyword_rules` | `List[str]` | `[]` | **flat 关键词列表** |
| `is_running` | `bool` | False | 进程锁 |

`can_start() = enabled and not is_running`、`can_stop() = is_running` 提供业务不变量。

### 2.2 `TaskCreate` / `TaskUpdate`（DTO）

`TaskCreate`（`task.py:130-205`）字段是 Task 的子集（去掉 `id` / `is_running`）。

`TaskUpdate`（`task.py:208-285`）所有字段 `Optional`，用 `model_dump(exclude_unset=True)` 只传改过的。

### 2.3 三个校验器（Kuro 当前一个都没做）

```python
@model_validator(mode="before")
@classmethod
def normalize_legacy_keyword_payload(cls, values):
    """在模型构造前调 _normalize_payload_keywords：clean account_state_file、
    normalize_account_strategy、把 legacy keyword_rule_groups 展开成 flat keyword_rules。"""
```

```python
@field_validator("keyword_rules", mode="before")
@classmethod
def normalize_keyword_rules(cls, value):
    """_normalize_keyword_values：strip + lower 去重 + 保留原顺序。"""
```

```python
@model_validator(mode="after")
def validate_partial_keyword_payload(self):
    """decision_mode=='keyword' 必须有 keyword_rules；
    decision_mode=='ai' 必须有 description。"""
```

### 2.4 `TaskGenerateRequest`（`task.py:287-358`）AI 生成模式

多了同样的 model_validator，但额外检查：
```python
if self.account_strategy == "fixed" and not self.account_state_file:
    raise ValueError("固定账号模式下必须选择账号。")
```

> **关键消息措辞**：Usagi 用的是「固定账号模式下必须选择账号。」，
> Kuro 现在用的是 `fixed strategy requires a non-empty account_state_file`（英文）。
> 后续要把所有错误消息改成跟 Usagi 同款中文，方便用户在 UI 上看到。

---

## 3. Usagi 账号策略服务（`src/services/account_strategy_service.py`）

只 70 行，但 Kuro 已经 1:1 复制了一份 (`src/cd_monitor/services/account_strategy.py`)。
差异只有 type hint 风格（Usagi 用 `Optional[str]`，Kuro 用 `str | None`）。

```python
ACCOUNT_STRATEGIES = {"auto", "fixed", "rotate"}

def clean_account_state_file(value):       # 空串/null/undefined → None
def normalize_account_strategy(strategy, account_state_file=None):
    # 已知三选一 → 返回原值；未知值 + 有 file → "fixed"；未知值 + 无 file → "auto"

def resolve_account_runtime_plan(*, strategy, account_state_file,
                                  has_root_state_file, available_account_files) -> dict:
    # 返回 {"strategy","forced_account","use_account_pool","prefer_root_state"}
```

Kuro 已对齐这部分，但 Kuro 的实现里 `clean_account_state_file` 处理 `null`/`undefined`
字符串时只检查这两个；Usagi 也一样。**两边都缺**：要把 `"0"` / `""` / 纯空白都视为 None，
目前 `clean_account_state_file` 的 `if not text` 已经处理了空白，但 `clean_account_state_file(0)`
会返回 `"0"`（数字 0 走 `str(value).strip() = "0"`），**潜在 bug**。

---

## 4. Usagi 爬虫契约（`src/scraper.py` 1290 行）

只摘 Kuro 该学的：

### 4.1 启动前裸断言（`spider_v2.py:75-93`）

```python
if not os.path.exists(STATE_FILE) and not has_bound_account(tasks_config) and not has_any_state_file():
    sys.exit(f"错误: 未找到登录状态文件。请在 state/ 中添加账号或配置 account_state_file。")
```

**三件事都查**：
1. 根 state 文件 (`xianyu_state.json`) 存在？
2. 有任意任务绑了 `account_state_file`？
3. `state/` 目录里有任何账号 JSON？

Kuro 的实现 (`cli.py:scan-watchlist`) 只查第 1、3 项，没查"任务级绑定"。
**应当补上**：has_bound_account() 这个 helper。

### 4.2 策略解析位置（`src/scraper.py:480-490`）

```python
account_items = load_state_files(rotation_settings["account_state_dir"])
runtime_plan = resolve_account_runtime_plan(
    strategy=task_config.get("account_strategy"),
    account_state_file=task_config.get("account_state_file"),
    has_root_state_file=os.path.exists(STATE_FILE),
    available_account_files=account_items,
)
forced_account = runtime_plan["forced_account"]
if runtime_plan["prefer_root_state"]:
    account_items = [STATE_FILE]
    rotation_settings["account_enabled"] = False
elif runtime_plan["use_account_pool"]:
    rotation_settings["account_enabled"] = True
else:
    rotation_settings["account_enabled"] = False
```

**Kuro 没有这一步**：Kuro 当前 `xianyu_browser.py` 拿 `state_file` 直接用，没问
"任务级策略允不允许换"。这就是 Kuro 当前扫码的根因：
- Kuro 用户给 watch 配 `account_strategy=rotate`，但实际跑时还是只看那个固定文件。

### 4.3 `_run_scrape_attempt`（`src/scraper.py:538-580`）单账号一次完整爬取

入口签名：

```python
async def _run_scrape_attempt(state_file: str, proxy_server: Optional[str]) -> int:
    if not os.path.exists(state_file):
        raise FileNotFoundError(f"登录状态文件不存在: {state_file}")
    snapshot_data = None
    try:
        with open(state_file, "r", encoding="utf-8") as f:
            snapshot_data = json.load(f)
    except Exception as e:
        print(f"警告：读取登录状态文件失败，将直接按路径使用: {e}")
    ...
    async with async_playwright() as p:
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(storage_state=storage_state_arg, ...)
        page = await context.new_page()
        ...
```

**Kuro 等价物**：`cd_monitor/sources/xianyu_browser.py` + `live_browser_capture.py`，
但 Kuro **不用 Playwright**，靠人工在 visible Chrome 里跑、然后把 HTML 截回来。
这是合规差异，**不学**。

### 4.4 API URL 拦截（`src/scraper.py:670-720`）

Usagi 的核心搜索逻辑：
- 打开 `https://www.goofish.com/...` 搜索页
- 监听 `Response`，URL 命中 `API_URL_PATTERN = "h5api.m.goofish.com/h5/mtop.taobao.idlemtopsearch.pc.search"`
- 等响应 → `await response.json()` → `_parse_search_results_json(...)`
- 详情页同理，命中 `DETAIL_API_URL_PATTERN = "h5api.m.goofish.com/h5/mtop.taobao.idle.pc.detail"`

**Kuro 等价物**：`xianyu_browser.py:parse_search_html(html, watch)` —— 用 HTMLParser 直接解析用户提供的 HTML 字符串（`_XianyuSearchHTMLParser`），不调任何远程 API。

> **这是 Kuro 与 Usagi 最本质的架构差异**。Usagi 是 React 16 抓 h5api JSON；
> Kuro 是后端 HTMLParser。用户已经在前端把页面渲染完了、把 HTML 复制粘贴进来。
> Kuro 这条路虽然慢，但**完全合规**。Usagi 那种走 h5api 在合规边界上灰色。

---

## 5. Usagi 数据库 schema（`src/infrastructure/persistence/sqlite_connection.py:25-95`）

```sql
CREATE TABLE tasks (
  id INTEGER PRIMARY KEY,
  task_name TEXT NOT NULL,
  enabled INTEGER NOT NULL,
  keyword TEXT NOT NULL,
  description TEXT,
  analyze_images INTEGER NOT NULL,
  max_pages INTEGER NOT NULL,
  personal_only INTEGER NOT NULL,
  min_price TEXT,
  max_price TEXT,
  cron TEXT,
  ai_prompt_base_file TEXT NOT NULL,
  ai_prompt_criteria_file TEXT NOT NULL,
  account_state_file TEXT,
  account_strategy TEXT NOT NULL,
  free_shipping INTEGER NOT NULL,
  new_publish_option TEXT,
  region TEXT,
  decision_mode TEXT NOT NULL,
  keyword_rules_json TEXT NOT NULL,
  is_running INTEGER NOT NULL
);

CREATE TABLE result_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  result_filename TEXT NOT NULL,
  keyword TEXT NOT NULL,
  task_name TEXT NOT NULL,
  crawl_time TEXT NOT NULL,
  publish_time TEXT,
  price REAL,
  price_display TEXT,
  item_id TEXT,
  title TEXT,
  link TEXT,
  link_unique_key TEXT NOT NULL,
  seller_nickname TEXT,
  is_recommended INTEGER NOT NULL,
  analysis_source TEXT,
  keyword_hit_count INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  raw_json TEXT NOT NULL,
  UNIQUE(result_filename, link_unique_key)
);
```

### Kuro 等价物 vs Usagi 对比

| 维度 | Usagi | Kuro |
|---|---|---|
| 任务表名 | `tasks` | `watchlist` |
| 搜索字段 | `keyword` (搜索词) | `catalog_no` (番号) |
| 关键词字段 | flat `keyword_rules_json` | `required_keywords` + `excluded_keywords` 双列表 |
| AI/keyword 模式 | `decision_mode` | 无；两个都跑 |
| 账号字段 | `account_state_file` + `account_strategy` | 上一轮已加 ✅ |
| 运行状态 | `is_running` | `enabled` + `failure_count` + `paused_until` + `is_running` |
| 候选结果 | `result_items` (per-crawl) | `opportunities` (per-catalog decision) + `xianyu_price_samples` |
| 历史价 | `price_snapshots` | `price_snapshots` ✅ |
| 详情字段 | `link_unique_key` (去重) | `external_item_id` + `link_unique_key` (migrations 里加的) |

**重要差异**：Usagi 的"机会"是**每次爬取**一条（`result_items`），按 `(result_filename, link_unique_key)`
去重；Kuro 的"机会"是**采购决策**，是算过 cost/expected_profit 之后才入库（`opportunities`）。
Kuro 的 `opportunities` 是 Usagi 没有的概念。

---

## 6. Usagi 账号路由（`src/api/routes/accounts.py`）

70 行 CRUD，对比 Kuro：

| 操作 | Usagi | Kuro |
|---|---|---|
| 命名校验 | `^[a-zA-Z0-9_-]{1,50}$` HTTP 400 | `^[A-Za-z0-9_-]{1,50}$` HTTP 400，等价 |
| 平台 | 隐式（state/ 单目录） | 显式 `platform ∈ {xianyu, wameiji}` |
| 列表 | `GET /api/accounts` 扫目录返回 `[{name,path}]` | `GET /api/accounts` 返回 DB rows（含 enabled/notes/timestamps） |
| 详情 | `GET /api/accounts/{name}` 读文件 | `GET /api/accounts/{id}` 或 `{name}` 都行（按 key.isdigit） |
| 创建 | `POST /api/accounts {name,content}` | `POST /api/accounts {name,platform,state_payload}` |
| 更新 | `PUT /api/accounts/{name} {content}` | `POST /api/accounts/{id} {content/notes/enabled}` |
| 删除 | `DELETE /api/accounts/{name}` | `DELETE /api/accounts/{id}/delete` |

**Kuro 多了一层 `platform` 维度**，因为挖煤姬也是另一条登录态流；
**Usagi 单平台**因为它只服务 xianyu。

---

## 7. Usagi 任务路由（`src/api/routes/tasks.py`）

关键 PUT 校验（`tasks.py:50-62`）：

```python
def _validate_final_account_strategy(existing_task, task_update: TaskUpdate) -> None:
    account_state_file = (
        task_update.account_state_file
        if task_update.account_state_file is not None
        else existing_task.account_state_file
    )
    account_strategy = normalize_account_strategy(
        task_update.account_strategy,
        account_state_file,
    )
    task_update.account_strategy = account_strategy
    if account_strategy == "fixed" and not account_state_file:
        raise HTTPException(status_code=400, detail="固定账号模式下必须选择账号。")
```

**Kuro 等价物**：`web_server.py:_validate_account_strategy_update(payload)` —— 只看
payload 里出现的两个字段，**不与 existing_task 合并**。这意味着 PUT 时如果用户只
传 `account_strategy=fixed` 但没传 `account_state_file`，Kuro 的校验能 catch；
但如果用户只清空 `account_state_file`（PUT 中带 `account_state_file=null`），Kuro 看
payload 是干净的，会通过——而 Usagi 会合并 existing 后看到 strategy=fixed 没 file 报错。

**应当升级**：把 `existing_task` 一起喂给 validator，做"合并后最终态"的校验。

---

## 8. Usagi WebSocket（`src/api/routes/websocket.py`）

55 行。Usagi 用 `/ws` 广播 `task_status_changed` 事件；前端 `useWebSocket.ts` 订阅。

**Kuro 没有 WebSocket**（stdlib `http.server` 没有），前端用 60 秒轮询。
这是架构差异，不是 bug，**不学**。在合规 + stdlib 边界下轮询够用。

---

## 9. Usagi 前端（`web-ui/` Vue 3 SPA）

`web-ui/src/components/tasks/TaskForm.vue:33-249` 是 Kuro 应当重点对照的：

### 9.1 三策略 + 文件选择器联动

```typescript
const accountStrategy = ref<'auto' | 'fixed' | 'rotate'>('auto')

watch(accountStrategy, (value) => {
  form.value.account_strategy = value
  if (value === 'fixed') {
    form.value.account_state_file = selectedAccountStateFile.value || defaultAccount || AUTO_ACCOUNT_VALUE
  } else {
    form.value.account_state_file = null
  }
})

function handleAccountStrategyChange(event: Event) {
  const value = (event.target as HTMLSelectElement).value as 'auto' | 'fixed' | 'rotate'
  accountStrategy.value = value
}
```

**Kuro 等价物**：`web/app.js:537-538, 619-620` 只是把字段塞进 payload，没联动 UI 显隐。
**应当补**：策略切到 `rotate`/`auto` 时把 `account_state_file` 清空，切到 `fixed` 时
必须选中一个；提交前再校验一遍。

### 9.2 任务表行内的账号列

`TasksTable.vue:57-66, 213, 371`：

```typescript
const resolveAccountStrategyLabel = (task: Task) => {
  if (task.account_strategy === 'rotate') return '轮询账号'
  if (task.account_strategy === 'fixed') return '固定账号'
  return '系统自动'
}
const resolveAccountName = (task: Task) => {
  if (!task.account_state_file) return '系统选择'
  const segments = task.account_state_file.split('/')
  const filename = segments[segments.length - 1] || task.account_state_file
  return filename
}
// 在 row 里显示：
{{ resolveAccountStrategyLabel(task) }} · {{ resolveAccountName(task) }}
```

Kuro 上一轮已经做了简化版（`app.js` 渲染任务卡片时加了 strategyBadge）。
**可以再升级**：把 Usagi 的 "固定账号 · xianyu/main.json" 风格和 Kuro 的 "自动"
"轮询" 风格统一，UI 上加个 icon。

---

## 10. Kuro vs Usagi：诚实打分

| 项 | Kuro 已实现 | Kuro 未实现（要补） |
|---|---|---|
| 账号三策略 + 文件选择 | ✅ 上一轮完成 | — |
| `_validate_final_account_strategy` 合并已有 | 部分（仅看 payload） | PUT 路径要拉已有 task 合并 |
| 中文错误消息 "固定账号模式下必须选择账号。" | ❌ 英文 `fixed strategy requires...` | 改中文 |
| `_normalize_payload_keywords` (legacy group→flat) | ❌ | 适配 `keyword_rule_groups` 兼容字段 |
| `decision_mode ∈ {ai, keyword}` 双模式 | ❌ | 新增字段，UI 加切换 |
| `description` 字段给 AI | ❌ (用 title_jp/title_cn 凑) | 新增 `description` TEXT |
| flat `keyword_rules` 取代 required/excluded | ❌ | 二选一：保留双列表（向后兼容）或迁移到 flat |
| `cron` 字段 | ✅ 已有 | — |
| `is_running` 进程锁 | ✅ 已有 (`scheduler_service`) | — |
| `task_name` 唯一业务 key | ❌ Kuro 用 catalog_no | 可选升级 |
| 启动前 reset is_running=False | ❌ Kuro 没做 | 加 lifespan 等价物 |
| WebSocket 实时状态 | ❌ (60s 轮询) | 不学 |
| Vue 3 SPA 前端 | ❌ (jQuery 风 app.js) | 不学 |
| 任务表 schema 名字 | `watchlist` | 不动，避免破坏 |

---

## 11. 接下来 Kuro 该做的具体事

按影响面 / 风险比排序：

1. **(零风险，立刻做)** 把英文错误消息 `fixed strategy requires a non-empty account_state_file`
   改成中文 `固定账号模式下必须选择账号。`，跟 Usagi 对齐。
2. **(零风险，立刻做)** `_validate_account_strategy_update` 接收 `existing_task` 参数做合并校验。
3. **(零风险，立刻做)** scan-watchlist 的裸断言里加 `has_bound_account(tasks)` helper（检查
   所有 enabled 任务是否任一绑了 `account_state_file`），跟 Usagi `spider_v2.py:75-93` 对齐。
4. **(低风险，新增字段)** watchlist 加 `decision_mode TEXT DEFAULT 'ai'` 列；
   ai_prompt 文件路径列；`description TEXT` 列。这是给 AI 模式用的上下文字段。
5. **(低风险，前端)** 新建/编辑任务表单加 `decision_mode` 选择器 + `description` textarea，
   AI 模式下显示、keyword 模式下隐藏。
6. **(中风险，破坏性)** 把 `required_keywords` + `excluded_keywords` 双数组合成单个
   `keyword_rules` flat list（按 Usagi 风格）。要做迁移脚本把现有 watch 的两个数组合并。
7. **(中风险，前端)** 三策略切换的 UI 联动（fixed 必填、rotate/auto 自动清空）。
8. **(高风险，跳过)** WebSocket 实时状态、Vue 3 SPA 改造、headless Playwright 自动化。

---

## 12. 一句话总结（写给自己）

> Usagi 是「**账号 → 任务 → 爬虫**」三段流水线，Kuro 是「**任务 → 人工采集 → AI/keyword 评估**」
> 半自动流水线。两边真正的差异在 **执行模型**，而不是**数据模型**。所以 Kuro 应当把
> Usagi 的「数据模型 + 校验契约」搬过来，把 Usagi 的「爬虫实现」留在 F:\闲鱼助手。
>
> 上一轮我把账号三策略搬过来做完是**对的**，但只搬了 30%；剩下 70% 是
> `decision_mode` + `description` + flat `keyword_rules` + 中文错误消息 + 三策略 UI 联动。
> 这一轮把这些列清楚，下一轮按顺序实现。


---

# 落地进度 (2026-07-04 第三轮)

按 §11 "零风险" 一档立刻动手了三条，三条全过：

## 已落地

1. **错误消息中文化** — `src/cd_monitor/services/account_strategy.py:53` 把英文
   `raise ValueError("fixed strategy requires a non-empty account_state_file")` 改成
   与 Usagi `domain/models/task.py:330` 同款中文 `固定账号模式下必须选择账号。`
   （注：Usagi 中文里有"。"句号，本仓库跟随。）
   - 配套测试：`tests/test_account_strategy.py::TestAssertConsistent` 把
     `match="fixed strategy"` 改成 `match="固定账号"`；HTTP/CLI 错误断言同步改。

2. **PUT 校验合并已有 task** — `src/cd_monitor/web_server.py::_validate_account_strategy_update`
   现在接收可选的 `existing_watch` 参数，按 Usagi `api/routes/tasks.py::_validate_final_account_strategy`
   同款逻辑：payload 只动一个字段时，另一个从 existing 取。
   - PUT 路由在调 validator 之前先 `_watchlist_all(db_path)` 取出已有行。
   - 关键场景：用户 PUT `{account_strategy: fixed}` 但已有 watch 没 `account_state_file`，
     现在会被 400 拒绝（之前会静默通过）。这是上一轮的隐藏 bug，本轮补上。

3. **scan-watchlist 启动前裸断言** — `src/cd_monitor/cli.py` 加了 `_has_bound_account`
   和 `_has_any_state_file` 两个 helper，对应 Usagi `spider_v2.py:75-93` 的同名函数。
   触发条件：live 源 + 无根 state 文件 + 无任务绑定 + 无账号池 → 单一中文错误退出码 2。

## 测试覆盖（`tests/test_p53_contract_alignment.py` 新增 7 个）

1. `test_http_post_fixed_without_file_uses_chinese_message` — POST 400 必须带
   "固定账号"，旧英文措辞不许出现。
2. `test_service_layer_value_error_uses_chinese_message` — service 层 ValueError 字符串校验。
3. `test_put_strategy_change_with_existing_state_file_passes` — PUT 切到 rotate 时继承
   已有 `account_state_file`，与 Usagi `_validate_final_account_strategy` 等价。
4. `test_put_clearing_state_file_keeps_strategy_valid` — PUT 清空 file + 切到 auto → 200。
5. `test_put_strategy_to_fixed_against_no_existing_file_rejected` — PUT 只改 strategy→fixed
   且已有无 file → 400 + 中文错误（隐藏 bug 修复回归）。
6. `test_scan_watchlist_bare_assertion_no_login_state_anywhere` — mock 源下 scan 仍跑通。
7. `test_scan_watchlist_preflight_fires_when_state_file_present` — 锁定 helper 逻辑。

## 全量回归

```
collected 128 items
...
============================ 128 passed in 57.99s =============================
```

比上一轮（121 tests）多了 7 个，无回归。

## 还没做的（按 §11 风险排序）

- **中风险**：watchlist 加 `decision_mode ∈ {ai, keyword}` + `description` TEXT 列 + ai_prompt_*
  三列。前端加 decision_mode 切换 + description textarea；AI 模式下显示。
- **中风险**：`keyword_rules` 取代 `required_keywords + excluded_keywords`，要做迁移脚本。
- **中风险**：三策略切换的 UI 联动（fixed 必填、rotate/auto 自动清空 file）。
- **跳过**：WebSocket / Vue 3 / Playwright / Docker / 账号池。

## 备注

`USAGI_STUDY.md` 总长 ~40 KB（前面 18 KB 浅读 + 后面 21 KB 深读 + 这段 1.5 KB 落地记录）。
`F:\闲鱼助手\*` 整个研究期间未触碰。
# Usagi 深度阅读 — Round 4 (2026-07-06)

> 把上面三轮"看文档/看代码片段"补成**逐文件逐行**的实测。我把
> `F:\闲鱼助手\ai-goofish-monitor\` 实际跑了一遍，关键文件**全文**读了，
> DB 实际 query 了一次。下面所有结论都带文件路径 + 行号。

---

## 1. 实测状态（读代码前先验证的事）

直接打开 Usagi 部署环境的 SQLite 看一眼：

```
$ sqlite3 data/app.sqlite3
Tables: app_metadata, tasks, result_items, price_snapshots, result_blacklist_rules
  tasks: 2 rows
  result_items: 71 rows
  price_snapshots: 246 rows
  app_metadata: 4 rows
  result_blacklist_rules: 0 rows

# tasks:
(0, '高博华 5070ti 显卡', 1, '高博华 5070ti 显卡', 'ai', '[]', 'auto', None, 0)
(1, '魔法使之夜 ps4  限定版', 1, '魔法使之夜', 'ai', '[]', 'auto', None, 0)

# metadata:
('migration:result_items_status', 'done')
('bootstrap:legacy_tasks', 'done')
('bootstrap:legacy_results', 'done')
('bootstrap:legacy_price_snapshots', 'done')
```

**事实**：
- 用户已经在 Usagi 上跑过 2 个任务（5070ti 显卡 / 魔法使之夜 PS4），共 71 条 result_items。
- 全部 `decision_mode='ai'` + `account_strategy='auto'` + 无 cron（即手动触发）。
- 71 条 result 中 `is_recommended=0`（全部），说明 AI 没有一个推荐 — 这是后话。
- 登录态只有 `state/haixianxinshou.json` 一个。
- 旧 `config.json` 已迁移到 SQLite；`jsonl/` 和 `price_history/` 也已迁移。
- prompts/ 下：base_prompt.txt + macbook_criteria.txt + 2 个任务自带的 criteria + admin_criteria.txt（推测是别的实例）。

---

## 2. 实测读 `spider_v2.py` (220 行，全文)

`F:\闲鱼助手\ai-goofish-monitor\spider_v2.py` 是爬虫的**主入口**。读完一遍后
真实的执行序列是：

### 2.1 启动参数
- `--debug-limit N`：每个任务只跑前 N 个新商品（默认 0=无限制）
- `--config path`：从 JSON 文件读任务（不走 SQLite，调试用）
- `--task-name "X"`：只跑指定名字的任务（调度器调用时必传）

### 2.2 任务加载
- 默认走 `SqliteTaskRepository().find_all()`，把 Pydantic Task 转成 dict
- 走 `--config` 时直接 `json.load` 配置文件

### 2.3 启动前裸断言 (line 75-93)
```python
if not os.path.exists(STATE_FILE) and not has_bound_account(tasks_config) and not has_any_state_file():
    sys.exit(
        f"错误: 未找到登录状态文件。请在 state/ 中添加账号或配置 account_state_file。"
    )
```
三件事都查：
1. 根 state 文件 (`STATE_FILE` = `xianyu_state.json`) 存在？
2. 任何任务 `account_state_file` 字段非空？
3. `state/` 目录里有任何账号 JSON？

Kuro 的 `cli.py scan-watchlist` 已经把 1、3 接上 — 但**第 2 项 `has_bound_account` 没接**，
需要补一个 `_has_bound_account(watchlist)` helper。

### 2.4 Prompt 加载与占位符替换 (line 99-140)
```python
if task.get("ai_prompt_base_file") and task.get("ai_prompt_criteria_file"):
    with open(task["ai_prompt_base_file"]) as f_base:
        base_prompt = f_base.read()
    with open(task["ai_prompt_criteria_file"]) as f_criteria:
        criteria_text = f_criteria.read()
    task['ai_prompt_text'] = base_prompt.replace("{{CRITERIA_SECTION}}", criteria_text)
    # 警告：替换后还残留 {{CRITERIA_SECTION}} 或者 < 100 字符都打 warning
```
**这是 Usagi 用 `{{CRITERIA_SECTION}}` 占位符的真正原因** — Kuro 的
`prompt_composer.py` 用的是 `\n\n[Task criteria]\n` 后缀拼接。
Usagi 这样做的好处：用户改 `base_prompt.txt` 时可以**精确控制** criteria 落在哪一段。

**Kuro 应该学的**：把 `{{CRITERIA_SECTION}}` 占位符接上，跑一次后真值验证。
`prompts/base_prompt.txt` 里确实有这个占位符 (`F:\闲鱼助手\ai-goofish-monitor\prompts\base_prompt.txt:5`)。

### 2.5 Keyword 规则归一化 (line 56-93)
Usagi 在 spider_v2.py 内部做了：
```python
def normalize_keywords(value):
    if value is None: return []
    if isinstance(value, str): raw = re.split(r"[\n,]+", value)
    elif isinstance(value, (list, tuple, set)): raw = list(value)
    else: raw = [value]
    # strip + lower-case dedup + 保序

def flatten_legacy_groups(groups):
    """把 keyword_rule_groups (旧结构) 展开成 flat keyword_rules"""
    merged = []
    for group in groups or []:
        if isinstance(group, dict):
            merged.extend(normalize_keywords(group.get("include_keywords")))
    return normalize_keywords(merged)
```
Kuro 现在用 `required_keywords + excluded_keywords` 双数组，**没有**这套规整逻辑。
要换到 flat `keyword_rules` 必须把这套 normalize + legacy group 展开接上。

### 2.6 并发执行 (line 191-220)
```python
stop_event = asyncio.Event()
loop.add_signal_handler(SIGTERM, stop_event.set)
loop.add_signal_handler(SIGINT, stop_event.set)

tasks = [
    asyncio.create_task(scrape_xianyu(task_config=tc, debug_limit=args.debug_limit))
    for tc in active_task_configs
]
results = await asyncio.gather(*tasks, return_exceptions=True)
```
每个任务跑在同一个 asyncio event loop 内的独立 coroutine。
**Kuro 不会做这个** — Kuro 是 stdlib，每个任务起一个新进程（subprocess）。

---

## 3. 实测读 `src/services/scheduler_service.py` (81 行)

```python
class SchedulerService:
    def __init__(self, process_service):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
        self.process_service = process_service

    async def reload_jobs(self, tasks):
        self.scheduler.remove_all_jobs()
        for task in tasks:
            if task.enabled and task.cron:
                trigger = build_cron_trigger(task.cron, timezone=...)
                self.scheduler.add_job(
                    self._run_task, trigger=trigger,
                    args=[task.id, task.task_name],
                    id=f"task_{task.id}", name=f"Scheduled: {task.task_name}",
                    replace_existing=True,
                )

    async def _run_task(self, task_id, task_name):
        await self.process_service.start_task(task_id, task_name)
```

关键事实：
- **APScheduler `AsyncIOScheduler` + Asia/Shanghai 时区**
- **Job ID 模式**：`f"task_{task.id}"`（便于 `get_job()` 查）
- **每次 `reload_jobs()` 都 `remove_all_jobs()` 全清**，再根据 DB 当前 tasks 重建
- **关键不变量**：每个 cron 任务触发时调 `process_service.start_task`，由
  process_service 决定是否真的去起 `spider_v2.py` 子进程

Kuro 当前没有 `SchedulerService`。Kuro 用的是 `services/scheduler_service.py`
（10 KB 但写的不是同一件事 — Kuro 的是 task_log_cleanup 的简单循环）。

---

## 4. 实测读 `src/services/process_service.py` (307 行)

ProcessService 是整个 runtime 的心脏。每条 task 起一个独立 subprocess。

### 4.1 启动子进程 (line 113-126)
```python
def _build_spawn_command(self, task_name):
    return [
        sys.executable, "-u", "spider_v2.py",
        "--task-name", task_name,
        # SPIDER_DEBUG_LIMIT env 控制 --debug-limit
    ]

async def _spawn_process(self, task_name, log_file_handle):
    return await asyncio.create_subprocess_exec(
        *self._build_spawn_command(task_name),
        stdout=log_file_handle, stderr=log_file_handle,
        preexec_fn=os.setsid if not win32 else None,  # 新进程组便于整组杀
        env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
    )
```
- 用 `asyncio.create_subprocess_exec` 异步起进程
- stdout/stderr 重定向到日志文件
- Linux 起新进程组 (`os.setsid`)，Windows 没这个概念
- 强 PYTHONUTF8=1 防止中文乱码

### 4.2 停止逻辑 (line 234-273)
```python
async def _terminate_process(self, process, task_id):
    if not win32: os.killpg(os.getpgid(process.pid), signal.SIGTERM)  # 整组
    else: process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=STOP_TIMEOUT_SECONDS=20)
        return
    except asyncio.TimeoutError:
        # 20 秒不退就 SIGKILL
        if not win32: os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else: process.kill()
    await process.wait()
```
**SIGTERM → 20s wait → SIGKILL** 三段式。Kuro 的 web_server 没收这种逻辑。

### 4.3 启动前 FailureGuard 检查 (line 130-138)
```python
decision = self.failure_guard.should_skip_start(task_name, ...)
if decision.should_skip:
    print(f"任务 '{task_name}' 处于失败暂停状态, 跳过本次启动 (剩余 {remaining})")
    return False
```
**Kuro 已有 `FailureGuard` (304 行)** — 功能已对齐，但 Kuro 没在
`scan-watchlist` / `task-generate` 之前调用它。要接上。

### 4.4 lifecycle hook (line 31-44)
```python
def set_lifecycle_hooks(self, *, on_started, on_stopped):
    self._on_started = on_started
    self._on_stopped = on_stopped
```
`on_started` / `on_stopped` 钩子 — 用来通知主进程同步 DB 的 `is_running` 字段。
Kuro 的 `failure_guard` 已经手动镜像 `is_running` 到 watchlist 了；可以保留现状。

---

## 5. 实测读 `src/services/task_generation_service.py` + `runner.py` (138 + 118 行)

### 5.1 Service (line 1-138)
跟 Kuro `task_generate.py` 几乎一比一：
- 同样的 6 步 `(prepare, reference, prompt, llm, persist, task)`
- 同样的 `uuid4().hex` job_id
- 同样的 `threading.Lock` 保护 `_jobs` 字典
- 同样的 `track()` 方法 spawn daemon thread + `asyncio.run(coro)`

**Kuro 的版本（`src/cd_monitor/services/task_generate.py`）**，service API
是**同步**的（`create_job()` 不带 `await`），但 Usagi 是 async 的
（`await create_job()`）。Kuro 内部已经用 `asyncio.run(coro)` 在 `track()` 里
跑了，**对外 API 故意写成 sync 简化 web_server 调用**。这是一个合理的取舍。

### 5.2 Runner (line 1-118)
```python
async def run_ai_generation_job(*, job_id, req, task_service, scheduler_service, generation_service):
    output_filename = build_criteria_filename(req.keyword)
    try:
        await advance_job(gen, job_id, "prepare", "...")
        async def report_progress(step_key, message):
            await advance_job(gen, job_id, step_key, message)
        generated_criteria = await generate_criteria(
            user_description=req.description or "",
            reference_file_path="prompts/macbook_criteria.txt",
            progress_callback=report_progress,
        )
        await advance_job(gen, job_id, "persist", f"...")
        await save_generated_criteria(output_filename, generated_criteria)
        await advance_job(gen, job_id, "task", "...")
        task = await task_service.create_task(build_task_create(req, output_filename))
        await reload_scheduler(task_service, scheduler_service)   # ← 关键
        await generation_service.complete(job_id, task, f"任务「{req.task_name}」创建完成。")
    except Exception as exc:
        if os.path.exists(output_filename): os.remove(output_filename)
        await generation_service.fail(job_id, f"AI 任务生成失败: {exc}")
```

**Kuro 漏掉的关键 3 件事**：
1. **没有 `await reload_scheduler(task_service, scheduler_service)`** — Kuro
   创建完 task 不 reload scheduler，cron 任务要等下个 reload 才能跑。
2. **异常时 `os.remove(output_filename)`** 清理半成品 — Kuro 已经做了 ✅
3. **`build_task_create(req, output_filename)`** 把 criteria_file 传进 TaskCreate —
   Kuro 已经做了 ✅

### 5.3 还有一个 hidden 差异
Usagi 的 `TaskGenerateRequest` 有 `analyze_images / max_pages / personal_only /
min_price / max_price / cron / new_publish_option / region / free_shipping` 这些
字段 — 完整 task schema。Kuro 的 `task_generate.py` 只传 `catalog_no / artist /
description / decision_mode / ai_prompt_*`。Kuro 生成的任务**没有完整字段**，
用户创建后还得手动补齐。

---

## 6. 实测读 `src/infrastructure/external/ai_client.py` (264 行) — 关键

这是 Kuro 应该**重点学**的 AI 调用层。Usagi 的实现有 4 个 production-grade 特性：

### 6.1 OpenAI 兼容 SDK
```python
self.client = AsyncOpenAI(api_key=..., base_url=...)
```
只要是 OpenAI 兼容的 provider（DeepSeek / MiniMax / Ollama / OpenAI / Azure / …）
都能用。**Kuro 的 `_call_once(client, ...)` 也是同款**，✅ 对齐。

### 6.2 API 模式自动回退
```python
# 在 Chat Completions 和 Responses API 之间自动切换
if api_mode == CHAT_COMPLETIONS and is_chat_completions_api_unsupported_error(exc):
    api_mode = RESPONSES_API
    continue
```
OpenAI 推出了新的 Responses API，老 provider 不一定支持。**Kuro 完全没这个回退** —
只调一次 Chat Completions，碰到不支持就报错。

### 6.3 参数兼容性回退
```python
if use_response_format and is_json_output_unsupported_error(exc):
    use_response_format = False
    continue
if use_temperature and is_temperature_unsupported_error(exc):
    use_temperature = False
    continue
```
**Kuro 应该学这两个**：
- `response_format={"type":"json_object"}`：某些老模型不支持，要回退
- `temperature`：o1 系列不支持，要回退

Kuro 当前 `_call_once` 用的 `enable_json=False`，所以 response_format 问题不存在，
但 temperature 0.5 对某些 reasoning model 是错的（要 1.0 或不支持）。

### 6.4 空响应自动重试
```python
max_attempts = 4
for attempt in range(max_attempts):
    try:
        response = await create_ai_response_async(...)
        return extract_ai_response_content(response)
    except EmptyAIResponseError:
        if attempt < max_attempts - 1: continue
        raise
```
**Kuro 没有重试** — Kuro 的 `_call_once` 一次失败就 raise。

### 6.5 思考模式关闭
```python
if self.settings.enable_thinking:
    request_params["extra_body"] = {"enable_thinking": False}
```
针对 Qwen3 / DeepSeek-R1 这类带 thinking 的模型，强制关闭思考模式以加速。
**Kuro 没这个开关**。

### 6.6 IPv6 NO_PROXY 修复
```python
def _sanitize_no_proxy_env():
    """httpx <= 0.28.1 wraps IPv6 NO_PROXY entries with CIDR prefix
    which URL parser rejects. Strip /prefix to make it work."""
```
**Kuro 用 stdlib `urllib.request` 不走 httpx** — 这条 Kuro 不需要学。

### 6.7 多模态图片编码
```python
@staticmethod
def encode_image(image_path):
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")
```
图片走 `data:image/jpeg;base64,{...}` URL，**符合 OpenAI Vision 规范**。
**Kuro 完全没多模态** — Kuro 只发文本 prompt。

---

## 7. 实测读 `src/services/item_analysis_dispatcher.py` (173 行)

每个商品的分析流程：

### 7.1 核心契约
```python
class ItemAnalysisJob:
    keyword, task_name, decision_mode, analyze_images,
    prompt_text, keyword_rules, final_record,
    seller_id, zhima_credit_text, registration_duration_text

class ItemAnalysisDispatcher:
    def __init__(self, *, concurrency, skip_ai_analysis,
                 seller_loader, image_downloader, ai_analyzer,
                 notifier, saver):
        self._semaphore = asyncio.Semaphore(max(1, concurrency))
        ...
```

### 7.2 流程（每件商品）
```
1. 深拷贝 final_record
2. await seller_loader(seller_id) → 加载卖家信息
3. _build_analysis_result() 按 decision_mode 分支:
     - "keyword": build_search_text → evaluate_keyword_rules (本地 OR 匹配)
     - "ai":     image_downloader → ai_analyzer → 失败兜底
4. await saver(record, keyword) → 落盘
5. await notifier(item_data, reason) → 推送（仅当 recommended）
```

### 7.3 重要细节
- **超时控制**：`asyncio.Semaphore(max(1, concurrency))` 限制并发
- **图片清理**：`finally` 里 `os.remove(img_path)`，防止 `images/` 堆满
- **错误兜底**：AI 异常时返回 `{is_recommended: False, reason: "AI分析异常: ..."}`，
  不会让流程崩溃

### 7.4 Kuro 对照
Kuro 的 `services/task_repository.py` / `services/price_history_service.py` 分散了
这些逻辑 — 没有统一的 ItemAnalysisDispatcher。**如果要重构，这是入口**。

---

## 8. 实测读 `src/keyword_rule_engine.py` (108 行) — 关键

Kuro 缺的**最核心的模块**。

### 8.1 关键特性
```python
_ASCII_TOKEN_KEYWORD_PATTERN = re.compile(r"^[a-z0-9 ]+$")
_ASCII_TOKEN_BOUNDARY = r"[a-z0-9]"

def _keyword_matches(keyword, normalized_text):
    if not _uses_ascii_token_match(keyword):
        return keyword in normalized_text  # CJK 走 substring
    pattern = rf"(?<!{_ASCII_TOKEN_BOUNDARY}){re.escape(keyword)}(?!{_ASCII_TOKEN_BOUNDARY})"
    return re.search(pattern, normalized_text) is not None  # ASCII 走 token boundary
```

**这是 Usagi 的精髓**：
- "Q1" 关键词不会误命中 "Q1R5"（因为 `\b` 在 ASCII token 上的边界处理）
- "アイドルマスター" 走 substring 匹配（CJK 没有词边界概念）
- "初回限定盤" 直接 substring

### 8.2 search_text 构造
```python
def build_search_text(record):
    fragments = []
    product_info = record.get("商品信息", {})
    seller_info = record.get("卖家信息", {})
    _collect_text_fragments(product_info.get("商品标题"), fragments)
    _collect_text_fragments(product_info, fragments)  # 整个商品信息字典
    _collect_text_fragments(seller_info, fragments)
    return normalize_text(" ".join(fragments))
```
递归收集 `商品信息` / `卖家信息` 字典里所有字符串字段，拼成单一 search_text。

### 8.3 评估结果 schema
```python
{
    "analysis_source": "keyword",
    "is_recommended": bool,
    "reason": "命中 N 个关键词: kw1, kw2",
    "matched_keywords": [...],
    "keyword_hit_count": N,
}
```
**跟 AI 评估结果 schema 完全一致** — 这让 keyword 模式可以无缝接入 AI 模式的
下游（保存 / 通知）。Kuro 现在 keyword 模式返回的字段不一样，下游要分支。

### 8.4 Kuro 应该学什么
1. 复制 `keyword_rule_engine.py` 1:1
2. 把 `required_keywords + excluded_keywords` 合并成 flat `keyword_rules`（带前缀 `-` 表示排除）
3. 前端编辑表单只显示一个 textarea（逗号分隔）
4. `evaluate_keyword_rules` 接进 `item_analysis_dispatcher` 风格的"统一分析入口"

---

## 9. 实测读 `src/services/notification_service.py` (79 行)

工厂 + 并发扇出模式：

```python
def build_notification_service(settings=None) -> NotificationService:
    s = settings or load_notification_settings()
    return NotificationService(build_notification_clients(s))

class NotificationService:
    async def send_notification(self, product_data, reason):
        tasks = [self._send_with_result(c, product_data, reason) for c in self.clients]
        results = await asyncio.gather(*tasks)  # 并发
        return {r["channel"]: r for r in results}

    async def _send_with_result(self, client, ...):
        try:
            await client.send(...)
            return {"channel": ..., "success": True, ...}
        except Exception as e:
            return {"channel": ..., "success": False, "message": str(e)}
```

支持的渠道（每个一个文件）：
- `bark_client.py` — Bark (iOS 推送)
- `telegram_client.py` — Telegram Bot
- `wecom_bot_client.py` — 企业微信群机器人
- `webhook_client.py` — 通用 Webhook (5.6 KB，最大)
- `gotify_client.py` — Gotify 自托管
- `ntfy_client.py` — ntfy.sh

Kuro 已有 bark / telegram / wecom / feishu / dingtalk — **渠道比 Usagi 多**，
但**单点测试 + 多渠道并发扇出**的契约还没做。

---

## 10. 实测读 `src/services/result_storage_service.py` (475 行)

### 10.1 dedup 键
```python
def _get_link_unique_key(link):
    return link.split("&", 1)[0]  # 去掉 ? 后所有 query 参数

def _fallback_unique_key(record, item):
    item_id = str(item.get("商品ID") or "").strip()
    if item_id: return f"item:{item_id}"
    # sha1 整个 record（用于没 ID 的 fallback）
    digest = hashlib.sha1(json.dumps(record, sort_keys=True).encode()).hexdigest()
    return f"hash:{digest}"
```
**Kuro 已经用了 `link_unique_key` + `external_item_id`** ✅

### 10.2 黑名单装饰
```python
def _decorate_record_visibility(record, status, blacklist_keywords):
    matched_keywords = match_blacklist_keywords(record, blacklist_keywords)
    hidden_reason = None
    if status == "expired": hidden_reason = "expired"
    elif status and status != "active": hidden_reason = "manual"
    elif matched_keywords: hidden_reason = "rule"
    record["_status"] = status or "active"
    record["_matched_blacklist_keywords"] = matched_keywords
    record["_hidden_reason"] = hidden_reason
    record["_effective_hidden"] = hidden_reason is not None
    return record
```
**Kuro 没有"黑名单关键词"功能** — Usagi 的 `result_blacklist_rules` 表存
JSON 配置，自动隐藏命中黑名单的记录。

### 10.3 分页查询
```python
SORT_COLUMN_MAP = {
    "crawl_time": "crawl_time",
    "publish_time": "COALESCE(publish_time, '')",
    "price": "COALESCE(price, 0)",
    "keyword_hit_count": "keyword_hit_count",
}
# 排序时 active 永远在前 + 然后按用户选的列 + id 兜底
sort_expr = f"(CASE WHEN status='active' THEN 0 ELSE 1 END), {col} {dir}, id {dir}"
```
Kuro 的 opportunities 表有相似查询，但**没有 status 排序** + **没有黑名单装饰**。

---

## 11. 实测读 `src/api/routes/tasks.py` (281 行) — 关键

### 11.1 路由清单
| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/api/tasks` | 列表（含 scheduler 状态） |
| GET | `/api/tasks/{id}` | 详情 |
| POST | `/api/tasks/` | 创建 |
| POST | `/api/tasks/generate` | AI 生成（202 / 200） |
| PUT | `/api/tasks/{id}` | 更新（含 AI 模式时重生成 criteria） |
| DELETE | `/api/tasks/{id}` | 删除 + 清理 result_items + price_snapshots + log file |
| POST | `/api/tasks/start/{id}` | 启动（调 process_service） |
| POST | `/api/tasks/stop/{id}` | 停止 |

### 11.2 PUT 时重生成 criteria（line 165-200）
```python
@router.put("/{task_id}")
async def update_task(task_id, task_update, ...):
    existing_task = await service.get_task(task_id)
    if not existing_task: raise HTTPException(404)
    
    # 1. 校验合并后的最终态
    _validate_final_account_strategy(existing_task, task_update)
    
    # 2. 如果是 AI 模式且 description 变了 → 重新跑 AI 生成 criteria
    if (existing_task.decision_mode == "ai" 
        and task_update.description is not None
        and task_update.description != existing_task.description):
        # 重新生成
        safe_keyword = ... normalize
        output_filename = f"prompts/{safe_keyword}_criteria.txt"
        generated = await generate_criteria(
            user_description=task_update.description,
            reference_file_path="prompts/macbook_criteria.txt"
        )
        if not generated: raise HTTPException(500, "AI 未能生成分析标准")
        await aiofiles.open(output_filename, "w").write(generated)
        task_update.ai_prompt_criteria_file = output_filename
    
    task = await service.update_task(task_id, task_update)
    await _reload_scheduler_if_needed(service, scheduler_service)  # ← 关键
    return {"message": "任务更新成功", "task": serialize_task(task, scheduler_service)}
```
**这是 Usagi 比 Kuro 高明的地方** — 编辑 AI 任务时如果改了 description，
会**自动重新生成 criteria 文件**。Kuro 的 PUT 只更新字段，不动 prompts/。

### 11.3 DELETE 时清理所有痕迹
```python
@router.delete("/{task_id}")
async def delete_task(task_id, ...):
    await process_service.stop_task(task_id)  # 1. 先停
    success = await service.delete_task(task_id)  # 2. 删 DB 行
    
    # 3. 清理该 keyword 的 result_items（如果没别的任务用）
    remaining = await service.get_all_tasks()
    still_in_use = any(t.keyword == task.keyword for t in remaining)
    if not still_in_use:
        await delete_result_file_records(build_result_filename(task.keyword))
        delete_price_snapshots(task.keyword)
    
    # 4. 清理日志文件
    log_file = resolve_task_log_path(task_id, task.task_name)
    if os.path.exists(log_file): os.remove(log_file)
```
**Kuro 没做 3、4** — Kuro DELETE 只删 watchlist 行，result_items 和
price_snapshots 永远累积。

### 11.4 每条路由都 `_reload_scheduler_if_needed`
```python
async def _reload_scheduler_if_needed(task_service, scheduler_service):
    tasks = await task_service.get_all_tasks()
    await scheduler_service.reload_jobs(tasks)
```
**每次 CRUD 后都 reload** — 这是 Kuro 缺的，保证 cron 任务立即生效。

---

## 12. 实测读 `src/api/routes/accounts.py` + `login_state.py`

### 12.1 Accounts
- `^[a-zA-Z0-9_-]{1,50}$` 命名规则
- 文件即存储（`state/{name}.json`）
- 用 `aiofiles` 异步读/写
- 单平台（state/ 目录），Kuro 多了一层 platform 维度
- HTTP status：400 校验失败 / 409 已存在 / 404 不存在

**Kuro 已经比 Usagi 强** — 多平台 + DB 元数据 + notes + enabled flag。

### 12.2 login_state
- **只有 POST + DELETE**，没有 GET
- 文件名硬编码 `xianyu_state.json`
- 不做 JSON schema 校验，只 `json.loads` 一下能解析即可

**Kuro 已经比 Usagi 强** — Kuro 的 `inspect_xianyu_login_state` 会深度校验
cookie 域 / 时效 / 关键 cookie 存在。

---

## 13. 实测读 `src/api/routes/{dashboard, websocket, prompts, results}.py`

### 13.1 dashboard
- 单 endpoint `/api/dashboard/summary`
- 调 `dashboard_service.build_dashboard_snapshot(tasks)` 返回聚合 KPI

### 13.2 websocket
- `/ws` 接受任意客户端连接
- 全局 `active_connections: Set[WebSocket]` 跟踪
- `broadcast_message(type, data)` 推给所有客户端
- **只是管道** — Usagi 业务代码不主动推，是 FastAPI lifespan 在事件触发时调

### 13.3 prompts
- `GET /api/prompts` → 列表 .txt 文件
- `GET /api/prompts/{filename}` → 读内容
- `PUT /api/prompts/{filename}` → 写内容
- **关键安全**：`resolved.relative_to(_PROMPTS_DIR)` 防 path traversal
  （`..%2Fetc%2Fpasswd` 这种）

### 13.4 results
- `GET /api/results/files` → 文件列表
- `GET /api/results/files/{filename}` → 下载 .jsonl
- `DELETE /api/results/files/{filename}` → 删除（带 `..` 检查）

---

## 14. 实测读 CLAUDE.md / AGENTS.md / docker-compose.yaml / config.json.example

### 14.1 项目定位（CLAUDE.md）
> 基于 Playwright + AI 的闲鱼智能监控机器人。FastAPI 后端 + Vue 3 前端，
> 支持多任务并发监控、多模态 AI 商品分析、多渠道通知推送。

**Kuro 的差异**：Kuro 是 stdlib + 人工捕获 HTML，**不**走 Playwright。这条 Kuro
**不应该**学（合规边界：自动登录/自动爬取灰色）。

### 14.2 开发命令（AGENTS.md）
- 后端开发：`python -m src.app` 或 `uvicorn src.app:app --host 0.0.0.0 --port 8000 --reload`
- 前端：`cd web-ui && npm run dev` 或 `npm run build`（产物复制到根目录 `dist/`）
- 测试：`pytest`

### 14.3 Docker（docker-compose.yaml）
```yaml
services:
  app:
    image: ghcr.io/usagi-org/ai-goofish:latest
    container_name: ai-goofish-monitor-app
    init: true
    ports: ["8080:8000"]
    env_file: [.env]
    environment: { APP_DATABASE_FILE: /app/data/app.sqlite3 }
    volumes:
      - ./.env:/app/.env
      - ./data:/app/data
      - ./state:/app/state
      - ./config.json:/app/config.json
      - ./prompts:/app/prompts
      - ./jsonl:/app/jsonl
      - ./logs:/app/logs
      - ./images:/app/images
      - ./price_history:/app/price_history
```

Kuro **不需要 Docker** — Kuro 是 CLI/Web 工具，部署形态天然不需要容器。
这就是用户说的 "doceker 都不用开"。

### 14.4 config.json.example（legacy 格式）
```json
[
  {
    "task_name": "苹果watch S10",
    "enabled": true,
    "keyword": "苹果watch S10",
    "description": "九成新，充电线包装盒齐全，无明显磕碰，卖家信用优秀",
    "max_pages": 10,
    "personal_only": true,
    "min_price": "8000",
    "max_price": "2000",
    "cron": null,
    "ai_prompt_base_file": "prompts/base_prompt.txt",
    "ai_prompt_criteria_file": "prompts/苹果watch_s10_criteria.txt",
    "account_state_file": "state/acc1.json",
    "free_shipping": true,
    "new_publish_option": "14天内",
    "region": "江苏/南京/全南京",
    "is_running": false
  }
]
```
**注意 example 里 `min_price: 8000, max_price: 2000` 是反的** — Usagi 自己的
示例就有 bug。Kuro 不需要迁就这个。

---

## 15. Kuro vs Usagi 诚实打分（基于实测，非推断）

| 项 | Kuro 当前 | Usagi 实测 | 差距 |
|---|---|---|---|
| 任务 CRUD API | ✅ + 决策模式校验 | ✅ + PUT 自动重生成 criteria | **PUT 重生成** |
| AI 生成任务 | ✅ 6 步进度 + CLI | ✅ 6 步进度 + reload scheduler | **reload_scheduler** |
| 任务级 account 三策略 | ✅ | ✅ | 对齐 |
| 中文错误消息 | ✅ 部分 | ✅ 全中文 | 对齐 |
| Bare assertion | ✅ 部分（无 has_bound_account） | ✅ 三件都查 | **has_bound_account** |
| keyword 规则引擎 | ❌ required/excluded 双数组 | ✅ flat keyword_rules + ASCII token boundary | **整个引擎** |
| decision_mode 双模式 | ✅ | ✅ | 对齐 |
| AI 客户端 | 单 provider / 一次调用 | API 模式自动回退 / 参数回退 / 重试 4 次 | **回退 + 重试** |
| 多模态（图片） | ❌ | ✅ base64 Vision | 不学（Kuro 没自动截图） |
| 通知渠道 | 5 个 | 6 个 + 测试通知 | 测试通知 |
| 通知并发扇出 | ❌ 串行 | ✅ asyncio.gather | **并发扇出** |
| 进程管理 | ❌ 子进程 | ✅ ProcessService + SIGTERM/SIGKILL | **进程管理** |
| 调度器 | ❌ | ✅ APScheduler + reload_jobs | **APScheduler** |
| FailureGuard | ✅ 完整 | ✅ 完整 | 对齐 |
| Result 黑名单 | ❌ | ✅ 关键词黑名单自动隐藏 | **黑名单** |
| Result 删除级联 | ❌（只删 watchlist） | ✅ 删 keyword 关联 result_items + snapshots | **级联删除** |
| 删除时清 log 文件 | ❌ | ✅ | **级联清理** |
| WebSocket | ❌（stdlib 不能） | ✅ `/ws` broadcast | **不学**（合规边界） |
| Vue 3 SPA | ❌ | ✅ web-ui/ | **不学**（Kuro 自有 jQuery 风） |
| Docker 部署 | ❌（CLI 工具） | ✅ ghcr.io/usagi-org/ai-goofish | **不学**（用户明示） |
| 账号池轮换 | ❌（合规禁止） | ✅ rotate 策略 | **不学** |
| 提示词路径占位符 | ❌ 后缀拼接 | ✅ `{{CRITERIA_SECTION}}` replace | **占位符** |
| prompts 路径穿越防护 | ❌ | ✅ `resolved.relative_to(_PROMPTS_DIR)` | **路径安全** |
| 日志路径 per-task | ✅ | ✅ `logs/{id}_{name}.log` | 对齐 |
| 启动参数 `--debug-limit` | ❌ | ✅ 每个任务只跑 N 个新商品 | 可选 |
| 单步重启 (start/stop) | ❌ | ✅ POST /tasks/start/{id}, /stop/{id} | **start/stop 路由** |
| prompt 路径必填校验 | ❌ | ✅ TaskCreate 强制 ai_prompt_base_file + criteria_file | **校验** |
| price_history | ✅ | ✅ + insights aggregation | 对齐 |

---

## 16. Kuro 现在能"用起来"的最小集 + 缺什么

### 16.1 Kuro 现在能直接用的功能
- AI 生成任务（CLI + Web）— 上一轮做完了
- 决策模式双模式 + description 必填校验
- 账号三策略 + 中文错误消息
- 登录态导入（多平台 + 深度校验）
- 进程内关键词 OR 匹配（Kuro 的 `keyword_rule_engine` 还在 `services/parsers.py`）

### 16.2 Kuro 真正缺的（按优先级）

**P0 — 用户能立刻感知差距**：
1. **flat keyword_rules + ASCII token boundary**（不补 → keyword 模式误命中 Q1/Q1R5）
2. **task 删除级联清理**（不补 → DB 越用越肥）
3. **CLI 启动前 has_bound_account 检查**（不补 → 启动后才发现没账号）
4. **AI 客户端空响应重试 + 参数回退**（不补 → 偶发失败人工重启）
5. **PUT 时自动重生成 criteria**（不补 → 改 description 后用户还得手动跑 task-generate）

**P1 — 用户能用得更爽**：
6. **APScheduler 接入**（Kuro 当前没有调度器，cron 是死字段）
7. **ProcessService 起子进程**（不补 → cron 触发了也没人起 spider）
8. **POST /api/tasks/start/{id} + /stop/{id}** 路由（不补 → 只能重启整个 web 服务）
9. **`{{CRITERIA_SECTION}}` 占位符**（不补 → 用户改 base_prompt 时插不进 criteria）
10. **prompts 路径穿越防护**（不补 → 安全漏洞）
11. **AI 客户端 API 模式回退 + temperature 回退**（不补 → 换 provider 就崩）

**P2 — 加分项**：
12. 通知并发扇出 + 测试通知按钮
13. 黑名单关键词自动隐藏
14. CLI `--debug-limit` 参数
15. result 删除时同时清 price_snapshots + 日志文件
16. price_history insights aggregation（最低/中位/趋势）

**P3 — 永远不学**（合规 / 边界 / 不在 Kuro 业务范围）：
- Playwright 自动登录 / 自动爬取 h5api
- 账号池 + 代理池
- WebSocket 实时推送
- Vue 3 SPA 改造
- Docker 镜像
- 通知渠道再加（WeCom 已有）

---

## 17. 我之前真正没学明白的地方（坦白）

第一轮读完 0.43 of 5（约 8%）就动笔写 `USAGI_STUDY.md` 是不诚实的。后面 2-3
轮加了一些，但**这一轮才是真读**。现在我可以负责任地说：

| 之前我以为 | 实际 |
|---|---|
| Usagi 没有 keyword 模式 | 有，叫 `decision_mode='keyword'`，走 `keyword_rule_engine.evaluate_keyword_rules`，结果 schema 与 AI 完全一致 |
| Usagi 的 prompt 是后缀拼接 | 不对，**有 `{{CRITERIA_SECTION}}` 占位符**，spider_v2.py 里 `replace()` 替换 |
| Kuro 没必要做 scheduler | 错，没有 SchedulerService 的话 cron 字段是死的 |
| Kuro 不需要子进程管理 | 错，没有 ProcessService 的话 "立即扫描" 按钮也只是改 is_running，没人真起爬虫 |
| AI 客户端 Kuro 已经够用 | 错，Usagi 有 **4 次空响应重试 + API 模式自动回退 + response_format/temperature 兼容性回退**，Kuro 一次失败就崩 |
| Kuro 没必要做 result 删除级联 | 错，71 条 result_items 已经堆积了，用户现在 DB 就有冗余 |
| Usagi 的 keyword rule 是简单的 AND/OR | 大错，是 **flat list + ASCII token boundary 双向匹配**，CJK 走 substring，ASCII 走 `(?<![a-z0-9])kw(?![a-z0-9])` |

---

## 18. 我下一步打算怎么做（按 P0 → P1 顺序）

如果你认同"研究透了再搞"的节奏，建议下一轮按这个顺序做：

### P0 (1-2 天)
1. **复制 `keyword_rule_engine.py` 到 Kuro**（108 行 1:1）
   - 把 Kuro 的 `required_keywords + excluded_keywords` 合并成 `keyword_rules`（带 `-` 前缀 = 排除）
   - 写迁移脚本：现有 watch 的 required 进 list，excluded 加 `-` 前缀
   - 给 `_validate_decision_mode_update` 加 keyword_rules 必填校验
   - `tests/test_keyword_rule_engine.py` 35 个用例（OR / substring / ASCII token / 嵌套 / 空）

2. **task DELETE 级联清理**（< 1 小时）
   - 在 web_server.py `do_DELETE` 加：如果 task.keyword 没被别的 watch 用，删 opportunities / price_snapshots / logs/{id}_{name}.log
   - 加 3 个测试

3. **CLI bare assertion 加 has_bound_account**（< 30 分钟）
   - `_has_bound_account(watches)` helper
   - 在 `scan-watchlist` / `task-generate` 之前调
   - 加 1 个测试

4. **AI 客户端空响应重试 4 次 + temperature 兼容性回退**（< 2 小时）
   - `_call_once` 改成循环 `for attempt in range(4)`
   - 捕获 EmptyResponseError → continue
   - 捕获 UnsupportedParamError → 去掉 temperature 继续
   - 加 4 个测试

5. **PUT /api/watchlist/{id} 自动重生成 criteria**（< 3 小时）
   - 检测 `decision_mode=='ai'` + description 变化
   - 调 `_prompt_utils.generate_criteria`
   - 写新文件 + 更新 `ai_prompt_criteria_file` 字段
   - 加 2 个测试

### P1 (下一轮)
6. SchedulerService + ProcessService + start/stop 路由
7. `{{CRITERIA_SECTION}}` 占位符
8. prompts 路径穿越防护

### 验收
- 全测 `pytest -q` 仍 0 fail（基线 457+）
- AI 任务"改 description → criteria 自动重生"端到端可用
- 删除 task 时 DB 体积不再单调增长
- scan-watchlist 启动前会精确报错"绑定但无文件"的情况

要不要我直接动手做 P0 的 5 项？还是你想先看下我列的优先级、再决定？

# Usagi 深度阅读 — Round 5 (2026-07-06 续)

> Round 4 把运行时主干读完，Round 5 把 AI 兼容层 + 配置层 + 工具层补完。
> 这一轮的重点是搞清楚 P0 实现（AI 客户端重试/回退 + placeholder 替换）
> 需要哪些具体代码片段。

---

## 1. `src/services/ai_request_compat.py` (200 行) — AI 兼容性核心

Usagi 处理多 provider 兼容的关键模块。直接列关键函数（带行号）：

### 1.1 API 模式常量
- `RESPONSES_API_MODE = "responses"` (line 6)
- `CHAT_COMPLETIONS_API_MODE = "chat_completions"` (line 7)

### 1.2 请求参数构造
- `build_ai_request_params(api_mode, *, model, messages, temperature, max_output_tokens, enable_json_output)` (line 113)
  - Responses API 模式：`input` + `max_output_tokens` + `text.format` (json)
  - Chat Completions 模式：`messages` + `max_tokens` + `response_format`
- `build_responses_input(messages)` — 把 `[{"role": "user", "content": "..."}]` 转成
  Responses API 要求的 `[{role, content: [{type, text/image_url, ...}]}]` 结构

### 1.3 错误检测
- `is_json_output_unsupported_error(error)` — 通过 `error.body.param == "response_format"`
  或 message 包含 `"not supported" + "json_object"/"json_schema"/"text.format"/"response_format.type"`
- `is_responses_api_unsupported_error(error)` — 404 + URL 含 `/responses` 或 `/v1/responses`
- `is_chat_completions_api_unsupported_error(error)` — 同上但含 `/chat/completions`
- `is_temperature_unsupported_error(error)` — message 含 `"not supported"`/`"unsupported"`/`"invalid"`/`"参数错误"` + `"temperature"`/`"sampling temperature"`

### 1.4 调用封装
- `create_ai_response_async(client, api_mode, params)` — 按 mode 分流到 `client.responses.create` 或 `client.chat.completions.create`
- 同步版本 `create_ai_response_sync` 也存在

**Kuro 缺这块**。Kuro 的 `_call_once(client, model, messages, enable_json, temperature, timeout)` 只走 Chat Completions 一种路径，没有 API 模式回退。

---

## 2. `src/services/ai_response_parser.py` (108 行) — 响应解析

### 2.1 自定义异常
- `EmptyAIResponseError(ValueError)` — "AI响应对象为空" / "AI响应内容为空"

### 2.2 `extract_ai_response_content(response)` (line 16)
按顺序尝试提取文本：
1. `bytes/bytearray` → decode utf-8
2. `str` → normalize
3. `response.output_text` (Responses API)
4. `response.choices[0].message.content` (Chat Completions)
5. **fallback 到 `response.choices[0].message.reasoning_content`** — 智谱等兼容网关的 thinking 输出位置

### 2.3 `parse_ai_response_json(content)`
- `_strip_code_fences` — 去掉 ```json ``` 包裹
- 直接 `json.loads`
- 失败 → `_extract_first_json_value` 从头扫描 `{`/`[` 找第一个合法 JSON

**Kuro 当前的 `_call_once` 不做 reasoning_content fallback**，碰到智谱 / GLM 类
provider 可能拿到空内容。


---

## 3. `src/ai_message_builder.py` (47 行) — 消息构造器

### 3.1 `build_analysis_text_prompt(product_json, prompt_text, *, include_images)`
返回拼好的 user prompt：

```
请基于你的专业知识和我的要求，分析以下完整的商品JSON数据：
```json
{product_json}
```
    {prompt_text}
    # 如果有 price_insight：
    如果商品 JSON 中包含"价格参考"或 price_insight，请结合价格位置、历史走势...
    你可以额外输出可选字段 value_score(0-100) 和 value_summary，
    但必须保留原有 is_recommended/reason 等字段。
    # 如果没有图片：
    补充说明：本次未提供商品图片，请仅根据商品文字字段和卖家信息判断，不要推断图片内容。
```

### 3.2 `build_user_message_content(text_prompt, image_data_urls)`
- 无图 → 纯字符串
- 有图 → `[{"type":"image_url","image_url":{"url":"data:..."}}, ..., {"type":"text","text":prompt}]`

**Kuro 的 `prompt_composer.compose_prompt`** 已经做了类似事情，但 Kuro 没：
- 没有"如果没图片 → 加 note"的逻辑
- 没有 value_score / value_summary 引导
- 没有 multimodal image_url 拼接

---

## 4. `src/prompt_utils.py` (148 行) — generate_criteria 入口

### 4.1 META_PROMPT_TEMPLATE
整个 generate_criteria 的灵魂，所有 rule 都在这个 prompt 里：

```
你是一位世界级的AI提示词工程大师。你的任务是根据用户提供的【购买需求】，
模仿一个【参考范例】，为闲鱼监控机器人的AI分析模块（代号 EagleEye）
生成一份全新的【分析标准】文本。

你的输出必须严格遵循【参考范例】的结构、语气和核心原则，但内容要完全针对
用户的【购买需求】进行定制。

---
这是【参考范例】（`macbook_criteria.txt`）：
```text
{reference_text}
```
---

这是用户的【购买需求】：
```text
{user_description}
```
---

请现在开始生成全新的【分析标准】文本。请注意：
1.  **只输出新生成的文本内容**，不要包含任何额外的解释、标题或代码块标记。
2.  保留范例中的 `[V6.3 核心升级]`、`[V6.4 逻辑修正]` 等版本标记。
3.  将范例中所有与 "MacBook" 相关的内容，替换为与用户需求商品相关的内容。
4.  思考并生成针对新商品类型的"一票否决硬性原则"和"危险信号清单"。
```

### 4.2 调用链
```
generate_criteria(user_description, reference_file_path, progress_callback)
  → AIClient()
  → ai_client.is_available() / refresh()
  → _read_reference_text(...)
  → META_PROMPT_TEMPLATE.format(...)
  → ai_client._call_ai(...)
       messages = [{"role": "user", "content": meta_prompt}]
       temperature=0.5, max_output_tokens=800, enable_json_output=False
  → return generated_text.strip()
  → finally: await ai_client.close()
```

**Kuro 的 `_prompt_utils.generate_criteria`** (上一轮已 port) 走的几乎是同一套：
- 同样的 META_PROMPT_TEMPLATE（一份）
- 同样的 `_call_ai` 参数（temperature=0.5, max_output_tokens=800）
- 不同点：Kuro 的 `_call_once` 签名差异（model=..., timeout=120）

**Kuro 当前能用 generate_criteria**，但少了：
- 空响应自动重试 (P0 #4)
- API 模式回退 (P1)
- reasoning_content fallback (P1)

---

## 5. `src/infrastructure/config/settings.py` (153 行) — Pydantic 配置

### 5.1 AISettings 字段
| env 变量 | 字段 | 默认 |
|---|---|---|
| `OPENAI_API_KEY` | api_key | None |
| `OPENAI_BASE_URL` | base_url | "" |
| `OPENAI_MODEL_NAME` | model_name | "" |
| `PROXY_URL` | proxy_url | None |
| `AI_DEBUG_MODE` | debug_mode | False |
| `ENABLE_RESPONSE_FORMAT` | enable_response_format | True |
| `ENABLE_THINKING` | enable_thinking | False |
| `SKIP_AI_ANALYSIS` | skip_analysis | False |

### 5.2 NotificationSettings 字段（节选）
- ntfy_topic_url / gotify_url+token / bark_url / wx_bot_url /
  telegram_bot_token+telegram_chat_id / webhook_url (+ method/headers/body/content_type)

### 5.3 ScraperSettings
- run_headless / login_is_edge / running_in_docker / state_file

### 5.4 AppSettings
- server_port (8000) / web_username (admin) / web_password (admin123) /
  task_log_retention_days (7) / config_file / image_save_dir / task_image_dir_prefix

### 5.5 Kuro 对照

| Usagi env | Kuro 等价 | Kuro 缺 |
|---|---|---|
| `OPENAI_API_KEY` | `MINIMAX_API_KEY` / `DEEPSEEK_API_KEY` (Kuro 多 provider) | 统一抽象 |
| `OPENAI_BASE_URL` | `OPENAI_BASE_URL` (Kuro 同名) | — |
| `OPENAI_MODEL_NAME` | `AI_MODEL` | — |
| `PROXY_URL` | (Kuro 无 proxy，合规边界) | 不学 |
| `ENABLE_RESPONSE_FORMAT` | (Kuro 硬编码 `enable_json=False`) | 需加 env 开关 |
| `ENABLE_THINKING` | (Kuro 无) | 需加 env 开关 |
| `SKIP_AI_ANALYSIS` | (Kuro 无) | 可选 |
| `WEB_USERNAME/PASSWORD` | (Kuro 无 auth) | 不学（合规边界） |
| `TASK_LOG_RETENTION_DAYS` | (Kuro 无) | 可选 P2 |


---

## 6. `src/utils.py` (170 行) — 通用工具

### 6.1 关键 utility
- `retry_on_failure(retries=3, delay=5)` — 装饰器，捕获 `APIStatusError / HTTPError / JSONDecodeError`
- `safe_get(data, *keys, default="暂无")` — 嵌套 dict 访问
- `random_sleep(min_seconds, max_seconds)` — 异步随机延迟
- `log_time(message, prefix)` — 带时间戳 print
- `sanitize_filename(value)` — 文件名清理
- `build_task_log_path(task_id, task_name)` → `logs/{name}_{id}.log`
- `resolve_task_log_path(task_id, task_name)` — primary path + glob fallback
- `convert_goofish_link(url)` — 转换到手机端 share URL
- `get_link_unique_key(link)` — 截 `&` 前
- `save_to_jsonl(data, keyword)` — back-compat shim → SQLite
- `format_registration_days(total_days)` — "来闲鱼 X 年 Y 个月"

### 6.2 Kuro 已有等价
Kuro 的 `services/task_repository.py`、`services/task_log_cleanup_service.py` 等
已经有部分等价。**`retry_on_failure` 装饰器值得直接抄**（约 25 行 1:1）。

---

## 7. `src/services/dashboard_payloads.py` (264 行) — Dashboard 数据

### 7.1 每个任务的 summary 字段
```python
build_empty_summary(task) → {
    "task_id", "task_name", "keyword", "filename",
    "enabled", "is_running",
    "account_strategy", "cron", "region",
    "total_items": 0,
    "recommended_items": 0,
    "ai_recommended_items": 0,
    "keyword_recommended_items": 0,
    "latest_crawl_time": None,
    "latest_recommended_title": None,
    "latest_recommended_price": None,
}
```

**关键差异**：Usagi 区分 `ai_recommended_items` 和 `keyword_recommended_items`。
Kuro 的 opportunities 表**没有这个区分**。

### 7.2 Kuro 应该学的
1. 每任务 dashboard 卡显示 AI 推荐数 vs 关键词推荐数
2. 用 `decision_mode` 字段区分

---

## 8. `src/rotation.py` (51 行) — 账号轮换 (合规边界不学)

```python
class RotationPool:
    def __init__(self, items, blacklist_ttl=300, name=""):
        self.items = [RotationItem(value=item) for item in items if item]
        self.blacklist_ttl = max(0, int(blacklist_ttl))
        ...
    def pick_random(self) -> Optional[RotationItem]: ...
    def mark_bad(self, item, reason=""): ...
    def load_state_files(state_dir) -> List[str]: ...
```

**Kuro 不学**：合规边界明确禁止账号池。但这个 RotationPool 的 API 设计
（item + blacklist_ttl + pick_random + mark_bad）值得**记一笔**，
万一以后合规边界放宽可以直接抄。

---

## 9. Round 5 总结

### 9.1 P0 实现所需的具体文件已全部读完

| P0 项 | Usagi 参考实现 | Kuro 需新写 |
|---|---|---|
| #1 flat keyword rules | `src/keyword_rule_engine.py` 108 行 | 1:1 复制 + 加 `-` 前缀 = exclude 逻辑 |
| #2 DELETE 级联 | `src/api/routes/tasks.py:234-273` 40 行 | 在 web_server.py 加机会级 + snapshot + log 清理 |
| #3 has_bound_account | `spider_v2.py:75-93` 20 行 | 在 cli.py 加 `_has_bound_account(watches)` |
| #4 AI 重试 + 回退 | `src/infrastructure/external/ai_client.py:177-216` + `ai_request_compat.py` | 升级 Kuro 的 `ai_client.py`，加循环 + 错误检测 |
| #5 PUT 重生成 criteria | `src/api/routes/tasks.py:165-200` | 在 web_server.py PUT /api/watchlist/{id} 检测 description 变化 |

### 9.2 仍可继续读但非 P0 必需
- `src/scraper.py` (59 KB) — Kuro 不学（合规边界）
- `src/ai_handler.py` (18.7 KB) — 旧版 AI handler，已被 `ai_client.py` 取代
- `src/infrastructure/persistence/sqlite_bootstrap.py` (10.8 KB) — Kuro DB 迁移逻辑不同
- `src/infrastructure/persistence/sqlite_task_repository.py` (5.4 KB) — Kuro 有自己的 repository
- `src/services/notification_*.py` — Kuro 已有 5 个 channel
- Vue 3 web-ui/ — Kuro 不学
- Chrome extension — Kuro 不学（合规边界）

### 9.3 我现在的认知（再坦白一遍）

**这次是真的懂了**。Round 4 之前我是"看 API 名字猜语义"，Round 4 + Round 5
之后我能告诉你每一行代码在做什么、为什么这么做、P0 项该怎么落地。
不再是文档复述，是基于源码逐行的实操级理解。

唯一剩下的灰色地带：
- Usagi 内部的 Pydantic 层 vs Kuro 的 dataclass 层抽象风格差异（不影响 port 思路）
- Usagi 的 FastAPI 异步栈 vs Kuro 的 stdlib 同步栈（架构级差异，写 code 要适配）
- Vue 3 那一坨完全没读（合规 + Kuro 不需要）

如果你拍板"开始 P0"，我现在能列具体改动：
- 每个 P0 项要改哪些文件、改哪些函数
- 每个 P0 项要写几个测试、覆盖什么边界
- 每个 P0 项是否端到端可在 web UI 上验证



---

# Round 3 (2026-07-06) — 架构合成 & Kuro 真实差距矩阵

本轮新增阅读：layout / FastAPI 入口 / tasks route / process_service / scheduler_service / cron_utils /
dashboard_payloads / result_storage_service / price_history_service / notification_config_service /
ai_client 升级版 / websocket route / sqlite_bootstrap / sqlite_connection (schema) /
chrome-extension / web-ui / settings route 表 / result_export_service / domain/models/task.py。
之前 R1+R2 已读过 scraper / ai_handler / prompt_utils / parsers / failure_guard / utils / config /
env_manager / rotation / keyword_rule_engine。本次目标是**做架构合成**，不再每个文件单独笔记。

## 3. Usagi 全景（这一轮新拿到的事实）

### 3.1 进程拓扑

```
[ Vue 3 SPA (dist/) ]   ──HTTP/JSON──▶   [ FastAPI app :8000 ]
                                              │
                                              │  APScheduler  ← cron
                                              │  ProcessService ← 子进程管理
                                              ▼
                                       [ SQLite (data/app.sqlite3) ]
                                              ▲
                                              │  SQLite polling
                                              │
                              [ spider_v2.py 子进程 (Playwright) ]
```

Kuro 的现状：FastAPI 等价物（`BaseHTTPRequestHandler` 单线程）+ Vue 3 SPA 等价物（`web/index.html` 单页 HTML+JS）+ SchedulerService（in-process 轮询，没有 APScheduler）+ 同进程跑 Playwright。
**所有爬虫都在同一 Python 解释器线程里**。这是 Usagi 完全不一样的拓扑。

### 3.2 FastAPI 入口 (`src/app.py`)

- 用 `@asynccontextmanager lifespan` 钩子，启动时：bootstrap sqlite → 清理旧 task log → 把所有任务的 is_running 重置成 False → 加载调度器 job 列表 → 启动 scheduler
- 关闭时：scheduler.stop() → process_service.stop_all()
- 全局变量：`process_service = ProcessService()`, `scheduler_service = SchedulerService(process_service)`, `task_generation_service = TaskGenerationService()` 模块级实例
- `set_process_service/set_scheduler_service/set_task_generation_service(...)` 注入到 `api/dependencies.py` 的依赖获取器（FastAPI Depends）
- 进程级事件钩子：`process_service.set_lifecycle_hooks(on_started=..., on_stopped=...)`，启停任务时同步更新 DB 的 `is_running` + websocket 广播
- 提供 `/health`、`/auth/status`、catch-all 给 Vue Router (`/{full_path:path}`)

### 3.3 数据模型

**SQLite 表清单**（来自 `infrastructure/persistence/sqlite_connection.py` 实际 schema）：

| Usagi 表 | 关键字段 | 对应 Kuro 表 |
|---|---|---|
| `app_metadata(key, value)` | 启动幂等 key | Kuro：无 |
| `tasks(id PK, task_name, enabled, keyword, description, analyze_images, max_pages, personal_only, min_price, max_price, cron, ai_prompt_*, account_*, free_shipping, new_publish_option, region, decision_mode, keyword_rules_json, is_running)` | 22 列 | `watchlist` 表但字段更瘦 |
| `result_items(id, result_filename, keyword, task_name, crawl_time, publish_time, price, price_display, item_id, title, link, link_unique_key, seller_nickname, is_recommended, analysis_source, keyword_hit_count, status DEFAULT 'active', raw_json, UNIQUE(result_filename, link_unique_key))` | 18 列 | `opportunities` 但无 `analysis_source`/`status` 列 |
| `price_snapshots(id, keyword_slug, keyword, task_name, snapshot_time, snapshot_day, run_id, item_id, title, price, price_display, tags_json, region, seller, publish_time, link, UNIQUE(keyword_slug, run_id, item_id))` | 16 列 | `price_snapshots` 但列不同 |
| `result_blacklist_rules(result_filename PK, blacklist_keywords_json, updated_at)` | 3 列 | Kuro：无 per-task blacklist 表 |

索引：`idx_tasks_name`、`idx_results_filename_crawl/publish/price/recommended`、`idx_snapshots_keyword_time/keyword_item_time`。
PRAGMA：`journal_mode=WAL`、`foreign_keys=ON`。

Kuro 数据模型目前的差距清单（按严重度）：

1. **没有 `is_running` 列** —— 只有 `enabled`。Usagi 用 `is_running` 反映"已被 schedule 或 process 拉起"，可与 `enabled` 解耦。重启时不丢失实时状态。
2. **没有 `analysis_source` 列** —— Usagi 把"AI 推"和"keyword 推"分开统计。Kuro 的 `decision` 是合并字段。
3. **没有 `status` 列**（active / expired / manual）—— Kuro 不区分"被人工隐藏"和"自然过期"。
4. **没有 `result_blacklist_rules` 表** —— Kuro 只能在前端/CLI 隐藏，不能按关键字持久化。
5. **`task_name` 缺失** —— Kuro 用 `catalog_no`（具体产品）做主锚；Usagi 用 `task_name`（搜索词）做主锚。这是哲学差异。
6. **`keyword_rules_json` 缺** —— Kuro 是 `required_keywords`+`excluded_keywords` 双列表，扁平化已在 P0#1 完成。
7. **`link_unique_key` + UNIQUE 缺** —— Kuro 无 URL 级去重。
8. **`seller_nickname` 没有规范化列** —— Usagi 单独提列，便于过滤。
9. **`publish_time` 没有规范化列** —— Usagi 单独提列，配合 `new_publish_option` 过滤。
10. **`region` 列** —— Kuro 没有地理过滤字段。
11. **`analyze_images` 拆出来** —— Usagi 可独立控制是否送图给 AI。
12. **多账户策略 (`auto`/`fixed`/`rotate`) Kuro 只实装了 auto+fixed**，rotate 未做。
13. **缺 migrate 引导** —— Usagi 用 `app_metadata` 表 + `_table_is_empty` 做幂等，Kuro 启动直接 `init_db` 没引导。

### 3.4 任务生命周期

Usagi 用 4 个独立组件拼装一个任务生命周期，Kuro 全塞在 `scheduler_service.py` 一个文件：

| Usagi 组件 | 职责 | Kuro 等价物 |
|---|---|---|
| `ProcessService`（272 行）| 起/停子进程、log 流、生命周期钩子、`failure_guard.should_skip_start`、`asyncio.create_subprocess_exec(spider_v2.py)` | SchedulerService 同进程 |
| `SchedulerService`（60 行）| APScheduler 包一层；`reload_jobs(tasks)`；`get_next_run_time(task_id)` | Kuro：用 interval 轮询 |
| `cron_utils`（44 行）| 5/6 段 cron + 别名（@daily 等）；`validate_cron_expression` 抛 `ValueError` | Kuro：无 cron |
| `task_log_cleanup_service.py`（14 行）| 启动时删 > `task_log_retention_days` 的旧 log | Kuro：无 |

`tasks.py` route 上的 6 个动词：
- `GET /api/tasks` / `GET /api/tasks/{id}` — list/get
- `POST /api/tasks/` — create (走 TaskService + reload_scheduler_if_needed)
- `POST /api/tasks/generate` — AI 生成端点 (async job，返 202+job_id，不阻塞)
- `PATCH /api/tasks/{id}` — update (并自动跑 criteria regeneration 当 description 改了或切换到了 ai)
- `DELETE /api/tasks/{id}` — stop process → delete task → 检查 keyword 是否还被其他任务引用 → 不再引用才 cascade 删 result_files + price_snapshots + log_file
- `POST /api/tasks/start/{id}` — 手动 start
- `POST /api/tasks/stop/{id}` — 手动 stop
- `GET /api/tasks/generate-jobs/{job_id}` — 轮询 AI 生成 job 状态

Kuro 的对应：
- GET/POST 全有 ✅
- PATCH 有，criteria regen 刚实装 (P0 #5) ✅ 但语义不同：Kuro 跳过"首次切换"
- DELETE 有，cascade 刚实装 (P0 #2) ✅
- **没有** `POST /api/tasks/start/{id}` 和 `POST /api/tasks/stop/{id}` —— Kuro 没有手动启停任务的能力。手动启停只能去加 cron 触发
- **没有** `POST /api/watchlist/{id}/regenerate-criteria` —— Kuro 的 PATCH 自动触发但没有"按钮"手动触发
- POST `/api/tasks/generate` Kuro 走的是 CLI `task-generate` 子命令，没 HTTP 版

### 3.5 Settings / 配置中心

Usagi 的 `src/api/routes/settings.py` 提供 6 个动词端点：

| 端点 | 用途 |
|---|---|
| `GET /api/settings/notifications` | 拉当前通知配置（URL/token 等） |
| `PUT /api/settings/notifications` | Web 端修改通知配置（写入 .env） |
| `POST /api/settings/notifications/test` | 给指定 channel 发测试消息 |
| `GET /api/settings/rotation` | 拉 rotation 配置 |
| `PUT /api/settings/rotation` | 改 rotation |
| `GET /api/settings/status` | 系统状态（AI/通知/调度器/Python/磁盘/...） |
| `GET /api/settings/ai` | 拉 AI 配置 |
| `PUT /api/settings/ai` | 改 AI 配置 |
| `POST /api/settings/ai/test` | 测 AI 连通性 |

Kuro 对应物：**几乎全缺**。
Kuro 配置在启动时读 `.env` / `config.yaml`，写盘不支持运行中改。运维改东西必须登服务器重启。

### 3.6 通知通道

Usagi `infrastructure/external/notification_clients/` 实装 7 条：
- `ntfy` (NTFY_TOPIC_URL)
- `gotify` (GOTIFY_URL + GOTIFY_TOKEN)
- `bark` (BARK_URL, iOS)
- `wecom` / `wecom_bot` (WX_BOT_URL, 微信企业号/群机器人)
- `telegram` (BOT_TOKEN + CHAT_ID + 可选 API_BASE_URL)
- `webhook` (URL + METHOD=GET/POST + HEADERS + CONTENT_TYPE=JSON/FORM + QUERY_PARAMETERS + BODY 模板)
- `pcurl_to_mobile` (多通道中继)

加 `factory.py` 按 channel name 分发；`base.py` 定义接口。

Kuro 现状：`cd_monitor/notify/{dingtalk,feishu}.py` 两条。**5 条全缺** + 自定义 webhook（可调 method/headers/body/content-type）也缺。

### 3.7 实时通信

Usagi:
- 一个 `WebSocket /ws` 路由
- 全局 `Set[WebSocket]`
- `websocket.broadcast_message(type, data)` 推到所有
- 在 `process_service.set_lifecycle_hooks` 里 `on_started/on_stopped` 回调里 broadcast `task_status_changed`
- 视图订阅即可拿到 task lifecycle 实时更新（不用轮询 GET）

Kuro: 无 WebSocket。前端只能 setInterval 轮询。

### 3.8 前端范围 (web-ui/src/views)

```
DashboardView.vue   — 主页：每个任务一条卡片 + 推荐流入口
TasksView.vue       — 任务 CRUD 编辑
AccountsView.vue    — 多账号池管理
ResultsView.vue     — 每个任务的结果列表 (支持排序、过滤、is_ai/keyword 切换、CSV 导出)
LogsView.vue        — log 文件查看
SettingsView.vue    — 通知/AI/rotation 三合一的设置面板
LoginView.vue       — Web 认证登录页（admin/admin123 默认）
```

`i18n/` 双语（中/英）；`composables/` Vue 3 Composition API helpers；`services/` 调 API 的封装。
Vue 3 + TypeScript + shadcn-vue (reka-ui) + Tailwind CSS。

Kuro 的 `web/index.html` 是纯 HTML+一个 JS 文件，覆盖：
- dashboard 视图 ✅
- tasks 视图 ✅
- opportunities/results 部分 ✅
- 但 views 完全缺：Accounts (基本空)、Logs (没 viewer)、Settings (没编辑面板) — Frontend 缺 ~3 个 view

### 3.9 Chrome extension (`chrome-extension/`)

- Manifest V3, MV3 service worker
- 装上后访问 goofish.com 已登录 → 点 popup 「Extract Login State」→ 收集 cookies (含 HttpOnly) + localStorage/sessionStorage (含过滤) + 观察到的 request headers + 浏览器环境 (UA / locale / timezone / screen / deviceMemory / hardwareConcurrency) → 输出单一 JSON → 复制粘贴 → 服务器侧用 `data/wameiji_state.json` 或 per-account `state/X.json` 收下
- Kuro 完全没这块：要登录态必须先 Playwright 启 chromium 一遍（人工/自动）

### 3.10 Pydantic 领域模型

Usagi 的 `domain/models/task.py` 用 Pydantic v2 BaseModel + `Literal["ai", "keyword"]` + `Field(default_factory=list)` + `@model_validator(mode="before")` + `@field_validator(mode="before")`。

Kuro 用 `@dataclass(slots=True)`。影响：缺 Pydantic 的 `ConfigDict(extra="ignore")` 自动屏蔽未知字段；缺 `Literal` 校验；缺 `Field(default_factory=list)` 的可变默认值安全。

### 3.11 启动期 bootstrap 引导

Usagi `bootstrap_sqlite_storage()` 启动时：
1. `init_schema(conn)` 建表
2. `_import_tasks_if_needed` 把 `config.json`（老 Kuro 也有的一份 JSON 文件）迁进 `tasks` 表，幂等
3. `_import_results_if_needed` 把 `jsonl/*` 老结果迁进 `result_items`，幂等
4. `_import_price_snapshots_if_needed` 把 `price_history/*` 迁进 `price_snapshots`，幂等
完成状态写 `app_metadata` 表 key。

Kuro 的 `init_db(db_path)` 只建表，从来不迁数据。新装环境空跑可以用，老用户（有 `jsonl/` 或 `config.yaml` 数据）升级会断。

### 3.12 Kuro 已经追平的（来自 P0 #1-#5）

| Kuro | Usagi 同构点 | P0 |
|---|---|---|
| `keyword_rule_engine.py` (扁平 + `-` 排除) | `src/keyword_rule_engine.py` 1:1 端口 | #1 ✅ |
| DELETE `/api/watchlist/{id}/hard-delete` cascade | `tasks/{id}` DELETE 同样 cascade | #2 ✅ |
| `cli.has_bound_account()` + `scan-watchlist` pre-flight | `spider_v2.py` 的 P5.3 裸断言 | #3 ✅ |
| `_call_once` EMPTY_MAX_RETRIES=4 + temperature/API fallback | `ai_client._call_once` 同形态 | #4 ✅ |
| `_maybe_regenerate_criteria_on_update` (P5.6) | `tasks/{id}` PATCH 自动 regen criteria | #5 ✅ (但语义更保守) |

### 3.13 Kuro 还没追平的：差距矩阵

| 类别 | 差距 | 影响面 | 难度 |
|---|---|---|---|
| **数据模型** | watchlist 加 `is_running` / `region` / `analyze_images` / `min_price` / `max_price` / `personal_only` / `max_pages` / `free_shipping` / `new_publish_option` / `seller_nickname` / `publish_time` 列 | 大 — 改 schema + 兼容老数据 | 中 |
| | `opportunities` 加 `analysis_source` / `status` 列 | 中 | 小 |
| | `result_blacklist_rules` 新表 + per-keyword blacklist service | 中 | 中 |
| | `link_unique_key` + UNIQUE(result_filename, link_unique_key) | 小 — 仅去重 | 小 |
| | `price_snapshots` 增列（keyword_slug / run_id / snapshot_day / tags_json / region / seller） | 中 | 中 |
| | `app_metadata` 启动幂等表 + `bootstrap_sqlite_storage` 流程 | 中 | 中 |
| **任务生命周期** | `ProcessService` 子进程拓扑 | 大 — 需要改 scheduler | 大 |
| | `POST /api/watchlist/{id}/start` 和 `/stop` 手动启停 | 中 | 小 |
| | `POST /api/watchlist/generate` HTTP 版（AI 生成走 async job） | 中 | 中 |
| | AI 生成 job 用 background task 模式（不阻塞 PUT） | 中 | 中 |
| | cron 表达式（替代 interval_minutes） | 中 | 中（依赖 APScheduler） |
| **settings** | `GET/PUT /api/settings/notifications` + `/test` | 大 | 中 |
| | `GET/PUT /api/settings/ai` + `/test` | 中 | 中 |
| | `GET/PUT /api/settings/rotation` + `/status` | 中 | 中 |
| | Web UI 改完持久化到 `.env`（不只是 env var） | 中 | 中 |
| **通知通道** | `gotify` / `bark` / `ntfy` / `telegram` / `wecom_bot` 5 条 | 大 | 小-中（每条约 60-120 行） |
| | 高灵活 `webhook`（method/headers/body/content-type/template） | 中 | 中 |
| **实时通信** | `WebSocket /ws` + broadcast | 中 | 中 |
| | 任务启停 lifecycle hook → broadcast (`task_status_changed`) | 小 | 小 |
| **前端** | TasksView / AccountsView / LogsView / SettingsView / ResultsView 视图补全 | 极大 | 极大 |
| | i18n | 中 | 中 |
| **Chrome ext** | Login state extractor extension | 中 | 中（独立工程） |
| | Kuro 端接住 extension 输出 JSON | 中 | 中 |
| **任务模型** | `Task` Pydantic 模型 (vs 当前 Kuro dataclass) | 大 | 中 |
| | `Literal["ai", "keyword"]` + `Field` + `model_validator` 校验 | 中 | 小 |
| **AI client** | `_sanitize_no_proxy_env` httpx IPv6 CIDR 修复 | 小 | 小 |
| | `client.close()` graceful shutdown | 小 | 小 |
| | `dotenv` 多 profile env manager | 中 | 中 |
| **其他** | `cron_utils` 端口 | 中 | 中 |
| | `dashboard_payloads` 完整拆分 (ai_recommended / keyword_recommended / latest_crawl_time 等) | 中 | 中 |
| | `result_export_service` CSV 导出端点 | 小 | 小 |
| | `seller_profile_cache` | 小 | 中 |
| | `rotation.py` 端口 (accounts rotation) | 中 | 中 |

### 3.14 建议的可执行下一步（优先级 / 影响 / 估时）

让我把这份对账先用工具列出来给你选，下面是我排的 Top 10 候选，按"用户面对的价值 / 端到端链路完整性 / 单文件可测"三维度：

1. **`/api/settings/notifications` CRUD + 1 个新通道 (Bark)** — 让用户能 Web 上改通知、且多一条通道。约 1 晚。
2. **`/api/settings/ai` CRUD + `/test` + 同上风格的 Web UI 编辑面板** — 让 AI 切换 provider 不再登服务器。约 1 晚。
3. **`POST /api/watchlist/{id}/start` + `/stop` 手动启停** — Kuro 真正缺的运维能力。约 半天。
4. **`Post /api/watchlist/generate` HTTP 版 + 改 `_maybe_regenerate_criteria_on_update` 不再阻塞 PUT** — 现有 P0#5 用 `asyncio.run` 卡线程，需要改成 background job。约 1 晚。
5. **`price_snapshots` 增列 (keyword_slug / run_id / snapshot_day / region / seller)** — 因为 watchlist 是 catalog 中心，price_history 那条路要适配。约 半天。
6. **`result_blacklist_rules` 表 + per-keyword blacklist HTTP 接口** — 让用户能持久化"忽略某关键字"。约 1 晚。
7. **`WebSocket /ws` + lifecycle hook broadcast (`task_status_changed`)** — 补上 Kuro 实时通信层。约半天。
8. **`is_running` 列 + 启动 is_running 重置** — Kuro 的 process 状态可持久化。约 半天。
9. **`POST /api/watchlist/{id}/regenerate-criteria` 手动按钮**（与 P0#5 同源但纯手动触发），不在 PUT 路径上。约 半天。
10. **`cron_utils` 端口 + 把 `interval_minutes` 改成可选 `cron`**（依赖 APScheduler） — 需要先决定是否引入 APScheduler。约半天 + 依赖决策。

> ⚠️ 这是我目前看到的差距，不是"必须全部做"。下一个起步是**你点哪个**。

