"""
Row-Level Security proof (§3.4) -- these tests use db.get_session(tenant_id),
the actual RLS-restricted app_user connection, not the admin session every
other test file uses. This is what stands between "tenant isolation is a
docstring" and "tenant isolation is enforced by Postgres itself."
"""
from datetime import date, datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.config.db import AppSessionLocal, get_admin_session, get_session
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.tests.conftest import dates


def _seed_two_tenants():
    admin = get_admin_session()
    tenant_a, tenant_b = uuid4(), uuid4()
    admin.add_all([Tenant(id=tenant_a, name=f"RLS Tenant A {tenant_a}"),
                    Tenant(id=tenant_b, name=f"RLS Tenant B {tenant_b}")])
    admin.flush()

    cust_a = Customer(id=uuid4(), tenant_id=tenant_a, name="Tenant A Customer",
                       credit_limit=0, terms_days=30, **dates(date(2026, 1, 1)))
    cust_b = Customer(id=uuid4(), tenant_id=tenant_b, name="Tenant B Customer",
                       credit_limit=0, terms_days=30, **dates(date(2026, 1, 1)))
    admin.add_all([cust_a, cust_b])
    admin.flush()
    invoice_a = Invoice(entity_id=uuid4(), tenant_id=tenant_a, customer_id=cust_a.id,
                         number="A-1", issue_date=date(2026, 1, 1), due_date=date(2026, 2, 1),
                         amount=1_00_000_00, invoice_type="invoice", **dates(date(2026, 1, 1)))
    admin.add(invoice_a)
    admin.commit()
    return tenant_a, tenant_b, cust_a.id, cust_b.id


def test_tenant_a_cannot_read_tenant_b_customers():
    tenant_a, tenant_b, cust_a_id, cust_b_id = _seed_two_tenants()

    session_a = get_session(tenant_a)
    names = set(session_a.scalars(select(Customer.name)).all())
    session_a.close()

    assert "Tenant A Customer" in names
    assert "Tenant B Customer" not in names


def test_tenant_b_cannot_read_tenant_a_invoices():
    tenant_a, tenant_b, cust_a_id, cust_b_id = _seed_two_tenants()

    session_b = get_session(tenant_b)
    invoices = session_b.scalars(select(Invoice)).all()
    session_b.close()

    assert all(inv.tenant_id == tenant_b for inv in invoices)


def test_no_tenant_set_returns_empty_not_everything():
    """Fails closed: an app_user connection with app.tenant_id unset sees nothing, not all tenants."""
    _seed_two_tenants()

    raw_session = AppSessionLocal()  # deliberately skip get_session()'s set_config call
    try:
        rows = raw_session.scalars(select(Customer)).all()
        assert rows == [], "with no tenant pinned, RLS must return zero rows, never everything"
    finally:
        raw_session.close()


def test_cross_tenant_tool_call_finds_nothing_not_an_error():
    """
    Edge case #12: the model requests a customer belonging to another tenant.
    tools.py passes the caller's real tenant_id regardless of what's in the
    request; this proves that even a DIRECT query for the other tenant's
    customer id, under the wrong session, returns nothing -- not an error
    that would leak "this belongs to someone else."
    """
    tenant_a, tenant_b, cust_a_id, cust_b_id = _seed_two_tenants()

    session_a = get_session(tenant_a)
    found = session_a.get(Customer, cust_b_id)  # tenant A's session, tenant B's customer id
    session_a.close()

    assert found is None
