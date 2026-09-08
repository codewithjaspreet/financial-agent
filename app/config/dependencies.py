from collections.abc import Generator
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.config.config import settings
from app.config.db import get_session
from app.services.auth import User

security = HTTPBearer()


def get_current_user(creds: HTTPAuthorizationCredentials = Depends(security)) -> User:
    try:
        payload = jwt.decode(creds.credentials, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="invalid or expired token")

    return User(
        user_id=UUID(payload["user_id"]),
        tenant_id=UUID(payload["tenant_id"]),
        role=payload["role"],
    )


def get_db_session(user: User = Depends(get_current_user)) -> Generator[Session, None, None]:
    """
    The RLS-scoped session -- every authenticated route uses this, never the
    admin engine. tenant_id comes from the decoded JWT, never from the request.
    """
    session = get_session(user.tenant_id)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
