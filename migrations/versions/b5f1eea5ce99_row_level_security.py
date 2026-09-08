"""row level security

Revision ID: b5f1eea5ce99
Revises: 58215375e5da
Create Date: 2026-09-07 22:50:18.771801

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5f1eea5ce99'
down_revision: Union[str, Sequence[str], None] = '58215375e5da'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

from alembic import op
from app.config.config import settings

def upgrade() -> None:
    op.execute(f"""
        DO $$
        BEGIN
           IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
              CREATE ROLE app_user LOGIN PASSWORD '{settings.app_user_password}';
           END IF;
        END
        $$;
    """)


def downgrade() -> None:
    """Downgrade schema."""
    pass
