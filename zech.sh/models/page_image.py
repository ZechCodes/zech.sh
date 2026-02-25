"""SQLAlchemy model for images associated with pages."""

from uuid import UUID

from advanced_alchemy.types import GUID
from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class PageImage(Base):
    """An image attached to a page.

    Each page can have multiple images in different roles (e.g. "featured",
    "og").  The ``sort_order`` field controls display ordering within a page.
    """

    __tablename__ = "page_image"
    __table_args__ = (
        Index("ix_page_image_page_role", "page_id", "role"),
    )

    page_id: Mapped[UUID] = mapped_column(GUID(length=16), nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        String(50), nullable=False, default="featured", server_default="featured",
    )
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    alt_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
