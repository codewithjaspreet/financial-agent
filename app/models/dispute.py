
from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey, String, Text,
)
from sqlalchemy.orm import  Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from app.models.base import Base

MAX_DATE = date(9999, 12, 31)
MAX_DATETIME = datetime(9999, 12, 31)

class Dispute(Base):
    __tablename__ = "disputes"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    customer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("customers.id"),
        nullable=False,
    )

    # References Invoice.entity_id, not Invoice.id: an invoice can be
    # corrected (new row, same entity_id), and the dispute must keep
    # pointing at the logical invoice across those corrections.
    invoice_entity_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
    )

    amount_paise: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="open",
    )

    # Business / valid time
    valid_from: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    valid_to: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=MAX_DATE,
    )

    # System / transaction time
    tx_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    tx_to: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=MAX_DATETIME,
    )