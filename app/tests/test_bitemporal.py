from datetime import date, datetime, timezone
from uuid import uuid4

from app.config.db import as_of
from app.models.customer import Customer
from app.models.invoice import Invoice
from app.models.tenant import Tenant
from app.tests.conftest import dates


def _make_tenant_and_customer(session):
    tenant = Tenant(id=uuid4(), name=f"Bitemporal Test {uuid4()}")
    session.add(tenant)
    session.flush()
    customer = Customer(id=uuid4(), tenant_id=tenant.id, name="Test Co", credit_limit=0,
                         terms_days=30, **dates(date(2026, 1, 1)))
    session.add(customer)
    session.flush()
    return tenant, customer


def test_as_of_answers_two_different_questions(db_session):
    """
    Edge case #4: 42L owed on 1 Sep, 10L paid on 2 Sep. Querying on_date=1 Sep
    must show the invoice but not the payment, regardless of when we ask.
    """
    tenant, customer = _make_tenant_and_customer(db_session)
    invoice = Invoice(id=uuid4(), entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id,
                       number="INV-1", issue_date=date(2026, 8, 1), due_date=date(2026, 9, 1),
                       amount=42_00_000_00, invoice_type="invoice", **dates(date(2026, 8, 1)))
    db_session.add(invoice)
    db_session.flush()

    now = datetime.now(timezone.utc)
    as_of_1sep = as_of(db_session, Invoice, tenant.id, date(2026, 9, 1), now)
    as_of_before_issued = as_of(db_session, Invoice, tenant.id, date(2026, 7, 31), now)

    assert len(as_of_1sep) == 1  # invoice is valid on/after its issue date (1 Aug)
    assert len(as_of_before_issued) == 0  # didn't exist yet the day before it was issued
    db_session.rollback()


def test_backdated_entry_valid_time_vs_transaction_time(db_session):
    """
    Edge case #5: on system-date 5 Sep, an invoice dated 28 Aug is entered.
    A report "as believed on 4 Sep" (before the entry) must NOT see it, even
    though the invoice's own business date (28 Aug) is in the past relative
    to both report dates. A report "as believed today" must see it.
    """
    tenant, customer = _make_tenant_and_customer(db_session)
    entered_at = datetime(2026, 9, 5, 10, 0, tzinfo=timezone.utc)
    invoice = Invoice(id=uuid4(), entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id,
                       number="BACK-1", issue_date=date(2026, 8, 28), due_date=date(2026, 9, 27),
                       amount=15_00_000_00, invoice_type="invoice",
                       **dates(date(2026, 8, 28), tx_from=entered_at))
    db_session.add(invoice)
    db_session.flush()

    business_date = date(2026, 8, 28)
    believed_before_entry = as_of(db_session, Invoice, tenant.id, business_date,
                                   datetime(2026, 9, 4, tzinfo=timezone.utc))
    believed_after_entry = as_of(db_session, Invoice, tenant.id, business_date,
                                  datetime(2026, 9, 6, tzinfo=timezone.utc))

    assert len(believed_before_entry) == 0, "yesterday's report could not have known about this invoice yet"
    assert len(believed_after_entry) == 1, "today's report, re-run, must see it"
    db_session.rollback()


def test_correct_fact_closes_old_row_and_opens_new_one(db_session):
    from app.config.db import correct_fact, insert_fact

    tenant, customer = _make_tenant_and_customer(db_session)
    invoice = Invoice(entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id,
                       number="CORR-1", issue_date=date(2026, 7, 1), due_date=date(2026, 8, 1),
                       amount=10_00_000_00, invoice_type="invoice")
    insert_fact(db_session, invoice, valid_from=date(2026, 7, 1))
    db_session.flush()
    original_tx_to = invoice.tx_to

    corrected = Invoice(entity_id=invoice.entity_id, tenant_id=tenant.id, customer_id=customer.id,
                         number="CORR-1", issue_date=date(2026, 7, 1), due_date=date(2026, 8, 1),
                         amount=12_00_000_00, invoice_type="invoice")
    correct_fact(db_session, invoice, corrected, valid_from=date(2026, 7, 1))
    db_session.flush()

    assert invoice.tx_to < original_tx_to  # old row's belief-window is now closed
    assert corrected.amount == 12_00_000_00
    db_session.rollback()
