from datetime import date, datetime, timezone
from uuid import uuid4

from app.models.credit_note import CreditNote
from app.models.customer import Customer
from app.models.dispute import Dispute
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.models.tenant import Tenant
from app.services.finance import allocate_payment, get_balance, rank_customers
from app.services.policy import save_policy, get_policy
from app.tests.conftest import dates

DEFAULT_POLICY = {
    "include": ["invoice"], "subtract": ["payment", "credit_note"],
    "net_advances": True, "advances_must_be_approved": True,
    "exclude_disputed": True, "exclude_below_paise": 0, "exclude_tags": [],
    "grace_days": 0, "aging_buckets": [30, 60, 90], "allocation": "oldest_first",
    "priority_weights": {"overdue_amount": 0.4, "overdue_days": 0.25,
                          "broken_promise": 0.2, "open_dispute": -0.3, "no_contact": 0.15},
    "min_name_confidence": 0.75, "min_name_gap": 0.15,
}


def _setup(db_session):
    tenant = Tenant(id=uuid4(), name=f"Finance Test {uuid4()}")
    db_session.add(tenant)
    db_session.flush()
    customer = Customer(id=uuid4(), tenant_id=tenant.id, name="Test Co", credit_limit=0,
                         terms_days=30, **dates(date(2026, 1, 1)))
    db_session.add(customer)
    db_session.flush()
    save_policy(db_session, tenant.id, DEFAULT_POLICY)
    return tenant, customer


def test_not_yet_due_invoice_counts_as_invoiced_but_not_overdue(db_session):
    tenant, customer = _setup(db_session)
    invoice = Invoice(entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id,
                       number="INV-1", issue_date=date(2026, 9, 1), due_date=date(2026, 10, 1),
                       amount=12_00_000_00, invoice_type="invoice", **dates(date(2026, 9, 1)))
    db_session.add(invoice)
    db_session.flush()

    now = datetime.now(timezone.utc)
    policy, version = get_policy(db_session, tenant.id, now)
    balance = get_balance(db_session, tenant.id, [customer.id], date(2026, 9, 5), now, policy, version)[customer.id]

    assert balance["invoiced"] == 12_00_000_00
    assert balance["overdue"] == 0
    db_session.rollback()


def test_disputed_invoice_excluded_when_policy_says_so(db_session):
    """Edge case #1 groundwork: exclude_disputed removes the invoice from the total entirely."""
    tenant, customer = _setup(db_session)
    entity_id = uuid4()
    invoice = Invoice(entity_id=entity_id, tenant_id=tenant.id, customer_id=customer.id,
                       number="INV-D", issue_date=date(2026, 6, 1), due_date=date(2026, 7, 1),
                       amount=5_00_000_00, invoice_type="invoice", **dates(date(2026, 6, 1)))
    dispute = Dispute(tenant_id=tenant.id, customer_id=customer.id, invoice_entity_id=entity_id,
                       amount_paise=5_00_000_00, status="open", **dates(date(2026, 6, 15)))
    db_session.add_all([invoice, dispute])
    db_session.flush()

    now = datetime.now(timezone.utc)
    policy, version = get_policy(db_session, tenant.id, now)
    balance = get_balance(db_session, tenant.id, [customer.id], date(2026, 9, 5), now, policy, version)[customer.id]

    assert balance["invoiced"] == 0
    assert balance["excluded_disputed"] == 5_00_000_00
    db_session.rollback()


def test_credit_note_after_full_payment_creates_negative_balance():
    """Edge case #14: a credit note against a settled invoice -- sign inversion, not an error."""
    from app.config.db import get_admin_session
    db_session = get_admin_session()
    tenant, customer = _setup(db_session)
    invoice = Invoice(entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id,
                       number="CN-1", issue_date=date(2026, 6, 1), due_date=date(2026, 7, 1),
                       amount=10_00_000_00, invoice_type="invoice", **dates(date(2026, 6, 1)))
    payment = Payment(entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id, ref="P-1",
                       amount=10_00_000_00, value_date=date(2026, 6, 15), **dates(date(2026, 6, 15)))
    note = CreditNote(entity_id=uuid4(), tenant_id=tenant.id, customer_id=customer.id,
                       invoice_entity_id=invoice.entity_id, amount=2_00_000_00,
                       reason="rate correction", **dates(date(2026, 8, 1)))
    db_session.add_all([invoice, payment, note])
    db_session.flush()

    now = datetime.now(timezone.utc)
    policy, version = get_policy(db_session, tenant.id, now)
    balance = get_balance(db_session, tenant.id, [customer.id], date(2026, 9, 5), now, policy, version)[customer.id]

    assert balance["outstanding"] == -2_00_000_00
    assert balance["is_credit"] is True
    db_session.rollback()


def test_allocate_payment_oldest_first_unallocated_remainder_never_silent():
    """Edge case #6: a 15L receipt against four open invoices -- remainder is explicit, never netted away."""
    invoices = [
        {"id": "a", "due_date": date(2026, 7, 1), "amount": 5_00_000_00},
        {"id": "b", "due_date": date(2026, 7, 15), "amount": 4_00_000_00},
        {"id": "c", "due_date": date(2026, 8, 1), "amount": 3_00_000_00},
        {"id": "d", "due_date": date(2026, 8, 15), "amount": 6_00_000_00},
    ]
    result = allocate_payment(15_00_000_00, invoices, {"allocation": "oldest_first"})
    assert sum(paise for _, paise in result["lines"]) + result["unallocated"] == 15_00_000_00
    assert result["unallocated"] == 0  # 5+4+3 = 12L, then 3L into the 4th (6L) invoice = 15L exactly


def test_allocate_payment_pro_rata_sums_back_exactly():
    invoices = [{"id": "a", "due_date": date(2026, 7, 1), "amount": 33},
                {"id": "b", "due_date": date(2026, 7, 2), "amount": 33},
                {"id": "c", "due_date": date(2026, 7, 3), "amount": 34}]
    result = allocate_payment(100, invoices, {"allocation": "pro_rata"})
    assert sum(paise for _, paise in result["lines"]) + result["unallocated"] == 100


def test_rank_customers_skips_credit_balance_and_ranks_by_score():
    big, small, credit = uuid4(), uuid4(), uuid4()
    balances = {
        big: {"outstanding": 50_00_000_00, "is_credit": False, "oldest_overdue_days": 60},
        small: {"outstanding": 1_00_000_00, "is_credit": False, "oldest_overdue_days": 5},
        credit: {"outstanding": -1_00_000_00, "is_credit": True, "oldest_overdue_days": 0},
    }
    signals: dict = {k: {} for k in balances}
    weights = {"overdue_amount": 0.4, "overdue_days": 0.25}
    ranked = rank_customers(balances, signals, weights)

    scored = [r for r in ranked if r["score"] is not None]
    skipped = [r for r in ranked if r["score"] is None]
    assert scored[0]["customer_id"] == big
    assert len(skipped) == 1 and skipped[0]["customer_id"] == credit
    assert skipped[0]["reasons"][0]["note"] == "customer is in credit"
