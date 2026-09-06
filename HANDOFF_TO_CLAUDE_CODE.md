# Claude Code Handoff

更新时间：2026-06-15

## 项目路径

```text
F:\WAMEIJI-XIANYU
```

## 当前可用公网预览

```text
https://4e3f58027aff37.lhr.life/?access_token=p0TFJ2PG7Vwv5Rm1DixKB9YzeluIsWot
```

匿名 localhost.run 域名不稳定，最新可用地址也写在：

```text
data/public-url.txt
```

如果链接失效，可尝试运行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/refresh_public_tunnel.ps1
```

## 已完成能力

- 中文 Web 控制台：KPI、机会表、详情、watchlist、复核、告警、扫描历史、配置、数据接入状态。
- SQLite 存储：watch、扫描记录、商品、闲鱼样本、机会、复核、通知去重。
- CLI：初始化、watch 管理、mock 扫描、watchlist 扫描、HTML/CSV/JSON 导入、评估、报告、回测、通知、复核。
- 业务核心：品番/JAN 标准化、标题匹配、闲鱼价格清洗、成本模型、利润/ROI/风险标签。
- 真实数据合规路径：`storage_state` 登录态导出、persistent profile、只读 HTML 采集、页面截图、页面网络响应摘要。
- Browser-Harness 状态机：遇到验证码、登录失效、安全验证、风控 token 时返回 `human_required`。
- 公网临时预览和刷新脚本。

## 关键命令

```powershell
python -m pytest
python -m ruff check src tests
python -m cd_monitor.cli web --db data/cd_monitor.db --host 127.0.0.1 --port 8765
python -m cd_monitor.cli doctor --db data/cd_monitor.db
```

登录态导出：

```powershell
python -m pip install -e ".[browser]"
python -m playwright install chromium
python -m cd_monitor.cli xianyu-login-state --output data/xianyu_state.json --timeout-seconds 180
```

只读采集：

```powershell
python -m cd_monitor.cli capture-live-html --source xianyu --catalog-no SRCL-3520 --state-file data/xianyu_state.json --output data/snapshots/xianyu_SRCL-3520.html --screenshot-output data/screenshots/xianyu_SRCL-3520.png --network-output data/snapshots/xianyu_SRCL-3520.network.json
python -m cd_monitor.cli capture-live-html --source xianyu --catalog-no SRCL-3520 --profile-dir data/browser_profiles/goofish --output data/snapshots/xianyu_SRCL-3520.html --screenshot-output data/screenshots/xianyu_SRCL-3520.png --network-output data/snapshots/xianyu_SRCL-3520.network.json
python -m cd_monitor.cli capture-live-html --source wameiji --catalog-no SRCL-3520 --output data/snapshots/wameiji_SRCL-3520.html --screenshot-output data/screenshots/wameiji_SRCL-3520.png --network-output data/snapshots/wameiji_SRCL-3520.network.json
```

闭环扫描：

```powershell
python -m cd_monitor.cli scan-live-html --catalog-no SRCL-3520 --state-file data/xianyu_state.json --db data/cd_monitor.db --snapshot-dir data/snapshots
python -m cd_monitor.cli scan-live-html --catalog-no SRCL-3520 --profile-dir data/browser_profiles/goofish --db data/cd_monitor.db --snapshot-dir data/snapshots
```

## 最近验证状态

```text
python -m pytest
125 passed

python -m ruff check src tests
All checks passed
```

## 重要文件

- `README.md`：用户运行说明和功能说明。
- `DELIVERY_REPORT.md`：完成情况、验证结果、未完成项。
- `CLAUDE_CODE_TODO.md`：已完成、未完成和后续补齐清单。
- `COMMUNITY_RESEARCH.md`：社区项目调研与方案取舍。
- `EXTERNAL_COLLECTOR_CONTRACT.md`：外部数据采集器对接契约。
- `scripts/refresh_public_tunnel.ps1`：临时公网链接刷新脚本。
- `src/cd_monitor/cli.py`：CLI 入口。
- `src/cd_monitor/web_server.py`：Web API。
- `src/cd_monitor/services/live_browser_capture.py`：只读 live HTML/截图/网络摘要采集。
- `src/cd_monitor/services/live_capture_scan.py`：两边采集后组合评估落库。
- `src/cd_monitor/sources/wameiji_browser.py` 与 `src/cd_monitor/sources/xianyu_browser.py`：HTML parser 和安全状态机。

## 当前限制

- 真实闲鱼登录态需要用户扫码生成 `data/xianyu_state.json` 或使用 `--profile-dir`。
- 真实挖煤姬网页登录态读取需要用户本机可见浏览器环境验证。
- 匿名 localhost.run 域名经常失效，长期使用应部署固定服务器或固定域名隧道。
- 本交接包不包含 cookie、token、数据库、浏览器 profile、截图、快照、网络日志和缓存。

## 后续建议

1. 先做固定部署：VPS / 内网穿透固定域名 / Cloudflare Tunnel 账号隧道。
2. 若使用外部采集层，按 `EXTERNAL_COLLECTOR_CONTRACT.md` 输出标准 JSON/HTML/CSV，由本项目负责入库、评估、展示和告警。
3. 用真实页面样本继续增强 Wameiji/Xianyu parser。
4. 把客户演示定位为“采购决策与复核平台”，数据源可来自授权 API、供应商数据、人工导入或外部采集器。
