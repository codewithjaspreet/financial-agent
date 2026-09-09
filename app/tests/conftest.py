"""
Shared fixtures. db_session is the admin (superuser) session -- tests create
their own fresh tenant per test (uuid4(), never reused), so parallel test
runs and leftover rows from previous runs never collide. Some code paths
under test (actions.py, agent nodes) commit internally, so this fixture does
NOT wrap tests in a rollback -- that would silently break in the middle of
those paths. Test data accumulates harmlessly under its own unique tenant ids.
"""
from datetime import date, datetime, timezone

import pytest

from app.config.db import get_admin_session

MAX_DATE = date(9999, 12, 31)
MAX_DT = datetime(9999, 12, 31, tzinfo=timezone.utc)


@pytest.fixture
def db_session():
    session = get_admin_session()
    yield session
    session.close()


def dates(valid_from: date, tx_from: datetime | None = None) -> dict:
    return {
        "valid_from": valid_from, "valid_to": MAX_DATE,
        "tx_from": tx_from or datetime.now(timezone.utc), "tx_to": MAX_DT,
    }
