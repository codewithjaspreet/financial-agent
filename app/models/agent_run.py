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


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    tenant_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=False,
    )

    user_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
    )

    question: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    on_date: Mapped[date | None] = mapped_column(Date)

    at_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    policy_version: Mapped[int | None] = mapped_column(Integer)

    state: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    reason: Mapped[str | None] = mapped_column(Text)

    answer: Mapped[str | None] = mapped_column(Text)

    tool_calls: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )