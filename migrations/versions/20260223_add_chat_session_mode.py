"""Add mode column to chat_session for distinguishing research types.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-02-23
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "chat_session",
        sa.Column("mode", sa.String(20), nullable=False, server_default="research"),
    )


def downgrade() -> None:
    op.drop_column("chat_session", "mode")
