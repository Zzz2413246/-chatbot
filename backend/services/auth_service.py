"""
用户认证服务
- 昵称ID注册/登录（用户自行设定昵称ID，不可重复）
- token 生成与验证（Base64 编码）
- token 缓存
"""
import base64
import json
import time
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database.models import User, Session as SessionModel, Message
from utils.logger import logger


class AuthService:
    """用户认证服务"""

    def __init__(self):
        # token 缓存：{token: {user_id, username, expires_at}}
        self._token_cache: dict = {}

    # ---------- 注册 ----------
    async def register(
        self,
        db: AsyncSession,
        username: str,
        nickname: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ) -> dict:
        """
        注册新用户，昵称ID不可重复
        - username: 用户自定义昵称ID（唯一）
        - nickname: 显示昵称（可选，默认同 username）
        """
        username = username.strip()
        if not username:
            raise ValueError("昵称ID不能为空")
        if len(username) < 2 or len(username) > 32:
            raise ValueError("昵称ID长度需为 2-32 个字符")

        # 检查是否已存在
        existing = await self._find_user_by_username(db, username)
        if existing:
            raise ValueError(f"昵称ID「{username}」已被占用，请换一个")

        user = User(
            username=username,
            nickname=nickname or username,
            avatar_url=avatar_url,
            created_at=datetime.utcnow(),
            last_login=datetime.utcnow(),
        )
        db.add(user)
        await db.flush()

        token = self._generate_token(user)
        logger.info(f"用户注册成功 | user_id={user.id} | username={username}")
        return {
            "token": token,
            "user": user.to_dict(),
        }

    # ---------- 登录 ----------
    async def login(self, db: AsyncSession, username: str) -> dict:
        """
        用昵称ID登录（无需密码）
        - 若用户存在则直接登录
        - 若不存在则返回提示让用户注册
        """
        username = username.strip()
        if not username:
            raise ValueError("昵称ID不能为空")

        user = await self._find_user_by_username(db, username)
        if not user:
            raise ValueError(f"昵称ID「{username}」不存在，请先注册")

        user.last_login = datetime.utcnow()
        await db.flush()

        token = self._generate_token(user)
        logger.info(f"用户登录成功 | user_id={user.id} | username={username}")
        return {
            "token": token,
            "user": user.to_dict(),
        }

    async def _find_user_by_username(self, db: AsyncSession, username: str) -> Optional[User]:
        """按 username 查找用户"""
        stmt = select(User).where(User.username == username)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    # ---------- Token 生成与验证 ----------
    def _generate_token(self, user: User) -> str:
        """生成 Base64 编码的 token"""
        expires_at = int(time.time()) + settings.token_expire_hours * 3600
        payload = {
            "user_id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "expires_at": expires_at,
            "nonce": uuid.uuid4().hex,
        }
        raw = json.dumps(payload, ensure_ascii=False)
        token = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

        # 缓存 token
        self._token_cache[token] = {
            "user_id": user.id,
            "username": user.username,
            "expires_at": expires_at,
        }
        return token

    def verify_token(self, token: str) -> Optional[dict]:
        """验证 token，返回用户信息或 None"""
        if not token:
            return None
        # 优先查缓存
        cached = self._token_cache.get(token)
        if cached:
            if time.time() > cached["expires_at"]:
                self._token_cache.pop(token, None)
                logger.warning("token 已过期（缓存）")
                return None
            return cached

        # 解析 token
        try:
            raw = base64.urlsafe_b64decode(token.encode("utf-8")).decode("utf-8")
            payload = json.loads(raw)
        except Exception:
            logger.warning("token 解析失败")
            return None

        if time.time() > payload.get("expires_at", 0):
            logger.warning("token 已过期")
            return None

        # 写入缓存
        self._token_cache[token] = {
            "user_id": payload.get("user_id"),
            "username": payload.get("username"),
            "expires_at": payload.get("expires_at"),
        }
        return self._token_cache[token]

    def logout(self, token: str) -> bool:
        """登出，移除 token 缓存"""
        if token in self._token_cache:
            self._token_cache.pop(token, None)
            logger.info("用户登出成功")
            return True
        return False

    # ---------- 获取用户信息 ----------
    async def get_user_info(self, db: AsyncSession, user_id: int) -> Optional[dict]:
        """根据 user_id 获取用户信息"""
        stmt = select(User).where(User.id == user_id)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if not user:
            return None
        return user.to_dict()

    # ---------- 删除用户 ----------
    async def delete_user(self, db: AsyncSession, user_id: int) -> bool:
        """
        删除用户及其所有关联数据
        删除顺序：先删消息 -> 再删会话 -> 最后删用户
        """
        # 1. 查找用户的所有会话 ID
        sess_stmt = select(SessionModel.session_id).where(SessionModel.user_id == user_id)
        sess_result = await db.execute(sess_stmt)
        session_ids = [row[0] for row in sess_result]

        # 2. 删除这些会话下的所有消息
        if session_ids:
            await db.execute(delete(Message).where(Message.session_id.in_(session_ids)))

        # 3. 删除所有会话
        await db.execute(delete(SessionModel).where(SessionModel.user_id == user_id))

        # 4. 删除用户本身
        user_stmt = select(User).where(User.id == user_id)
        user_result = await db.execute(user_stmt)
        user = user_result.scalar_one_or_none()
        if not user:
            return False
        await db.delete(user)

        # 清理该用户的所有 token 缓存
        tokens_to_remove = [
            token for token, info in self._token_cache.items()
            if info.get("user_id") == user_id
        ]
        for token in tokens_to_remove:
            self._token_cache.pop(token, None)

        logger.info(f"删除用户及关联数据 | user_id={user_id} | sessions={len(session_ids)}")
        return True

    def extract_token(self, authorization: Optional[str]) -> Optional[str]:
        """从 Authorization 头提取 token"""
        if not authorization:
            return None
        # 支持 "Bearer <token>" 或直接 token
        if authorization.startswith("Bearer "):
            return authorization[7:].strip()
        return authorization.strip()
