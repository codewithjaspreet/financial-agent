from datetime import datetime, date

from sqlalchemy.orm import  Mapped, mapped_column
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    String,
)

from app.models.base import Base


class Advance(Base):
    __tablename__ = "advances"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    entity_id: Mapped[UUID] = mapped_column(default=uuid4)

    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))
    customer_id: Mapped[UUID] = mapped_column(ForeignKey("customers.id"))

    amount: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(50))

    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    tx_from: Mapped[datetime] = mapped_column(DateTime)
    tx_to: Mapped[datetime] = mapped_column(DateTime)

