"""认证 API：微信扫码登录、用户信息、登出。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.db import get_db
from database.models import User
from deps import get_current_user
from logger import get_logger
from services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])
settings = get_settings()
logger = get_logger(__name__)


class WechatLoginRequest(BaseModel):
    """微信登录请求。"""

    code: str = Field(..., min_length=1, description="微信登录凭证 code")


class WechatCallbackRequest(BaseModel):
    """微信开放平台回调请求。"""

    code: str = Field(..., min_length=1, description="微信授权 code")
    state: Optional[str] = Field(None, description="CSRF state 参数")


class WechatLoginResponse(BaseModel):
    """微信登录响应。"""

    token: str
    user: dict


class UpdateUserRequest(BaseModel):
    """更新用户信息请求。"""

    nickname: Optional[str] = None
    avatar_url: Optional[str] = None


# ==================== 微信登录状态 ====================


@router.get("/wechat/status")
async def wechat_status() -> dict:
    """检查微信开放平台登录是否可用。"""
    return {"web_login_enabled": AuthService.is_web_login_enabled()}


# ==================== 微信扫码登录 ====================


@router.get("/wechat/qrurl")
async def get_qr_url() -> dict:
    """获取微信扫码登录的授权 URL。"""
    if not AuthService.is_web_login_enabled():
        raise HTTPException(
            status_code=400,
            detail="微信登录未配置（缺少 WECHAT_APP_ID / WECHAT_APP_SECRET）",
        )
    return AuthService.generate_qr_url()


@router.post("/wechat/callback")
async def wechat_callback(
    req: WechatCallbackRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """微信开放平台回调接口。前端 callback.html 将微信重定向传来的 code + state 发给此接口。"""
    auth_service = AuthService(db)
    try:
        result = await auth_service.handle_callback(req.code, req.state)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("auth.callback.error")
        raise HTTPException(status_code=500, detail=f"登录失败: {e}")

    return {"token": result["token"], "user": result["user"]}


# ==================== 通用微信登录 ====================


@router.post("/wechat/login", response_model=WechatLoginResponse)
async def wechat_login(
    req: WechatLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> WechatLoginResponse:
    """微信登录（兼容 code 登录和模拟模式）。"""
    auth_service = AuthService(db)
    try:
        result = await auth_service.wechat_login(req.code)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("auth.login.error")
        raise HTTPException(status_code=500, detail=f"登录失败: {e}")

    return WechatLoginResponse(token=result["token"], user=result["user"])


# ==================== 用户信息 ====================


@router.get("/user/info")
async def get_user_info(user: Optional[User] = Depends(get_current_user)) -> dict:
    """获取用户信息。"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return {
        "user": user.to_dict(),
        "mock_mode": not settings.is_wechat_configured,
    }


@router.put("/user/info")
async def update_user_info(
    req: UpdateUserRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """更新用户信息。"""
    auth_service = AuthService(db)
    updated = await auth_service.update_user_info(
        user.id, nickname=req.nickname, avatar_url=req.avatar_url
    )
    if not updated:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user": updated.to_dict()}


# ==================== 登出 ====================


@router.post("/logout")
async def logout(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """登出。"""
    if not authorization:
        return {"success": True, "message": "无 token，无需登出"}
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return {"success": True, "message": "无效 token"}
    auth_service = AuthService(db)
    auth_service.logout(parts[1])
    return {"success": True, "message": "已登出"}
