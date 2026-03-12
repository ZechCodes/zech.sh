"""Add encrypted_payload and nonce columns to ai_chat_message for E2E history.

Revision ID: h0k6f8g7i345
Revises: f8i4d6e5g123
Create Date: 2026-03-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "h0k6f8g7i345"
down_revision: str = "f8i4d6e5g123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_message",
        sa.Column("encrypted_payload", sa.Text(), nullable=True),
    )
    op.add_column(
        "ai_chat_message",
        sa.Column("nonce", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_message", "nonce")
    op.drop_column("ai_chat_message", "encrypted_payload")
