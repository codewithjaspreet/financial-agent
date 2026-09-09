import threading
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from app.config.db import AdminSessionLocal
from app.models.customer import Customer
from app.models.outbound import Outbound
from app.models.tenant import Tenant
from app.services.actions import FakeProvider, approve_and_send, make_draft, reconcile
from app.tests.conftest import dates


def _setup(db_session):
    tenant = Tenant(id=uuid4(), name=f"Actions Test {uuid4()}")
    db_session.add(tenant)
    db_session.flush()
    customer = Customer(id=uuid4(), tenant_id=tenant.id, name="Test Co", credit_limit=0,
                         terms_days=30, **dates(date(2026, 1, 1)))
    db_session.add(customer)
    db_session.flush()
    db_session.commit()
    return tenant, customer


def test_approve_and_send_happy_path(db_session):
    tenant, customer = _setup(db_session)
    idem_key = f"k-happy-{tenant.id}"
    make_draft(db_session, tenant.id, customer.id, None, "whatsapp", "Please pay.", idem_key=idem_key)
    db_session.commit()

    result = approve_and_send(db_session, tenant.id, idem_key, FakeProvider(mode="ok"))
    assert result["state"] == "sent"
    assert result["duplicate"] is False


def test_approving_twice_sends_exactly_once(db_session):
    """Two managers approving the same reminder concurrently must result in one message."""
    tenant, customer = _setup(db_session)
    idem_key = f"k-race-{tenant.id}"
    make_draft(db_session, tenant.id, customer.id, None, "whatsapp", "Please pay.", idem_key=idem_key)
    db_session.commit()

    provider = FakeProvider(mode="ok")
    results = []

    def worker():
        session = AdminSessionLocal()
        results.append(approve_and_send(session, tenant.id, idem_key, provider))
        session.close()

    threads = [threading.Thread(target=worker), threading.Thread(target=worker)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    sent_count = sum(1 for r in results if not r["duplicate"])
    assert sent_count == 1
    assert len(provider.sent_keys) == 1


def test_timeout_becomes_unknown_then_reconcile_resolves_it(db_session):
    """Edge case #11: an approved send times out with delivery unknown -- resolved later, not guessed now."""
    tenant, customer = _setup(db_session)
    idem_key = f"k-timeout-{tenant.id}"
    make_draft(db_session, tenant.id, customer.id, None, "whatsapp", "Please pay.", idem_key=idem_key)
    db_session.commit()

    provider = FakeProvider(mode="timeout_then_sent")
    result = approve_and_send(db_session, tenant.id, idem_key, provider)
    assert result["state"] == "unknown"

    row = db_session.query(Outbound).filter_by(tenant_id=tenant.id, idempotency_key=idem_key).one()
    row.updated_at = datetime.now(timezone.utc) - timedelta(seconds=200)
    db_session.commit()

    resolved = reconcile(db_session, provider)
    assert resolved["sent"] >= 1
    row = db_session.query(Outbound).filter_by(tenant_id=tenant.id, idempotency_key=idem_key).one()
    assert row.status == "sent"
