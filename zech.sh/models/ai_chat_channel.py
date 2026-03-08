from uuid import UUID

from advanced_alchemy.types import GUID
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class AiChatChannel(Base):
    """A channel for AI chat, each with its own agent and keypair."""

    __tablename__ = "ai_chat_channel"

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        GUID(length=16), nullable=True
    )
