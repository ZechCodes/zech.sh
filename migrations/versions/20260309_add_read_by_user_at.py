"""Add read_by_user_at column to ai_chat_message.

Revision ID: a3f8c1d2e456
Revises: 9b21192c2125
Create Date: 2026-03-09
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from advanced_alchemy.types import DateTimeUTC

revision: str = "a3f8c1d2e456"
down_revision: Union[str, None] = "9b21192c2125"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_message",
        sa.Column("read_by_user_at", DateTimeUTC(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_message", "read_by_user_at")
