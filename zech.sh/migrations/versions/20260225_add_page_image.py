"""Add page_image table for associating images with pages.

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-02-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from advanced_alchemy.types import GUID, DateTimeUTC


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "page_image",
        sa.Column("id", GUID(length=16), nullable=False),
        sa.Column("created_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("page_id", GUID(length=16), nullable=False),
        sa.Column("role", sa.String(50), nullable=False, server_default="featured"),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("url", sa.String(1024), nullable=False),
        sa.Column("alt_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_page_image_page_id", "page_image", ["page_id"])
    op.create_index("ix_page_image_page_role", "page_image", ["page_id", "role"])


def downgrade() -> None:
    op.drop_index("ix_page_image_page_role", table_name="page_image")
    op.drop_index("ix_page_image_page_id", table_name="page_image")
    op.drop_table("page_image")
