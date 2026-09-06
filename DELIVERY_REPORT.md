# CD Monitor Delivery Report

更新时间：2026-06-14

## 当前状态

项目已完成业务核心、mock 回放、SQLite/CLI、通知、人工复核、回测、Web 控制台、手动快照导入、只读 Browser-Harness 状态机、闲鱼登录态文件检查、登录态导出命令、只读 live HTML 采集命令、搜索页截图留证、页面网络响应摘要留证和 live HTML 闭环评估命令。

当前远程预览入口：

```text
https://4e3f58027aff37.lhr.life/?access_token=p0TFJ2PG7Vwv5Rm1DixKB9YzeluIsWot
```

不带 token 的 `/api/health` 已验证返回 401。

## Phase 完成情况

- Phase 0 初始化：完成。项目结构、`pyproject.toml`、配置样例、README、测试框架均已建立。
- Phase 1 业务核心与 mock 回放：完成。品番/JAN 标准化、匹配、闲鱼清洗、成本、估价、机会判断和 mock adapter 已实现并测试。
- Phase 2 SQLite 与 CLI：完成。核心表、幂等初始化、watchlist、扫描、导入、回测、报告、复核、通知、live readiness、live HTML 采集相关 CLI 均已实现。
- Phase 3 通知模块：完成。飞书/钉钉 dry-run 和真实 webhook 路径已实现，webhook 明文不会通过 Web 配置接口泄露。
- Phase 4 Wameiji Browser-Harness：部分完成。已实现安全状态机、手动入口、HTML 快照解析、只读 Playwright HTML 采集命令；真实登录态页面端到端读取需要用户本机环境验证。
- Phase 5 Xianyu Browser-Harness：部分完成。已实现安全状态机、Playwright `storage_state` 检查、登录态导出命令、只读搜索页 HTML 采集和解析；真实登录态读取需要用户扫码登录后验证。
- Phase 6 调度、二次复核与回测：完成。P0/P1/P2 队列、候选二次复核、人工复核、报告和回测均已实现。

## 真实数据接入路径

安装可选浏览器依赖：

```powershell
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

由用户扫码/登录并导出闲鱼登录态：

```powershell
python -m cd_monitor.cli xianyu-login-state --output data/xianyu_state.json --timeout-seconds 180
```

只读采集两边搜索页 HTML：

```powershell
python -m cd_monitor.cli capture-live-html --source xianyu --catalog-no SRCL-3520 --state-file data/xianyu_state.json --output data/snapshots/xianyu_SRCL-3520.html --screenshot-output data/screenshots/xianyu_SRCL-3520.png --network-output data/snapshots/xianyu_SRCL-3520.network.json
python -m cd_monitor.cli capture-live-html --source xianyu --catalog-no SRCL-3520 --profile-dir data/browser_profiles/goofish --output data/snapshots/xianyu_SRCL-3520.html --screenshot-output data/screenshots/xianyu_SRCL-3520.png --network-output data/snapshots/xianyu_SRCL-3520.network.json
python -m cd_monitor.cli capture-live-html --source wameiji --catalog-no SRCL-3520 --output data/snapshots/wameiji_SRCL-3520.html --screenshot-output data/screenshots/wameiji_SRCL-3520.png --network-output data/snapshots/wameiji_SRCL-3520.network.json
```

只读闭环扫描并落库：

```powershell
python -m cd_monitor.cli scan-live-html --catalog-no SRCL-3520 --state-file data/xianyu_state.json --db data/cd_monitor.db --snapshot-dir data/snapshots
python -m cd_monitor.cli scan-live-html --catalog-no SRCL-3520 --profile-dir data/browser_profiles/goofish --db data/cd_monitor.db --snapshot-dir data/snapshots
```

## 安全边界

已明确禁止并未实现：

- 自动下单、自动付款、自动加购
- 自动发布闲鱼商品
- 自动联系卖家或发送交易消息
- 绕过验证码、安全验证、Cloudflare、2FA
- 账号池、代理池、反检测规避

真实读取命令只允许打开搜索页、等待页面加载、保存 HTML、保存搜索页截图、保存页面自己加载到的站点响应摘要、解析可见文本。遇到验证码、安全验证或登录失效时返回 `human_required`，不生成伪机会。

## 验证结果

最近一次全量验证：

```text
python -m pytest
125 passed

python -m ruff check src tests
All checks passed
```

外链验证：

- `app.js` 包含 `真实数据接入流程`、`xianyu-login-state`、`capture-live-html`、`--screenshot-output`、`--profile-dir`、`--network-output`、`scan-live-html`
- 首页包含 `数据接入状态`、`挖煤姬实时`、`闲鱼实时`
- 不带 token 的 `/api/health` 返回 401

## 未完成 / 需要用户参与

- 真实闲鱼登录态需要用户扫码登录后生成 `data/xianyu_state.json`。
- 真实挖煤姬网页登录态读取需要用户本机可见浏览器环境验证。
- 匿名 localhost.run 隧道域名不稳定，长期使用建议改为有账号/固定域名的隧道或部署到受控内网/VPS。

## 下一步建议

1. 用户扫码运行 `xianyu-login-state` 生成真实 state 文件。
2. 用 `scan-live-html` 对 1-2 个低风险品番做真实只读采集验证。
3. 根据真实页面 HTML 调整 parser 的站点细节字段。
4. 若需要长期远程使用，换固定域名隧道并保留 `WEB_ACCESS_TOKEN`。


---

# Phase M+ Final State (2026-06-20)

## 增量交付（Wameiji 侧 + spec compliance audit）

**Wameiji 侧从头实现**（参考 `reference_usagi/` 的 ai-goofish-monitor 模式）：

- `src/cd_monitor/sources/wameiji_browser.py`：~830 行
  - `WameijiBrowserAdapter`（兼容旧安全骨架）
  - `WameijiBrowserAdapterWithRunner`（profile_dir 走 Playwright）
  - `WameijiPlaywrightRunner`：真实 Chromium 读取执行器
  - `_WameijiCardParser` + `_extract_generic_items`：双路径 HTML 解析（spec §3.1 全部字段：title / price / source_site / url / image_url / availability / condition_text / fees_hint / raw_text）
  - `_requires_human`：captcha / Cloudflare / 登录失效 检测
  - `_StopAndHuman` / `_TransientError`：内部异常类型
  - `_safe_screenshot` / `_save_raw_html_snapshot`：snapshot 保存
- `src/cd_monitor/services/notify_dispatcher.py`：飞书/钉钉 fanout + dry-run
- `src/cd_monitor/web_server.py`：新增 2 个 POST 端点（live-html / live-watchlist）
- `web/index.html` + `web/app.js`：新增 2 个前端按钮（实时扫描 / 实时清单）

**Spec compliance 修补**（Phase O 审计）：

- spec §4.3：`rental` / `sample` / `poor_condition` 现在出现在 `Opportunity.risk_labels`（之前只停留在 `MatchResult.negative_reasons`）
- spec §3.1：`MarketItem.fees_hint` 字段 + `_detect_fees_hint` 自动从 raw_text 提取代购手续费 / 加固 / 拍照 / 检查费 / 保障 / 合单费 / 国内运费 等费用提示
- spec §3.1：`_WameijiCardParser` 升级，把 card 内所有文本节点纳入 raw_text（之前只采 `data-*` 属性），让 `availability` / `condition_text` / `fees_hint` 在真实 HTML 也能命中

**测试覆盖**：

- 2026-06-20 基线：194 passed
- 当前：215 passed（+21 个新测试）
  - `test_evaluator.py`：+4（§4.3 风险标签）
  - `test_wameiji_browser_runner.py`：+5（§3.1 费用提示）
  - `test_cost_model_walkthrough.py`：+5（新文件，§6 端到端手算对照）
  - `test_wameiji_realistic_fixtures.py`：+7（新文件，§3.1 真实 HTML 夹具）

**设计文档**：

- `WAMEIJI_DESIGN.md`：~830 行
  - §1-9：项目定位 / 架构 / 行为准则 / 用法 / 配置 / 测试 / CLI / 架构图 / Live+Notify 端到端
  - 附录 A-J：流程图 / 错误处理矩阵 / 成本走查 / 通知模板 / 上手命令 / 与参考项目对比 / 当前进度 / 未来路线 / 变更时间线
  - 附录 K：**Spec Coverage Matrix**（spec §1.1/§1.2/§3.1/§4.3/§6/§7/§8 全条款逐条对照代码+测试位置）
  - 附录 L：**Phase O changelog**
  - 附录 M：**Cost Model Walkthrough**（带手算数字）
  - 附录 N：**Final State 交付清单**

**端到端 CLI 验证**（已在本机跑通）：

```powershell
$env:PYTHONPATH = "F:\WAMEIJI-XIANYU-full-handoff\src"
$env:CD_DB_PATH = "C:\Users\19097\AppData\Local\Temp\cd_monitor.db"
Remove-Item $env:CD_DB_PATH -Force
python -m cd_monitor.cli init-db --db $env:CD_DB_PATH
python -m cd_monitor.cli add-watch --db $env:CD_DB_PATH --catalog-no SRCL-3520 --artist "Artist" --edition "初回限定"
python -m cd_monitor.cli scan-once --db $env:CD_DB_PATH --catalog-no SRCL-3520 --notify --notify-dry-run --notify-channel feishu
# → 1 strong_alert opportunity, landed_cost=103.07 CNY（与 spec §6.3 手算一致）
```

## 死代码（待 supervisor 释放后清理）

- `src/cd_monitor/app.py`（5KB FastAPI 原型，无任何引用）
- `src/cd_monitor/services/scraper/`（30+ 文件，无任何引用）
- `src/cd_monitor/infrastructure/`（config/ + external/ai_* + notification_clients/ + persistence/，无任何引用）
- `MIGRATION_PLAN.md`（已由 WAMEIJI_DESIGN.md 取代）

清理命令（用户侧执行）：

```powershell
Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\app.py -Force
Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\services\scraper -Recurse -Force
Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\infrastructure -Recurse -Force
Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\MIGRATION_PLAN.md -Force
```


---



---

# Phase R（2026-06-25）：Wameiji Chrome 扩展登录态提取器

## 一句话总结
抄 `reference_usagi/chrome-extension/` 的轻量级模式做挖煤姬侧：用户在浏览器登录 meruki.cn → 扩展采集 cookies / localStorage / env / headers → 用户复制 JSON → 粘贴到 web UI（或 `cli wameiji-login-state-import`）→ 后端转成 Playwright `storage_state` 写盘 → `WameijiPlaywrightRunner` 直接 `browser.new_context(storage_state=...)` 加载。**不再需要用户维护整个 Chrome user-data-dir**。

## 改动统计
- 新文件：4 个扩展文件（manifest / background / popup.html / popup.js）+ 1 个 service（wameiji_login_state.py）+ 2 个测试文件 + 1 个隐私政策 HTML + 1 个扩展 README
- 修改文件：9（runner / config / live_browser_capture / live_capture_scan / web_server / cli / web/index.html / web/app.js / web/styles.css）
- 测试新增：17（test_wameiji_login_state.py 14 + test_cli_wameiji_login_state.py 3） + 7 扩展（test_wameiji_browser_runner.py）
- 测试总数：240/240 ✅

## 完整文件清单

```
chrome-extension/wameiji-login-state-extractor/
├── manifest.json                                   (NEW)
├── background.js                                   (NEW)
├── popup.html                                      (NEW)
├── popup.js                                        (NEW)
├── wameiji-login-state-privacy.html                (NEW)
└── README.md                                       (NEW)

src/cd_monitor/services/wameiji_login_state.py      (NEW, ~220 lines)
src/cd_monitor/sources/wameiji_browser.py           (MODIFIED: state_file path)
src/cd_monitor/services/live_browser_capture.py     (MODIFIED: wameiji_state_file)
src/cd_monitor/services/live_capture_scan.py        (MODIFIED: wameiji_state_file)
src/cd_monitor/config.py                            (MODIFIED: wameiji_state_file + WAMEIJI_STATE_FILE env)
src/cd_monitor/web_server.py                        (MODIFIED: POST/DELETE /api/login-state/wameiji + _browser_status surfacing)
src/cd_monitor/cli.py                               (MODIFIED: wameiji-login-state-import subcmd + --wameiji-state-file flags)
web/index.html                                      (MODIFIED: wameiji login state import panel)
web/app.js                                          (MODIFIED: importWameijiLoginState / refresh / delete)
web/styles.css                                      (MODIFIED: form-hint / form-actions / event-item.ok/.warn / danger)
tests/test_wameiji_login_state.py                   (NEW, 14 tests)
tests/test_cli_wameiji_login_state.py               (NEW, 3 tests)
tests/test_wameiji_browser_runner.py                (EXTENDED, +7 tests)
WAMEIJI_DESIGN.md                                   (MODIFIED: top changelog + 附录 R)
DELIVERY_REPORT.md                                  (MODIFIED: 本节)
```

## 端到端 smoke 验证（已在本机跑通）

```powershell
# 启动 web server（带 WAMEIJI_STATE_FILE env）
$env:CD_MONITOR_DB_PATH = "C:\Users\19097\AppData\Local\Temp\cd_monitor_web.db"
$env:WAMEIJI_STATE_FILE = "C:\Users\19097\AppData\Local\Temp\wameiji_state.json"
python -m cd_monitor.cli web --host 127.0.0.1 --port 9877

# 上传扩展采集的 JSON
$payload = @{ content = '{"capturedAt":"2026-06-25","pageUrl":"https://meruki.cn/","cookies":[{"name":"session","value":"SECRET","domain":".meruki.cn","path":"/","expires":9999999999,"httpOnly":true,"secure":true,"sameSite":"Lax"}],"storage":{"local":{"token":"X"},"session":{}}}' } | ConvertTo-Json
Invoke-RestMethod -Method POST http://127.0.0.1:9877/api/login-state/wameiji -ContentType application/json -Body $payload
# → {"output":"...","cookie_count":1,"origin_count":1,"wameiji_cookie_domains":[".meruki.cn"],"login_state_ready":true}

# 检查状态
Invoke-RestMethod http://127.0.0.1:9877/api/login-state/wameiji
# → {"state_file":"...","status":"ready","cookie_domains":[".meruki.cn"]}

# 浏览器读取状态会反映 login_state_ready
(Invoke-RestMethod http://127.0.0.1:9877/api/browser/status).wameiji
# → login_state_ready: true, state_file_status: ready, state_cookie_domains: [.meruki.cn]

# CLI 路径
python -m cd_monitor.cli wameiji-login-state-import pasted.json --output data/wameiji_state.json
# → {"output":"data\\wameiji_state.json","cookie_count":1,"login_state_ready":true,...}

# 删除
Invoke-RestMethod -Method DELETE http://127.0.0.1:9877/api/login-state/wameiji
# → {"deleted":true,"path":"...\\wameiji_state.json"}
```

## 用户操作流程（终态）

### 一次性安装扩展
1. Chrome 打开 `chrome://extensions` → Developer mode → Load unpacked → 选 `chrome-extension/wameiji-login-state-extractor/`
2. 工具栏出现 Wameiji 图标

### 每次需要登录态时
1. Chrome 打开 meruki.cn → 登录
2. 点扩展图标 → 「采集登录态」→ 「复制 JSON 到剪贴板」
3. 打开 CD Monitor 前端（http://127.0.0.1:9877/）→ 操作面板 → 「挖煤姬登录态」区 → 粘贴 JSON → 「导入登录态」
4. 数据接入状态条立刻显示「挖煤姬实时 · 登录态已就绪」

### 触发实时扫描
- 点顶部「实时扫描」按钮 → `cli scan-live-html --catalog-no SRCL-3520` 走 `wameiji_state_file` 自动加载存储态 + 真实读取

## 安全姿态（重申）
- 扩展 host_permissions = `*://*.meruki.cn/*`，只读挖煤姬域，不会触及其他网站
- 扩展不做持久化，仅在用户点击时内存拼 JSON + 用户主动复制
- 后端校验后写 `data/wameiji_state.json`，文件含 Playwright `storage_state`（cookies + localStorage） + 原始扩展快照（调试用，可选 `--no-keep-raw` 关闭）
- 整个 wameiji_state.json 几 KB，可版本控制、可跨机器 git pull

## 已知边界
- 扩展不抓 IndexedDB（meruki.cn 没发现有用 IndexedDB 存登录态）
- 不抓 service worker cache（参考项目也没抓）
- 如果挖煤姬登录后用 fingerprint / TLS pinning，Playwright 加载 storage_state 后仍可能被识别为 headless → 已在 `_attempt` 里走 `--disable-blink-features=AutomationControlled` + 人类节奏 sleep；命中 captcha / security_check 立即 human_required

## 后续可做（Phase S+，非本 Phase）
- 扩展支持自动定时采集（每个 N 小时弹一次）
- 在 web 面板显示 storage_state 的过期时间（按 cookies.expires 最大值）
- 给 storage_state 加密码保护（用用户设置的 master password 加密落盘）



---

# Phase S（2026-06-26）：Xianyu Chrome 扩展登录态提取器

## 一句话总结
镜像 Phase R（挖煤姬）做闲鱼侧：用户在 Chrome 登录 goofish.com → 扩展采集 cookies / localStorage / env / headers → 用户复制 JSON → 粘贴到 web UI（或 `cli xianyu-login-state-import`）→ 后端转成 Playwright `storage_state` 写盘 → `XianyuBrowserAdapter(state_file=...)` 直接加载。**不再需要为闲鱼侧单独打开 headed Chromium 扫码**。

## 三种 xianyu 登录态获取路径并存
| 路径 | 何时用 | 用户体验 |
|---|---|---|
| `cli xianyu-login-state`（Playwright headed）| 用户没有 logged-in Chrome | 弹出 headed Chromium → 扫码 → cookies 出现后自动保存 |
| `cli xianyu-login-state-import <file>`（Phase S 新增）| 用户有 logged-in Chrome | 装扩展 → 复制 JSON → 一次 CLI 命令导入 |
| web UI「导入闲鱼登录态」面板（Phase S 新增）| 同上，更轻量 | 装扩展 → 复制 JSON → 粘贴到 web 表单 → 点导入按钮 |

所有路径都生成同一份 `data/xianyu_state.json`（Playwright `storage_state` 形状），`XianyuBrowserAdapter(state_file=...)` 一视同仁加载。

## 改动统计
- 新文件：6 个（4 个扩展核心 + privacy + README）
- 修改文件：4（`xianyu_login_state.py` / `web_server.py` / `cli.py` / `web/index.html` + `web/app.js`）
- 测试新增：15（test_xianyu_login_state.py +12，test_cli_xianyu_login_state.py +3）
- 测试总数：254/254 ✅（含 2 env-bound deselect）

## 完整文件清单

```
chrome-extension/xianyu-login-state-extractor/
├── manifest.json                                   (NEW, host=*.goofish.com)
├── background.js                                   (NEW, ~7.7KB)
├── popup.html                                      (NEW, 红色主题)
├── popup.js                                        (NEW)
├── xianyu-login-state-privacy.html                (NEW, v2026-06-26)
└── README.md                                       (NEW)

src/cd_monitor/services/xianyu_login_state.py       (EXTENDED: +save/load/inspect/ImportError/_is_xianyu_auth_domain)
src/cd_monitor/web_server.py                       (EXTENDED: GET/POST/DELETE /api/login-state/xianyu + _browser_status surfacing)
src/cd_monitor/cli.py                              (EXTENDED: xianyu-login-state-import subcommand)
web/index.html                                     (EXTENDED: xianyu login state import panel)
web/app.js                                         (EXTENDED: importXianyuLoginState/refresh/delete)
tests/test_xianyu_login_state.py                   (EXTENDED: +12 tests)
tests/test_cli_xianyu_login_state.py               (EXTENDED: +3 tests)
WAMEIJI_DESIGN.md                                  (EXTENDED: top changelog + 附录 S)
DELIVERY_REPORT.md                                 (EXTENDED: 本节)
```

## 转换器复用策略
`extension_snapshot_to_playwright_state` 和 `parse_extension_snapshot` 是 generic 的（只做 cookies / localStorage 形状转换，不做 auth domain 检查）。两个 service 模块各自负责：
- `wameiji_login_state`：auth domains = (`.meruki.cn`,)
- `xianyu_login_state`：auth domains = (`.goofish.com`, `xianyu`, `.taobao.com`, `.alibaba.com`, `.tmall.com`)

转换器代码 0 重复。如果将来要加第三个站（mercari.jp / surugaya.jp / 骏河屋 / 煤炉 等），只需新增一个 service 模块 + 一组 host 的扩展。

## 端到端 smoke 验证（已在本机跑通）

```powershell
# 启动 web server（带 WAMEIJI_STATE_FILE + XIANYU_STATE_FILE env）
$env:CD_MONITOR_DB_PATH = "C:\Users\19097\AppData\Local\Temp\cd_monitor_web.db"
$env:WAMEIJI_STATE_FILE = "C:\Users\19097\AppData\Local\Temp\wameiji_state.json"
$env:XIANYU_STATE_FILE  = "C:\Users\19097\AppData\Local\Temp\xianyu_state.json"
python -m cd_monitor.cli web --host 127.0.0.1 --port 9879

# 上传 wameiji 扩展采集的 JSON
$w = @{ content = '{"capturedAt":"x","pageUrl":"https://meruki.cn/","cookies":[{"name":"session","value":"SECRET-W","domain":".meruki.cn","path":"/","expires":9999999999,"httpOnly":true,"secure":true,"sameSite":"Lax"}],"storage":{"local":{},"session":{}}}' } | ConvertTo-Json
Invoke-RestMethod -Method POST http://127.0.0.1:9879/api/login-state/wameiji -ContentType application/json -Body $w
# → {"output":"...\\wameiji_state.json","cookie_count":1,"origin_count":1,"wameiji_cookie_domains":[".meruki.cn"],"login_state_ready":true}

# 上传 xianyu 扩展采集的 JSON
$x = @{ content = '{"capturedAt":"x","pageUrl":"https://www.goofish.com/","cookies":[{"name":"cookie2","value":"SECRET-X","domain":".goofish.com","path":"/","expires":9999999999,"httpOnly":true,"secure":true,"sameSite":"Lax"}],"storage":{"local":{},"session":{}}}' } | ConvertTo-Json
Invoke-RestMethod -Method POST http://127.0.0.1:9879/api/login-state/xianyu -ContentType application/json -Body $x
# → {"output":"...\\xianyu_state.json","cookie_count":1,"origin_count":2,"xianyu_cookie_domains":[".goofish.com"],"login_state_ready":true}

# 检查两侧状态
Invoke-RestMethod http://127.0.0.1:9879/api/login-state/wameiji
# → {"state_file":"...","status":"ready","cookie_domains":[".meruki.cn"]}
Invoke-RestMethod http://127.0.0.1:9879/api/login-state/xianyu
# → {"state_file":"...","status":"ready","cookie_domains":[".goofish.com"]}

# 浏览器读取状态会反映 login_state_ready
(Invoke-RestMethod http://127.0.0.1:9879/api/browser/status).wameiji
# → login_state_ready: true, state_file_status: ready, state_cookie_domains: [.meruki.cn]
(Invoke-RestMethod http://127.0.0.1:9879/api/browser/status).xianyu
# → login_state_ready: true, state_file_status: ready, state_cookie_domains: [.goofish.com]

# CLI 路径（与 web 路由等价）
python -m cd_monitor.cli wameiji-login-state-import pasted_wameiji.json
python -m cd_monitor.cli xianyu-login-state-import pasted_xianyu.json

# 删除
Invoke-RestMethod -Method DELETE http://127.0.0.1:9879/api/login-state/wameiji
Invoke-RestMethod -Method DELETE http://127.0.0.1:9879/api/login-state/xianyu
```

## 用户操作流程（终态）

### 一次性安装两个扩展
1. Chrome 打开 `chrome://extensions` → Developer mode → Load unpacked
2. 先选 `chrome-extension/wameiji-login-state-extractor/` → 工具栏出现蓝色 Wameiji 图标
3. 再选 `chrome-extension/xianyu-login-state-extractor/` → 工具栏出现红色 Xianyu 图标

### 每次需要登录态时（wameiji 侧）
1. Chrome 打开 meruki.cn → 登录
2. 点蓝色扩展 → 「采集登录态」→「复制 JSON 到剪贴板」
3. 打开 CD Monitor 前端（http://127.0.0.1:9879/）→ 操作面板 → 「挖煤姬登录态」区 → 粘贴 → 「导入登录态」

### 每次需要登录态时（xianyu 侧）
1. Chrome 打开 goofish.com → 登录
2. 点红色扩展 → 「采集登录态」→「复制 JSON 到剪贴板」
3. 打开 CD Monitor 前端 → 操作面板 → 「闲鱼登录态」区 → 粘贴 → 「导入登录态」

### 触发实时扫描
- 点顶部「实时扫描」→ `cli scan-live-html --catalog-no SRCL-3520` 自动加载两侧 storage_state + 真实读取

## 安全姿态（与 Phase R 一致）
- 扩展 host_permissions = `*://*.goofish.com/*`，只读闲鱼域，不会触及其他网站
- 扩展不做持久化，仅在用户点击时内存拼 JSON + 用户主动复制
- 后端校验后写 `data/xianyu_state.json`，文件含 Playwright `storage_state` + 原始扩展快照（可选 `--no-keep-raw` 关闭）
- 整个 xianyu_state.json 几 KB，可版本控制、可跨机器 git pull

## 已知边界
- 扩展不抓 IndexedDB（goofish.com 没有发现有用 IndexedDB 存登录态）
- 不抓 service worker cache
- 闲鱼风控比挖煤姬更严（mobile-only login flow / 滑块 / 短信二次验证），Playwright 加载 storage_state 后仍可能撞风控 → 已在 `live_browser_capture.py` 走人类节奏 sleep；命中 captcha / security_check 立即 human_required
- storage_state 过期：cookie `expires` 字段代表秒数级别过期时间，扩展采集后若用户隔几天再用会失效；本工具不自动续期

## 后续可做（Phase T+）
- 自动续期：定时跑 CLI 提醒用户「storage_state 还有 N 天过期」
- 给 storage_state 加密码保护（用用户设置的 master password 加密落盘）
- web UI 显示 storage_state 的过期时间（按 cookies.expires 最大值）
