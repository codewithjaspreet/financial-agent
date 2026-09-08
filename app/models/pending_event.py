from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey, String, Text, Boolean, UniqueConstraint, Integer, JSON,
)
from sqlalchemy.orm import  Mapped, mapped_column
from app.models.base import Base
from sqlalchemy.dialects.postgresql import UUID as PGUUID

class PendingEvent(Base):
    __tablename__ = "pending_events"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    event_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )

    reason: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    # What this event is blocked on -- e.g. the payment ref a reversal reverses,
    # so we know what to check for when retry_pending runs.
    waiting_for: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    tries: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
