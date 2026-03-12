"""Widen chat_session.mode column from String(20) to String(30).

Needed for 'experimental_research' mode (23 chars).

Revision ID: g9j5e7f6h234
Revises: e7h3c5d4f012
Create Date: 2026-03-12
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "g9j5e7f6h234"
down_revision: str = "e7h3c5d4f012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "chat_session",
        "mode",
        type_=sa.String(30),
        existing_type=sa.String(20),
        existing_nullable=False,
        existing_server_default="research",
    )


def downgrade() -> None:
    op.alter_column(
        "chat_session",
        "mode",
        type_=sa.String(20),
        existing_type=sa.String(30),
        existing_nullable=False,
        existing_server_default="research",
    )
