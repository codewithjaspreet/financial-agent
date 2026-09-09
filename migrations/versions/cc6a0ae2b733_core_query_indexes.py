"""core query indexes

Revision ID: cc6a0ae2b733
Revises: c45aaf6298f4
Create Date: 2026-09-08 23:18:16.815806

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cc6a0ae2b733'
down_revision: Union[str, Sequence[str], None] = 'c45aaf6298f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # These match the exact WHERE clauses finance._fetch and db.as_of always
    # use: tenant_id + customer_id + the bitemporal "currently true" filter.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invoices_tenant_customer_bitemporal "
        "ON invoices (tenant_id, customer_id, tx_to, valid_to);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_invoices_tenant_customer_due_date "
        "ON invoices (tenant_id, customer_id, due_date);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_payments_tenant_customer_bitemporal "
        "ON payments (tenant_id, customer_id, tx_to, valid_to);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_claims_tenant_customer "
        "ON claims (tenant_id, customer_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_disputes_tenant_customer_status "
        "ON disputes (tenant_id, customer_id, status);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_messages_text_fts "
        "ON messages USING GIN (to_tsvector('english', message_text));"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_messages_text_fts;")
    op.execute("DROP INDEX IF EXISTS ix_disputes_tenant_customer_status;")
    op.execute("DROP INDEX IF EXISTS ix_claims_tenant_customer;")
    op.execute("DROP INDEX IF EXISTS ix_payments_tenant_customer_bitemporal;")
    op.execute("DROP INDEX IF EXISTS ix_invoices_tenant_customer_due_date;")
    op.execute("DROP INDEX IF EXISTS ix_invoices_tenant_customer_bitemporal;")
