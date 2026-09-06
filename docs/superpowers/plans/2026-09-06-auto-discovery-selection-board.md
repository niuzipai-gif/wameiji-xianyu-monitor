# 自动发现选品广场：实施计划

> 依据 `docs/superpowers/specs/2026-09-06-auto-discovery-selection-board-design.md`。

## 目标

在不破坏现有单品监控的前提下，增加由本机浏览器执行的 CD／实体游戏候选池轮询。采集到的挖煤姬商品会被规范化、用品番／JAN或标题版本匹配闲鱼样本、计算利润并同步到 Render；GitHub Pages 默认显示按预计利润金额排序的选品广场。

## 实施顺序

### 1. 领域模型、SQLite 迁移和查询接口

**文件**

- 新增 `src/cd_monitor/core/discovery.py`
- 修改 `src/cd_monitor/storage/sqlite.py`
- 修改 `src/cd_monitor/storage/migrations.py`
- 新增 `tests/test_discovery_storage.py`

**工作**

1. 定义 `DiscoveryPool`、`DiscoveryKeyword`、`DiscoveryCandidate`、`DiscoveryRun` 和 `CollectorCommand` 数据类。
2. 为 `discovery_pools`、`discovery_keywords`、`discovery_runs`、`discovery_candidates`、`collector_commands` 建立幂等迁移。
3. 给现有 `opportunities` 增加候选关联、媒体类型、身份键和最后观察时间，不改变原有单品数据含义。
4. 实现池、关键词、候选、运行记录和命令的 CRUD 与按利润倒序机会查询。

**先写的失败测试**

- 新库初始化自动生成 CD／实体游戏两个默认池。
- 重复调用迁移不会丢失旧的 watchlist 和 opportunity 数据。
- 身份键优先级为品番、JAN、来源 ID、标题版本哈希。
- 默认机会流严格以 `expected_profit DESC` 排序，旧单品机会仍可被读取。

### 2. 离线候选发现和评估服务

**文件**

- 新增 `src/cd_monitor/services/discovery.py`
- 修改 `src/cd_monitor/services/live_browser_capture.py`
- 视解析结果修改 `src/cd_monitor/sources/wameiji_browser.py` 与 `src/cd_monitor/sources/xianyu_browser.py`
- 新增 `tests/test_discovery_service.py`

**工作**

1. 让现有可见搜索页采集函数接受通用搜索词，同时保持 `catalog_no` 参数兼容。
2. 从挖煤姬结果页抽取候选，过滤不可购买、排除词和重复卡片。
3. 为每张到期候选生成闲鱼查询词，复用已有 HTML 解析、价格清洗、成本模型和机会评估器。
4. 仅对新候选、价格变化候选或复核到期候选查询闲鱼；成功扫描中的已售罄与多次缺失候选按规则进入 `expired`／`stale`。
5. 对安全验证、登录失效和解析失败保留旧结果并写入 `human_required`／失败运行记录。

**先写的失败测试**

- 固定的挖煤姬与闲鱼 HTML 能产生 CD 和实体游戏候选及利润机会。
- 标题兜底身份键可查询但不会越过高置信度阈值进入默认利润榜。
- 同一价格未变候选不会在短期内第二次查询闲鱼。
- 显式售罄与三次成功缺失的状态变化符合规格。

### 3. 本机循环和 Render 命令回传

**文件**

- 新增 `src/cd_monitor/services/discovery_worker.py`
- 修改 `src/cd_monitor/cli.py`
- 修改 `scripts/start-all.ps1`
- 新增 `scripts/start-discovery.ps1`
- 修改 `scripts/publish-replica.py`（仅在需要的共享请求代码处）
- 新增 `tests/test_discovery_worker.py`

**工作**

1. 新增 `discovery-worker` CLI：按池到期时间扫描、低频轮询并响应立即扫描标记。
2. 将 worker 纳入采集电脑的一键启动脚本；它与本地 Web 服务、数据库副本发布器独立重启。
3. Render 端保存家中页面提交的命令；本机 worker 轮询受同步密钥保护的 collector 端点，幂等执行并确认结果。
4. 采集机离线、Render 重启或浏览器需要人工处理时，命令状态可回查且不会伪称完成。

**先写的失败测试**

- 远程“立即扫描”命令只执行一次并留下确认记录。
- 禁用池后 worker 不启动该池。
- 远程请求失败不会终止下一轮本机扫描。

### 4. API 和选品广场前端

**文件**

- 修改 `src/cd_monitor/web_server.py`
- 修改 `web/index.html`
- 修改 `web/app.js`
- 修改 `web/style.css`（如现有样式文件需要）
- 新增 `tests/test_discovery_api.py`
- 新增或扩展 `tests/test_ui_complete_features.py`

**工作**

1. 提供池、关键词、运行状态、候选、按利润排序机会和命令状态 API。
2. 首页改为选品广场，展示最后扫描、同步状态、活跃候选数、有效机会数和利润榜。
3. “新建任务”改为“高级单品复核”，候选池编辑改为默认配置入口。
4. GitHub Pages 保留现有 Render URL／访问令牌配置；“立即扫描”只创建命令，页面显示待接收、执行中、完成或人工处理。

**先写的失败测试**

- API 以 `expected_profit` 降序返回并包含媒体类型、身份键、最后观察时间。
- 普通浏览 token 可以提交命令；collector 专用端点拒绝缺失同步密钥的请求。
- 页面源代码不再把品番输入设为首页必须动作。

### 5. 真实浏览器冒烟验证和部署

**文件**

- 修改 `README.md` 与 `TAKEOVER_STATUS.md`
- 必要时修改 `render.yaml`、GitHub Pages 工作流

**工作**

1. 使用已登录的挖煤姬和闲鱼本机档案，以一个低预算关键词跑一次只读真实扫描。
2. 检查截图、HTML、解析数量、候选数、机会排序和 Render 副本。
3. 部署 Render，发布数据库副本，确认 GitHub Pages 在家中可读。
4. 更新交接说明，使采集电脑只需双击启动器。

## 每段验证

先运行目标测试，再运行：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_discovery_storage.py tests/test_discovery_service.py tests/test_discovery_worker.py tests/test_discovery_api.py -q
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check src tests
```

真实浏览器扫描是最后的可观察冒烟检查，遇到登录、验证码或风控则停在 `human_required`，不尝试绕过。

## 交付边界

- 前端、Render API 和本机采集器之间只传递数据与扫描命令。
- 本机 SQLite 保留为唯一事实来源；Render 免费实例只是可恢复的读取副本和短时命令中继。
- 不添加任何付费数据源，也不实施交易类浏览器操作。
