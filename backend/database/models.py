"""
数据库模型定义
- User: 用户
- Session: 会话
- Message: 消息
- Preset: 自定义预设角色
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Index, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db import Base


def _utc_iso(dt) -> Optional[str]:
    """将 UTC datetime 转为带 Z 后缀的 ISO 字符串，前端 new Date() 可正确转为本地时间"""
    if not dt:
        return None
    return dt.isoformat() + "Z"


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="用户昵称ID，唯一")
    nickname: Mapped[str] = mapped_column(String(128), default="", comment="显示昵称")
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, comment="头像URL")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    last_login: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="最后登录时间")

    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "username": self.username,
            "nickname": self.nickname or self.username,
            "avatar_url": self.avatar_url,
            "created_at": _utc_iso(self.created_at),
            "last_login": _utc_iso(self.last_login),
        }


class Session(Base):
    """会话表"""
    __tablename__ = "sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(64), unique=True, index=True, comment="会话唯一标识")
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True, comment="用户ID"
    )
    title: Mapped[str] = mapped_column(String(128), default="新对话", comment="会话标题")
    model_name: Mapped[str] = mapped_column(String(64), default="deepseek-chat", comment="使用的模型")
    preset_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, comment="预设角色ID")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    last_active: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="最后活跃时间")
    message_count: Mapped[int] = mapped_column(Integer, default=0, comment="消息数量")
    # token 用量累计（兼容旧数据，旧数据默认 0）
    total_prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="会话累计 prompt token 用量")
    total_completion_tokens: Mapped[int] = mapped_column(Integer, default=0, comment="会话累计 completion token 用量")

    user: Mapped[Optional["User"]] = relationship("User", back_populates="sessions")
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="session", cascade="all, delete-orphan",
        order_by="Message.id",
    )

    __table_args__ = (
        Index("idx_sessions_last_active", "last_active"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "title": self.title,
            "model_name": self.model_name,
            "preset_id": self.preset_id,
            "created_at": _utc_iso(self.created_at),
            "last_active": _utc_iso(self.last_active),
            "message_count": self.message_count,
            "total_prompt_tokens": self.total_prompt_tokens or 0,
            "total_completion_tokens": self.total_completion_tokens or 0,
        }


class Message(Base):
    """消息表"""
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("sessions.session_id"), index=True, comment="会话ID"
    )
    role: Mapped[str] = mapped_column(String(32), comment="角色: user/assistant/system")
    content: Mapped[str] = mapped_column(Text, comment="消息内容")
    image_data: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="图片base64数据")
    timestamp: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="时间戳")
    # token 用量（仅 assistant 消息记录；兼容旧数据，nullable）
    prompt_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="输入 prompt token 数")
    completion_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="输出 completion token 数")

    session: Mapped["Session"] = relationship("Session", back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "image_data": self.image_data,
            "timestamp": _utc_iso(self.timestamp),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


class Preset(Base):
    """自定义预设角色表（系统内置预设从 prompts/presets.py 读取，不入库）"""
    __tablename__ = "presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # user_id 为 NULL 表示系统内置（保留字段，当前自定义预设必填）
    user_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True, comment="所属用户ID，NULL=系统内置"
    )
    name: Mapped[str] = mapped_column(String(64), comment="预设名称")
    description: Mapped[str] = mapped_column(String(256), default="", comment="预设描述")
    system_prompt: Mapped[str] = mapped_column(Text, comment="系统提示词")
    icon: Mapped[str] = mapped_column(String(16), default="🤖", comment="图标 emoji")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否系统内置")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, comment="更新时间")

    def to_dict(self) -> dict:
        """转换为 dict；id 用字符串形式以便与内置预设 ID 统一"""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "name": self.name,
            "description": self.description or "",
            "system_prompt": self.system_prompt,
            "icon": self.icon or "🤖",
            "is_builtin": self.is_builtin,
            "created_at": _utc_iso(self.created_at),
            "updated_at": _utc_iso(self.updated_at),
        }
