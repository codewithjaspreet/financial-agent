import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.outbound import Outbound

RECONCILE_AFTER_SECONDS = 120
MAX_RECONCILE_TRIES = 3
TERMINAL_STATES = {"sending", "sent", "unknown", "not_sent", "needs_human"}


class FakeProvider:
    """
    Stand-in for the real WhatsApp/email API. `mode` picks what happens on send:
    'ok'                -> succeeds immediately
    'timeout_then_sent' -> raises, but the message actually went out (provider will confirm later)
    'timeout_then_lost' -> raises, and the message never went out
    """

    def __init__(self, mode: str = "ok"):
        self.mode = mode
        self.sent_keys: set[str] = set()

    def send(self, body: str, key: str) -> str:
        if self.mode == "ok":
            self.sent_keys.add(key)
            return f"provider-msg-{key[:8]}"
        if self.mode == "timeout_then_sent":
            self.sent_keys.add(key)  # it DID go out, we just don't know it yet
            raise TimeoutError(key)
        if self.mode == "timeout_then_lost":
            raise TimeoutError(key)  # never went out
        raise ValueError(f"unknown provider mode: {self.mode}")

    def check(self, key: str) -> bool:
        """Used by reconcile: did this key actually go out?"""
        return key in self.sent_keys


def make_draft(session: Session, tenant_id: UUID, customer_id: UUID, run_id: UUID | None,
               channel: str, body: str, idem_key: str | None = None) -> dict:
    """idem_key defaults to a hash of (run_id, body) -- same input always gets the same key."""
    idem_key = idem_key or hashlib.sha256(f"{run_id}:{body}".encode()).hexdigest()[:32]
    now = datetime.now(timezone.utc)

    existing = session.scalar(
        select(Outbound).where(Outbound.tenant_id == tenant_id, Outbound.idempotency_key == idem_key)
    )
    if existing:
        return {"outbound_id": existing.id, "idem_key": idem_key, "state": existing.status}

    draft = Outbound(
        tenant_id=tenant_id, customer_id=customer_id, run_id=run_id,
        channel=channel, message=body, idempotency_key=idem_key,
        status="draft", attempts=0, created_at=now, updated_at=now,
    )
    session.add(draft)
    session.flush()
    return {"outbound_id": draft.id, "idem_key": idem_key, "state": "draft"}


def approve_and_send(session: Session, tenant_id: UUID, idem_key: str, provider: FakeProvider) -> dict:
    """
    SELECT ... FOR UPDATE: two managers approving the same key concurrently --
    the second call blocks until the first commits, then sees the row already
    in a terminal state and returns duplicate=True instead of sending twice.
    """
    row = session.scalar(
        select(Outbound)
        .where(Outbound.tenant_id == tenant_id, Outbound.idempotency_key == idem_key)
        .with_for_update()
    )
    if row is None:
        raise ValueError("no draft with that idempotency key")

    if row.status in TERMINAL_STATES:
        return {"outbound_id": row.id, "state": row.status, "duplicate": True}

    outbound_id = row.id
    row.status = "sending"
    row.attempts += 1
    row.updated_at = datetime.now(timezone.utc)
    session.commit()  # release the row lock BEFORE the network call

    try:
        provider_id = provider.send(row.message, key=idem_key)
        new_status = "sent"
    except TimeoutError:
        provider_id = None
        new_status = "unknown"  # a timeout is not a failure -- it's an unknown

    row = session.scalar(select(Outbound).where(Outbound.id == outbound_id).with_for_update())
    assert row is not None, "outbound row disappeared between the two commits"
    row.status = new_status
    if provider_id:
        row.provider_id = provider_id
    row.updated_at = datetime.now(timezone.utc)
    session.commit()

    return {"outbound_id": outbound_id, "state": new_status, "duplicate": False}


def reconcile(session: Session, provider: FakeProvider) -> dict:
    """
    Every row stuck in 'unknown' for more than RECONCILE_AFTER_SECONDS:
    ask the provider whether that key actually went out.
    yes -> 'sent'. no -> retry once. 3 tries -> 'needs_human'.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=RECONCILE_AFTER_SECONDS)
    stuck = session.scalars(
        select(Outbound).where(Outbound.status == "unknown", Outbound.updated_at <= cutoff)
    ).all()

    resolved = {"sent": 0, "resent": 0, "still_unknown": 0, "needs_human": 0}
    for row in stuck:
        if provider.check(row.idempotency_key):
            row.status = "sent"
            resolved["sent"] += 1
        elif row.attempts >= MAX_RECONCILE_TRIES:
            row.status = "needs_human"
            resolved["needs_human"] += 1
        else:
            row.attempts += 1
            try:
                row.provider_id = provider.send(row.message, key=row.idempotency_key)
                row.status = "sent"
                resolved["resent"] += 1
            except TimeoutError:
                resolved["still_unknown"] += 1
        row.updated_at = datetime.now(timezone.utc)

    session.commit()
    return resolved
