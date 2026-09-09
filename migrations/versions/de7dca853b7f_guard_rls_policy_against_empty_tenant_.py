"""guard rls policy against empty tenant setting

Revision ID: de7dca853b7f
Revises: cc6a0ae2b733
Create Date: 2026-09-08 23:24:23.101444

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'de7dca853b7f'
down_revision: Union[str, Sequence[str], None] = 'cc6a0ae2b733'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


"""
Bug: once a pooled connection has had app.tenant_id set at least once,
Postgres's reset-on-checkin leaves it as an empty string '', not NULL.
current_setting(..., true)::uuid on '' raises invalid_text_representation
instead of failing closed to zero rows. NULLIF(...,'') turns '' into a real
NULL before the cast, so an unset-or-reset tenant reliably yields no rows
(RLS's tenant_id = NULL comparison is never true) instead of a 500 error.
"""
from alembic import op

TENANT_TABLES = [
    "users", "customers", "invoices", "payments", "allocations",
    "credit_notes", "debit_notes", "advances", "disputes",
    "messages", "claims", "policies", "balance_cache",
    "customer_version", "raw_events", "pending_events",
    "runs", "outbound",
]
RUN_CHILD_TABLES = ["run_steps", "run_facts"]


def upgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid);
        """)

    for table in RUN_CHILD_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (run_id IN (
                SELECT id FROM runs
                WHERE tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            ));
        """)


def downgrade() -> None:
    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (tenant_id = current_setting('app.tenant_id', true)::uuid);
        """)

    for table in RUN_CHILD_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table};")
        op.execute(f"""
            CREATE POLICY tenant_isolation ON {table}
            USING (run_id IN (
                SELECT id FROM runs WHERE tenant_id = current_setting('app.tenant_id', true)::uuid
            ));
        """)
