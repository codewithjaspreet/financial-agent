from collections import defaultdict
from datetime import date, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.advance import Advance
from app.models.credit_note import CreditNote
from app.models.debit_note import DebitNote
from app.models.dispute import Dispute
from app.models.invoice import Invoice
from app.models.payment import Payment
from app.utils.money import split_by_weights

ROW_KEYS = ("invoices", "payments", "credit_notes", "debit_notes", "advances", "disputes")


def _empty_rows() -> dict:
    return {key: [] for key in ROW_KEYS}


def _fetch(session: Session, model, tenant_id: UUID, customer_ids: list[UUID],
           on_date: date, at_time: datetime) -> list:
    """
    One query per table, scoped to only the customers asked for -- never a
    per-customer loop. Same bitemporal filter as db.as_of, plus customer_id IN (...).
    """
    rows = session.scalars(
        select(model).where(
            model.tenant_id == tenant_id,
            model.customer_id.in_(customer_ids),
            model.valid_from <= on_date, model.valid_to > on_date,
            model.tx_from <= at_time, model.tx_to > at_time,
        )
    ).all()
    return list(rows)


def _bucket_for(days_overdue: int, buckets: list[int]) -> str:
    for bucket in buckets:
        if days_overdue <= bucket:
            return f"0-{bucket}"
    return f"{buckets[-1]}+"


def apply_policy(rows: dict, policy: dict, on_date: date) -> dict:
    """
    Reads rows for ONE customer, returns the components dict.
    All amounts are paise integers. Every branch reads a policy key --
    nothing here is hardcoded per tenant.
    """
    grace = timedelta(days=policy.get("grace_days", 0))
    min_amount = policy.get("exclude_below_paise", 0)
    aging_buckets = policy.get("aging_buckets", [30, 60, 90])

    disputed_entities = set()
    if policy.get("exclude_disputed"):
        disputed_entities = {
            d.invoice_entity_id for d in rows["disputes"]
            if d.status == "open" and d.invoice_entity_id is not None
        }

    invoiced = 0
    overdue = 0
    excluded_disputed = 0
    oldest_overdue_days = 0
    aging: dict[str, int] = {}

    for invoice in rows["invoices"]:
        if invoice.amount < min_amount:
            continue
        if invoice.entity_id in disputed_entities:
            excluded_disputed += invoice.amount
            continue

        invoiced += invoice.amount

        effective_due = invoice.due_date + grace
        if effective_due <= on_date:
            days_overdue = (on_date - effective_due).days
            overdue += invoice.amount
            oldest_overdue_days = max(oldest_overdue_days, days_overdue)
            bucket = _bucket_for(days_overdue, aging_buckets)
            aging[bucket] = aging.get(bucket, 0) + invoice.amount

    debit_notes = sum(n.amount for n in rows["debit_notes"])
    payments = sum(p.amount for p in rows["payments"])
    credit_notes = sum(n.amount for n in rows["credit_notes"])

    kind_totals = {
        "invoice": invoiced, "debit_note": debit_notes,
        "payment": payments, "credit_note": credit_notes,
    }
    included = sum(kind_totals[k] for k in policy["include"])
    subtracted = sum(kind_totals[k] for k in policy["subtract"])

    advances_netted = 0
    if policy.get("net_advances"):
        for advance in rows["advances"]:
            if policy.get("advances_must_be_approved") and advance.status != "approved":
                continue
            advances_netted += advance.amount

    outstanding = included - subtracted - advances_netted

    return {
        "invoiced": invoiced,
        "paid": payments,
        "credit_notes": credit_notes,
        "debit_notes": debit_notes,
        "advances_netted": advances_netted,
        "excluded_disputed": excluded_disputed,
        "outstanding": outstanding,
        "aging": aging,
        "overdue": overdue,
        "oldest_overdue_days": oldest_overdue_days,
        "is_credit": outstanding < 0,
    }


def get_balance(session: Session, tenant_id: UUID, customer_ids: list[UUID],
                 on_date: date, at_time: datetime,
                 policy: dict, policy_version: int) -> dict[UUID, dict]:
    """
    NOTE: balance_cache is not wired in yet. The current BalanceCache model
    only has a UNIQUE(tenant_id, customer_id) key and a single balance_paise
    column -- no policy_version or as_of_date. Caching through it as-is could
    serve last month's number for an as-of query, or last policy's number
    after a policy change. That's worse than being slow, so this always
    computes fresh until the model gets those two columns added.
    """
    if not customer_ids:
        return {}

    invoices = _fetch(session, Invoice, tenant_id, customer_ids, on_date, at_time)
    payments = _fetch(session, Payment, tenant_id, customer_ids, on_date, at_time)
    credit_notes = _fetch(session, CreditNote, tenant_id, customer_ids, on_date, at_time)
    debit_notes = _fetch(session, DebitNote, tenant_id, customer_ids, on_date, at_time)
    advances = _fetch(session, Advance, tenant_id, customer_ids, on_date, at_time)
    disputes = _fetch(session, Dispute, tenant_id, customer_ids, on_date, at_time)

    by_customer: dict[UUID, dict] = defaultdict(_empty_rows)
    for invoice in invoices:
        by_customer[invoice.customer_id]["invoices"].append(invoice)
    for payment in payments:
        by_customer[payment.customer_id]["payments"].append(payment)
    for note in credit_notes:
        by_customer[note.customer_id]["credit_notes"].append(note)
    for note in debit_notes:
        by_customer[note.customer_id]["debit_notes"].append(note)
    for advance in advances:
        by_customer[advance.customer_id]["advances"].append(advance)
    for dispute in disputes:
        by_customer[dispute.customer_id]["disputes"].append(dispute)

    return {
        customer_id: apply_policy(by_customer.get(customer_id, _empty_rows()), policy, on_date)
        for customer_id in customer_ids
    }


def allocate_payment(amount: int, open_invoices: list[dict], policy: dict) -> dict:
    """
    open_invoices: [{"id": ..., "due_date": ..., "amount": ...}, ...]
    oldest_first: sort by due date, fill until money runs out
    smallest_first: sort by amount ascending
    pro_rata: split_by_weights across all open invoices
    Returns {"lines": [(invoice_id, paise)], "unallocated": paise}
    Unallocated is always returned separately, never folded into the balance.
    """
    strategy = policy.get("allocation", "oldest_first")

    if strategy == "pro_rata":
        weights = [invoice["amount"] for invoice in open_invoices]
        shares = split_by_weights(amount, weights) if weights else []
        lines = [(invoice["id"], share) for invoice, share in zip(open_invoices, shares)]
        return {"lines": lines, "unallocated": amount - sum(shares)}

    if strategy == "smallest_first":
        ordered = sorted(open_invoices, key=lambda i: i["amount"])
    else:  # oldest_first, the default
        ordered = sorted(open_invoices, key=lambda i: i["due_date"])

    lines: list[tuple] = []
    remaining = amount
    for invoice in ordered:
        take = min(invoice["amount"], remaining)
        if take > 0:
            lines.append((invoice["id"], take))
        remaining -= take
        if remaining <= 0:
            break

    assert sum(paise for _, paise in lines) + remaining == amount
    return {"lines": lines, "unallocated": remaining}


def rank_customers(balances: dict[UUID, dict], signals: dict[UUID, dict], weights: dict) -> list[dict]:
    """
    signals come from claims.py and carry flags/counts only, never money --
    this function never reads a paise value out of signals.
    Customers with a credit balance are skipped with reason "customer is in credit".
    """
    scored = []
    skipped = []

    for customer_id, balance in balances.items():
        if balance["is_credit"]:
            skipped.append({
                "customer_id": customer_id, "score": None, "rank": None,
                "reasons": [{"factor": "in_credit", "note": "customer is in credit"}],
            })
            continue

        signal = signals.get(customer_id, {})
        reasons = []
        score = 0.0

        if balance["outstanding"] > 0:
            points = weights.get("overdue_amount", 0) * min(balance["outstanding"] / 100_000_00, 1.0)
            score += points
            reasons.append({"factor": "overdue_amount", "value": balance["outstanding"], "points": points})

        if balance.get("oldest_overdue_days"):
            points = weights.get("overdue_days", 0) * min(balance["oldest_overdue_days"] / 90, 1.0)
            score += points
            reasons.append({"factor": "overdue_days", "value": balance["oldest_overdue_days"], "points": points})

        if signal.get("has_broken_promise"):
            points = weights.get("broken_promise", 0)
            score += points
            reasons.append({"factor": "broken_promise", "points": points})

        if signal.get("open_disputes"):
            points = weights.get("open_dispute", 0) * signal["open_disputes"]
            score += points
            reasons.append({"factor": "open_dispute", "value": signal["open_disputes"], "points": points})

        if signal.get("days_since_contact"):
            points = weights.get("no_contact", 0) * min(signal["days_since_contact"] / 30, 1.0)
            score += points
            reasons.append({"factor": "no_contact", "value": signal["days_since_contact"], "points": points})

        scored.append({"customer_id": customer_id, "score": score, "reasons": reasons})

    scored.sort(key=lambda entry: -entry["score"])
    for rank, entry in enumerate(scored, start=1):
        entry["rank"] = rank

    return scored + skipped
