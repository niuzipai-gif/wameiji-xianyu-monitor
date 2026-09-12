# Docker 部署

## 快速启动

```bash
# 1) 准备 .env（参考 .env.example）
cp .env.example .env
# 编辑 .env 填入 OPENAI_API_KEY / 通知 webhook 等

# 2) 创建 Docker 专用运行目录（不要复制或挂载 `./data`）
mkdir -p docker-runtime-data/imported-evidence/{snapshots,screenshots,images}

# 3) 启动
docker compose up -d --build

# 4) 看日志
docker compose logs -f app

# 5) 访问
# http://127.0.0.1:9890
```

Docker 只运行 API、比价计算、导出和备份，不启动市场直采进程。它固定使用 `./docker-runtime-data/cd_monitor.db`，容器路径仍为 `/app/data/cd_monitor.db`。`./docker-runtime-data` 是唯一允许绑定到 `/app/data` 的宿主机目录：其中只能放这一个 SQLite 数据库及已筛选的证据资产（`imported-evidence/snapshots`、`screenshots`、`images`）。绝不能复制、绑定或软链接整个 `./data`，也不能放入 `data/xianyu_state.json`、`data/wameiji_state.json`、`data/browser_profiles`、Cookie 或任何 storage state。双边采集仍保持暂停，未来得到明确授权后，宿主机的可见浏览器适配器只能经 API 提交已筛选证据，不能直接写 Docker 正在使用的 SQLite。

## 端口

- 9890: web 后端 (cd-monitor web)
- 容器内 `0.0.0.0:9890`，通过 `docker-compose.yaml` 端口映射暴露

## 持久化卷

| 宿主机路径 | 容器内路径 | 用途 |
|---|---|---|
| `./docker-runtime-data` | `/app/data` | 唯一 SQLite 数据库 `cd_monitor.db` 与已筛选证据资产；不包含 profile、Cookie 或 storage state |
| `./docker-runtime-data/imported-evidence/{snapshots,screenshots,images}` | `/app/data/imported-evidence/{snapshots,screenshots,images}` | 仅用于导入经筛选的快照、截图和商品图片 |

## 常用命令

```bash
# 重新构建
docker compose build --no-cache

# 进入容器调试
docker compose exec app bash

# 看健康检查
docker compose ps
```

当前 Docker 阶段没有开放主机浏览器到 API 的证据导入入口，因此不要把 `scan-live-html` 当作 Docker 的配套命令，也不要通过共享 SQLite 或目录绕过单写者规则。

## 与 ai-goofish-monitor 的差异

- 本项目前端是 `web/`（vanilla JS），不需要 Node 构建阶段
- 默认端口 9890（参考是 8080）
- 入口命令是 `cd-monitor web`（本项目的 CLI）而非 `python -m src.app`
