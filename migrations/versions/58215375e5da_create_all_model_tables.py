"""create all model tables

Revision ID: 58215375e5da
Revises:
Create Date: 2026-09-07

"""

from typing import Sequence, Union

from alembic import op

from app.models import Base

revision: str = "58215375e5da"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    Base.metadata.create_all(op.get_bind())


def downgrade() -> None:
    Base.metadata.drop_all(op.get_bind())
