"""SQLAlchemy model for API key authentication."""

from datetime import datetime
from uuid import UUID

from advanced_alchemy.types import GUID
from sqlalchemy import Boolean, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class ApiKey(Base):
    """An API key for programmatic access, owned by a user."""

    __tablename__ = "api_key"
    __table_args__ = (
        Index("ix_api_key_user_id", "user_id"),
    )

    user_id: Mapped[UUID] = mapped_column(GUID(length=16), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(8), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
