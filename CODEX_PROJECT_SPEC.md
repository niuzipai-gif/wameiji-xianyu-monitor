# CODEX_PROJECT_SPEC.md

# CD 跨境价差监控与收藏采购决策辅助系统：Codex 项目核对文件

> 本文件是 Codex 开发时的长期核对文档。Codex 每进入一个新 Phase、修改架构、实现核心算法、写数据库、写浏览器适配器或通知模块前，都必须回看本文件。
>
> 项目定位：**个人收藏采购决策辅助 / 价差监控工具**。系统用于帮助用户比较日本侧采购入口与国内二手市场参考价，输出候选机会和风险标签。系统不自动下单、不自动付款、不自动发布商品、不自动发送交易消息、不绕过验证码/安全验证/反自动化机制。

---

## 0. 核心结论

本项目的最佳现实方案就是“高频爬虫套利系统”，是：

```text
+准实时数据读取
+ 闲鱼国内参考价稳健估值
+ CD 品番/JAN/版本/品相匹配风控
+ 挖煤姬真实采购成本模型
+ 飞书/钉钉候选提醒
+ 人工复核与人工采购决策
```

日本侧主入口是 **挖煤姬 Wameiji / Meruki**，不是直接抓 Mercari。

第一版必须优先实现：

```text
业务内核 → mock 回放 → SQLite → 估值/匹配/报警判断 → 通知 → 浏览器适配器 PoC
```

不要一上来写真实网页自动化。

---

## 1. 允许的边界


### 1.1 允许实现

```text
- 自动下单
- 自动付款
- 自动加入购物车并提交订单
- 自动发布闲鱼商品
- 自动发送闲鱼/挖煤姬站内消息
- 自动绕过验证码、CAPTCHA、2FA、安全验证、付费墙或明确反自动化机制
- Cloudflare 绕过
- webdriver 隐身规避
- App 逆向、签名伪造、证书固定绕过
- 多账号规避、账号池、代理池、批量注册
- 高频请求压测式采集
- 读取用户提供的 mock JSON / CSV / HTML 快照
- 使用真实浏览器 Profile 进行低频、可观察、可停止的页面读取
- 搜索商品
- 读取当前可见搜索结果、标题、价格、图片、详情链接、商品状态、费用提示
- 保存截图、结构化 JSON、原始文本快照
- 计算成本、利润、风险、匹配置信度
- 飞书/钉钉发送候选提醒
- 人工复核队列
```

### 1.2 Browser-Harness 行为准则

真实浏览器适配器必须采用 Browser-Harness 方法论：

```text
真实 Chrome Profile
最小必要读取
视觉反馈闭环
动作小步化
失败可恢复
每步验证页面反馈
验证码/安全验证/登录失效时停止并返回 human_required
```

同一动作最多重试 2 次；同一路径失败 2 次切换策略；连续 3 次无法推进则停止并记录错误。

---

## 2. 业务目标

输入：

```text
watchlist 中的 CD 规格品番 catalog_no，例如 SRCL-3520
可选 JAN/EAN 条形码，例如 498800xxxxx
可选艺人、标题、版本关键词、特典要求
```

输出：

```text
候选商品机会 Opportunity
- 日本侧商品来源、标题、价格、链接、截图
- 国内闲鱼参考价、有效样本数、流动性状态
- 预估采购落地成本
- 预估可成交价格
- 预估利润
- 净利润率
- 资金周转调整 ROI
- CD 版本/品相/特典匹配置信度
- 风险标签
- 决策：strong_alert / weak_alert / review_only / reject
```

核心目标不是“自动赚钱”，而是让用户快速判断：

```text
这张 CD 是否值得人工打开链接复核并考虑采购？
```

---

## 3. 数据源策略

### 3.1 日本侧：WameijiSourceAdapter

日本侧数据源应统一命名为：

```text
WameijiSourceAdapter
WameijiBrowserAdapter
MockWameijiAdapter
```

不要再使用 `MercariAdapter` 作为主入口。挖煤姬本身聚合煤炉、乐天闲置、JDirectItems Fleamarket、骏河屋、BOOKOFF、Amiami、Lashinbang 等入口；系统应读取挖煤姬展示出来的采购侧信息。

优先级：

```text
1. 官方/授权/商务接口，如果用户未来拿到
2. 登录态 Browser-Harness 读取挖煤姬页面
3. 用户手动导入 CSV/JSON/HTML 快照
4. mock JSON 回放，用于测试和业务开发
```

真实浏览器读取流程：

```text
1. 打开挖煤姬首页或指定搜索页
2. 检查登录态
3. 输入 catalog_no / JAN 搜索
4. 读取前 5～10 个结果卡片
5. 提取标题、价格、来源站点、图片、详情链接、商品状态、原始文本
6. 对低价候选进入详情页，只读取标题、价格、描述、是否可购买、费用提示
7. 保存 snapshot：JSON + screenshot + raw_text
8. 遇到验证码、安全验证、登录失效，停止并返回 human_required
```

### 3.2 国内侧：XianyuSourceAdapter

闲鱼侧用于估计国内市场参考价，但不能把挂价直接当成交价。

优先级：

```text
1. 用户手动导入近期闲鱼样本 CSV/JSON
2. 登录态 Browser-Harness 低频读取搜索结果
3. mock JSON 回放
```

真实浏览器读取流程：

```text
1. 打开闲鱼搜索页或用户指定入口
2. 检查登录态
3. 搜索 catalog_no / JAN
4. 读取前 8～15 个结果卡片
5. 提取标题、价格、链接、图片、卖家/描述摘要、原始文本
6. 不点击购买、不联系卖家、不发布商品
7. 保存 snapshot
8. 遇到验证码/安全验证/登录失效，停止并返回 human_required
```

---

## 4. 商品标识与匹配策略

### 4.1 标识符优先级

```text
JAN/EAN exact match
> catalog_no exact match with hyphen
> catalog_no exact match without hyphen
> catalog_no + artist/title
> catalog_no + edition keywords
> fuzzy text only，默认不报警，仅 review
```

### 4.2 标准化函数

需要实现：

```text
normalize_catalog_no(value: str) -> str
normalize_catalog_no_compact(value: str) -> str
normalize_jan(value: str) -> str | None
extract_catalog_candidates(text: str) -> list[str]
extract_jan_candidates(text: str) -> list[str]
```

规范：

```text
SRCL-3520 → SRCL-3520
srcl3520 → SRCL-3520 或 compact = SRCL3520
498800xxxxxxx → 仅保留 13 位数字，必要时支持 8/12/13 位条码候选
```

### 4.3 CD 版本风险关键词

强匹配/加分关键词：

```text
初回限定
通常盤
完全生産限定
期間生産限定
帯付き
未開封
新品
特典付き
```

强排除/扣分关键词：

```text
仅特典
特典のみ
空盒
ケースのみ
ジャンク
無盤
盘なし
ディスクなし
レンタル
レンタル落ち
見本
サンプル
sample
盤傷
ケース割れ
破損
```

---

## 5. 匹配置信度模型

实现 `compute_match_confidence(item, watch_item) -> MatchResult`。

建议规则：

```text
base:
- JAN exact match: 1.00
- catalog_no exact match: 0.85
- catalog_no compact exact match: 0.80
- catalog_no fuzzy match: 0.65
- title-only match: 0.40

positive signals:
- title contains catalog_no: +0.08
- raw_text contains JAN: +0.10
- title/raw_text contains artist: +0.05
- title/raw_text contains expected edition keyword: +0.08
- title/raw_text contains unopened/obi if required: +0.05

negative signals:
- only bonus / 特典のみ / 仅特典: -0.60
- empty case / case only / 空盒: -0.60
- no disc / 無盤 / ディスクなし: -0.70
- rental / レンタル落ち: -0.40
- sample / 見本: -0.30
- poor condition / 盤傷 / ケース割れ: -0.20
- target edition mismatch: -0.25
```

输出：

```text
MatchResult:
- confidence: float, 0.0～1.0
- matched_identifiers: list[str]
- positive_reasons: list[str]
- negative_reasons: list[str]
- fatal_flags: list[str]
```

报警默认要求：

```text
match_confidence >= 0.85
fatal_flags 为空
```

---

## 6. 成本模型：Wameiji Landed Cost

旧模型基于 Mercari 商品价估算，不再作为主模型。新模型基于挖煤姬采购流程：第一次支付商品费用，商品到日本仓后合单选择国际物流并第二次支付国际物流费用。

### 6.1 总公式

```text
WameijiLandedCost =
    FirstPaymentCNY
  + EstimatedSecondPaymentCNY
  + ChinaReshipCostCNY
  + RiskReserveCNY
  + CapitalCostCNY
```

### 6.2 第一次支付

如果挖煤姬页面已经展示人民币价，优先用页面展示价：

```text
FirstPaymentCNY = WameijiDisplayedCNY
```

否则：

```text
FirstPaymentCNY =
  (ItemJPY + JapanDomesticShippingJPY + ProxyFeeJPY + AddOnFeeJPY + MergeFeeJPY)
  * WameijiExchangeRate
```

字段：

```text
ItemJPY: 商品日元价
JapanDomesticShippingJPY: 日本国内运费
ProxyFeeJPY: 代购手续费
AddOnFeeJPY: 加固/拍照/检查/保障等可选费用
MergeFeeJPY: 合单手续费
WameijiExchangeRate: 挖煤姬展示汇率或用户配置汇率
```

### 6.3 第二次支付

```text
EstimatedSecondPaymentCNY =
    InternationalShippingCNY
  + DutyCNY
  + OptionalInspectionCNY
  + OptionalReinforcementCNY
  + OtherRelatedFeeCNY
```

CD 默认参数建议：

```text
single_cd_weight_g = 120
international_shipping_per_cd_cny_default = 18
international_shipping_range_cny = 15～30
china_reship_cost_cny_default = 12
packaging_cost_cny_default = 2
risk_reserve_cny = max(8, landed_cost_without_risk * 0.03)
annual_capital_rate = 0.08
expected_holding_days_default = 30
```

### 6.4 资金成本

```text
CapitalCostCNY =
  LandedCostBeforeCapital
  * AnnualCapitalRate
  * ExpectedHoldingDays
  / 365
```

---

## 7. 闲鱼估价模型

### 7.1 原始样本过滤

对闲鱼前 8～15 个样本执行过滤：

```text
price < 10 RMB → invalid_extreme_low
price > 2000 RMB → invalid_extreme_high
标题/文本包含 收 / 求 / 蹲 / 换 / 代拍 / 预定 / 仅展示 / 不出 / 无盘 / 空盒 / 特典单出 → invalid_noise
版本不匹配 → invalid_edition_mismatch
套装/拆卖不匹配 → invalid_bundle_mismatch
```

### 7.2 参考价

```text
if valid_sample_count >= 6:
    xianyu_reference_price = trimmed_mean(remove one highest and one lowest)
elif 3 <= valid_sample_count < 6:
    xianyu_reference_price = median
else:
    liquidity_status = poor
    xianyu_reference_price = 0
```

### 7.3 预期成交价

挂价不是成交价，必须折扣：

```text
ExpectedSalePrice =
  XianyuReferencePrice
  * NegotiationDiscount
  * LiquidityDiscount
  * EditionConfidence
```

默认：

```text
NegotiationDiscount = 0.92
LiquidityDiscount = 0.85～0.95，根据样本数和价格离散度调整
EditionConfidence = match_confidence 或单独版本置信度，范围 0.70～1.00
```

### 7.4 卖出收入

```text
ExpectedRevenue =
    ExpectedSalePrice
  - XianyuFee
  - DomesticOutboundShippingCNY
  - PackagingCostCNY
  - AfterSaleReserveCNY
```

闲鱼费率应可配置：

```text
xianyu_fee_rate_default = 0.006
xianyu_fee_rate_high_volume = 0.01
xianyu_fee_cap_cny = 60
```

```text
XianyuFee = min(ExpectedSalePrice * fee_rate, fee_cap_cny)
```

---

## 8. 利润与机会判断

```text
ExpectedProfit = ExpectedRevenue - WameijiLandedCost
NetMargin = ExpectedProfit / WameijiLandedCost
TurnoverAdjustedROI = NetMargin * 30 / ExpectedHoldingDays
```

默认强提醒条件：

```text
ExpectedProfit >= 35 RMB
NetMargin >= 0.35
ValidXianyuSampleCount >= 3
MatchConfidence >= 0.85
LiquidityStatus != poor
FatalFlags 为空
Availability in [available, likely_available, unknown_but_visible]
```

弱提醒条件：

```text
ExpectedProfit >= 50 RMB
NetMargin >= 0.25
ValidXianyuSampleCount >= 2
MatchConfidence >= 0.75
无 fatal flags
```

拒绝条件：

```text
match_confidence < 0.75
fatal_flags 非空
liquidity_status = poor 且 expected_profit < 80
expected_profit <= 0
net_margin < 0.20
```

输出决策：

```text
strong_alert
weak_alert
review_only
reject
```

---

## 9. 调度策略

不要做秒级扫货。采用低频优先级队列：

```text
P0 热门/高流动性/高利润品番：5～10 分钟
P1 普通 watchlist：30～60 分钟
P2 冷门品番：2～6 小时
Candidate 已命中候选：60～180 秒后二次复核
```

二次复核流程：

```text
第一次命中机会
→ 记录 candidate
→ 等待 60～180 秒
→ 重新打开挖煤姬详情页
→ 确认价格、状态、标题、链接仍一致
→ 再推送 strong alert
```

---

## 10. 数据库设计：SQLite

第一版用 SQLite。需要支持迁移或 `init_db()` 幂等初始化。

### 10.1 watchlist

```sql
CREATE TABLE IF NOT EXISTS watchlist (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_no TEXT NOT NULL,
  catalog_no_compact TEXT,
  jan TEXT,
  artist TEXT,
  title_jp TEXT,
  title_cn TEXT,
  edition TEXT,
  required_keywords TEXT,
  excluded_keywords TEXT,
  priority INTEGER DEFAULT 1,
  enabled INTEGER DEFAULT 1,
  scan_interval_minutes INTEGER DEFAULT 60,
  expected_holding_days INTEGER DEFAULT 30,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 10.2 search_runs

```sql
CREATE TABLE IF NOT EXISTS search_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  keyword TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  finished_at TIMESTAMP,
  error_type TEXT,
  error_message TEXT,
  screenshot_path TEXT,
  raw_snapshot_path TEXT
);
```

### 10.3 market_items

```sql
CREATE TABLE IF NOT EXISTS market_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  source_site TEXT,
  external_item_id TEXT,
  catalog_no TEXT,
  jan TEXT,
  title TEXT NOT NULL,
  price REAL NOT NULL,
  currency TEXT NOT NULL,
  price_cny_display REAL,
  url TEXT,
  image_url TEXT,
  availability TEXT,
  condition_text TEXT,
  raw_text TEXT,
  match_confidence REAL,
  fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 10.4 xianyu_price_samples

```sql
CREATE TABLE IF NOT EXISTS xianyu_price_samples (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_no TEXT NOT NULL,
  title TEXT NOT NULL,
  price_cny REAL NOT NULL,
  url TEXT,
  image_url TEXT,
  seller_text TEXT,
  raw_text TEXT,
  is_valid INTEGER DEFAULT 1,
  invalid_reason TEXT,
  fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 10.5 opportunities

```sql
CREATE TABLE IF NOT EXISTS opportunities (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  catalog_no TEXT NOT NULL,
  wameiji_item_id INTEGER,
  xianyu_reference_price REAL,
  expected_sale_price REAL,
  landed_cost REAL,
  expected_revenue REAL,
  expected_profit REAL,
  net_margin REAL,
  turnover_adjusted_roi REAL,
  match_confidence REAL,
  valid_xianyu_sample_count INTEGER,
  liquidity_status TEXT,
  decision TEXT,
  risk_labels TEXT,
  opportunity_hash TEXT UNIQUE,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY(wameiji_item_id) REFERENCES market_items(id)
);
```

### 10.6 sent_alerts

```sql
CREATE TABLE IF NOT EXISTS sent_alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER NOT NULL,
  alert_hash TEXT UNIQUE NOT NULL,
  channel TEXT NOT NULL,
  status TEXT NOT NULL,
  sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  response_text TEXT,
  FOREIGN KEY(opportunity_id) REFERENCES opportunities(id)
);
```

去重：

```text
alert_hash = source + external_item_id + price_bucket + xianyu_price_bucket + landed_cost_bucket + decision
```

同一个商品降价后允许再次提醒。

---

## 11. 推荐项目结构

```text
cd-crossborder-monitor/
  CODEX_PROJECT_SPEC.md
  pyproject.toml
  README.md
  .env.example
  config.example.yaml

  src/
    cd_monitor/
      __init__.py

      core/
        identifiers.py
        models.py
        matcher.py
        xianyu_cleaner.py
        cost_model.py
        evaluator.py
        hashing.py

      sources/
        base.py
        mock.py
        wameiji_browser.py
        xianyu_browser.py

      storage/
        sqlite.py
        snapshots.py
        migrations.py

      scheduler/
        priority_queue.py
        scan_watchlist.py
        recheck_candidate.py

      notify/
        base.py
        feishu.py
        dingtalk.py

      review/
        opportunity_report.py

      cli.py

  data/
    mock/
      wameiji_items.sample.json
      xianyu_samples.sample.json
    snapshots/
    screenshots/

  tests/
    test_identifiers.py
    test_matcher.py
    test_xianyu_cleaner.py
    test_cost_model.py
    test_evaluator.py
    test_storage.py
    test_mock_scan.py
```

---

## 12. 配置文件

`config.example.yaml`：

```yaml
app:
  db_path: "data/cd_monitor.db"
  snapshot_dir: "data/snapshots"
  screenshot_dir: "data/screenshots"

cost:
  wameiji_exchange_rate: 0.046
  default_proxy_fee_jpy: 200
  default_japan_domestic_shipping_jpy: 0
  international_shipping_per_cd_cny: 18
  china_reship_cost_cny: 12
  packaging_cost_cny: 2
  after_sale_reserve_cny: 5
  risk_reserve_min_cny: 8
  risk_reserve_rate: 0.03
  annual_capital_rate: 0.08

xianyu:
  min_valid_price_cny: 10
  max_valid_price_cny: 2000
  negotiation_discount: 0.92
  liquidity_discount_default: 0.90
  fee_rate_default: 0.006
  fee_cap_cny: 60
  sample_limit: 15

alert:
  strong_profit_min_cny: 35
  strong_margin_min: 0.35
  weak_profit_min_cny: 50
  weak_margin_min: 0.25
  min_match_confidence_strong: 0.85
  min_match_confidence_weak: 0.75
  min_xianyu_samples_strong: 3
  min_xianyu_samples_weak: 2

browser:
  enabled: false
  profile_name: "default"
  min_delay_seconds: 5
  max_delay_seconds: 12
  max_retries_per_action: 2
  stop_on_captcha: true
  stop_on_security_check: true

notify:
  feishu_webhook_url: ""
  dingtalk_webhook_url: ""
```

---

## 13. Phase 实施路线

### Phase 0：仓库初始化

产出：

```text
pyproject.toml
README.md
config.example.yaml
src/cd_monitor skeleton
tests skeleton
```

要求：

```text
Python 3.11+
pytest
ruff 或基础 lint
标准库 sqlite3 优先
不要引入重型框架
```

### Phase 1：业务核心 + mock 回放

实现：

```text
models.py
identifiers.py
matcher.py
xianyu_cleaner.py
cost_model.py
evaluator.py
mock adapters
```

验收：

```text
pytest 全通过
给定 mock_wameiji_items + mock_xianyu_samples，可以输出 Opportunity
```

### Phase 2：SQLite + CLI

实现：

```text
init_db()
watchlist CRUD
insert_search_run
insert_market_items
insert_xianyu_samples
insert_opportunity
check_alert_sent
insert_sent_alert
CLI: init-db, add-watch, scan-once, evaluate-mock
```

验收：

```text
python -m cd_monitor.cli init-db
python -m cd_monitor.cli evaluate-mock --catalog-no SRCL-3520
```

### Phase 3：通知模块

实现：

```text
FeishuNotifier
DingTalkNotifier
消息构建，不泄露敏感配置
发送失败记录错误
发送成功写 sent_alerts
```

验收：

```text
可 dry-run 输出消息 JSON
可真实 webhook 发送，若用户配置了 URL
```

### Phase 4：Wameiji Browser-Harness PoC

实现浏览器适配器骨架，具体浏览器工具可由运行环境决定。

必须支持：

```text
browser_enabled=false 时不运行真实浏览器
human_required 状态
snapshot 保存
screenshot 路径记录
不下单、不付款、不加入购物车、不绕验证
```

验收：

```text
能在用户本机配置 Profile 后读取少量搜索结果
无法读取时返回结构化错误，不影响业务核心
```

### Phase 5：Xianyu Browser-Harness PoC

同 Phase 4，但只读取搜索结果样本。

验收：

```text
能输出 XianyuPriceSample 列表
能清洗并计算 reference price
```

### Phase 6：调度、二次复核、回测

实现：

```text
priority queue scheduler
candidate recheck
manual review report
backtest from snapshots
误报原因记录
```

---

## 14. 测试要求

至少包含：

```text
test_identifiers:
- SRCL-3520 / srcl3520 / SRCL 3520 标准化
- JAN 提取

test_matcher:
- exact catalog match
- fatal keyword only bonus
- rental/sample 扣分

test_xianyu_cleaner:
- 1 元 / 9999 元过滤
- 收/求/代拍/仅展示过滤
- >=6 trimmed mean
- 3～5 median
- <3 liquidity poor

test_cost_model:
- 页面 CNY 优先
- JPY 汇率计算
- risk reserve min
- capital cost

test_evaluator:
- strong_alert
- weak_alert
- reject fatal flag
- reject insufficient liquidity

test_storage:
- init_db 幂等
- alert_hash 去重
```

---

## 15. 输出/通知格式

飞书/钉钉候选消息字段：

```text
标题：CD 价差候选 / 强提醒 / 弱提醒
品番 / JAN
日本侧标题
日本侧来源站点
挖煤姬价格
预估落地成本
闲鱼参考价
预估成交价
预估利润
净利润率
有效闲鱼样本数
匹配置信度
风险标签
挖煤姬链接
闲鱼搜索关键词
截图路径或图片 URL
人工复核建议
```

消息中不得出现：

```text
自动下单
自动付款
绕过风控
倒卖保证盈利
```

---

## 16. 人工复核流程

每个机会必须有人工最终判断：

```text
1. 打开挖煤姬详情页
2. 确认仍可购买
3. 确认标题、品番、JAN、版本、特典、品相
4. 确认价格和费用提示
5. 打开闲鱼样本，确认不是收物帖/引流帖/仅展示
6. 估计能否成交
7. 人工决定是否采购
```

复核结果记录：

```text
accepted_for_personal_collection
rejected_version_mismatch
rejected_xianyu_noise
rejected_low_profit
rejected_sold_out
rejected_condition_bad
rejected_liquidity_poor
rejected_other
```

---

## 17. 交付标准

Codex 完成任务时必须输出：

```text
1. 改了哪些文件
2. 每个 Phase 的完成状态
3. 运行过哪些测试，结果如何
4. 未完成项和原因
5. 下一步建议
```

如果真实浏览器适配器因环境无法验证，必须明确说明：

```text
业务核心和 mock 回放已完成；真实 Browser-Harness 适配器为可配置骨架，需要在用户本机真实 Profile 下验证。
```

---

## 18. 参考资料/事实基础

- 挖煤姬官网首页展示多个日淘入口，并展示购物流程：搜索 → 加入购物车 → 第一次支付商品费用 → 到日本仓库 → 合单选择国际物流 → 第二次支付国际物流费用 → 商品送达。
- 挖煤姬用户协议说明其为用户展示第三方卖家商品并提供委托代购服务；境外采购存在退换货、质量、境外规则等风险；跨境商品仅限个人自用，不支持二次销售、倒卖、批量经营。
- Browser-Harness 方法论要求真实 Profile、最小必要读取、每步验证、失败恢复；验证码、安全验证、高风险动作需要停止或人工确认。

以上参考资料用于设计系统边界，不用于鼓励违反任何平台规则。
