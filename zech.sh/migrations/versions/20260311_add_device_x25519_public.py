"""Add x25519_public column to ai_chat_device for E2E key exchange.

Revision ID: f8i4d6e5g123
Revises: e7h3c5d4f012
Create Date: 2026-03-11
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f8i4d6e5g123"
down_revision: str = "e7h3c5d4f012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_device",
        sa.Column("x25519_public", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_device", "x25519_public")
