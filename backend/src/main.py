"""
科普 Chatbot 后端 API
FastAPI + LangChain + SQLAlchemy(async) + SQLite

启动方式：
    cd backend && python -m src.main
    或
    cd backend/src && python main.py
"""
import os
import sys

# 将 backend 根目录加入 sys.path，保证两种启动方式下模块导入均可用
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

import asyncio
import json
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, Response
from pydantic import BaseModel

import uvicorn

from config.settings import settings, get_model_config
from database.db import init_db, AsyncSessionLocal, get_db
from services.llm_service import LLMService
from services.session_service import SessionService
from services.auth_service import AuthService
from services.export_service import ExportService
from prompts.presets import get_preset, get_preset_list, DEFAULT_PRESET_ID
from utils.logger import logger, setup_logging


# ========== 日志初始化 ==========
setup_logging(debug=settings.debug)


# ========== 服务实例 ==========
llm_service = LLMService()
session_service = SessionService(llm_service=llm_service)
auth_service = AuthService()
export_service = ExportService()


# ========== 后台任务 ==========
async def _background_generate_title(session_id: str, first_message: str):
    """后台生成标题，不阻塞对话响应"""
    async with AsyncSessionLocal() as db:
        try:
            await session_service.auto_generate_title(db, session_id, first_message)
            await db.commit()
        except Exception as e:
            logger.error(f"后台生成标题失败 | session_id={session_id} | error={e}")


# ========== 生命周期 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("应用启动中...")
    await init_db()
    logger.info("数据库初始化完成")
    yield
    logger.info("应用关闭")


# ========== FastAPI 应用 ==========
app = FastAPI(
    title="科普 Chatbot API",
    description="基于 FastAPI + LangChain 的科普对话后端服务",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS：允许所有源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ========== 请求模型 ==========
class ChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    model_name: Optional[str] = None
    preset_id: Optional[str] = None


class ImageChatRequest(BaseModel):
    session_id: Optional[str] = None
    message: str
    image_data: str
    model_name: Optional[str] = None
    preset_id: Optional[str] = None


class CreateSessionRequest(BaseModel):
    model_name: Optional[str] = None
    preset_id: Optional[str] = None
    title: Optional[str] = None
    user_id: Optional[int] = None


class RenameTitleRequest(BaseModel):
    title: str


class SwitchModelRequest(BaseModel):
    session_id: str
    model_name: str


class WechatLoginRequest(BaseModel):
    code: str
    nickname: Optional[str] = "微信用户"
    avatar_url: Optional[str] = None


class WechatWebCallbackRequest(BaseModel):
    code: str
    state: str


# ========== 辅助函数 ==========
async def _ensure_session(
    db,
    session_id: Optional[str],
    model_name: Optional[str] = None,
    preset_id: Optional[str] = None,
    user_id: Optional[int] = None,
) -> dict:
    """获取或创建会话，返回会话信息"""
    if session_id:
        session = await session_service.get_session(db, session_id)
        if session:
            return session
    # 创建新会话
    return await session_service.create_session(
        db,
        user_id=user_id,
        model_name=model_name,
        preset_id=preset_id,
    )


def _get_system_prompt(preset_id: Optional[str]) -> Optional[str]:
    """根据预设ID获取系统提示词"""
    preset = get_preset(preset_id or DEFAULT_PRESET_ID)
    if preset:
        return preset.get("system_prompt")
    return None


# ========== 健康检查 ==========
@app.get("/api/")
async def health_check():
    return {
        "status": "ok",
        "service": "科普 Chatbot API",
        "version": "1.0.0",
        "default_model": settings.model_name,
    }


# ========== 聊天相关 ==========

@app.post("/api/chat")
async def chat(request: ChatRequest, db=Depends(get_db)):
    """非流式对话"""
    start = time.time()
    logger.info(f"非流式对话 | session_id={request.session_id}")

    session = await _ensure_session(
        db, request.session_id, request.model_name, request.preset_id
    )
    sid = session["session_id"]
    model = request.model_name or session.get("model_name") or settings.model_name

    # 获取历史消息
    history = await session_service.get_messages(db, sid)
    system_prompt = _get_system_prompt(session.get("preset_id"))

    # 调用 LLM
    try:
        response_text = await llm_service.chat(
            history=history,
            message=request.message,
            model_name=model,
            system_prompt=system_prompt,
        )
    except Exception as e:
        logger.error(f"对话失败: {e}")
        raise HTTPException(status_code=500, detail=f"对话失败: {e}")

    # 保存消息
    is_first = session.get("message_count", 0) == 0
    await session_service.add_message(db, sid, "user", request.message)
    await session_service.add_message(db, sid, "assistant", response_text)

    # 首条消息后台生成标题（不阻塞响应）
    if is_first:
        asyncio.create_task(_background_generate_title(sid, request.message))

    duration = (time.time() - start) * 1000
    logger.info(f"非流式对话完成 | session_id={sid} | 耗时={duration:.0f}ms")

    return {
        "session_id": sid,
        "response": response_text,
        "model": model,
    }


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """流式对话（SSE）"""
    logger.info(f"流式对话 | session_id={request.session_id}")

    async def event_generator():
        # 流式响应中使用独立 db 会话
        async with AsyncSessionLocal() as db:
            try:
                session = await _ensure_session(
                    db, request.session_id, request.model_name, request.preset_id
                )
                sid = session["session_id"]
                model = request.model_name or session.get("model_name") or settings.model_name

                history = await session_service.get_messages(db, sid)
                system_prompt = _get_system_prompt(session.get("preset_id"))
                is_first = session.get("message_count", 0) == 0

                # 先发送会话信息
                meta = {"type": "session", "session_id": sid, "model": model}
                yield f"data: {json.dumps(meta, ensure_ascii=False)}\n\n"

                # 保存用户消息
                await session_service.add_message(db, sid, "user", request.message)

                full_response = ""
                try:
                    async for chunk in llm_service.chat_stream(
                        history=history,
                        message=request.message,
                        model_name=model,
                        system_prompt=system_prompt,
                    ):
                        full_response += chunk
                        data = {"type": "chunk", "content": chunk}
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                except Exception as e:
                    logger.error(f"流式对话异常: {e}")
                    err = {"type": "error", "content": str(e)}
                    yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                    await db.rollback()
                    return

                # 保存助手回复
                await session_service.add_message(db, sid, "assistant", full_response)

                # 首条消息后台生成标题（不阻塞 done 信号）
                if is_first:
                    asyncio.create_task(_background_generate_title(sid, request.message))

                await db.commit()

                done = {"type": "done", "session_id": sid, "length": len(full_response)}
                yield f"data: {json.dumps(done, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            except Exception as e:
                logger.error(f"流式对话生成器异常: {e}")
                err = {"type": "error", "content": str(e)}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                await db.rollback()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/image")
async def chat_image(request: ImageChatRequest, db=Depends(get_db)):
    """图片识别对话"""
    logger.info(f"图片对话 | session_id={request.session_id}")

    session = await _ensure_session(
        db, request.session_id, request.model_name, request.preset_id
    )
    sid = session["session_id"]
    model = request.model_name or session.get("model_name") or settings.model_name

    # 检查模型是否支持图片
    if not llm_service.model_supports_image(model):
        # 自动切换到支持图片的模型
        model = "gpt-4o-mini"
        logger.info(f"当前模型不支持图片，切换至 {model}")

    history = await session_service.get_messages(db, sid)
    system_prompt = _get_system_prompt(session.get("preset_id"))

    try:
        response_text = await llm_service.chat(
            history=history,
            message=request.message,
            model_name=model,
            system_prompt=system_prompt,
            image_data=request.image_data,
        )
    except Exception as e:
        logger.error(f"图片对话失败: {e}")
        raise HTTPException(status_code=500, detail=f"图片对话失败: {e}")

    is_first = session.get("message_count", 0) == 0
    await session_service.add_message(db, sid, "user", request.message, request.image_data)
    await session_service.add_message(db, sid, "assistant", response_text)

    if is_first:
        await session_service.auto_generate_title(db, sid, request.message)

    return {
        "session_id": sid,
        "response": response_text,
        "model": model,
    }


@app.websocket("/api/chat/ws")
async def chat_websocket(websocket: WebSocket):
    """WebSocket 对话"""
    await websocket.accept()
    logger.info("WebSocket 连接已建立")

    try:
        while True:
            data = await websocket.receive_json()
            session_id = data.get("session_id")
            message = data.get("message", "")
            model_name = data.get("model_name")
            preset_id = data.get("preset_id")

            async with AsyncSessionLocal() as db:
                try:
                    session = await _ensure_session(db, session_id, model_name, preset_id)
                    sid = session["session_id"]
                    model = model_name or session.get("model_name") or settings.model_name

                    history = await session_service.get_messages(db, sid)
                    system_prompt = _get_system_prompt(session.get("preset_id"))
                    is_first = session.get("message_count", 0) == 0

                    await session_service.add_message(db, sid, "user", message)

                    full_response = ""
                    async for chunk in llm_service.chat_stream(
                        history=history,
                        message=message,
                        model_name=model,
                        system_prompt=system_prompt,
                    ):
                        full_response += chunk
                        await websocket.send_text(json.dumps(
                            {"type": "chunk", "content": chunk}, ensure_ascii=False
                        ))

                    await session_service.add_message(db, sid, "assistant", full_response)

                    # 首条消息后台生成标题
                    if is_first:
                        asyncio.create_task(_background_generate_title(sid, message))

                    await db.commit()

                    await websocket.send_text(json.dumps(
                        {"type": "done", "session_id": sid}, ensure_ascii=False
                    ))
                except Exception as e:
                    logger.error(f"WebSocket 处理异常: {e}")
                    await websocket.send_text(json.dumps(
                        {"type": "error", "content": str(e)}, ensure_ascii=False
                    ))
                    await db.rollback()
    except WebSocketDisconnect:
        logger.info("WebSocket 连接已断开")
    except Exception as e:
        logger.error(f"WebSocket 异常: {e}")


# ========== 会话管理 ==========

@app.post("/api/session")
async def create_session(request: CreateSessionRequest, db=Depends(get_db)):
    """创建会话"""
    session = await session_service.create_session(
        db,
        user_id=request.user_id,
        model_name=request.model_name,
        preset_id=request.preset_id,
        title=request.title,
    )
    return {"session": session}


@app.get("/api/session/{session_id}")
async def get_session(session_id: str, db=Depends(get_db)):
    """获取会话详情（含消息）"""
    session = await session_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": session}


@app.delete("/api/session/{session_id}")
async def delete_session(session_id: str, db=Depends(get_db)):
    """删除会话"""
    success = await session_service.delete_session(db, session_id)
    if not success:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"message": "会话已删除", "session_id": session_id}


@app.get("/api/sessions")
async def list_sessions(
    page: int = 1,
    page_size: int = 20,
    search: Optional[str] = None,
    user_id: Optional[int] = None,
    db=Depends(get_db),
):
    """列出会话（分页、搜索）"""
    result = await session_service.list_sessions(
        db, user_id=user_id, page=page, page_size=page_size, search=search
    )
    return result


@app.put("/api/session/{session_id}/title")
async def rename_session_title(session_id: str, request: RenameTitleRequest, db=Depends(get_db)):
    """重命名会话标题"""
    session = await session_service.rename_title(db, session_id, request.title)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": session}


@app.get("/api/sessions/stats")
async def session_stats(user_id: Optional[int] = None, db=Depends(get_db)):
    """会话统计"""
    stats = await session_service.get_stats(db, user_id=user_id)
    return stats


# ========== 模型管理 ==========

@app.get("/api/models")
async def list_models():
    """获取可用模型列表"""
    return {"models": llm_service.list_models()}


@app.post("/api/models/switch")
async def switch_model(request: SwitchModelRequest, db=Depends(get_db)):
    """切换会话模型"""
    cfg = get_model_config(request.model_name)
    if not cfg:
        raise HTTPException(status_code=400, detail=f"不支持的模型: {request.model_name}")
    session = await session_service.switch_model(db, request.session_id, request.model_name)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"session": session}


# ========== 预设管理 ==========

@app.get("/api/presets")
async def list_presets():
    """获取预设列表"""
    return {"presets": get_preset_list(), "default": DEFAULT_PRESET_ID}


@app.get("/api/presets/{preset_id}")
async def get_preset_detail(preset_id: str):
    """获取预设详情"""
    preset = get_preset(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="预设不存在")
    return {"preset": preset}


# ========== 用户认证 ==========

@app.get("/api/auth/wechat/status")
async def wechat_login_status():
    """获取微信登录配置状态"""
    return {
        "web_login_enabled": auth_service.web_login_enabled(),
        "redirect_uri": settings.wechat_web_redirect_uri,
        "miniapp_configured": bool(settings.wechat_appid and settings.wechat_secret),
    }


@app.get("/api/auth/wechat/qrurl")
async def wechat_qr_login_url():
    """获取微信扫码登录 URL"""
    if not auth_service.web_login_enabled():
        raise HTTPException(
            status_code=400,
            detail="未配置微信开放平台网站应用，请在 .env 中设置 WECHAT_WEB_APPID 和 WECHAT_WEB_SECRET",
        )
    state = uuid.uuid4().hex[:16]
    url = auth_service.get_qr_login_url(state=state)
    return {
        "qr_url": url,
        "state": state,
        "redirect_uri": settings.wechat_web_redirect_uri,
    }


@app.post("/api/auth/wechat/callback")
async def wechat_web_callback(request: WechatWebCallbackRequest, db=Depends(get_db)):
    """微信扫码登录回调（前端拿到 code+state 后调用）"""
    try:
        result = await auth_service.web_login_callback(
            db, code=request.code, state=request.state
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"微信扫码登录失败: {e}")
        raise HTTPException(status_code=500, detail=f"微信扫码登录失败: {e}")


@app.post("/api/auth/wechat/login")
async def wechat_login(request: WechatLoginRequest, db=Depends(get_db)):
    """微信小程序登录（或模拟模式）"""
    try:
        result = await auth_service.wechat_login(
            db, code=request.code, nickname=request.nickname, avatar_url=request.avatar_url
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"登录失败: {e}")
        raise HTTPException(status_code=500, detail=f"登录失败: {e}")


@app.get("/api/auth/user/info")
async def get_user_info(
    request: Request,
    user_id: Optional[int] = None,
    db=Depends(get_db),
):
    """获取用户信息（可通过 query 参数 user_id 或 token 获取）"""
    # 优先用 query 参数
    uid = user_id
    if not uid:
        # 尝试从 token 解析
        authorization = request.headers.get("Authorization")
        token = auth_service.extract_token(authorization)
        payload = auth_service.verify_token(token) if token else None
        if payload:
            uid = payload.get("user_id")
    if not uid:
        raise HTTPException(status_code=401, detail="未提供有效的用户标识")
    user = await auth_service.get_user_info(db, uid)
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return {"user": user}


@app.post("/api/auth/logout")
async def logout(request: Request):
    """登出"""
    authorization = request.headers.get("Authorization")
    token = auth_service.extract_token(authorization)
    if not token:
        raise HTTPException(status_code=400, detail="缺少 token")
    success = auth_service.logout(token)
    return {"success": success, "message": "已登出" if success else "token 不存在"}


# ========== 导出 ==========

@app.get("/api/session/{session_id}/export")
async def export_session(session_id: str, format: str = "markdown", db=Depends(get_db)):
    """导出会话（支持 json/markdown/txt）"""
    session = await session_service.get_session(db, session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = session.get("messages", [])
    content, media_type, ext = export_service.export(session, messages, format)

    # 使用 ASCII 安全的文件名，避免 Content-Disposition 头编码错误
    import urllib.parse
    ascii_filename = f"session_{session_id[:8]}.{ext}"
    utf8_filename = f"{session.get('title', 'session')[:32]}_{session_id[:8]}.{ext}"

    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="{ascii_filename}"; '
                f"filename*=UTF-8''{urllib.parse.quote(utf8_filename)}"
            ),
        },
    )


# ========== 启动 ==========
if __name__ == "__main__":
    logger.info(f"启动服务 | host={settings.host} | port={settings.port}")
    uvicorn.run(
        "main:app" if os.path.basename(os.getcwd()) == "src" else "src.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )
