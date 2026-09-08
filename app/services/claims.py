from collections import defaultdict
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.claims import Claim
from app.models.dispute import Dispute
from app.models.message import Message
from app.models.payment import Payment

PROMISE_GRACE_DAYS = 7          # days past due before "overdue" becomes "broken"
PAYMENT_MATCH_WINDOW_DAYS = 5   # how close a payment must land to a claimed date to count as a match
PAYMENT_MATCH_TOLERANCE = 0.05  # 5% amount tolerance for a claim/payment match


def save_claim(session: Session, tenant_id: UUID, customer_id: UUID | None,
               claim_type: str, claimed_paise: int | None, claimed_date: date | None,
               message_id: UUID | None, confidence: int,
               expires_at: datetime | None = None) -> UUID:
    """
    Insert-only, same idea as insert_fact for money: a claim is never edited.
    A newer claim about the same promise is a new row, not an update to the old one.
    """
    claim = Claim(
        tenant_id=tenant_id,
        customer_id=customer_id,
        message_id=message_id,
        claim_type=claim_type,
        amount_paise=claimed_paise,
        claim_date=claimed_date,
        confidence=confidence,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc),
    )
    session.add(claim)
    session.flush()
    return claim.id


def get_claims(session: Session, tenant_id: UUID, customer_ids: list[UUID],
                on_date: date) -> dict[UUID, list[Claim]]:
    """All claims made about these customers, as known on_date. Grouped, never summed into money."""
    if not customer_ids:
        return {}

    rows = session.scalars(
        select(Claim).where(
            Claim.tenant_id == tenant_id,
            Claim.customer_id.in_(customer_ids),
            Claim.claim_date <= on_date,
        ).order_by(Claim.created_at)
    ).all()

    by_customer: dict[UUID, list[Claim]] = defaultdict(list)
    for claim in rows:
        if claim.customer_id is not None:  # unmatched-entity claims aren't grouped here
            by_customer[claim.customer_id].append(claim)
    return dict(by_customer)


def _promise_status(claim: Claim, payments: list[Payment], today: date, promises: list[Claim]) -> str:
    """
    Computed fresh every call, never written to a column: a promise's status
    ("broken", "kept") is implied by today's date and whether a payment turned
    up -- not an independent fact worth storing and getting out of sync.
    """
    newer_exists = any(
        other.created_at > claim.created_at
        for other in promises
    )
    if newer_exists:
        return "replaced"

    due = claim.expires_at.date() if claim.expires_at else claim.claim_date
    if due is None or due >= today:
        return "open"

    matched = any(
        claim.amount_paise is not None
        and abs(payment.amount - claim.amount_paise) <= claim.amount_paise * PAYMENT_MATCH_TOLERANCE
        and 0 <= (payment.value_date - due).days <= PAYMENT_MATCH_WINDOW_DAYS
        for payment in payments
    )
    if matched:
        return "kept"

    return "broken" if (today - due).days > PROMISE_GRACE_DAYS else "overdue"


def update_promises(session: Session, tenant_id: UUID, customer_id: UUID, today: date) -> dict[UUID, str]:
    """{claim_id: status} for every promise-type claim this customer has made."""
    claims = list(session.scalars(
        select(Claim).where(Claim.tenant_id == tenant_id, Claim.customer_id == customer_id)
        .order_by(Claim.created_at)
    ).all())
    promises = [c for c in claims if c.claim_type == "promise"]
    if not promises:
        return {}

    payments = list(session.scalars(
        select(Payment).where(Payment.tenant_id == tenant_id, Payment.customer_id == customer_id)
    ).all())

    return {claim.id: _promise_status(claim, payments, today, promises) for claim in promises}


def find_conflicts(session: Session, tenant_id: UUID, customer_id: UUID, claims: list[Claim]) -> list[dict]:
    """
    A 'paid' claim with no matching payment in the ledger -> reported, never
    used to adjust the balance. This is the whole answer to "claim vs.
    verified state": we only ever read payments here to compare, never write.
    """
    payments = list(session.scalars(
        select(Payment).where(Payment.tenant_id == tenant_id, Payment.customer_id == customer_id)
    ).all())

    conflicts = []
    for claim in claims:
        if claim.claim_type != "paid" or claim.amount_paise is None or claim.claim_date is None:
            continue
        matched = any(
            abs(payment.amount - claim.amount_paise) <= claim.amount_paise * PAYMENT_MATCH_TOLERANCE
            and abs((payment.value_date - claim.claim_date).days) <= PAYMENT_MATCH_WINDOW_DAYS
            for payment in payments
        )
        if not matched:
            conflicts.append({
                "type": "claim_not_in_erp",
                "claim_id": claim.id,
                "claimed_paise": claim.amount_paise,
                "claimed_date": claim.claim_date,
                "note": "customer claims this was paid; no matching payment found in the ERP",
            })
    return conflicts


def get_signals(session: Session, tenant_id: UUID, customer_ids: list[UUID], on_date: date) -> dict[UUID, dict]:
    """
    Flags and counts only, never paise -- this is the ONLY thing rank_customers
    is allowed to see besides the verified balance.
    """
    if not customer_ids:
        return {}

    claims_by_customer = get_claims(session, tenant_id, customer_ids, on_date)

    messages = session.scalars(
        select(Message).where(Message.tenant_id == tenant_id, Message.customer_id.in_(customer_ids))
    ).all()
    messages_by_customer: dict[UUID, list[Message]] = defaultdict(list)
    for message in messages:
        if message.customer_id is not None:  # unmatched-entity messages aren't grouped here
            messages_by_customer[message.customer_id].append(message)

    disputes = session.scalars(
        select(Dispute).where(
            Dispute.tenant_id == tenant_id,
            Dispute.customer_id.in_(customer_ids),
            Dispute.status == "open",
        )
    ).all()
    open_disputes_by_customer: dict[UUID, int] = defaultdict(int)
    for dispute in disputes:
        open_disputes_by_customer[dispute.customer_id] += 1

    signals = {}
    for customer_id in customer_ids:
        claims = claims_by_customer.get(customer_id, [])
        promise_status = update_promises(session, tenant_id, customer_id, on_date)
        conflicts = find_conflicts(session, tenant_id, customer_id, claims)

        customer_messages = messages_by_customer.get(customer_id, [])
        if customer_messages:
            last_contact = max(m.message_time for m in customer_messages)
            days_since_contact = (datetime.now(timezone.utc) - last_contact).days
            evidence = "lots" if len(customer_messages) >= 10 else "some"
        else:
            days_since_contact = None
            evidence = "none"  # absence of evidence is not evidence of absence

        signals[customer_id] = {
            "has_broken_promise": any(status == "broken" for status in promise_status.values()),
            "days_since_contact": days_since_contact,
            "open_disputes": open_disputes_by_customer.get(customer_id, 0),
            "unmatched_payment_claims": len(conflicts),
            "evidence": evidence,
        }
    return signals
