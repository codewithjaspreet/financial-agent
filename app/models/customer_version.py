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

class CustomerVersion(Base):
    __tablename__ = "customer_version"

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    customer_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
    )

    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
