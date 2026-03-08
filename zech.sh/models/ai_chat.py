from datetime import datetime
from uuid import UUID

from advanced_alchemy.types import GUID, DateTimeUTC
from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class AiChatMessage(Base):
    """A message in the AI chat between Zech and Claude."""

    __tablename__ = "ai_chat_message"
    __table_args__ = (
        Index("ix_ai_chat_message_created_at", "created_at"),
    )

    sender: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(GUID(length=16), nullable=True)
    read_by_claude_at: Mapped[datetime | None] = mapped_column(
        DateTimeUTC(timezone=True), nullable=True
    )
