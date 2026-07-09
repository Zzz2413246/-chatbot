"""认证 API：用户名注册/登录、用户信息、登出。"""
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


class SimpleRegisterRequest(BaseModel):
    """注册请求。"""

    username: str = Field(..., min_length=2, max_length=32, description="用户名")
    nickname: Optional[str] = Field(None, description="昵称")


class SimpleLoginRequest(BaseModel):
    """登录请求。"""

    username: str = Field(..., min_length=2, max_length=32, description="用户名")


class UpdateUserRequest(BaseModel):
    """更新用户信息请求。"""

    nickname: Optional[str] = None
    avatar_url: Optional[str] = None


# ==================== 注册/登录 ====================


@router.post("/register")
async def register(
    req: SimpleRegisterRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """用户名注册。"""
    auth_service = AuthService(db)
    try:
        result = await auth_service.simple_register(req.username, req.nickname)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": result["token"], "user": result["user"]}


@router.post("/login")
async def login(
    req: SimpleLoginRequest,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """用户名登录。"""
    auth_service = AuthService(db)
    try:
        result = await auth_service.simple_login(req.username)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"token": result["token"], "user": result["user"]}


# ==================== 用户信息 ====================


@router.get("/user/info")
async def get_user_info(user: Optional[User] = Depends(get_current_user)) -> dict:
    """获取用户信息。"""
    if not user:
        raise HTTPException(status_code=401, detail="未登录或登录已过期")
    return {"user": user.to_dict()}


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
