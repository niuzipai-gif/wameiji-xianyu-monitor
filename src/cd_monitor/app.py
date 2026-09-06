"""
WAMEIJI-XIANYU FastAPI 入口

参考 ai-goofish-monitor 的 src/app.py，结构上做对齐：
- lifespan 内 bootstrap SQLite + 重置运行态任务 + 启动 Scheduler
- 注册 router（业务路由在阶段 4 注入）
- 挂 dist/ 静态资源
- 暴露 /health（无需鉴权）和 /auth/status

阶段 1 仅做最小可启动验证：FastAPI 启动 + /health 200。
后续阶段会接入任务、爬虫、AI、通知、前端路由。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from cd_monitor.infrastructure.config.settings import settings, ai_settings, scraper_settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。

    阶段 1 仅打印启动信息 + 准备目录，不依赖未实现的路由。
    阶段 2+ 会在此处执行：bootstrap_sqlite / cleanup_task_logs / scheduler_service.start
    """
    print("[WAMEIJI-XIANYU] 正在启动应用...")
    print(f"[WAMEIJI-XIANYU] 服务端口: {settings.server_port}")
    print(f"[WAMEIJI-XIANYU] 数据库: {os.getenv('CD_DB_PATH', 'data/cd_monitor.db')}")
    print(f"[WAMEIJI-XIANYU] 主 AI: {ai_settings.model_name} @ {ai_settings.base_url}")
    print(f"[WAMEIJI-XIANYU] 主 AI 已配置: {ai_settings.is_configured()}")
    print(f"[WAMEIJI-XIANYU] 无头模式: {scraper_settings.run_headless}")

    # 阶段 1 最小占位：创建运行所需目录
    for d in (settings.image_save_dir, settings.state_dir, settings.log_dir,
              settings.prompts_dir, settings.jsonl_dir, settings.price_history_dir,
              "data", "data/snapshots", "data/screenshots", "data/images",
              "data/state", "dist"):
        Path(d).mkdir(parents=True, exist_ok=True)

    print("[WAMEIJI-XIANYU] 应用启动完成")
    yield
    print("[WAMEIJI-XIANYU] 应用已关闭")


app = FastAPI(
    title="WAMEIJI-XIANYU 采购决策与复核平台",
    description="基于 Playwright 与多模态 AI 的挖煤姬/闲鱼跨境价差监控 + 机会复核 + 告警。",
    version="3.0.0",
    lifespan=lifespan,
)


# ---- 业务路由（阶段 4 注入） -----------------------------------------
# 占位：阶段 4 之前 FastAPI 启动后只暴露 /health 与 /auth/status。
# 阶段 4 会在此处 import 并 include_router：tasks / dashboard / results /
#   accounts / settings / prompts / logs / websocket / notifications

# ---- 静态资源 -------------------------------------------------------
DIST_DIR = Path("dist")

if DIST_DIR.exists():
    assets_dir = DIST_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

if Path("static").exists():
    app.mount("/static", StaticFiles(directory="static"), name="static")


# ---- 基础端点 -------------------------------------------------------
@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "WAMEIJI-XIANYU",
        "version": "3.0.0",
        "ai_configured": ai_settings.is_configured(),
    }


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/status")
async def auth_status(payload: LoginRequest):
    if payload.username == settings.web_username and payload.password == settings.web_password:
        return {"authenticated": True, "username": payload.username}
    raise HTTPException(status_code=401, detail="认证失败")


# ---- 前端入口 -------------------------------------------------------
@app.get("/")
async def read_root():
    index_path = DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return JSONResponse(
        status_code=200,
        content={
            "service": "WAMEIJI-XIANYU",
            "status": "backend_ready_frontend_pending",
            "message": (
                "后端已启动。前端构建产物 dist/index.html 暂未生成；"
                "阶段 7 会接入 Vue 3 + Vite + Tailwind。"
            ),
            "endpoints": ["/health", "/auth/status", "/docs"],
        },
    )


# Catch-all：阶段 7 之前所有非 API 路径都返回上面的 JSON 说明
@app.get("/{full_path:path}")
async def serve_spa(request: Request, full_path: str):
    if full_path.startswith(("api/", "auth/", "ws/", "health", "docs", "redoc",
                             "openapi.json", "assets/", "static/")):
        return JSONResponse(status_code=404, content={"error": "资源未找到"})

    index_path = DIST_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))

    return JSONResponse(
        status_code=200,
        content={
            "service": "WAMEIJI-XIANYU",
            "status": "backend_ready_frontend_pending",
            "path": full_path,
            "hint": "阶段 7 之前请使用 /health 与 /auth/status 验证后端。",
        },
    )


def main() -> None:
    """`python -m cd_monitor.app` 入口。"""
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=settings.server_port)


if __name__ == "__main__":
    main()
