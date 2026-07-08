"""会话管理服务：CRUD、分页、搜索、统计、导出、消息管理。"""
import json
from typing import List, Optional, Tuple

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Message, Session
from logger import get_logger
from services.llm_service import llm_service

logger = get_logger(__name__)


class SessionService:
    """会话管理服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create_session(
        self,
        user_id: Optional[int] = None,
        title: str = "新对话",
        model_name: str = "deepseek-chat",
        system_prompt: str = "",
    ) -> Session:
        """创建会话（可关联用户）。"""
        session = Session(
            user_id=user_id,
            title=title,
            model_name=model_name,
            system_prompt=system_prompt,
        )
        self.db.add(session)
        await self.db.flush()
        await self.db.refresh(session)
        logger.info("session.create", session_id=session.id, user_id=user_id, title=title)
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话。"""
        result = await self.db.execute(
            select(Session).where(Session.id == session_id)
        )
        return result.scalar_one_or_none()

    async def update_session_title(self, session_id: str, title: str) -> Optional[Session]:
        """更新会话标题。"""
        session = await self.get_session(session_id)
        if not session:
            return None
        session.title = title
        await self.db.flush()
        await self.db.refresh(session)
        logger.info("session.update_title", session_id=session_id, title=title)
        return session

    async def delete_session(self, session_id: str) -> bool:
        """删除会话（级联删除消息）。"""
        session = await self.get_session(session_id)
        if not session:
            return False
        await self.db.delete(session)
        await self.db.flush()
        logger.info("session.delete", session_id=session_id)
        return True

    async def list_sessions(
        self,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Session], int]:
        """列出会话（支持分页）。可按 user_id 过滤。"""
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size

        base_query = select(Session).where(Session.is_active == True)  # noqa: E712
        count_query = select(func.count(Session.id)).where(Session.is_active == True)  # noqa: E712

        if user_id is not None:
            base_query = base_query.where(Session.user_id == user_id)
            count_query = count_query.where(Session.user_id == user_id)

        base_query = base_query.order_by(Session.updated_at.desc()).offset(offset).limit(page_size)

        result = await self.db.execute(base_query)
        sessions = list(result.scalars().all())

        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        return sessions, total

    async def search_sessions(
        self,
        keyword: str,
        user_id: Optional[int] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[Session], int]:
        """按标题和消息内容搜索会话。"""
        page = max(1, page)
        page_size = max(1, min(page_size, 100))
        offset = (page - 1) * page_size
        like = f"%{keyword}%"

        # 按标题匹配的会话 ID
        title_match_subq = select(Session.id).where(Session.title.like(like))
        # 按消息内容匹配的会话 ID
        content_match_subq = select(Message.session_id).where(Message.content.like(like))

        matched_ids_expr = title_match_subq.union(content_match_subq).subquery()
        matched_ids = select(matched_ids_expr.c.id)

        # 计数
        count_query = select(func.count()).select_from(matched_ids_expr)
        if user_id is not None:
            count_query = count_query.where(
                Session.id.in_(matched_ids)
            )

        # 列表查询
        list_query = (
            select(Session)
            .where(Session.id.in_(matched_ids))
            .order_by(Session.updated_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        if user_id is not None:
            list_query = list_query.where(Session.user_id == user_id)
            count_query = (
                select(func.count(Session.id))
                .where(Session.id.in_(matched_ids))
                .where(Session.user_id == user_id)
            )

        result = await self.db.execute(list_query)
        sessions = list(result.scalars().all())

        count_result = await self.db.execute(count_query)
        total = count_result.scalar() or 0

        logger.info("session.search", keyword=keyword, total=total)
        return sessions, total

    async def get_stats(self, user_id: Optional[int] = None) -> dict:
        """统计信息：总会话数、总消息数、各模型使用情况。"""
        session_q = select(func.count(Session.id)).where(Session.is_active == True)  # noqa: E712
        msg_q = select(func.count(Message.id))

        if user_id is not None:
            session_q = session_q.where(Session.user_id == user_id)
            msg_q = msg_q.join(Session, Message.session_id == Session.id).where(
                Session.user_id == user_id
            )

        total_sessions = (await self.db.execute(session_q)).scalar() or 0
        total_messages = (await self.db.execute(msg_q)).scalar() or 0

        # 各模型使用情况
        model_q = (
            select(Session.model_name, func.count(Session.id))
            .where(Session.is_active == True)  # noqa: E712
            .group_by(Session.model_name)
        )
        if user_id is not None:
            model_q = model_q.where(Session.user_id == user_id)
        model_result = await self.db.execute(model_q)
        model_usage = {name: count for name, count in model_result.all()}

        return {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "model_usage": model_usage,
        }

    async def export_session(self, session_id: str, fmt: str = "json") -> Optional[str]:
        """导出会话为 JSON 或 Markdown。"""
        session = await self.get_session(session_id)
        if not session:
            return None
        messages = await self.get_session_messages(session_id)

        if fmt.lower() == "markdown":
            return self._to_markdown(session, messages)
        return self._to_json(session, messages)

    @staticmethod
    def _to_json(session: Session, messages: List[Message]) -> str:
        data = {
            "session": session.to_dict(),
            "messages": [m.to_dict() for m in messages],
        }
        return json.dumps(data, ensure_ascii=False, indent=2)

    @staticmethod
    def _to_markdown(session: Session, messages: List[Message]) -> str:
        lines = [
            f"# {session.title}",
            "",
            f"- **模型**: {session.model_name}",
            f"- **创建时间**: {session.created_at}",
            f"- **会话ID**: {session.id}",
            "",
            "---",
            "",
        ]
        if session.system_prompt:
            lines.extend([f"> **系统提示**: {session.system_prompt}", "", "---", ""])

        role_label = {"user": "🧑 用户", "assistant": "🤖 助手", "system": "⚙️ 系统"}
        for msg in messages:
            label = role_label.get(msg.role, msg.role)
            lines.append(f"### {label}")
            lines.append("")
            lines.append(msg.content)
            lines.append("")
            if msg.image_url:
                lines.append(f"![图片]({msg.image_url})")
                lines.append("")
        return "\n".join(lines)

    async def add_message(
        self,
        session_id: str,
        role: str,
        content: str,
        image_url: Optional[str] = None,
    ) -> Optional[Message]:
        """添加消息。若为用户消息且标题仍为默认，自动生成标题。"""
        session = await self.get_session(session_id)
        if not session:
            return None

        message = Message(
            session_id=session_id,
            role=role,
            content=content,
            image_url=image_url,
        )
        self.db.add(message)
        await self.db.flush()
        await self.db.refresh(message)

        # 第一条用户消息后自动生成标题
        if role == "user" and session.title == "新对话":
            try:
                title = await llm_service.generate_title(content)
                session.title = title
                await self.db.flush()
                logger.info("session.auto_title", session_id=session_id, title=title)
            except Exception:
                logger.warning("session.auto_title.failed", session_id=session_id)

        logger.info("message.add", session_id=session_id, role=role, message_id=message.id)
        return message

    async def get_session_messages(self, session_id: str) -> List[Message]:
        """获取会话全部消息（按 id 升序）。"""
        result = await self.db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.id.asc())
        )
        return list(result.scalars().all())


# 工厂函数，便于在路由层依赖注入
async def get_session_service(db: AsyncSession) -> SessionService:
    return SessionService(db)
