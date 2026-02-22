"""SQLAlchemy model for caching parsed robots.txt rules per domain."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from skrift.db.base import Base


class RobotsTxtCache(Base):
    """Cached robots.txt rules for a domain.

    Stores both the raw robots.txt content and the parsed rules as JSON.
    Rules are reprocessed after 24 hours.
    """

    __tablename__ = "robots_txt_cache"

    domain: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    raw_content: Mapped[str] = mapped_column(Text, default="", nullable=False)
    rules_json: Mapped[str] = mapped_column(Text, default="{}", nullable=False)
    crawl_delay: Mapped[float | None] = mapped_column(Float, nullable=True)
    ai_blocked: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    next_check_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
