"""聊天 API：非流式、SSE 流式、图片识别。"""
import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from database.db import get_db
from database.models import Session, User
from deps import get_current_user
from logger import get_logger
from prompts.presets import get_preset_by_name
from services.llm_service import llm_service
from services.session_service import SessionService

router = APIRouter(prefix="/api", tags=["chat"])
settings = get_settings()
logger = get_logger(__name__)


class ChatRequest(BaseModel):
    """聊天请求。"""

    session_id: Optional[str] = Field(None, description="会话ID，不传则创建新会话")
    message: str = Field(..., min_length=1, description="用户消息")
    model_name: Optional[str] = Field(None, description="模型名称")
    preset_id: Optional[str] = Field(None, description="预设角色ID（前端传递）")
    system_prompt: Optional[str] = Field(None, description="系统提示词")
    temperature: Optional[float] = Field(None, description="温度")
    max_tokens: Optional[int] = Field(None, description="最大token数")


class ChatResponse(BaseModel):
    """聊天响应。"""

    session_id: str
    role: str = "assistant"
    content: str
    model_name: str


@router.post("/chat", response_model=ChatResponse)
async def chat(
    req: ChatRequest,
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ChatResponse:
    """非流式聊天。"""
    session_service = SessionService(db)

    session = await _ensure_session(req, user, session_service)
    model_name = req.model_name or session.model_name
    # 解析 system_prompt：优先 req.system_prompt，其次 preset_id，最后 session.system_prompt
    system_prompt = _resolve_system_prompt(req.system_prompt, req.preset_id) or session.system_prompt

    # 保存用户消息
    await session_service.add_message(session.id, "user", req.message)

    # 取历史消息构造上下文
    messages = await session_service.get_session_messages(session.id)
    history = [m.to_dict() for m in messages]

    try:
        reply = await llm_service.chat(
            messages=history,
            model_name=model_name,
            system_prompt=system_prompt,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
        )
    except Exception as e:
        logger.exception("chat.error", session_id=session.id)
        raise HTTPException(status_code=500, detail=f"LLM 调用失败: {e}")

    # 保存助手回复
    await session_service.add_message(session.id, "assistant", reply)

    return ChatResponse(
        session_id=session.id,
        content=reply,
        model_name=model_name,
    )


@router.post("/chat/stream")
async def chat_stream(
    req: ChatRequest,
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE 流式聊天。"""
    session_service = SessionService(db)

    session = await _ensure_session(req, user, session_service)
    model_name = req.model_name or session.model_name
    # 解析 system_prompt：优先 req.system_prompt，其次 preset_id，最后 session.system_prompt
    system_prompt = _resolve_system_prompt(req.system_prompt, req.preset_id) or session.system_prompt

    # 保存用户消息（先提交，确保历史可见）
    await session_service.add_message(session.id, "user", req.message)

    messages = await session_service.get_session_messages(session.id)
    history = [m.to_dict() for m in messages]

    session_id = session.id

    async def event_generator():
        full_reply_parts: list[str] = []
        try:
            # SSE 头事件：发送会话ID与模型（前端监听 type: 'session'）
            head = {
                "type": "session",
                "session_id": session_id,
                "model_name": model_name,
            }
            yield f"data: {json.dumps(head, ensure_ascii=False)}\n\n"

            # 如果标题被自动生成，发送 title 事件
            if session.title and session.title != "新对话":
                title_event = {
                    "type": "title",
                    "content": session.title,
                    "session_id": session_id,
                }
                yield f"data: {json.dumps(title_event, ensure_ascii=False)}\n\n"

            async for chunk in llm_service.chat_stream(
                messages=history,
                model_name=model_name,
                system_prompt=system_prompt,
                temperature=req.temperature,
                max_tokens=req.max_tokens,
            ):
                full_reply_parts.append(chunk)
                payload = json.dumps({"type": "chunk", "content": chunk}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

            done = {"type": "done", "session_id": session_id}
            yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
        except Exception as e:
            logger.exception("chat.stream.error", session_id=session_id)
            err = {"type": "error", "message": str(e)}
            yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            return
        finally:
            # 持久化完整回复（复用同一 db 连接）
            full_reply = "".join(full_reply_parts).strip()
            if full_reply:
                svc = SessionService(db)
                await svc.add_message(session_id, "assistant", full_reply)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


class ImageChatRequest(BaseModel):
    """图片聊天请求（JSON 格式，前端发送 base64）。"""

    session_id: Optional[str] = Field(None, description="会话ID")
    message: str = Field("请详细描述这张图片的内容。", description="用户消息")
    image_data: str = Field(..., min_length=1, description="Base64 编码的图片数据")
    model_name: Optional[str] = Field(None, description="模型名称")
    preset_id: Optional[str] = Field(None, description="预设角色ID")


@router.post("/chat/image")
async def chat_image_json(
    req: ImageChatRequest,
    user: Optional[User] = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """图片识别（JSON 格式，适配前端 base64 上传）。"""
    import base64

    try:
        image_bytes = base64.b64decode(req.image_data)
    except Exception:
        raise HTTPException(status_code=400, detail="无效的 base64 图片数据")

    if not image_bytes:
        raise HTTPException(status_code=400, detail="图片为空")

    # 限制 10MB
    if len(image_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片不能超过 10MB")

    session_service = SessionService(db)
    session = await _ensure_session(
        ChatRequest(
            session_id=req.session_id,
            message=req.message,
            model_name=req.model_name,
            preset_id=req.preset_id,
        ),
        user,
        session_service,
    )
    target_model = req.model_name or session.model_name
    system_prompt = _resolve_system_prompt(None, req.preset_id) or session.system_prompt

    # 构建 data URL 用于消息回放
    data_url = f"data:image/jpeg;base64,{req.image_data}"
    await session_service.add_message(
        session.id, "user", req.message, image_url=data_url
    )

    try:
        reply = await llm_service.recognize_image(
            image_bytes=image_bytes,
            prompt=req.message,
            model_name=target_model,
            mime_type="image/jpeg",
        )
    except Exception as e:
        logger.exception("chat.image.error", session_id=session.id)
        raise HTTPException(status_code=500, detail=f"图片识别失败: {e}")

    await session_service.add_message(session.id, "assistant", reply)

    return {
        "session_id": session.id,
        "role": "assistant",
        "content": reply,
        "model_name": target_model,
    }


async def _ensure_session(
    req: ChatRequest,
    user: Optional[User],
    session_service: SessionService,
) -> Session:
    """获取或创建会话。"""
    # 解析 preset_id → system_prompt
    system_prompt = _resolve_system_prompt(req.system_prompt, req.preset_id)

    if req.session_id:
        session = await session_service.get_session(req.session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        return session

    model_name = req.model_name or settings.model_name
    return await session_service.create_session(
        user_id=user.id if user else None,
        title="新对话",
        model_name=model_name,
        system_prompt=system_prompt,
    )


def _resolve_system_prompt(explicit_prompt: Optional[str], preset_id: Optional[str]) -> str:
    """解析 system_prompt：优先 explicit_prompt，其次 preset_id 映射。"""
    if explicit_prompt:
        return explicit_prompt
    if preset_id:
        preset = get_preset_by_name(preset_id)
        if preset:
            return preset.system_prompt
    return ""
