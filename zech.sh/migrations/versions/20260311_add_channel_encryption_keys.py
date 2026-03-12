"""Add encrypted_channel_key and key_nonce columns to ai_chat_channel.

Revision ID: e7h3c5d4f012
Revises: d6g2b4c3e901
Create Date: 2026-03-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e7h3c5d4f012"
down_revision: str = "d6g2b4c3e901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_channel",
        sa.Column("encrypted_channel_key", sa.Text(), nullable=True),
    )
    op.add_column(
        "ai_chat_channel",
        sa.Column("key_nonce", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_channel", "key_nonce")
    op.drop_column("ai_chat_channel", "encrypted_channel_key")
