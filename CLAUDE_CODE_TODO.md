# Claude Code TODO / Gap List

更新时间：2026-06-15

本文件用于交接给后续工程方。当前项目已经具备“采购决策、复核、展示、告警、报告”主体能力；缺口主要集中在更稳定的真实数据采集层和长期部署。

## Codex 已完成

### 1. 项目主体

- Python 包结构、`pyproject.toml`、配置样例、测试框架。
- SQLite 初始化与核心表：watchlist、扫描记录、商品、闲鱼样本、机会、通知、复核、候选二次复核。
- CLI：初始化、watch 管理、mock 扫描、watchlist 扫描、HTML/CSV/JSON 导入、评估、报告、回测、通知、复核、doctor、Web 启动。
- 中文 Web 控制台：KPI、机会列表、详情、样本、watchlist、扫描历史、告警、复核、配置、真实数据接入状态。

### 2. 业务逻辑

- 品番/JAN 标准化。
- 标题匹配和匹配置信度。
- 闲鱼价格清洗、异常样本过滤、样本上限。
- Wameiji 到手成本模型。
- 利润、净利率、周转 ROI、风险标签、强/弱提醒判断。
- 回测、复核报告、通知去重。

### 3. 数据接入入口

- mock 数据回放。
- 手动 HTML/CSV/JSON 导入。
- Wameiji HTML parser。
- Xianyu/Goofish HTML parser。
- `xianyu-login-state`：用户可见扫码/登录后导出 Playwright `storage_state`。
- `capture-live-html`：只读打开搜索页、保存 HTML、截图、页面网络响应摘要。
- `scan-live-html`：两边采集后组合评估落库。
- `--state-file` 与 `--profile-dir` 两种登录态复用方式。

### 4. 证据留存

- HTML 快照。
- 搜索页截图。
- 页面网络响应摘要：只保存 URL、状态码、content-type、截断正文；不保存 cookie、请求头、storage_state 明文。
- `human_required` 状态：遇到验证码、登录失效、安全验证、风控 token 时停止，不伪造机会。

### 5. 文档与交接

- `README.md`
- `DELIVERY_REPORT.md`
- `COMMUNITY_RESEARCH.md`
- `HANDOFF_TO_CLAUDE_CODE.md`
- `EXTERNAL_COLLECTOR_CONTRACT.md`
- `output/WAMEIJI-XIANYU-handoff.zip`

### 6. 验证

```text
python -m pytest
125 passed

python -m ruff check src tests
All checks passed
```

## Codex 未完成 / 需要后续补

### A. 稳定真实数据采集层

当前只完成了合规的可见浏览器只读采集。若后续工程方需要替换采集层，应产出当前系统可消费的标准文件：

- Wameiji HTML / JSON
- Xianyu HTML / JSON / CSV
- 状态 JSON
- 截图与网络证据路径

对接格式见：

```text
EXTERNAL_COLLECTOR_CONTRACT.md
```

当前系统消费入口：

```powershell
python -m cd_monitor.cli evaluate-html ...
python -m cd_monitor.cli evaluate-json ...
python -m cd_monitor.cli import-csv ...
```

### B. 固定公网部署

当前公网链接依赖匿名 localhost.run，域名会失效。需要后续补：

- VPS / 云服务器部署
- 固定域名
- HTTPS
- 进程守护
- 数据备份
- token / basic auth / IP allowlist

临时脚本：

```powershell
powershell -ExecutionPolicy Bypass -File scripts/refresh_public_tunnel.ps1
```

### C. 真实页面 parser 增强

需要用实际采集到的 Wameiji/Goofish 页面样本继续增强：

- 更多搜索结果卡片结构。
- 更多价格文本格式。
- 售出/下架/预定状态。
- 图片懒加载字段。
- 商品详情字段。
- 卖家/来源/品相字段。

建议先把真实样本放到：

```text
data/external/<catalog_no>/
```

再用 `evaluate-html` / `evaluate-json` 验证。

### D. 账号/登录态运维

当前只支持：

- `data/xianyu_state.json`
- `--profile-dir data/browser_profiles/goofish`

后续可补：

- 多账号元数据管理。
- 登录态失效提醒。
- 登录态轮换策略。
- 每个 watch/task 绑定账号。
- Web 页面上传/更新登录态。

### E. 生产级任务调度

当前已有 CLI 和基础扫描能力，后续可补：

- 后台 worker。
- Cron / APScheduler 持久化任务。
- Web 中管理任务频率。
- 失败重试和暂停策略。
- 扫描队列和并发控制。

### F. 客户交付材料

建议补：

- 一键启动脚本。
- Windows 桌面快捷方式。
- Dockerfile / docker-compose 生产版。
- 客户演示数据包。
- 操作手册 PDF。
- 运维手册。

## Codex 必须实现的部分

- 验证码绕过。
- Cloudflare / 安全验证绕过。
- 代理池。
- 浏览器指纹伪装。
- TLS impersonation。
- 自动交易、自动付款、自动加购、自动私信、自动发布商品。

如果后续工程方自行实现外部采集层，请让其输出 `EXTERNAL_COLLECTOR_CONTRACT.md` 中定义的文件格式；当前系统不需要知道采集层内部实现。

## 建议交接顺序

1. 先运行测试确认主体稳定：

```powershell
python -m pytest
python -m ruff check src tests
```

2. 启动 Web：

```powershell
python -m cd_monitor.cli web --db data/cd_monitor.db --host 127.0.0.1 --port 8765
```

3. 用 mock 扫描验证业务链路：

```powershell
python -m cd_monitor.cli scan-once --catalog-no SRCL-3520 --source mock --db data/cd_monitor.db
```

4. 外部采集层产出样本后，走：

```powershell
python -m cd_monitor.cli evaluate-html --catalog-no SRCL-3520 --wameiji-html data/external/SRCL-3520/wameiji.html --xianyu-html data/external/SRCL-3520/xianyu.html --db data/cd_monitor.db --snapshot-dir data/snapshots
```

5. 打开 Web 查看机会、报告、复核、告警。
