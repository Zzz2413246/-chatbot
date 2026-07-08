"""会话管理 API。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from database.models import Session, User
from deps import get_current_user
from logger import get_logger
from services.session_service import SessionService

router = APIRouter(prefix="/api", tags=["session"])
logger = get_logger(__name__)


class CreateSessionRequest(BaseModel):
    """创建会话请求。"""

    title: str = Field("新对话", description="会话标题")
    model_name: str = Field("deepseek-chat", description="模型名称")
    system_prompt: str = Field("", description="系统提示词")


class UpdateTitleRequest(BaseModel):
    """更新标题请求。"""

    title: str = Field(..., min_length=1, max_length=256, description="新标题")


class SessionResponse(BaseModel):
    id: str
    user_id: Optional[int] = None
    title: str
    model_name: str
    system_prompt: str
    is_active: bool


class SessionListResponse(BaseModel):
    """会话列表响应。"""

    items: list
    total: int
    page: int
    page_size: int


@router.post("/session", response_model=SessionResponse)
async def create_session(
    req: CreateSessionRequest,
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """创建会话。"""
    session_service = SessionService(db)
    session = await session_service.create_session(
        user_id=user.id if user else None,
        title=req.title,
        model_name=req.model_name,
        system_prompt=req.system_prompt,
    )
    return SessionResponse(**_to_response_dict(session))


@router.get("/sessions/stats")
async def get_stats(
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """统计信息。"""
    session_service = SessionService(db)
    user_id = user.id if user else None
    return await session_service.get_stats(user_id=user_id)


@router.get("/session/{session_id}")
async def get_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取会话（含消息列表）。"""
    session_service = SessionService(db)
    session = await session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = await session_service.get_session_messages(session_id)
    return {
        "session": _to_response_dict(session),
        "session_id": session_id,
        "messages": [m.to_dict() for m in messages],
    }


@router.get("/session/{session_id}/messages")
async def get_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """获取会话全部消息。"""
    session_service = SessionService(db)
    session = await session_service.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = await session_service.get_session_messages(session_id)
    return {
        "session_id": session_id,
        "messages": [m.to_dict() for m in messages],
    }


@router.put("/session/{session_id}/title", response_model=SessionResponse)
async def update_session_title(
    session_id: str,
    req: UpdateTitleRequest,
    db: AsyncSession = Depends(get_db),
) -> SessionResponse:
    """更新会话标题。"""
    session_service = SessionService(db)
    session = await session_service.update_session_title(session_id, req.title)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return SessionResponse(**_to_response_dict(session))


@router.delete("/session/{session_id}")
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """删除会话。"""
    session_service = SessionService(db)
    ok = await session_service.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session_id": session_id, "deleted": True}


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    search: Optional[str] = Query(None, description="搜索关键词"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SessionListResponse:
    """列出会话（支持搜索与分页）。"""
    session_service = SessionService(db)
    user_id = user.id if user else None

    if search:
        sessions, total = await session_service.search_sessions(
            keyword=search, user_id=user_id, page=page, page_size=page_size
        )
    else:
        sessions, total = await session_service.list_sessions(
            user_id=user_id, page=page, page_size=page_size
        )

    return SessionListResponse(
        items=[_to_response_dict(s) for s in sessions],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/session/{session_id}/export")
async def export_session(
    session_id: str,
    format: str = Query("json", pattern="^(json|markdown)$", description="导出格式"),
    db: AsyncSession = Depends(get_db),
) -> PlainTextResponse:
    """导出会话为 JSON 或 Markdown。"""
    session_service = SessionService(db)
    content = await session_service.export_session(session_id, fmt=format)
    if content is None:
        raise HTTPException(status_code=404, detail="会话不存在")

    media_type = "application/json" if format == "json" else "text/markdown"
    filename = f"session_{session_id}.{'json' if format == 'json' else 'md'}"
    return PlainTextResponse(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _to_response_dict(session: Session) -> dict:
    return {
        "id": session.id,
        "session_id": session.id,  # 前端兼容字段
        "user_id": session.user_id,
        "title": session.title,
        "model_name": session.model_name,
        "system_prompt": session.system_prompt,
        "is_active": session.is_active,
    }
