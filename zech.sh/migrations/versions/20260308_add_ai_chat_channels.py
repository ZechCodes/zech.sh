"""Add ai_chat_channel table and channel_id to messages.

Revision ID: a1b2c3d4e5f6
Revises: 724a59bffa94
Create Date: 2026-03-08
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from advanced_alchemy.types import GUID, DateTimeUTC

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "724a59bffa94"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_channel",
        sa.Column("id", GUID(length=16), nullable=False),
        sa.Column("created_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("created_by_user_id", GUID(length=16), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "ai_chat_message",
        sa.Column("channel_id", GUID(length=16), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_chat_message_channel_id",
        "ai_chat_message",
        "ai_chat_channel",
        ["channel_id"],
        ["id"],
    )
    op.create_index(
        "ix_ai_chat_message_channel_created",
        "ai_chat_message",
        ["channel_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_chat_message_channel_created", table_name="ai_chat_message"
    )
    op.drop_constraint(
        "fk_ai_chat_message_channel_id",
        "ai_chat_message",
        type_="foreignkey",
    )
    op.drop_column("ai_chat_message", "channel_id")
    op.drop_table("ai_chat_channel")
