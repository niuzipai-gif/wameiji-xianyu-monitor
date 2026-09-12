# 选品广场首页信息流优先设计

## 目标

用户打开首页时，最先看到的是可筛选的选品广场机会信息流，而不是品牌说明、看板娘、参考样本库或采集控制。

## 已确认的决策

- 首页保留一个紧凑概览：选品广场标题、四项现有 KPI，以及对机会流证据范围的一句说明。
- 机会流的标题、筛选和 `#homeFeed` 紧接概览之后，成为首屏主内容。
- 正样本参考库保留全部现有 DOM id 和渲染逻辑，但移动到信息流之后，并包裹在默认关闭的 `details.secondary-evidence` 中。它是研究依据，不参与机会排序。
- 移除侧栏中仅介绍视觉方向和看板娘的两张说明卡；保留导航、操作按钮和 API 状态。
- 不修改机会排序、候选判定、采集、登录状态、API 路由、Render 同步或 GitHub Pages 的部署结构。

## 兼容性

`web/discovery-ui.js` 通过 `.hero .stats` 定位 KPI 容器。因此紧凑概览继续使用 `.hero` 和 `.stats` 这两个选择器，只调整其内容和样式，避免前端运行时逻辑发生变化。

## 验收

1. `#homeFeed` 在 HTML 中位于 `#referenceMemoryTitle` 之前。
2. 参考正样本库在默认收起的 `details.secondary-evidence` 中，现有渲染 id 均未改变。
3. 首页不再包含 `.mascot-stage`、`Design Direction` 或 `Mascot` 说明卡。
4. 首页相关 Python 静态契约测试和 JavaScript 语法检查通过。
