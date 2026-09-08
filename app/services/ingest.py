from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config.db import insert_fact
from app.models.customer_version import CustomerVersion
from app.models.payment import Payment
from app.models.pending_event import PendingEvent
from app.models.raw_event import RawEvent

RETRY_BACKOFF_SECONDS = 60


def bump_version(session: Session, tenant_id: UUID, customer_id: UUID) -> None:
    version_row = session.get(CustomerVersion, {"tenant_id": tenant_id, "customer_id": customer_id})
    now = datetime.now(timezone.utc)
    if version_row is None:
        session.add(CustomerVersion(tenant_id=tenant_id, customer_id=customer_id, version=1, updated_at=now))
    else:
        version_row.version += 1
        version_row.updated_at = now


def receive_event(session: Session, tenant_id: UUID, source: str, event_id: str,
                   event_type: str, payload: dict) -> dict:
    """
    The unique (tenant_id, source, event_id) constraint does the real work:
    the same webhook delivered twice is a no-op here, never a double-applied payment.
    """
    existing = session.scalar(
        select(RawEvent).where(
            RawEvent.tenant_id == tenant_id, RawEvent.source == source, RawEvent.event_id == event_id
        )
    )
    if existing:
        return {"duplicate": True, "raw_event_id": existing.id}

    raw_event = RawEvent(
        tenant_id=tenant_id, source=source, event_id=event_id, event_type=event_type,
        payload=payload, received_at=datetime.now(timezone.utc), processed=False,
    )
    session.add(raw_event)
    session.flush()

    process_event(session, raw_event)
    return {"duplicate": False, "raw_event_id": raw_event.id, "processed": raw_event.processed}


def process_event(session: Session, raw_event: RawEvent) -> None:
    payload = raw_event.payload
    if raw_event.event_type == "payment":
        _apply_payment(session, raw_event, payload)
    elif raw_event.event_type == "reversal":
        _apply_reversal_or_wait(session, raw_event, payload)
    else:
        raise ValueError(f"unknown event_type: {raw_event.event_type}")


def _apply_payment(session: Session, raw_event: RawEvent, payload: dict) -> None:
    payment = Payment(
        entity_id=uuid4(),
        tenant_id=raw_event.tenant_id,
        customer_id=UUID(payload["customer_id"]),
        ref=payload["ref"],
        amount=payload["amount"],
        value_date=date.fromisoformat(payload["value_date"]),
    )
    insert_fact(session, payment, valid_from=payment.value_date)
    bump_version(session, raw_event.tenant_id, payment.customer_id)
    raw_event.processed = True
    _release_waiting_on(session, raw_event.tenant_id, ref=payload["ref"])


def _apply_reversal(session: Session, raw_event: RawEvent, payload: dict, original: Payment) -> None:
    reversal = Payment(
        entity_id=original.entity_id,  # same logical payment, so it nets out for that entity
        tenant_id=raw_event.tenant_id,
        customer_id=original.customer_id,
        ref=payload["ref"],
        amount=-original.amount,
        value_date=date.fromisoformat(payload["value_date"]),
    )
    insert_fact(session, reversal, valid_from=reversal.value_date)
    bump_version(session, raw_event.tenant_id, original.customer_id)
    raw_event.processed = True


def _find_payment(session: Session, tenant_id: UUID, ref: str) -> Payment | None:
    return session.scalar(
        select(Payment).where(Payment.tenant_id == tenant_id, Payment.ref == ref)
        .order_by(Payment.tx_from.desc())
    )


def _apply_reversal_or_wait(session: Session, raw_event: RawEvent, payload: dict) -> None:
    reverses_ref = payload["reverses_ref"]
    original = _find_payment(session, raw_event.tenant_id, reverses_ref)

    if original is None:
        # We never apply a reversal early -- park it until its payment shows up.
        now = datetime.now(timezone.utc)
        session.add(PendingEvent(
            tenant_id=raw_event.tenant_id, event_id=raw_event.event_id, event_type="reversal",
            payload=payload, reason=f"payment {reverses_ref} not seen yet",
            waiting_for=reverses_ref, retry_at=now + timedelta(seconds=RETRY_BACKOFF_SECONDS),
            tries=0, created_at=now,
        ))
        return

    _apply_reversal(session, raw_event, payload, original)


def _release_waiting_on(session: Session, tenant_id: UUID, ref: str) -> None:
    """Called right after a payment lands, in case a reversal was already parked waiting for it."""
    waiting = session.scalars(
        select(PendingEvent).where(PendingEvent.tenant_id == tenant_id, PendingEvent.waiting_for == ref)
    ).all()

    for pending in waiting:
        raw_event = session.scalar(
            select(RawEvent).where(RawEvent.tenant_id == tenant_id, RawEvent.event_id == pending.event_id)
        )
        original = _find_payment(session, tenant_id, ref)
        if raw_event is not None and original is not None:
            _apply_reversal(session, raw_event, pending.payload, original)
            session.delete(pending)


def retry_pending(session: Session) -> int:
    """Background job: re-check every parked reversal whose retry_at has passed."""
    now = datetime.now(timezone.utc)
    due = session.scalars(select(PendingEvent).where(PendingEvent.retry_at <= now)).all()

    resolved = 0
    for pending in due:
        raw_event = session.scalar(
            select(RawEvent).where(RawEvent.tenant_id == pending.tenant_id, RawEvent.event_id == pending.event_id)
        )
        original = _find_payment(session, pending.tenant_id, pending.waiting_for)

        if raw_event is not None and original is not None:
            _apply_reversal(session, raw_event, pending.payload, original)
            session.delete(pending)
            resolved += 1
        else:
            pending.tries += 1
            pending.retry_at = now + timedelta(seconds=RETRY_BACKOFF_SECONDS * (pending.tries + 1))

    session.commit()
    return resolved
