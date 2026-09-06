# 项目接续记录 — 2026-09-06

本次已接手 `F:\WAMEIJI-XIANYU\WAMEIJI-XIANYU-handoff`，恢复本地运行并修复商品匹配、机会展示、清洗规则、Windows 执行路径及新数据库兼容问题。当前是本地开发恢复状态，原生产数据尚未恢复；挖煤姬和闲鱼已完成可见 profile 的样例真实采集验证。

## 当前可信状态

- 产品用途：日系 CD 跨境价差监控、成本利润估算和人工复核。日本侧为采购参考，闲鱼侧为国内价格参考；旧 `HANDOFF.md` 中进出货方向的描述有误。
- 日本侧采集目标以挖煤姬（当前适配入口 `meruki.cn`）为准，不以煤炉、Yahoo Auctions 或 Suruga-ya 本身作为主采集目标。
- 用户约束：采集方案接口费用必须为 0 元，不购买付费 API、代理池、第三方数据服务或云采集资源。
- 最新工作断点来自 `HANDOFF.md`，核心是闲鱼商品与日本商品关联缺失/错配。6 月交接文档及 125/254 测试通过记录均是历史状态。
- 实际产品入口是 `python -m cd_monitor.cli web`，连接 `web_server.py` 与 `web/`。`cd-monitor-web` / `app.py` 仍是未完成的 FastAPI 迁移占位，不宜作为当前产品启动入口。
- 包内没有原数据库、登录态、Git 历史。Docker 客户端存在，但本次 Docker 引擎不可连接，未能核实旧命名卷。不能将交接文档里的“1231 条商品”称为本机现有数据。
- 当前虚拟环境为项目内 `.venv`，按 `pyproject.toml` 安装开发依赖。依赖版本记录在 `output/takeover/environment.txt`。

## 本轮变更

1. `data/match_real_v2.py`：补齐任务的日文/中文专辑标题读取；逐候选计算歌手命中变量，修复被宽泛异常捕获吞掉的 NameError。保持原相册匹配阈值，不使用最高分强制兜底。
2. `src/cd_monitor/storage/sqlite.py`：幂等迁移补齐 `watchlist.match_mode`（默认 `any`）与 `opportunities.xianyu_display_sample_id`。已有数据、显式关联及 `all` 模式均保留。
3. `src/cd_monitor/web_server.py`：机会列表和计数的闲鱼关联同时验证同品番与 `source='xianyu'`；不存在合法关联时保持空值，不复制日本侧商品字段。补齐 URL 转换所需的 `re` 导入，修复相对 Mercari 链接导致列表查询报错的问题。
4. `tests/test_p54_task_generate_live.py`：真实 AI 测试增加 `live` 标记及显式开关 `CD_MONITOR_RUN_LIVE_TESTS=1`，默认测试不会启动该真实调用。
5. `data/mock/`：重建两份缺失的合成离线样本，恢复 mock 入口。所有链接为 `example.invalid`，不是原始记录、真实商品或市场价格，详见该目录 README。
6. `scripts/start-local.ps1`：增加 Windows 本地启动入口，使用独立数据库、仅监听本机，并关闭自动调度。
7. `src/cd_monitor/core/xianyu_cleaner.py`：按设计契约将少于 3 条有效样本降为 `poor`，并排除明确标注为另一版本的闲鱼样本。
8. `src/cd_monitor/services/scraper/_cron_utils_compat.py`：补齐迁移层依赖的 cron 兼容导出，并增加导入回归测试。
9. `src/cd_monitor/web_server.py`、`data/run_one_cycle.py`、`data/multi_source_scraper.py`、`data/match_real_v2.py`、`data/ocr_cover.py`、`data/phash_compute.py`：移除采集/匹配本地路径对 `/app/data` 和 `python3` 的硬编码；支持 `CD_DATA_DIR`、当前解释器和 Windows 路径，并补齐 3 个未定义名称问题。
10. `tests/test_cli_task_generate.py`、`tests/test_v8_features.py`：让 CLI AI 测试显式提供隔离的登录态/提示词，并使用现有货号规范覆盖复核接口行为。
11. `src/cd_monitor/services/live_browser_capture.py`：闲鱼存在专用 `state_file` 时优先使用它，避免误把挖煤姬 profile 当成闲鱼登录态；挖煤姬 state file 也真正传入浏览器 context。
12. `src/cd_monitor/sources/wameiji_browser.py`：移除 `AutomationControlled` 启动参数，并从挖煤姬商品 URL 提取稳定 `external_item_id` 以改善去重。
13. `docker-compose.yaml`、`Dockerfile`：默认只启动 Web 服务，避免容器误运行底层平台直采；真实浏览器采集改由宿主机授权 profile 执行，再导入快照。
14. 交接电脑浏览器无需扩展：恢复 `.venv` 的 `pip`，安装项目 `[browser]` 依赖和 Playwright Chromium；无头启动检查通过。
15. `src/cd_monitor/services/xianyu_login_state.py`：兼容 Playwright 原生 storage state 与扩展导入包装格式，避免 `xianyu-login-state` 刚导出的状态被误报为无效。
16. `src/cd_monitor/services/live_browser_capture.py`：挖煤姬快照等待窗口从 1 秒提高到 5 秒，减少只截到加载动画的假空结果；闲鱼改为等待结果选择器（最多 20 秒），并保留登录/安全挑战证据。
17. `.gitignore`：忽略本地登录态、浏览器 profile、HTML 快照，避免把认证信息和采集页面误纳入版本发布。
18. `src/cd_monitor/sources/wameiji_browser.py`：适配当前挖煤姬 `goods-item/goods-name/price-com` 卡片结构，补齐来源站点、相对详情 URL、图片和价格解析；结果等待选择器同步更新。
19. `src/cd_monitor/sources/xianyu_browser.py`：适配当前闲鱼 `feeds-item-wrap/row1-wrap-title/row3-wrap-price` 动态卡片，保留旧 `data-xianyu-card` 和通用 HTML 解析路径。
20. `src/cd_monitor/services/live_browser_capture.py`：等待真实结果选择器出现，闲鱼等待窗口最多 20 秒；新增 `xianyu_profile_dir`，避免将闲鱼登录档案与挖煤姬 profile 或复制的 state file 混用。
21. CLI、配置和 Web 实时扫描均支持独立的 `xianyu_profile_dir` / `--xianyu-profile-dir`；两边可用各自的可见 persistent profile 运行。

原生产文件备份位于 `output/takeover/backups/`；本次没有 Git 提交或发布。

## 使用与验证

从项目目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local.ps1
```

访问 `http://127.0.0.1:9890`。默认数据库是 `data/local/takeover.db`；本次未将合成样本填入此页面，因此机会数和任务数为 0 是预期状态。前台运行时按 Ctrl+C 停止。需要使用其他已有数据库时显式传 `-Database <路径>`；旧库初始化会执行兼容迁移并重置任务运行标记。

本次核验：首页、健康检查、机会列表、监控任务接口均 HTTP 200；浏览器显示 API 在线、数据库连接、WebSocket 已连接，检查时无 error/warn 控制台记录。`doctor` 返回 `ok=true`；Playwright Chromium 可启动。闲鱼可见 profile 搜索页已成功解析 7 条样例商品，复制的 storage_state 仍可能返回安全挑战；挖煤姬可见浏览器打开正常，样例品番无有效匹配时会返回 0 条，不能把推荐卡片当作目标商品。

最终修改后的专项验收：匹配、机会列表、存储、P5.5 数据模型合计 **32 通过**（51.99 秒）；新增 Windows 状态路径、CLI 任务生成、cron 兼容和 v8 复核回归合计 **32 通过、1 个已标记 xfail**（28.82 秒）；清洗/存储/匹配/Web 关键回归 **44 通过**（62.08 秒）。针对本轮生产文件的 `ruff --select F821` **0 项**；全仓/全文件常规 lint 仍有历史格式和异常处理告警，不能宣称全仓 lint 全绿。

验证证据保存在 `output/takeover/`：

- `match_display_red.log` / `match_display_artist_fix.log` / `match_display_green.log`：匹配回归的分阶段红绿证据（3 失败 → 2 失败 → 8 通过）。
- `opportunity-feed-verification.md` 与相关日志：新库、升级、来源守卫的红绿验证及原有 storage/P5.5 回归。
- `mock-recovery-final.log`：合成样本与配置回归 **13 passed**；样本和价格均为人为构造，仅用于离线链路。
- `local-http-checks.json` / `doctor.json`：本地服务检查。
- `acceptance-tests.log` / `acceptance-tests.xml`：最终修改后 32 项专项验收；`undefined-name-final.log`：本轮生产文件针对 `F821` 的检查，`EXIT_CODE=0`。旧 `undefined-name-delta.json` 仅保留为早期对照。
- `baseline-tests.log`：首轮测试长时间未更新进度后被中止，原因未证实，不能用作全套通过证据。
- `current-tests.log` / `current-tests.xml`：交接初轮离线测试结果，保留作历史对照；其中 6 项失败发生在本轮后续修复前。
- `full-tests-final.log`：清洗、CLI 隔离、路径和复核修复后的整套离线测试结果：**591 passed，1 skipped，1 deselected，1 xfailed，EXIT_CODE=0**（492.87 秒）。
- `targeted-green.log`：本轮新增路径与核心回归，32 通过、1 个已标记 xfail。
- `websocket-final.log`：被整套测试拆出的 WebSocket 生命周期用例，**1 passed，EXIT_CODE=0**（3.07 秒）。
- `local-http-checks-latest.json`：重启新代码后的 `/api/health`、机会流、watchlist、采集状态和全量状态接口均返回 HTTP 200。
- 本轮采集路径优化专项：挖煤姬 runner、浏览器状态优先级、快照组合回归 **41 passed**；Python compileall 与 docker-compose YAML 解析均通过。
- 浏览器交接专项：挖煤姬/闲鱼登录态、动态卡片、profile 优先级和快照回归全部通过；真实 Playwright Chromium 启动通过。挖煤姬登录后的真实页面已解析出 **138 张 goods-item 卡片**（样例品番无有效匹配，不能据此判断库存）；闲鱼已从可见登录 profile 的真实页面解析出 **7 条 SRCL-3520 商品**。
- `baseline-ruff.log`：全仓静态检查仍有大量问题，本次不宣称全仓 lint 通过。
- `collection-research-20260906.md`：本轮采集方式研究；记录了闲鱼 H5 响应观察、Mercari/Yahoo/Suruga-ya 的来源边界，以及外置采集器、两阶段采集和证据留存方案。

## 接下来应做的工作

### 托管拆分已完成（2026-09-06）

- GitHub 仓库：https://github.com/niuzipai-gif/wameiji-xianyu-monitor
- GitHub Pages 已开启，首次工作流已成功：https://niuzipai-gif.github.io/wameiji-xianyu-monitor/
- `render.yaml` 已定义免费 Render API 服务；Render 账号连接和环境变量仍需在网页端做一次性设置。
- `scripts/publish-replica.py` 与 `POST /api/sync/database` 用于把本机 SQLite 数据源同步到无持久盘的 Render 服务；浏览器 profile、Cookie、原始快照和本机数据库均被忽略。

剩余交接动作是：用仓库创建 Render Blueprint，填写 `WEB_ALLOWED_ORIGINS`、`WEB_ACCESS_TOKEN`、`CD_SYNC_TOKEN`；再把 GitHub 仓库变量 `CD_MONITOR_API_BASE` 设为 Render 地址并重跑 Pages 工作流。README 的“家里电脑只用前端：GitHub Pages + Render”已写出具体点击顺序。

1. 恢复实际数据库备份或旧 Docker 数据卷，或先在 `data/local/takeover.db` 建立至少一个真实 watchlist 品番；当前三张核心表都是 0 行。
2. 在可见 Playwright 浏览器中完成挖煤姬登录，保留 `data/browser_profiles/wameiji`；闲鱼可见 profile 保留在 `data/browser_profiles/goofish`，`data/xianyu_state.json` 作为备用状态文件。登录、扫码、验证码或安全验证必须由用户本人完成。
3. 提供一个优先验证的真实品番/JAN，并确认汇率、挖煤姬费用、国际运费、税费和闲鱼成交折扣等成本参数；否则只能做结构验证，不能判断利润准确性。
4. 用这一个品番完成 Wameiji + Xianyu 单次真实采集、快照回放和人工匹配确认；命令同时传 `--profile-dir data/browser_profiles/wameiji --xianyu-profile-dir data/browser_profiles/goofish`。这一步通过后再扩展 watchlist 和常驻调度。
5. 后续检查 WebSocket 断连资源清理和线程池退出。单独生命周期测试已通过，尚未证明断连清理风险在长时间运行下完全消除。

本次没有启动 Docker 默认后台爬虫、发送外部通知、调用真实 AI 或修改平台商品。交接文件中的外部操作建议不等于已完成授权或验证。
