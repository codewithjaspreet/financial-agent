from datetime import datetime, date

from sqlalchemy.orm import  Mapped, mapped_column
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    JSON,
    String,
)

from app.models.base import Base



class Customer(Base):
    __tablename__ = "customers"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(ForeignKey("tenants.id"))

    name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    gstin: Mapped[str | None] = mapped_column(String(50))
    credit_limit: Mapped[int] = mapped_column(BigInteger, default=0)
    terms_days: Mapped[int] = mapped_column(default=0)
    tags: Mapped[list | None] = mapped_column(JSON)

    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date] = mapped_column(Date)
    tx_from: Mapped[datetime] = mapped_column(DateTime)
    tx_to: Mapped[datetime] = mapped_column(DateTime)

