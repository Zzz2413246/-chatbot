"""认证服务：用户名注册/登录、token 缓存、用户信息。"""
import secrets
import time
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import User
from logger import get_logger

settings = get_settings()
logger = get_logger(__name__)


class AuthService:
    """认证服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._token_cache: dict = getattr(AuthService, "_global_token_cache", {})

    # 全局 token 缓存（生产环境建议用 Redis）
    _global_token_cache: dict = {}

    # ==================== 注册/登录 ====================

    async def simple_register(self, username: str, nickname: Optional[str] = None) -> dict:
        """用户名注册。"""
        openid = f"simple_{username}"
        existing = await self.db.execute(select(User).where(User.openid == openid))
        if existing.scalar_one_or_none():
            raise ValueError("用户名已存在")
        user = User(
            openid=openid,
            nickname=nickname or username,
        )
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        token = self._create_token(user.id)
        logger.info("auth.simple_register.success", user_id=user.id, username=username)
        return {"token": token, "user": user.to_dict()}

    async def simple_login(self, username: str) -> dict:
        """用户名登录。"""
        openid = f"simple_{username}"
        result = await self.db.execute(select(User).where(User.openid == openid))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("用户不存在，请先注册")
        user.last_login = datetime.utcnow()
        await self.db.flush()
        await self.db.refresh(user)
        token = self._create_token(user.id)
        logger.info("auth.simple_login.success", user_id=user.id, username=username)
        return {"token": token, "user": user.to_dict()}

    # ==================== 用户管理 ====================

    def _create_token(self, user_id: int) -> str:
        """创建 token 并缓存（7 天过期）。"""
        token = secrets.token_urlsafe(32)
        entry = {
            "user_id": user_id,
            "created_at": time.time(),
            "expires_at": time.time() + 7 * 24 * 3600,
        }
        self._token_cache[token] = entry
        AuthService._global_token_cache[token] = entry
        return token

    async def get_user_by_token(self, token: str) -> Optional[User]:
        """根据 token 获取用户。"""
        cache = AuthService._global_token_cache.get(token)
        if not cache:
            return None
        if time.time() > cache["expires_at"]:
            AuthService._global_token_cache.pop(token, None)
            return None
        user_id = cache["user_id"]
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def update_user_info(
        self, user_id: int, nickname: Optional[str] = None, avatar_url: Optional[str] = None
    ) -> Optional[User]:
        """更新用户信息。"""
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            return None
        if nickname is not None:
            user.nickname = nickname
        if avatar_url is not None:
            user.avatar_url = avatar_url
        await self.db.flush()
        await self.db.refresh(user)
        return user

    async def delete_user(self, user_id: int) -> None:
        """删除用户及其所有关联数据（会话、消息）。"""
        result = await self.db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
        if not user:
            raise ValueError("用户不存在")
        expired = [
            k for k, v in AuthService._global_token_cache.items()
            if v.get("user_id") == user_id
        ]
        for k in expired:
            AuthService._global_token_cache.pop(k, None)
        await self.db.delete(user)
        await self.db.flush()
        logger.info("auth.user.deleted", user_id=user_id)

    def logout(self, token: str) -> bool:
        """登出：移除 token。"""
        if token in AuthService._global_token_cache:
            AuthService._global_token_cache.pop(token, None)
            logger.info("auth.logout.success")
            return True
        return False
