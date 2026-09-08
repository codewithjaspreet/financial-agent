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



class RunFact(Base):
    __tablename__ = "run_facts"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("runs.id"),
        nullable=False,
    )

    fact_key: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    kind: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    amount_paise: Mapped[int | None] = mapped_column(
        BigInteger
    )

    display_value: Mapped[str | None] = mapped_column(
        String(255)
    )

    source: Mapped[str | None] = mapped_column(
        String(255)
    )

    is_claim: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )