"""
数据库模型定义
- User: 用户
- Session: 会话
- Message: 消息
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.db import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    openid: Mapped[str] = mapped_column(String(128), unique=True, index=True, comment="微信openid")
    nickname: Mapped[str] = mapped_column(String(128), default="微信用户", comment="用户昵称")
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True, comment="头像URL")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="创建时间")
    last_login: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, comment="最后登录时间")

    sessions: Mapped[list["Session"]] = relationship(
        "Session", back_populates="user", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "openid": self.openid,
            "nickname": self.nickname,
            "avatar_url": self.avatar_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login": self.last_login.isoformat() if self.last_login else None,
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
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_active": self.last_active.isoformat() if self.last_active else None,
            "message_count": self.message_count,
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

    session: Mapped["Session"] = relationship("Session", back_populates="messages")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "session_id": self.session_id,
            "role": self.role,
            "content": self.content,
            "image_data": self.image_data,
            "timestamp": self.timestamp.isoformat() if self.timestamp else None,
        }
