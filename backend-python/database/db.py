"""数据库初始化与会话管理。

使用 SQLAlchemy 异步引擎和异步 session。
"""
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from config import get_settings

settings = get_settings()

# 异步引擎
engine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
)

# 异步 session 工厂
async_session = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    """ORM 基类。"""

    pass


async def init_db() -> None:
    """创建所有表。"""
    # 导入模型以确保它们被注册到 Base.metadata
    from database import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI 依赖：获取数据库 session。"""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
