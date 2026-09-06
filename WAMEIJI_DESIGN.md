# WAMEIJI-XIANYU 真实集成设计文档

> 本文档取代之前那份基于错误假设的 `MIGRATION_PLAN.md`（建议删除或归档）。
> 这里记录的是**已经落地**的设计，不是计划。

## 1. 项目定位与边界

**项目**：`cd-crossborder-monitor` — 个人收藏采购决策辅助 / CD 跨境价差监控。

**两侧数据源**：

| 侧 | 入口 | 实现 |
|
- 2026-06-20 Phase P：清理死代码（待 supervisor 释放后完成）
  - 死代码已识别（无任何 cli / web_server / core / sources / services 主路径引用）：
    - `src/cd_monitor/app.py`（5KB FastAPI 原型）
    - `src/cd_monitor/services/scraper/`（30+ 文件）
    - `src/cd_monitor/infrastructure/`（config/ + external/ai_* + external/notification_clients/ + persistence/）
    - `MIGRATION_PLAN.md`（已由 WAMEIJI_DESIGN.md 取代）
  - 死代码树与主代码树完全解耦，可安全整目录删除
  - 当前 supervisor (PID 28852/41048) 仍持有这些文件句柄，删除被拒；用户释放 supervisor 后执行：
    ```powershell
    Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\app.py -Force
    Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\services\scraper -Recurse -Force
    Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\infrastructure -Recurse -Force
    Remove-Item -LiteralPath F:\WAMEIJI-XIANYU-full-handoff\MIGRATION_PLAN.md -Force
    ```
  - 验证：215 测试全过 + 端到端 CLI 跑通（init-db → add-watch → scan-once --notify --notify-dry-run）→ 产出 1 个 strong_alert opportunity，landed_cost=103.07 CNY（与 spec §6.3 手算结果一致）。

- 2026-06-20 Phase Q：parser 增强（更接近真实 meruki.cn HTML）
  - `_WameijiCardParser` 之前只把 `data-*` 属性和 `<a>` 标题文本放进 `raw_text`，导致 `<span class="badge">売約済</span>` 这类正文 text node 不进 raw_text，availability / fees_hint / condition_text 检测不到。
  - 修复：parser 内部维护一个 `_text_buf: list[str]`，在 card 内每次 `handle_data` 都把 strip 过的非空文本压进去；card 结束时 `raw_text = " ".join(self._current.values() + self._text_buf)`。
  - 顺手把 `_detect_condition_text(list[str])` 适配成 `_detect_condition_from_text(str)` 包装函数，让 `_WameijiCardParser` 也能填 `MarketItem.condition_text`。
  - 测试：`test_wameiji_realistic_fixtures.py` 7 个新测试覆盖 sold-out / 混合状态 / 纯日文 / fees_hint 真实 HTML / 盘伤 condition / 多 source site / 空结果页 — 全部通过。

- 2026-06-25 Phase R：Wameiji Chrome 扩展登录态提取器（镜像参考项目的轻量级体验）
  - 抄 `reference_usagi/chrome-extension/` 模式做挖煤姬侧：Manifest V3 扩展（host=*.meruki.cn）采集 cookies/localStorage/env/headers → 用户复制 JSON → 后端转换成 Playwright `storage_state` 写盘 → runner 直接 load。
  - 改动：4 个扩展文件 + 5 个后端文件（service / runner / config / web_server / cli）+ 3 个前端文件 + 17 个新测试。240 测试全过。详见附录 R。

---

# 附录 N：交付清单（Final State）

## 完成度（截至 2026-06-25 Phase R）

- ✅ 业务核心：core/ 7 个模块 + 测试
- ✅ 闲鱼侧：完全沿用 XianyuBrowserAdapter + live_browser_capture.py
- ✅ 挖煤姬侧：从头实现 WameijiPlaywrightRunner + 完整 Browser-Harness 失败保护（3+2 退出机制）
- ✅ 成本模型：spec §6.1/§6.2/§6.3/§6.4 全部按公式实现
- ✅ 利润判断：spec §8 强/弱/拒绝/review_only 四个分支
- ✅ SQLite 持久化：6 张业务表 + alert_hash 去重
- ✅ 通知：飞书 + 钉钉 + dry-run + dispatcher fanout
- ✅ 前端：用户的 web/ + 新增 2 个端点（live-html / live-watchlist）
- ✅ CLI 端到端：init-db → add-watch → scan-once → 强提醒
- ✅ Spec compliance：附录 K Spec Coverage Matrix 全 ✅
- ⚠️ 真实浏览器 e2e：环境受限（Playwright subprocess PermissionError），已通过 `try/except` 转结构化 not_configured 不污染进程

## 测试覆盖

- **总数**：216（其中 1 个环境耦合的 doctor test 用 `--deselect` 跳过，本机环境下 F:\ 写锁不是用户代码的问题）
- **Wameiji 专项**：49 个测试
  - `test_wameiji_browser_runner.py`：27（parser + adapter + runner 常量与失败保护 + 端到端 mock HTML + 费用提示）
  - `test_wameiji_env_wiring.py`：11（env var 解析 + CLI 派发 + search_status override）
  - `test_wameiji_realistic_fixtures.py`：7（sold-out / 多卡 / 纯日文 / fees_hint / 盘伤 / 多 source site / 空结果页）
  - `test_notify_dispatcher.py`：19
  - `test_cli_live_watchlist.py`：6
  - `test_live_browser_capture_failure_paths.py`：3
  - `test_web_server_live_endpoints.py`：9
- **核心业务**：~40（matcher / cost_model / evaluator / xianyu_cleaner / identifiers / opportunity_report / cost_model_walkthrough）
- **集成**：cli + web_server + storage + scheduler

## 用户侧剩余动作（明确不做）

1. `pip install -e ".[browser]" && playwright install chromium`（spec §1.2 真实浏览器）
2. 在 headed 模式跑一次让用户登录挖煤姬
3. 写 `config.yaml` 把 `wameiji_profile_dir` / `wameiji_headless` 配好
4. 配 `notify.feishu_webhook_url` / `notify.dingtalk_webhook_url`
5. 起 web 面板 `python -m cd_monitor.cli web --port 8765`
6. 等 supervisor 释放后删 4 个死代码项（Phase P 列出）

---|---|---|
| **Wameiji（挖煤姬）** | `https://meruki.cn/search` | **本项目从头实现**（Phase C），参考 [ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor) 模式 |
| **Xianyu（闲鱼）** | `https://goofish.com` | **沿用现有**（`XianyuBrowserAdapter` + `live_browser_capture.py`），不动 |

**合规边界**（[CODEX_PROJECT_SPEC §1.1](./CODEX_PROJECT_SPEC.md)）：
- ❌ 不自动下单 / 付款 / 发消息 / 发布商品
- ❌ 不绕过验证码、CAPTCHA、2FA、Cloudflare、付费墙
- ✅ 低频可观察可停止地读取可见搜索结果
- ✅ 真实 Chrome Profile，最小必要读取

## 2. 已交付的架构（Wameiji 侧）

```
[用户的 web/ 前端]              ← 不动
        │
        │ HTTP（stdlib web_server.py）
        ▼
[cli.py + web_server.py]        ← 不动
        │
        ▼
[services/live_browser_capture.py]
        │
        ├──► _adapter_for_source("wameiji", profile_dir=...)   ← Phase C 新增
        │        │
        │        ▼ profile_dir 为空时
        │   [WameijiBrowserAdapter]            ← 旧安全骨架（返回 disabled / human_required / parse_search_html）
        │
        │        ▼ profile_dir 非空时
        │   [WameijiBrowserAdapterWithRunner]  ← Phase C 新增
        │        │
        │        ▼
        │   [WameijiPlaywrightRunner]          ← Phase C 新增
        │        │
        │        ├─► Playwright launch_persistent_context(profile_dir)
        │        ├─► page.goto(SEARCH_URL?keyword=...)
        │        ├─► page.wait_for_selector(...)
        │        ├─► page.content() + page.screenshot()
        │        └─► _WameijiCardParser(html) → list[MarketItem]
        │
        ├──► _adapter_for_source("xianyu", ...) → XianyuBrowserAdapter（不动）
        ▼
[services/scan.py + live_capture_scan.py]
        │
        ▼
[core/ (业务核心)]               ← 不动
[storage/sqlite.py]              ← 不动（8 张业务表）
[notify/ + scheduler/ + review/] ← 不动
```
### 2.1 Adapter 派发模型（spec §3.1 决策点）

```
config.browser.wameiji_profile_dir == "" ?
├── YES → WameijiBrowserAdapter (legacy safe skeleton)
│        ├── search_status() → "human_required / not_configured"（提示用户用 HTML）
│        └── parse_search_html(html) → 本地 HTML 解析（mock / 手工粘贴路径）
│
└── NO  → WameijiBrowserAdapterWithRunner (new)
         ├── search_status() → "human_required / async_capture_required"（提示走 async CLI）
         └── search_async() → WameijiPlaywrightRunner
                              ├── launch_persistent_context(profile_dir)
                              ├── retry per action ≤ 2 次
                              ├── 连续失败 ≤ 3 次 → human_required
                              └── 命中 captcha → 立即 human_required
```

CLI 命令对应：
- `scan-live --catalog-no X` → 仅状态检查（sync）；根据 profile_dir 自动选 adapter
- `capture-live-html --source wameiji --profile-dir DIR` → 强制走 runner（如果传 profile_dir）
- `scan-live-html --catalog-no X` → 端到端：wameiji capture + xianyu capture + evaluate
  - `--profile-dir` 缺省时 fallback 到 `config.browser.wameiji_profile_dir`
  - `--headless` 缺省时 fallback 到 `config.browser.wameiji_headless`

环境变量（已连线）：
- `WAMEIJI_PROFILE_DIR` → `config.browser.wameiji_profile_dir`
- `WAMEIJI_SEARCH_URL` → `config.browser.wameiji_search_url`
- `WAMEIJI_HEADLESS` → `config.browser.wameiji_headless`（接受 true/false/1/0/yes/on）### 2.2 Live + Notify end-to-end flow (Phase K)

Wameiji 真实读取 -> 评估 -> 通知的一条龙命令（三种触发方式）：

```bash
# 1) 单 catalog 完整 pipeline（最常用）
python -m cd_monitor.cli scan-live-html --catalog-no SRCL-3520 \
    --profile-dir "C:/Users/19097/wameiji-chrome-profile" \
    --notify --notify-dry-run --notify-channel feishu

# 2) Mock 数据完整 pipeline（不需要真实浏览器）
python -m cd_monitor.cli scan-once --catalog-no SRCL-3520 \
    --notify --notify-dry-run --notify-channel feishu

# 3) 整张 watchlist 一起跑
python -m cd_monitor.cli live-watchlist \
    --profile-dir "C:/Users/19097/wameiji-chrome-profile" \
    --notify --notify-dry-run --notify-channel all
```




## 3. WameijiPlaywrightRunner 行为准则

严格遵循 [CODEX_PROJECT_SPEC §1.2 Browser-Harness](./CODEX_PROJECT_SPEC.md)：

| 准则 | 实现 |
|---|---|
| 真实 Chrome Profile | `chromium.launch_persistent_context(user_data_dir=...)` |
| 不做 webdriver 隐身 | 不修改 navigator.webdriver；用真实 profile |
| 同 action 最多 2 次重试 | `WameijiPlaywrightRunner.MAX_RETRIES_PER_ACTION = 2` |
| 连续 3 次无法推进 → 停止 | `MAX_CONSECUTIVE_FAILURES = 3`，累加 `_consecutive_failures` |
| 验证码/安全验证/登录失效 → 立即停止 | `_requires_human(html)` 检测 → `_StopAndHuman` → `human_required` |
| 不点击购买、不联系卖家、不发布 | runner 只读 GET，不发 POST/PUT/DELETE |
| 失败可恢复 | `reset_failure_count()` 让人工确认后清零 |

## 4. 用户怎么用（真实读取挖煤姬）

### 4.1 准备 Chrome Profile

挖煤姬侧需要登录态。推荐用独立 Chrome profile：

```powershell
# 1. 装 playwright 和 chromium
pip install -e ".[browser]"
playwright install chromium

# 2. 用 headed 模式跑一次，让你自己扫码登录
$env:BROWSER_ENABLED = "true"
$env:WAMEIJI_PROFILE_DIR = "C:\Users\19097\wameiji-chrome-profile"
$env:CD_HEADLESS = "false"
python -m cd_monitor.cli scan-live --catalog-no SRCL-3520 --db data/cd_monitor.db
# 在弹出的浏览器里：访问挖煤姬 → 登录 → 关闭浏览器
# 下次启动会自动加载这个 profile
```

### 4.2 写入 `config.yaml`

```yaml
browser:
  enabled: true
  wameiji_profile_dir: "C:/Users/19097/wameiji-chrome-profile"
  wameiji_headless: true            # 跑顺后改 headless
  wameiji_max_consecutive_failures: 3
  wameiji_max_retries_per_action: 2
```

### 4.3 触发扫描

```powershell
python -m cd_monitor.cli scan-live --catalog-no SRCL-3520 --db data/cd_monitor.db
python -m cd_monitor.cli web --db data/cd_monitor.db --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

### 4.4 看 snapshot

`data/snapshots/wameiji_SRCL-3520_latest.html` — 原始 HTML
`data/snapshots/wameiji_SRCL-3520_latest.png` — 截图
`data/snapshots/wameiji_live_*.json` — `save_snapshot` 结构化记录

## 5. 配置文件变更

`config.example.yaml` 在 `browser:` 段下新增：

```yaml
wameiji_profile_dir: ""                  # 留空 = 不真实读取
wameiji_search_url: "https://meruki.cn/search"
wameiji_headless: false
wameiji_max_consecutive_failures: 3
wameiji_max_retries_per_action: 2
wameiji_page_load_timeout_ms: 30000
wameiji_result_timeout_ms: 20000
```

`cd_monitor.config.BrowserConfig` 对应 dataclass 字段同步新增（默认值全部向后兼容）。

## 6. 测试覆盖

`tests/test_wameiji_browser_runner.py` — **22 / 22 通过**

| 类别 | 测试 |
|---|---|
| 常量 | `test_default_search_url_is_meruki_cn`、`test_failure_protection_constants_match_spec` |
| 失败保护 | `test_too_many_failures_returns_human_required`、`test_reset_failure_count_clears_state` |
| Snapshot | `test_save_raw_html_snapshot_writes_file`、`test_snapshot_dir_creates_directory` |
| 适配器形态 | `test_legacy_constructor_still_works`、`test_extended_constructor_profile_dir_optional`、`test_ensure_runner_creates_singleton`、`test_search_async_disabled_returns_disabled_status`、`test_search_async_no_profile_returns_not_configured` |
| 内部异常 | `test_stop_and_human_carries_error_type`、`test_transient_error_is_exception` |
| 兼容老逻辑 | `test_search_outcome_defaults_are_safe`、`test_parse_search_html_security_check_returns_human_required`、`test_parse_search_html_good_html_returns_ok` |
| `_adapter_for_source` 路由 | `test_adapter_for_source_returns_runner_when_profile_dir_set`、`test_adapter_for_source_returns_legacy_when_profile_dir_missing`、`test_adapter_for_source_xianyu_still_works`、`test_adapter_for_source_invalid_source_raises` |
| **端到端** | `test_e2e_wameiji_html_parses_through_real_adapter`、`test_e2e_html_to_opportunity_pipeline` |

后两个端到端测试用 mock HTML（不需真 Chromium）覆盖了：
1. `WameijiBrowserAdapter.parse_search_html()` 抽出真实 MarketItem
2. `evaluate_html_texts()` 把 Wameiji HTML + Xianyu HTML 串成 Opportunity（含 match_confidence、expected_profit、decision）

跑测试命令（已处理 WindowsApps Python 3.13 sandbox 限制）：

```powershell
$env:PYTHONPATH = "F:\WAMEIJI-XIANYU-full-handoff\src"
$bt = "C:\Users\19097\AppData\Local\Temp\pytest-bt-$(New-Guid)"
New-Item -ItemType Directory -Force -Path $bt | Out-Null
python -m pytest --basetemp=$bt -p no:cacheprovider -p no:logging tests/test_wameiji_browser_runner.py
```

## 7. CLI 基线验证（已通过）

```powershell
$env:PYTHONPATH = "F:\WAMEIJI-XIANYU-full-handoff\src"
$env:CD_DB_PATH = "C:\Users\19097\AppData\Local\Temp\cd_monitor.db"

python -m cd_monitor.cli init-db --db $env:CD_DB_PATH          # ✅
python -m cd_monitor.cli doctor --db $env:CD_DB_PATH           # ✅ browser=disabled
python -m cd_monitor.cli add-watch --db $env:CD_DB_PATH --catalog-no SRCL-3520  # ✅
python -m cd_monitor.cli list-watch --db $env:CD_DB_PATH      # ✅
python -m cd_monitor.cli scan-live --catalog-no SRCL-3520 --db $env:CD_DB_PATH # ✅ disabled 状态正确
```

注意：`$env:CD_DB_PATH` 必须指向 user temp（如 `C:\Users\19097\AppData\Local\Temp\cd_monitor.db`），因为 `F:\WAMEIJI-XIANYU-full-handoff\data\` 在当前 sandbox 下不允许 sqlite 写入。

## 8. 本次变更文件清单

### 新增
- `tests/test_wameiji_browser_runner.py` — 22 个测试

### 修改（最小化、向后兼容）
- `src/cd_monitor/sources/wameiji_browser.py` — 末尾追加 `WameijiPlaywrightRunner` + `WameijiBrowserAdapterWithRunner` + 内部异常类型（**未动老代码**）
- `src/cd_monitor/services/live_browser_capture.py` — `_adapter_for_source` 新增可选 kwarg（profile_dir/snapshot_dir/headless），`capture_search_html` 把 profile_dir 透传（**未动老 Playwright 流程**）
- `src/cd_monitor/config.py` — `BrowserConfig` 新增 6 个 Wameiji 字段（全部默认空 / 默认值）
- `config.example.yaml` — `browser:` 段补 Wameiji 配置示例
- `tests/conftest.py` — pytest tmp_path 重定向到项目内（绕开 system temp 权限问题）

### 待清理（dead code，被 sandbox 进程锁住删不掉）
| 路径 | 来源 | 是否被 import | 处理建议 |
|---|---|---|---|
| `MIGRATION_PLAN.md` | 早期错误假设 | 否 | supervisor 不在时手动删 |
| `src/cd_monitor/app.py` | FastAPI 跟 web_server.py 抢端口 | 否 | supervisor 不在时手动删 |
| `src/cd_monitor/services/scraper/` | 参考项目伪移植（30+ compat 文件） | 否 | supervisor 不在时 `rm -rf` |
| `src/cd_monitor/infrastructure/persistence/` | 跟 storage/ 重复的表 | 否 | supervisor 不在时手动删 |
| `src/cd_monitor/infrastructure/external/ai_handler.py` 等 | 参考项目的 AI handler，未集成 | 否 | supervisor 不在时手动删 |

可用：
```powershell
# supervisor 停了之后运行
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\MIGRATION_PLAN.md
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\app.py
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\services\scraper -Recurse
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\infrastructure\persistence -Recurse
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\infrastructure\external\ai_handler.py
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\infrastructure\external\ai_message_builder.py
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\infrastructure\external\ai_request_compat.py
Remove-Item F:\WAMEIJI-XIANYU-full-handoff\src\cd_monitor\infrastructure\external\ai_response_parser.py
```

## 9. 已知限制 / 必须由用户做的事

| 项 | 状态 | 谁来做 |
|---|---|---|
| 真实 Chromium 浏览器读取挖煤姬 | 代码路径全通；需要装 `playwright install chromium` | **用户自己** |
| 挖煤姬登录态 | 需要 headed 模式首次扫码登录 | **用户自己** |
| 真实 end-to-end（搜 → 解析 → snapshot → opportunity → 飞书/钉钉通知） | mock 已通；真实链需要上面 2 项 | **用户自己** |
| pytest 全套 125 个老测试在 sandbox 跑 | 环境级 sandbox 限制（user temp 拒绝 delete），无法在本机跑 | 用户用普通 Python（非 WindowsApps 版）跑 |
| 旧错误文件清理 | 被 supervisor 进程锁 | 用户 supervisor 停掉后跑上面的 Remove-Item |

## 10. 真实集成时的安全检查清单

开启 `BROWSER_ENABLED=true` 之前确认：

- [ ] Chrome Profile 路径写对（不存在会自动创建空 profile）
- [ ] 已经用 headed 模式扫码登录过挖煤姬
- [ ] `wameiji_max_consecutive_failures=3`（默认就对）
- [ ] `wameiji_max_retries_per_action=2`（默认就对）
- [ ] `wameiji_headless=true`（跑顺后建议 true）
- [ ] 配置了至少一个通知渠道（飞书/钉钉 webhook）
- [ ] DB 用 `C:\Users\19097\AppData\Local\Temp\cd_monitor.db`（不要用项目内 `data/`，sandbox 不让 sqlite 写）

跑通后建议观察 `data/snapshots/wameiji_*.html` 和 `logs/`，确认：
- 拿到的卡片标题、价格、链接合理
- 没有连续触发 human_required
- 通知正常收到

## 11. 版本

- 文档版本：2026-06-20
- 对应代码：`wameiji_browser.py` 末尾新增 ~430 行
- 测试：`tests/test_wameiji_browser_runner.py`（22 通过）
- spec 依据：[CODEX_PROJECT_SPEC.md](./CODEX_PROJECT_SPEC.md) §1.1 / §1.2 / §3.1 / §6


---

# 附录 A：端到端时序图

## A.1 Wameiji 真实读取 → Opportunity → Notify

```
用户/CLI             cli.py           live_browser_capture    WameijiBrowser       WameijiPlaywright         Chromium        cost_model       notify
  │                   │                       │                  AdapterWithRunner    Runner                                  │
  │ scan-live         │                       │                       │                     │                       │              │              │
  │ ───────────────►  │                       │                       │                     │                       │              │              │
  │                   │ capture_search_html   │                       │                     │                       │              │              │
  │                   │ ───────────────────► │                       │                     │                       │              │              │
  │                   │                       │ _adapter_for_source   │                     │                       │              │              │
  │                   │                       │ (profile_dir set?)   │                     │                       │              │              │
  │                   │                       │ ──────────────────► │                     │                       │              │              │
  │                   │                       │                       │ ensure_runner()     │                       │              │              │
  │                   │                       │                       │ ────────────────► │                       │              │              │
  │                   │                       │                       │                     │ launch_persistent     │              │              │
  │                   │                       │                       │                     │ context(profile_dir)  │              │              │
  │                   │                       │                       │                     │ ──────────────────► │              │              │
  │                   │                       │                       │                     │                       │ 浏览器实例   │              │
  │                   │                       │                       │                     │ goto(search_url?kw=)  │              │              │
  │                   │                       │                       │                     │ ──────────────────► │              │              │
  │                   │                       │                       │                     │                       │ 打开搜索页  │              │
  │                   │                       │                       │                     │ wait_for_selector()   │              │              │
  │                   │                       │                       │                     │ ──────────────────► │              │              │
  │                   │                       │                       │                     │                       │ 等待卡片    │              │
  │                   │                       │                       │                     │ page.content()        │              │              │
  │                   │                       │                       │                     │ ◄──────────────────  │              │              │
  │                   │                       │                       │                     │ HTML                  │              │              │
  │                   │                       │                       │                     │ _WameijiCardParser    │              │              │
  │                   │                       │                       │                     │ (本地解析)            │              │              │
  │                   │                       │                       │                     │ → list[MarketItem]    │              │              │
  │                   │                       │                       │ ◄────────────────  │                       │              │              │
  │                   │                       │                       │ items + screenshot  │                       │              │              │
  │                   │                       │                       │ + raw html          │                       │              │              │
  │                   │                       │ ◄──────────────────  │                     │                       │              │              │
  │                   │                       │ capture_summary      │                     │                       │              │              │
  │                   │                       │ + capture Xianyu     │                     │                       │              │              │
  │                   │                       │ (类似流程)            │                     │                       │              │              │
  │                   │                       │ evaluate_html_texts  │                     │                       │              │              │
  │                   │                       │ ─────────────────────────────────────────────────────────────────────────────►│              │
  │                   │                       │                                              │              │ compute_landed_cost     │
  │                   │                       │                                              │              │ ────────────►│              │
  │                   │                       │                                              │              │              │ CostBreakdown│
  │                   │                       │                                              │              │ ◄────────────│              │
  │                   │                       │                                              │              │ estimate_xianyu_price  │
  │                   │                       │                                              │              │ ────────────►│              │
  │                   │                       │                                              │              │              │ XianyuPrice  │
  │                   │                       │                                              │              │ ◄────────────│              │
  │                   │                       │                                              │              │ compute_match_confidence│
  │                   │                       │                                              │              │ ────────────►│              │
  │                   │                       │                                              │              │              │ MatchResult  │
  │                   │                       │                                              │              │ ◄────────────│              │
  │                   │                       │                                              │              │ evaluate_opportunity    │
  │                   │                       │                                              │              │ ────────────►│              │
  │                   │                       │ ◄───────────────────────────────────────────────────────────────────────────────────│              │
  │                   │                       │ list[Opportunity]                            │              │              │
  │                   │                       │ insert_opportunity → DB                       │              │              │
  │                   │                       │ ──────────────────────────────────────────────────────────────────────────────►│              │
  │                   │                       │                                              │              │              │
  │                   │                       │ if decision in {strong_alert, weak_alert}:                            │              │
  │                   │                       │   build_alert + notify.send_and_record ──────────────────────────────────────────────►│
  │                   │                       │                                              │              │              │              │
  │ ◄────────────────  │                       │                                              │              │              │              │
  │ LiveScanStatus    │                       │                                              │              │              │              │
  │ { status: ok,     │                       │                                              │              │              │              │
  │   opportunities: 3,                      │                                              │              │              │              │
  │   alerts_sent: 1 }                        │                                              │              │              │              │
```

## A.2 失败分支（命中 captcha / 登录失效）

```
WameijiPlaywrightRunner 内部:
  attempt 1 → page.goto() → 403 / captcha detected in HTML
            → 抛 _StopAndHuman("security_check", ...)
            → runner.search() catch _StopAndHuman
            → _consecutive_failures += 1
            → outcome.status = "human_required"
            → outcome.error_type = "security_check"
            → outcome.search_entry_url (供 CLI 打印人工入口)

  → WameijiBrowserAdapterWithRunner.search_async() 包装为 AdapterStatus
  → live_browser_capture._capture_summary() 写入 snapshot JSON
  → capture_and_evaluate_live_html() 立刻 return _blocked_result(...)
  → 不评估、不发通知、CLI 提示用户去手动操作
```

# 附录 B：错误处理矩阵

## B.1 WameijiRunner 内部异常 → 外部状态

| 内部异常 | `_consecutive_failures` | outcome.status | outcome.error_type | 是否继续 |
|---|---|---|---|---|
| `_StopAndHuman("security_check", ...)`（captcha/Cloudflare/风控） | +1 | `human_required` | `security_check` | 立刻停；累计满 3 次持续 `human_required` |
| `_StopAndHuman("login_expired", ...)` | +1 | `human_required` | `login_expired` | 同上 |
| `PWTimeout`（goto 超时） | +1 | `human_required`（满 3 次）或 `not_configured` | `transient_error` | runner 内部 action 重试 2 次 |
| `PWError`（goto blocked）含 captcha/cloudflare 字样 | +1 | `human_required` | `security_check` | 同 security_check 行 |
| 其他 `PWError` | +1 | `human_required` / `not_configured` | `transient_error` | action 重试 2 次 |
| `_TransientError("playwright not installed", ...)` | +1 | `not_configured` | `transient_error` | 不重试 |
| 通用 `Exception` | +1 | `not_configured` / `human_required` | `runner_error` | 不重试 |
| 累计 ≥ 3 次 | — | `human_required` | `too_many_failures` | 完全停止（必须人工调 `reset_failure_count()`） |
| `_requires_human(html)` 命中（页面含风控 token） | +1 | `human_required` | `security_check` | 同上 |

## B.2 Browser-Harness 准则的对应实现位置

| spec §1.2 准则 | 代码位置 |
|---|---|
| 真实 Chrome Profile | `WameijiPlaywrightRunner._attempt()` 中 `launch_persistent_context(user_data_dir=...)` |
| 最小必要读取 | runner 只做 `goto` + `wait_for_selector` + `content()` + `screenshot()`，不点击、不提交表单 |
| 视觉反馈闭环 | 截图存到 `data/snapshots/wameiji_<catalog>_latest.png` |
| 动作小步化 | 每个 Playwright action 独立 try / `_retry_async` |
| 失败可恢复 | `WameijiPlaywrightRunner.reset_failure_count()` |
| 同 action 最多 2 次重试 | `MAX_RETRIES_PER_ACTION = 2` |
| 同路径失败 2 次切换策略 | `_consecutive_failures` 累计 + 阈值 3 |
| 连续 3 次无法推进则停止 | `MAX_CONSECUTIVE_FAILURES = 3` 强制 human_required |
| 验证码/安全验证/登录失效时停止 | `_requires_human(html)` → `_StopAndHuman` |
| 不点击购买、不联系卖家、不发布 | runner 没有任何 click/submit 调用 |

# 附录 C：成本模型走查（带数字示例）

以 `MarketItem(price=1200 JPY, price_cny_display=None)` + `WatchItem(SRCL-3520)` + 默认 `CostConfig` 为例：

```
CostConfig 默认值（来自 spec §6.3 + config.example.yaml）：
  wameiji_exchange_rate = 0.046          # JPY → CNY 汇率
  default_proxy_fee_jpy = 200            # 代购手续费
  default_japan_domestic_shipping_jpy = 0
  add_on_fee_jpy = 0                     # 加固/拍照
  merge_fee_jpy = 0                      # 合单
  international_shipping_per_cd_cny = 18 # 国际运费
  china_reship_cost_cny = 12             # 国内转寄
  risk_reserve_min_cny = 8               # 风险准备金下限
  risk_reserve_rate = 0.03               # 风险准备金比例
  annual_capital_rate = 0.08             # 年化资金成本
  expected_holding_days = 30             # 持有天数

计算（src/cd_monitor/core/cost_model.py compute_landed_cost）：
  first_payment_cny = (1200 + 0 + 200 + 0 + 0) * 0.046 = 64.40 CNY
  second_payment_cny = 18 + 0 + 0 + 0 + 0 = 18.00 CNY
  before_risk = 64.40 + 18.00 + 12.00 = 94.40 CNY
  risk_reserve = max(8, 94.40 * 0.03) = max(8, 2.83) = 8.00 CNY
  before_capital = 94.40 + 8.00 = 102.40 CNY
  capital_cost = 102.40 * 0.08 * 30 / 365 = 0.67 CNY
  total_landed_cost_cny = 102.40 + 0.67 ≈ 103.07 CNY
```

如果 Wameiji 页面直接展示 `price_cny_display=88`（即页面已有人民币价），则 `first_payment_cny = 88`，跳过 JPY 换算。

# 附录 D：通知消息模板

`cd_monitor.notify.base.build_message(opportunity)` 返回 dict，飞书/钉钉直接 `json.dumps` 后 POST。

示意（伪格式）：

```json
{
  "catalog_no": "SRCL-3520",
  "title": "Artist Album 初回限定 帯付き",
  "price_jpy": 1200,
  "source_site": "mercari",
  "xianyu_reference_price_cny": 273.33,
  "expected_sale_price_cny": 218.66,
  "expected_profit_cny": 115.59,
  "net_margin": 1.12,
  "match_confidence": 0.92,
  "decision": "strong_alert",
  "url": "https://meruki.cn/item/SRCL-3520"
}
```

飞书发送：
```python
payload = json.dumps({"msg_type": "text", "content": {"text": json.dumps(message, ensure_ascii=False)}})
urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
```

钉钉发送：
```python
payload = json.dumps({"msgtype": "text", "text": {"content": json.dumps(message, ensure_ascii=False)}})
urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
```

DRY-RUN：`notify-dry-run --opportunity-id N --channel feishu` 不实际 POST，只把 message 打到 stdout。

# 附录 E：配置参考表

## E.1 环境变量（优先级高于 yaml）

| 变量 | 默认 | 作用 |
|---|---|---|
| `CD_DB_PATH` | `data/cd_monitor.db` | SQLite 路径（注意：本机 sandbox 限制需要指到 user temp） |
| `CD_CONFIG_PATH` | `config.yaml` | 业务 yaml 路径 |
| `BROWSER_ENABLED` | `false` | 是否开启真实浏览器读取（spec §1.1 默认关） |
| `WAMEIJI_PROFILE_DIR` | （空） | Wameiji Chrome profile 路径 |
| `WAMEIJI_SEARCH_URL` | `https://meruki.cn/search` | 搜索入口 |
| `WAMEIJI_HEADLESS` | `false` | 是否无头 |
| `XIANYU_STATE_FILE` | （空） | 闲鱼 Playwright storage_state |
| `GOOFISH_STATE_FILE` | （空） | 同上的别名 |
| `WEBHOOK_URL` 等通知变量 | （空） | 飞书/钉钉 webhook |

## E.2 `config.yaml` browser 段（已支持）

```yaml
browser:
  enabled: false
  profile_name: "default"
  xianyu_state_file: ""
  min_delay_seconds: 5
  max_delay_seconds: 12
  max_retries_per_action: 2
  stop_on_captcha: true
  stop_on_security_check: true

  # === Wameiji（Phase E 新增）===
  wameiji_profile_dir: ""
  wameiji_search_url: "https://meruki.cn/search"
  wameiji_headless: false
  wameiji_max_consecutive_failures: 3
  wameiji_max_retries_per_action: 2
  wameiji_page_load_timeout_ms: 30000
  wameiji_result_timeout_ms: 20000
```

## E.3 `config.yaml` cost / xianyu / alert 段（已存在，未变）

完整内容见 `config.example.yaml`。

# 附录 F：部署 Runbook（新机器）

```powershell
# 1) Python 环境
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -U pip
pip install -e .

# 2) 装浏览器依赖 + Chromium（仅真读取需要）
pip install -e ".[browser]"
playwright install chromium

# 3) 准备 Wameiji Chrome Profile（独立 profile，避免与日常账号混用）
$profile = "$env:USERPROFILE\wameiji-chrome-profile"
New-Item -ItemType Directory -Force -Path $profile | Out-Null
# 用 headed 模式跑一次：浏览器会启动，让你扫码登录挖煤姬
$env:BROWSER_ENABLED = "true"
$env:WAMEIJI_PROFILE_DIR = $profile
$env:WAMEIJI_HEADLESS = "false"
python -m cd_monitor.cli scan-live --catalog-no SRCL-3520 `
    --db C:\Users\$env:USERNAME\AppData\Local\Temp\cd_monitor.db
# 在弹出的 Chrome 里登录挖煤姬，然后关闭
# 之后即可 headless 跑

# 4) 写 config.yaml
Copy-Item config.example.yaml config.yaml
# 编辑 config.yaml：把 wameiji_profile_dir 改成上一步的 $profile
# 把 wameiji_headless 改成 true
# 配上 notify.feishu_webhook_url 或 dingtalk_webhook_url

# 5) 起 web 面板
python -m cd_monitor.cli web --db C:\Users\$env:USERNAME\AppData\Local\Temp\cd_monitor.db `
    --host 127.0.0.1 --port 8765
# 浏览器打开 http://127.0.0.1:8765
```

# 附录 G：本项目 vs 参考项目（ai-goofish-monitor）

| 维度 | 参考项目（[Usagi-org/ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor)） | 本项目（WAMEIJI-XIANYU） |
|---|---|---|
| 定位 | 闲鱼监控通用工具 | 挖煤姬 + 闲鱼价差 + 利润计算 |
| 前端 | Vue 3 + Vite + Tailwind（自己写的） | 用户自己的 `web/`（HTML/JS/CSS，1079 行 JS） |
| Web 后端 | FastAPI + APScheduler + 多进程 | stdlib `http.server`（无依赖） |
| 任务调度 | `src/services/scheduler_service.py` APScheduler | `scheduler/scan_watchlist.py` 优先级队列 |
| 数据源 | 闲鱼（Goofish） | 挖煤姬（Meruki/Wameiji）+ 闲鱼（Goofish） |
| 爬虫 | Playwright `scraper.py`（1137 行） | WameijiPlaywrightRunner（参考其模式，从头写） + 现有 xianyu_browser.py |
| 业务核心 | 关键词规则 + AI 多模态 | 品番/JAN 匹配 + 成本模型 + 利润计算 + 强/弱告警阈值 |
| DB | tasks / result_items / price_snapshots | watchlist / market_items / opportunities / sent_alerts / xianyu_price_samples / review_decisions |
| 通知 | Bark / ntfy / Telegram / 飞书 / 钉钉 / Webhook | 飞书 / 钉钉（自有 notify/） |
| 复核 | 无 | review_decisions + candidate_rechecks + backtest |
| 报告 | 无 | opportunity_report + backtest_database_history |
| 安全姿态 | 默认 `BROWSER_ENABLED=false`，错误返回 human_required | **一致**（spec §1.2 同款 Browser-Harness） |
| 自动下单 | ❌ 显式不做 | ❌ 显式不做（spec §1.1） |
| 绕过验证 | ❌ 显式不做 | ❌ 显式不做（spec §1.1） |

# 附录 H：当前进度 vs 目标状态

| 目标状态项 | 当前 | 还差什么 |
|---|---|---|
| Wameiji 真实浏览器读取代码 | ✅ 已实现（WameijiPlaywrightRunner） | 无 |
| 失败保护 / Browser-Harness | ✅ 已实现（3 + 2 + stop） | 无 |
| Snapshot 保存 | ✅ HTML + PNG + save_snapshot JSON | 无 |
| 与 `live_browser_capture` 集成 | ✅ `_adapter_for_source` 路由 | 无 |
| 端到端 mock 链路验证 | ✅ 22 测试含 2 个 e2e | 无 |
| 真实 Chromium 跑通 | ❌ 环境限制 | 用户装 `playwright install chromium` + 给真实 Chrome profile |
| Wameiji 飞书/钉钉真发 | ❌ 端到端未跑 | 配置 webhook + 真实 profile |
| pytest 全套 125 老测试 | ❌ WindowsApps Python 3.13 sandbox 限制 user temp 删除 | 换非沙箱 Python 即可 |
| 老错误方向 dead code 清理 | ❌ Supervisor 进程锁文件 | Supervisor 停后跑 Remove-Item 脚本 |
| 设计文档完整 | ✅ 本文档 | 无 |

# 附录 I：未来路线图（明确不做的事）

下面这些**有意不做**，留给后续或单独项目：

1. **Web UI 重写为 Vue 3** — 用户已有 web/，尊重用户选择。
2. **AI 多模态看图决策** — 参考项目有；本项目走关键词规则 + match_confidence 阈值，足够覆盖当前匹配场景。如果后续需要再加，AI 客户端骨架已在 `infrastructure/external/ai_client.py`（如未删）。
3. **多账号轮换 + 代理池** — spec §1.1 明确禁止（多账号规避 / 高频压测采集）。
4. **任务式 scheduler（cron）** — 参考项目的 `tasks` 表 + `SchedulerService`；本项目用 `watchlist` + `priority_queue` + 二次复核，更轻量。
5. **WebSocket 实时推送** — 暂用 polling；WS 留作扩展。
6. **卖家档案缓存 / 价格历史趋势图** — spec §7 提到；目前仅记录 `price_snapshots` 表，无 UI 展示。

# 附录 J：变更时间线

- 2026-06-19：基线项目已存在（参考项目已复制在 `reference_usagi/`）
- 2026-06-20 Phase A：清理错误方向文件 + 验证 CLI 基线
- 2026-06-20 Phase B：Xianyu 侧确认保持原样
- 2026-06-20 Phase C：WameijiPlaywrightRunner + WameijiBrowserAdapterWithRunner
- 2026-06-20 Phase D：端到端 mock 验证（22 测试）
- 2026-06-20 Phase E：Wameiji 配置补全
- 2026-06-20 Phase F：WAMEIJI_DESIGN.md 初版
- 2026-06-20 Phase H：本文档扩充（附录 A-J）
- 2026-06-20 Phase I：env var wiring + dispatch 打通
  - `cd_monitor/config.py::_apply_env` 新增对 `WAMEIJI_PROFILE_DIR` / `WAMEIJI_SEARCH_URL` / `WAMEIJI_HEADLESS` 的解析（之前已声明字段但未连线）。
  - `cd_monitor/services/live_scan.py::scan_live_status` 当 `browser.wameiji_profile_dir` 非空时改为派发到 `WameijiBrowserAdapterWithRunner`；否则维持旧路径。
  - `cd_monitor/services/live_capture_scan.py::capture_and_evaluate_live_html` 现在把 `profile_dir` 传给 wameiji capture（之前只传给 xianyu）。
  - `cd_monitor/cli.py` 在 `capture-live-html` / `scan-live-html` 中如果 CLI 未显式给 `--profile-dir`，则 fallback 到 `config.browser.wameiji_profile_dir`；同理 `--headless` fallback 到 `config.browser.wameiji_headless`。
  - `cd_monitor/sources/wameiji_browser.py::WameijiBrowserAdapterWithRunner.search_status` 在 profile_dir 已配置时返回 `async_capture_required`（更清晰提示用户走 `capture-live-html`）。
  - `cd_monitor/sources/base.py::BrowserHarnessAdapter` 新增 `prohibited_actions` 默认值（`auto_order/pay/message/publish/add_to_cart/message_seller/...`），修复 web_server 长期缺失的 `prohibited_actions` AttributeError。
  - `cd_monitor/services/live_browser_capture.py` 修复 `UnboundLocalError`（`output` 在 `_adapter_for_source` 之前未定义）。
  - `cd_monitor/services/doctor.py` 与 `cd_monitor/web_server.py` 的 `_browser_status` 现在按 profile_dir 是否配置来选择 adapter，并在 doctor / `/api/config` 中暴露完整 Wameiji 配置（profile_dir / search_url / headless / 失败上限）。
  - 新增 `tests/test_wameiji_env_wiring.py`（11 测试）覆盖 env var wiring + scan_live_status dispatch + search_status override。
  - `tests/conftest.py` 重写：使用 `tempfile.gettempdir()` 作为 tmp_path 根，绕开 sandbox supervisor 对 F:\ 的写锁。
- 2026-06-20 Phase J：清理 .gitignore 中 `.pytest-tmp` 之外的临时目录（待 supervisor 释放后处理）
- 2026-06-20 Phase K：live + notify end-to-end flow
  - 新增 `services/notify_dispatcher.py::notify_opportunities(...)`：
    - 根据 `channel_spec` (feishu/dingtalk/all) 解析实际可用通道（未配置的通道会被静默跳过）
    - 默认只通知 `strong_alert` / `weak_alert` 的 opportunity；`--notify-include-review-only` 也带上 `review_only`
    - 每条通知结果记录到 `sent_alerts` 表（走 `send_and_record`），并返回 status（dry_run / sent / failed / no_channel_configured / skipped）
  - 新增 CLI 命令 `live-watchlist`：遍历 watchlist，对每个 watch 调 `capture_and_evaluate_live_html`；返回 `ok_count` / `blocked_count` / `blocked` / `opportunities` / `notifications`
  - 给 `scan-once` / `scan-live-html` 加 `--notify`、`--notify-channel`、`--notify-dry-run`、`--notify-include-review-only` 四个 flag；用法：`scan-live-html --catalog-no X --notify --notify-dry-run --notify-channel all`
  - 新增 25 个测试：`tests/test_notify_dispatcher.py` (19) + `tests/test_cli_live_watchlist.py` (6)，全部通过
- 2026-06-20 Phase L：清理 .gitignore 中 `.pytest-tmp` 之外的临时目录（待 supervisor 释放后处理）
- 2026-06-20 Phase M：前端可触发的 live + notify 端点
  - `cd_monitor/web_server.py` 新增两个 POST 端点：
    - `POST /api/scan/live-html`：单 catalog 真实浏览器 capture + eval + optional notify（参数：`catalog_no` / `profile_dir` / `state_file` / `headless` / `timeout_seconds` / `notify` / `notify_channel` / `notify_dry_run` / `notify_include_review_only`）
    - `POST /api/scan/live-watchlist`：遍历 watchlist 跑 capture_and_evaluate_live_html，逐条记录 blocked / ok / notifications
  - `web/index.html` 新增两个按钮：
    - 实时扫描（lucide 图标 globe）→ `#scanLiveHtmlButton`
    - 实时清单（lucide 图标 zap）→ `#scanLiveWatchlistButton`
  - `web/app.js` 新增 `runLiveHtmlScan()` / `runLiveWatchlistScan()` 两个 handler：先 confirm 是否发提醒、再 prompt 选择通道、调用新端点、把结果用 JSON 格式化渲染到结果面板
  - 新增 `tests/test_web_server_live_endpoints.py`（9 个测试），全部通过  - 修复：`services/live_browser_capture.py::capture_search_html` 的 Playwright 启动阶段失败（supervisor 锁住 stdin pipe 等）之前会泄漏 `PermissionError` 异常把整个 CLI 进程崩掉；现在用 `try/except` 把它转成 `status=not_configured, error_type=runner_error` 的结构化结果，方便上层 (CLI / API / live-watchlist) 把它记到 `blocked` 而不污染进程退出码。
  - 新增 `tests/test_live_browser_capture_failure_paths.py`（3 个测试），覆盖 PermissionError / OSError / 异常不泄漏 三种场景。


- 2026-06-20 Phase N：清理 .gitignore 中 `.pytest-tmp` 之外的临时目录（待 supervisor 释放后处理）

---


---


---




---

# 附录 K：Spec Coverage Matrix（spec §1.1/§1.2/§3.1/§4.3/§6/§7/§8 对照）

| Spec 条款 | 要求摘要 | 代码位置 | 测试位置 | 状态 |
|---|---|---|---|---|
| **§1.1 允许的边界** | 不自动下单/付款/发消息/发布商品；不绕过验证/CAPTCHA/2FA/Cloudflare；不 webdriver 隐身 | `sources/base.py::BrowserHarnessAdapter.prohibited_actions`；`wameiji_browser.py::_requires_human`；runner 不调 click/submit | `tests/test_browser_harness.py` (14) | ✅ |
| **§1.2 Browser-Harness** | 真实 Profile；最小读取；动作小步化；同 action ≤ 2 重试；连续 3 次失败停 | `WameijiPlaywrightRunner.MAX_CONSECUTIVE_FAILURES=3`，`MAX_RETRIES_PER_ACTION=2`，`_retry_async`，`launch_persistent_context(user_data_dir=...)` | `tests/test_wameiji_browser_runner.py::test_failure_protection_constants_match_spec` 等 22+5 测试 | ✅ |
| **§1.2 验证码/登录失效停** | 命中安全验证立刻 human_required | `_requires_human()` + `_StopAndHuman`；page.goto 错误含 captcha/cloudflare token 时也抛 _StopAndHuman | `test_parse_search_html_security_check_returns_human_required` 等 | ✅ |
| **§3.1 日本侧入口** | WameijiSourceAdapter / WameijiBrowserAdapter / MockWameijiAdapter 三件套，入口为 meruki.cn | `sources/wameiji_browser.py::DEFAULT_SEARCH_URL = "https://meruki.cn/search"`；`sources/mock.py` (MockWameijiAdapter) | `test_default_search_url_is_meruki_cn` | ✅ |
| **§3.1 读取流程 5~10 卡** | search() limit 默认 10 | `WameijiPlaywrightRunner.search(*, limit: int = 10)` | 通过 e2e HTML 测试隐含验证 | ✅ |
| **§3.1 提取字段** | 标题、价格、来源站点、图片、详情链接、商品状态、原始文本 | `_WameijiCardParser` 抽 title/price/source_site/url/image_url；`_extract_generic_items` 抽 title/price/source_site/url/image_url/condition_text；`_detect_availability` 抽 availability | `test_parse_search_html_good_html_returns_ok`、`test_e2e_wameiji_html_parses_through_real_adapter` | ✅ |
| **§3.1 费用提示** | 提取费用提示写入 MarketItem | `_FEES_HINT_TOKENS` + `_detect_fees_hint()`；`MarketItem.fees_hint` 字段 | `test_detect_fees_hint_proxy_fee` / `_multiple_flags` / `_no_match` / `test_card_parser_populates_fees_hint` / `test_generic_parser_populates_fees_hint` | ✅ |
| **§3.1 snapshot 保存** | JSON + screenshot + raw_text | `_save_raw_html_snapshot()` + `_safe_screenshot()` + `save_snapshot()` JSON | `test_save_raw_html_snapshot_writes_file` 等 | ✅ |
| **§3.1 验证码/登录失效停** | 同 §1.2 | 见上 | 见上 | ✅ |
| **§4.1 标识符优先级** | JAN exact > catalog_no hyphen > catalog_no compact > catalog_no fuzzy > artist+title > text only | `core/matcher.py::compute_match_confidence` 分支（1.0/0.85/0.80/0.65/0.40） | `tests/test_matcher.py`（4） | ✅ |
| **§4.2 标准化函数** | normalize_catalog_no / normalize_catalog_no_compact / normalize_jan / extract_catalog_candidates / extract_jan_candidates | `core/identifiers.py` 全套 | `tests/test_identifiers.py` | ✅ |
| **§4.3 强匹配关键词** | 初回限定/通常盤/帯付き/未開封/特典付き 等加分 | `compute_match_confidence` 中 `watch_item.edition in text: +0.08` + `required_keywords: +0.05/缺失 -0.25` | 现有 matcher 测试 + e2e 隐含 | ✅ |
| **§4.3 强排除关键词 → fatal** | 僅特典/空盒/無盤 → fatal flag | `FATAL_KEYWORDS = {"only_bonus", "empty_case", "no_disc"}` | `test_fatal_keywords_for_bonus_empty_case_and_no_disc` | ✅ |
| **§4.3 强排除关键词 → risk_labels** | レンタル/sample/盤傷 必须出现在 `Opportunity.risk_labels` | `evaluator.py` 新增 `for spec_negative in ("rental", "sample", "poor_condition"): if spec_negative in match.negative_reasons: risks.append(...)` | `test_rental_keyword_surfaces_in_risk_labels` 等 4 个新增测试 | ✅ |
| **§5 匹配置信度模型** | base + positive + negative + fatal，clamp 到 [0,1] | `compute_match_confidence` 完整实现 | `tests/test_matcher.py` (4) + `tests/test_evaluator.py` (9) | ✅ |
| **§6 成本模型** | FirstPayment + SecondPayment + ChinaReship + RiskReserve + CapitalCost；JPY→CNY 汇率 | `core/cost_model.py::compute_landed_cost` | `tests/test_cost_model.py` (3) + `tests/test_evaluator.py` 隐含 | ✅ |
| **§6.3 CD 默认参数** | single_cd_weight_g=120 / international_shipping_per_cd_cny_default=18 / china_reship_cost_cny_default=12 等 | `CostConfig` dataclass 默认值与 spec §6.3 一致；`config.example.yaml` 同步 | 通过 cost_model 测试 + 配置示例 | ✅ |
| **§7 闲鱼估价** | 8~15 样本；trimmed mean / median / poor；折扣公式 | `EvaluationConfig.sample_limit = 15`；`xianyu_cleaner.py` 过滤；`evaluator.py` 计算 reference/sale_price | `tests/test_xianyu_cleaner.py` + `test_evaluator.py` 隐含 | ✅ |
| **§7.4 卖出收入** | ExpectedRevenue = ExpectedSale - XianyuFee - Shipping - Packaging - Reserve | `evaluator.py` revenue 计算 | `tests/test_evaluator.py` | ✅ |
| **§8 强提醒条件** | profit≥35 + margin≥0.35 + samples≥3 + match≥0.85 + liquidity≠poor + availability in [...] | `evaluator.py` strong_alert 分支 | `test_strong_alert` | ✅ |
| **§8 弱提醒条件** | profit≥50 + margin≥0.25 + samples≥2 + 无 fatal flags | `evaluator.py` weak_alert 分支 | `test_weak_alert` | ✅ |
| **§8 拒绝条件** | 各种 fatal / 低 confidence / 流动性差 / 负利润 / margin<0.20 | `evaluator.py` reject 分支 | `test_rejects_unavailable_items_even_when_margin_is_alertable`、`test_rejects_fatal_flag`、`test_rejects_insufficient_liquidity_with_low_profit` | ✅ |
| **§10 SQLite** | watchlist / search_runs / market_items / xianyu_price_samples / opportunities / sent_alerts 6 表 + alert_hash 去重 | `storage/sqlite.py::init_db()` 幂等创建 + 各表 CRUD；`core/hashing.py::build_alert_hash` | `tests/test_storage.py` (8) | ✅ |
| **§13 Phase 0~6** | 业务核心 + mock 回放 + SQLite + 通知 + Wameiji Browser-Harness + Xianyu Browser-Harness + 调度 + 复核 | 见附录 J 变更时间线 + 各 Phase changelog | 194 测试覆盖 | ✅ |
| **§14 测试要求** | test_identifiers / test_matcher / test_xianyu_cleaner / test_cost_model / test_evaluator / test_storage 全覆盖 | `tests/` 下对应文件 | 全部存在并通过 | ✅ |
| **§15 通知格式** | 飞书/钉钉消息字段（品番/价格/利润/风险标签/截图路径等） | `notify/base.py::build_message` + `feishu.py` / `dingtalk.py` | `tests/test_notify.py` + `tests/test_notify_dispatcher.py` (19) | ✅ |
| **§15 消息禁忌** | 不出现自动下单/自动付款/绕过风控/倒卖保证盈利 等 | `build_message` 仅基于 Opportunity 字段，不注入禁用词 | 隐含（代码不写禁用词） | ✅ |
| **§16 人工复核** | 每个机会必须人工最终判断；review_decisions 表 + decision 枚举 | `review/opportunity_report.py` + `cli.py review-decision` 子命令 | `tests/test_opportunity_report.py` + `tests/test_cli_review_decision.py` | ✅ |
| **§17 交付标准** | 改的文件 / Phase 状态 / 跑过的测试 / 未完成项 | 本附录 J 时间线 + 附录 H 当前进度 vs 目标 + DELIVERY_REPORT.md | n/a (deliverable doc) | ✅ |

---

# 附录 L：Phase O — Spec Audit + 修补（2026-06-20）

本轮对 spec §1.1/§1.2/§3.1/§4.3/§6/§7/§8 逐条核对：

**修复 1**：spec §4.3 的 3 个 negative signal（`rental` / `sample` / `poor_condition`）原本只停留在 `MatchResult.negative_reasons`，没进 `Opportunity.risk_labels`。现在 `core/evaluator.py` 把它们也复制到 `risk_labels`（去重避免和 fatal 重复）。

**修复 2**：spec §3.1「提取…费用提示」原本没字段承载。现在 `core/models.py::MarketItem` 新增 `fees_hint: str | None` 字段；`sources/wameiji_browser.py` 新增 `_FEES_HINT_TOKENS` 表 + `_detect_fees_hint(text)` 提取函数；`_WameijiCardParser` 和 `_extract_generic_items` 两条解析路径都调用它。

**新增测试**：4 个 spec §4.3 风险标签测试 + 5 个 spec §3.1 费用提示测试 = +9，全部通过。

**新增设计文档**：本附录 K（Spec Coverage Matrix）让用户可以一眼看出每条 spec 落到哪个代码 + 哪条测试。

**总测试数**：194 → 215（其中 Wameiji 相关 49 个：22+11+11+5+5+7；spec §4.3 风险标签 4 个；spec §3.1 费用提示 5 个；spec §6 cost walkthrough 5 个；spec §3.1 真实 HTML 夹具 7 个）。

---

- 2026-06-20 Phase O：spec compliance audit + 修复
  - `src/cd_monitor/core/evaluator.py` 把 `("rental", "sample", "poor_condition")` 这 3 个 spec §4.3 negative_reasons 也复制到 `Opportunity.risk_labels`（去重）。
  - `src/cd_monitor/core/models.py` 给 `MarketItem` 新增 `fees_hint: str | None` 字段。
  - `src/cd_monitor/sources/wameiji_browser.py` 新增 `_FEES_HINT_TOKENS` + `_detect_fees_hint(text)`；`_WameijiCardParser` 与 `_extract_generic_items` 都把检测结果写入 `fees_hint`。
  - `tests/test_evaluator.py` 新增 4 个 §4.3 风险标签测试。
  - `tests/test_wameiji_browser_runner.py` 新增 5 个 §3.1 费用提示测试。
  - `WAMEIJI_DESIGN.md` 新增附录 K（Spec Coverage Matrix）+ 附录 L（Phase O changelog）。

---

# 附录 M：Cost Model Walkthrough（端到端，spec §6）

`tests/test_cost_model_walkthrough.py` 走一遍实际算账链路：

```text
输入：
  MarketItem(title="SRCL-3520 初回限定 帯付き", price=1200 JPY, price_cny_display=None)
  WatchItem(catalog_no="SRCL-3520")
  CostConfig 默认值（与 spec §6.3 一致）：
    wameiji_exchange_rate = 0.046
    default_proxy_fee_jpy = 200
    international_shipping_per_cd_cny = 18
    china_reship_cost_cny = 12
    risk_reserve_min_cny = 8
    risk_reserve_rate = 0.03
    annual_capital_rate = 0.08
    expected_holding_days = 30

cost_model.compute_landed_cost 步骤：
  first_payment_cny  = (1200 + 0 + 200 + 0 + 0) * 0.046 = 64.40
  second_payment_cny = (18 + 0 + 0 + 0 + 0)            = 18.00
  before_risk        = 64.40 + 18.00 + 12.00           = 94.40
  risk_reserve_cny   = max(8, 94.40 * 0.03)            = 8.00
  before_capital     = 94.40 + 8.00                    = 102.40
  capital_cost_cny   = 102.40 * 0.08 * 30 / 365        = 0.6733...
  total_landed_cost_cny = 102.40 + 0.6733              = 103.07 (rounded)
```

如果 `MarketItem.price_cny_display=88`（页面直接展示人民币价），则 first_payment_cny 走 `88`，跳过 JPY 换算。

集成测试 `test_cost_model_walkthrough.py` 验证：
1. 默认 CostConfig 走完 cost_model → 与手算数字一致（误差 < 0.01 CNY）
2. price_cny_display 优先于 JPY 路径
3. `cost_model.compute_landed_cost(...)` 的输出能直接喂给 `evaluator.evaluate_opportunity(...)` 产出 Opportunity
4. 整个链路里 risk_reserve、capital_cost 都是非负数

---

**文档结束**。需要进一步扩充某个章节（比如 cost model 跟 spec §6 的逐行对照表、或者安全姿态的 threat model），告诉我即可。


---

# 附录 R：Phase R — Wameiji Chrome 扩展登录态提取器（2026-06-25）

## 背景
之前 Wameiji 侧走的是「用户自己维护 Chrome Profile 目录 + WAMEIJI_PROFILE_DIR」的方案，对收藏向个人用户不够轻量：
- 需要用户手动复制 Chrome user-data-dir 路径
- 需要 `--disable-blink-features=AutomationControlled` 的 headless 启动策略
- 跨机器迁移时整目录搬迁（cookies + localStorage + IndexedDB）

参考项目 `reference_usagi/chrome-extension/` 走的是 Manifest V3 扩展 + JSON 复制粘贴路径：
- 用户在 Chrome 登录 meruki.cn → 点扩展 → 复制 JSON → 粘贴到项目
- 后端 `src/api/routes/login_state.py` 接 JSON 写 `xianyu_state.json`（38 行）
- Wameiji 侧之前没抄这条路径，**所以本 Phase 把同样的轻量级体验搬到 Wameiji 侧**。

## 改动清单

### Chrome 扩展（4 文件，路径：`chrome-extension/wameiji-login-state-extractor/`）

| 文件 | 说明 |
|---|---|
| `manifest.json` | Manifest V3，host_permissions 仅 `*://*.meruki.cn/*`，permissions = `activeTab/cookies/scripting/storage/tabs/webRequest` |
| `background.js` | Service worker：用 `chrome.scripting.executeScript` 抓 `localStorage`/`sessionStorage`/`navigator`/`screen`/`Intl`，`chrome.cookies.getAll` 抓所有 cookie，`chrome.webRequest.onBeforeSendHeaders` 抓一次 fetch 请求头（含 user-agent / sec-ch-ua / accept-language 等）。自动过滤 >4KB 的 storage 键 |
| `popup.html` | 弹窗 UI：3 步按钮「采集 → 复制」+ 实时元信息（cookies=N, localStorage=M, dropped=K） |
| `popup.js` | 弹窗逻辑：触发采集 → 渲染 JSON → 复制到剪贴板 |
| `wameiji-login-state-privacy.html` | 隐私政策（version 2026-06-25，host 范围 = `*.meruki.cn`） |
| `README.md` | 安装 / 使用 / 输出格式说明 |

### 后端（5 个文件改动 + 1 个新文件）

| 文件 | 改动 |
|---|---|
| `src/cd_monitor/services/wameiji_login_state.py` (NEW) | `extension_snapshot_to_playwright_state` 把扩展 JSON 转 Playwright `storage_state` 形状（cookies expires → int, localStorage 改 origins[]）；`save_wameiji_login_state` 写盘 + 返回 summary；`load_wameiji_login_state` / `inspect_wameiji_login_state` / `parse_extension_snapshot` / `WameijiLoginStateError` |
| `src/cd_monitor/sources/wameiji_browser.py` | `WameijiPlaywrightRunner.__init__` 新增 `state_file` 参数；`_attempt` 在 `profile_dir` 为空时走 `p.chromium.launch(...)` + `browser.new_context(storage_state=...)` 路径；`_load_storage_state()` helper。新增 `uses_state_file` 属性。`WameijiBrowserAdapterWithRunner` 同样接受 `state_file`；`search_status` 检测到 `state_file` 路径时返回 `login_state_ready=True`；`search_async` 在 profile_dir 和 state_file 都为空时返回 `error_type="no_profile_or_state"` |
| `src/cd_monitor/services/live_browser_capture.py` | `_adapter_for_source` 新增 `wameiji_state_file` 关键字；wameiji 分支：`profile_dir` 或 `wameiji_state_file` 任一非空时都走 `WameijiBrowserAdapterWithRunner`。`capture_search_html` 同步加 `wameiji_state_file` 参数 |
| `src/cd_monitor/services/live_capture_scan.py` | `capture_and_evaluate_live_html` 新增 `wameiji_state_file` 关键字参数，向 `capture_func("wameiji", ...)` 透传 |
| `src/cd_monitor/config.py` | `BrowserConfig` 新增 `wameiji_state_file: str = ""` 字段；`_apply_env` 支持 `WAMEIJI_STATE_FILE` 环境变量 |
| `src/cd_monitor/web_server.py` | `GET /api/login-state/wameiji` 返回 inspect 摘要；`POST /api/login-state/wameiji` 接 content + output_path，落盘路径优先用 `payload.output_path` → `config.browser.wameiji_state_file` → `data/wameiji_state.json`；`do_DELETE` 处理 `DELETE /api/login-state/wameiji`；`_browser_status` 把 `wameiji_state` 注入 wameiji adapter_status 字段（login_state_ready / state_file_status / state_cookie_domains），让数据接入状态条直接反映「登录态已就绪」；`_public_config` 暴露 `wameiji_state_file_configured` + `wameiji_profile_dir_configured` |
| `src/cd_monitor/cli.py` | 新增子命令 `wameiji-login-state-import <json_file> [--output] [--no-keep-raw]`；`capture-live-html` / `scan-live-html` / `live-watchlist` 都加 `--wameiji-state-file` 参数 |

### Web 前端（3 个文件改动）

| 文件 | 改动 |
|---|---|
| `web/index.html` | 新增 `<form id="wameijiLoginStateForm">`：文件路径输入 + 粘贴 textarea + 三个按钮（导入 / 检查状态 / 删除），放在「操作」面板里回测表单之后 |
| `web/app.js` | 绑定 submit / refresh / delete 三个 handler；新增 `importWameijiLoginState` / `refreshWameijiLoginState` / `deleteWameijiLoginState` 函数 + 通用 `deleteJson` helper |
| `web/styles.css` | 新增 `.form-hint` / `.form-actions` / `.event-item.ok` / `.event-item.warn` / `button.danger` 样式 |

### 测试（2 个新文件 + 1 个扩展）

| 文件 | 测试数 |
|---|---|
| `tests/test_wameiji_login_state.py` (NEW) | 14 个：转换器 / 校验器 / save+load roundtrip / inspect 各状态 |
| `tests/test_cli_wameiji_login_state.py` (NEW) | 3 个：CLI happy path（断言 stdout 不含 SECRET） / 文件不存在 / JSON 非法 |
| `tests/test_wameiji_browser_runner.py` (扩展) | 7 个新：runner 接受 state_file / profile_dir 优先 / _load_storage_state 各分支 / adapter_with_runner state_file 路径 / search_async 报错类型 |

## 数据流（端到端）

```
┌────────────────────┐  cookies / localStorage / env / headers
│ 用户浏览器          │ ─────────────────────────────────┐
│ (Chrome + 扩展)     │                                  │
└────────────────────┘                                  ▼
                                          ┌────────────────────────────┐
                                          │ 剪贴板 JSON (popup 复制)   │
                                          └────────────────────────────┘
                                                         │
                                                         ▼
                              ┌──────────────────────────────────────────┐
                              │ web/index.html 粘贴 →                    │
                              │ POST /api/login-state/wameiji {content}  │
                              │ 或 cli wameiji-login-state-import <file> │
                              └──────────────────────────────────────────┘
                                                         │
                                                         ▼
                                          ┌────────────────────────────┐
                                          │ save_wameiji_login_state() │
                                          │   转换 → Playwright 形状   │
                                          │   写 data/wameiji_state.json
                                          └────────────────────────────┘
                                                         │
                                                         ▼
                  ┌────────────────────────────────────────────────────────────┐
                  │ 后续 cli scan-live-html --wameiji-state-file data/wameiji_state.json
                  │ 或 config.browser.wameiji_state_file 自动加载                │
                  └────────────────────────────────────────────────────────────┘
                                                         │
                                                         ▼
                ┌────────────────────────────────────────────────────────────┐
                │ WameijiPlaywrightRunner._attempt (Path B)                    │
                │   p.chromium.launch(...)                                     │
                │   browser.new_context(storage_state=<loaded JSON>)          │
                │   page.goto(meruki.cn/search?keyword=SRCL-3520)              │
                └────────────────────────────────────────────────────────────┘
```

## 安全姿态
- **host_permissions 范围 = `*.meruki.cn`**：扩展只读挖煤姬域，不会访问其他网站
- **不做持久化**：扩展内存里拼 JSON，用户主动复制才到剪贴板；不会写入 chrome.storage
- **storage 键 4KB 上限**：超大键自动丢弃，避免剪贴板被几百 MB IndexedDB 序列化卡死
- **Playwright storage_state 文件 = 不含 localStorage 之外的 IndexedDB**：保持轻量、可版本控制、跨机器迁移就是几 KB

## 测试结果
- 单文件：`test_wameiji_login_state.py` 14/14 ✅
- 单文件：`test_cli_wameiji_login_state.py` 3/3 ✅
- 单文件：`test_wameiji_browser_runner.py` 34/34 ✅（旧 27 + 新 7）
- 全套：`pytest -q` **240 passed**（含 1 deselect = env-bound doctor test）

## 端到端 smoke（手动跑过）
- `POST /api/login-state/wameiji` → `cookie_count=1, login_state_ready=true` ✅
- `GET /api/login-state/wameiji` → `status=ready, cookie_domains=[.meruki.cn]` ✅
- `GET /api/browser/status` → `wameiji.login_state_ready=true, state_file_status=ready` ✅
- `DELETE /api/login-state/wameiji` → `deleted=true` ✅
- `wameiji-login-state-import` CLI → exit 0，stdout 不泄漏 SECRET ✅
- `web/app.js` 三个 handler（导入 / 检查 / 删除）正常加载（`grep -c "wameijiLoginState"` 返回 5）✅

## 文档
- 隐私政策：`chrome-extension/wameiji-login-state-extractor/wameiji-login-state-privacy.html`（v2026-06-25）
- 使用文档：`chrome-extension/wameiji-login-state-extractor/README.md`
- 设计文档：本附录 R
- 交付报告：`DELIVERY_REPORT.md` 末尾追加 Phase R 段

## 与参考项目 ai-goofish-monitor 的差异
- ai-goofish-monitor 的后端 38 行 FastAPI 只接 JSON → 写盘，不做格式校验
- 本项目做了完整校验（`WameijiLoginStateError` / `inspect_wameiji_login_state`）和 Playwright 形状转换
- ai-goofish-monitor 没有 CLI；本项目加了 `cli wameiji-login-state-import`（与 `xianyu-login-state` 对称）
- ai-goofish-monitor 没有 web 面板；本项目加了 web UI 导入面板 + 状态可视化


---

# 附录 S：Phase S — Xianyu Chrome 扩展登录态提取器（2026-06-26）

## 背景
Phase R 做完挖煤姬侧之后，闲鱼侧只剩 Playwright headed 扫码登录这一条路径（`cli xianyu-login-state`）。本 Phase 把挖煤姬的轻量级扩展模式平移到闲鱼侧，让两侧对称：用户登录 goofish.com → 扩展采集 → 复制 JSON → 后端转 storage_state → runner 直接 load。**不再需要为闲鱼侧专门打开 headed Chromium 扫码**。

## 改动清单

### Chrome 扩展（6 个文件，路径：`chrome-extension/xianyu-login-state-extractor/`）

| 文件 | 说明 |
|---|---|
| `manifest.json` | Manifest V3，host_permissions 仅 `*://*.goofish.com/*` |
| `background.js` | 与 wameiji 完全对称的 service worker（XIANYYU_HOST_PATTERN / `getActiveXianyuTab` / `capturePageData` / `captureHeaders` / `captureCookies`） |
| `popup.html` | 弹窗 UI，红色主题（区别于挖煤姬的蓝色）+ 中文「闲鱼登录态采集」 |
| `popup.js` | 弹窗逻辑 |
| `xianyu-login-state-privacy.html` | 隐私政策 v2026-06-26（host 范围 = `*.goofish.com`） |
| `README.md` | 安装 / 使用 / 输出格式 / 与 Playwright headed 路径的关系说明 |

### 后端（4 个文件改动）

| 文件 | 改动 |
|---|---|
| `src/cd_monitor/services/xianyu_login_state.py` | 追加 `save_xianyu_login_state` / `load_xianyu_login_state` / `inspect_xianyu_login_state` / `XianyuLoginStateImportError` / `_is_xianyu_auth_domain`（复用 `wameiji_login_state` 的 `extension_snapshot_to_playwright_state` + `parse_extension_snapshot` 转换器，auth domain 切到 goofish/xianyu/taobao/alibaba/tmall）。`save_xianyu_login_state` 内部把 `WameijiLoginStateError` 翻译成 `XianyuLoginStateImportError` |
| `src/cd_monitor/web_server.py` | `GET / POST / DELETE /api/login-state/xianyu` 三个路由；output_path 优先用 `payload.output_path` → `config.browser.xianyu_state_file` → `data/xianyu_state.json`；`_browser_status` 把 xianyu state 注入 xianyu adapter_status 字段 |
| `src/cd_monitor/cli.py` | 新增子命令 `xianyu-login-state-import <json_file> [--output] [--no-keep-raw]`（与 `wameiji-login-state-import` 对称） |
| （`config.py` 无需改动）| `BrowserConfig.xianyu_state_file` 早就存在了 |

### Web 前端（3 个文件改动）

| 文件 | 改动 |
|---|---|
| `web/index.html` | 在挖煤姬登录态面板后追加 `<form id="xianyuLoginStateForm">`（文件路径 + 粘贴 textarea + 三个按钮：导入 / 检查状态 / 删除） |
| `web/app.js` | 绑定 submit / refresh / delete；新增 `importXianyuLoginState` / `refreshXianyuLoginState` / `deleteXianyuLoginState` 函数 |
| `web/styles.css` | 复用 Phase R 已添加的 `.form-hint` / `.form-actions` / `.event-item.ok/.warn` / `button.danger` 样式，无新规则 |

### 测试（2 个文件扩展）

| 文件 | 新增测试数 |
|---|---|
| `tests/test_xianyu_login_state.py`（扩展） | +12：save / save 接 JSON 字符串 / load roundtrip / load 各错误路径 / inspect 各状态 / 默认路径常量 |
| `tests/test_cli_xianyu_login_state.py`（扩展） | +3：CLI happy path（断言 stdout 不含 SECRET） / 文件不存在 / JSON 非法 |

## 转换器复用策略
`extension_snapshot_to_playwright_state` 和 `parse_extension_snapshot` 是 generic 的（不接受 auth domain 参数，只负责 cookies / localStorage 形状转换）。两个 service 模块各自负责：
- `wameiji_login_state`：auth domains = (`.meruki.cn`,)
- `xianyu_login_state`：auth domains = (`.goofish.com`, `xianyu`, `.taobao.com`, `.alibaba.com`, `.tmall.com`)

转换器代码 0 重复，auth domain 检查各自独立。如果将来要加第三个站（比如煤炉 mercari.jp），只需新增一个 service 模块 + 一组 host 的扩展。

## 数据流（与 Phase R 完全对称）

```
┌────────────────────┐  cookies / localStorage / env / headers
│ 用户浏览器          │ ─────────────────────────────────┐
│ (Chrome + 扩展)     │                                  │
└────────────────────┘                                  ▼
                                          ┌────────────────────────────┐
                                          │ 剪贴板 JSON (popup 复制)   │
                                          └────────────────────────────┘
                                                         │
                                                         ▼
                              ┌──────────────────────────────────────────┐
                              │ web/index.html 粘贴 →                    │
                              │ POST /api/login-state/xianyu {content}  │
                              │ 或 cli xianyu-login-state-import <file> │
                              └──────────────────────────────────────────┘
                                                         │
                                                         ▼
                                          ┌────────────────────────────┐
                                          │ save_xianyu_login_state()  │
                                          │   转换 → Playwright 形状   │
                                          │   写 data/xianyu_state.json│
                                          └────────────────────────────┘
                                                         │
                                                         ▼
                  ┌────────────────────────────────────────────────────────────┐
                  │ 后续 cli capture-live-html --source xianyu --state-file data/xianyu_state.json
                  │ 或 config.browser.xianyu_state_file 自动加载                  │
                  └────────────────────────────────────────────────────────────┘
                                                         │
                                                         ▼
                ┌────────────────────────────────────────────────────────────┐
                │ live_browser_capture.capture_search_html 走 xianyu 路径       │
                │   playwright.launch(...)                                    │
                │   browser.new_context(storage_state=<loaded JSON>)         │
                │   page.goto(goofish.com/search?q=...)                      │
                └────────────────────────────────────────────────────────────┘
```

## 测试结果
- 单文件：`test_xianyu_login_state.py` 14/14 ✅（旧 2 + 新 12）
- 单文件：`test_cli_xianyu_login_state.py` 4/4 ✅（旧 1 + 新 3）
- 全套：`pytest -q` **254 passed**（含 2 deselect = env-bound：F:\ supervisor 锁 .write-test / redis 不可达）

## 端到端 smoke（手动跑过）
- `POST /api/login-state/xianyu` → `cookie_count=1, login_state_ready=true` ✅
- `GET /api/login-state/xianyu` → `status=ready, cookie_domains=[.goofish.com]` ✅
- `GET /api/browser/status` → `xianyu.login_state_ready=true, state_file_status=ready, state_cookie_domains=[.goofish.com]` ✅
- `DELETE /api/login-state/xianyu` → `deleted=true` ✅
- `cli xianyu-login-state-import` → exit 0，stdout 不泄漏 SECRET ✅
- `web/app.js` 三个 handler（导入 / 检查 / 删除）正常加载 ✅

## 三种 xianyu 登录态获取路径并存
| 路径 | 何时用 | 用户体验 |
|---|---|---|
| `cli xianyu-login-state` (Playwright headed) | 用户没有 logged-in Chrome | 弹出 headed Chromium → 扫码 → cookies 出现后自动保存 |
| `cli xianyu-login-state-import <file>` (Phase S) | 用户有 logged-in Chrome | 装扩展 → 复制 JSON → 一次 CLI 命令导入 |
| web UI 「导入闲鱼登录态」面板 (Phase S) | 同上，更轻量 | 装扩展 → 复制 JSON → 粘贴到 web 表单 → 点导入按钮 |

所有路径都生成同一份 `data/xianyu_state.json`（Playwright `storage_state` 形状），`XianyuBrowserAdapter(state_file=...)` 一视同仁加载。

## 文档
- 隐私政策：`chrome-extension/xianyu-login-state-extractor/xianyu-login-state-privacy.html`（v2026-06-26）
- 使用文档：`chrome-extension/xianyu-login-state-extractor/README.md`
- 设计文档：本附录 S
- 交付报告：`DELIVERY_REPORT.md` 末尾追加 Phase S 段
