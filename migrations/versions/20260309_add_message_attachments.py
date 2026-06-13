"""Add attachments JSON column to ai_chat_message.

Revision ID: b4e9d2f1a789
Revises: a3f8c1d2e456
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b4e9d2f1a789"
down_revision: Union[str, None] = "a3f8c1d2e456"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_message",
        sa.Column("attachments", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_message", "attachments")
