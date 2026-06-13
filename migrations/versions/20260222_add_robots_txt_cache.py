"""Add robots_txt_cache table for caching parsed robots.txt rules.

Revision ID: 88152c73224b
Revises:
Create Date: 2026-02-22
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from advanced_alchemy.types import GUID, DateTimeUTC


revision: str = "88152c73224b"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "robots_txt_cache",
        sa.Column("id", GUID(length=16), nullable=False),
        sa.Column("created_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("domain", sa.String(255), nullable=False),
        sa.Column("raw_content", sa.Text(), nullable=False, server_default=""),
        sa.Column("rules_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("crawl_delay", sa.Float(), nullable=True),
        sa.Column("ai_blocked", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_check_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("domain", name="uq_robots_txt_cache_domain"),
    )
    op.create_index(
        "ix_robots_txt_cache_domain",
        "robots_txt_cache",
        ["domain"],
    )
    op.create_index(
        "ix_robots_txt_cache_next_check_at",
        "robots_txt_cache",
        ["next_check_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_robots_txt_cache_next_check_at", table_name="robots_txt_cache")
    op.drop_index("ix_robots_txt_cache_domain", table_name="robots_txt_cache")
    op.drop_table("robots_txt_cache")
