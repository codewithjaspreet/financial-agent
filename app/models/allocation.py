from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
)
from sqlalchemy.orm import  Mapped, mapped_column
from app.models.base import Base


class Allocation(Base):
    __tablename__ = "allocations"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))

    payment_entity_id: Mapped[UUID] = mapped_column()
    invoice_entity_id: Mapped[UUID] = mapped_column()
    amount: Mapped[int] = mapped_column(BigInteger)

    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    tx_from: Mapped[datetime] = mapped_column(DateTime)
    tx_to: Mapped[datetime] = mapped_column(DateTime)
