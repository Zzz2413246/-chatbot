"""共享依赖：从 Authorization 头解析当前用户。"""
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from database.models import User
from services.auth_service import AuthService


async def get_current_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """从 Authorization 头解析当前用户。

    支持 "Bearer <token>" 格式。未提供或无效时返回 None（不强制鉴权），
    由各路由决定是否要求登录。
    """
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1]
    auth_service = AuthService(db)
    return await auth_service.get_user_by_token(token)


async def get_current_user_required(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """强制要求登录的依赖。"""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或登录已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user
