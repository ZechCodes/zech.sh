"""Add archived column to ai_chat_channel.

Revision ID: d6g2b4c3e901
Revises: c5f1a3b2d890
Create Date: 2026-03-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d6g2b4c3e901"
down_revision: str = "c5f1a3b2d890"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_channel",
        sa.Column("archived", sa.Boolean(), server_default="false", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_channel", "archived")
