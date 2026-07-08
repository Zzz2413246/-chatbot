"""认证服务：微信开放平台扫码登录、模拟模式、token 缓存、用户信息。"""
import secrets
import time
import uuid
from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.models import User
from logger import get_logger

settings = get_settings()
logger = get_logger(__name__)

# 微信开放平台 API 地址
WECHAT_OAUTH_BASE = "https://open.weixin.qq.com/connect/qrconnect"
WECHAT_API_ACCESS_TOKEN = "https://api.weixin.qq.com/sns/oauth2/access_token"
WECHAT_API_USERINFO = "https://api.weixin.qq.com/sns/userinfo"


class AuthService:
    """认证服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self._token_cache: dict = getattr(AuthService, "_global_token_cache", {})

    # 全局 token 缓存（生产环境建议用 Redis）
    _global_token_cache: dict = {}
    # State 缓存（CSRF 防护，5 分钟过期）
    _state_cache: dict = {}

    # ==================== 微信开放平台扫码登录 ====================

    @classmethod
    def is_web_login_enabled(cls) -> bool:
        """是否已配置微信开放平台登录。"""
        return bool(settings.wechat_app_id and settings.wechat_app_secret)

    @classmethod
    def generate_qr_url(cls, redirect_uri: Optional[str] = None) -> dict:
        """生成微信扫码登录授权 URL。"""
        app_id = settings.wechat_app_id
        redirect = redirect_uri or settings.wechat_redirect_uri
        state = uuid.uuid4().hex

        # 缓存 state，5 分钟后过期
        cls._state_cache[state] = time.time() + 5 * 60
        cls._cleanup_states()

        params = {
            "appid": app_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "scope": "snsapi_login",
            "state": state,
        }
        qr_url = f"{WECHAT_OAUTH_BASE}?{urlencode(params)}#wechat_redirect"

        logger.info("auth.qr_url.generated", state=state)
        return {"qr_url": qr_url, "state": state}

    async def handle_callback(self, code: str, state: Optional[str] = None) -> dict:
        """处理微信开放平台回调（code + state 换 token）。"""
        # 验证 state（CSRF 防护）
        if state:
            if state not in self._state_cache:
                logger.warning("auth.callback.invalid_state", state=state)
                raise ValueError("无效的 state 参数，可能已过期")
            del self._state_cache[state]

        # 用 code 换 access_token + openid
        token_data = await self._request_access_token(code)
        access_token = token_data["access_token"]
        openid = token_data["openid"]

        # 查找或创建用户
        user = await self._get_or_create_user(openid)

        # 获取微信用户信息
        await self._fetch_user_info(access_token, openid, user)

        user.last_login = datetime.utcnow()
        await self.db.flush()
        await self.db.refresh(user)

        token = self._create_token(user.id)
        logger.info("auth.callback.success", user_id=user.id, openid=openid)
        return {"token": token, "user": user.to_dict()}

    async def _request_access_token(self, code: str) -> dict:
        """调用微信开放平台 API：code → access_token + openid。"""
        url = WECHAT_API_ACCESS_TOKEN
        params = {
            "appid": settings.wechat_app_id,
            "secret": settings.wechat_app_secret,
            "code": code,
            "grant_type": "authorization_code",
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

        if "access_token" not in data:
            errcode = data.get("errcode", "unknown")
            errmsg = data.get("errmsg", "unknown error")
            logger.error("auth.wechat.token_error", errcode=errcode, errmsg=errmsg)
            raise ValueError(f"微信登录失败: {errmsg} (errcode={errcode})")

        logger.info("auth.wechat.token_ok", has_openid=bool(data.get("openid")))
        return data

    async def _fetch_user_info(self, access_token: str, openid: str, user: User) -> None:
        """获取微信用户信息（昵称、头像）。"""
        try:
            url = WECHAT_API_USERINFO
            params = {"access_token": access_token, "openid": openid, "lang": "zh_CN"}
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()

            if "errcode" in data and data["errcode"] != 0:
                logger.warning("auth.wechat.userinfo_error", errcode=data.get("errcode"))
                return

            nickname = data.get("nickname")
            headimgurl = data.get("headimgurl")
            if nickname:
                user.nickname = nickname
            if headimgurl:
                user.avatar_url = headimgurl
            logger.info("auth.wechat.userinfo_ok", nickname=nickname)

        except Exception as e:
            logger.warning("auth.wechat.userinfo_failed", error=str(e))

    # ==================== 通用登录 ====================

    async def wechat_login(self, code: str) -> dict:
        """微信登录（兼容旧 code 登录和模拟模式）。"""
        if not settings.is_wechat_configured:
            logger.info("auth.login.mock_mode")
            openid = f"mock_{code[:16]}_{uuid.uuid4().hex[:8]}"
            user = await self._get_or_create_user(openid)
            user.last_login = datetime.utcnow()
            await self.db.flush()
            await self.db.refresh(user)
            token = self._create_token(user.id)
            logger.info("auth.login.mock.success", user_id=user.id)
            return {"token": token, "user": user.to_dict()}

        # 用开放平台 API 登录
        token_data = await self._request_access_token(code)
        openid = token_data["openid"]
        access_token = token_data["access_token"]

        user = await self._get_or_create_user(openid)
        await self._fetch_user_info(access_token, openid, user)
        user.last_login = datetime.utcnow()
        await self.db.flush()
        await self.db.refresh(user)

        token = self._create_token(user.id)
        logger.info("auth.login.success", user_id=user.id)
        return {"token": token, "user": user.to_dict()}

    # ==================== 用户管理 ====================

    async def _get_or_create_user(self, openid: str) -> User:
        """根据 openid 查找或创建用户。"""
        result = await self.db.execute(select(User).where(User.openid == openid))
        user = result.scalar_one_or_none()
        if user:
            return user
        user = User(openid=openid, nickname=f"用户_{openid[-6:]}")
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        logger.info("auth.user.created", user_id=user.id)
        return user

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

    def logout(self, token: str) -> bool:
        """登出：移除 token。"""
        if token in AuthService._global_token_cache:
            AuthService._global_token_cache.pop(token, None)
            logger.info("auth.logout.success")
            return True
        return False

    @classmethod
    def _cleanup_states(cls) -> None:
        """清理过期的 state 缓存。"""
        now = time.time()
        expired = [k for k, v in cls._state_cache.items() if v < now]
        for k in expired:
            del cls._state_cache[k]
