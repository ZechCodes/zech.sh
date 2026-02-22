"""SQLAlchemy models for persisting research chat sessions and messages."""

from uuid import UUID

from advanced_alchemy.types import GUID
from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class ChatSession(Base):
    """A research chat session owned by a user."""

    __tablename__ = "chat_session"
    __table_args__ = (
        Index("ix_chat_session_user_created", "user_id", "created_at"),
    )

    user_id: Mapped[UUID] = mapped_column(
        GUID(length=16), ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(500), nullable=False)


class ChatMessage(Base):
    """A single message (user query or assistant response) within a chat."""

    __tablename__ = "chat_message"
    __table_args__ = (
        Index("ix_chat_message_chat_created", "chat_id", "created_at"),
    )

    chat_id: Mapped[UUID] = mapped_column(
        GUID(length=16), ForeignKey("chat_session.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)  # "user" | "assistant"
    content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    events_json: Mapped[str] = mapped_column(Text, default="[]", nullable=False)
    usage_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
