"""
用户认证服务
- 微信扫码登录（网站应用，OAuth2.0）
- 微信小程序登录（code2session 换 openid）
- token 生成与验证（使用 Base64 编码）
- 未配置微信 AppID 时使用模拟模式
- token 缓存（dict 存储）
"""
import base64
import json
import time
import uuid
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from database.models import User
from utils.logger import logger


class AuthService:
    """用户认证服务"""

    # 微信 code2session 接口（小程序）
    WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

    # 微信开放平台（网站应用）OAuth2.0 接口
    WECHAT_WEB_AUTH_URL = "https://open.weixin.qq.com/connect/qrconnect"
    WECHAT_WEB_TOKEN_URL = "https://api.weixin.qq.com/sns/oauth2/access_token"
    WECHAT_WEB_USERINFO_URL = "https://api.weixin.qq.com/sns/userinfo"

    def __init__(self):
        # token 缓存：{token: {openid, user_id, expires_at}}
        self._token_cache: dict = {}
        # state 缓存：防止 CSRF，{state: created_ts}
        self._state_cache: dict = {}

    # ---------- 微信扫码登录（网站应用）----------
    def web_login_enabled(self) -> bool:
        """是否已配置微信扫码登录"""
        return bool(settings.wechat_web_appid and settings.wechat_web_secret)

    def get_qr_login_url(self, state: Optional[str] = None) -> str:
        """生成微信扫码登录 URL"""
        if not state:
            state = uuid.uuid4().hex[:16]
        # 缓存 state 用于校验（5 分钟有效）
        self._state_cache[state] = time.time()

        params = {
            "appid": settings.wechat_web_appid,
            "redirect_uri": settings.wechat_web_redirect_uri,
            "response_type": "code",
            "scope": "snsapi_login",
            "state": state,
        }
        url = f"{self.WECHAT_WEB_AUTH_URL}?{urlencode(params)}#wechat_redirect"
        logger.info(f"生成微信扫码登录 URL | state={state}")
        return url

    def _validate_state(self, state: str) -> bool:
        """校验 state，防止 CSRF"""
        created = self._state_cache.pop(state, None)
        if not created:
            return False
        # 5 分钟有效
        return (time.time() - created) < 300

    async def web_login_callback(self, db: AsyncSession, code: str, state: str) -> dict:
        """微信扫码登录回调，用 code 换取用户信息并登录"""
        # 校验 state
        if not self._validate_state(state):
            raise ValueError("state 校验失败，可能存在 CSRF 攻击，请重新登录")

        # 第一步：用 code 换 access_token 和 openid
        token_data = await self._web_get_access_token(code)
        access_token = token_data.get("access_token")
        openid = token_data.get("openid")
        if not access_token or not openid:
            err = token_data.get("errmsg", "未知错误")
            raise ValueError(f"微信扫码登录失败: {err}")

        # 第二步：获取用户信息（昵称、头像）
        user_info = await self._web_get_userinfo(access_token, openid)
        nickname = user_info.get("nickname") or "微信用户"
        # headimgurl 是微信用户头像
        avatar_url = user_info.get("headimgurl")

        # 查找或创建用户（openid 加前缀区分来源）
        web_openid = f"web_{openid}"
        user = await self._get_or_create_user(db, web_openid, nickname, avatar_url)
        user.last_login = datetime.utcnow()
        await db.flush()

        token = self._generate_token(user)
        logger.info(f"微信扫码登录成功 | user_id={user.id} | openid={web_openid}")
        return {
            "token": token,
            "user": user.to_dict(),
        }

    async def _web_get_access_token(self, code: str) -> dict:
        """用 code 换取 access_token（网站应用）"""
        params = {
            "appid": settings.wechat_web_appid,
            "secret": settings.wechat_web_secret,
            "code": code,
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.WECHAT_WEB_TOKEN_URL, params=params)
                data = resp.json()
            if "access_token" not in data:
                logger.error(f"网站应用换取 access_token 失败: {data}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"请求微信接口异常: {e}")
            raise ValueError(f"微信登录网络异常: {e}")

    async def _web_get_userinfo(self, access_token: str, openid: str) -> dict:
        """获取用户信息（昵称、头像）"""
        params = {
            "access_token": access_token,
            "openid": openid,
            "lang": "zh_CN",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.WECHAT_WEB_USERINFO_URL, params=params)
                data = resp.json()
            if "openid" not in data:
                logger.error(f"获取微信用户信息失败: {data}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"请求微信用户信息异常: {e}")
            return {}

    # ---------- 微信小程序登录 ----------
    async def wechat_login(
        self,
        db: AsyncSession,
        code: str,
        nickname: str = "微信用户",
        avatar_url: Optional[str] = None,
    ) -> dict:
        """微信小程序登录，返回 token 与用户信息"""
        # 模拟模式：未配置 AppID
        if not settings.wechat_appid or not settings.wechat_secret:
            logger.info("微信登录-模拟模式（未配置 AppID）")
            openid = f"mock_{code[:16]}" if code else f"mock_{uuid.uuid4().hex[:16]}"
        else:
            openid = await self._code2session(code)

        # 查找或创建用户
        user = await self._get_or_create_user(db, openid, nickname, avatar_url)
        # 更新最后登录时间
        user.last_login = datetime.utcnow()
        await db.flush()

        # 生成 token
        token = self._generate_token(user)

        logger.info(f"微信登录成功 | user_id={user.id} | openid={openid}")
        return {
            "token": token,
            "user": user.to_dict(),
        }

    async def _code2session(self, code: str) -> str:
        """调用微信 code2session 接口换取 openid"""
        params = {
            "appid": settings.wechat_appid,
            "secret": settings.wechat_secret,
            "js_code": code,
            "grant_type": "authorization_code",
        }
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(self.WECHAT_CODE2SESSION_URL, params=params)
                data = resp.json()
            if "openid" not in data:
                err = data.get("errmsg", "未知错误")
                logger.error(f"code2session 失败: {data}")
                raise ValueError(f"微信登录失败: {err}")
            return data["openid"]
        except httpx.HTTPError as e:
            logger.error(f"请求微信接口异常: {e}")
            raise ValueError(f"微信登录网络异常: {e}")

    async def _get_or_create_user(
        self,
        db: AsyncSession,
        openid: str,
        nickname: str,
        avatar_url: Optional[str],
    ) -> User:
        """查找或创建用户"""
        stmt = select(User).where(User.openid == openid)
        result = await db.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            # 更新昵称与头像
            if nickname:
                user.nickname = nickname
            if avatar_url:
                user.avatar_url = avatar_url
            return user

        user = User(
            openid=openid,
            nickname=nickname or "微信用户",
            avatar_url=avatar_url,
            created_at=datetime.utcnow(),
            last_login=datetime.utcnow(),
        )
        db.add(user)
        await db.flush()
        logger.info(f"创建新用户 | openid={openid}")
        return user

    # ---------- Token 生成与验证 ----------
    def _generate_token(self, user: User) -> str:
        """生成 Base64 编码的 token"""
        expires_at = int(time.time()) + settings.token_expire_hours * 3600
        payload = {
            "user_id": user.id,
            "openid": user.openid,
            "nickname": user.nickname,
            "expires_at": expires_at,
            "nonce": uuid.uuid4().hex,
        }
        raw = json.dumps(payload, ensure_ascii=False)
        token = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("utf-8")

        # 缓存 token
        self._token_cache[token] = {
            "user_id": user.id,
            "openid": user.openid,
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
            "openid": payload.get("openid"),
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

    def extract_token(self, authorization: Optional[str]) -> Optional[str]:
        """从 Authorization 头提取 token"""
        if not authorization:
            return None
        # 支持 "Bearer <token>" 或直接 token
        if authorization.startswith("Bearer "):
            return authorization[7:].strip()
        return authorization.strip()
