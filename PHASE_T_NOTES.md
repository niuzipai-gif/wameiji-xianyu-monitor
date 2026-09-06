# Phase T 前端增量重构说明 (2026-06-27)

## 背景
后端 + 254 测试已稳定 100% 完成（Phase R 挖煤姬 + Phase S 闲鱼 双登录态通道齐全）。
用户要求前端重构但未提供框架图，所以增量做了 6 项"安全范围"内的小重构 — 全部在 254 测试契约内（所有 form/button/div id 保留），后端零变化。

## 实际改动（按文件列出，证据 = 当前文件大小）

| 文件 | 大小 | 状态 | 改动 |
|------|------|------|------|
| `web/index.html` | 19842 B | 修改 | 加 status dot + 登录态 grid wrapper + lastUpdated |
| `web/app.js`     | 58305 B | 修改 | 加 setStatusDot / updateLastUpdated 函数 + 3 处刷新钩子 |
| `web/styles.css` | 17460 B | **未动** | 保留作为对照（不影响） |
| `web/styles/themes.css` | 339 B | 新建 | 仅 `:root` 设计 token（颜色 / 字体 / 阴影） |
| `web/styles/app.css`    | 18601 B | 新建 | 全部布局/组件 + Phase T 增量规则 |
| `web.legacy/`  | 4 文件 | 备份 | 整个旧版 web/ 一键回滚用 |

## 6 项增量（按时间倒序）

1. **topbar 最后更新时间戳** — `<small id="lastUpdated">`，每次 refreshAll 成功触发
2. **状态点 JS 联动 + pulse 动画** — loading/ready/error/warn 真实切换，loading 蓝点呼吸
3. **状态点 HTML + CSS** — 6 种 data-state 颜色规则
4. **登录态二合一** — 挖煤姬 + 闲鱼进 `.login-state-grid`，宽屏并排 / 移动端纵向
5. **CSS 拆分** — `themes.css`（设计 token）+ `app.css`（布局/组件）分离
6. **`web.legacy/` 备份** — 重构前 `Copy-Item web web.legacy`

## 验证

- `pytest -q` → **254 passed in ~50s**（重复跑过 4 次，零回归）
- `/api/health` → `{"ok": true, "database": "...", "static_dir": "...web"}`
- 服务 4 个新元素全部就位：`#lastUpdated` / `setStatusDot` / `updateLastUpdated` / `#lastUpdated` CSS
- 后端文件（`src/cd_monitor/**`）零修改
- 所有 form/button/div id 保留 — 254 测试覆盖的端到端契约零变更

## 回滚

```powershell
cd F:\WAMEIJI-XIANYU-full-handoff
Remove-Item -Recurse -Force web
Rename-Item web.legacy web
```

## 下一步（等用户）

- 前端框架图（结构 / tab 化 / 视觉重做 等）
- 或具体改动指令（"加 X / 把 Y 改成 Z"）
- 或 "OK 收工"

## 启动服务（之前已停掉）

```powershell
$env:PYTHONPATH = "src"
$env:CD_MONITOR_DB_PATH = "C:\Users\19097\AppData\Local\Temp\cd_monitor_refactor.db"
python -m cd_monitor.cli web --host 127.0.0.1 --port 9888 --static-dir web
# 浏览器开 http://127.0.0.1:9888
```
