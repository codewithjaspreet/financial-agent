from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class DebitNote(Base):
    __tablename__ = "debit_notes"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    entity_id: Mapped[UUID] = mapped_column(default=uuid4)

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"))
    invoice_entity_id: Mapped[UUID | None] = mapped_column()

    amount: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str | None] = mapped_column(Text)

    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    tx_from: Mapped[datetime] = mapped_column(DateTime)
    tx_to: Mapped[datetime] = mapped_column(DateTime)
