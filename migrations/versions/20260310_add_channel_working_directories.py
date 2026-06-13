"""Add working_directory and additional_directories to ai_chat_channel.

Revision ID: c5f1a3b2d890
Revises: a3f8e2c1d456, b4e9d2f1a789
Create Date: 2026-03-10
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "c5f1a3b2d890"
down_revision: tuple[str, str] = ("a3f8e2c1d456", "b4e9d2f1a789")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_chat_channel",
        sa.Column("working_directory", sa.String(500), nullable=True),
    )
    op.add_column(
        "ai_chat_channel",
        sa.Column("additional_directories", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ai_chat_channel", "additional_directories")
    op.drop_column("ai_chat_channel", "working_directory")
