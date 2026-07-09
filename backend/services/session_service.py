"""
会话管理服务
- 基于数据库持久化
- 创建会话（可关联用户、选择模型、选择预设）
- 标题自动生成（用 LLM 根据第一条消息生成 3-5 字标题）
- 标题重命名
- 会话列表（按最后活跃时间排序）
- 会话搜索（按标题和消息内容搜索）
- 会话统计（总会话数、总消息数、各模型使用情况）
- 删除会话
"""
import uuid
from datetime import datetime
from typing import List, Dict, Optional, Any

from sqlalchemy import select, func, delete, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database.models import Session as SessionModel, Message
from config.settings import settings
from prompts.presets import get_preset, DEFAULT_PRESET_ID
from utils.logger import logger


class SessionService:
    """会话管理服务"""

    def __init__(self, llm_service=None):
        # 延迟引用 LLMService 以避免循环依赖
        self._llm_service = llm_service

    @property
    def llm_service(self):
        if self._llm_service is None:
            from services.llm_service import LLMService
            self._llm_service = LLMService()
        return self._llm_service

    # ---------- 创建会话 ----------
    async def create_session(
        self,
        db: AsyncSession,
        user_id: Optional[int] = None,
        model_name: Optional[str] = None,
        preset_id: Optional[str] = None,
        title: Optional[str] = None,
    ) -> dict:
        """创建新会话"""
        session_id = str(uuid.uuid4()).replace("-", "")[:24]
        model = model_name or settings.model_name
        preset = preset_id or DEFAULT_PRESET_ID

        # 校验内置预设；若是自定义预设（数字 ID），保留原值由 preset_service 解析
        if preset != DEFAULT_PRESET_ID and get_preset(preset) is None:
            # 非内置预设：可能是数据库中的自定义预设，保留原值
            # （若为无效 ID，后续获取 system_prompt 时会回退到默认）
            pass

        session = SessionModel(
            session_id=session_id,
            user_id=user_id,
            title=title or "新对话",
            model_name=model,
            preset_id=preset,
            created_at=datetime.utcnow(),
            last_active=datetime.utcnow(),
            message_count=0,
            total_prompt_tokens=0,
            total_completion_tokens=0,
        )
        db.add(session)
        await db.flush()
        logger.info(f"创建会话 | session_id={session_id} | model={model} | preset={preset}")
        return session.to_dict()

    # ---------- 获取会话详情 ----------
    async def get_session(self, db: AsyncSession, session_id: str) -> Optional[dict]:
        """获取会话详情，包含消息列表"""
        stmt = (
            select(SessionModel)
            .options(selectinload(SessionModel.messages))
            .where(SessionModel.session_id == session_id)
        )
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            return None
        data = session.to_dict()
        data["messages"] = [m.to_dict() for m in session.messages]
        return data

    async def get_session_model(self, db: AsyncSession, session_id: str) -> Optional[SessionModel]:
        """获取会话 ORM 对象"""
        stmt = select(SessionModel).where(SessionModel.session_id == session_id)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    # ---------- 删除会话 ----------
    async def delete_session(self, db: AsyncSession, session_id: str) -> bool:
        """删除会话（含消息）"""
        session = await self.get_session_model(db, session_id)
        if not session:
            return False
        # 先删消息
        await db.execute(delete(Message).where(Message.session_id == session_id))
        await db.delete(session)
        logger.info(f"删除会话 | session_id={session_id}")
        return True

    # ---------- 会话列表 ----------
    async def list_sessions(
        self,
        db: AsyncSession,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
        search: Optional[str] = None,
    ) -> dict:
        """列出会话（按最后活跃时间倒序，支持分页与搜索）"""
        conditions = []
        if user_id is not None:
            conditions.append(SessionModel.user_id == user_id)

        if search:
            keyword = f"%{search}%"
            # 按标题搜索；消息内容搜索通过子查询
            msg_subq = select(Message.session_id).where(Message.content.like(keyword)).distinct()
            conditions.append(
                or_(
                    SessionModel.title.like(keyword),
                    SessionModel.session_id.in_(msg_subq),
                )
            )

        # 总数
        count_stmt = select(func.count(SessionModel.id))
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = (await db.execute(count_stmt)).scalar() or 0

        # 分页查询
        stmt = select(SessionModel).order_by(desc(SessionModel.last_active))
        if conditions:
            stmt = stmt.where(*conditions)
        offset = (page - 1) * page_size
        stmt = stmt.offset(offset).limit(page_size)

        result = await db.execute(stmt)
        sessions = result.scalars().all()

        return {
            "sessions": [s.to_dict() for s in sessions],
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    # ---------- 重命名标题 ----------
    async def rename_title(self, db: AsyncSession, session_id: str, title: str) -> Optional[dict]:
        """重命名会话标题"""
        session = await self.get_session_model(db, session_id)
        if not session:
            return None
        session.title = title[:64]
        await db.flush()
        logger.info(f"重命名会话 | session_id={session_id} | title={title}")
        return session.to_dict()

    # ---------- 切换模型 ----------
    async def switch_model(self, db: AsyncSession, session_id: str, model_name: str) -> Optional[dict]:
        """切换会话使用的模型"""
        session = await self.get_session_model(db, session_id)
        if not session:
            return None
        session.model_name = model_name
        await db.flush()
        logger.info(f"切换模型 | session_id={session_id} | model={model_name}")
        return session.to_dict()

    # ---------- 添加消息 ----------
    async def add_message(
        self,
        db: AsyncSession,
        session_id: str,
        role: str,
        content: str,
        image_data: Optional[str] = None,
        prompt_tokens: Optional[int] = None,
        completion_tokens: Optional[int] = None,
    ) -> Optional[dict]:
        """向会话添加消息，并更新会话统计（含 token 用量）"""
        session = await self.get_session_model(db, session_id)
        if not session:
            return None

        message = Message(
            session_id=session_id,
            role=role,
            content=content,
            image_data=image_data,
            timestamp=datetime.utcnow(),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        db.add(message)
        session.message_count = (session.message_count or 0) + 1
        session.last_active = datetime.utcnow()
        # 累计 token 用量（仅 assistant 消息通常带 token，但此处统一累加非空值）
        if prompt_tokens:
            session.total_prompt_tokens = (session.total_prompt_tokens or 0) + int(prompt_tokens)
        if completion_tokens:
            session.total_completion_tokens = (session.total_completion_tokens or 0) + int(completion_tokens)
        await db.flush()
        return message.to_dict()

    # ---------- 获取消息历史 ----------
    async def get_messages(self, db: AsyncSession, session_id: str) -> List[dict]:
        """获取会话的消息历史"""
        stmt = (
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.id)
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()
        return [m.to_dict() for m in messages]

    # ---------- 自动生成标题 ----------
    async def auto_generate_title(self, db: AsyncSession, session_id: str, first_message: str) -> Optional[str]:
        """根据第一条消息自动生成标题"""
        session = await self.get_session_model(db, session_id)
        if not session:
            return None
        title = await self.llm_service.generate_title(first_message, session.model_name)
        session.title = title[:64]
        await db.flush()
        logger.info(f"自动生成标题 | session_id={session_id} | title={title}")
        return title

    # ---------- 会话统计 ----------
    async def get_stats(self, db: AsyncSession, user_id: Optional[int] = None) -> dict:
        """获取会话统计信息（含 token 用量）"""
        base_filter = []
        if user_id is not None:
            base_filter.append(SessionModel.user_id == user_id)

        # 总会话数
        total_stmt = select(func.count(SessionModel.id))
        if base_filter:
            total_stmt = total_stmt.where(*base_filter)
        total_sessions = (await db.execute(total_stmt)).scalar() or 0

        # 总消息数
        msg_total_stmt = select(func.count(Message.id)).join(
            SessionModel, Message.session_id == SessionModel.session_id
        )
        if base_filter:
            msg_total_stmt = msg_total_stmt.where(*base_filter)
        total_messages = (await db.execute(msg_total_stmt)).scalar() or 0

        # 总 token 用量（从 session 表累计）
        token_stmt = select(
            func.coalesce(func.sum(SessionModel.total_prompt_tokens), 0).label("total_prompt_tokens"),
            func.coalesce(func.sum(SessionModel.total_completion_tokens), 0).label("total_completion_tokens"),
        )
        if base_filter:
            token_stmt = token_stmt.where(*base_filter)
        token_row = (await db.execute(token_stmt)).one()
        total_prompt_tokens = int(token_row.total_prompt_tokens or 0)
        total_completion_tokens = int(token_row.total_completion_tokens or 0)

        # 各模型使用情况（含 token 统计）
        model_stmt = (
            select(
                SessionModel.model_name,
                func.count(SessionModel.id).label("session_count"),
                func.coalesce(func.sum(SessionModel.message_count), 0).label("message_count"),
                func.coalesce(func.sum(SessionModel.total_prompt_tokens), 0).label("prompt_tokens"),
                func.coalesce(func.sum(SessionModel.total_completion_tokens), 0).label("completion_tokens"),
            )
            .group_by(SessionModel.model_name)
        )
        if base_filter:
            model_stmt = model_stmt.where(*base_filter)
        model_result = await db.execute(model_stmt)
        model_usage = [
            {
                "model_name": row.model_name,
                "session_count": row.session_count,
                "message_count": row.message_count,
                "prompt_tokens": int(row.prompt_tokens or 0),
                "completion_tokens": int(row.completion_tokens or 0),
            }
            for row in model_result
        ]

        return {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "model_usage": model_usage,
        }
