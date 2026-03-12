"""SQLAlchemy models for persisting research chat sessions and messages."""

from uuid import UUID

from advanced_alchemy.types import GUID
from sqlalchemy import Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class ChatSession(Base):
    """A research chat session owned by a user."""

    __tablename__ = "chat_session"
    __table_args__ = (
        Index("ix_chat_session_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(GUID(length=16), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    mode: Mapped[str] = mapped_column(String(30), default="research", server_default="research", nullable=False)
    last_notification_at: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)


class ChatMessage(Base):
    """A single message (user query or assistant response) within a chat."""

    __tablename__ = "chat_message"
    __table_args__ = (
        Index("ix_chat_message_chat_created", "chat_id", "created_at"),
    )

    chat_id: Mapped[UUID] = mapped_column(GUID(length=16), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    events_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    usage_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    agent_messages_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
