"""enable pg_trgm

Revision ID: 99d5e222e434
Revises: b5f1eea5ce99
Create Date: 2026-09-08 22:21:50.542016

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '99d5e222e434'
down_revision: Union[str, Sequence[str], None] = 'b5f1eea5ce99'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute("CREATE INDEX IF NOT EXISTS ix_customers_name_trgm ON customers USING GIN (name gin_trgm_ops);")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_customers_name_trgm;")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm;")
