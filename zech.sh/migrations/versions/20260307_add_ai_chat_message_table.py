"""Add ai_chat_message table.

Revision ID: 724a59bffa94
Revises: f6a7b8c9d0e1
Create Date: 2026-03-07
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from advanced_alchemy.types import GUID, DateTimeUTC

revision: str = "724a59bffa94"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_message",
        sa.Column("id", GUID(length=16), nullable=False),
        sa.Column("created_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("sender", sa.String(10), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("user_id", GUID(length=16), nullable=True),
        sa.Column("read_by_claude_at", DateTimeUTC(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_chat_message_created_at",
        "ai_chat_message",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_chat_message_created_at", table_name="ai_chat_message")
    op.drop_table("ai_chat_message")
