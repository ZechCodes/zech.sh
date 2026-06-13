"""Add ai_chat_device table and device_id to channels.

Revision ID: a3f8e2c1d456
Revises: 9b21192c2125
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from advanced_alchemy.types import GUID, DateTimeUTC

revision: str = "a3f8e2c1d456"
down_revision: Union[str, None] = "9b21192c2125"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ai_chat_device",
        sa.Column("id", GUID(length=16), nullable=False),
        sa.Column("created_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("updated_at", DateTimeUTC(timezone=True), nullable=False),
        sa.Column("sa_orm_sentinel", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("owner_user_id", GUID(length=16), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="offline"),
        sa.Column("last_seen_at", DateTimeUTC(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.add_column(
        "ai_chat_channel",
        sa.Column("device_id", GUID(length=16), nullable=True),
    )
    op.create_foreign_key(
        "fk_ai_chat_channel_device_id",
        "ai_chat_channel",
        "ai_chat_device",
        ["device_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_ai_chat_channel_device_id",
        "ai_chat_channel",
        type_="foreignkey",
    )
    op.drop_column("ai_chat_channel", "device_id")
    op.drop_table("ai_chat_device")
