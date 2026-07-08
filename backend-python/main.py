"""FastAPI 应用入口。"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from database.db import init_db
from logger import get_logger, setup_logging
from routers import auth, chat, models, presets, session

setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时初始化数据库。"""
    logger.info("app.startup.begin")
    await init_db()
    logger.info("app.startup.done", message="数据库初始化完成")
    yield
    logger.info("app.shutdown")


app = FastAPI(
    title="ChatBot API",
    description="基于 FastAPI + LangChain + SQLAlchemy 的聊天机器人后端",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS 中间件：允许所有源（前端端口 80）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# 注册路由
app.include_router(chat.router)
app.include_router(session.router)
app.include_router(auth.router)
app.include_router(models.router)
app.include_router(presets.router)


@app.get("/api/")
async def health_check() -> dict:
    """健康检查。"""
    return {
        "status": "ok",
        "service": "chatbot-backend",
        "version": "1.0.0",
    }


@app.get("/")
async def root() -> dict:
    """根路径。"""
    return {
        "service": "chatbot-backend",
        "docs": "/docs",
        "health": "/api/",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info",
    )
