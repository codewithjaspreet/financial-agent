from datetime import date, datetime, timezone
from uuid import uuid4

from app.config.db import as_of
from app.models.customer import Customer
from app.models.payment import Payment
from app.models.pending_event import PendingEvent
from app.models.tenant import Tenant
from app.services.ingest import receive_event
from app.tests.conftest import dates


def _setup(db_session):
    tenant = Tenant(id=uuid4(), name=f"Ingest Test {uuid4()}")
    db_session.add(tenant)
    db_session.flush()
    customer = Customer(id=uuid4(), tenant_id=tenant.id, name="Test Co", credit_limit=0,
                         terms_days=30, **dates(date(2026, 1, 1)))
    db_session.add(customer)
    db_session.flush()
    return tenant, customer


def test_duplicate_webhook_delivery_is_a_no_op(db_session):
    """Edge case #7 (part 1): the same settlement webhook arrives twice."""
    tenant, customer = _setup(db_session)
    payload = {"customer_id": str(customer.id), "ref": "PAY-DUP", "amount": 5_00_000_00, "value_date": "2026-09-01"}

    first = receive_event(db_session, tenant.id, "bank", "evt-dup", "payment", payload)
    second = receive_event(db_session, tenant.id, "bank", "evt-dup", "payment", payload)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    payments = as_of(db_session, Payment, tenant.id, date(2026, 9, 1), datetime.now(timezone.utc))
    assert len(payments) == 1, "duplicate delivery must never create a second payment row"


def test_reversal_before_its_payment_parks_then_auto_releases(db_session):
    """Edge case #7 (part 2): a reversal arrives before the payment it reverses."""
    tenant, customer = _setup(db_session)

    receive_event(db_session, tenant.id, "bank", "evt-rev", "reversal",
                   {"ref": "REV-1", "reverses_ref": "PAY-LATE", "value_date": "2026-09-03"})

    pending = db_session.query(PendingEvent).filter_by(tenant_id=tenant.id, waiting_for="PAY-LATE").all()
    assert len(pending) == 1, "must park, never guess or drop"

    payments = as_of(db_session, Payment, tenant.id, date(2026, 9, 3), datetime.now(timezone.utc))
    assert all(p.ref != "REV-1" for p in payments), "reversal must not apply before its payment exists"

    receive_event(db_session, tenant.id, "bank", "evt-pay-late", "payment",
                   {"customer_id": str(customer.id), "ref": "PAY-LATE", "amount": 3_00_000_00, "value_date": "2026-09-02"})

    pending_after = db_session.query(PendingEvent).filter_by(tenant_id=tenant.id, waiting_for="PAY-LATE").all()
    assert len(pending_after) == 0, "must auto-release once the payment lands"

    payments = as_of(db_session, Payment, tenant.id, date(2026, 9, 3), datetime.now(timezone.utc))
    by_ref = {p.ref: p.amount for p in payments}
    assert by_ref["PAY-LATE"] == 3_00_000_00
    assert by_ref["REV-1"] == -3_00_000_00
