
"""
valid_from / valid_to
        ↓
"When was this actually true?"

tx_from / tx_to
        ↓
"When did our system believe this?"

"""

from sqlalchemy import text
from datetime import date, datetime, timezone
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config.config import settings


admin_engine = create_engine(settings.database_url, pool_pre_ping=True)
app_engine = create_engine(settings.app_database_url, pool_pre_ping=True)

AdminSessionLocal = sessionmaker(bind=admin_engine, expire_on_commit=False)
AppSessionLocal = sessionmaker(bind=app_engine, expire_on_commit=False)




MAX_DATE = date(9999, 12, 31)
MAX_DATETIME = datetime(
    9999,
    12,
    31,
    tzinfo=timezone.utc,
)

def get_admin_session() -> Session:
    return AdminSessionLocal()

def get_session(tenant_id: UUID) -> Session:
    """
    set_config(), not "SET LOCAL x = :param" -- SET is a utility statement
    and Postgres rejects a bind parameter in that position (syntax error).
    set_config() is a normal SQL function, so it takes a real parameter safely.

    The third argument is false (SESSION-scoped, not LOCAL/transaction-scoped):
    some service functions (e.g. actions.approve_and_send) commit partway
    through a single request to release a row lock before a network call --
    a LOCAL setting would vanish at that commit. SESSION-scoped survives
    commits within this session, and is still always reset here, fresh,
    before a single query runs on a freshly checked-out connection -- so it
    can never leak a previous request's tenant into a new one.
    """
    session = AppSessionLocal()
    session.execute(text("SELECT set_config('app.tenant_id', :tenant_id, false)"), {"tenant_id": str(tenant_id)})
    return session

def as_of(
    session: Session,
    model,
    tenant_id: UUID,
    on_date: date,
    at_time: datetime,
):
    """
    Return records that were valid on the business date
    AND believed to be true at the requested system time.
    """

    return session.scalars(
        select(model).where(
            model.tenant_id == tenant_id,

            model.valid_from <= on_date,
            model.valid_to > on_date,

            model.tx_from <= at_time,
            model.tx_to > at_time,
        )
    ).all()


def insert_fact(
    session: Session,
    fact,
    valid_from: date,
):
    """
    Insert a new financial fact with both temporal clocks starting now.
    """

    now = datetime.now(timezone.utc)

    fact.valid_from = valid_from
    fact.valid_to = MAX_DATE

    fact.tx_from = now
    fact.tx_to = MAX_DATETIME

    session.add(fact)

    return fact


def correct_fact(
    session: Session,
    old_fact,
    new_fact,
    valid_from: date,
):
    """
    Correct a fact without destroying history.

    Old version becomes historically closed.
    New version becomes the current belief.
    """

    now = datetime.now(timezone.utc)

    old_fact.tx_to = now

    new_fact.valid_from = valid_from
    new_fact.valid_to = old_fact.valid_to

    new_fact.tx_from = now
    new_fact.tx_to = MAX_DATETIME

    session.add(new_fact)

    return new_fact
