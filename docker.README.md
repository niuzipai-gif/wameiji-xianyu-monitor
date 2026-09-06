# Docker 部署

## 快速启动

```bash
# 1) 准备 .env（参考 .env.example）
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY / 通知 webhook 等

# 2) 启动
docker compose up -d --build

# 3) 看日志
docker compose logs -f app

# 4) 访问
# http://127.0.0.1:9890
```

Docker 只运行 Web 服务，不启动市场直采进程。挖煤姬和闲鱼的真实浏览器采集需要在宿主机使用用户授权的 Chrome profile / storage state，再用 `capture-live-html` 或 `scan-live-html` 写入快照和数据库；这样不会把宿主机登录态塞进容器，也不会误把煤炉、Yahoo 等底层平台当成日本侧主采集源。

## 端口

- 9890: web 后端 (cd-monitor web)
- 容器内 `0.0.0.0:9890`，通过 `docker-compose.yaml` 端口映射暴露

## 持久化卷

| 宿主机路径 | 容器内路径 | 用途 |
|---|---|---|
| `./data` | `/app/data` | SQLite 数据库、登录态、截图、快照 |
| `./state` | `/app/state` | 浏览器持久化状态 |
| `./logs` | `/app/logs` | 应用日志 |
| `./jsonl` | `/app/jsonl` | JSONL 检索记录 |
| `./price_history` | `/app/price_history` | 历史价格 |
| `./.env` | `/app/.env` (ro) | 环境变量 |

## 常用命令

```bash
# 重新构建
docker compose build --no-cache

# 进入容器调试
docker compose exec app bash

# 跑一次离线扫描（参考 ai-goofish-monitor 的 scan 入口）
docker compose exec app cd-monitor scan --help

# 看健康检查
docker compose ps
```

宿主机单品实采示例（PowerShell）：

```powershell
.\.venv\Scripts\python.exe -m cd_monitor.cli scan-live-html `
  --db data\local\takeover.db `
  --catalog-no <品番> `
  --profile-dir data\browser_profiles\wameiji `
  --state-file data\xianyu_state.json `
  --snapshot-dir data\snapshots
```

## 与 ai-goofish-monitor 的差异

- 本项目前端是 `web/`（vanilla JS），不需要 Node 构建阶段
- 默认端口 9890（参考是 8080）
- 入口命令是 `cd-monitor web`（本项目的 CLI）而非 `python -m src.app`
