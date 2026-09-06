# cd-crossborder-monitor

> **2026-09-06 接续入口**：当前实测状态、首批修复、验证证据和待办见 [TAKEOVER_STATUS.md](TAKEOVER_STATUS.md)。Windows 本地启动使用 `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-local.ps1`。默认使用独立本地库，原生产数据库尚未恢复；挖煤姬和闲鱼可见登录 profile 已完成样例采集验证。`data/mock/` 为明确标识的合成演示样本。下方阶段说明包含历史记录，以本次接续记录为准。

## 当前方向：自动选品广场

项目不再以“先输入一张具体 CD”作为主流程。它会定时用一组发现关键词在**挖煤姬**寻找日系 CD 和实体游戏，再拿**闲鱼**可比样本做保守估价，扣除配置中的换汇、运费和平台成本后，按预估净利润从高到低展示候选。

- 挖煤姬是采购来源，闲鱼是国内参考价；不直接做煤炉、雅虎或其他平台的对齐。
- 品番/JAN 仍然是最可靠的匹配证据，但由系统从候选标题、详情和条码中尽量提取；只有未能自动确认的个案才需要人工复核。
- 采集只在这台“采集电脑”的已登录浏览器 profile 中发生。家里的电脑只打开网页查看结果、调整选品池、下达扫描命令。
- 不使用付费 API、付费插件、代理池或云采集服务。

## 家里电脑只用前端：GitHub Pages + Render

项目已经拆好部署入口：`web/` 是静态前端，`render.yaml` 是 Render Web Service，`.github/workflows/deploy-pages.yml` 会在推送 `main` 后发布 GitHub Pages。真实浏览器 profile、登录态和采集数据库继续留在采集电脑，不上传 GitHub。

Render 免费 Web Service 的文件系统会在重启、重新部署或闲置唤醒时丢失；因此 Render 端只作为远程 API 外壳，采集电脑是唯一数据源。`scripts/publish-replica.py` 会把本地 SQLite 压缩后通过单独的 `CD_SYNC_TOKEN` 推送到 Render，默认每分钟同步一次，循环运行即可在 Render 重启后恢复数据。免费 Render Postgres 也会在 30 天后到期，所以本方案不依赖它。

### 日常使用（最简单）

1. 采集电脑双击项目根目录的 `启动采集电脑.cmd`。它会自动启动本地后端、数据库同步和自动选品工作器；已运行的进程不会重复启动。
2. 家里电脑只打开 <https://niuzipai-gif.github.io/wameiji-xianyu-monitor/>。第一次看到令牌输入框时，从 Render 的 **Environment** 复制 `WEB_ACCESS_TOKEN` 粘贴进去；浏览器会记住它，之后不需要再输入。
3. 在网页顶部点击“让采集电脑立即扫描”。采集电脑会在下一分钟内收到命令；扫描完成后，结果会随下一次数据库同步出现在“自动选品广场”。无需输入具体 CD，也无需在家里的电脑安装插件。
4. 需要调整方向时，在“选品池设置”中改发现关键词、扫描间隔、每轮候选数、最低利润和最低利润率，点击保存即可。设置会由采集电脑执行。

首次接管按下面顺序操作：

1. 在 Render 用本仓库的 Blueprint 创建 `wameiji-xianyu-api`，选择 Free。填写三个环境变量：`WEB_ALLOWED_ORIGINS=https://niuzipai-gif.github.io`、随机的 `WEB_ACCESS_TOKEN`、随机的 `CD_SYNC_TOKEN`。两个 token 只放 Render 和本机 `.env`，不要写进仓库。
2. 把 Render 给出的 API 地址填入 GitHub 仓库变量 `CD_MONITOR_API_BASE`，然后手动运行一次 `Deploy frontend to GitHub Pages`。页面地址会是 `https://niuzipai-gif.github.io/<仓库名>/`。
3. 在采集电脑的项目目录双击 `启动采集电脑.cmd`。它会读取本机 `.env` 中的 Render 地址和令牌，并同时启动发布循环与自动选品工作器；登录 profile、Cookie、原始快照和本地数据库都不会上传 GitHub。

   如需单独排查自动选品工作器，可运行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start-discovery.ps1
   ```

   工作器日志在 `data/local/discovery-worker.log`；遇到验证码、登录过期或网站安全页时会记录为需要本机人工处理，不会伪造机会。

4. 家里打开 Pages 地址。如果看到“API 离线”，从 Render 服务的 Environment 复制 `WEB_ACCESS_TOKEN`，在 Pages 地址后追加 `?access_token=令牌` 并回车；页面会把令牌保存为会话 Cookie，之后可清理地址栏。

Pages 工作流不保存 token；`web/runtime-config.js` 只保存空配置，前端也支持用 URL `api_base` 或浏览器 `localStorage` 覆盖 API 地址。采集电脑仍按本地启动脚本运行，浏览器登录和验证码处理都只发生在这台电脑。

个人收藏采购决策辅助 / CD 跨境价差监控工具。

系统比较挖煤姬展示的日本侧采购入口价格与闲鱼参考价，输出候选机会、风险标签和人工复核建议。它不自动下单、不自动付款、不发布商品、不联系卖家，也不绕过验证码、安全验证或反自动化机制。

## Quick Start

```powershell
python -m pytest
python -m cd_monitor.cli init-db --db data/cd_monitor.db
python -m cd_monitor.cli doctor --db data/cd_monitor.db
python -m cd_monitor.cli evaluate-mock --catalog-no SRCL-3520
python -m cd_monitor.cli scan-once --catalog-no SRCL-3520 --source mock --db data/cd_monitor.db
python -m cd_monitor.cli scan-live --catalog-no SRCL-3520 --db data/cd_monitor.db
python -m cd_monitor.cli scan-watchlist --source mock --db data/cd_monitor.db
python -m cd_monitor.cli evaluate-html --catalog-no SRCL-3520 --wameiji-html path/to/wameiji.html --xianyu-html path/to/xianyu.html --db data/cd_monitor.db --snapshot-dir data/snapshots
python -m cd_monitor.cli notify-dry-run --opportunity-id 1 --channel feishu --db data/cd_monitor.db
python -m cd_monitor.cli notify --opportunity-id 1 --channel feishu --config config.example.yaml
python -m cd_monitor.cli alert-list --db data/cd_monitor.db
python -m cd_monitor.cli backtest --snapshot-dir data/snapshots
python -m cd_monitor.cli backtest --db data/cd_monitor.db --catalog-no SRCL-3520
python -m cd_monitor.cli list-opportunities --db data/cd_monitor.db
python -m cd_monitor.cli report --db data/cd_monitor.db --output data/review-report.md
python -m cd_monitor.cli review --db data/cd_monitor.db --opportunity-id 1 --result rejected_low_profit --note "manual check"
python -m cd_monitor.cli review-list --db data/cd_monitor.db
python -m cd_monitor.cli recheck-plan --db data/cd_monitor.db --opportunity-id 1 --delay-seconds 120 --reason "second pass"
python -m cd_monitor.cli recheck-list --db data/cd_monitor.db
python -m cd_monitor.cli recheck-resolve --db data/cd_monitor.db --recheck-id 1 --status confirmed --reason "still available"
python -m cd_monitor.cli web --db data/cd_monitor.db --host 127.0.0.1 --port 8765
```

## Safety Boundary

真实浏览器采集只允许低频、可观察、可停止地读取可见搜索结果和详情信息；遇到验证码、安全验证或登录失效会返回 `human_required`。本项目不依赖 Chrome/Edge 扩展：CLI 使用项目虚拟环境里的 Playwright Chromium，登录、扫码和安全验证由用户在可见浏览器窗口中完成。

## 数据源状态

当前已验证可用的数据入口：

- 模拟数据回放：`scan-once --source mock`、`scan-watchlist --source mock`。
- 用户手动快照：`import-html`、`import-csv`、`evaluate-json`、`evaluate-files`、`evaluate-html`，支持 Wameiji HTML 快照和闲鱼 CSV/HTML/JSON 样本。

当前仍需要用户准备登录态的入口：

- Wameiji：可用独立的 Playwright persistent profile，或用户导出的 `wameiji_state.json`。
- 闲鱼：可用用户导出的 Playwright `storage_state`，或独立的 persistent profile。

可用 `python -m cd_monitor.cli doctor --db data/cd_monitor.db` 查看 `data_sources`，其中 `manual_snapshots` 会列出 CLI 与 Web 可用入口，`wameiji_live_browser` 和 `xianyu_live_browser` 会明确显示 disabled / not_configured / human_required 等状态。

`scan-live` 和 Web 控制台的 `实时检查` 用于检查真实浏览器读取准备度：默认仍受 `BROWSER_ENABLED` 开关保护；直接采集使用下面的 `capture-live-html` / `scan-live-html` 命令。每次 readiness 检查都会在 `search_runs` 中分别记录 Wameiji/Xianyu 适配器状态，便于回查失败原因；它不会伪造 opportunity。

闲鱼登录态文件可通过配置或环境变量指定：

```yaml
browser:
  enabled: true
  xianyu_state_file: "data/xianyu_state.json"
  xianyu_profile_dir: "data/browser_profiles/goofish"
```

```powershell
$env:BROWSER_ENABLED = "true"
$env:XIANYU_STATE_FILE = "data/xianyu_state.json"
# 优先使用可见登录档案（可与 state_file 同时保留作备份）：
$env:XIANYU_PROFILE_DIR = "data/browser_profiles/goofish"
# 或兼容 goofish 命名：
$env:GOOFISH_STATE_FILE = "data/xianyu_state.json"
```

状态检查只读取 `storage_state` 的结构和 cookie 域名，不会返回 cookie 值。有效文件会显示 `login_state_ready=true` / `state_file_status=ready`；实时采集命令仍会在遇到验证码、安全验证或登录失效时停止并要求用户处理。

如果交接电脑没有浏览器插件，也不影响本项目。首次安装本地运行环境（只需做一次）并打开闲鱼/Goofish 可见登录页：

```powershell
.\.venv\Scripts\python.exe -m ensurepip --upgrade       # 若提示 No module named pip
.\.venv\Scripts\python.exe -m pip install -e ".[browser]"
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m cd_monitor.cli xianyu-login-state --output data/xianyu_state.json --timeout-seconds 180
```

该命令只保存 Playwright `storage_state` 到本地文件，并在终端输出 cookie 数量和域名摘要，不输出 cookie 值；超时未检测到 Goofish/Xianyu 登录 cookie 时不会写出状态文件。

拿到登录态文件后，可以只读采集搜索页 HTML 快照，并用现有解析器统计条目：

```powershell
.\.venv\Scripts\python.exe -m cd_monitor.cli capture-live-html --source xianyu --catalog-no SRCL-3520 --xianyu-profile-dir data/browser_profiles/goofish --state-file data/xianyu_state.json --output data/snapshots/xianyu_SRCL-3520.html --screenshot-output data/screenshots/xianyu_SRCL-3520.png --network-output data/snapshots/xianyu_SRCL-3520.network.json
.\.venv\Scripts\python.exe -m cd_monitor.cli capture-live-html --source wameiji --catalog-no SRCL-3520 --profile-dir data/browser_profiles/wameiji --output data/snapshots/wameiji_SRCL-3520.html --screenshot-output data/screenshots/wameiji_SRCL-3520.png --network-output data/snapshots/wameiji_SRCL-3520.network.json
```

也可以复用 Playwright persistent profile，而不是导出的 `storage_state`：

```powershell
.\.venv\Scripts\python.exe -m cd_monitor.cli capture-live-html --source xianyu --catalog-no SRCL-3520 --profile-dir data/browser_profiles/goofish --output data/snapshots/xianyu_SRCL-3520.html --screenshot-output data/screenshots/xianyu_SRCL-3520.png --network-output data/snapshots/xianyu_SRCL-3520.network.json
```

`capture-live-html` 只执行打开搜索页、等待页面加载、保存 HTML、可选保存当前页截图、可选保存页面自己加载到的同源/站点响应摘要和本地解析统计；它不会伪造签名、绕过验证码、使用代理池或模拟 TLS 指纹。遇到验证码、安全验证或登录失效文本会返回 `human_required/security_check`。后续仍需用 `evaluate-html` 把 Wameiji/闲鱼两份 HTML 组合评估落库。

也可以用只读闭环命令一次完成“两边采集 HTML + 组合评估落库”：

```powershell
.\.venv\Scripts\python.exe -m cd_monitor.cli scan-live-html --catalog-no SRCL-3520 --profile-dir data/browser_profiles/wameiji --xianyu-profile-dir data/browser_profiles/goofish --state-file data/xianyu_state.json --db data/cd_monitor.db --snapshot-dir data/snapshots
```

`scan-live-html` 会先采集 Wameiji，再采集 Xianyu/Goofish，并默认在 `<snapshot-dir>/live_screenshots/` 保存两边搜索页截图、在 `<snapshot-dir>/live_network/` 保存两边页面网络响应摘要；任一端返回 `human_required` 时会停止，不生成机会、不伪造数据。两边均成功解析后才调用现有 `evaluate-html` 流程写入 SQLite。

## Current Modules

- `core`: 品番/JAN 标准化、匹配置信度、闲鱼清洗、成本模型、机会判断。
- `sources`: mock 数据源，以及 Wameiji/Xianyu Browser-Harness 安全骨架和 HTML 快照解析。
- `storage`: SQLite 表初始化、扫描记录、商品、样本、机会、通知去重。
- `services`: 一次扫描的编排流程，负责保存 snapshot 并写入数据库。
- `notify`: 飞书/钉钉 dry-run 和 webhook 发送。
- `scheduler`: P0/P1/P2 队列和候选二次复核时间。
- `review`: 人工复核报告和 snapshot 回测。
- `review_decisions`: 人工复核结果记录，支持 `accepted_for_personal_collection` 和 `rejected_*` 结果。
- `candidate_rechecks`: 候选二次复核计划与人工确认结果，支持 pending、confirmed、rejected、human_required。
- `opportunities`: 以 `opportunity_hash` 去重；重复扫描同一机会时会返回已有真实 ID，保证通知、复核和报告继续指向同一条机会记录。
- `web`: 本地高级感运营控制台，展示 KPI、机会表、机会详情、闲鱼样本、watchlist、复核记录、二次复核计划、告警日志、扫描历史、配置与 Browser-Harness 状态，并支持新增/编辑/禁用 watch、单品 mock 扫描、批量扫描 watchlist、JSON/HTML/CSV 文件评估、数据库回测、写出复核报告、记录复核、通知 dry-run/真实发送。

## Web Console

启动本地控制台：

```powershell
python -m cd_monitor.cli web --db data/cd_monitor.db --host 127.0.0.1 --port 8765
```

打开 `http://127.0.0.1:8765`。Web 控制台只调用本地 API 和 SQLite，不执行下单、付款、发布商品、联系卖家或绕过验证。

Web 控制台当前支持：

- 点击机会表行查看落地成本、预估售价、ROI、挖煤姬来源条目、闲鱼样本、人工复核 checklist、该机会的复核历史和通知历史。
- Web scan/import/backtest API 返回的 opportunity 摘要包含与 CLI 一致的复核字段，包括闲鱼参考价、落地成本、预估利润、周转 ROI、有效样本数、流动性、来源链接、图片、可购买状态和品相片段。
- 桌面端使用密集表格；移动端机会表自动切换为卡片式行，避免手机上横向拖动宽表。
- `模拟扫描` 扫描顶部输入的单个品番。
- `扫描关注清单` 批量扫描所有启用 watch，并报告没有机会的品番；批量扫描会使用 watch 中的版本、必需关键词、排除关键词和预期持有天数配置。
- 在侧栏新增 watch；新增和编辑都支持 catalog、JAN/EAN、艺人、日文标题、中文标题、版本/特典、必需关键词、排除关键词、优先级和预期持有天数，点击 watch 后可继续修改或禁用。
- 记录人工复核；选中机会后会自动带入 opportunity ID，提交后刷新机会详情和复核队列。
- 为选中的机会创建 60～180 秒后的 pending 二次复核计划，并可人工标记 confirmed/rejected；机会详情会显示 recheck 历史。该流程只记录本地状态，不执行真实网页动作。
- 在 `Operations` 中输入本地 JSON 路径执行 `evaluate-json`，输入 Wameiji HTML + Xianyu CSV/HTML 路径执行文件评估，或直接粘贴 Wameiji/Xianyu 可见 HTML 文本评估落库；也可输入品番执行数据库回测，写出 Markdown 复核报告。
- 在机会详情中选择飞书/钉钉执行通知；默认 dry-run，会写入 `sent_alerts` 去重记录，取消 dry-run 后才会使用配置中的真实 webhook。
- 在 `设置` 中查看当前 SQLite 路径、snapshot 路径、核心成本、强/弱提醒阈值、匹配置信度阈值、闲鱼有效价格窗口、样本上限、通知配置状态、Wameiji/Xianyu Browser-Harness 安全状态、enabled 时的手动入口 URL、闲鱼登录态文件状态、采集说明和真实数据接入流程命令。Webhook URL 会被隐藏，只显示是否配置。
- 外网预览启用 `WEB_ACCESS_TOKEN` 时，`设置` 会显示访问保护已启用，仅暴露是否启用，不暴露 token。

Web API 仅供本机控制台使用：无 `Origin` 的本地脚本请求允许；浏览器 POST 请求若携带跨站 `Origin` 会被拒绝，减少其他网页误打本机端口的风险。POST 请求中的 JSON 若格式错误会返回结构化 `400 invalid_json`；数字字段格式错误会返回 `400 invalid_integer` 和对应字段名。

临时外网预览时请设置 `WEB_ACCESS_TOKEN` 后再通过隧道暴露本地服务，例如：

```powershell
$env:WEB_ACCESS_TOKEN = "replace-with-random-token"
python -m cd_monitor.cli web --db data/cd_monitor.db --host 127.0.0.1 --port 8765
npx -y localtunnel --port 8765 --local-host 127.0.0.1
```

访问 `https://<tunnel-domain>/?access_token=<token>` 会设置本会话 cookie；未带 token 的请求会返回 `401 unauthorized`。不要把不带 token 的隧道链接公开转发。

当前远程预览进程已在本机 `127.0.0.1:8765` 后方启动，并通过 localhost.run SSH 反向隧道暴露。已验证入口：

```text
https://4e3f58027aff37.lhr.life/?access_token=p0TFJ2PG7Vwv5Rm1DixKB9YzeluIsWot
```

带 token 页面返回 200 且加载 `WAMEIJI-XIANYU`；该 lhr.life 地址已验证不会出现 localtunnel 中间确认页。公网 HTML/JS 已确认包含中文界面、数据接入状态区、新版设置行、Browser-Harness 入口行、闲鱼登录态状态逻辑、真实数据接入流程命令、`--screenshot-output`、`--profile-dir`、`--network-output` 和完整 watchlist 字段；`/api/browser/status` 当前返回 `enabled=true`、Wameiji/Xianyu `human_required/not_configured`，不带 token 访问 `/api/health` 返回 401。

## Manual Imports

HTML 快照导入：

```powershell
python -m cd_monitor.cli import-html --source wameiji --catalog-no SRCL-3520 --html path/to/wameiji.html --snapshot-dir data/snapshots
python -m cd_monitor.cli import-html --source xianyu --catalog-no SRCL-3520 --html path/to/xianyu.html --snapshot-dir data/snapshots
```

闲鱼 CSV 样本导入：

```powershell
python -m cd_monitor.cli import-csv --source xianyu --catalog-no SRCL-3520 --csv path/to/xianyu.csv --snapshot-dir data/snapshots
```

CSV 字段支持英文或中文列名：`title`/`标题`、`price_cny`/`price`/`价格`、`url`/`链接`、`image_url`/`图片`、`seller_text`/`卖家`、`raw_text`/`原文`。价格值支持纯数字、`¥/￥`、`元`、`人民币`、`CNY`、`RMB` 和千分位逗号。

把手动保存的 Wameiji HTML 和闲鱼 CSV 直接组合评估并落库：

```powershell
python -m cd_monitor.cli evaluate-files --catalog-no SRCL-3520 --wameiji-html path/to/wameiji.html --xianyu-csv path/to/xianyu.csv --db data/cd_monitor.db --snapshot-dir data/snapshots
```

把手动保存的 Wameiji HTML 和闲鱼 HTML 直接组合评估并落库：

```powershell
python -m cd_monitor.cli evaluate-html --catalog-no SRCL-3520 --wameiji-html path/to/wameiji.html --xianyu-html path/to/xianyu.html --db data/cd_monitor.db --snapshot-dir data/snapshots
```

Web 控制台的 `Evaluate Pasted HTML` 可直接粘贴两段可见 HTML 文本，不必先保存为本地文件；后端仍只解析快照文本并写入本地 SQLite，不会打开真实网页或执行交易动作。

闲鱼估价会先按 `xianyu.sample_limit` 取前 N 条样本，再清洗样本：低于 `xianyu.min_valid_price_cny`、高于 `xianyu.max_valid_price_cny`、收/求/代拍/仅展示等噪声会剔除；当 watch 指定 `edition`、必需关键词或排除关键词时，不匹配版本的闲鱼样本会标记为 `invalid_edition_mismatch`，不会进入参考价计算。

匹配置信度会同时使用 watch 的必需/排除关键词：必需关键词出现时加分，缺失时记录 `missing_required_keyword:*` 并扣分；排除关键词出现时扣分，低置信度机会会进入 reject/review 流程。

JSON 文件组合评估：

```powershell
python -m cd_monitor.cli evaluate-json --catalog-no SRCL-3520 --wameiji-json path/to/wameiji.json --xianyu-json path/to/xianyu.json --db data/cd_monitor.db --snapshot-dir data/snapshots
```

仓库提供了可直接试跑的手动导入样本：

```text
data/mock/wameiji_search.sample.html
data/mock/xianyu_samples.sample.csv
```

## Config

```powershell
python -m cd_monitor.cli --config config.example.yaml scan-once --catalog-no SRCL-3520 --source mock
```

命令行参数会覆盖配置文件；环境变量如 `CD_MONITOR_DB_PATH`、`FEISHU_WEBHOOK_URL`、`DINGTALK_WEBHOOK_URL`、`BROWSER_ENABLED`、`XIANYU_STATE_FILE`、`GOOFISH_STATE_FILE` 会覆盖配置文件。

## Notifications

Dry-run 不会发真实 webhook，但会按 `alert_hash` 写入 `sent_alerts` 做去重：

```powershell
python -m cd_monitor.cli notify-dry-run --opportunity-id 1 --channel feishu --db data/cd_monitor.db
```

真实发送会从配置或环境变量读取 webhook URL：

```powershell
python -m cd_monitor.cli notify --opportunity-id 1 --channel feishu --config config.example.yaml
```

发送失败会写入 `sent_alerts(status=failed)`，然后把异常抛出，便于发现配置或网络问题。

通知 payload 包含品番、JAN、日本侧标题、来源站点、Wameiji 价格、落地成本、闲鱼参考价、预估成交价、利润、净利率、周转 ROI、有效样本数、流动性、匹配置信度、风险标签、Wameiji 链接、图片 URL、截图路径、可购买状态、品相片段和闲鱼搜索关键词。

## Recheck

CLI 和 Web 都支持候选二次复核闭环。`recheck-plan` 只写入本地 pending 计划，不打开真实网页；同一机会只保留一条 pending 计划，重复计划会更新计划时间和原因。`recheck-resolve` 用于人工确认后记录结果，confirmed/rejected/human_required 历史可保留多条。

`scan-once`、`scan-watchlist`、`evaluate-json`、`evaluate-files`、`evaluate-html` 和 `list-opportunities` 输出包含真实数据库 `id`，并携带闲鱼参考价、落地成本、预估利润、周转 ROI、有效样本数、流动性、匹配置信度、来源链接、图片、可购买状态和品相片段，可直接用于 `notify`、`review`、`recheck-plan` 和报告回查。`backtest` 会使用当前配置中的成本参数、闲鱼清洗阈值、样本上限和提醒阈值重算机会，避免回测与扫描配置不一致。

```powershell
python -m cd_monitor.cli recheck-plan --db data/cd_monitor.db --opportunity-id 1 --delay-seconds 120 --reason "second pass"
python -m cd_monitor.cli recheck-list --db data/cd_monitor.db --status all
python -m cd_monitor.cli recheck-resolve --db data/cd_monitor.db --recheck-id 1 --status confirmed --reason "price and availability still match"
```

Markdown 复核报告会包含日本侧来源、价格、链接、图片、可购买状态、品相片段、落地成本、闲鱼参考价、预估成交价、预估收入、预估利润、净利润率、周转 ROI、有效样本数、流动性、匹配置信度、风险标签、人工复核建议，以及已记录的人工复核和二次复核历史；历史按数据库真实 `opportunity_id` 关联记录，避免列表倒序或非连续 ID 导致串到其他品番。

## Browser-Harness 状态

`WameijiBrowserAdapter` 和 `XianyuBrowserAdapter` 当前不会自动打开真实网页。它们提供：

- `enabled=false` 默认禁用真实读取。
- `parse_search_html(...)` 解析用户保存或工具采集到的可见 HTML 快照，支持常见嵌套卡片结构；没有专用 `data-*` 属性时，会尝试从包含品番的普通链接块和价格文本中提取候选。价格文本支持 `¥/￥`、`円/元/日元/日圓/人民币`、`JPY/CNY/RMB` 等常见写法；标题/价格也会读取 `title`、`aria-label`、`alt` 等属性文本，图片支持 `src`、`data-src`、`data-original`、`data-lazy-src`；Wameiji 普通文本会尽量识别 `mercari/rakuma/yahoo/bookoff/surugaya` 等来源站点和 `盤傷/ケース割れ/破損` 等品相片段；`已售/售罄/売り切れ/sold out` 等状态会标记为 `sold_out`，评估器会拒绝明确不可购买的候选；多商品父容器会被跳过，避免重复或错配。
- `search_status(...)` 在 `BROWSER_ENABLED=true` 但没有真实浏览器执行器时返回 `human_required/not_configured`，并暴露安全的手动搜索入口：Wameiji `https://meruki.cn/search?keywords=<catalog>`，Xianyu `https://www.goofish.com/search?q=<catalog>`。
- `XianyuBrowserAdapter` 支持检查 Playwright `storage_state` 文件：路径缺失返回 `state_file_missing`，JSON/结构无效返回 `invalid_state_file`，识别到 goofish/xianyu/taobao/alibaba/tmall 相关 cookie 域名时返回 `login_state_ready=true`、`state_file_status=ready` 和域名列表；不会返回 cookie 值。
- `xianyu-login-state` 可打开可见浏览器登录页并导出 `storage_state`；该流程需要用户自己扫码/登录，命令不会绕过验证，也不会执行搜索、购买、私信或发布动作。
- `capture-live-html` 可打开 Wameiji 或 Xianyu/Goofish 搜索页并保存只读 HTML 快照，可通过 `--screenshot-output` 同步保存当前搜索页截图、通过 `--network-output` 保存页面响应摘要；闲鱼侧支持 `--state-file` 或 `--profile-dir` 复用用户登录态，随后用现有 parser 统计条目数；它不会点击商品详情、加购、购买、私信、发布或绕过验证。
- `scan-live-html` 将 Wameiji/闲鱼两边只读 HTML 采集、搜索页截图、页面响应摘要和 `evaluate-html` 串成闭环；任一端需要人工处理时会停止并返回 `human_required`。
- 发现 CAPTCHA、安全验证、登录失效、Cloudflare，以及社区项目中常见的 `RGV587_ERROR`、`FAIL_SYS_USER_VALIDATE`、`punish`、滑块/验证码/风控文本时返回 `human_required`。

真实 Chrome Profile 读取需要在用户本机浏览器环境下继续接入，但禁止自动下单、付款、加购、发消息或绕过验证。
