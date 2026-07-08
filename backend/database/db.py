"""
数据库连接与初始化
- 使用 SQLite + SQLAlchemy 异步引擎 (aiosqlite)
- 数据库文件存储在 backend/data/chatbot.db
- 自动创建数据表
"""
import os
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


# 后端根目录（backend/）
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 数据目录
DATA_DIR = os.path.join(BACKEND_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# 数据库文件绝对路径，避免受工作目录影响
DB_PATH = os.path.join(DATA_DIR, "chatbot.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类"""
    pass


# 异步引擎
engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    future=True,
)

# 异步会话工厂
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """初始化数据库：创建所有表"""
    # 导入模型以注册到 Base.metadata
    from database import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖项：获取异步数据库会话"""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
