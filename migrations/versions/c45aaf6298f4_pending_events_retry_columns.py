"""pending events retry columns

Revision ID: c45aaf6298f4
Revises: 99d5e222e434
Create Date: 2026-09-08 22:25:20.414290

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c45aaf6298f4'
down_revision: Union[str, Sequence[str], None] = '99d5e222e434'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("pending_events", sa.Column("waiting_for", sa.String(255), nullable=False, server_default=""))
    op.add_column("pending_events", sa.Column("retry_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("pending_events", sa.Column("tries", sa.Integer, nullable=False, server_default="0"))
    op.alter_column("pending_events", "waiting_for", server_default=None)
    op.alter_column("pending_events", "retry_at", server_default=None)
    op.alter_column("pending_events", "tries", server_default=None)


def downgrade() -> None:
    op.drop_column("pending_events", "tries")
    op.drop_column("pending_events", "retry_at")
    op.drop_column("pending_events", "waiting_for")
