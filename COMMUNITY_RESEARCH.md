# Community Research Notes

更新时间：2026-06-14

本轮继续检索 Goofish / Xianyu / Wameiji / Meruki 社区项目，目标是找更稳的数据接入方案，同时保持本项目边界：个人收藏采购决策辅助、只读低频、遇到验证码/安全验证停止、不自动交易。

## 结论

当前最适合本项目的路线仍是：

```text
Playwright 可见浏览器 / persistent profile / storage_state
+ 只读搜索页 HTML 或页面上下文只读数据
+ 本地 parser / evaluate-html
+ human_required 状态机
```

不建议接入高速 mtop HTTP 客户端、TLS impersonation、代理池、反检测参数、自动发布/消息/交易工具。

## 可采纳模式

### 1. Playwright storage_state / persistent context

Playwright 官方认证文档说明，Web 登录态通常存储在 cookies、local storage 或 IndexedDB，`browser_context.storage_state()` 可导出状态并用于创建预填充登录态的新 context。

本项目已采纳：

- `xianyu-login-state`：用户自己扫码/登录后导出 `data/xianyu_state.json`
- `XianyuBrowserAdapter`：检查 state 文件是否存在、结构是否有效、是否包含 goofish/xianyu/taobao 等域名
- `capture-live-html` / `scan-live-html`：只读使用 state 文件打开搜索页
- `capture-live-html --profile-dir` / `scan-live-html --profile-dir`：可复用 Playwright persistent profile 打开闲鱼搜索页

### 2. 可见 profile + 页面上下文只读读取

[`mercy719/goofish-mcp-server`](https://github.com/mercy719/goofish-mcp-server) 使用 Node.js + Playwright，并在页面上下文里调用 `window.lib.mtop.request(...)`；它默认使用 persistent browser profile，需要用户首次手动登录，后续复用 session。

可采纳思路：

- 用户手动登录一次，复用 persistent profile（已通过 `--profile-dir` 落地）
- 只读 search/detail 可以作为未来增强方向
- 保持非 headless、可观察、可停止

暂不直接接入原因：

- 当前 Python 项目已经有 storage_state/HTML 只读闭环
- 页面上下文 mtop 仍需真实站点验证和严格只读封装
- 需要防止工具暴露写能力

### 3. 统一输出契约与风控熔断

[`fancyboi999/goofish-cli`](https://github.com/fancyboi999/goofish-cli) 的社区实践里有统一输出格式、命令 registry、风控关键字识别和熔断思路；其 docs 提到响应体识别 `RGV587_ERROR / punish / FAIL_SYS_USER_VALIDATE` 等关键字，并触发熔断。

已落地：

- `WameijiBrowserAdapter` 与 `XianyuBrowserAdapter` 已增强 `human_required` 关键字列表。
- 新增覆盖 `RGV587_ERROR`、`FAIL_SYS_USER_VALIDATE`、`punish`、`____tmd____`、`x5step` 的回归测试。

后续可继续：

- 对 live capture 增加“风控文本检测后冷却/停止”的状态记录。
- 保持 JSON 输出契约稳定

暂不采纳内容：

- 发布、下架、IM、地址、图片上传等写接口
- mtop 签名实现

### 4. 本地控制台与证据留存

[`tristanwqy/GooFish-AIMonitor`](https://github.com/tristanwqy/GooFish-AIMonitor) 是 2026-06-13 新建、2026-06-14 更新的本地闲鱼监控台项目，采用 Python / FastAPI / Playwright / React / SQLite 组合，并强调本地运行、登录态、事件流和页面化管理。

可采纳思路：

- 本地 SQLite 和 Web 控制台继续保留为主操作面。
- 真实采集结果应该能留存证据，方便人工复核。

已落地：

- `capture-live-html` 增加 `--screenshot-output`，可在保存搜索页 HTML 的同时保存当前页截图。
- `scan-live-html` 默认在 `live_screenshots` 目录保存 Wameiji/Goofish 两边搜索页截图。
- `capture-live-html --network-output` 与 `scan-live-html` 默认 `live_network` 目录可保存可见页面自己加载到的站点响应摘要，用于解析调试和人工复核。
- Web 设置区的“真实数据接入流程”已同步展示带截图参数的命令。

## 不采纳模式

### 1. 高速 mtop + TLS impersonation

[`Edioff/goofish-scrape`](https://github.com/Edioff/goofish-scrape) 明确描述了 MTOP SDK、动态 cookies、签名和 TLS fingerprint，并使用 Playwright 抓 cookie + `curl_cffi` Chrome TLS impersonation 做高速 API 请求。

不采纳原因：

- 该路线核心价值在绕过/规避反机器人检测和高吞吐采集
- 与本项目“低频、可观察、可停止”的 Browser-Harness 边界冲突
- 容易滑向批量数据采集，不适合个人收藏采购决策辅助

### 2. 原始 cookie 字符串 + 代理 + 反检测参数

部分 OpenClaw/skill 项目通过原始 cookie 字符串、代理配置、浏览器反检测参数启动采集。

不采纳原因：

- 原始 cookie 字符串更容易泄露敏感凭据
- 代理池/反检测参数在本项目禁止边界内
- 本项目用本地 `storage_state` 文件和摘要状态代替 cookie 明文

### 3. 自动发布、自动消息、自动发货、卖家 SaaS 工具

GitHub topics 里出现了 seller tool、auto-reply、auto-delivery、publish item 等项目。

不采纳原因：

- 本项目不是闲鱼卖家运营工具
- 自动发布/私信/发货与项目规格直接冲突

## Wameiji / Meruki 发现

社区里没有找到稳定、公开、适合接入的 Wameiji/Meruki 商品搜索 API。搜索结果主要出现了 `app.meruki.cn` 开屏广告相关 rewrite 规则，不能作为商品数据源。

当前 Wameiji 最稳路线仍是：

- 用户登录态可见浏览器
- 打开搜索页
- 只读 HTML/snapshot
- 本地解析标题、价格、来源站点、图片、状态、品相文本

## 后续可做增强

1. 为 live capture 增加风控冷却/停止状态记录。
2. 未来若尝试页面上下文 mtop，只允许 search/detail 只读接口，并在代码层白名单限制工具能力。
