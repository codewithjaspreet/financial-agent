"""enable rls and grant app_user

Revision ID: f3c8a91d0b12
Revises: de7dca853b7f
Create Date: 2026-09-10

Policies already exist. Without ENABLE ROW LEVEL SECURITY they do nothing.
app_user also needs table grants or request-scoped sessions fail closed for
the wrong reason (permission denied instead of empty rows).
"""

from typing import Sequence, Union

from alembic import op

revision: str = "f3c8a91d0b12"
down_revision: Union[str, Sequence[str], None] = "de7dca853b7f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TENANT_TABLES = [
    "users", "customers", "invoices", "payments", "allocations",
    "credit_notes", "debit_notes", "advances", "disputes",
    "messages", "claims", "policies", "balance_cache",
    "customer_version", "raw_events", "pending_events",
    "runs", "outbound",
]
RUN_CHILD_TABLES = ["run_steps", "run_facts"]


def upgrade() -> None:
    op.execute("GRANT USAGE ON SCHEMA public TO app_user")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user")
    op.execute(
        "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user"
    )

    for table in TENANT_TABLES + RUN_CHILD_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    for table in TENANT_TABLES + RUN_CHILD_TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute("REVOKE ALL ON ALL TABLES IN SCHEMA public FROM app_user")
