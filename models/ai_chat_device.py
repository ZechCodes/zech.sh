from datetime import datetime
from uuid import UUID

from advanced_alchemy.types import GUID, DateTimeUTC
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class AiChatDevice(Base):
    """A registered device that can run AI chat workers."""

    __tablename__ = "ai_chat_device"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(GUID(length=16), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="offline", nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTimeUTC(timezone=True), nullable=True
    )
