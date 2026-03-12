from uuid import UUID

from advanced_alchemy.types import GUID
from sqlalchemy import Boolean, ForeignKey, String, Text
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
    device_id: Mapped[UUID | None] = mapped_column(
        GUID(length=16),
        ForeignKey("ai_chat_device.id"),
        nullable=True,
    )
    working_directory: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    additional_directories: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )  # JSON-encoded list of directory paths
    archived: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false", nullable=False
    )
    # E2E encryption: channel key encrypted with device master key
    encrypted_channel_key: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    key_nonce: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
